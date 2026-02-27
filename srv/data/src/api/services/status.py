"""
Status service for real-time status tracking via SSE.

Uses PostgreSQL LISTEN/NOTIFY for efficient pub/sub updates.
"""

import json
import uuid
from typing import AsyncIterator, Dict, Optional

import asyncpg
import structlog

logger = structlog.get_logger()


class StatusService:
    """Service for status tracking with SSE."""
    
    def __init__(self, config: dict):
        """Initialize status service."""
        self.config = config
        self.host = config.get("postgres_host", "postgres")
        self.port = config.get("postgres_port", 5432)
        self.database = config.get("postgres_db", "busibox")
        self.user = config.get("postgres_user", "postgres")
        self.password = config.get("postgres_password", "")
    
    async def get_current_status(self, file_id: str, user_id: Optional[str] = None) -> Optional[Dict]:
        """Get current status for a file, setting RLS context when user_id is provided."""
        conn = await asyncpg.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
        )
        
        try:
            if user_id:
                await conn.execute(f"SET app.user_id = '{user_id}'")
            
            row = await conn.fetchrow("""
                SELECT 
                    s.file_id,
                    s.stage,
                    s.progress,
                    s.chunks_processed,
                    s.total_chunks,
                    s.pages_processed,
                    s.total_pages,
                    s.error_message,
                    s.status_message,
                    s.processing_pass,
                    s.pass_metadata,
                    s.started_at,
                    s.completed_at,
                    s.updated_at
                FROM data_status s
                WHERE s.file_id = $1
            """, uuid.UUID(file_id))
            
            if not row:
                return None
            
            result = {
                "fileId": str(row["file_id"]),
                "stage": row["stage"],
                "progress": row["progress"],
                "chunksProcessed": row["chunks_processed"],
                "totalChunks": row["total_chunks"],
                "pagesProcessed": row["pages_processed"],
                "totalPages": row["total_pages"],
                "errorMessage": row["error_message"],
                "statusMessage": row["status_message"],
                "startedAt": row["started_at"].isoformat() if row["started_at"] else None,
                "completedAt": row["completed_at"].isoformat() if row["completed_at"] else None,
                "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
            
            # Include progressive pipeline pass details if present
            processing_pass = row.get("processing_pass")
            pass_metadata_raw = row.get("pass_metadata")
            if processing_pass is not None:
                result["processingPass"] = processing_pass
                pass_metadata = {}
                if pass_metadata_raw:
                    if isinstance(pass_metadata_raw, str):
                        try:
                            pass_metadata = json.loads(pass_metadata_raw)
                        except (json.JSONDecodeError, TypeError):
                            pass_metadata = {}
                    else:
                        pass_metadata = dict(pass_metadata_raw) if pass_metadata_raw else {}
                
                result["passDetails"] = {
                    "currentPass": pass_metadata.get("current_pass", processing_pass),
                    "totalPasses": pass_metadata.get("total_passes", 3),
                    "passName": pass_metadata.get("pass_name", ""),
                }
            
            return result
        finally:
            await conn.close()
    
    async def stream_status_updates(
        self,
        file_id: str,
        user_id: str,
    ) -> AsyncIterator[Dict]:
        """
        Stream status updates via PostgreSQL LISTEN/NOTIFY.
        
        Yields:
            Status update dictionaries
        """
        conn = await asyncpg.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
        )
        
        try:
            if user_id:
                await conn.execute(f"SET app.user_id = '{user_id}'")
            
            # Send current status immediately
            current = await self.get_current_status(file_id, user_id=user_id)
            if current:
                yield current
            else:
                yield {
                    "error": "File not found",
                    "fileId": file_id,
                }
                return
            
            # Verify user ownership
            ownership_check = await conn.fetchrow("""
                SELECT user_id FROM data_files WHERE file_id = $1
            """, uuid.UUID(file_id))
            
            if not ownership_check or str(ownership_check["user_id"]) != user_id:
                yield {
                    "error": "Unauthorized access",
                    "fileId": file_id,
                }
                return
            
            # Set up LISTEN on status_updates channel
            await conn.add_listener("status_updates", self._handle_notify)
            
            # Wait for updates (with timeout handling)
            # Note: asyncpg doesn't have built-in async iteration for notifications
            # We'll use a polling approach with asyncio.sleep
            import asyncio
            
            last_update = current.get("updatedAt")
            timeout_count = 0
            max_timeout = 600  # 10 minutes max
            
            while timeout_count < max_timeout:
                # Check for updates
                updated = await self.get_current_status(file_id, user_id=user_id)
                if updated and updated.get("updatedAt") != last_update:
                    yield updated
                    last_update = updated.get("updatedAt")
                    
                    # Stop if completed or failed
                    if updated.get("stage") in ["completed", "failed"]:
                        break
                
                await asyncio.sleep(1)  # Poll every second
                timeout_count += 1
            
            if timeout_count >= max_timeout:
                yield {
                    "error": "Status stream timeout",
                    "fileId": file_id,
                }
        
        finally:
            await conn.remove_listener("status_updates", self._handle_notify)
            await conn.close()
    
    def _handle_notify(self, connection, pid, channel, payload):
        """Handle PostgreSQL NOTIFY event."""
        # This is called synchronously by asyncpg
        # For async handling, we use polling in stream_status_updates
        pass

