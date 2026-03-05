"""
Progressive enhancement pipeline for PDF document ingestion.

Runs 3 passes over a PDF document, producing progressively better text:

  Pass 1 (Fast Extract):    pdfplumber per-page → chunk → embed → index → available
  Pass 2 (OCR Enhancement): Surya OCR per-page → diff → update changed chunks
  Pass 3 (LLM + Marker):   LLM cleanup + selective Marker → final chunks

After Pass 1 the document is viewable and searchable (stage=available).
Passes 2 and 3 improve quality in the background, updating chunks/embeddings
incrementally via upsert so the user always sees the best available version.
"""

import asyncio
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

from processors.text_extractor import TextExtractor
from processors.chunker import Chunker, Chunk
from processors.llm_cleanup import LLMCleanup, CleanupAssessment
from processors.markdown_generator import MarkdownGenerator
from processors.vision_extractor import VisionExtractor

logger = structlog.get_logger()

PASS_NAMES = {
    1: "Fast Extract",
    2: "OCR Enhancement",
    3: "LLM Cleanup + Marker",
}

# Similarity threshold: if a page's text hash changed between passes,
# the new text is considered an improvement worth re-processing.
MIN_TEXT_CHANGE_RATIO = 0.05  # At least 5% difference in length to re-process


@dataclass
class PageText:
    """Text content for a single page, tracked across passes."""
    page_number: int  # 1-based
    text: str
    text_hash: str
    source_pass: int
    needs_marker: bool = False
    marker_reason: str = ""
    flags: set = field(default_factory=set)

    @staticmethod
    def compute_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class PassResult:
    """Result of a single progressive pass."""
    pass_number: int
    page_texts: List[PageText]
    pages_changed: int
    pages_skipped: int
    combined_text: str
    duration_seconds: float


@dataclass
class ProgressiveContext:
    """Shared context across all passes for a single document."""
    file_id: str
    file_path: str
    storage_path: str
    user_id: str
    mime_type: str
    page_count: int
    visibility: str = "personal"
    role_ids: Optional[List[str]] = None
    content_hash: str = ""

    # Per-page text across passes (index 0 = page 1)
    page_texts: List[PageText] = field(default_factory=list)
    # Pass 1 texts preserved for LLM comparison (layout-rich extraction)
    pass1_texts: List[str] = field(default_factory=list)
    # Current chunks and embeddings
    chunks: List[Chunk] = field(default_factory=list)
    embeddings: List[List[float]] = field(default_factory=list)

    pass_metadata: Dict = field(default_factory=dict)


class ProgressivePipeline:
    """
    Orchestrates the 3-pass progressive enhancement pipeline for PDFs.

    Collaborates with external services (postgres, milvus, file_service, embedder)
    provided by the worker, but encapsulates all pass logic internally.
    """

    def __init__(
        self,
        text_extractor: TextExtractor,
        chunker: Chunker,
        llm_cleanup: LLMCleanup,
        markdown_generator: MarkdownGenerator,
        config: dict,
        vision_extractor: Optional[VisionExtractor] = None,
    ):
        self.text_extractor = text_extractor
        self.chunker = chunker
        self.llm_cleanup = llm_cleanup
        self.markdown_generator = markdown_generator
        self.config = config
        self.vision_extractor = vision_extractor

    # =========================================================================
    # Pass 1: Fast Extract (pdfplumber)
    # =========================================================================

    def run_pass1(
        self,
        ctx: ProgressiveContext,
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
    ) -> PassResult:
        """
        Pass 1: Fast per-page text extraction with pdfplumber.

        Args:
            ctx: Progressive context with file info
            progress_callback: Optional (stage, progress_pct, message) callback

        Returns:
            PassResult with extracted page texts
        """
        start = time.time()
        if progress_callback:
            progress_callback("parsing", 5, "Pass 1: Fast text extraction")

        page_texts_raw = self.text_extractor.extract_all_pages_fast(ctx.file_path)
        ctx.page_count = len(page_texts_raw) or ctx.page_count

        page_texts: List[PageText] = []
        for i, text in enumerate(page_texts_raw):
            pt = PageText(
                page_number=i + 1,
                text=text,
                text_hash=PageText.compute_hash(text),
                source_pass=1,
            )
            page_texts.append(pt)

            if progress_callback and ctx.page_count > 0:
                pct = 5 + int(15 * (i + 1) / ctx.page_count)
                progress_callback(
                    "parsing", pct,
                    f"Pass 1: Extracted page {i+1}/{ctx.page_count}",
                )

        ctx.page_texts = page_texts
        ctx.pass1_texts = [pt.text for pt in page_texts]
        combined = self._combine_page_texts(page_texts)

        ctx.pass_metadata["pass1"] = {
            "pages": ctx.page_count,
            "total_chars": len(combined),
            "page_hashes": {pt.page_number: pt.text_hash for pt in page_texts},
        }

        duration = time.time() - start
        logger.info(
            "Pass 1 complete",
            file_id=ctx.file_id,
            pages=ctx.page_count,
            total_chars=len(combined),
            duration=f"{duration:.1f}s",
        )

        return PassResult(
            pass_number=1,
            page_texts=page_texts,
            pages_changed=ctx.page_count,
            pages_skipped=0,
            combined_text=combined,
            duration_seconds=duration,
        )

    # =========================================================================
    # Pass 1 Batch: extract a page range for incremental availability
    # =========================================================================

    def run_pass1_batch(
        self,
        ctx: ProgressiveContext,
        start_page: int,
        end_page: int,
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
    ) -> Tuple[List[PageText], str]:
        """
        Extract a range of pages (1-based inclusive) and return PageText objects.

        Does NOT modify ctx.page_texts -- the caller accumulates batches into
        ctx and decides when to set ctx.pass1_texts.

        Returns:
            (batch_page_texts, combined_text_for_batch)
        """
        page_numbers = list(range(start_page, end_page + 1))
        page_texts_raw = self.text_extractor.extract_pages_fast(
            ctx.file_path, page_numbers,
        )

        batch_page_texts: List[PageText] = []
        for i, text in enumerate(page_texts_raw):
            page_num = start_page + i
            flags = self._detect_page_flags(text)
            pt = PageText(
                page_number=page_num,
                text=text,
                text_hash=PageText.compute_hash(text),
                source_pass=1,
                flags=flags,
            )
            batch_page_texts.append(pt)

            if progress_callback and ctx.page_count > 0:
                pct = 5 + int(25 * page_num / ctx.page_count)
                progress_callback(
                    "parsing", pct,
                    f"Pass 1: Extracted page {page_num}/{ctx.page_count}",
                )

        combined = self._combine_page_texts(batch_page_texts)
        return batch_page_texts, combined

    def chunk_text_for_batch(
        self,
        batch_page_texts: List[PageText],
        overlap_page_texts: List[PageText],
        chunk_index_offset: int,
    ) -> List[Chunk]:
        """
        Chunk a page batch with overlap context from the previous batch.

        Prepends overlap_page_texts so the chunker sees continuous text across
        the boundary, then discards chunks whose char_offset falls within the
        overlap region and re-indexes starting from chunk_index_offset.
        """
        all_page_texts = overlap_page_texts + batch_page_texts
        combined = self._combine_page_texts(all_page_texts)
        if not combined.strip():
            return []

        chunks = self.chunker.chunk(combined)

        page_boundaries = self._build_page_boundaries(all_page_texts)
        for chunk in chunks:
            if chunk.char_offset is not None:
                chunk.page_number = self._page_for_offset(
                    chunk.char_offset, page_boundaries
                )

        if overlap_page_texts:
            overlap_combined = self._combine_page_texts(overlap_page_texts)
            overlap_len = len(overlap_combined) + len("\n\n---\n\n")
            chunks = [c for c in chunks if c.char_offset is None or c.char_offset >= overlap_len]

        for i, chunk in enumerate(chunks):
            chunk.chunk_index = chunk_index_offset + i

        return chunks

    @staticmethod
    def _detect_page_flags(text: str) -> set:
        """Detect quality flags for a page based on its extracted text."""
        flags: set = set()
        stripped = text.strip()

        if len(stripped) < 50:
            flags.add("low_text")

        placeholder_pattern = re.compile(
            r'==>.*?picture.*?intentionally\s+omitted\s*<==', re.IGNORECASE
        )
        placeholder_count = len(placeholder_pattern.findall(text))
        if placeholder_count > 0:
            flags.add("has_images")

        chart_keywords = re.compile(
            r'\b(Figure|Chart|Graph|Exhibit)\b', re.IGNORECASE
        )
        if placeholder_count > 0 and chart_keywords.search(text):
            flags.add("has_chart")

        pipe_lines = [
            ln for ln in text.split('\n')
            if ln.strip().startswith('|') and ln.strip().endswith('|')
        ]
        if len(pipe_lines) >= 2:
            col_counts = [ln.count('|') - 1 for ln in pipe_lines]
            if len(set(col_counts)) > 1:
                flags.add("garbled_table")

        return flags

    # =========================================================================
    # Pass 2: OCR Enhancement (Surya)
    # =========================================================================

    def run_pass2(
        self,
        ctx: ProgressiveContext,
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
    ) -> PassResult:
        """
        Pass 2: Flag-based OCR + vision enhancement.

        Only processes pages that were flagged during Pass 1. Dispatch:
          - low_text: Tesseract first, vision OCR if still poor
          - has_chart / has_images: vision describe/chart (skip Tesseract)
          - garbled_table: vision table extraction
          - No flags: skip entirely
        """
        start = time.time()
        if progress_callback:
            progress_callback("available", 30, "Pass 2: OCR + vision enhancement")

        flagged_indices = [
            i for i, pt in enumerate(ctx.page_texts) if pt.flags
        ]

        if not flagged_indices:
            logger.info("Pass 2: No flagged pages, skipping", file_id=ctx.file_id)
            combined = self._combine_page_texts(ctx.page_texts)
            ctx.pass_metadata["pass2"] = {
                "pages_changed": 0, "pages_skipped": len(ctx.page_texts),
                "flagged_pages": 0,
            }
            return PassResult(
                pass_number=2, page_texts=ctx.page_texts,
                pages_changed=0, pages_skipped=len(ctx.page_texts),
                combined_text=combined, duration_seconds=time.time() - start,
            )

        logger.info(
            "Pass 2: Processing flagged pages",
            file_id=ctx.file_id,
            flagged_count=len(flagged_indices),
            total_pages=ctx.page_count,
        )

        pages_changed = 0
        pages_skipped = 0
        vision_used = 0

        for progress_idx, i in enumerate(flagged_indices):
            pt = ctx.page_texts[i]
            flags = pt.flags
            improved_text: Optional[str] = None

            if "has_chart" in flags and self.vision_extractor:
                result = asyncio.get_event_loop().run_until_complete(
                    self.vision_extractor.analyse_page(
                        ctx.file_path, pt.page_number, mode="chart",
                        existing_text=pt.text,
                    )
                )
                if result.success and result.text:
                    improved_text = result.text
                    vision_used += 1

            elif "garbled_table" in flags and self.vision_extractor:
                result = asyncio.get_event_loop().run_until_complete(
                    self.vision_extractor.analyse_page(
                        ctx.file_path, pt.page_number, mode="table",
                        existing_text=pt.text,
                    )
                )
                if result.success and result.text:
                    improved_text = result.text
                    vision_used += 1

            elif "has_images" in flags and self.vision_extractor:
                result = asyncio.get_event_loop().run_until_complete(
                    self.vision_extractor.analyse_page(
                        ctx.file_path, pt.page_number, mode="describe",
                        existing_text=pt.text,
                    )
                )
                if result.success and result.text:
                    improved_text = result.text
                    vision_used += 1

            elif "low_text" in flags:
                ocr_texts = self.text_extractor.ocr_all_pages_tesseract(
                    ctx.file_path, ctx.page_count
                )
                ocr_text = ocr_texts[i] if i < len(ocr_texts) else ""
                if self._is_meaningful_improvement(pt.text, ocr_text):
                    improved_text = ocr_text
                elif self.vision_extractor:
                    result = asyncio.get_event_loop().run_until_complete(
                        self.vision_extractor.analyse_page(
                            ctx.file_path, pt.page_number, mode="ocr",
                            existing_text=pt.text,
                        )
                    )
                    if result.success and result.text:
                        improved_text = result.text
                        vision_used += 1

            if improved_text and self._is_meaningful_improvement(pt.text, improved_text):
                ctx.page_texts[i] = PageText(
                    page_number=pt.page_number,
                    text=improved_text,
                    text_hash=PageText.compute_hash(improved_text),
                    source_pass=2,
                    flags=pt.flags,
                )
                pages_changed += 1
            else:
                pages_skipped += 1

            if progress_callback and flagged_indices:
                pct = 30 + int(20 * (progress_idx + 1) / len(flagged_indices))
                progress_callback(
                    "available", pct,
                    f"Pass 2: page {pt.page_number}/{ctx.page_count} "
                    f"({pages_changed} improved, vision={vision_used})",
                )

        combined = self._combine_page_texts(ctx.page_texts)

        ctx.pass_metadata["pass2"] = {
            "pages_changed": pages_changed,
            "pages_skipped": pages_skipped,
            "flagged_pages": len(flagged_indices),
            "vision_used": vision_used,
            "page_hashes": {pt.page_number: pt.text_hash for pt in ctx.page_texts},
        }

        duration = time.time() - start
        logger.info(
            "Pass 2 complete",
            file_id=ctx.file_id,
            pages_changed=pages_changed,
            pages_skipped=pages_skipped,
            flagged_pages=len(flagged_indices),
            vision_used=vision_used,
            duration=f"{duration:.1f}s",
        )

        return PassResult(
            pass_number=2,
            page_texts=ctx.page_texts,
            pages_changed=pages_changed,
            pages_skipped=pages_skipped,
            combined_text=combined,
            duration_seconds=duration,
        )

    # =========================================================================
    # Pass 3: LLM Cleanup + Selective Marker
    # =========================================================================

    def run_pass3(
        self,
        ctx: ProgressiveContext,
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
    ) -> PassResult:
        """
        Pass 3: LLM cleanup with structured Marker assessment, then selective
        Marker re-extraction for pages that need it.
        """
        start = time.time()
        if progress_callback:
            progress_callback("available", 50, "Pass 3: LLM cleanup + assessment")

        loop = asyncio.new_event_loop()
        try:
            assessments = loop.run_until_complete(
                self._run_llm_assessments(ctx, progress_callback)
            )
        finally:
            loop.close()

        # Apply LLM cleaned text and collect pages flagged for vision re-extraction
        # (needs_marker and needs_vision both route to the vision model now)
        vision_flagged_pages: List[int] = []
        pages_changed = 0

        for i, assessment in enumerate(assessments):
            if i >= len(ctx.page_texts):
                break

            needs_reextract = assessment.needs_marker or assessment.needs_vision

            if assessment.changed:
                ctx.page_texts[i] = PageText(
                    page_number=i + 1,
                    text=assessment.cleaned_text,
                    text_hash=PageText.compute_hash(assessment.cleaned_text),
                    source_pass=3,
                    needs_marker=needs_reextract,
                    marker_reason=assessment.reason if needs_reextract else "",
                )
                pages_changed += 1
            elif needs_reextract:
                ctx.page_texts[i].needs_marker = True
                ctx.page_texts[i].marker_reason = assessment.reason

            if needs_reextract:
                vision_flagged_pages.append(i + 1)  # 1-based

        # Vision-based extraction for pages flagged by LLM assessment
        # Replaces the legacy Marker path — uses the same VisionExtractor as Pass 2
        vision_improved = 0

        if vision_flagged_pages and self.vision_extractor:
            if progress_callback:
                progress_callback(
                    "available", 65,
                    f"Pass 3: Vision extraction on {len(vision_flagged_pages)} flagged pages",
                )
            vision_improved = self._run_selective_vision(ctx, vision_flagged_pages, progress_callback)
            pages_changed += vision_improved
        elif vision_flagged_pages:
            logger.info(
                "Skipping vision extraction (no vision extractor configured)",
                file_id=ctx.file_id,
                flagged_pages=len(vision_flagged_pages),
            )

        combined = self._combine_page_texts(ctx.page_texts)

        ctx.pass_metadata["pass3"] = {
            "pages_llm_changed": pages_changed - vision_improved,
            "vision_pages_requested": len(vision_flagged_pages),
            "vision_pages_improved": vision_improved,
            "page_hashes": {pt.page_number: pt.text_hash for pt in ctx.page_texts},
        }

        duration = time.time() - start
        logger.info(
            "Pass 3 complete",
            file_id=ctx.file_id,
            pages_changed=pages_changed,
            marker_pages=len(marker_pages),
            marker_improved=marker_improved,
            duration=f"{duration:.1f}s",
        )

        return PassResult(
            pass_number=3,
            page_texts=ctx.page_texts,
            pages_changed=pages_changed,
            pages_skipped=ctx.page_count - pages_changed,
            combined_text=combined,
            duration_seconds=duration,
        )

    # =========================================================================
    # Chunk + Embed helpers (called by the worker after each pass)
    # =========================================================================

    def chunk_text(self, combined_text: str, page_texts: List[PageText]) -> List[Chunk]:
        """Chunk combined text, preserving page number metadata."""
        if not combined_text.strip():
            return []

        chunks = self.chunker.chunk(combined_text)

        # Assign page numbers based on character offset mapping
        page_boundaries = self._build_page_boundaries(page_texts)
        for chunk in chunks:
            if chunk.char_offset is not None:
                chunk.page_number = self._page_for_offset(
                    chunk.char_offset, page_boundaries
                )

        return chunks

    def generate_markdown(
        self,
        combined_text: str,
        extraction_method: str = "simple",
        images: Optional[List[dict]] = None,
    ) -> Tuple[str, dict]:
        """Generate markdown from combined page text, optionally inserting images."""
        return self.markdown_generator.generate(
            combined_text,
            extraction_method=extraction_method,
            images=images,
        )

    def upload_markdown(
        self,
        file_service: Any,
        file_id: str,
        storage_path: str,
        user_id: str,
        markdown_content: str,
    ) -> Optional[str]:
        """Upload markdown to MinIO, returns the markdown_path or None."""
        try:
            path_parts = storage_path.rsplit("/", 2)
            if len(path_parts) >= 2:
                base_path = path_parts[0] + "/" + file_id
            else:
                base_path = f"{user_id}/{file_id}"
            markdown_path = f"{base_path}/content.md"

            md_bytes = markdown_content.encode("utf-8")
            file_service.client.put_object(
                bucket_name=file_service.bucket,
                object_name=markdown_path,
                data=io.BytesIO(md_bytes),
                length=len(md_bytes),
                content_type="text/markdown",
            )

            logger.info(
                "Markdown uploaded",
                file_id=file_id,
                path=markdown_path,
                size=len(md_bytes),
            )
            return markdown_path

        except Exception as e:
            logger.error(
                "Markdown upload failed",
                file_id=file_id,
                error=str(e),
            )
            return None

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def replace_image_placeholders(
        self,
        ctx: ProgressiveContext,
        image_refs: List[dict],
    ) -> None:
        """
        Replace pymupdf4llm image placeholders with actual markdown image refs.

        pymupdf4llm emits lines like:
            **==> picture [810 x 202] intentionally omitted <==**
        or multi-line variants.  We match them against image_refs (which have
        page numbers) and substitute ``![Image N](images/image_N.png)``.

        This updates ctx.page_texts AND ctx.pass1_texts in place so the LLM
        cleanup sees proper image references instead of raw placeholders.
        """
        if not image_refs:
            return

        # Group image refs by 1-based page number
        # image_refs already use 1-based page numbers from the image extractor
        refs_by_page: Dict[int, List[dict]] = {}
        for ref in image_refs:
            pg = ref.get("page")
            if pg is not None:
                refs_by_page.setdefault(pg, []).append(ref)

        # Pattern matches single-line and multi-line pymupdf4llm placeholders:
        #   **==> picture [W x H] intentionally omitted <==**
        #   ==> picture [W x H] intentionally omitted <==
        #   **==> picture\n\n[W x H] intentionally omitted <==**
        # Also catches orphaned tail fragments:
        #   [W x H] intentionally omitted <==**
        placeholder_re = re.compile(
            r'\*{0,2}==>[ \t]*picture\s*'
            r'(?:\[[\d]+\s*x\s*[\d]+\]\s*)?'
            r'intentionally\s+omitted\s*<==\*{0,2}',
            re.IGNORECASE | re.DOTALL,
        )
        # Orphaned tails (the ==> picture was on a previous line that got split)
        orphan_re = re.compile(
            r'^\s*\[[\d]+\s*x\s*[\d]+\]\s*intentionally\s+omitted\s*<==\*{0,2}\s*$',
            re.IGNORECASE | re.MULTILINE,
        )

        total_replaced = 0

        for i, pt in enumerate(ctx.page_texts):
            page_num = pt.page_number  # 1-based
            page_refs = refs_by_page.get(page_num, [])
            if not page_refs:
                # No images for this page -- just strip any remaining placeholders
                cleaned = placeholder_re.sub("", pt.text)
                cleaned = orphan_re.sub("", cleaned).strip()
                ctx.page_texts[i] = PageText(
                    page_number=pt.page_number,
                    text=cleaned,
                    text_hash=PageText.compute_hash(cleaned),
                    source_pass=pt.source_pass,
                )
                if i < len(ctx.pass1_texts):
                    p1 = placeholder_re.sub("", ctx.pass1_texts[i])
                    ctx.pass1_texts[i] = orphan_re.sub("", p1).strip()
                continue

            ref_iter = iter(page_refs)

            def _replace(match: re.Match) -> str:
                nonlocal total_replaced
                ref = next(ref_iter, None)
                if ref:
                    total_replaced += 1
                    path = ref.get("path", "images/image_0.png")
                    caption = ref.get("caption", "Image")
                    return f"![{caption}]({path})"
                return ""  # no more refs for this page, remove placeholder

            new_text = placeholder_re.sub(_replace, pt.text)
            new_text = orphan_re.sub("", new_text)

            ctx.page_texts[i] = PageText(
                page_number=pt.page_number,
                text=new_text,
                text_hash=PageText.compute_hash(new_text),
                source_pass=pt.source_pass,
            )

            # Also update pass1_texts so LLM dual-text comparison has image refs
            if i < len(ctx.pass1_texts):
                ref_iter2 = iter(page_refs)

                def _replace2(match: re.Match) -> str:
                    ref = next(ref_iter2, None)
                    if ref:
                        path = ref.get("path", "images/image_0.png")
                        caption = ref.get("caption", "Image")
                        return f"![{caption}]({path})"
                    return ""

                p1 = placeholder_re.sub(_replace2, ctx.pass1_texts[i])
                ctx.pass1_texts[i] = orphan_re.sub("", p1)

        logger.info(
            "Image placeholders replaced",
            file_id=ctx.file_id,
            total_replaced=total_replaced,
            total_image_refs=len(image_refs),
        )

    @staticmethod
    def post_cleanup_sanitize(text: str) -> str:
        """
        Safety-net pass that removes residual artefacts the LLM may have missed:
        - Unicode garbage clusters (3+ consecutive non-ASCII-letter chars)
        - Leftover pymupdf4llm picture placeholders
        - Empty picture-text blocks
        - Duplicate horizontal rules
        """
        # Remove leftover picture placeholders (single and multi-line)
        text = re.sub(
            r'\*{0,2}==>[ \t]*picture\s*'
            r'(?:\[[\d]+\s*x\s*[\d]+\]\s*)?'
            r'intentionally\s+omitted\s*<==\*{0,2}',
            '', text, flags=re.IGNORECASE | re.DOTALL,
        )
        # Remove orphaned placeholder tails
        text = re.sub(
            r'^\s*\[[\d]+\s*x\s*[\d]+\]\s*intentionally\s+omitted\s*<==\*{0,2}\s*$',
            '', text, flags=re.IGNORECASE | re.MULTILINE,
        )

        # Remove picture-text blocks that contain mostly garbled text
        def _clean_picture_text(m: re.Match) -> str:
            inner = m.group(1).strip()
            # If >40% of chars are non-ASCII, it's garbled -- drop the block
            if inner:
                non_ascii = sum(1 for c in inner if ord(c) > 127)
                if non_ascii / len(inner) > 0.4:
                    return ""
            # Otherwise keep the inner text without the markers
            return inner if inner else ""

        text = re.sub(
            r'\*{0,2}-{3,}\s*Start of picture text\s*-{3,}\*{0,2}'
            r'(.*?)'
            r'\*{0,2}-{3,}\s*End of picture text\s*-{3,}\*{0,2}',
            _clean_picture_text,
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove lines that are predominantly garbled unicode
        # A line is "garbled" if >50% of its letter characters are non-ASCII
        cleaned_lines = []
        for line in text.split('\n'):
            stripped = line.strip()
            if not stripped:
                cleaned_lines.append(line)
                continue
            # Preserve markdown image refs and horizontal rules
            if stripped.startswith('![') or stripped == '---':
                cleaned_lines.append(line)
                continue
            letters = [c for c in stripped if c.isalpha()]
            if letters:
                non_ascii_letters = sum(1 for c in letters if ord(c) > 127)
                if non_ascii_letters / len(letters) > 0.5:
                    continue  # skip garbled line
            cleaned_lines.append(line)

        text = '\n'.join(cleaned_lines)

        # Collapse multiple blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    PAGE_MARKER_TEMPLATE = "<!-- page:{} -->"

    def _combine_page_texts(self, page_texts: List[PageText]) -> str:
        """Combine per-page text into a single document string with page markers."""
        parts = []
        for pt in page_texts:
            if pt.text.strip():
                marker = self.PAGE_MARKER_TEMPLATE.format(pt.page_number)
                parts.append(f"{marker}\n{pt.text}")
        return "\n\n---\n\n".join(parts)

    def _is_meaningful_improvement(self, old_text: str, new_text: str) -> bool:
        """Check if new text is a meaningful improvement over old text."""
        old_len = len(old_text.strip())
        new_len = len(new_text.strip())

        if old_len == 0 and new_len > 0:
            return True
        if new_len == 0:
            return False

        old_hash = PageText.compute_hash(old_text)
        new_hash = PageText.compute_hash(new_text)
        if old_hash == new_hash:
            return False

        # Check if there's a meaningful length change
        if old_len > 0:
            change_ratio = abs(new_len - old_len) / old_len
            if change_ratio < MIN_TEXT_CHANGE_RATIO:
                # Very small change -- check character-level difference
                common = sum(1 for a, b in zip(old_text, new_text) if a == b)
                similarity = common / max(old_len, new_len)
                if similarity > 0.95:
                    return False

        # New text is longer (OCR found more text) or significantly different
        return True

    async def _run_llm_assessments(
        self,
        ctx: ProgressiveContext,
        progress_callback: Optional[Callable] = None,
    ) -> List[CleanupAssessment]:
        """Run LLM cleanup + assessment on all pages concurrently."""
        import asyncio

        max_concurrent = int(os.getenv("LLM_CLEANUP_BATCH_SIZE", "3"))
        semaphore = asyncio.Semaphore(max_concurrent)

        async def assess_page(i: int) -> CleanupAssessment:
            async with semaphore:
                pt = ctx.page_texts[i]
                layout_text = ctx.pass1_texts[i] if i < len(ctx.pass1_texts) else None
                result = await self.llm_cleanup.cleanup_page_with_assessment(
                    pt.text, page_number=pt.page_number,
                    layout_text=layout_text,
                )
                if progress_callback and ctx.page_count > 0:
                    pct = 50 + int(15 * (i + 1) / ctx.page_count)
                    progress_callback(
                        "available", pct,
                        f"Pass 3: Assessed page {i+1}/{ctx.page_count}",
                    )
                return result

        tasks = [assess_page(i) for i in range(len(ctx.page_texts))]
        return await asyncio.gather(*tasks)

    def _run_selective_vision(
        self,
        ctx: ProgressiveContext,
        flagged_pages: List[int],
        progress_callback: Optional[Callable] = None,
    ) -> int:
        """Run VisionExtractor on pages flagged by LLM assessment.
        
        Picks the best vision mode based on the marker_reason hint left by
        the LLM assessment (table, formula, OCR artifacts → "table"; default → "ocr").
        """
        improved = 0
        loop = asyncio.new_event_loop()

        try:
            for idx, page_num in enumerate(flagged_pages):
                try:
                    pt = ctx.page_texts[page_num - 1]
                    reason = (pt.marker_reason or "").lower()

                    if "table" in reason or "column" in reason:
                        mode = "table"
                    elif "formula" in reason or "equation" in reason:
                        mode = "ocr"
                    else:
                        mode = "ocr"

                    result = loop.run_until_complete(
                        self.vision_extractor.analyse_page(
                            ctx.file_path, page_num, mode=mode,
                            existing_text=pt.text,
                        )
                    )

                    if result.success and result.text and self._is_meaningful_improvement(
                        pt.text, result.text
                    ):
                        ctx.page_texts[page_num - 1] = PageText(
                            page_number=page_num,
                            text=result.text,
                            text_hash=PageText.compute_hash(result.text),
                            source_pass=3,
                            needs_marker=False,
                            marker_reason=f"Vision ({mode}) applied",
                        )
                        improved += 1

                    if progress_callback:
                        pct = 65 + int(10 * (idx + 1) / len(flagged_pages))
                        progress_callback(
                            "available", pct,
                            f"Pass 3: Vision page {idx+1}/{len(flagged_pages)}",
                        )

                except Exception as e:
                    logger.warning(
                        "Vision extraction failed for page",
                        file_id=ctx.file_id,
                        page=page_num,
                        error=str(e),
                    )
        finally:
            loop.close()

        return improved

    def _build_page_boundaries(
        self, page_texts: List[PageText]
    ) -> List[Tuple[int, int, int]]:
        """Build (start_offset, end_offset, page_number) tuples.

        Accounts for the ``<!-- page:N -->\\n`` marker prepended to each page
        in ``_combine_page_texts`` so chunk char_offset maps correctly.
        """
        boundaries = []
        offset = 0
        separator_len = len("\n\n---\n\n")

        for i, pt in enumerate(page_texts):
            if not pt.text.strip():
                continue
            marker_len = len(self.PAGE_MARKER_TEMPLATE.format(pt.page_number)) + 1  # +1 for \n
            text_len = len(pt.text)
            text_start = offset + marker_len
            boundaries.append((text_start, text_start + text_len, pt.page_number))
            offset += marker_len + text_len + separator_len

        return boundaries

    def _page_for_offset(
        self, char_offset: int, boundaries: List[Tuple[int, int, int]]
    ) -> Optional[int]:
        """Find which page a character offset belongs to."""
        for start, end, page_num in boundaries:
            if start <= char_offset < end:
                return page_num
        if boundaries:
            return boundaries[-1][2]
        return None
