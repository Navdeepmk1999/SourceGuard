import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import TENANT_SETTING
from app.models import Base

# Tables carrying tenant data. `workspaces` owns `user_id` directly; the rest
# reach the owner through their parent, so a single row can never be visible
# without its whole ancestry being owned by the caller.
#
# Every policy wraps the setting in NULLIF(..., '')::uuid rather than casting
# it directly. `current_setting(name, true)` returns NULL when never set, but
# an EMPTY STRING once the value has been cleared - and ''::uuid raises
# "invalid input syntax for type uuid", which would turn a cleared context
# into a query error instead of a clean "no rows". NULLIF collapses both
# cases to NULL, and `col = NULL` is NULL, so the policy fails closed.
_TENANT_UUID = f"NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"

_POLICIES: dict[str, tuple[str, str]] = {
    "workspaces": (
        "workspace_isolation",
        f"user_id = {_TENANT_UUID}",
    ),
    "documents": (
        "document_isolation",
        f"workspace_id IN (SELECT id FROM workspaces WHERE user_id = {_TENANT_UUID})",
    ),
    "document_chunks": (
        "document_chunk_isolation",
        "document_id IN (SELECT d.id FROM documents d JOIN workspaces w "
        f"ON d.workspace_id = w.id WHERE w.user_id = {_TENANT_UUID})",
    ),
    "chat_sessions": (
        "chat_session_isolation",
        f"user_id = {_TENANT_UUID}",
    ),
    "chat_messages": (
        "chat_message_isolation",
        "session_id IN (SELECT id FROM chat_sessions "
        f"WHERE user_id = {_TENANT_UUID})",
    ),
}


async def _provision_app_role(conn, role: str, password: str) -> None:
    """Creates (or updates) the non-superuser role the application connects as.

    This role is the entire reason RLS has any effect: Postgres exempts
    superusers and BYPASSRLS roles from every policy, so an app connecting as
    `postgres` gets no isolation regardless of how the policies are written.

    Granted CRUD on the app tables and nothing more - no DDL, no BYPASSRLS,
    no ownership. It is deliberately NOT the table owner: an owner bypasses
    RLS unless FORCE is set, and relying on FORCE alone would make isolation
    depend on one easily-missed flag.
    """
    if not password:
        raise RuntimeError(
            "APP_DB_PASSWORD must be set to provision the restricted database role. "
            "Without a dedicated non-superuser role the RLS policies below are inert."
        )

    # Quoted identifier; the password is bound as a literal via format() into
    # a DDL statement that cannot take bind parameters, so it is validated
    # rather than interpolated blind.
    if not role.replace("_", "").isalnum():
        raise RuntimeError(f"Invalid app_db_role {role!r}: expected an alphanumeric identifier.")
    if "'" in password:
        raise RuntimeError("APP_DB_PASSWORD must not contain a single quote.")

    await conn.execute(
        text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} LOGIN PASSWORD '{password}';
                ELSE
                    ALTER ROLE {role} WITH LOGIN PASSWORD '{password}';
                END IF;
            END
            $$;
            """
        )
    )

    # Explicitly strip the two attributes that would silently defeat RLS,
    # in case the role pre-existed with them.
    await conn.execute(text(f"ALTER ROLE {role} NOSUPERUSER NOBYPASSRLS"))

    await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    await conn.execute(
        text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
    )
    await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"))
    # Covers tables added by a later migration without a re-grant.
    await conn.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
        )
    )


async def init_db() -> None:
    """Idempotent bootstrap: extension, tables, restricted role, and RLS.

    Runs against `settings.bootstrap_database_url` (the admin connection) -
    not the application's own URL, which by design lacks the privileges to
    create roles, tables, or policies.

    RLS and the `set_config`/`current_setting` mechanism are Postgres-only
    with no SQLite equivalent, so those steps are skipped off Postgres,
    mirroring `PortableVector`'s dialect-gated behavior elsewhere.
    """
    settings = get_settings()
    engine = create_async_engine(settings.bootstrap_database_url, echo=False, future=True)

    try:
        async with engine.begin() as conn:
            is_postgres = conn.dialect.name == "postgresql"
            if is_postgres:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

            if not is_postgres:
                return

            await _provision_app_role(conn, settings.app_db_role, settings.app_db_password)

            for table, (policy_name, using_clause) in _POLICIES.items():
                await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
                # FORCE extends the policy to the table OWNER, which plain
                # ENABLE exempts. Defense in depth for the case where the app
                # role is ever made an owner.
                #
                # It does NOT make a superuser subject to RLS: the SUPERUSER
                # and BYPASSRLS attributes bypass policies unconditionally,
                # and no table-level flag overrides that (verified against
                # this database - the admin role still reads every row with
                # FORCE set). The actual fix is connecting as
                # `settings.app_db_role`, which holds neither attribute.
                await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
                # Postgres has no CREATE POLICY IF NOT EXISTS; drop-then-create
                # keeps init_db() re-runnable per its idempotent contract.
                await conn.execute(text(f"DROP POLICY IF EXISTS {policy_name} ON {table}"))
                # No FOR clause -> FOR ALL, and with WITH CHECK omitted the
                # USING expression governs INSERT/UPDATE too, so a caller
                # cannot write a row it would not be allowed to read.
                await conn.execute(
                    text(f"CREATE POLICY {policy_name} ON {table} USING ({using_clause})")
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())
