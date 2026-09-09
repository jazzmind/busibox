"""
Attachment resolution service for agentic chat.

Resolves uploaded chat attachments into prompt-ready context by:
- Waiting for data-api processing completion (or partial availability)
- Fetching markdown for fully processed documents
- Using early chunk text from Postgres when embeddings aren't ready yet
- Applying lightweight keyword ranking for early-access chunk selection
- Applying token-aware full-document vs RAG chunk injection
"""

import asyncio
import logging
import math
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.busibox import BusiboxClient
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent, content, progress, thought

logger = logging.getLogger(__name__)

_STAGE_DISPLAY: dict[str, str] = {
    "queued": "Queued for processing",
    "parsing": "Extracting text",
    "classifying": "Classifying document",
    "extracting_metadata": "Extracting metadata",
    "chunking": "Splitting into chunks",
    "cleanup": "Cleaning up text",
    "markdown": "Generating readable format",
    "entity_extraction": "Extracting entities",
    "embedding": "Creating vector embeddings",
    "indexing": "Indexing for search",
    "available": "Almost ready",
}

TERMINAL_STATES = {"completed", "complete", "ready", "indexed", "success"}
EARLY_READY_STATES = {"available", "embedding", "indexing", "cleanup", "markdown", "entity_extraction"}
FAILED_STATES = {"failed", "error"}

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "because", "but", "and", "or", "if", "while", "about", "up",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their",
})

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")

StreamFn = Callable[[StreamEvent], Awaitable[None]]


class _ProcessingStatus:
    """Structured result from a processing status check."""

    __slots__ = ("stage", "is_terminal", "is_early_ready", "is_failed",
                 "pages_processed", "total_pages", "raw_info")

    def __init__(
        self,
        stage: str,
        *,
        is_terminal: bool = False,
        is_early_ready: bool = False,
        is_failed: bool = False,
        pages_processed: Optional[int] = None,
        total_pages: Optional[int] = None,
        raw_info: Optional[Dict[str, Any]] = None,
    ):
        self.stage = stage
        self.is_terminal = is_terminal
        self.is_early_ready = is_early_ready
        self.is_failed = is_failed
        self.pages_processed = pages_processed
        self.total_pages = total_pages
        self.raw_info = raw_info or {}


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

                result = await self._resolve_document(
                    client=client,
                    file_id=file_id,
                    filename=filename,
                    query=query,
                    available_tokens=available_tokens,
                    stream=stream,
                    attachment=attachment,
                )
                resolved.append(result)
            except Exception as exc:
                logger.warning("Attachment resolution failed for %s: %s", attachment.get("id"), exc)
                resolved.append(self._fallback_attachment(attachment))

        # Every branch above appends exactly one result per attachment, so the
        # lists line up. Carry the "sent on an earlier turn" marker through so
        # the prompt can say so.
        for meta, item in zip(attachment_metadata, resolved):
            if meta.get("carried_forward"):
                item["carried_forward"] = True

        return resolved

    async def _resolve_document(
        self,
        *,
        client: BusiboxClient,
        file_id: str,
        filename: str,
        query: str,
        available_tokens: int,
        stream: Optional[StreamFn],
        attachment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Resolve a document attachment, using early chunks if available."""
        proc_status = await self._check_status(client, file_id)

        if proc_status.is_terminal:
            return await self._resolve_completed_document(
                client=client,
                file_id=file_id,
                filename=filename,
                query=query,
                available_tokens=available_tokens,
                stream=stream,
                attachment=attachment,
            )

        if proc_status.is_failed:
            if stream:
                await stream(content(
                    source="attachments",
                    message=f"Processing failed for **{filename}**. I'll try to work with what's available.",
                    data={"phase": "attachment_failed", "file_id": file_id},
                ))
            return self._fallback_attachment(attachment)

        if proc_status.is_early_ready:
            return await self._resolve_early_document(
                client=client,
                file_id=file_id,
                filename=filename,
                query=query,
                available_tokens=available_tokens,
                stream=stream,
                attachment=attachment,
                proc_status=proc_status,
            )

        # Still in early pipeline (queued, parsing, chunking) -- poll until
        # we get either a terminal state or an early-ready state.
        return await self._resolve_with_polling(
            client=client,
            file_id=file_id,
            filename=filename,
            query=query,
            available_tokens=available_tokens,
            stream=stream,
            attachment=attachment,
        )

    async def _resolve_completed_document(
        self,
        *,
        client: BusiboxClient,
        file_id: str,
        filename: str,
        query: str,
        available_tokens: int,
        stream: Optional[StreamFn],
        attachment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Full-quality path for completed documents."""
        markdown = await self._fetch_markdown(client=client, file_id=file_id)
        if not (markdown or "").strip():
            # Processing finished but produced no text: almost always a
            # scanned / image-only PDF whose OCR pass has not run yet. Say so
            # explicitly rather than handing the model a bare "[Attachment]"
            # placeholder it might invent contents for.
            logger.info("Attachment %s completed with no extractable text", file_id)
            if stream:
                await stream(thought(
                    source="attachments",
                    message=f"**{filename}** has no extractable text yet (scanned document?).",
                    data={"phase": "attachment_no_text", "file_id": file_id},
                ))
            return self._no_text_attachment(attachment)

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
                content_text = markdown[: max(1000, available_tokens * 4)]

        if stream:
            await stream(thought(
                source="attachments",
                message=f"Prepared context from {filename} ({source_kind}).",
            ))

        return {
            "attachment_id": attachment.get("id"),
            "filename": filename,
            "source_kind": source_kind,
            "content": content_text,
        }

    async def _resolve_early_document(
        self,
        *,
        client: BusiboxClient,
        file_id: str,
        filename: str,
        query: str,
        available_tokens: int,
        stream: Optional[StreamFn],
        attachment: Dict[str, Any],
        proc_status: _ProcessingStatus,
    ) -> Dict[str, Any]:
        """Early-access path using raw chunk text from Postgres."""
        pages_proc = proc_status.pages_processed
        total_pages = proc_status.total_pages
        coverage = f"{pages_proc} of {total_pages} pages" if pages_proc and total_pages else "partial"

        if stream:
            await stream(content(
                source="attachments",
                message=(
                    f"**{filename}** is partially processed ({coverage}). "
                    "I'll answer with what's available now."
                ),
                data={
                    "phase": "attachment_early_ready",
                    "file_id": file_id,
                    "pages_processed": pages_proc,
                    "total_pages": total_pages,
                },
            ))

        chunks_response = await self._fetch_early_chunks(client=client, file_id=file_id)
        if not chunks_response:
            logger.warning("No early chunks available for %s, falling back", file_id)
            return self._fallback_attachment(attachment)

        ranked = self._rank_chunks_by_relevance(query, chunks_response)
        content_text = self._pack_chunks_to_budget(ranked, available_tokens)

        if not content_text.strip():
            return self._fallback_attachment(attachment)

        if stream:
            await stream(thought(
                source="attachments",
                message=f"Using early chunks from {filename} ({coverage}, keyword-ranked).",
            ))

        return {
            "attachment_id": attachment.get("id"),
            "filename": filename,
            "source_kind": "early_chunks",
            "content": content_text,
            "pages_processed": pages_proc,
            "total_pages": total_pages,
        }

    async def _resolve_with_polling(
        self,
        *,
        client: BusiboxClient,
        file_id: str,
        filename: str,
        query: str,
        available_tokens: int,
        stream: Optional[StreamFn],
        attachment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Poll until document reaches a usable state, then resolve."""
        elapsed = 0.0
        interval = 1.0
        max_interval = 8.0
        notified_initial = False
        last_stage = ""

        while elapsed < self.max_wait_seconds:
            proc_status = await self._check_status(client, file_id)

            if proc_status.is_terminal:
                if notified_initial and stream:
                    await stream(content(
                        source="attachments",
                        message=f"**{filename}** is ready.",
                        data={"phase": "attachment_ready", "file_id": file_id},
                    ))
                return await self._resolve_completed_document(
                    client=client,
                    file_id=file_id,
                    filename=filename,
                    query=query,
                    available_tokens=available_tokens,
                    stream=stream,
                    attachment=attachment,
                )

            if proc_status.is_failed:
                if stream:
                    await stream(content(
                        source="attachments",
                        message=f"Processing failed for **{filename}**. I'll try to work with what's available.",
                        data={"phase": "attachment_failed", "file_id": file_id},
                    ))
                raise RuntimeError(f"Processing failed for {filename}")

            if proc_status.is_early_ready:
                return await self._resolve_early_document(
                    client=client,
                    file_id=file_id,
                    filename=filename,
                    query=query,
                    available_tokens=available_tokens,
                    stream=stream,
                    attachment=attachment,
                    proc_status=proc_status,
                )

            stage_lower = proc_status.stage.lower()
            if stream:
                stage_label = _STAGE_DISPLAY.get(stage_lower, stage_lower)
                if not notified_initial:
                    await stream(content(
                        source="attachments",
                        message=(
                            f"**{filename}** is still being processed ({stage_label}). "
                            "I'll wait for it to finish before answering your question."
                        ),
                        data={
                            "phase": "attachment_processing",
                            "file_id": file_id,
                            "stage": stage_lower,
                            "filename": filename,
                        },
                    ))
                    notified_initial = True
                elif stage_lower != last_stage:
                    await stream(progress(
                        source="attachments",
                        message=f"Processing **{filename}**: {stage_label}...",
                        data={
                            "phase": "attachment_processing",
                            "file_id": file_id,
                            "stage": stage_lower,
                            "filename": filename,
                        },
                    ))
                last_stage = stage_lower

            await asyncio.sleep(interval)
            elapsed += interval
            interval = min(max_interval, interval * 2)

        if stream:
            await stream(content(
                source="attachments",
                message=(
                    f"**{filename}** is taking longer than expected to process. "
                    "I'll answer with what's available, but some details may be incomplete."
                ),
                data={"phase": "attachment_timeout", "file_id": file_id},
            ))
        raise RuntimeError(f"Timed out waiting for {filename} processing")

    # ------------------------------------------------------------------
    # Status check
    # ------------------------------------------------------------------

    async def _check_status(self, client: BusiboxClient, file_id: str) -> _ProcessingStatus:
        """Poll data-api for file processing status and return structured result."""
        file_info = await client.request("GET", f"/files/{file_id}")
        stage = self._extract_status(file_info)
        stage_lower = stage.lower()

        pages_processed, total_pages = self._extract_page_progress(file_info)

        return _ProcessingStatus(
            stage=stage,
            is_terminal=stage_lower in TERMINAL_STATES,
            is_early_ready=stage_lower in EARLY_READY_STATES,
            is_failed=stage_lower in FAILED_STATES,
            pages_processed=pages_processed,
            total_pages=total_pages,
            raw_info=file_info,
        )

    # ------------------------------------------------------------------
    # Early chunk fetching (Postgres, no embeddings required)
    # ------------------------------------------------------------------

    async def _fetch_early_chunks(
        self,
        *,
        client: BusiboxClient,
        file_id: str,
        limit: int = 200,
    ) -> List[str]:
        """Fetch raw chunk text from data-api before embeddings are ready."""
        try:
            response = await client.request(
                "GET",
                f"/files/{file_id}/chunks",
                params={"limit": limit},
            )
        except Exception as exc:
            logger.warning("Failed to fetch early chunks for %s: %s", file_id, exc)
            return []
        return self._extract_chunk_texts(response)

    # ------------------------------------------------------------------
    # Keyword-based chunk ranking (no LLM, no vectors)
    # ------------------------------------------------------------------

    @staticmethod
    def _rank_chunks_by_relevance(query: str, chunks: List[str]) -> List[str]:
        """Rank chunks by lightweight keyword overlap with the query.

        Uses a simplified BM25-like scoring: for each query term that appears
        in a chunk, score += 1 / log(1 + df) where df is the number of chunks
        containing that term. Chunks are returned sorted by descending score,
        with positional order as a tiebreaker.
        """
        if not chunks or not query:
            return chunks

        query_terms = {
            t for t in _WORD_RE.findall(query.lower()) if t not in _STOPWORDS and len(t) > 1
        }
        if not query_terms:
            return chunks

        chunk_term_sets: List[set] = []
        for chunk in chunks:
            chunk_term_sets.append(set(_WORD_RE.findall(chunk.lower())))

        # Document frequency per query term
        df: Dict[str, int] = {}
        for term in query_terms:
            df[term] = sum(1 for ts in chunk_term_sets if term in ts)

        scored: List[Tuple[float, int, str]] = []
        for idx, (chunk, terms) in enumerate(zip(chunks, chunk_term_sets)):
            score = 0.0
            for qt in query_terms:
                if qt in terms:
                    score += 1.0 / math.log(1 + max(df.get(qt, 1), 1))
            scored.append((score, idx, chunk))

        scored.sort(key=lambda t: (-t[0], t[1]))
        return [chunk for _, _, chunk in scored]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    def _extract_page_progress(self, payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
        """Extract pages_processed and total_pages from nested file info."""
        for container in (payload, payload.get("status", {}), payload.get("data", {})):
            if not isinstance(container, dict):
                continue
            pp = container.get("pages_processed")
            tp = container.get("total_pages")
            if pp is not None or tp is not None:
                return (
                    int(pp) if pp is not None else None,
                    int(tp) if tp is not None else None,
                )
        return None, None

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

    def _no_text_attachment(self, attachment: Dict[str, Any]) -> Dict[str, Any]:
        """Result for a processed document that yielded no text (e.g. a scan)."""
        return {
            "attachment_id": attachment.get("id"),
            "filename": attachment.get("filename") or "attachment",
            "source_kind": "no_text",
            "mime_type": (attachment.get("mime_type") or "").lower(),
            "content": "",
        }


attachment_resolver = AttachmentResolver()
