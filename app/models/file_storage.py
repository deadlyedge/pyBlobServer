import asyncio
import os
import secrets
from urllib.parse import unquote
import aiofiles
from fastapi import HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from typing import Literal, Optional
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from loguru import logger
import pendulum
from tortoise import timezone as tz
from tortoise.transactions import in_transaction
from tortoise.exceptions import DoesNotExist

from .utils import json_datetime_convert
from .user_models import UsersInfo, FileInfo
from .env import ENV

class FileStorage:
    def __init__(self, user: str = ""):
        self.folder = os.path.join(ENV.BASE_FOLDER, user)
        os.makedirs(self.folder, exist_ok=True)
        self.user = user

    @staticmethod
    def _generate_random_string(length: int) -> str:
        pool = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnprstuvwxyz2345678"
        return "".join(secrets.choice(pool) for _ in range(length))

    async def _validate_file_size(self, file: UploadFile):
        if not hasattr(file, "size"):
            file.file.seek(0, 2)
            file.size = file.file.tell()
            file.file.seek(0)

        if file.size and file.size > ENV.FILE_SIZE_LIMIT_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File size exceeds limit of [{ENV.FILE_SIZE_LIMIT_MB}] MB",
            )

        user = await UsersInfo.get(user=self.user)
        if user.total_size + (file.size or 0) > ENV.TOTAL_SIZE_LIMIT_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Total size limit of [{ENV.TOTAL_SIZE_LIMIT_MB}] MB exceeded",
            )

    async def _update_user_usage(
        self,
        file_size: int = 0,
        function: Literal["upload", "delete", "download"] = "upload",
    ):
        try:
            async with in_transaction():
                user = await UsersInfo.get(user=self.user)
                match function:
                    case "upload":
                        user.total_upload_byte += file_size
                        user.total_upload_times += 1
                        user.last_upload_at = tz.now()
                    case "delete":
                        pass
                    case "download":
                        user.total_download_byte += file_size
                        user.total_download_times += 1
                        user.last_download_at = tz.now()
                    case _:
                        raise ValueError("Invalid function")

                user.total_size = await self._get_total_size()
                logger.info(
                    f"Update {user.user} usage: {function} {file_size/(1024*1024):.3f} MB"
                )
                await user.save()
                return {
                    "available_space": f"{(
                        ENV.TOTAL_SIZE_LIMIT_MB * 1024 * 1024 - user.total_size
                    )
                    / (1024 * 1024):.3f} MB"
                }
        except DoesNotExist:
            raise HTTPException(status_code=404, detail="User not found")
        except Exception as e:
            logger.error(f"Error updating user usage: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def _get_total_size(self) -> int:
        try:
            files = await FileInfo.filter(user=self.user).all()
            return sum(file.file_size for file in files)
        except Exception as e:
            logger.error(f"Error getting total size: {e}")
            return 0

    def _check_file_path(self, file_id: str) -> Optional[str]:
        file_path = self._get_file_path(file_id)
        return file_path if os.path.exists(file_path) else None

    def _get_file_path(self, file_id: str) -> str:
        return os.path.join(self.folder, file_id)

    async def _load_file_info(self, file_id: str) -> Optional[FileInfo]:
        try:
            return await FileInfo.get(file_id=file_id)
        except DoesNotExist:
            return None
        except Exception as e:
            logger.error(f"Error loading file info: {e}")
            return None

    def _write_file(self, file: UploadFile, file_path: str):
        try:
            with open(file_path, "wb") as f:
                f.write(file.file.read())
        except IOError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to write file: {e}",
            )

    async def _save_file_info(self, file_id: str, file: UploadFile):
        try:
            await FileInfo.create(
                file_id=file_id,
                user=await UsersInfo.get(user=self.user),
                file_name=file.filename,
                file_size=file.size,
            )
        except Exception as e:
            logger.error(f"Error saving file info: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")
    async def _save_file_info_socket(self, file_id: str, filename: str, file_size: int):
        try:
            await FileInfo.create(
                file_id=file_id,
                user=await UsersInfo.get(user=self.user),
                file_name=filename,
                file_size=file_size,
            )
        except Exception as e:
            logger.error(f"Error saving file info: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def _save_file_info_chunk(self, file_id: str, file_name: str, file_size: int):
        try:
            await FileInfo.update_or_create(
                file_id=file_id,
                defaults={
                    "user": await UsersInfo.get(user=self.user),
                    "file_name": file_name,
                    "file_size": file_size,
                },
            )
        except Exception as e:
            logger.error(f"Error saving file info chunk: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def save_file(self, file: UploadFile) -> dict:
        try:
            await self._validate_file_size(file)
            file_id = self._generate_random_string(ENV.DEFAULT_SHORT_PATH_LENGTH)
            file_path = self._get_file_path(file_id)
            self._write_file(file, file_path)
            await self._save_file_info(file_id, file)
            available_space = await self._update_user_usage(file.size or 0)
            return {
                "file_id": file_id,
                "file_url": f"{ENV.BASE_URL}/s/{file_id}",
                "show_image": f"{ENV.BASE_URL}/s/{file_id}?output=html",
                **available_space,
            }
        except HTTPException as e:
            return {
                "filename": file.filename,
                "status_code": status.HTTP_426_UPGRADE_REQUIRED,
                "error": e.detail,
            }
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def save_websocket_file(self, websocket: WebSocket) -> None:
        try:
            while True:
                file_name = await websocket.receive_text()  # Receive the file name
                file_id = self._generate_random_string(ENV.DEFAULT_SHORT_PATH_LENGTH)
                file_path = self._get_file_path(file_id)
                with open(file_path, "wb") as file:
                    try:
                        data = await websocket.receive_bytes()  # Receive the file data
                        if not data:
                            break
                        file.write(data)  # Write the data into the file
                        file_size = len(data)
                        await self._save_file_info_socket(file_id, file_name, file_size)
                        await websocket.send_json(
                            {
                                "file_id": file_id,
                                "file_url": f"{ENV.BASE_URL}/s/{file_id}",
                                "show_image": f"{ENV.BASE_URL}/s/{file_id}?output=html",
                            }
                        )
                        await self._update_user_usage(file_size, function="upload")
                    except WebSocketDisconnect:
                        break  # Client disconnected

        except Exception as e:
            logger.error(f"Error saving file: {e}")

    async def save_chunk(self, request: Request):
        file_id = self._generate_random_string(ENV.DEFAULT_SHORT_PATH_LENGTH)
        file_path = self._get_file_path(file_id)
        try:
            filename = request.headers.get("filename", "unknown")
            filename = unquote(filename)
            async with aiofiles.open(file_path, "wb") as f:
                async for chunk_data in request.stream():
                    await f.write(chunk_data)
            file_size = os.path.getsize(file_path)
            await self._save_file_info_chunk(
                file_id, filename, file_size
            )  # add file info
            available_space = await self._update_user_usage(
                file_size, function="upload"
            )
            return {
                "file_id": file_id,
                "file_url": f"{ENV.BASE_URL}/s/{file_id}",
                "show_image": f"{ENV.BASE_URL}/s/{file_id}?output=html",
                **available_space,
            }
            return {"status": "chunk failed"}
        except Exception as e:
            logger.error(f"Error saving chunk: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def get_file(
        self, file_id: str, output: str = "file"
    ) -> HTMLResponse | JSONResponse | FileResponse:
        try:
            file_info = await FileInfo.get(file_id=file_id)
            await file_info.fetch_related("user")
            current_user = file_info.user.user
        except DoesNotExist:
            raise HTTPException(status_code=404, detail="File not found")

        file_storage = FileStorage(current_user)
        try:
            file_path = file_storage._check_file_path(file_id)
            if not file_path:
                raise HTTPException(status_code=404, detail="File not found")

            file_info = await file_storage._load_file_info(file_id)
            if not file_info:
                raise HTTPException(status_code=404, detail="File info not found")
        except Exception as e:
            logger.error(f"Error getting file: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

        match output:
            case "html":
                html_content = f"""
                    <!DOCTYPE html>
                    <html>
                    <head><title>Image</title></head>
                    <body><h1>Image {file_info.file_name}</h1>
                    <img src="/s/{file_id}" alt="Uploaded Image" style="max-width:100%">
                    </body>
                    </html>
                """
                return HTMLResponse(content=html_content, status_code=200)
            case "json":
                return JSONResponse(json_datetime_convert(file_info), status_code=200)
            case _:
                await file_storage._update_user_usage(
                    file_info.file_size, function="download"
                )
                file_info.download_times += 1
                file_info.last_download_at = tz.now()
                await file_info.save()
                disposition = "inline" if output != "download" else "attachment"
                return FileResponse(
                    file_path,
                    filename=file_info.file_name,
                    headers={
                        "Content-Disposition": f'{disposition}; filename="{file_info.file_name.encode("utf-8").decode("latin-1")}"'
                    },
                )

    async def delete_file(self, file_id: str, skip_usage_update: bool = False) -> bool:
        try:
            file = await FileInfo.get(file_id=file_id, user=self.user)
            file_size = file.file_size
            file_path = self._get_file_path(file_id)
            if os.path.exists(file_path):
                os.remove(file_path)
            await file.delete()
            if not skip_usage_update:
                await self._update_user_usage(file_size, function="delete")
            return True
        except DoesNotExist:
            return False
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def batch_delete(self, function="all") -> JSONResponse:
        try:
            async with in_transaction():
                if function == "all":
                    files = await FileInfo.filter(user=self.user).all()
                    logger.info(f"Deleting {len(files)} files for user {self.user}...")

                    # Use asyncio.gather for parallel deletion but skip usage updates
                    await asyncio.gather(
                        *[
                            self.delete_file(file.file_id, skip_usage_update=True)
                            for file in files
                        ]
                    )

                    # Update usage once after all files are deleted
                    await self._update_user_usage(0, function="delete")

                    return JSONResponse(
                        {"message": "All files deleted"}, status_code=200
                    )

                elif function == "expired":
                    cutoff = pendulum.now().subtract(days=90)
                    files = await FileInfo.filter(
                        user=self.user, upload_at__lt=cutoff
                    ).all()
                    logger.info(
                        f"Deleting {len(files)} expired files for user {self.user}..."
                    )

                    # Use asyncio.gather for parallel deletion but skip usage updates
                    await asyncio.gather(
                        *[
                            self.delete_file(file.file_id, skip_usage_update=True)
                            for file in files
                        ]
                    )

                    # Update usage once after all files are deleted
                    await self._update_user_usage(0, function="delete")

                    return JSONResponse(
                        {
                            "message": "All files not been download for 90 days are deleted"
                        },
                        status_code=200,
                    )
                else:
                    return JSONResponse(
                        {"error": "Invalid function parameter"}, status_code=405
                    )
        except Exception as e:
            logger.error(f"Error during batch deletion: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")
