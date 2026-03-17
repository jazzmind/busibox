#!/usr/bin/env python3
"""
Seed default branding, platform config, and core apps into the config-api database.

Normally run automatically at service startup via seed.py. This standalone
script is provided for manual re-seeding or troubleshooting.

Usage:
    python scripts/seed_default_branding.py

Environment:
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""

import asyncio
import os
import sys

import asyncpg

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "config")
POSTGRES_USER = os.getenv("POSTGRES_USER", "busibox_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")


async def main():
    conn = await asyncpg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )

    print(f"[SEED] Connected to {POSTGRES_DB}@{POSTGRES_HOST}")

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from schema import get_config_schema
    schema = get_config_schema()
    await schema.apply(conn)
    print("[SEED] Schema applied")

    from seed import seed_defaults
    await seed_defaults(conn)

    await conn.close()
    print("[SEED] Done")


if __name__ == "__main__":
    asyncio.run(main())
