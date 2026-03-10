"""
Config API Database Schema.

Defines config_entries and app_registry tables.
Applied idempotently on every service startup.
"""

import sys
from pathlib import Path

_shared_paths = [
    Path(__file__).parent.parent.parent.parent / "shared",
    Path("/srv/shared"),
]
for _path in _shared_paths:
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    from busibox_common import SchemaManager
except ImportError:
    class SchemaManager:
        def __init__(self):
            self._sql_statements = []

        def add_extension(self, name: str) -> "SchemaManager":
            self._sql_statements.append(f'CREATE EXTENSION IF NOT EXISTS "{name}";')
            return self

        def add_table(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self

        def add_index(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self

        def add_migration(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self

        async def apply(self, conn) -> None:
            for sql in self._sql_statements:
                try:
                    await conn.execute(sql)
                except Exception as e:
                    error_str = str(e).lower()
                    if "already exists" in error_str or "does not exist" in error_str:
                        pass
                    else:
                        raise


def get_config_schema() -> SchemaManager:
    """Build the complete config-api schema definition."""
    schema = SchemaManager()

    schema.add_extension("pgcrypto")

    # ---- config_entries: key/value config with access tiers ----
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS config_entries (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key         TEXT NOT NULL,
            value       TEXT NOT NULL DEFAULT '',
            encrypted   BOOLEAN NOT NULL DEFAULT FALSE,
            scope       TEXT NOT NULL DEFAULT 'platform',
            app_id      TEXT,
            tier        TEXT NOT NULL DEFAULT 'admin',
            category    TEXT,
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS idx_config_key_app ON config_entries(key, COALESCE(app_id, ''))")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_config_scope ON config_entries(scope)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_config_app_id ON config_entries(app_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_config_tier ON config_entries(tier)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_config_category ON config_entries(category)")

    # ---- app_registry: installed application definitions ----
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS app_registry (
            id                      TEXT PRIMARY KEY,
            name                    TEXT NOT NULL,
            description             TEXT,
            type                    TEXT NOT NULL DEFAULT 'LIBRARY',
            sso_audience            TEXT,
            url                     TEXT,
            deployed_path           TEXT,
            icon_url                TEXT,
            selected_icon           TEXT,
            display_order           INT DEFAULT 0,
            is_active               BOOLEAN DEFAULT TRUE,
            health_endpoint         TEXT,
            github_repo             TEXT,
            deployed_version        TEXT,
            latest_version          TEXT,
            update_available        BOOLEAN DEFAULT FALSE,
            dev_mode                BOOLEAN DEFAULT FALSE,
            primary_color           TEXT,
            secondary_color         TEXT,
            last_deployment_status  TEXT,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    schema.add_migration(
        "ALTER TABLE app_registry ADD COLUMN IF NOT EXISTS last_deployment_status TEXT"
    )

    schema.add_index("CREATE INDEX IF NOT EXISTS idx_app_registry_type ON app_registry(type)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_app_registry_active ON app_registry(is_active)")

    # ---- updated_at trigger ----
    schema.add_migration("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    for table in ("config_entries", "app_registry"):
        schema.add_migration(f"""
            DO $$ BEGIN
                CREATE TRIGGER set_updated_at
                    BEFORE UPDATE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """)

    return schema
