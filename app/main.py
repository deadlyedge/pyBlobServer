from contextlib import asynccontextmanager
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

# from pydantic import BaseModel
from loguru import logger
from dotenv import load_dotenv
import peewee as pw
from playhouse.shortcuts import model_to_dict

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
BASE_FOLDER = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
DEFAULT_SHORT_PATH_LENGTH = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
FILE_SIZE_LIMIT_MB = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
TOTAL_SIZE_LIMIT_MB = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))
DATABASE_FILE = os.getenv("DATABASE_FILE", f"{os.getcwd()}/uploads/blobserver.db")

if not ALLOWED_USERS:
    logger.error("ALLOWED_USERS is empty, please set it in .env file")
    exit(1)

# Peewee database setup
db = pw.SqliteDatabase(DATABASE_FILE)


class BaseModel(pw.Model):
    class Meta:
        database = db


class UsersInfo(BaseModel):
    user = pw.CharField(primary_key=True)
    token = pw.CharField()
    total_size = pw.IntegerField(default=0)
    total_uploads = pw.IntegerField(default=0)
    total_downloads = pw.IntegerField(default=0)
    created_at = pw.CharField(default=pendulum.now().to_iso8601_string)
    last_upload_at = pw.CharField(null=True)
    last_download_at = pw.CharField(null=True)


class FileInfo(BaseModel):
    file_id = pw.CharField(primary_key=True)
    user = pw.ForeignKeyField(UsersInfo, backref="files")
    file_name = pw.CharField()
    file_size = pw.IntegerField()
    upload_time = pw.CharField(default=pendulum.now().to_iso8601_string)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)
        db.connect()
        db.create_tables([UsersInfo, FileInfo], safe=True)
        logger.info("Database connected successfully.")
        yield
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        raise  # Re-raise the exception to halt startup
    finally:
        db.close()
        logger.info("Database closed.")
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


# class User(BaseModel):
#     user: str
#     token: str


# class FileInfo(BaseModel):
#     file_id: str
#     file_name: str
#     file_size: int
#     upload_time: str


class UserManager:
    def _load_user(self, user_id: str) -> Optional[dict]:
        try:
            user = UsersInfo.get(UsersInfo.user == user_id)
            return model_to_dict(user)

        except Exception as e:
            logger.error(f"Error loading user: {e}")
            return None

    # def _save_user(self, user_info: dict):
    #     user, created = UsersInfo.get_or_create(
    #         user=user_info["user"],
    #         defaults={
    #             "token": user_info["token"],
    #             "created_at": pendulum.parse(user_info["created_at"]),
    #         },
    #     )
    #     if not created:
    #         # user.token = user_info["token"]
    #         # user.total_size = user_info.get("total_size", 0)
    #         # user.total_uploads = user_info.get("total_uploads", 0)
    #         # user.total_downloads = user_info.get("total_downloads", 0)
    #         # if user_info.get("last_upload_at"):
    #         #     user.last_upload_at = pendulum.parse(user_info["last_upload_at"])
    #         # if user_info.get("last_download_at"):
    #         #     user.last_download_at = pendulum.parse(user_info["last_download_at"])
    #         user = UsersInfo.update(**user_info)
    #         user.save()

    def _initial_user(self, user_id: str) -> dict:
        new_user = UsersInfo.create(user=user_id, token=str(uuid.uuid4()))
        return {
            "user": new_user.user,
            "token": new_user.token,
            "created_at": new_user.created_at,
        }

    def get_user(self, user_id: str) -> dict:
        user = self._load_user(user_id)
        if user:
            return user
        return self._initial_user(user_id)

    def change_token(self, user_id: str) -> dict:
        user = UsersInfo.get(UsersInfo.user == user_id)
        user.token = str(uuid.uuid4())
        user.save()
        return self.get_user(user_id)

    # def update_user(self, user_info: dict):
    #     self._save_user(user_info)

    def api_token_auth(self, token):
        try:
            user = UsersInfo.get(UsersInfo.token == token)
            return UsersInfo(user=user.user, token=user.token)
        except Exception as e:
            logger.error(f"Error authenticating user: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Token"
            )

    def close(self):
        db.close()


async def get_current_user(token: str = Security(oauth2_scheme)):
    user_manager = UserManager()
    user = user_manager.api_token_auth(token)
    user_manager.close()
    return user


class FileStorage:
    def __init__(self, user: str):
        self.folder = os.path.join(BASE_FOLDER, user)
        os.makedirs(self.folder, exist_ok=True)
        self.user = user

    @staticmethod
    def _get_random_string(length: int) -> str:
        pool = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnprstuvwxyz2345678"
        return "".join(random.choice(pool) for _ in range(length))

    def _validate_file_size(self, file: UploadFile):
        if file.size and file.size > FILE_SIZE_LIMIT_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File size exceeds limit",
            )

    def _update_user_usage(
        self, file_size: Optional[int] = 0, is_deletion: bool = False
    ):
        user = UsersInfo.get(UsersInfo.user == self.user)
        with db.atomic():
            if not is_deletion:
                if user.total_size + file_size > TOTAL_SIZE_LIMIT_MB * 1024 * 1024:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Total size limit exceeded",
                    )
                user.total_uploads += file_size
                user.last_upload_at = pendulum.now().to_iso8601_string()
            user.total_size = self._get_total_size()
            user.save()

    def _get_total_size(self) -> int:
        return sum(
            file.file_size
            for file in FileInfo.select().where(FileInfo.user == self.user)
        )

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

    def _save_file_info(self, file_id: str, file: UploadFile):
        FileInfo.create(
            file_id=file_id,
            user=self.user,  # UsersInfo.get(UsersInfo.user == self.user),
            file_name=file.filename,
            file_size=file.size,
        )

    def load_files_info(self) -> List[FileInfo]:
        return [
            FileInfo(
                file_id=file.file_id,
                file_name=file.file_name,
                file_size=file.file_size,
                upload_time=file.upload_time,
            )
            for file in FileInfo.select().where(FileInfo.user == self.user)
        ]

    def save_file(self, file: UploadFile) -> str:
        self._validate_file_size(file)
        file_id = self._get_random_string(DEFAULT_SHORT_PATH_LENGTH)
        file_path = self._get_file_path(file_id)
        self._write_file(file, file_path)
        self._save_file_info(file_id, file)
        self._update_user_usage(file.size)
        return file_id

    def get_file(self, file_id: str) -> Optional[str]:
        file_path = self._get_file_path(file_id)
        return file_path if os.path.exists(file_path) else None

    def get_file_info(self, file_id: str) -> Optional[FileInfo]:
        try:
            file = FileInfo.get(FileInfo.file_id == file_id)
            return FileInfo(
                file_id=file.file_id,
                file_name=file.file_name,
                file_size=file.file_size,
                upload_time=file.upload_time,
            )
        except Exception as e:
            logger.error(f"Error loading file info: {e}")

            return None

    def delete_file(self, file_id: str) -> bool:
        try:
            file = FileInfo.get(FileInfo.file_id == file_id)
            file.delete_instance()
            file_path = self._get_file_path(file_id)
            if os.path.exists(file_path):
                os.remove(file_path)
            self._update_user_usage(file.file_size, is_deletion=True)
            return True
        except Exception as e:
            logger.error(f"Error deleting file info: {e}")

            return False

    def close(self):
        db.close()


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
    if f == "change_token":
        return JSONResponse(user_manager.change_token(user_id))
    return JSONResponse(user_manager.get_user(user_id))


@app.get("/s/{file_id}")
async def get_file(
    file_id: str, output: str = "file", current_user=Depends(get_current_user)
):
    logger.info(f"user {current_user} getting.")
    file_storage = FileStorage(current_user.user)
    file = file_storage.get_file(file_id)
    if not file:
        return JSONResponse({"error": "File not found"}, status_code=404)

    file_info = file_storage.get_file_info(file_id)
    if not file_info:
        return JSONResponse({"error": "File info not found"}, status_code=404)

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
        return JSONResponse(file_info, status_code=200)
    disposition = "inline" if output == "download" else "attachment"
    return FileResponse(
        file,
        filename=str(file_info.file_name),
        headers={
            "Content-Disposition": f'{disposition}; filename="{file_info.file_name}"'
        },
    )


@app.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    logger.info(f"user {current_user} is here.")
    file_storage = FileStorage(current_user.user)
    file_id = file_storage.save_file(file)
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


@app.get("/list")
async def list_files(current_user=Depends(get_current_user)):
    file_storage = FileStorage(current_user.user)
    return JSONResponse(
        [model_to_dict(file_info) for file_info in file_storage.load_files_info()],
        status_code=200,
    )


@app.delete("/delete/{file_id}")
async def delete_file(file_id: str, current_user=Depends(get_current_user)):
    file_storage = FileStorage(current_user.user)
    if file_storage.delete_file(file_id):
        logger.info(f"File {file_id} deleted")
        return JSONResponse({"message": "File deleted"}, status_code=200)
    return JSONResponse({"error": "File not found"}, status_code=404)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
