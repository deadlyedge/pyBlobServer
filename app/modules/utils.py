import datetime
import secrets

from pendulum import instance, timezone
from tortoise import models


def json_datetime_convert(data) -> dict:
    tz = timezone("Asia/Hong_Kong")
    if hasattr(data, "__dict__"):
        data = {
            key: value
            for key, value in data.__dict__.items()
            if not key.startswith("_")
        }

    # Convert datetime fields to string
    for key, value in data.items():
        if isinstance(value, datetime.datetime):
            data[key] = tz.convert(instance(value)).to_iso8601_string()
        elif isinstance(value, models.Model):
            data[key] = json_datetime_convert(value)

    return data


def generate_random_string(length: int) -> str:
    pool = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnprstuvwxyz2345678"
    return "".join(secrets.choice(pool) for _ in range(length))
