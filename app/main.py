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

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
BASE_FOLDER = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
DEFAULT_SHORT_PATH_LENGTH = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
FILE_SIZE_LIMIT_MB = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
TOTAL_SIZE_LIMIT_MB = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))

FILES_INFO_FILENAME = "[files_info].json"
USERS_INFO_FILENAME = "[users_info].json"

if not ALLOWED_USERS:
    logger.error("API_KEYS is empty, please set it in .env file")
    exit(1)

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
    user: str
    token: str


class FileInfo(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    upload_time: str


class UserInfo(User):
    files: List[FileInfo] = []
    total_size: int = 0
    total_uploads: int = 0
    total_downloads: int = 0
    created_at: str = pendulum.now().to_iso8601_string()
    last_upload_at: Optional[str] = None
    last_download_at: Optional[str] = None


class UserManager:
    def __init__(self):
        self.users_info_path = os.path.join(BASE_FOLDER, USERS_INFO_FILENAME)
        os.makedirs(BASE_FOLDER, exist_ok=True)

    def _load_users_info(self) -> List[UserInfo]:
        if not os.path.exists(self.users_info_path):
            return []
        try:
            with open(self.users_info_path, "r", encoding="utf-8") as f:
                return [UserInfo(**user_data) for user_data in json.load(f)]
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load user info: {e}")
            return []

    def _save_users_info(self, users_info_list: List[UserInfo]):
        try:
            with open(self.users_info_path, "w", encoding="utf-8") as f:
                json.dump(
                    [user.model_dump() for user in users_info_list],
                    f,
                    indent=4,
                    ensure_ascii=False,
                )
        except IOError as e:
            logger.error(f"Failed to save user info: {e}")

    def _initial_user(self, user_id: str) -> UserInfo:
        new_user = UserInfo(user=user_id, token=str(uuid.uuid4()))
        self.update_user(new_user)
        return new_user

    def get_user(self, user_id: str) -> UserInfo:
        users_info = self._load_users_info()
        for user in users_info:
            if user.user == user_id:
                return user
        return self._initial_user(user_id)

    def change_token(self, user_id: str) -> UserInfo:
        user = self.get_user(user_id)
        user.token = str(uuid.uuid4())
        self.update_user(user)
        return user

    def update_user(self, user_info: UserInfo):
        users_info = self._load_users_info()
        user_index = next(
            (i for i, user in enumerate(users_info) if user.user == user_info.user),
            None,
        )
        if user_index is not None:
            users_info[user_index] = user_info
        else:
            users_info.append(user_info)
        self._save_users_info(users_info)

    def api_token_auth(self, token):
        for user_info in self._load_users_info():
            if user_info.token == token:
                return User(user=user_info.user, token=user_info.token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Token"
        )


async def get_current_user(token: str = Security(oauth2_scheme)):
    return UserManager().api_token_auth(token)


class FileStorage:
    def __init__(self, user: str):
        self.folder = os.path.join(BASE_FOLDER, user)
        self.files_info_path = os.path.join(self.folder, FILES_INFO_FILENAME)
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
        file_size = file_size or 0
        update_info = self.user_manager.get_user(self.user)
        if is_deletion:
            update_info.total_size -= file_size
        else:
            if (
                file_size > 0
                and update_info.total_size + file_size
                > TOTAL_SIZE_LIMIT_MB * 1024 * 1024
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Total size limit exceeded",
                )
            update_info.total_uploads += file_size
            update_info.total_size += file_size
            update_info.last_upload_at = pendulum.now().to_iso8601_string()
        self.user_manager.update_user(update_info)

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
        files_info_list = self.load_files_info()
        file_info = FileInfo(
            file_id=file_id,
            file_name=file.filename or "",
            file_size=file.size or 0,
            upload_time=pendulum.now().to_iso8601_string(),
        )
        files_info_list.append(file_info)
        self._write_files_info(files_info_list)

    def load_files_info(self) -> List[FileInfo]:
        if os.path.exists(self.files_info_path):
            try:
                with open(self.files_info_path, "r", encoding="utf-8") as f:
                    return [FileInfo(**item) for item in json.load(f)]
            except (IOError, json.JSONDecodeError) as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to load file info: {e}",
                )
        return []

    def _write_files_info(self, files_info_list: List[FileInfo]):
        try:
            with open(self.files_info_path, "w", encoding="utf-8") as f:
                json.dump(
                    [data.model_dump() for data in files_info_list],
                    f,
                    indent=4,
                    ensure_ascii=False,
                )
        except IOError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to write file info: {e}",
            )

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
        files_info_list = self.load_files_info()
        return next(
            (
                file_info
                for file_info in files_info_list
                if file_info.file_id == file_id
            ),
            None,
        )

    def delete_file(self, file_id: str) -> bool:
        file_path = self._get_file_path(file_id)
        if os.path.exists(file_path):
            os.remove(file_path)
            files_info_list = [
                file_info
                for file_info in self.load_files_info()
                if file_info.file_id != file_id
            ]
            self._write_files_info(files_info_list)
            return True
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
    if f == "change_token":
        return JSONResponse(user_manager.change_token(user_id).model_dump())
    return JSONResponse(user_manager.get_user(user_id).model_dump())


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
