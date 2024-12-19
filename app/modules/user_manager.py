import uuid

from fastapi import HTTPException
from loguru import logger
from tortoise.exceptions import DoesNotExist

from .database_models import UsersInfo
from .cache import cache_result, _cache
from .utils import json_datetime_convert


class UserManager:
    def __init__(self, user_id: str):
        self.user_id = user_id

    async def _change_token(self) -> UsersInfo:
        try:
            user = await UsersInfo.get(user=self.user_id)
            user.token = str(uuid.uuid4())
            await user.save()
            _cache.invalidate(f"get_user:{self.user_id}")  # Invalidate cache
            return user
        except DoesNotExist:
            raise HTTPException(status_code=404, detail="User not found")
        except Exception as e:
            logger.error(f"Error changing token: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    @cache_result()
    async def get_user(self, function: str = "") -> dict:
        try:
            if function == "change_token":
                user_dict = json_datetime_convert(await self._change_token())
            else:
                user_dict = json_datetime_convert(
                    await UsersInfo.get(user=self.user_id)
                )
                user_dict = {**user_dict, "token": "[hidden...]"}

            return user_dict
        except DoesNotExist:
            new_user_dict = json_datetime_convert(
                await UsersInfo.create(user=self.user_id, token=str(uuid.uuid4()))
            )
            return new_user_dict
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")
