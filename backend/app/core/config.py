from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the backend/ directory so it loads regardless of
# the working directory the app or tests are invoked from.
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"

# Fixed by the pgvector column schema (app/models/chunk.py). Not environment
# configurable: changing this requires a DB migration, not a .env edit.
EMBEDDING_DIMENSIONS = 1536


class Settings(BaseSettings):
    """Application configuration loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # General
    app_name: str = "SourceGuard"
    environment: str = "development"

    # Database (loaded from DATABASE_URL in .env)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sourceguard"

    # Redis / Upstash cache
    redis_url: str = "redis://localhost:6379/0"

    # AI providers
    groq_api_key: str = ""
    together_api_key: str = ""
    together_api_base: str = "https://api.together.xyz/v1"
    embedding_model: str = "togethercomputer/m2-bert-80M-32k-retrieval"

    # Chunking defaults
    chunk_size: int = 1000
    chunk_overlap: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
