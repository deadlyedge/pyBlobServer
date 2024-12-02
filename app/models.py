import datetime
import os
from typing import List
from pendulum import instance, timezone
from tortoise import fields, models


class ENV:
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    BASE_FOLDER: str = os.getenv("BASE_FOLDER", f"{os.getcwd()}/uploads")
    ALLOWED_USERS: List[str] = os.getenv("ALLOWED_USERS", "").split(",")
    DEFAULT_SHORT_PATH_LENGTH: int = int(os.getenv("DEFAULT_SHORT_PATH_LENGTH", 8))
    FILE_SIZE_LIMIT_MB: int = int(os.getenv("FILE_SIZE_LIMIT_MB", 10))
    TOTAL_SIZE_LIMIT_MB: int = int(os.getenv("TOTAL_SIZE_LIMIT_MB", 500))
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite://./uploads/blobserver.db")


class UsersInfo(models.Model):
    user = fields.CharField(max_length=255, pk=True)
    token = fields.CharField(max_length=255)
    total_size = fields.IntField(default=0)
    total_uploads = fields.IntField(default=0)
    total_downloads = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_upload_at = fields.DatetimeField(null=True)
    last_download_at = fields.DatetimeField(null=True)

    class Meta:
        ordering = ["-created_at"]


class FileInfo(models.Model):
    file_id = fields.CharField(max_length=255, pk=True)
    user = fields.ForeignKeyField("models.UsersInfo", related_name="files")
    file_name = fields.CharField(max_length=255)
    file_size = fields.IntField()
    upload_time = fields.DatetimeField(auto_now_add=True)

    class Meta:
        ordering = ["-upload_time"]


def json_datetime_convert(data) -> dict:
    tz = timezone("Asia/Hong_Kong")
    data = {
        key: value for key, value in data.__dict__.items() if not key.startswith("_")
    }

    # Convert datetime fields to string
    for key, value in data.items():
        if isinstance(value, datetime.datetime):
            data[key] = tz.convert(instance(value)).to_datetime_string()

    return data
