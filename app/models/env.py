import os
from typing import List


class ENV:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "secret_key")
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    BASE_FOLDER: str = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
    ALLOWED_USERS: List[str] = os.getenv("ALLOWED_USERS", "testuser").split(",")
    ALLOWED_ORIGINS: List[str] = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    DEFAULT_SHORT_PATH_LENGTH: int = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
    FILE_SIZE_LIMIT_MB: int = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
    TOTAL_SIZE_LIMIT_MB: int = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite://./uploads/blobserver.db")
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", 300))  # 5 minutes cache
    REQUEST_TIMES_PER_MINTUE: int = int(os.getenv("REQUEST_TIMES_PER_MINTUE", 100))
