#!/usr/bin/env python3
"""
Migrate deploy-api encryption from standalone crypto_utils to AuthZ keystore.

This script:
1. Reads all encrypted rows from github_connections, app_secrets, app_databases
2. Decrypts each value using the OLD crypto_utils (SECRETS_ENCRYPTION_KEY env var)
3. Re-encrypts using the NEW authz_crypto (AuthZ keystore API)
4. Updates each row in-place

Prerequisites:
- SECRETS_ENCRYPTION_KEY (or ENCRYPTION_KEY) must be set (old key for decryption)
- AUTHZ_URL must be set (AuthZ service URL)
- DEPLOY_BOOTSTRAP_TOKEN must be set (for AuthZ API authentication)
- AuthZ service must be running and accessible
- PostgreSQL must be accessible (POSTGRES_HOST, POSTGRES_ADMIN_PASSWORD, etc.)

Usage:
    # Dry run (shows what would be migrated, no changes)
    python migrate_encryption.py --dry-run

    # Run migration
    python migrate_encryption.py

    # Run with verbose output
    python migrate_encryption.py --verbose

Run this BEFORE removing SECRETS_ENCRYPTION_KEY from the environment.
"""

import asyncio
import argparse
import base64
import hashlib
import logging
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Add parent src directory to path so we can import deploy modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from authz_crypto import encrypt as new_encrypt, ensure_system_kek
from database import execute_sql


# ---------------------------------------------------------------------------
# Old decryption logic (inlined from deleted crypto_utils.py)
# ---------------------------------------------------------------------------
_OLD_IV_LENGTH = 16
_OLD_AUTH_TAG_LENGTH = 16
_OLD_KEY_LENGTH = 32
_OLD_PBKDF2_ITERATIONS = 100_000
_OLD_PBKDF2_SALT = b'deployment-secrets'


def _old_derive_key() -> bytes:
    raw_key = os.getenv('SECRETS_ENCRYPTION_KEY') or os.getenv('ENCRYPTION_KEY')
    if not raw_key:
        raise RuntimeError('SECRETS_ENCRYPTION_KEY or ENCRYPTION_KEY must be set')
    return hashlib.pbkdf2_hmac(
        'sha512', raw_key.encode('utf-8'),
        _OLD_PBKDF2_SALT, _OLD_PBKDF2_ITERATIONS, dklen=_OLD_KEY_LENGTH,
    )


def old_decrypt(encrypted_data: str) -> str:
    """Decrypt a value encrypted with the old AES-256-GCM format."""
    key = _old_derive_key()
    combined = base64.b64decode(encrypted_data)
    iv = combined[:_OLD_IV_LENGTH]
    auth_tag = combined[_OLD_IV_LENGTH:_OLD_IV_LENGTH + _OLD_AUTH_TAG_LENGTH]
    ciphertext = combined[_OLD_IV_LENGTH + _OLD_AUTH_TAG_LENGTH:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext + auth_tag, None)
    return plaintext.decode('utf-8')

logger = logging.getLogger(__name__)

DB_NAME = 'data'


async def query(sql: str) -> str:
    """Execute a query against the data database."""
    stdout, stderr, code = await execute_sql(sql, DB_NAME)
    if code != 0:
        raise RuntimeError(f"Query failed: {stderr}")
    return stdout.strip()


async def execute(sql: str) -> None:
    """Execute a statement against the data database."""
    stdout, stderr, code = await execute_sql(sql, DB_NAME)
    if code != 0:
        raise RuntimeError(f"Execute failed: {stderr}")


def escape(value: str) -> str:
    """Escape a string for SQL."""
    s = str(value).replace("'", "''")
    return f"'{s}'"


async def migrate_github_connections(dry_run: bool = False, verbose: bool = False) -> int:
    """Migrate github_connections.access_token and refresh_token."""
    logger.info("Migrating github_connections...")
    
    sql = "SELECT id, user_id, access_token, refresh_token FROM github_connections"
    result = await query(sql)
    
    if not result:
        logger.info("  No github_connections found")
        return 0
    
    count = 0
    for line in result.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) < 4:
            continue
        
        row_id, user_id, enc_access, enc_refresh = parts[0], parts[1], parts[2], parts[3]
        
        if not enc_access:
            continue
        
        if verbose:
            logger.info(f"  Processing github_connection id={row_id} user_id={user_id}")
        
        try:
            # Decrypt with old key
            plaintext_access = old_decrypt(enc_access)
            
            if dry_run:
                logger.info(f"  [DRY RUN] Would re-encrypt access_token for user {user_id}")
            else:
                # Re-encrypt with authz keystore
                new_enc_access = await new_encrypt(plaintext_access, f"github:{user_id}:access")
                
                update_parts = [f"access_token = {escape(new_enc_access)}"]
                
                if enc_refresh and enc_refresh.strip():
                    plaintext_refresh = old_decrypt(enc_refresh)
                    new_enc_refresh = await new_encrypt(plaintext_refresh, f"github:{user_id}:refresh")
                    update_parts.append(f"refresh_token = {escape(new_enc_refresh)}")
                
                update_sql = f"UPDATE github_connections SET {', '.join(update_parts)} WHERE id = {escape(row_id)}"
                await execute(update_sql)
                logger.info(f"  Migrated github_connection for user {user_id}")
            
            count += 1
            
        except Exception as e:
            logger.error(f"  Failed to migrate github_connection id={row_id}: {e}")
            raise
    
    return count


async def migrate_app_secrets(dry_run: bool = False, verbose: bool = False) -> int:
    """Migrate app_secrets.encrypted_value."""
    logger.info("Migrating app_secrets...")
    
    sql = "SELECT id, deployment_config_id, key, encrypted_value FROM app_secrets"
    result = await query(sql)
    
    if not result:
        logger.info("  No app_secrets found")
        return 0
    
    count = 0
    for line in result.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) < 4:
            continue
        
        row_id, config_id, key, enc_value = parts[0], parts[1], parts[2], parts[3]
        
        if not enc_value:
            continue
        
        if verbose:
            logger.info(f"  Processing app_secret id={row_id} config={config_id} key={key}")
        
        try:
            # Decrypt with old key
            plaintext = old_decrypt(enc_value)
            
            if dry_run:
                logger.info(f"  [DRY RUN] Would re-encrypt secret {key} for config {config_id}")
            else:
                # Re-encrypt with authz keystore
                new_enc = await new_encrypt(plaintext, f"secret:{config_id}:{key}")
                
                update_sql = f"UPDATE app_secrets SET encrypted_value = {escape(new_enc)} WHERE id = {escape(row_id)}"
                await execute(update_sql)
                logger.info(f"  Migrated secret {key} for config {config_id}")
            
            count += 1
            
        except Exception as e:
            logger.error(f"  Failed to migrate app_secret id={row_id}: {e}")
            raise
    
    return count


async def migrate_app_databases(dry_run: bool = False, verbose: bool = False) -> int:
    """Migrate app_databases.encrypted_password."""
    logger.info("Migrating app_databases...")
    
    sql = "SELECT id, deployment_config_id, encrypted_password FROM app_databases"
    result = await query(sql)
    
    if not result:
        logger.info("  No app_databases found")
        return 0
    
    count = 0
    for line in result.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) < 3:
            continue
        
        row_id, config_id, enc_password = parts[0], parts[1], parts[2]
        
        if not enc_password:
            continue
        
        if verbose:
            logger.info(f"  Processing app_database id={row_id} config={config_id}")
        
        try:
            # Decrypt with old key
            plaintext = old_decrypt(enc_password)
            
            if dry_run:
                logger.info(f"  [DRY RUN] Would re-encrypt password for config {config_id}")
            else:
                # Re-encrypt with authz keystore
                new_enc = await new_encrypt(plaintext, f"dbpass:{config_id}")
                
                update_sql = f"UPDATE app_databases SET encrypted_password = {escape(new_enc)} WHERE id = {escape(row_id)}"
                await execute(update_sql)
                logger.info(f"  Migrated database password for config {config_id}")
            
            count += 1
            
        except Exception as e:
            logger.error(f"  Failed to migrate app_database id={row_id}: {e}")
            raise
    
    return count


async def main():
    parser = argparse.ArgumentParser(
        description="Migrate deploy-api encryption from crypto_utils to AuthZ keystore"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed progress"
    )
    args = parser.parse_args()
    
    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Validate environment
    old_key = os.getenv('SECRETS_ENCRYPTION_KEY') or os.getenv('ENCRYPTION_KEY')
    if not old_key:
        logger.error("SECRETS_ENCRYPTION_KEY or ENCRYPTION_KEY must be set for decryption")
        sys.exit(1)
    
    authz_url = os.getenv('AUTHZ_URL')
    if not authz_url:
        logger.error("AUTHZ_URL must be set for re-encryption")
        sys.exit(1)
    
    bootstrap_token = os.getenv('DEPLOY_BOOTSTRAP_TOKEN')
    if not bootstrap_token:
        logger.warning("DEPLOY_BOOTSTRAP_TOKEN not set - AuthZ authentication may fail")
    
    if args.dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN MODE - No changes will be made")
        logger.info("=" * 60)
    
    logger.info("Starting encryption migration...")
    logger.info(f"  AuthZ URL: {authz_url}")
    logger.info(f"  Old key source: {'SECRETS_ENCRYPTION_KEY' if os.getenv('SECRETS_ENCRYPTION_KEY') else 'ENCRYPTION_KEY'}")
    
    # Ensure system KEK exists before starting
    if not args.dry_run:
        logger.info("Ensuring system KEK exists in AuthZ...")
        await ensure_system_kek()
    
    # Run migrations
    total = 0
    
    gh_count = await migrate_github_connections(dry_run=args.dry_run, verbose=args.verbose)
    total += gh_count
    
    secret_count = await migrate_app_secrets(dry_run=args.dry_run, verbose=args.verbose)
    total += secret_count
    
    db_count = await migrate_app_databases(dry_run=args.dry_run, verbose=args.verbose)
    total += db_count
    
    # Summary
    logger.info("")
    logger.info("=" * 60)
    if args.dry_run:
        logger.info(f"DRY RUN COMPLETE - Would migrate {total} records:")
    else:
        logger.info(f"MIGRATION COMPLETE - Migrated {total} records:")
    logger.info(f"  GitHub connections: {gh_count}")
    logger.info(f"  App secrets: {secret_count}")
    logger.info(f"  App databases: {db_count}")
    logger.info("=" * 60)
    
    if not args.dry_run and total > 0:
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Verify the migrated data works correctly")
        logger.info("  2. Redeploy deploy-api: make manage SERVICE=deploy ACTION=redeploy")


if __name__ == "__main__":
    asyncio.run(main())
