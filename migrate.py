from tortoise import Tortoise
import asyncio
from app.models import ENV

async def migrate():
    await Tortoise.init(
        db_url=ENV.DATABASE_URL,
        modules={'models': ['app.models']},
        _create_db=True
    )
    await Tortoise.generate_schemas()

if __name__ == "__main__":
    asyncio.run(migrate())
