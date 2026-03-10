"""
Progressive pipeline processing methods extracted from worker.py.

This module provides the PipelineMixin class containing methods for:
- Fast reprocessing (re-embed and re-index existing chunks)
- Progressive PDF processing (3-pass enhancement pipeline)
- Chunk/embed/index operations for progressive passes
- Markdown path updates in the database

The IngestWorker class inherits from PipelineMixin to use these as instance methods
with access to self (services, config, etc.).
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

import structlog

from processors.chunker import Chunk
from processors.progressive_pipeline import PageText, PassResult, ProgressiveContext, ProgressivePipeline

logger = structlog.get_logger()


class CancelledError(Exception):
    """Raised when processing is cancelled via the cancel API."""


class PipelineMixin:
    """Mixin providing progressive pipeline processing methods for IngestWorker."""

    CANCEL_KEY_PREFIX = "cancel:"
    CANCEL_KEY_TTL = 300  # seconds

    def _is_cancelled(self, file_id: str) -> bool:
        """Check whether a cancellation has been requested for this file."""
        try:
            return bool(self.redis_client.exists(f"{self.CANCEL_KEY_PREFIX}{file_id}"))
        except Exception:
            return False

    def _handle_cancellation(self, file_id: str) -> None:
        """Mark the document as cancelled and clean up the cancel flag."""
        self.postgres_service.update_status(
            file_id=file_id,
            stage="cancelled",
            status_message="Processing cancelled by user",
            request=self._current_rls_context,
        )
        try:
            self.redis_client.delete(f"{self.CANCEL_KEY_PREFIX}{file_id}")
        except Exception:
            pass
        raise CancelledError(f"Processing cancelled for {file_id}")

    def _store_page_texts(
        self,
        ctx,
        file_id: str,
        storage_path: str,
        user_id: str,
    ) -> None:
        """Persist per-page text as JSON in MinIO for on-demand enhancement."""
        import json as _json

        page_data = [
            {
                "page_number": pt.page_number,
                "text": pt.text,
                "source_pass": pt.source_pass,
                "flags": pt.flags if pt.flags else [],
            }
            for pt in ctx.page_texts
        ]

        path_parts = storage_path.rsplit("/", 2)
        if len(path_parts) >= 2:
            base_path = path_parts[0] + "/" + file_id
        else:
            base_path = f"{user_id}/{file_id}"
        object_path = f"{base_path}/page_texts.json"

        data = _json.dumps(page_data, ensure_ascii=False).encode("utf-8")
        try:
            import io
            self.file_service.client.put_object(
                bucket_name=self.file_service.bucket,
                object_name=object_path,
                data=io.BytesIO(data),
                length=len(data),
                content_type="application/json",
            )
            logger.info("Page texts stored", file_id=file_id, path=object_path, pages=len(page_data))
        except Exception as e:
            logger.warning("Failed to store page texts", file_id=file_id, error=str(e))

    def _enqueue_pass2(
        self,
        file_id: str,
        user_id: str,
        storage_path: str,
        mime_type: str,
        original_filename: str,
        visibility: str,
        role_ids: Optional[List[str]],
        delegation_token: Optional[str],
    ) -> None:
        """Enqueue a Pass 2 (OCR enhancement) job on the low-priority stream."""
        import json as _json
        from datetime import datetime as _dt

        stream = f"{self.stream_name}:low" if hasattr(self, "stream_name") else "jobs:data:low"
        job_data = {
            "job_id": file_id,
            "file_id": file_id,
            "user_id": user_id,
            "storage_path": storage_path,
            "mime_type": mime_type,
            "original_filename": original_filename,
            "created_at": _dt.utcnow().isoformat(),
            "visibility": visibility,
            "priority": "low",
            "processing_config": _json.dumps({"start_pass": 2}),
        }
        if role_ids:
            job_data["role_ids"] = _json.dumps(role_ids)
        if delegation_token:
            job_data["delegation_token"] = delegation_token

        self.redis_client.xadd(stream, job_data, maxlen=10000)
        logger.info("Pass 2 enqueued on low-priority stream", file_id=file_id, stream=stream)

    def _fast_reprocess(
        self,
        file_id: str,
        user_id: str,
        chunks: List,
        start_stage: str,
        visibility: str,
        role_ids: Optional[List[str]],
        processing_config: dict,
        start_time: float,
    ):
        """
        Fast path for partial reprocessing - just re-embed and/or re-index.
        
        This is used when chunks already exist and we just need to regenerate
        embeddings or re-index into Milvus.
        """
        total_chunks = len(chunks)
        
        logger.info(
            "Fast reprocess starting",
            file_id=file_id,
            start_stage=start_stage,
            chunk_count=total_chunks,
        )
        
        self.history.log_stage_start(
            file_id, start_stage,
            f"Fast reprocess from {start_stage} with {total_chunks} existing chunks",
            metadata={"chunk_count": total_chunks, "fast_path": True}
        )
        
        def _chunk_has_embedding(chunk_obj: Any) -> bool:
            embedding = getattr(chunk_obj, "embedding", None)
            return isinstance(embedding, list) and len(embedding) > 0

        # PostgreSQL no longer stores chunk embeddings; ensure we have embeddings
        # whenever we need to index.
        need_embeddings = start_stage == "embedding" or any(not _chunk_has_embedding(c) for c in chunks)
        if need_embeddings:
            embedding_start = self.history.log_stage_start(
                file_id, "embedding",
                f"Starting embedding generation for {total_chunks} chunks",
                metadata={"chunk_count": total_chunks}
            )
            
            self.postgres_service.update_status(
                file_id=file_id,
                stage="embedding",
                progress=50,
                chunks_processed=0,
                total_chunks=total_chunks,
                status_message=f"Embedding {total_chunks} chunks",
                request=self._current_rls_context,
            )
            
            # Generate text embeddings
            text_embedding_start = time.time()
            text_embeddings = self.embedder.embed_chunks_sync(
                [chunk.text for chunk in chunks]
            )
            
            # Assign embeddings to chunks
            for i, chunk in enumerate(chunks):
                if i < len(text_embeddings):
                    chunk.embedding = text_embeddings[i]
            
            self.history.log_substep(
                file_id, "embedding", "text_embeddings",
                f"Generated {len(text_embeddings)} text embeddings",
                metadata={"count": len(text_embeddings)},
                started_at=text_embedding_start
            )
            
            self.history.log_stage_complete(
                file_id, "embedding",
                f"Completed embedding generation: {len(text_embeddings)} text",
                metadata={"text_count": len(text_embeddings)},
                started_at=embedding_start
            )
        
        # Stage: Indexing (always runs in fast path)
        indexing_start = self.history.log_stage_start(
            file_id, "indexing",
            f"Starting Milvus indexing for {total_chunks} chunks",
            metadata={"chunk_count": total_chunks}
        )
        
        self.postgres_service.update_status(
            file_id=file_id,
            stage="indexing",
            progress=82,
            status_message="Indexing in vector database",
            request=self._current_rls_context,
        )
        
        # Prepare vectors for Milvus
        text_vectors = []
        for chunk in chunks:
            if hasattr(chunk, 'embedding') and chunk.embedding:
                chunk_index = getattr(chunk, "chunk_index", getattr(chunk, "index", 0))
                chunk_metadata = getattr(chunk, "metadata", {})
                text_vectors.append({
                    "file_id": file_id,
                    "chunk_index": chunk_index,
                    "embedding": chunk.embedding,
                    "text": chunk.text[:1000],  # Truncate for storage
                    "metadata": chunk_metadata,
                })
        
        # Insert into Milvus
        if text_vectors:
            try:
                self.milvus_service.insert_text_vectors(
                    file_id=file_id,
                    user_id=user_id,
                    vectors=text_vectors,
                    visibility=visibility,
                    role_ids=role_ids,
                )
                self.history.log_substep(
                    file_id, "indexing", "milvus_text_insert",
                    f"Inserted {len(text_vectors)} text vectors into Milvus",
                    metadata={"count": len(text_vectors)},
                    started_at=indexing_start
                )
            except Exception as e:
                logger.error(
                    "Failed to insert text vectors",
                    file_id=file_id,
                    error=str(e),
                    exc_info=True,
                )
        
        self.history.log_stage_complete(
            file_id, "indexing",
            f"Completed Milvus indexing: {len(text_vectors)} total vectors",
            metadata={"total_vectors": len(text_vectors)},
            started_at=indexing_start
        )
        
        # Mark as completed
        processing_time = time.time() - start_time
        self.postgres_service.update_status(
            file_id=file_id,
            stage="completed",
            progress=100,
            chunks_processed=total_chunks,
            total_chunks=total_chunks,
            status_message="Processing complete",
            request=self._current_rls_context,
        )
        
        self.postgres_service.update_file_metadata(
            file_id=file_id,
            chunk_count=total_chunks,
            vector_count=len(text_vectors),
            processing_duration_seconds=int(processing_time),
            request=self._current_rls_context,
        )
        
        self.history.log_stage_complete(
            file_id, "completed",
            f"Fast reprocess complete: {total_chunks} chunks, {len(text_vectors)} vectors in {processing_time:.0f}s",
            metadata={
                "chunk_count": total_chunks,
                "vector_count": len(text_vectors),
                "processing_time_seconds": processing_time,
                "fast_path": True,
            },
            started_at=start_time
        )
        
        logger.info(
            "Fast reprocess completed",
            file_id=file_id,
            chunk_count=total_chunks,
            vector_count=len(text_vectors),
            processing_time_seconds=processing_time,
        )
    
    def _process_pdf_progressive(
        self,
        file_id: str,
        user_id: str,
        temp_file_path: str,
        storage_path: str,
        mime_type: str,
        original_filename: str,
        content_hash: str,
        visibility: str,
        role_ids: Optional[List[str]],
        processing_config: dict,
        delegation_token: Optional[str],
        start_time: float,
    ):
        """
        Process a PDF using the progressive enhancement pipeline.
        
        Runs 3 passes, making the document viewable after Pass 1 and
        progressively improving quality through Pass 2 (OCR) and Pass 3 (LLM+Marker).
        
        Supports processing_config["start_pass"] (1, 2, or 3) to skip earlier passes
        during reprocessing.
        """
        from processors.pdf_splitter import PDFSplitter
        
        start_pass = int(processing_config.get("start_pass", 1)) if processing_config else 1
        start_pass = max(1, min(3, start_pass))
        
        page_count = self.text_extractor.pdf_splitter.get_page_count(temp_file_path)
        
        ctx = ProgressiveContext(
            file_id=file_id,
            file_path=temp_file_path,
            storage_path=storage_path,
            user_id=user_id,
            mime_type=mime_type,
            page_count=page_count,
            visibility=visibility,
            role_ids=role_ids,
            content_hash=content_hash,
        )
        
        def _progress(stage: str, pct: int, msg: str):
            self.postgres_service.update_status(
                file_id=file_id,
                stage=stage,
                progress=pct,
                pages_processed=pct,
                total_pages=ctx.page_count,
                status_message=msg,
                request=self._current_rls_context,
            )
        
        if start_pass > 1:
            logger.info(
                "Progressive reprocess: skipping to pass",
                file_id=file_id,
                start_pass=start_pass,
            )
        
        # ── Pass 1: Fast Extract (page-batched for large PDFs) ───────────
        # Always run Pass 1 text extraction to populate ctx.page_texts
        # (needed by subsequent passes even when start_pass > 1)
        page_batch_size = int(
            (processing_config or {}).get("page_batch_size", 20)
        )
        num_batches = max(1, (page_count + page_batch_size - 1) // page_batch_size)
        use_batching = page_count > page_batch_size

        self.history.log_stage_start(
            file_id, "parsing",
            f"Progressive Pass 1: Fast text extraction (pymupdf4llm + layout)"
            f"{' (' + str(num_batches) + ' batches)' if use_batching else ''}"
            f"{' (text-only, skipping index)' if start_pass > 1 else ''}",
            metadata={"pass": 1, "page_count": page_count, "start_pass": start_pass,
                       "page_batch_size": page_batch_size, "num_batches": num_batches},
        )
        self.postgres_service.update_pass_info(
            file_id, processing_pass=1,
            pass_metadata={"current_pass": 1, "total_passes": 2, "pass_name": "Fast Extract"},
            request=self._current_rls_context,
        )

        pass1_start = time.time()
        total_chunks = 0
        chunk_index_offset = 0
        overlap_page_texts: list = []
        OVERLAP_PAGES = 2

        for batch_idx in range(num_batches):
            if self._is_cancelled(file_id):
                self._handle_cancellation(file_id)

            batch_start = batch_idx * page_batch_size + 1
            batch_end = min((batch_idx + 1) * page_batch_size, page_count)

            batch_page_texts, batch_combined = (
                self.progressive_pipeline.run_pass1_batch(
                    ctx, batch_start, batch_end, progress_callback=_progress,
                )
            )

            ctx.page_texts.extend(batch_page_texts)
            ctx.pass1_texts.extend([pt.text for pt in batch_page_texts])

            if start_pass <= 1:
                batch_chunks = self.progressive_pipeline.chunk_text_for_batch(
                    batch_page_texts, overlap_page_texts, chunk_index_offset,
                )

                if batch_chunks:
                    self._batch_chunk_embed_index(
                        file_id, user_id, content_hash,
                        visibility, role_ids,
                        batch_chunks, processing_pass=1,
                    )
                    chunk_index_offset += len(batch_chunks)
                    total_chunks += len(batch_chunks)

                batch_combined_for_md = self.progressive_pipeline._combine_page_texts(
                    batch_page_texts
                )
                markdown_content, _ = self.progressive_pipeline.generate_markdown(
                    batch_combined_for_md, extraction_method="simple",
                )
                self.progressive_pipeline.upload_markdown(
                    self.file_service, file_id, storage_path, user_id,
                    self.progressive_pipeline._combine_page_texts(ctx.page_texts),
                )

                pct = 5 + int(25 * (batch_idx + 1) / num_batches)
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="available" if batch_idx == 0 else "available",
                    progress=pct,
                    chunks_processed=total_chunks,
                    total_chunks=total_chunks,
                    pages_processed=batch_end,
                    total_pages=page_count,
                    status_message=(
                        f"Pages 1-{batch_end} available"
                        + (f", processing remaining..." if batch_end < page_count else "")
                    ),
                    request=self._current_rls_context,
                )

            overlap_page_texts = batch_page_texts[-OVERLAP_PAGES:]

        pass1_duration = time.time() - pass1_start
        pass1_combined = self.progressive_pipeline._combine_page_texts(ctx.page_texts)

        ctx.page_count = len(ctx.page_texts) or page_count
        ctx.pass_metadata["pass1"] = {
            "pages": ctx.page_count,
            "total_chars": len(pass1_combined),
            "batches": num_batches,
            "page_batch_size": page_batch_size,
        }

        # Classify and extract metadata from the full document
        _progress("classifying", 22, "Classifying document type")
        document_type, confidence = self.classifier.classify(
            pass1_combined, original_filename, mime_type,
        )
        primary_language, detected_languages = self.classifier.detect_languages(
            pass1_combined,
        )

        _progress("extracting_metadata", 28, "Extracting metadata")
        metadata = self.metadata_extractor.extract(
            temp_file_path, mime_type, pass1_combined,
        )
        self.postgres_service.update_file_metadata(
            file_id=file_id,
            document_type=document_type,
            primary_language=primary_language,
            detected_languages=detected_languages,
            extracted_title=metadata.get("title"),
            extracted_author=metadata.get("author"),
            extracted_date=metadata.get("date"),
            extracted_keywords=metadata.get("keywords", []),
            request=self._current_rls_context,
        )

        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            with conn.cursor() as cur:
                word_count = len(pass1_combined.split())
                cur.execute("""
                    UPDATE data_files
                    SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                    WHERE file_id = %s
                """, (json.dumps({"page_count": ctx.page_count, "word_count": word_count}), file_id))
                conn.commit()
            self.postgres_service._return_connection(conn)
        except Exception as e:
            logger.warning("Failed to update metadata JSON", file_id=file_id, error=str(e))

        # Extract and upload images
        images_metadata = []
        images_data = []
        image_refs = []
        try:
            images_metadata, images_data = self.image_extractor.extract(
                temp_file_path, mime_type=mime_type
            )
            for i, img_meta in enumerate(images_metadata):
                image_refs.append({
                    'path': f'images/image_{i}.png',
                    'caption': f'Image {i+1}',
                    'page': img_meta.get('page'),
                })
            if images_data:
                path_parts = storage_path.rsplit("/", 2)
                if len(path_parts) >= 2:
                    base_path = path_parts[0] + "/" + file_id
                else:
                    base_path = f"{user_id}/{file_id}"
                images_path = f"{base_path}/images"
                import io as _io
                for i, (img_data, img_meta) in enumerate(zip(images_data, images_metadata)):
                    image_path = f"{images_path}/image_{i}.png"
                    self.file_service.client.put_object(
                        bucket_name=self.file_service.bucket,
                        object_name=image_path,
                        data=_io.BytesIO(img_data),
                        length=len(img_data),
                        content_type='image/png',
                    )
                if images_metadata:
                    import json as _json
                    _meta_json = _json.dumps(images_metadata, default=str).encode("utf-8")
                    _meta_path = f"{images_path}/metadata.json"
                    self.file_service.client.put_object(
                        bucket_name=self.file_service.bucket,
                        object_name=_meta_path,
                        data=_io.BytesIO(_meta_json),
                        length=len(_meta_json),
                        content_type='application/json',
                    )

                logger.info(
                    "Images extracted and uploaded",
                    file_id=file_id,
                    image_count=len(images_data),
                )
                try:
                    conn = self.postgres_service._get_connection(self._current_rls_context)
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE data_files
                            SET images_path = %s, image_count = %s
                            WHERE file_id = %s
                        """, (images_path, len(images_data), file_id))
                        conn.commit()
                    self.postgres_service._return_connection(conn)
                except Exception as e:
                    logger.warning("Failed to update image metadata", file_id=file_id, error=str(e))
        except Exception as img_err:
            logger.warning(
                "Image extraction failed (non-fatal)",
                file_id=file_id,
                error=str(img_err),
            )

        # Replace pymupdf4llm image placeholders with actual image refs
        if image_refs:
            self.progressive_pipeline.replace_image_placeholders(ctx, image_refs)
            pass1_combined = self.progressive_pipeline._combine_page_texts(ctx.page_texts)

        # Upload final markdown with image refs
        markdown_content, md_metadata = self.progressive_pipeline.generate_markdown(
            pass1_combined, extraction_method="simple",
            images=image_refs if image_refs else None,
        )
        markdown_path = self.progressive_pipeline.upload_markdown(
            self.file_service, file_id, storage_path, user_id, markdown_content,
        )
        if markdown_path:
            self._update_markdown_in_db(file_id, markdown_path, user_id)

        self.postgres_service.update_status(
            file_id=file_id,
            stage="available",
            progress=30,
            chunks_processed=total_chunks,
            total_chunks=total_chunks,
            pages_processed=ctx.page_count,
            total_pages=ctx.page_count,
            status_message="Content available - enhancing quality",
            request=self._current_rls_context,
        )

        self.history.log_stage_complete(
            file_id, "parsing",
            f"Pass 1 complete: {total_chunks} chunks in {num_batches} batch(es), document now viewable",
            metadata={"pass": 1, "chunks": total_chunks, "duration": pass1_duration,
                       "batches": num_batches},
            started_at=start_time,
        )

        self._check_pass_triggers(file_id, user_id, delegation_token, current_pass=1)

        # If this job started at Pass 1, enqueue Pass 2 as a low-priority job
        # so new uploads get their Pass 1 first.
        if start_pass <= 1:
            self._enqueue_pass2(
                file_id=file_id,
                user_id=user_id,
                storage_path=storage_path,
                mime_type=mime_type,
                original_filename=original_filename,
                visibility=visibility,
                role_ids=role_ids,
                delegation_token=delegation_token,
            )
            # Mark as available (enhancing) with current chunk counts
            self.postgres_service.update_status(
                file_id=file_id,
                stage="available",
                progress=50,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
                status_message="Pass 1 complete, enhancement queued",
                request=self._current_rls_context,
            )

            processing_duration = int(time.time() - start_time)
            self.postgres_service.update_file_metadata(
                file_id=file_id,
                chunk_count=total_chunks,
                vector_count=total_chunks,
                processing_duration_seconds=processing_duration,
                request=self._current_rls_context,
            )
            return

        # ── Pass 2: OCR Enhancement (Tesseract) ─────────────────────────
        if start_pass <= 2:
            if self._is_cancelled(file_id):
                self._handle_cancellation(file_id)

            self.history.log_stage_start(
                file_id, "available",
                "Progressive Pass 2: Tesseract OCR enhancement",
                metadata={"pass": 2},
            )
            self.postgres_service.update_pass_info(
                file_id, processing_pass=2,
                pass_metadata={
                    "current_pass": 2, "total_passes": 2, "pass_name": "OCR Enhancement",
                    **ctx.pass_metadata,
                },
                request=self._current_rls_context,
            )
            
            pass2 = self.progressive_pipeline.run_pass2(ctx, progress_callback=_progress)
            
            if pass2.pages_changed > 0:
                chunks = self._progressive_chunk_embed_index(
                    ctx, pass2, file_id, user_id, content_hash,
                    visibility, role_ids, processing_pass=2,
                )
                total_chunks = len(chunks)
                
                markdown_content, _ = self.progressive_pipeline.generate_markdown(
                    pass2.combined_text, extraction_method="simple",
                    images=image_refs if image_refs else None,
                )
                markdown_path = self.progressive_pipeline.upload_markdown(
                    self.file_service, file_id, storage_path, user_id, markdown_content,
                )
                if markdown_path:
                    self._update_markdown_in_db(file_id, markdown_path, user_id)
                
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="available",
                    progress=50,
                    chunks_processed=total_chunks,
                    total_chunks=total_chunks,
                    status_message=f"OCR enhanced {pass2.pages_changed} pages",
                    request=self._current_rls_context,
                )
            else:
                logger.info(
                    "Pass 2: No pages improved by OCR, skipping re-index",
                    file_id=file_id,
                )
            
            self.history.log_stage_complete(
                file_id, "parsing",
                f"Pass 2 complete: {pass2.pages_changed} pages enhanced, {pass2.pages_skipped} unchanged",
                metadata={"pass": 2, "changed": pass2.pages_changed, "skipped": pass2.pages_skipped},
            )
            
            self._check_pass_triggers(file_id, user_id, delegation_token, current_pass=2)
        else:
            logger.info("Skipping Pass 2", start_pass=start_pass, file_id=file_id)
        
        # Pass 3 (LLM Cleanup) is no longer run automatically.
        # It can be triggered on-demand per page via the enhance API endpoint
        # or via the reprocess UI with start_pass=3.
        
        # Persist per-page text data for on-demand enhancement operations
        self._store_page_texts(ctx, file_id, storage_path, user_id)

        # ── Complete ──────────────────────────────────────────────────────
        processing_duration = int(time.time() - start_time)
        
        self.postgres_service.update_file_metadata(
            file_id=file_id,
            chunk_count=total_chunks,
            vector_count=total_chunks,
            processing_duration_seconds=processing_duration,
            request=self._current_rls_context,
        )
        
        self.postgres_service.update_pass_info(
            file_id, processing_pass=2,
            pass_metadata={
                "total_passes": 2, "pass_name": "Completed",
                "processing_duration_seconds": processing_duration,
                **ctx.pass_metadata,
            },
            request=self._current_rls_context,
        )
        
        self.postgres_service.update_status(
            file_id=file_id,
            stage="completed",
            progress=100,
            chunks_processed=total_chunks,
            total_chunks=total_chunks,
            pages_processed=ctx.page_count,
            total_pages=ctx.page_count,
            status_message="Processing complete",
            request=self._current_rls_context,
        )
        
        self.history.log_stage_complete(
            file_id, "completed",
            f"Progressive processing complete: {total_chunks} chunks in {processing_duration}s",
            metadata={
                "total_chunks": total_chunks,
                "processing_duration_seconds": processing_duration,
                "passes_completed": 2,
                "pass_metadata": ctx.pass_metadata,
            },
            started_at=start_time,
        )
        
        # Pass-2 triggers already fired at line 713 after Pass 2 completed.
        # Do NOT re-fire here; it causes duplicate extraction.
        
        logger.info(
            "Progressive PDF processing completed",
            file_id=file_id,
            total_chunks=total_chunks,
            processing_time=processing_duration,
        )
    
    def _progressive_chunk_embed_index(
        self,
        ctx: ProgressiveContext,
        pass_result: PassResult,
        file_id: str,
        user_id: str,
        content_hash: str,
        visibility: str,
        role_ids: Optional[List[str]],
        processing_pass: int,
    ) -> List[Chunk]:
        """Chunk text, embed, upsert to PG, and index in Milvus for a progressive pass."""
        chunks = self.progressive_pipeline.chunk_text(
            pass_result.combined_text, pass_result.page_texts,
        )
        
        if not chunks:
            logger.warning("No chunks produced", file_id=file_id, pass_number=processing_pass)
            return []
        
        # Store chunks in PostgreSQL (upsert for progressive updates)
        chunk_dicts = [c.to_dict() for c in chunks]
        self.postgres_service.upsert_chunks(file_id, chunk_dicts, processing_pass=processing_pass)
        
        # Generate embeddings
        chunk_texts = [c.text for c in chunks]
        embeddings = self.embedder.embed_chunks_sync(chunk_texts)
        
        # Index in Milvus (delete + re-insert for progressive updates)
        chunk_dicts_for_milvus = [
            {
                "text": c.text,
                "chunk_index": c.chunk_index,
                "page_number": c.page_number,
                "char_offset": c.char_offset,
                "section_heading": c.section_heading,
                "language": c.language,
            }
            for c in chunks
        ]
        
        self.milvus_service.update_file_vectors(
            file_id=file_id,
            user_id=user_id,
            chunks=chunk_dicts_for_milvus,
            embeddings=embeddings,
            content_hash=content_hash,
            visibility=visibility,
            role_ids=role_ids,
        )
        
        logger.info(
            "Progressive chunk/embed/index complete",
            file_id=file_id,
            pass_number=processing_pass,
            chunk_count=len(chunks),
        )
        
        return chunks

    def _batch_chunk_embed_index(
        self,
        file_id: str,
        user_id: str,
        content_hash: str,
        visibility: str,
        role_ids: Optional[List[str]],
        chunks: List[Chunk],
        processing_pass: int,
    ) -> None:
        """
        Embed and index pre-chunked data using append (not delete+reinsert).

        Used during page-batched Pass 1 where each batch's chunks are appended
        incrementally.  Passes 2/3 continue to use _progressive_chunk_embed_index
        which does a full delete+reinsert.
        """
        if not chunks:
            return

        chunk_dicts = [c.to_dict() for c in chunks]
        self.postgres_service.upsert_chunks(file_id, chunk_dicts, processing_pass=processing_pass)

        chunk_texts = [c.text for c in chunks]
        embeddings = self.embedder.embed_chunks_sync(chunk_texts)

        chunk_dicts_for_milvus = [
            {
                "text": c.text,
                "chunk_index": c.chunk_index,
                "page_number": c.page_number,
                "char_offset": c.char_offset,
                "section_heading": c.section_heading,
                "language": c.language,
            }
            for c in chunks
        ]

        self.milvus_service.insert_text_chunks(
            file_id=file_id,
            user_id=user_id,
            chunks=chunk_dicts_for_milvus,
            embeddings=embeddings,
            content_hash=content_hash,
            visibility=visibility,
            role_ids=role_ids,
        )

        logger.info(
            "Batch chunk/embed/index complete (append)",
            file_id=file_id,
            pass_number=processing_pass,
            chunk_count=len(chunks),
        )

    def _update_markdown_in_db(self, file_id: str, markdown_path: str, user_id: str):
        """Update data_files with markdown path."""
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE data_files
                        SET has_markdown = true,
                            markdown_path = %s,
                            updated_at = NOW()
                        WHERE file_id = %s
                    """, (markdown_path, file_id))
                    conn.commit()
            finally:
                self.postgres_service._return_connection(conn)
        except Exception as e:
            logger.warning(
                "Failed to update markdown path in DB",
                file_id=file_id,
                error=str(e),
            )
