import os
from dotenv import load_dotenv
from typing import Callable, List
import time

from fastapi import (
    FastAPI,
    Request,
    UploadFile,
    File,
    Depends,
    HTTPException,
    WebSocket,
    status,
    Security,
)
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from contextlib import asynccontextmanager
from loguru import logger

from tortoise import Tortoise
from tortoise.exceptions import DoesNotExist

from app.modules.env import ENV
from app.modules.database_models import UsersInfo
from app.modules.tusserver.metadata import FileMetadata
from app.modules.tusserver.tus import create_api_router
from app.modules.user_manager import UserManager
from app.modules.file_storage import FileStorage


load_dotenv()

# Rate limiting configuration
rate_limit_dict = {}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        # Get client IP
        client_ip = request.client.host if request.client else "unknown"
        current_time = time.time()

        # Clean up old entries
        rate_limit_dict.clear()

        # Check rate limit
        if client_ip in rate_limit_dict:
            requests = rate_limit_dict[client_ip]
            if len(requests) >= ENV.REQUEST_TIMES_PER_MINTUE:
                oldest_request = requests[0]
                if current_time - oldest_request < 60:  # Within 1 minute
                    return JSONResponse(
                        status_code=429, content={"error": "Too many requests"}
                    )
                requests.pop(0)
        else:
            rate_limit_dict[client_ip] = []

        rate_limit_dict[client_ip].append(current_time)
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(
            f"{request.method} {request.url.path} "
            f"Status: {response.status_code} "
            f"Duration: {process_time:.3f}s"
        )
        return response


if not ENV.ALLOWED_USERS:
    logger.error("ALLOWED_USERS is empty, please set it in .env file")
    exit(1)


async def database_connect():
    # Configure database with connection pooling
    await Tortoise.init(
        db_url=ENV.DATABASE_URL,
        modules={"models": ["app.modules.database_models"]},
        _create_db=True,
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


# Add security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    return response


# Add middlewares
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(SessionMiddleware, secret_key=ENV.SECRET_KEY)

# Configure CORS with more specific settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=ENV.ALLOWED_ORIGINS if hasattr(ENV, "ALLOWED_ORIGINS") else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "Location",
        "X-Filename",
        "Tus-Resumable",
        "Tus-Extension",
        "Tus-Version",
        "Tus-Max-Size",
        "Upload-Expires",
        "Upload-Metadata",
        "Upload-Offset",
        "Upload-Length",
    ],
    expose_headers=[
        "Location",
        "X-Filename",
        "Tus-Resumable",
        "Tus-Extension",
        "Tus-Version",
        "Tus-Max-Size",
        "Upload-Expires",
        "Upload-Metadata",
        "Upload-Offset",
        "Upload-Length",
    ],
    max_age=3600,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


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


###############################
# Routes


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/auth")
async def check_token(
    current_user=Depends(get_current_user),
):
    user = (
        {"user": current_user.user, "token": current_user.token}
        if current_user
        else {"message": "Invalid Token"}
    )
    return JSONResponse(content=user, status_code=200)


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


# very basic upload
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


@app.post("/upload_batch")
async def batch_upload_file(
    files: List[UploadFile] = [File(...)],
    current_user=Depends(get_current_user),
):
    resaults = []
    for file in files:
        try:
            resaults.append(await FileStorage(current_user.user).save_file(file))
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            resaults.append(e)

    return JSONResponse(
        resaults,
        status_code=status.HTTP_207_MULTI_STATUS
        if any("status_code" in resault for resault in resaults)
        else status.HTTP_200_OK,
    )


@app.websocket("/upload_socket")
async def websocket_upload_file(websocket: WebSocket):
    await websocket.accept()

    token = await websocket.receive_text()

    current_user = await api_token_auth(token)

    await websocket.send_text(f"user is: {current_user.user}")

    try:
        result = await FileStorage(current_user.user).save_websocket_file(websocket)
        return JSONResponse(result, status_code=200)
    except Exception as e:
        logger.error(f"Error during WebSocket upload: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# TUS upload local tester with uppy
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# handle TUS upload
async def on_upload_complete(file_path: str, metadata: FileMetadata):
    # print("Upload complete")
    # print(file_path)
    # print(metadata)
    try:
        result = await FileStorage(metadata.metadata["userId"]).save_tus_file(metadata)
        return JSONResponse(result, status_code=200)
    except Exception as e:
        logger.error(f"Error during TUS upload: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# Setup TUS upload adapter
app.include_router(
    create_api_router(
        files_dir=os.path.join(ENV.BASE_FOLDER, ENV.TUS_TEMP_FOLDER),
        max_size=ENV.FILE_SIZE_LIMIT_MB * 1024 * 1024,
        on_upload_complete=on_upload_complete,
        auth=get_current_user,
        prefix="upload_tus",
    ),
)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
