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

    # Database (loaded from DATABASE_URL in .env).
    #
    # SECURITY: this must point at a NON-SUPERUSER role without BYPASSRLS, or
    # the Row-Level Security policies in app/db/init_db.py are silently
    # inert - Postgres exempts superusers and BYPASSRLS roles from every
    # policy. `init_db()` provisions exactly such a role (see app_db_role).
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sourceguard"

    # Elevated connection used ONLY by app/db/init_db.py for DDL: creating
    # the extension, tables, the restricted role, grants, and policies - none
    # of which the restricted runtime role can do. Falls back to
    # `database_url` when unset, which keeps a single-URL local setup working
    # for bootstrap.
    admin_database_url: str = ""

    # The non-superuser role the application connects as. `init_db()` creates
    # it and grants it CRUD on the app tables (never DDL, never BYPASSRLS).
    app_db_role: str = "sourceguard_app"
    app_db_password: str = ""

    @property
    def bootstrap_database_url(self) -> str:
        """Connection used for DDL/bootstrap - the admin URL when configured."""
        return self.admin_database_url or self.database_url

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

    # LangSmith telemetry (Module 10). NOTE: this codebase makes no LangChain
    # or LangGraph LLM calls - generation is a direct httpx stream to Groq's
    # API, and the only langchain import anywhere is the pure-text
    # RecursiveCharacterTextSplitter in app/services/chunker.py. So
    # LANGCHAIN_TRACING_V2 alone would auto-instrument nothing; tracing is
    # emitted by explicit @traceable decorators in app/services/telemetry.py.
    # Both names are read: LANGSMITH_* is the current convention, LANGCHAIN_*
    # the legacy one that the langsmith SDK still honors.
    langchain_tracing_v2: bool = False
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "sourceguard"

    @property
    def tracing_enabled(self) -> bool:
        """True only when tracing is switched on AND an API key exists - a
        key-less 'enabled' would make every traced call emit failing network
        requests to LangSmith on the request path."""
        return bool((self.langchain_tracing_v2 or self.langsmith_tracing) and self.langsmith_api_key)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_comma_separated_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
