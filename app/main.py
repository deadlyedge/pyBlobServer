import os
import pendulum
import json
import uuid

from fastapi import FastAPI, Request, UploadFile, File, Depends, HTTPException, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
BASE_FOLDER = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
# API_KEYS = os.getenv("XD_API_KEY", "")
DEFAULT_SHORT_PATH_LENGTH = os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8)
FILE_SIZE_LIMIT_MB = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))


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
    token: str


class FileInfo(BaseModel):
    file_id: str
    file_name: str
    file_size: int
    upload_time: str


class FilesInfo(BaseModel):
    user: User
    files: list[FileInfo]


async def get_current_user(token: str = Depends(oauth2_scheme)):
    user = api_token_auth(token)
    return user


def api_token_auth(token):
    users_info_path = f"{BASE_FOLDER}/[users_info].json"
    if not os.path.exists(users_info_path):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Users Info Not Found"
        )
    with open(users_info_path, "r") as f:
        users_info = json.load(f)
        # 检查token是否在users_info中
        for user_info in users_info:
            if user_info["token"] == token:
                return User(**user_info)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Token"
        )


# header_scheme = APIKeyHeader(name="XD_API_KEY")
# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


# 写一个类，在根目录下创建一个/file-data文件夹，如果不存在的话，然后把这个文件夹作为一个文件存储数据库，应该根据用户使用的不同的api_key来区分不同的文件夹，然后在这个文件夹下创建一个[file_info]文件，用以记录原始上传的文件信息，然后使用getRandomString作为文件名，把文件保存到这个文件夹下，然后返回这个文件的url


class FileStorage:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.folder = f"{BASE_FOLDER}/{api_key}"
        os.makedirs(self.folder, exist_ok=True)

    def save_file(self, file: UploadFile):
        def getRandomString(length: int):
            import random

            pool = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnprstuvwxyz2345678"
            result_str = "".join(random.choice(pool) for i in range(length))
            return result_str

        if file.size and file.size > FILE_SIZE_LIMIT_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File size exceeds limit",
            )
        file_id = getRandomString(int(DEFAULT_SHORT_PATH_LENGTH))
        file_path = os.path.join(self.folder, file_id)
        with open(file_path, "wb") as f:
            f.write(file.file.read())
        self.save_file_info(file_id, file)
        return file_id

    def save_file_info(self, file_id: str, file: UploadFile):
        files_info_list = []
        file_info = {
            "file_id": file_id,
            "file_name": file.filename,
            "file_size": file.size,
            "upload_time": pendulum.now().to_iso8601_string(),
        }

        files_info_path = os.path.join(self.folder, "[files_info].json")
        # 如果文件不存在，则创建一个空的列表
        if os.path.exists(files_info_path):
            with open(files_info_path, "r", encoding="utf-8") as f:
                files_info_list = json.load(f)

        files_info_list.append(file_info)
        with open(files_info_path, "w", encoding="utf-8") as f:
            json.dump(files_info_list, f, indent=4, ensure_ascii=False)

    def get_file(self, file_id: str):
        file_path = os.path.join(self.folder, file_id)
        if not os.path.exists(file_path):
            return None
        return file_path  # open(file_path, "rb")

    def get_file_info(self, file_id: str):
        files_info_path = os.path.join(self.folder, "[files_info].json")
        if not os.path.exists(files_info_path):
            return None
        with open(files_info_path, "r", encoding="utf-8") as f:
            file_info_list = json.load(f)
        # 遍历列表，找到file_id对应的文件信息
        for file_info in file_info_list:
            if file_info["file_id"] == file_id:
                return file_info
        return None


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/user/{user_id}")
async def get_user(user_id: str):
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

    users_info_path = os.path.join(BASE_FOLDER, "[users_info].json")
    try:
        with open(users_info_path, "r", encoding="utf-8") as f:
            users_info = json.load(f)
    except FileNotFoundError:
        users_info = []

    # Find existing user or create a new one
    for user in users_info:
        if user["user"] == user_id:
            return JSONResponse({"user": user_id, "token": user["token"]})

    # Generate a new API key
    new_key = str(uuid.uuid4())
    new_user = User(user=user_id, token=new_key)
    users_info.append(new_user.model_dump())
    logger.warning(f"New user: {new_user}")

    try:
        os.makedirs(BASE_FOLDER, exist_ok=True)
        with open(users_info_path, "w", encoding="utf-8") as f:
            json.dump(users_info, f, indent=4, ensure_ascii=False)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving user info: {e}",
        )

    return JSONResponse(new_user.model_dump())


@app.get("/s/{file_id}")
async def get_file(
    file_id: str, output: str = "file", current_user: User = Depends(get_current_user)
):
    logger.info(f"user {current_user} getting.")

    # if key not in API_KEYS:
    #     return JSONResponse({"error": "Invalid API Key"}, status_code=401)

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
