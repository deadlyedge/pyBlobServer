# import asyncio
import os
from dotenv import load_dotenv
import pendulum
import uuid
import random

from typing import List, Optional
from fastapi import (
    FastAPI,
    Request,
    UploadFile,
    File,
    Depends,
    HTTPException,
    status,
    Security,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware

from contextlib import asynccontextmanager
from loguru import logger
# from dotenv import load_dotenv

from tortoise import Tortoise
from tortoise.exceptions import DoesNotExist

from app.models import UsersInfo, FileInfo, json_datetime_convert, ENV

load_dotenv()

# BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
# BASE_FOLDER = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
# ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
# DEFAULT_SHORT_PATH_LENGTH = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
# FILE_SIZE_LIMIT_MB = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
# TOTAL_SIZE_LIMIT_MB = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))
# DATABASE_URL = os.getenv("DATABASE_URL", "sqlite://./uploads/blobserver.db")

if not ENV.ALLOWED_USERS:
    logger.error("ALLOWED_USERS is empty, please set it in .env file")
    exit(1)


async def database_connect():
    await Tortoise.init(
        db_url=ENV.DATABASE_URL, modules={"models": ["app.models"]}, _create_db=True
    )
    await Tortoise.generate_schemas()


async def database_close():
    await Tortoise.close_connections()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(ENV.BASE_FOLDER, exist_ok=True)
    try:
        await database_connect()
        yield
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        raise  # Re-raise the exception to halt startup
    finally:
        await database_close()
        logger.info("Shutting down...")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


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
                user_dict = {**user_dict, "token": "[hide...]"}
            return user_dict
        except DoesNotExist:
            new_user_dict = json_datetime_convert(
                await UsersInfo.create(user=self.user_id, token=str(uuid.uuid4()))
            )
            return new_user_dict
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")


async def api_token_auth(token: str) -> UsersInfo:
    try:
        return await UsersInfo.get(token=token)
    except DoesNotExist:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Token"
        )
    except Exception as e:
        logger.error(f"Error authenticating user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server Error"
        )


async def get_current_user(token: str = Security(oauth2_scheme)):
    try:
        return await api_token_auth(token)
    except HTTPException as e:
        raise e


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

    async def _update_user_usage(self, file_size: int = 0, is_deletion: bool = False):
        try:
            user = await UsersInfo.get(user=self.user)
            if not is_deletion:
                user.total_uploads += file_size
                user.last_upload_at = pendulum.now()
            user.total_size = await self._get_total_size()
            await user.save()
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
            await self._update_user_usage(file.size or 0)
            logger.info(
                f"{file.filename}, {round((file.size or 1) / 1024 / 1024, 2)} MB, uploaded."
            )
            return {
                "file_id": file_id,
                "file_url": f"{ENV.BASE_URL}/s/{file_id}",
                "show_image": f"{ENV.BASE_URL}/s/{file_id}?output=html",
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

        if output == "html":
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
        if output == "json":
            return JSONResponse(json_datetime_convert(file_info), status_code=200)
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
            file = await FileInfo.get(
                file_id=file_id, user=self.user
            )  # Added user filter for safety
            file_size = file.file_size
            file_path = self._get_file_path(file_id)
            if os.path.exists(file_path):
                os.remove(file_path)
            await (
                file.delete()
            )  # Moved delete after file path removal to handle potential errors better.
            await self._update_user_usage(file_size, is_deletion=True)
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
                    user=self.user, upload_time__lt=cutoff
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


###############################
# Routes


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/user/{user_id}")
async def get_user(user_id: str, function: str = ""):
    """
    Fetches user information for a given user ID.

    The function first checks if the user ID is in the list of allowed users.

    If the user is allowed, it attempts to retrieve user information using
    the UserManager class. Optionally, a function can be specified to change
    the user's token.

    Args:
        user_id (str): The ID of the user to retrieve information for.
        function (str, optional): A function name to execute specific actions
                                  like changing the token. Defaults to an empty string.

    Returns:
        JSONResponse: A JSON response containing the user's information.
    """
    if user_id not in ENV.ALLOWED_USERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User not allowed"
        )
    return JSONResponse(await UserManager(user_id).get_user(function), status_code=200)


@app.get("/s/{file_id}")
async def get_file(file_id: str, output: str = "file"):
    return await FileStorage().get_file(file_id, output)


@app.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    return JSONResponse(
        await FileStorage(current_user.user).save_file(file),
        status_code=200,
    )


@app.get("/list")
async def list_files(current_user=Depends(get_current_user)):
    return JSONResponse(
        await FileStorage(current_user.user).get_files_info_list(),
        status_code=200,
    )


@app.delete("/delete/{file_id}")
async def delete_file(file_id: str, current_user=Depends(get_current_user)):
    if await FileStorage(current_user.user).delete_file(file_id):
        logger.info(f"File {file_id} deleted")
        return JSONResponse({"message": "File deleted"}, status_code=200)
    return JSONResponse({"error": "File not found"}, status_code=404)


@app.delete("/delete_all/")
async def delete_all(
    confirm: str = "No", function="all", current_user=Depends(get_current_user)
):
    """
    Deletes all or a specific function's files for a current user based on confirmation.

    This endpoint handles the deletion of files for a user. Before performing the deletion,
    it checks for a confirmation parameter ('confirm') to ensure the user's intent. If the
    'confirm' parameter is not set to 'yes', the deletion won't be processed.

    Args:
        confirm (str): Confirmation parameter to authorize file deletion. Default is "No".
                       To proceed with deletion, this should be set to "yes".
        function (str): Specifies the function type for deletion, default is "all",
                        indicating all files.
        current_user: Dependency injection to get the currently authenticated user.

    Returns:
        JSONResponse: Confirmation of deletion success or an error message if confirmation
    """
    if confirm.lower() != "yes":
        return JSONResponse(
            {
                "error": "Invalid delete_all parameter. Use '?confirm=yes' to confirm deletion of all files."
            },
            status_code=404,
        )
    return await FileStorage(current_user.user).batch_delete(function)
    # if function == "all":
    #     try:
    #         if await file_storage.batch_delete():
    #             return
    #         return JSONResponse({"error": "No files found"}, status_code=404)
    #     except Exception as e:
    #         logger.error(f"Error deleting all files: {e}")
    #         raise HTTPException(status_code=500, detail="Internal Server Error")

    # if function == "expired":
    #     try:
    #         if file_storage.batch_delete(function="expired"):
    #             logger.info(f"Expired files deleted for user {current_user.user}")
    #             return JSONResponse(
    #                 {"message": "Expired files deleted"}, status_code=200
    #             )
    #         return JSONResponse({"error": "No expired files found"}, status_code=404)
    #     except Exception as e:
    #         logger.error(f"Error deleting expired files: {e}")
    #         raise HTTPException(status_code=500, detail="Internal Server Error")

    # return JSONResponse({"error": "Invalid function parameter"}, status_code=400)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
