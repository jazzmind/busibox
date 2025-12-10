import json


async def set_rls_session_vars(conn, user_id: str | None, role_ids: list[str] | None):
    """
    Apply RLS session variables on the given connection.
    Mirrors the ingest RLS helpers but kept minimal for authz.
    """
    uid = user_id or "00000000-0000-0000-0000-000000000000"
    await conn.execute("SET app.user_id = $1", uid)
    await conn.execute("SET app.user_role_ids_read = $1", json.dumps(role_ids or []))
    await conn.execute("SET app.user_role_ids_write = $1", json.dumps(role_ids or []))

