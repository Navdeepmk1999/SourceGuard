import asyncio

from sqlalchemy import text

from app.db.session import engine
from app.models import Base

# `workspaces` (Workspace.__tablename__), not the singular "workspace" - a
# table name mismatch here would fail outright against a real Postgres
# instance ("relation does not exist").
_ENABLE_WORKSPACE_RLS = "ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY"

# `current_setting(..., true)` (the missing_ok flag) rather than the
# single-argument form: an unauthenticated/misconfigured session (the
# variable never set) then reads as NULL, so the policy fails closed to "no
# rows visible" instead of raising a raw, unhandled Postgres error.
_WORKSPACE_RLS_POLICY = """
CREATE POLICY workspace_isolation ON workspaces
    USING (user_id = current_setting('app.current_user_id', true)::uuid)
"""


async def init_db() -> None:
    """Ensure the pgvector extension exists, create all ORM tables, and (on
    Postgres only) enable Row-Level Security tenant isolation on `workspaces`.

    RLS - and the `set_config`/`current_setting` mechanism `app/api/deps.py::
    get_authenticated_db` uses to satisfy it per-request - is Postgres-only
    syntax with no SQLite equivalent, so this step is skipped entirely off
    Postgres, mirroring `PortableVector`'s dialect-gated behavior elsewhere.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

        if conn.dialect.name == "postgresql":
            await conn.execute(text(_ENABLE_WORKSPACE_RLS))
            # DROP + CREATE rather than a bare CREATE POLICY: Postgres has no
            # `CREATE POLICY IF NOT EXISTS`, and init_db() must stay safely
            # re-runnable per its existing idempotent-bootstrap contract.
            await conn.execute(text("DROP POLICY IF EXISTS workspace_isolation ON workspaces"))
            await conn.execute(text(_WORKSPACE_RLS_POLICY))


if __name__ == "__main__":
    asyncio.run(init_db())
