"""
Database Initialization and Migration Management.

Provides a unified approach to database schema management across all Busibox services:

1. **Schema Initialization**: Creates tables from a master schema if they don't exist
2. **Migration Management**: Runs Alembic migrations for incremental updates
3. **Startup Integration**: Can be called on every service startup (idempotent)

Usage:
    from busibox_common import DatabaseInitializer
    
    # In service startup:
    async def startup():
        db_init = DatabaseInitializer(
            database_url=settings.database_url,
            alembic_config_path="/app/alembic.ini",  # Optional
            schema_sql_path="/app/schema.sql",       # Optional master schema
        )
        await db_init.ensure_ready()

The pattern:
    1. Check if alembic_version table exists
    2. If not: either run master schema.sql OR run all migrations from scratch
    3. If yes: run any pending migrations (alembic upgrade head)
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, List, Callable, Awaitable
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class SchemaManager:
    """
    Manages schema creation using CREATE TABLE IF NOT EXISTS pattern.
    
    This is the approach used by authz - tables are defined in code and
    created idempotently on every startup.
    
    Usage:
        schema = SchemaManager()
        schema.add_extension("pgcrypto")
        schema.add_table('''
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        ''')
        schema.add_index("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        
        await schema.apply(connection)
    """
    
    def __init__(self):
        self._extensions: List[str] = []
        self._tables: List[str] = []
        self._indexes: List[str] = []
        self._migrations: List[str] = []  # Inline migrations (ALTER TABLE IF NOT EXISTS patterns)
        self._rls_policies: List[str] = []  # Row-Level Security policies
    
    def add_extension(self, name: str) -> "SchemaManager":
        """Add a PostgreSQL extension to be created."""
        self._extensions.append(name)
        return self
    
    def add_table(self, create_sql: str) -> "SchemaManager":
        """Add a CREATE TABLE IF NOT EXISTS statement."""
        self._tables.append(create_sql.strip())
        return self
    
    def add_index(self, create_sql: str) -> "SchemaManager":
        """Add a CREATE INDEX IF NOT EXISTS statement."""
        self._indexes.append(create_sql.strip())
        return self
    
    def add_migration(self, migration_sql: str) -> "SchemaManager":
        """
        Add an inline migration (for adding columns, etc).
        
        Use DO $$ blocks for conditional logic:
            schema.add_migration('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'users' AND column_name = 'status'
                    ) THEN
                        ALTER TABLE users ADD COLUMN status TEXT;
                    END IF;
                END $$;
            ''')
        """
        self._migrations.append(migration_sql.strip())
        return self
    
    def add_rls(self, rls_sql: str) -> "SchemaManager":
        """
        Add Row-Level Security (RLS) policies.
        
        Examples:
            schema.add_rls("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
            schema.add_rls('''
                CREATE POLICY IF NOT EXISTS users_isolation ON users
                USING (organization_id = current_setting('app.current_organization_id')::UUID)
            ''')
        """
        self._rls_policies.append(rls_sql.strip())
        return self
    
    async def apply(self, conn) -> None:
        """
        Apply all schema definitions to the database connection.
        
        Args:
            conn: An asyncpg connection or SQLAlchemy async connection
        """
        # Detect connection type and get execute function
        execute = self._get_execute_func(conn)
        
        # Create extensions
        for ext in self._extensions:
            await execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}";')
            logger.debug(f"Extension ensured: {ext}")
        
        # Create tables
        for table_sql in self._tables:
            await execute(table_sql)
        logger.debug(f"Tables ensured: {len(self._tables)}")
        
        # Create indexes
        for index_sql in self._indexes:
            await execute(index_sql)
        logger.debug(f"Indexes ensured: {len(self._indexes)}")
        
        # Run migrations
        for migration_sql in self._migrations:
            await execute(migration_sql)
        logger.debug(f"Migrations applied: {len(self._migrations)}")
        
        # Apply RLS policies
        for rls_sql in self._rls_policies:
            await execute(rls_sql)
        logger.debug(f"RLS policies applied: {len(self._rls_policies)}")
        
        logger.info("Schema initialization complete")
    
    def _get_execute_func(self, conn) -> Callable[[str], Awaitable]:
        """Get the appropriate execute function for the connection type."""
        # Check for asyncpg connection by module name (more reliable than coroutine check)
        conn_type = type(conn).__module__
        if 'asyncpg' in conn_type:
            return conn.execute
        
        # SQLAlchemy async connection - wrap with text()
        if hasattr(conn, 'execute'):
            from sqlalchemy import text
            async def sqlalchemy_execute(sql: str):
                await conn.execute(text(sql))
            return sqlalchemy_execute
        
        raise TypeError(f"Unsupported connection type: {type(conn)}")


class DatabaseInitializer:
    """
    Unified database initialization that combines schema creation with Alembic migrations.
    
    This provides a consistent pattern for all services:
    1. On fresh install: Creates schema (via SchemaManager or alembic from scratch)
    2. On updates: Runs pending Alembic migrations
    
    Usage:
        # Option 1: With Alembic only
        db_init = DatabaseInitializer(
            database_url="postgresql+asyncpg://...",
            alembic_config_path="/app/alembic.ini",
        )
        await db_init.ensure_ready()
        
        # Option 2: With SchemaManager (no Alembic)
        schema = SchemaManager()
        schema.add_table("CREATE TABLE IF NOT EXISTS ...")
        
        db_init = DatabaseInitializer(
            database_url="postgresql+asyncpg://...",
            schema_manager=schema,
        )
        await db_init.ensure_ready()
        
        # Option 3: Both (schema for tables, alembic for migrations)
        db_init = DatabaseInitializer(
            database_url="postgresql+asyncpg://...",
            alembic_config_path="/app/alembic.ini",
            schema_manager=schema,
        )
        await db_init.ensure_ready()
    """
    
    def __init__(
        self,
        database_url: str,
        alembic_config_path: Optional[str] = None,
        schema_manager: Optional[SchemaManager] = None,
        service_name: str = "unknown",
    ):
        self.database_url = database_url
        self.alembic_config_path = alembic_config_path
        self.schema_manager = schema_manager
        self.service_name = service_name
        self._is_ready = False
    
    async def ensure_ready(self) -> None:
        """
        Ensure the database is ready for the service.
        
        This is idempotent and safe to call on every startup.
        """
        if self._is_ready:
            return
        
        logger.info(f"[{self.service_name}] Initializing database...")
        
        # Apply schema if SchemaManager is provided
        if self.schema_manager:
            await self._apply_schema()
        
        # Run Alembic migrations if configured
        if self.alembic_config_path:
            await self._run_alembic_migrations()
        
        self._is_ready = True
        logger.info(f"[{self.service_name}] Database initialization complete")
    
    async def _apply_schema(self) -> None:
        """Apply schema using SchemaManager."""
        from sqlalchemy.ext.asyncio import create_async_engine
        
        engine = create_async_engine(self.database_url, echo=False)
        try:
            async with engine.begin() as conn:
                await self.schema_manager.apply(conn)
        finally:
            await engine.dispose()
    
    async def _run_alembic_migrations(self) -> None:
        """Run Alembic migrations (upgrade head)."""
        if not self.alembic_config_path or not Path(self.alembic_config_path).exists():
            logger.warning(f"Alembic config not found: {self.alembic_config_path}")
            return
        
        # Alembic is synchronous, so run in executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._run_alembic_sync)
    
    def _run_alembic_sync(self) -> None:
        """Synchronous Alembic migration runner."""
        try:
            from alembic.config import Config
            from alembic import command
            
            # Set DATABASE_URL for alembic env.py
            os.environ["DATABASE_URL"] = self.database_url
            
            alembic_cfg = Config(self.alembic_config_path)
            
            # Get current revision
            try:
                from alembic.runtime.migration import MigrationContext
                from sqlalchemy import create_engine
                
                # Convert async URL to sync for checking
                sync_url = self.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")
                engine = create_engine(sync_url)
                
                with engine.connect() as conn:
                    context = MigrationContext.configure(conn)
                    current_rev = context.get_current_revision()
                
                engine.dispose()
                
                if current_rev is None:
                    logger.info(f"[{self.service_name}] No migration history, running full migration")
                else:
                    logger.info(f"[{self.service_name}] Current revision: {current_rev}")
                    
            except Exception as e:
                logger.debug(f"Could not check current revision: {e}")
            
            # Run migrations
            command.upgrade(alembic_cfg, "head")
            logger.info(f"[{self.service_name}] Alembic migrations complete")
            
        except ImportError:
            logger.warning("Alembic not installed, skipping migrations")
        except Exception as e:
            logger.error(f"[{self.service_name}] Alembic migration failed: {e}")
            raise


def create_schema_manager() -> SchemaManager:
    """Factory function to create a new SchemaManager."""
    return SchemaManager()
