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


class PipelineMixin:
    """Mixin providing progressive pipeline processing methods for IngestWorker."""

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
        """
        from processors.pdf_splitter import PDFSplitter
        
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
            )
        
        # ── Pass 1: Fast Extract ──────────────────────────────────────────
        self.history.log_stage_start(
            file_id, "parsing",
            "Progressive Pass 1: Fast text extraction (pymupdf4llm + layout)",
            metadata={"pass": 1, "page_count": page_count},
        )
        self.postgres_service.update_pass_info(
            file_id, processing_pass=1,
            pass_metadata={"current_pass": 1, "total_passes": 3, "pass_name": "Fast Extract"},
            request=self._current_rls_context,
        )
        
        pass1 = self.progressive_pipeline.run_pass1(ctx, progress_callback=_progress)
        
        # Classify and extract metadata from Pass 1 text
        _progress("classifying", 22, "Classifying document type")
        document_type, confidence = self.classifier.classify(
            pass1.combined_text, original_filename, mime_type,
        )
        primary_language, detected_languages = self.classifier.detect_languages(
            pass1.combined_text,
        )
        
        _progress("extracting_metadata", 28, "Extracting metadata")
        metadata = self.metadata_extractor.extract(
            temp_file_path, mime_type, pass1.combined_text,
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
        
        # Update page_count and word_count metadata
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            with conn.cursor() as cur:
                word_count = len(pass1.combined_text.split())
                cur.execute("""
                    UPDATE data_files
                    SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                    WHERE file_id = %s
                """, (json.dumps({"page_count": ctx.page_count, "word_count": word_count}), file_id))
                conn.commit()
            self.postgres_service._return_connection(conn)
        except Exception as e:
            logger.warning("Failed to update metadata JSON", file_id=file_id, error=str(e))
        
        # Chunk, embed, index Pass 1
        chunks = self._progressive_chunk_embed_index(
            ctx, pass1, file_id, user_id, content_hash,
            visibility, role_ids, processing_pass=1,
        )
        total_chunks = len(chunks)
        
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
                # Save image metadata JSON for duplicate/decorative filtering
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
        
        # Replace pymupdf4llm image placeholders with actual image refs in page texts
        if image_refs:
            self.progressive_pipeline.replace_image_placeholders(ctx, image_refs)
            pass1.combined_text = self.progressive_pipeline._combine_page_texts(ctx.page_texts)

        # Generate and upload markdown (with inline images if available)
        markdown_content, md_metadata = self.progressive_pipeline.generate_markdown(
            pass1.combined_text, extraction_method="simple",
            images=image_refs if image_refs else None,
        )
        markdown_path = self.progressive_pipeline.upload_markdown(
            self.file_service, file_id, storage_path, user_id, markdown_content,
        )
        
        # Update DB with markdown path
        if markdown_path:
            self._update_markdown_in_db(file_id, markdown_path, user_id)
        
        # Set stage=available -- content is now viewable and searchable
        self.postgres_service.update_status(
            file_id=file_id,
            stage="available",
            progress=30,
            chunks_processed=total_chunks,
            total_chunks=total_chunks,
            pages_processed=ctx.page_count,
            total_pages=ctx.page_count,
            status_message="Content available - enhancing quality",
        )
        
        self.history.log_stage_complete(
            file_id, "parsing",
            f"Pass 1 complete: {total_chunks} chunks, document now viewable",
            metadata={"pass": 1, "chunks": total_chunks, "duration": pass1.duration_seconds},
            started_at=start_time,
        )
        
        # Check if triggers should run at pass 1
        self._check_pass_triggers(file_id, user_id, delegation_token, current_pass=1)
        
        # ── Pass 2: OCR Enhancement (Tesseract) ─────────────────────────
        self.history.log_stage_start(
            file_id, "available",
            "Progressive Pass 2: Tesseract OCR enhancement",
            metadata={"pass": 2},
        )
        self.postgres_service.update_pass_info(
            file_id, processing_pass=2,
            pass_metadata={
                "current_pass": 2, "total_passes": 3, "pass_name": "OCR Enhancement",
                **ctx.pass_metadata,
            },
            request=self._current_rls_context,
        )
        
        pass2 = self.progressive_pipeline.run_pass2(ctx, progress_callback=_progress)
        
        if pass2.pages_changed > 0:
            # Re-chunk, re-embed, re-index only if OCR improved text
            chunks = self._progressive_chunk_embed_index(
                ctx, pass2, file_id, user_id, content_hash,
                visibility, role_ids, processing_pass=2,
            )
            total_chunks = len(chunks)
            
            # Update markdown with images
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
        
        # ── Pass 3: LLM Cleanup + Selective Marker ───────────────────────
        llm_cleanup_enabled = (
            (processing_config.get("llm_cleanup_enabled", False) if processing_config else False)
            or (self.llm_cleanup and self.llm_cleanup.enabled)
        )
        
        if llm_cleanup_enabled:
            # processing_config overrides the env-var default on the LLMCleanup instance
            if self.llm_cleanup and not self.llm_cleanup.enabled:
                self.llm_cleanup.enabled = True

            self.history.log_stage_start(
                file_id, "cleanup",
                "Progressive Pass 3: LLM cleanup + selective Marker",
                metadata={"pass": 3},
            )
            self.postgres_service.update_pass_info(
                file_id, processing_pass=3,
                pass_metadata={
                    "current_pass": 3, "total_passes": 3,
                    "pass_name": "LLM Cleanup + Marker",
                    **ctx.pass_metadata,
                },
                request=self._current_rls_context,
            )
            
            pass3 = self.progressive_pipeline.run_pass3(ctx, progress_callback=_progress)
            
            # Post-cleanup sanitization: strip residual unicode junk, leftover placeholders
            from processors.progressive_pipeline import ProgressivePipeline, PageText
            for i, pt in enumerate(ctx.page_texts):
                sanitized = ProgressivePipeline.post_cleanup_sanitize(pt.text)
                if sanitized != pt.text:
                    ctx.page_texts[i] = PageText(
                        page_number=pt.page_number,
                        text=sanitized,
                        text_hash=PageText.compute_hash(sanitized),
                        source_pass=pt.source_pass,
                        needs_marker=pt.needs_marker,
                        marker_reason=pt.marker_reason,
                    )
                    pass3.pages_changed = max(pass3.pages_changed, 1)
            pass3.combined_text = self.progressive_pipeline._combine_page_texts(ctx.page_texts)

            if pass3.pages_changed > 0:
                chunks = self._progressive_chunk_embed_index(
                    ctx, pass3, file_id, user_id, content_hash,
                    visibility, role_ids, processing_pass=3,
                )
                total_chunks = len(chunks)
                
                # Final markdown with images
                markdown_content, _ = self.progressive_pipeline.generate_markdown(
                    pass3.combined_text, extraction_method="simple",
                    images=image_refs if image_refs else None,
                )
                markdown_path = self.progressive_pipeline.upload_markdown(
                    self.file_service, file_id, storage_path, user_id, markdown_content,
                )
                if markdown_path:
                    self._update_markdown_in_db(file_id, markdown_path, user_id)
            
            self.history.log_stage_complete(
                file_id, "cleanup",
                f"Pass 3 complete: {pass3.pages_changed} pages improved",
                metadata={
                    "pass": 3,
                    "changed": pass3.pages_changed,
                    "marker_pages": ctx.pass_metadata.get("pass3", {}).get("marker_pages_requested", 0),
                },
            )
        else:
            logger.info("Pass 3 skipped: LLM cleanup disabled", file_id=file_id)
        
        # ── Complete ──────────────────────────────────────────────────────
        processing_duration = int(time.time() - start_time)
        
        self.postgres_service.update_file_metadata(
            file_id=file_id,
            chunk_count=total_chunks,
            vector_count=total_chunks,  # 1:1 for text chunks
            processing_duration_seconds=processing_duration,
            request=self._current_rls_context,
        )
        
        self.postgres_service.update_pass_info(
            file_id, processing_pass=3,
            pass_metadata={
                "total_passes": 3, "pass_name": "Completed",
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
        )
        
        self.history.log_stage_complete(
            file_id, "completed",
            f"Progressive processing complete: {total_chunks} chunks in {processing_duration}s",
            metadata={
                "total_chunks": total_chunks,
                "processing_duration_seconds": processing_duration,
                "passes_completed": 3 if llm_cleanup_enabled else 2,
                "pass_metadata": ctx.pass_metadata,
            },
            started_at=start_time,
        )
        
        # Run pass-3 triggers (includes default triggers)
        self._check_pass_triggers(file_id, user_id, delegation_token, current_pass=3)
        
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
