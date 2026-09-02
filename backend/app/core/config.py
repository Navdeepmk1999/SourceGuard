from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    groq_api_base: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-20b"
    together_api_key: str = ""
    together_api_base: str = "https://api.together.xyz/v1"
    embedding_model: str = "togethercomputer/m2-bert-80M-32k-retrieval"

    # Chunking defaults
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # CORS: origins allowed to call this API (comma-separated in .env)
    cors_allowed_origins: list[str] = ["http://localhost:3000"]

    # Auth: verifies Supabase-issued JWTs via JWKS (see app/api/deps.py::
    # get_current_user). This project's Supabase signing key is ES256/
    # asymmetric (the newer JWT-signing-keys scheme), confirmed by reading
    # `{supabase_url}/auth/v1/.well-known/jwks.json` directly - not the legacy
    # HS256-shared-secret scheme, so no separate secret setting is needed here.
    # Empty means unconfigured - get_current_user fails closed (HTTPException
    # 500) rather than accepting unverifiable tokens.
    supabase_url: str = ""

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_comma_separated_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
