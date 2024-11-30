from contextlib import asynccontextmanager
import os
import pendulum
import json
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
from pydantic import BaseModel
from loguru import logger
from dotenv import load_dotenv
from prisma import Prisma

load_dotenv()

# Environment variables
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
BASE_FOLDER = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
DEFAULT_SHORT_PATH_LENGTH = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
FILE_SIZE_LIMIT_MB = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
TOTAL_SIZE_LIMIT_MB = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))

if not ALLOWED_USERS:
    logger.error("ALLOWED_USERS is empty, please set it in .env file")
    exit(1)

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     db = Prisma()
#     await db.connect()
#     yield db
#     await db.disconnect()
#     logger.info("Shutting down...")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

class User(BaseModel):
    user_id: str
    token: str

class FileInfo(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    upload_time: str

class UserManager:
    def __init__(self, db: Prisma):
        self.db = db
        os.makedirs(BASE_FOLDER, exist_ok=True)

    async def get_user(self, user_id: str) -> dict:
        user = await self.db.user.find_unique(where={'user_id': user_id})
        if user:
            return user.dict()
        return await self._initial_user(user_id)

    async def _initial_user(self, user_id: str) -> dict:
        new_user = {
            "user_id": user_id,
            "token": str(uuid.uuid4()),
            # "created_at": pendulum.now().to_iso8601_string(),
        }
        await self.db.user.create(data=new_user)
        return new_user

    async def change_token(self, user_id: str) -> dict:
        user = await self.get_user(user_id)
        user["token"] = str(uuid.uuid4())
        await self.db.user.update(where={'user_id': user_id}, data={'token': user["token"]})
        return user

    async def update_user(self, user_info: dict):
        await self.db.user.update(where={'user_id': user_info["user_id"]}, data=user_info)

    async def api_token_auth(self, token: str):
        user = await self.db.user.find_unique(where={'token': token})
        if user:
            return User(user=user.user_id, token=user.token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Token"
        )

async def get_current_user(token: str = Security(oauth2_scheme)):
    db = Prisma()
    await db.connect()
    user_manager = UserManager(db)
    user = await user_manager.api_token_auth(token)
    await db.disconnect()
    return user

class FileStorage:
    def __init__(self, user: str, db: Prisma):
        self.folder = os.path.join(BASE_FOLDER, user)
        os.makedirs(self.folder, exist_ok=True)
        self.user = user
        self.db = db

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

    async def _update_user_usage(
        self, file_size: Optional[int] = 0, is_deletion: bool = False
    ):
        user_manager = UserManager(self.db)
        user_info = await user_manager.get_user(self.user)
        update_data = {}
        if is_deletion:
            update_data["total_size"] = user_info["total_size"] - file_size
        else:
            new_total_size = user_info.get("total_size", 0) + file_size
            if new_total_size > TOTAL_SIZE_LIMIT_MB * 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Total size limit exceeded",
                )
            update_data["total_size"] = new_total_size
            update_data["total_uploads"] = user_info.get("total_uploads", 0) + 1
            update_data["last_upload_at"] = pendulum.now().to_iso8601_string()
        update_data["user_id"] = self.user
        await user_manager.update_user(update_data)

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
        await self.db.file.create(data={
            "file_id": file_id,
            "user_id": self.user,
            "file_name": file.filename,
            "file_size": file.size,
            "created_at": pendulum.now().to_iso8601_string(),
        })

    async def load_files_info(self) -> List[FileInfo]:
        files = await self.db.file.find_many(where={'user_id': self.user})
        return [
            FileInfo(
                file_id=file.file_id,
                file_name=file.file_name,
                file_size=file.file_size,
                upload_time=file.created_at,
            )
            for file in files
        ]

    async def save_file(self, file: UploadFile) -> str:
        self._validate_file_size(file)
        await self._update_user_usage(file.size)
        file_id = self._get_random_string(DEFAULT_SHORT_PATH_LENGTH)
        file_path = self._get_file_path(file_id)
        self._write_file(file, file_path)
        await self._save_file_info(file_id, file)
        return file_id

    async def get_file_info(self, file_id: str) -> Optional[FileInfo]:
        file = await self.db.file.find_unique(where={'file_id': file_id})
        if file:
            return FileInfo(
                file_id=file.file_id,
                file_name=file.file_name,
                file_size=file.file_size,
                upload_time=file.created_at,
            )
        return None

    async def delete_file(self, file_id: str) -> bool:
        file_info = await self.get_file_info(file_id)
        if file_info:
            file_path = self._get_file_path(file_id)
            if os.path.exists(file_path):
                os.remove(file_path)
                await self._update_user_usage(file_info.file_size, is_deletion=True)
                await self.db.file.delete(where={'file_id': file_id})
                return True
        return False

# ... (rest of the code remains largely the same, except for the parts interacting with JSON files which are now replaced with the SQLite interaction)


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
    file_id: str, output: str = "file", current_user: User = Depends(get_current_user)
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
        return JSONResponse(file_info.dict(), status_code=200)
    disposition = "inline" if output == "download" else "attachment"
    return FileResponse(
        file,
        filename=file_info.file_name,
        headers={
            "Content-Disposition": f'{disposition}; filename="{file_info.file_name}"'
        },
    )


@app.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
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
async def list_files(current_user: User = Depends(get_current_user)):
    file_storage = FileStorage(current_user.user)
    return JSONResponse(
        [file_info.model_dump() for file_info in file_storage.load_files_info()],
        status_code=200,
    )


@app.delete("/delete/{file_id}")
async def delete_file(file_id: str, current_user: User = Depends(get_current_user)):
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
