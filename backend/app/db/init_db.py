import asyncio

from sqlalchemy import text

from app.db.session import engine
from app.models import Base


async def init_db() -> None:
    """Ensure the pgvector extension exists and create all ORM tables."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(init_db())
