#!/usr/bin/env python3
"""
Encrypt Existing Files Migration Script

Encrypts existing unencrypted files in MinIO storage using the authz keystore
envelope encryption system.

This script:
1. Queries all files where is_encrypted = false (or NULL)
2. Downloads each file from MinIO
3. Checks if already encrypted using content heuristics
4. If plaintext, encrypts via authz keystore API
5. Re-uploads encrypted content to MinIO
6. Updates is_encrypted = true in the database

Usage:
    python encrypt-existing-files.py [--dry-run] [--verbose] [--batch-size N]

Environment variables:
    INGEST_DB_URL: Ingest database connection URL
    MINIO_ENDPOINT: MinIO server endpoint (default: 10.96.200.205:9000)
    MINIO_ACCESS_KEY: MinIO access key
    MINIO_SECRET_KEY: MinIO secret key
    MINIO_BUCKET: MinIO bucket name (default: documents)
    AUTHZ_BASE_URL: AuthZ service URL (default: http://10.96.201.210:8010)
    AUTHZ_ADMIN_TOKEN: AuthZ admin token for keystore API

Execution Context:
    - Runs on: Admin workstation or within ingest container
    - Requires: Network access to MinIO, PostgreSQL, and AuthZ services
    - Safe to run multiple times (idempotent)
"""

import argparse
import asyncio
import base64
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import asyncpg
import httpx
from minio import Minio
from minio.error import S3Error


# =============================================================================
# Configuration
# =============================================================================

def get_ingest_db_url() -> str:
    """Get ingest database connection URL."""
    if url := os.environ.get("INGEST_DB_URL"):
        return url
    
    host = os.environ.get("INGEST_DB_HOST", "10.96.200.206")
    port = os.environ.get("INGEST_DB_PORT", "5432")
    dbname = os.environ.get("INGEST_DB_NAME", "ingest")
    user = os.environ.get("INGEST_DB_USER", "ingest")
    password = os.environ.get("INGEST_DB_PASSWORD", "ingest")
    
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def get_minio_client() -> Minio:
    """Create MinIO client."""
    endpoint = os.environ.get("MINIO_ENDPOINT", "10.96.200.205:9000")
    access_key = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
    
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
    )


def get_minio_bucket() -> str:
    """Get MinIO bucket name."""
    return os.environ.get("MINIO_BUCKET", "documents")


def get_authz_config() -> Tuple[str, str]:
    """Get AuthZ keystore configuration."""
    base_url = os.environ.get("AUTHZ_BASE_URL", "http://10.96.201.210:8010")
    admin_token = os.environ.get("AUTHZ_ADMIN_TOKEN", "")
    return base_url, admin_token


# =============================================================================
# Encryption Detection
# =============================================================================

# Common file signatures for known unencrypted formats
FILE_SIGNATURES = [
    b'%PDF',           # PDF
    b'PK\x03\x04',     # ZIP/DOCX/XLSX
    b'\x89PNG',        # PNG
    b'\xff\xd8\xff',   # JPEG
    b'GIF8',           # GIF
    b'<!DOCTYPE',      # HTML
    b'<html',          # HTML
    b'{',              # JSON
    b'[',              # JSON array
]


def is_encrypted(content: bytes) -> bool:
    """
    Check if content appears to be encrypted.
    
    Encrypted content has a specific format:
    - First 12 bytes: nonce
    - Rest: ciphertext with GCM tag
    
    This is a heuristic check - encrypted content won't have common
    file signatures (PDF, DOCX, etc.) and will be mostly non-ASCII bytes.
    
    Plain text files (markdown, txt, code) are mostly ASCII and should
    NOT be flagged as encrypted.
    """
    if len(content) < 28:  # Minimum: 12 byte nonce + 16 byte tag
        return False
    
    # Check for known file signatures
    for sig in FILE_SIGNATURES:
        if content.startswith(sig):
            return False
    
    # Check if content is mostly ASCII/UTF-8 text (plain text is not encrypted)
    # Sample the first 1KB for efficiency
    sample = content[:1024]
    try:
        # Try to decode as UTF-8 - if it decodes cleanly, it's likely text
        decoded = sample.decode('utf-8')
        # Count printable ASCII characters (including common text chars)
        printable_count = sum(1 for c in decoded if c.isprintable() or c in '\n\r\t')
        # If more than 90% is printable text, it's not encrypted
        if printable_count / len(decoded) > 0.9:
            return False
    except UnicodeDecodeError:
        # If UTF-8 decoding fails, check for high-entropy binary data
        pass
    
    # Count non-ASCII bytes in sample - encrypted data is mostly non-ASCII
    non_ascii_count = sum(1 for b in sample if b > 127)
    
    # If less than 30% is non-ASCII, probably not encrypted
    # (Real AES-GCM ciphertext has ~50% non-ASCII due to random bytes)
    if len(sample) > 0 and non_ascii_count / len(sample) < 0.3:
        return False
    
    # Content has no known signature AND is mostly non-ASCII - likely encrypted
    return True


# =============================================================================
# Database Operations
# =============================================================================

async def fetch_unencrypted_files(
    conn: asyncpg.Connection,
    batch_size: int = 100,
    verbose: bool = False
) -> List[Dict]:
    """Fetch files that need encryption."""
    query = """
        SELECT 
            file_id,
            user_id,
            storage_path,
            visibility,
            filename,
            size_bytes
        FROM ingestion_files
        WHERE is_encrypted IS NOT TRUE
        ORDER BY created_at ASC
        LIMIT $1
    """
    
    rows = await conn.fetch(query, batch_size)
    files = [dict(row) for row in rows]
    
    if verbose:
        print(f"  Found {len(files)} unencrypted files")
    
    return files


async def mark_file_encrypted(
    conn: asyncpg.Connection,
    file_id: str,
    dry_run: bool = False,
    verbose: bool = False
) -> bool:
    """Mark a file as encrypted in the database."""
    if dry_run:
        if verbose:
            print(f"    [DRY RUN] Would mark file {file_id} as encrypted")
        return True
    
    await conn.execute(
        "UPDATE ingestion_files SET is_encrypted = true WHERE file_id = $1",
        file_id
    )
    
    if verbose:
        print(f"    Marked file {file_id} as encrypted")
    
    return True


async def get_file_role_ids(conn: asyncpg.Connection, file_id: str) -> List[str]:
    """Get role IDs associated with a file (for shared files)."""
    rows = await conn.fetch(
        "SELECT role_id FROM document_roles WHERE file_id = $1",
        file_id
    )
    return [str(row['role_id']) for row in rows]


# =============================================================================
# MinIO Operations
# =============================================================================

def download_from_minio(
    client: Minio,
    bucket: str,
    storage_path: str
) -> bytes:
    """Download file content from MinIO."""
    try:
        response = client.get_object(bucket, storage_path)
        content = response.read()
        response.close()
        response.release_conn()
        return content
    except S3Error as e:
        raise RuntimeError(f"Failed to download from MinIO: {e}")


def upload_to_minio(
    client: Minio,
    bucket: str,
    storage_path: str,
    content: bytes
) -> None:
    """Upload file content to MinIO."""
    import io
    
    try:
        data_stream = io.BytesIO(content)
        client.put_object(
            bucket,
            storage_path,
            data_stream,
            length=len(content),
        )
    except S3Error as e:
        raise RuntimeError(f"Failed to upload to MinIO: {e}")


# =============================================================================
# Encryption Operations
# =============================================================================

async def encrypt_content(
    authz_base_url: str,
    admin_token: str,
    file_id: str,
    content: bytes,
    user_id: Optional[str] = None,
    role_ids: Optional[List[str]] = None,
) -> bytes:
    """Encrypt content using the authz keystore API."""
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }
    
    # Ensure KEKs exist for roles
    async with httpx.AsyncClient(timeout=60.0) as client:
        if role_ids:
            for role_id in role_ids:
                await client.post(
                    f"{authz_base_url}/keystore/kek/ensure-for-role/{role_id}",
                    headers=headers,
                )
        
        # Encrypt the content
        resp = await client.post(
            f"{authz_base_url}/keystore/encrypt",
            headers=headers,
            json={
                "file_id": file_id,
                "content": base64.b64encode(content).decode(),
                "role_ids": role_ids or [],
                "user_id": user_id,
            },
        )
        
        if resp.status_code == 200:
            result = resp.json()
            encrypted_b64 = result.get("encrypted_content")
            if encrypted_b64:
                return base64.b64decode(encrypted_b64)
            else:
                raise RuntimeError("Empty encrypted content returned")
        else:
            raise RuntimeError(f"Encryption failed: {resp.status_code} - {resp.text}")


# =============================================================================
# Migration Logic
# =============================================================================

async def process_file(
    conn: asyncpg.Connection,
    minio_client: Minio,
    bucket: str,
    authz_base_url: str,
    admin_token: str,
    file_info: Dict,
    dry_run: bool = False,
    verbose: bool = False,
) -> Tuple[bool, str]:
    """
    Process a single file for encryption.
    
    Returns:
        Tuple of (success, status_message)
    """
    file_id = str(file_info['file_id'])
    user_id = str(file_info['user_id'])
    storage_path = file_info['storage_path']
    visibility = file_info['visibility']
    filename = file_info['filename']
    
    try:
        # Step 1: Download from MinIO
        if verbose:
            print(f"  Downloading {filename} ({file_id})...")
        
        content = download_from_minio(minio_client, bucket, storage_path)
        
        # Step 2: Check if already encrypted
        if is_encrypted(content):
            # Already encrypted - just mark in DB
            await mark_file_encrypted(conn, file_info['file_id'], dry_run, verbose)
            return True, "already_encrypted"
        
        # Step 3: Determine encryption parameters
        role_ids = None
        encrypt_user_id = None
        
        if visibility == "shared":
            role_ids = await get_file_role_ids(conn, file_info['file_id'])
            if not role_ids:
                return False, "shared_no_roles"
        else:
            encrypt_user_id = user_id
        
        # Step 4: Encrypt content
        if verbose:
            print(f"    Encrypting {len(content)} bytes...")
        
        if dry_run:
            print(f"    [DRY RUN] Would encrypt and re-upload {filename}")
            await mark_file_encrypted(conn, file_info['file_id'], dry_run, verbose)
            return True, "would_encrypt"
        
        encrypted_content = await encrypt_content(
            authz_base_url,
            admin_token,
            file_id,
            content,
            user_id=encrypt_user_id,
            role_ids=role_ids,
        )
        
        # Step 5: Re-upload to MinIO
        if verbose:
            print(f"    Re-uploading encrypted content ({len(encrypted_content)} bytes)...")
        
        upload_to_minio(minio_client, bucket, storage_path, encrypted_content)
        
        # Step 6: Update database
        await mark_file_encrypted(conn, file_info['file_id'], dry_run, verbose)
        
        return True, "encrypted"
        
    except Exception as e:
        return False, f"error: {str(e)}"


async def run_migration(
    dry_run: bool = False,
    verbose: bool = False,
    batch_size: int = 100,
) -> Dict[str, int]:
    """
    Run the file encryption migration.
    
    Returns:
        Dictionary with counts of different outcomes
    """
    print("=" * 60)
    print("File Encryption Migration")
    print("=" * 60)
    
    if dry_run:
        print("DRY RUN MODE - No changes will be made")
    
    print()
    
    # Get configuration
    db_url = get_ingest_db_url()
    minio_client = get_minio_client()
    bucket = get_minio_bucket()
    authz_base_url, admin_token = get_authz_config()
    
    print(f"Database: {db_url.split('@')[1] if '@' in db_url else db_url}")
    print(f"MinIO: {os.environ.get('MINIO_ENDPOINT', 'localhost:9000')}/{bucket}")
    print(f"AuthZ: {authz_base_url}")
    print()
    
    if not admin_token:
        print("ERROR: AUTHZ_ADMIN_TOKEN environment variable not set")
        print("This token is required to call the keystore encryption API")
        sys.exit(1)
    
    # Connect to database
    conn = await asyncpg.connect(db_url)
    
    # Track outcomes
    outcomes = {
        "encrypted": 0,
        "already_encrypted": 0,
        "would_encrypt": 0,
        "shared_no_roles": 0,
        "errors": 0,
        "total": 0,
    }
    
    try:
        total_processed = 0
        
        while True:
            # Fetch batch of unencrypted files
            print(f"Fetching batch of {batch_size} files...")
            files = await fetch_unencrypted_files(conn, batch_size, verbose)
            
            if not files:
                print("No more unencrypted files to process")
                break
            
            print(f"Processing {len(files)} files...")
            
            for file_info in files:
                outcomes["total"] += 1
                total_processed += 1
                
                success, status = await process_file(
                    conn,
                    minio_client,
                    bucket,
                    authz_base_url,
                    admin_token,
                    file_info,
                    dry_run,
                    verbose,
                )
                
                if success:
                    if status in outcomes:
                        outcomes[status] += 1
                    else:
                        outcomes["encrypted"] += 1
                else:
                    outcomes["errors"] += 1
                    if verbose:
                        print(f"    ERROR: {file_info['filename']} - {status}")
                
                # Progress indicator every 10 files
                if total_processed % 10 == 0:
                    print(f"  Processed {total_processed} files...")
            
            # If dry run, don't loop - just show what would happen for first batch
            if dry_run:
                print("(Stopping after first batch in dry-run mode)")
                break
        
        return outcomes
        
    finally:
        await conn.close()


async def main():
    parser = argparse.ArgumentParser(
        description="Encrypt existing unencrypted files in storage"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making changes"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of files to process per batch (default: 100)"
    )
    
    args = parser.parse_args()
    
    try:
        outcomes = await run_migration(
            dry_run=args.dry_run,
            verbose=args.verbose,
            batch_size=args.batch_size,
        )
        
        print()
        print("=" * 60)
        print("Migration Summary")
        print("=" * 60)
        print(f"Total files processed: {outcomes['total']}")
        print(f"  - Newly encrypted: {outcomes['encrypted']}")
        print(f"  - Already encrypted: {outcomes['already_encrypted']}")
        if args.dry_run:
            print(f"  - Would encrypt: {outcomes['would_encrypt']}")
        print(f"  - Shared without roles (skipped): {outcomes['shared_no_roles']}")
        print(f"  - Errors: {outcomes['errors']}")
        
        if args.dry_run:
            print()
            print("This was a DRY RUN. Run without --dry-run to apply changes.")
        
        print()
        print("Migration complete!")
        
        # Exit with error if there were failures
        if outcomes['errors'] > 0:
            sys.exit(1)
        
    except asyncpg.PostgresError as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
