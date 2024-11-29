import os
from typing import Optional
import pendulum
import json
import uuid
import random

from fastapi import FastAPI, Request, UploadFile, File, Depends, HTTPException, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)
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
FILES_INFO_FILENAME = "[files_info].json"
USERS_INFO_FILENAME = "[users_info].json"


if ALLOWED_USERS == "":
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


class UploadResponse(BaseModel):
    url: str


class User(BaseModel):
    user: str
    token: str | None


class FileInfo(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    upload_time: str


# class FilesInfo(BaseModel):
#     user: User
#     files: list[FileInfo]


class UserInfo(User):
    files: list[FileInfo] = []
    total_size: int = 0
    total_uploads: int = 0
    total_downloads: int = 0
    created_at: str = pendulum.now().to_iso8601_string()
    last_upload_at: Optional[str] = None
    last_download_at: Optional[str] = None


class UserManage:
    def __init__(self):
        self.users_info_path = os.path.join(BASE_FOLDER, USERS_INFO_FILENAME)

    def _load_users_info(self) -> list[UserInfo]:
        """Loads user information from the JSON file.  Handles file I/O errors."""
        if not os.path.exists(self.users_info_path):
            return []  # Return empty list if file doesn't exist.

        try:
            with open(self.users_info_path, "r", encoding="utf-8") as f:
                users_info_list = json.load(f)
                return [
                    UserInfo(**user_data) for user_data in users_info_list
                ]  # Convert to UserInfo objects
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load user info: {e}")
            return []

    def _save_users_info(self, users_info_list: list[UserInfo]):
        """Saves user information to the JSON file. Handles file I/O errors."""
        try:
            os.makedirs(
                os.path.dirname(self.users_info_path), exist_ok=True
            )  # Ensure directory exists
            with open(self.users_info_path, "w", encoding="utf-8") as f:
                json.dump(
                    [user.model_dump() for user in users_info_list],
                    f,
                    indent=4,
                    ensure_ascii=False,
                )
        except IOError as e:
            logger.error(f"Failed to save user info: {e}")

    def get_user(self, user_id: str) -> UserInfo:
        """Retrieves user information by user ID."""
        users_info = self._load_users_info()
        for user in users_info:
            if user.user == user_id:
                return user
        return self.initial_user(user_id)

    def change_token(self, user_id: str) -> UserInfo:
        """Changes the token for a user."""
        user = self.get_user(user_id)
        user.token = str(uuid.uuid4())
        self.update_user(user)
        return user

    def initial_user(self, user_id: str) -> UserInfo:
        """Initializes a new user."""
        new_user = UserInfo(user=user_id, token=str(uuid.uuid4()))
        self.update_user(new_user)
        return new_user

    def update_user(self, user_info: UserInfo):
        """Updates user information.  If the user doesn't exist, adds them."""
        users_info = self._load_users_info()
        user_index = next(
            (i for i, user in enumerate(users_info) if user.user == user_info.user),
            None,
        )

        if user_index is not None:
            users_info[user_index] = user_info  # Update existing user
        else:
            users_info.append(user_info)  # Add new user

        self._save_users_info(users_info)

    # def delete_user(self, user_id: str) -> bool:
    #     """Deletes a user."""
    #     users_info = self._load_users_info()
    #     users_info = [user for user in users_info if user.user != user_id]
    #     self._save_users_info(users_info)
    #     return True  # Or return whether a user was actually deleted

    # Example Usage (needs to be integrated into the main application)
    # users_info_path = os.path.join(BASE_FOLDER, "[users_info].json")
    # user_manager = UserManage(users_info_path)

    # new_user = UserInfo(user="testuser", token="testtoken", files=[], total_size=0, total_uploads=0, total_downloads=0)
    # user_manager.update_user(new_user)

    # retrieved_user = user_manager.get_user("testuser")
    # print(retrieved_user)

    # user_manager.delete_user("testuser")

    def api_token_auth(self, token):
        # users_info_path = f"{BASE_FOLDER}/[users_info].json"
        # if not os.path.exists(users_info_path):
        #     raise HTTPException(
        #         status_code=status.HTTP_401_UNAUTHORIZED, detail="Users Info Not Found"
        #     )
        # with open(self.users_info_path, "r") as f:
        #     users_info = json.load(f)
        # 检查token是否在users_info中
        for user_info in self._load_users_info():
            if user_info.token == token:
                return User(user=user_info.user, token=user_info.token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Token"
        )


async def get_current_user(token: str = Depends(oauth2_scheme)):
    return UserManage().api_token_auth(token)


class FileStorage:
    def __init__(self, user: str):
        self.folder = f"{BASE_FOLDER}/{user}"
        self.files_info_path = os.path.join(self.folder, FILES_INFO_FILENAME)
        os.makedirs(self.folder, exist_ok=True)

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
        files_info_list = self._load_files_info()

        file_info = {
            "file_id": file_id,
            "file_name": file.filename,
            "file_size": file.size,
            "upload_time": pendulum.now().to_iso8601_string(),
        }
        files_info_list.append(file_info)

        self._write_files_info(files_info_list)

    def _load_files_info(self) -> list:
        if os.path.exists(self.files_info_path):
            try:
                with open(self.files_info_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (IOError, json.JSONDecodeError) as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to load file info: {e}",
                )
        return []

    def _write_files_info(self, files_info_list: list):
        try:
            with open(self.files_info_path, "w", encoding="utf-8") as f:
                json.dump(files_info_list, f, indent=4, ensure_ascii=False)
        except IOError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to write file info: {e}",
            )

    def save_file(self, file: UploadFile) -> str:
        self._validate_file_size(file)
        file_id = self._get_random_string(DEFAULT_SHORT_PATH_LENGTH)
        file_path = self._get_file_path(file_id)
        self._write_file(file, file_path)
        self._save_file_info(file_id, file)
        return file_id

    def get_file(self, file_id: str) -> str | None:
        file_path = self._get_file_path(file_id)
        return file_path if os.path.exists(file_path) else None

    def get_file_info(self, file_id: str) -> dict | None:
        files_info_list = self._load_files_info()
        for file_info in files_info_list:
            if file_info["file_id"] == file_id:
                return file_info
        return None

    def get_files_info(self) -> list:
        return self._load_files_info()

    def delete_file(self, file_id: str) -> bool:
        file_path = self._get_file_path(file_id)

        if os.path.exists(file_path):
            os.remove(file_path)
            files_info_list = self._load_files_info()
            files_info_list = [
                file_info
                for file_info in files_info_list
                if file_info["file_id"] != file_id
            ]
            self._write_files_info(files_info_list)
            return True
        return False


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/user/{user_id}")
async def get_user(user_id: str, f: str = ""):
    """Retrieves user information and generates a new API key if the user doesn't exist.

    Args:
        user_id: The ID of the user.

    Returns:
        A JSON response containing the user ID and API key.  Returns a 403 error if the user is not allowed.
    """
    # Check if user is allowed
    if user_id not in ALLOWED_USERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User not allowed"
        )

    match f:
        case "change_token":
            return JSONResponse(UserManage().change_token(user_id).model_dump())
        case _:
            return JSONResponse(UserManage().get_user(user_id).model_dump())


@app.get("/s/{file_id}")
async def get_file(
    file_id: str,
    output: str = "file",
    current_user: User = Depends(get_current_user),
):
    logger.info(f"user {current_user} getting.")

    file_storage = FileStorage(current_user.user)
    file = file_storage.get_file(file_id)
    if file is None:
        return JSONResponse({"error": "File not found"}, status_code=404)

    file_info = file_storage.get_file_info(file_id)
    if file_info is None:
        return JSONResponse({"error": "File info not found"}, status_code=404)

    match output:
        case "html":
            html_content = f"""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <title>Image</title>
                    </head>
                    <body>
                        <h1>Image{file_info["file_name"]}</h1>
                        <img src="/s/{file_id}" alt="Uploaded Image" style="max-width:100%">
                    </body>
                    </html>
                    """
            return HTMLResponse(content=html_content, status_code=200)
        case "json":
            return JSONResponse(file_info, status_code=200)
        case "download":
            return FileResponse(
                file,
                filename=file_info["file_name"],
                headers={
                    "Content-Disposition": f'attachment; filename="{file_info["file_name"]}"'
                },
            )
        case _:
            return FileResponse(
                file,
                filename=file_info["file_name"],
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
            "file_id": str(file_id),
            "file_url": f"{BASE_URL}/s/{file_id}",
            "show_image": f"{BASE_URL}/s/{file_id}?output=html",
        },
        status_code=200,
    )


@app.get("/list")
async def list_files(current_user: User = Depends(get_current_user)):
    file_storage = FileStorage(current_user.user)
    files_info = file_storage.get_files_info()
    return JSONResponse(files_info, status_code=200)


@app.delete("/delete/{file_id}")
async def delete_file(file_id: str, current_user: User = Depends(get_current_user)):
    file_storage = FileStorage(current_user.user)
    if file_storage.delete_file(file_id):
        logger.info(f"File {file_id} deleted")
        return JSONResponse({"message": "File deleted"}, status_code=200)
    else:
        return JSONResponse({"error": "File not found"}, status_code=404)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
