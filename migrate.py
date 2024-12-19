from tortoise import Tortoise
import asyncio
from app.modules.env import ENV

async def migrate():
    await Tortoise.init(
        db_url=ENV.DATABASE_URL,
        modules={'models': ['app.modules.database_models']},
        _create_db=True
    )
    await Tortoise.generate_schemas()

if __name__ == "__main__":
    asyncio.run(migrate())
