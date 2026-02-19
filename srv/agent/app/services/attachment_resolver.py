"""
Attachment resolution service for agentic chat.

Resolves uploaded chat attachments into prompt-ready context by:
- Waiting for data-api processing completion
- Fetching markdown for processed documents
- Applying token-aware full-document vs RAG chunk injection
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.busibox import BusiboxClient
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent, thought

logger = logging.getLogger(__name__)

StreamFn = Callable[[StreamEvent], Awaitable[None]]


class AttachmentResolver:
    """Resolves uploaded attachment records into model-ready context."""

    def __init__(
        self,
        *,
        max_wait_seconds: int = 60,
        reserve_response_tokens: int = 2000,
        default_context_window_tokens: int = 12000,
    ) -> None:
        self.max_wait_seconds = max_wait_seconds
        self.reserve_response_tokens = reserve_response_tokens
        self.default_context_window_tokens = default_context_window_tokens

    async def resolve(
        self,
        *,
        query: str,
        attachment_metadata: List[Dict[str, Any]],
        principal: Optional[Principal],
        user_id: Optional[str],
        session: Optional[AsyncSession] = None,
        stream: Optional[StreamFn] = None,
        context_token_estimate: int = 0,
    ) -> List[Dict[str, Any]]:
        if not attachment_metadata:
            return []

        resolved: List[Dict[str, Any]] = []

        if not principal or not principal.token or not user_id:
            logger.warning("Attachment resolution skipped: missing principal token or user id")
            for attachment in attachment_metadata:
                resolved.append(self._fallback_attachment(attachment))
            return resolved

        data_token: Optional[str] = None

        # Use the proven get_or_exchange_token path (DB-cached, same as tools)
        if session:
            try:
                from app.services.token_service import get_or_exchange_token
                exchange_result = await get_or_exchange_token(
                    session=session,
                    principal=principal,
                    scopes=["data.read"],
                    purpose="data",
                )
                data_token = exchange_result.access_token
            except Exception as exc:
                logger.warning("Token exchange via token_service failed: %s", exc)

        # Fallback: direct zero-trust exchange
        if not data_token:
            try:
                from app.auth.token_exchange import exchange_token_zero_trust
                result = await exchange_token_zero_trust(
                    subject_token=principal.token,
                    target_audience="data-api",
                    user_id=user_id,
                    scopes="data.read",
                )
                data_token = result.access_token if result else None
            except Exception as exc:
                logger.warning("Direct token exchange failed: %s", exc)

        if not data_token:
            logger.warning("Attachment resolution skipped: failed to get data-api token")
            for attachment in attachment_metadata:
                resolved.append(self._fallback_attachment(attachment))
            return resolved

        client = BusiboxClient(data_token)

        query_tokens = self._estimate_tokens(query)
        available_tokens = max(
            1000,
            self.default_context_window_tokens
            - self.reserve_response_tokens
            - context_token_estimate
            - query_tokens,
        )

        for attachment in attachment_metadata:
            try:
                source_kind = "document"
                file_id = attachment.get("file_id")
                mime_type = (attachment.get("mime_type") or "").lower()
                filename = attachment.get("filename") or "attachment"

                if mime_type.startswith("image/"):
                    resolved.append(
                        {
                            "attachment_id": attachment.get("id"),
                            "filename": filename,
                            "source_kind": "image",
                            "image_url": attachment.get("file_url"),
                            "content": "",
                        }
                    )
                    continue

                if not file_id:
                    parsed = attachment.get("parsed_content")
                    if parsed:
                        resolved.append(
                            {
                                "attachment_id": attachment.get("id"),
                                "filename": filename,
                                "source_kind": "parsed_content",
                                "content": str(parsed),
                            }
                        )
                    else:
                        resolved.append(self._fallback_attachment(attachment))
                    continue

                await self._wait_until_processed(
                    client=client,
                    file_id=file_id,
                    filename=filename,
                    stream=stream,
                )

                markdown = await self._fetch_markdown(client=client, file_id=file_id)
                if not markdown:
                    resolved.append(self._fallback_attachment(attachment))
                    continue

                markdown_tokens = self._estimate_tokens(markdown)
                if markdown_tokens <= available_tokens:
                    source_kind = "full_markdown"
                    content_text = markdown
                else:
                    source_kind = "rag_chunks"
                    chunks = await self._search_chunks(
                        client=client,
                        file_id=file_id,
                        query=query,
                    )
                    content_text = self._pack_chunks_to_budget(chunks, available_tokens)
                    if not content_text.strip():
                        # Last resort: trimmed markdown head to avoid empty context.
                        content_text = markdown[: max(1000, available_tokens * 4)]

                if stream:
                    await stream(
                        thought(
                            source="attachments",
                            message=f"Prepared context from {filename} ({source_kind}).",
                        )
                    )

                resolved.append(
                    {
                        "attachment_id": attachment.get("id"),
                        "filename": filename,
                        "source_kind": source_kind,
                        "content": content_text,
                    }
                )
            except Exception as exc:
                logger.warning("Attachment resolution failed for %s: %s", attachment.get("id"), exc)
                resolved.append(self._fallback_attachment(attachment))

        return resolved

    async def _wait_until_processed(
        self,
        *,
        client: BusiboxClient,
        file_id: str,
        filename: str,
        stream: Optional[StreamFn],
    ) -> None:
        elapsed = 0.0
        interval = 1.0
        max_interval = 8.0
        notified = False

        while elapsed < self.max_wait_seconds:
            file_info = await client.request("GET", f"/files/{file_id}")
            status = self._extract_status(file_info)
            status_lower = status.lower()
            logger.debug(
                "Attachment status poll: file_id=%s status=%s elapsed=%.1f",
                file_id, status, elapsed,
            )

            if status_lower in {"completed", "complete", "ready", "indexed", "success"}:
                return
            if status_lower in {"failed", "error"}:
                raise RuntimeError(f"Processing failed for {filename}")

            if stream and not notified:
                await stream(
                    thought(
                        source="attachments",
                        message=f"Waiting for {filename} to finish processing...",
                    )
                )
                notified = True

            await asyncio.sleep(interval)
            elapsed += interval
            interval = min(max_interval, interval * 2)

        raise RuntimeError(f"Timed out waiting for {filename} processing")

    async def _fetch_markdown(self, *, client: BusiboxClient, file_id: str) -> str:
        response = await client.request("GET", f"/files/{file_id}/markdown")
        if isinstance(response, dict):
            markdown = (
                response.get("markdown")
                or response.get("content")
                or (response.get("data") or {}).get("markdown")
                or ""
            )
            return str(markdown)
        return ""

    async def _search_chunks(self, *, client: BusiboxClient, file_id: str, query: str) -> List[str]:
        response = await client.request(
            "POST",
            f"/files/{file_id}/search",
            json={"query": query, "limit": 20},
        )
        return self._extract_chunk_texts(response)

    def _extract_status(self, payload: Dict[str, Any]) -> str:
        candidate_keys = [
            "status",
            "processing_status",
            "state",
            "data_status",
        ]
        nested_keys = ["stage", "status", "state"]
        for key in candidate_keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                for nk in nested_keys:
                    nested_val = value.get(nk)
                    if isinstance(nested_val, str) and nested_val:
                        return nested_val
        data = payload.get("data")
        if isinstance(data, dict):
            for key in candidate_keys:
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
                if isinstance(value, dict):
                    for nk in nested_keys:
                        nested_val = value.get(nk)
                        if isinstance(nested_val, str) and nested_val:
                            return nested_val
        return "unknown"

    def _extract_chunk_texts(self, response: Dict[str, Any]) -> List[str]:
        result_sets = []
        if isinstance(response, dict):
            for key in ("results", "chunks", "hits", "data"):
                value = response.get(key)
                if isinstance(value, list):
                    result_sets.append(value)
                elif isinstance(value, dict):
                    for nested_key in ("results", "chunks", "hits"):
                        nested_value = value.get(nested_key)
                        if isinstance(nested_value, list):
                            result_sets.append(nested_value)

        chunks: List[str] = []
        for result_set in result_sets:
            for item in result_set:
                if isinstance(item, str):
                    chunks.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("snippet")
                    or item.get("chunk_text")
                    or item.get("markdown")
                )
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
        return chunks

    def _pack_chunks_to_budget(self, chunks: List[str], token_budget: int) -> str:
        if not chunks:
            return ""
        kept: List[str] = []
        used_tokens = 0
        for idx, chunk in enumerate(chunks, start=1):
            chunk_tokens = self._estimate_tokens(chunk)
            if used_tokens + chunk_tokens > token_budget:
                break
            kept.append(f"[Chunk {idx}]\n{chunk}")
            used_tokens += chunk_tokens
        return "\n\n".join(kept)

    def _estimate_tokens(self, text: str) -> int:
        # Simple fallback estimate; adequate for budgeting decisions.
        return max(1, len(text) // 4)

    def _fallback_attachment(self, attachment: Dict[str, Any]) -> Dict[str, Any]:
        filename = attachment.get("filename") or "attachment"
        mime_type = (attachment.get("mime_type") or "").lower()
        if mime_type.startswith("image/"):
            return {
                "attachment_id": attachment.get("id"),
                "filename": filename,
                "source_kind": "image",
                "image_url": attachment.get("file_url"),
                "content": "",
            }
        return {
            "attachment_id": attachment.get("id"),
            "filename": filename,
            "source_kind": "fallback",
            "content": f"[Attachment: {filename}]",
        }


attachment_resolver = AttachmentResolver()
