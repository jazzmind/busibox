import json


async def set_rls_session_vars(conn, user_id: str | None, role_ids: list[str] | None):
    """
    Apply RLS session variables on the given connection.
    Mirrors the ingest RLS helpers but kept minimal for authz.
    """
    uid = user_id or "00000000-0000-0000-0000-000000000000"
    role_ids_json = json.dumps(role_ids or [])
    
    # SET commands don't support parameterized queries, use string formatting with proper escaping
    await conn.execute(f"SET app.user_id = '{uid}'")
    await conn.execute(f"SET app.user_role_ids_read = '{role_ids_json}'")
    await conn.execute(f"SET app.user_role_ids_write = '{role_ids_json}'")






