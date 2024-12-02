import os
import datetime
import random
import pendulum
import uuid

from typing import List, Literal, Optional
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from loguru import logger
from pendulum import instance, timezone
from tortoise import fields, models, timezone as tz
from tortoise.exceptions import DoesNotExist


class ENV:
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    BASE_FOLDER: str = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
    ALLOWED_USERS: List[str] = os.getenv("ALLOWED_USERS", "").split(",")
    DEFAULT_SHORT_PATH_LENGTH: int = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
    FILE_SIZE_LIMIT_MB: int = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
    TOTAL_SIZE_LIMIT_MB: int = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite://./uploads/blobserver.db")


class UsersInfo(models.Model):
    user = fields.CharField(max_length=255, pk=True)
    token = fields.CharField(max_length=255)
    total_size = fields.IntField(default=0)
    total_upload_times = fields.IntField(default=0)
    total_upload_byte = fields.IntField(default=0)
    total_download_times = fields.IntField(default=0)
    total_download_byte = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_upload_at = fields.DatetimeField(null=True)
    last_download_at = fields.DatetimeField(null=True)

    class Meta:
        ordering = ["-created_at"]


class FileInfo(models.Model):
    file_id = fields.CharField(max_length=255, pk=True)
    user = fields.ForeignKeyField("models.UsersInfo", related_name="files")
    file_name = fields.CharField(max_length=255)
    file_size = fields.IntField()
    upload_at = fields.DatetimeField(auto_now_add=True)
    download_times = fields.IntField(default=0)
    last_download_at = fields.DatetimeField(null=True)

    class Meta:
        ordering = ["-upload_at"]


class UserManager:
    def __init__(self, user_id: str):
        self.user_id = user_id

    async def _change_token(self) -> Optional[UsersInfo]:
        try:
            user = await UsersInfo.get(user=self.user_id)
            user.token = str(uuid.uuid4())
            await user.save()
            return user
        except DoesNotExist:
            raise HTTPException(status_code=404, detail="User not found")
        except Exception as e:
            logger.error(f"Error changing token: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def get_user(self, function: str = "") -> dict:
        try:
            if function == "change_token":
                user_dict = json_datetime_convert(await self._change_token())
            else:
                user_dict = json_datetime_convert(
                    await UsersInfo.get(user=self.user_id)
                )
                user_dict = {**user_dict, "token": "[hidden...]"}
            return user_dict
        except DoesNotExist:
            new_user_dict = json_datetime_convert(
                await UsersInfo.create(user=self.user_id, token=str(uuid.uuid4()))
            )
            return new_user_dict
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")


class FileStorage:
    def __init__(self, user: str = ""):
        self.folder = os.path.join(ENV.BASE_FOLDER, user)
        os.makedirs(self.folder, exist_ok=True)
        self.user = user

    @staticmethod
    def _generate_random_string(length: int) -> str:
        pool = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnprstuvwxyz2345678"
        return "".join(random.choice(pool) for _ in range(length))

    async def _validate_file_size(self, file: UploadFile):
        if file.size and file.size > ENV.FILE_SIZE_LIMIT_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File size exceeds limit",
            )

        user = await UsersInfo.get(user=self.user)
        if (
            file.size
            and user.total_size + file.size > ENV.TOTAL_SIZE_LIMIT_MB * 1024 * 1024
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Total size limit exceeded",
            )

    async def _update_user_usage(
        self,
        file_size: int = 0,
        function: Literal["upload", "delete", "download"] = "upload",
    ):
        try:
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
                f"Update {user.user} usage: {function} {file_size/(1024*1024):.3} MB"
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

    async def get_files_info_list(self) -> List[dict]:
        try:
            return [
                json_datetime_convert(f)
                for f in await FileInfo.filter(
                    user=await UsersInfo.get(user=self.user)
                ).all()
            ]
        except DoesNotExist:
            return []
        except Exception as e:
            logger.error(f"Error loading file info: {e}")
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
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
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
                disposition = "inline" if output == "download" else "attachment"
                return FileResponse(
                    file_path,
                    filename=file_info.file_name,
                    headers={
                        "Content-Disposition": f'{disposition}; filename="{file_info.file_name}"'
                    },
                )

    async def delete_file(self, file_id: str) -> bool:
        try:
            file = await FileInfo.get(file_id=file_id, user=self.user)
            file_size = file.file_size
            file_path = self._get_file_path(file_id)
            if os.path.exists(file_path):
                os.remove(file_path)
            await file.delete()
            await self._update_user_usage(file_size, function="delete")
            return True
        except DoesNotExist:
            return False
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def batch_delete(self, function="all") -> JSONResponse:
        try:
            if function == "all":
                files = await FileInfo.filter(user=self.user).all()
                logger.info(
                    f"Deleting {len(files)} files for user {self.user}..."
                )  # Corrected logging

                [
                    await self.delete_file(file.file_id) for file in files
                ]  # Changed to gather

                return JSONResponse({"message": "All files deleted"}, status_code=200)

            elif function == "expired":
                cutoff = pendulum.now().subtract(days=90)
                files = await FileInfo.filter(
                    user=self.user, upload_at__lt=cutoff
                ).all()
                logger.info(
                    f"Deleting {len(files)} expired files for user {self.user}..."
                )  # Corrected logging

                [
                    await self.delete_file(file.file_id) for file in files
                ]  # Changed to gather

                return JSONResponse(
                    {"message": "All files not been download for 90 days are deleted"},
                    status_code=200,
                )
            else:
                return JSONResponse(
                    {"error": "No file been deleted, function error."}, status_code=405
                )
        except Exception as e:
            logger.error(f"Error during batch deletion: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")


def json_datetime_convert(data) -> dict:
    tz = timezone("Asia/Hong_Kong")
    data = {
        key: value for key, value in data.__dict__.items() if not key.startswith("_")
    }

    # Convert datetime fields to string
    for key, value in data.items():
        if isinstance(value, datetime.datetime):
            data[key] = tz.convert(instance(value)).to_datetime_string()

    return data
