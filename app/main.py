import os
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
from dotenv import load_dotenv

from tortoise import Tortoise
from tortoise.exceptions import DoesNotExist

from app.models import UsersInfo, FileInfo, json_datetime_convert

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
BASE_FOLDER = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
DEFAULT_SHORT_PATH_LENGTH = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
FILE_SIZE_LIMIT_MB = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
TOTAL_SIZE_LIMIT_MB = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite://./uploads/blobserver.db")

if not ALLOWED_USERS:
    logger.error("ALLOWED_USERS is empty, please set it in .env file")
    exit(1)


async def database_connect():
    await Tortoise.init(
        db_url=DATABASE_URL, modules={"models": ["app.models"]}, _create_db=True
    )
    await Tortoise.generate_schemas()


async def database_close():
    await Tortoise.close_connections()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(BASE_FOLDER, exist_ok=True)
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
    async def get_user(self, user_id: str) -> Optional[UsersInfo]:
        try:
            return await UsersInfo.get(user=user_id)
        except DoesNotExist:
            new_user = await UsersInfo.create(user=user_id, token=str(uuid.uuid4()))
            return new_user
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None

    async def change_token(self, user_id: str) -> Optional[UsersInfo]:
        try:
            user = await UsersInfo.get(user=user_id)
            user.token = str(uuid.uuid4())
            await user.save()
            return user
        except DoesNotExist:
            raise HTTPException(status_code=404, detail="User not found")
        except Exception as e:
            logger.error(f"Error changing token: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    async def api_token_auth(self, token: str) -> UsersInfo:
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
    user_manager = UserManager()
    try:
        return await user_manager.api_token_auth(token)
    except HTTPException as e:
        raise e


class FileStorage:
    def __init__(self, user: str):
        self.folder = os.path.join(BASE_FOLDER, user)
        os.makedirs(self.folder, exist_ok=True)
        self.user = user

    @staticmethod
    def _get_random_string(length: int) -> str:
        pool = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnprstuvwxyz2345678"
        return "".join(random.choice(pool) for _ in range(length))

    async def _validate_file_size(self, file: UploadFile):
        if file.size and file.size > FILE_SIZE_LIMIT_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File size exceeds limit",
            )
        
        user = await UsersInfo.get(user=self.user)
        if file.size and user.total_size + file.size > TOTAL_SIZE_LIMIT_MB * 1024 * 1024:
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
            user.total_size = self._get_total_size()
            await user.save()
        except DoesNotExist:
            raise HTTPException(status_code=404, detail="User not found")
        except Exception as e:
            logger.error(f"Error updating user usage: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")
    def _get_total_size(self) -> int:
        try:
            return sum(
                file.file_size
                for file in FileInfo.select().where(FileInfo.user == self.user)  # xdream mark
            )
        except Exception as e:
            logger.error(f"Error getting total size: {e}")
            return 0

    def _get_file_path(self, file_id: str) -> str:
        return os.path.join(self.folder, file_id)

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

    async def load_files_info(self) -> List[FileInfo]:
        try:
            return await FileInfo.filter(user=await UsersInfo.get(user=self.user)).all()
        except Exception as e:
            logger.error(f"Error loading file info: {e}")
            return []

    async def save_file(self, file: UploadFile) -> str:
        await self._validate_file_size(file)
        file_id = self._get_random_string(DEFAULT_SHORT_PATH_LENGTH)
        file_path = self._get_file_path(file_id)
        self._write_file(file, file_path)
        await self._save_file_info(file_id, file)
        await self._update_user_usage(file.size or 0)
        return file_id

    def get_file(self, file_id: str) -> Optional[str]:
        file_path = self._get_file_path(file_id)
        return file_path if os.path.exists(file_path) else None

    async def get_file_info(self, file_id: str) -> Optional[FileInfo]:
        try:
            return await FileInfo.get(file_id=file_id)
        except DoesNotExist:
            return None
        except Exception as e:
            logger.error(f"Error loading file info: {e}")
            return None

    async def delete_file(self, file_id: str) -> bool:
        try:
            file = await FileInfo.get(file_id=file_id)
            file_size = file.file_size
            await file.delete()
            file_path = self._get_file_path(file_id)
            if os.path.exists(file_path):
                os.remove(file_path)
            await self._update_user_usage(file_size, is_deletion=True)
            return True
        except DoesNotExist:
            return False
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            return False

    async def batch_delete(self, function="all") -> bool:
        if function == "all":
            try:
                files = FileInfo.select().where(FileInfo.user == self.user)
                for file in files:
                    await self.delete_file(file.file_id)
                #     file_path = self._get_file_path(file.file_id)
                #     if os.path.exists(file_path):
                #         os.remove(file_path)
                #     file.delete_instance()
                # self._update_user_usage(is_deletion=True)
                return True
            except Exception as e:
                logger.error(f"Error deleting all files: {e}")
                return False

        elif function == "expired":
            try:
                # files = FileInfo.select(pendulum.parse(str(FileInfo.upload_time)).days>90).where(FileInfo.user == self.user)
                files = (
                    FileInfo.select().where(FileInfo.user == self.user)
                    # .where(
                    #     pendulum.parse(str(FileInfo.upload_time)).add(days=90)
                    #     < pendulum.now()
                    # )
                )

                for file in files:
                    if pendulum.parse(file.upload_time) < pendulum.now().subtract(
                        days=90
                    ):  # type: ignore
                        self.delete_file(file.file_id)
                return True
            except Exception as e:
                logger.error(f"Error deleting expired files: {e}")
                return False
        else:
            return False
@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/user/{user_id}")
async def get_user(user_id: str, f: str = ""):
    if user_id not in ALLOWED_USERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User not allowed"
        )
    user_manager = UserManager()
    try:
        user = await user_manager.get_user(user_id)
        if f == "change_token":
            return JSONResponse(json_datetime_convert(await user_manager.change_token(user_id)))
        return JSONResponse(json_datetime_convert(user))
    except Exception as e:
        logger.error(f"Error in get_user: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.get("/s/{file_id}")
async def get_file(
    file_id: str, output: str = "file", current_user=Depends(get_current_user)
):
    file_storage = FileStorage(current_user.user)
    try:
        file_path = file_storage.get_file(file_id)
        if not file_path:
            raise HTTPException(status_code=404, detail="File not found")

        file_info = await file_storage.get_file_info(file_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File info not found")

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
            filename=str(file_info.file_name),
            headers={
                "Content-Disposition": f'{disposition}; filename="{file_info.file_name}"'
            },
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error getting file: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    file_storage = FileStorage(current_user.user)
    try:
        file_id = await file_storage.save_file(file)
        logger.info(
            f"{file.filename}, {round((file.size or 1) / 1024 / 1024, 2)} MB, uploaded."
        )
        return JSONResponse(
            {
                "file_id": file_id,
                "file_url": f"{BASE_URL}/s/{file_id}",
                "show_image": f"{BASE_URL}/s/{file_id}?output=html",
            },
            status_code=200,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.get("/list")
async def list_files(current_user=Depends(get_current_user)):
    file_storage = FileStorage(current_user.user)
    try:
        return JSONResponse(
            [json_datetime_convert(f) for f in await file_storage.load_files_info()],
            status_code=200,
        )
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.delete("/delete/{file_id}")
async def delete_file(file_id: str, current_user=Depends(get_current_user)):
    file_storage = FileStorage(current_user.user)
    try:
        if await file_storage.delete_file(file_id):
            logger.info(f"File {file_id} deleted")
            return JSONResponse({"message": "File deleted"}, status_code=200)
        return JSONResponse({"error": "File not found"}, status_code=404)
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
