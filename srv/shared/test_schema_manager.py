#!/usr/bin/env python3
"""
Comprehensive test for the shared SchemaManager and database setup.

Tests:
1. SchemaManager can be imported
2. SchemaManager can create tables in test databases
3. Authz schema applies correctly
4. Ingest schema applies correctly
5. Test database isolation is maintained
"""

import asyncio
import os
import sys
from pathlib import Path

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "authz" / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "ingest" / "src"))

# Test configuration
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = "busibox_test_user"
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "testpassword")

# Test databases
TEST_AUTHZ_DB = "test_authz"
TEST_INGEST_DB = "test_files"
TEST_AGENT_DB = "test_agent_server"

# Production databases (for isolation verification)
PROD_AUTHZ_DB = "authz"
PROD_INGEST_DB = "files"
PROD_AGENT_DB = "agent_server"


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.error = None
        self.details = []

    def __str__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        result = f"{status} {self.name}"
        if self.error:
            result += f"\n    Error: {self.error}"
        for detail in self.details:
            result += f"\n    {detail}"
        return result


async def test_import_busibox_common() -> TestResult:
    """Test 1: Verify busibox_common package can be imported."""
    result = TestResult("Import busibox_common package")
    try:
        from busibox_common import SchemaManager, DatabaseInitializer
        result.passed = True
        result.details.append(f"SchemaManager: {SchemaManager}")
        result.details.append(f"DatabaseInitializer: {DatabaseInitializer}")
    except ImportError as e:
        result.error = str(e)
    return result


async def test_schema_manager_basic() -> TestResult:
    """Test 2: Verify SchemaManager basic functionality."""
    result = TestResult("SchemaManager basic functionality")
    try:
        from busibox_common import SchemaManager
        
        schema = SchemaManager()
        schema.add_extension("pgcrypto")
        schema.add_table("""
            CREATE TABLE IF NOT EXISTS test_table (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL
            )
        """)
        schema.add_index("CREATE INDEX IF NOT EXISTS idx_test_name ON test_table(name)")
        schema.add_migration("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'test_table' AND column_name = 'status'
                ) THEN
                    ALTER TABLE test_table ADD COLUMN status TEXT;
                END IF;
            END $$
        """)
        
        result.passed = True
        result.details.append(f"Extensions: {len(schema._extensions)}")
        result.details.append(f"Tables: {len(schema._tables)}")
        result.details.append(f"Indexes: {len(schema._indexes)}")
        result.details.append(f"Migrations: {len(schema._migrations)}")
    except Exception as e:
        result.error = str(e)
    return result


async def test_authz_schema_import() -> TestResult:
    """Test 3: Verify authz schema can be imported."""
    result = TestResult("Import authz schema")
    try:
        import importlib.util
        authz_schema_path = Path(__file__).parent.parent / "authz" / "src" / "schema.py"
        
        spec = importlib.util.spec_from_file_location("authz_schema", authz_schema_path)
        authz_schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(authz_schema_module)
        
        schema = authz_schema_module.get_authz_schema()
        result.passed = True
        result.details.append(f"Extensions: {len(schema._extensions)}")
        result.details.append(f"Tables: {len(schema._tables)}")
        result.details.append(f"Indexes: {len(schema._indexes)}")
        result.details.append(f"Migrations: {len(schema._migrations)}")
    except Exception as e:
        result.error = str(e)
    return result


async def test_authz_schema_apply() -> TestResult:
    """Test 4: Apply authz schema to test database."""
    result = TestResult("Apply authz schema to test_authz database")
    try:
        import asyncpg
        import importlib.util
        
        authz_schema_path = Path(__file__).parent.parent / "authz" / "src" / "schema.py"
        spec = importlib.util.spec_from_file_location("authz_schema", authz_schema_path)
        authz_schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(authz_schema_module)
        
        conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=TEST_AUTHZ_DB,
        )
        
        try:
            schema = authz_schema_module.get_authz_schema()
            await schema.apply(conn)
            
            # Verify tables were created
            tables = await conn.fetch("""
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public'
                ORDER BY tablename
            """)
            table_names = [t['tablename'] for t in tables]
            
            expected_tables = [
                'authz_users', 'authz_roles', 'authz_user_roles',
                'authz_oauth_clients', 'authz_signing_keys', 'audit_logs'
            ]
            
            missing = [t for t in expected_tables if t not in table_names]
            if missing:
                result.error = f"Missing tables: {missing}"
            else:
                result.passed = True
                result.details.append(f"Tables created: {len(table_names)}")
                result.details.append(f"Sample: {table_names[:5]}...")
        finally:
            await conn.close()
            
    except Exception as e:
        result.error = str(e)
    return result


async def test_ingest_schema_import() -> TestResult:
    """Test 5: Verify ingest schema can be imported."""
    result = TestResult("Import ingest schema")
    try:
        import importlib.util
        ingest_schema_path = Path(__file__).parent.parent / "ingest" / "src" / "schema.py"
        
        spec = importlib.util.spec_from_file_location("ingest_schema", ingest_schema_path)
        ingest_schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ingest_schema_module)
        
        schema = ingest_schema_module.get_ingest_schema()
        result.passed = True
        result.details.append(f"Extensions: {len(schema._extensions)}")
        result.details.append(f"Tables: {len(schema._tables)}")
        result.details.append(f"Indexes: {len(schema._indexes)}")
        result.details.append(f"Migrations: {len(schema._migrations)}")
    except Exception as e:
        result.error = str(e)
    return result


async def test_ingest_schema_apply() -> TestResult:
    """Test 6: Apply ingest schema to test database."""
    result = TestResult("Apply ingest schema to test_files database")
    try:
        import asyncpg
        import importlib.util
        
        ingest_schema_path = Path(__file__).parent.parent / "ingest" / "src" / "schema.py"
        spec = importlib.util.spec_from_file_location("ingest_schema", ingest_schema_path)
        ingest_schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ingest_schema_module)
        
        conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=TEST_INGEST_DB,
        )
        
        try:
            schema = ingest_schema_module.get_ingest_schema()
            await schema.apply(conn)
            
            # Verify tables were created
            tables = await conn.fetch("""
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public'
                ORDER BY tablename
            """)
            table_names = [t['tablename'] for t in tables]
            
            expected_tables = [
                'ingestion_files', 'ingestion_status', 'ingestion_chunks',
                'document_roles', 'groups', 'group_memberships'
            ]
            
            missing = [t for t in expected_tables if t not in table_names]
            if missing:
                result.error = f"Missing tables: {missing}"
            else:
                result.passed = True
                result.details.append(f"Tables created: {len(table_names)}")
                result.details.append(f"Sample: {table_names[:5]}...")
        finally:
            await conn.close()
            
    except Exception as e:
        result.error = str(e)
    return result


async def test_database_isolation() -> TestResult:
    """Test 7: Verify test databases are isolated from production."""
    result = TestResult("Database isolation verification")
    try:
        import asyncpg
        
        # Connect to test database
        test_conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=TEST_AGENT_DB,
        )
        
        # Connect to production database  
        prod_conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user="busibox_user",
            password="devpassword",
            database=PROD_AGENT_DB,
        )
        
        try:
            # Check agent count in both databases
            test_count = await test_conn.fetchval(
                "SELECT COUNT(*) FROM agent_definitions"
            )
            prod_count = await prod_conn.fetchval(
                "SELECT COUNT(*) FROM agent_definitions"
            )
            
            result.details.append(f"Test DB agents: {test_count}")
            result.details.append(f"Prod DB agents: {prod_count}")
            
            # Test should have 0 or very few, prod should have some
            if test_count == 0 and prod_count > 0:
                result.passed = True
                result.details.append("✓ Test DB is empty, Prod DB has data - ISOLATED")
            elif test_count == 0 and prod_count == 0:
                result.passed = True
                result.details.append("✓ Both empty (expected for fresh setup)")
            elif test_count > 0 and prod_count > 0 and test_count != prod_count:
                result.passed = True
                result.details.append("✓ Different counts - likely isolated")
            else:
                result.error = "Cannot verify isolation - counts are equal"
                
        finally:
            await test_conn.close()
            await prod_conn.close()
            
    except Exception as e:
        result.error = str(e)
    return result


async def test_agent_db_schema() -> TestResult:
    """Test 8: Verify agent test database has correct schema (from alembic)."""
    result = TestResult("Agent test database schema verification")
    try:
        import asyncpg
        
        conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=TEST_AGENT_DB,
        )
        
        try:
            # Check for expected tables
            tables = await conn.fetch("""
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public'
                ORDER BY tablename
            """)
            table_names = [t['tablename'] for t in tables]
            
            expected_tables = [
                'agent_definitions', 'tool_definitions', 'conversations',
                'messages', 'run_records', 'alembic_version'
            ]
            
            missing = [t for t in expected_tables if t not in table_names]
            if missing:
                result.error = f"Missing tables: {missing}"
            else:
                result.passed = True
                result.details.append(f"Tables: {len(table_names)}")
                result.details.append(f"Found: {', '.join(expected_tables)}")
                
                # Check alembic version
                version = await conn.fetchval(
                    "SELECT version_num FROM alembic_version"
                )
                result.details.append(f"Alembic version: {version}")
                
        finally:
            await conn.close()
            
    except Exception as e:
        result.error = str(e)
    return result


async def run_all_tests():
    """Run all tests and report results."""
    print("=" * 60)
    print("BUSIBOX SCHEMA MANAGER & DATABASE TESTS")
    print("=" * 60)
    print()
    
    tests = [
        test_import_busibox_common,
        test_schema_manager_basic,
        test_authz_schema_import,
        test_authz_schema_apply,
        test_ingest_schema_import,
        test_ingest_schema_apply,
        test_database_isolation,
        test_agent_db_schema,
    ]
    
    results = []
    for test_func in tests:
        print(f"Running: {test_func.__doc__}")
        result = await test_func()
        results.append(result)
        print(result)
        print()
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    print(f"Passed: {passed}/{len(results)}")
    print(f"Failed: {failed}/{len(results)}")
    
    if failed > 0:
        print("\nFailed tests:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.error}")
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
