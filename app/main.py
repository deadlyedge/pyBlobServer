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
import sqlite3

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
BASE_FOLDER = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
DEFAULT_SHORT_PATH_LENGTH = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
FILE_SIZE_LIMIT_MB = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
TOTAL_SIZE_LIMIT_MB = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))
DATABASE_FILE = os.getenv(
    "DATABASE_FILE", f"{os.getcwd()}/uploads/blobserver.db"
)  # Added Database file path

if not ALLOWED_USERS:
    logger.error("ALLOWED_USERS is empty, please set it in .env file")
    exit(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Close the database connection on shutdown
    file_storage = FileStorage("dummy")  # Create dummy object to access close method
    yield
    file_storage.close()
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


class User(BaseModel):
    user: str
    token: str


class FileInfo(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    upload_time: str


# No longer needed since we use SQLite
# class UserInfo(User):
#     files: List[FileInfo] = []
#     total_size: int = 0
#     total_uploads: int = 0
#     total_downloads: int = 0
#     created_at: str = pendulum.now().to_iso8601_string()
#     last_upload_at: Optional[str] = None
#     last_download_at: Optional[str] = None


class UserManager:
    def __init__(self):
        os.makedirs(BASE_FOLDER, exist_ok=True)
        self.conn = sqlite3.connect(DATABASE_FILE)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user TEXT PRIMARY KEY,
                token TEXT,
                total_size INTEGER DEFAULT 0,
                total_uploads INTEGER DEFAULT 0,
                total_downloads INTEGER DEFAULT 0,
                created_at TEXT,
                last_upload_at TEXT,
                last_download_at TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                file_id TEXT PRIMARY KEY,
                user TEXT,
                file_name TEXT,
                file_size INTEGER,
                upload_time TEXT,
                FOREIGN KEY (user) REFERENCES users(user)
            )
        """)
        self.conn.commit()

    def _load_user(self, user_id: str) -> Optional[dict]:
        self.cursor.execute("SELECT * FROM users WHERE user = ?", (user_id,))
        row = self.cursor.fetchone()
        if row:
            return dict(zip([d[0] for d in self.cursor.description], row))
        return None

    def _save_user(self, user_info: dict):
        self.cursor.execute(
            """
            INSERT OR REPLACE INTO users (user, token, total_size, total_uploads, total_downloads, created_at, last_upload_at, last_download_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_info["user"],
                user_info["token"],
                user_info.get("total_size", 0),
                user_info.get("total_uploads", 0),
                user_info.get("total_downloads", 0),
                user_info.get("created_at"),
                user_info.get("last_upload_at"),
                user_info.get("last_download_at"),
            ),
        )
        self.conn.commit()

    def _initial_user(self, user_id: str) -> dict:
        new_user = {
            "user": user_id,
            "token": str(uuid.uuid4()),
            "created_at": pendulum.now().to_iso8601_string(),
        }
        self._save_user(new_user)
        return new_user

    def get_user(self, user_id: str) -> dict:
        user = self._load_user(user_id)
        if user:
            return user
        return self._initial_user(user_id)

    def change_token(self, user_id: str) -> dict:
        user = self.get_user(user_id)
        user["token"] = str(uuid.uuid4())
        self._save_user(user)
        return user

    def update_user(self, user_info: dict):
        self._save_user(user_info)

    def api_token_auth(self, token):
        self.cursor.execute("SELECT * FROM users WHERE token = ?", (token,))
        row = self.cursor.fetchone()
        if row:
            return User(user=row[0], token=row[1])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Token"
        )

    def close(self):
        self.conn.close()


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
        self.user_manager = UserManager()

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
        user_info = self.user_manager.get_user(self.user)
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
        update_data["user"] = self.user
        update_data["token"] = user_info["token"]
        update_data["created_at"] = user_info.get("created_at")
        update_data["last_download_at"] = user_info.get("last_download_at", None)
        self.user_manager.update_user(update_data)

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
        self.user_manager.cursor.execute(
            """
            INSERT INTO files (file_id, user, file_name, file_size, upload_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                file_id,
                self.user,
                file.filename,
                file.size,
                pendulum.now().to_iso8601_string(),
            ),
        )
        self.user_manager.conn.commit()

    def load_files_info(self) -> List[FileInfo]:
        self.user_manager.cursor.execute(
            "SELECT * FROM files WHERE user = ?", (self.user,)
        )
        rows = self.user_manager.cursor.fetchall()
        return [
            FileInfo(
                file_id=row[0],
                file_name=row[2],
                file_size=row[3],
                upload_time=row[4],
            )
            for row in rows
        ]

    def save_file(self, file: UploadFile) -> str:
        self._validate_file_size(file)
        self._update_user_usage(file.size)
        file_id = self._get_random_string(DEFAULT_SHORT_PATH_LENGTH)
        file_path = self._get_file_path(file_id)
        self._write_file(file, file_path)
        self._save_file_info(file_id, file)
        return file_id

    def get_file(self, file_id: str) -> Optional[str]:
        file_path = self._get_file_path(file_id)
        return file_path if os.path.exists(file_path) else None

    def get_file_info(self, file_id: str) -> Optional[FileInfo]:
        self.user_manager.cursor.execute(
            "SELECT * FROM files WHERE file_id = ?", (file_id,)
        )
        row = self.user_manager.cursor.fetchone()
        if row:
            return FileInfo(
                file_id=row[0],
                file_name=row[2],
                file_size=row[3],
                upload_time=row[4],
            )
        return None

    def delete_file(self, file_id: str) -> bool:
        file_path = self._get_file_path(file_id)
        if os.path.exists(file_path):
            file_info = self.get_file_info(file_id)
            if file_info:
                os.remove(file_path)
                self._update_user_usage(file_info.file_size, is_deletion=True)
                self.user_manager.cursor.execute(
                    "DELETE FROM files WHERE file_id = ?", (file_id,)
                )
                self.user_manager.conn.commit()
                return True
        return False

    def close(self):
        self.user_manager.close()


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
