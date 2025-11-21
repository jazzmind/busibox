"""
Multi-Flow Document Processor

Processes documents through multiple strategies in parallel to enable
comparison and optimization of extraction methods.

Each document is processed with:
1. SIMPLE - Fast baseline extraction
2. MARKER - Enhanced PDF processing (if enabled and PDF)
3. COLPALI - Visual embeddings (if enabled and PDF/image)

All results are stored separately with strategy tags for comparison.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from pathlib import Path

import structlog

from processors.processing_strategy import (
    ProcessingStrategy,
    ProcessingResult,
    StrategySelector,
    compare_strategy_results,
)
from processors.text_extractor import TextExtractor, ExtractionResult
from processors.chunker import Chunker, Chunk
from processors.embedder import Embedder
from processors.colpali import ColPaliEmbedder
from processors.classifier import DocumentClassifier

logger = structlog.get_logger()


class MultiFlowProcessor:
    """
    Process documents through multiple strategies in parallel.
    
    This enables comparison of different extraction methods on the same document
    to determine which strategy works best for different document types.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize multi-flow processor.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.strategy_selector = StrategySelector(config)
        
        # Initialize processors
        self.text_extractor = TextExtractor(config)
        self.chunker = Chunker(config)
        self.embedder = Embedder(config)
        self.colpali = ColPaliEmbedder(config)
        self.classifier = DocumentClassifier(config)
        
        # Thread pool for parallel processing
        self.max_workers = config.get("max_parallel_strategies", 3)
        
        logger.info(
            "MultiFlowProcessor initialized",
            max_workers=self.max_workers,
            marker_enabled=config.get("marker_enabled", False),
            colpali_enabled=config.get("colpali_enabled", True),
        )
    
    async def process_document(
        self,
        file_path: str,
        mime_type: str,
        file_id: str,
        original_filename: str,
    ) -> Dict[str, ProcessingResult]:
        """
        Process document through all applicable strategies.
        
        Args:
            file_path: Path to the document file
            mime_type: MIME type of the document
            file_id: Unique file identifier
            original_filename: Original filename
        
        Returns:
            Dict mapping strategy name to ProcessingResult
        """
        logger.info(
            "Starting multi-flow processing",
            file_id=file_id,
            mime_type=mime_type,
            filename=original_filename,
        )
        
        # Get applicable strategies
        strategies = self.strategy_selector.get_applicable_strategies(mime_type)
        
        if not strategies:
            logger.warning(
                "No applicable strategies for document",
                file_id=file_id,
                mime_type=mime_type,
            )
            return {}
        
        logger.info(
            "Running strategies in parallel",
            file_id=file_id,
            strategies=[s.value for s in strategies],
        )
        
        # Process strategies in parallel
        results = {}
        
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all strategy tasks
            future_to_strategy = {
                executor.submit(
                    self._process_with_strategy,
                    strategy,
                    file_path,
                    mime_type,
                    file_id,
                    original_filename,
                ): strategy
                for strategy in strategies
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_strategy):
                strategy = future_to_strategy[future]
                try:
                    result = future.result()
                    results[strategy.value] = result
                    logger.info(
                        "Strategy completed",
                        file_id=file_id,
                        strategy=strategy.value,
                        success=result.success,
                        processing_time=result.processing_time_seconds,
                    )
                except Exception as e:
                    logger.error(
                        "Strategy failed",
                        file_id=file_id,
                        strategy=strategy.value,
                        error=str(e),
                        exc_info=True,
                    )
                    results[strategy.value] = ProcessingResult(
                        strategy=strategy,
                        success=False,
                        error=str(e),
                    )
        
        # Generate comparison
        comparison = compare_strategy_results(list(results.values()))
        logger.info(
            "Multi-flow processing complete",
            file_id=file_id,
            strategies_completed=len(results),
            comparison=comparison,
        )
        
        return results
    
    def _process_with_strategy(
        self,
        strategy: ProcessingStrategy,
        file_path: str,
        mime_type: str,
        file_id: str,
        original_filename: str,
    ) -> ProcessingResult:
        """
        Process document with a specific strategy.
        
        Args:
            strategy: Processing strategy to use
            file_path: Path to the document
            mime_type: MIME type
            file_id: File identifier
            original_filename: Original filename
        
        Returns:
            ProcessingResult with extracted data
        """
        start_time = time.time()
        
        try:
            logger.info(
                "Processing with strategy",
                file_id=file_id,
                strategy=strategy.value,
            )
            
            if strategy == ProcessingStrategy.SIMPLE:
                return self._process_simple(
                    file_path,
                    mime_type,
                    file_id,
                    original_filename,
                    start_time,
                )
            
            elif strategy == ProcessingStrategy.MARKER:
                return self._process_marker(
                    file_path,
                    mime_type,
                    file_id,
                    original_filename,
                    start_time,
                )
            
            elif strategy == ProcessingStrategy.COLPALI:
                return self._process_colpali(
                    file_path,
                    mime_type,
                    file_id,
                    original_filename,
                    start_time,
                )
            
            else:
                raise ValueError(f"Unknown strategy: {strategy}")
        
        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(
                "Strategy processing failed",
                file_id=file_id,
                strategy=strategy.value,
                error=str(e),
                exc_info=True,
            )
            return ProcessingResult(
                strategy=strategy,
                success=False,
                error=str(e),
                processing_time_seconds=processing_time,
            )
    
    def _process_simple(
        self,
        file_path: str,
        mime_type: str,
        file_id: str,
        original_filename: str,
        start_time: float,
    ) -> ProcessingResult:
        """Process with SIMPLE strategy (basic extraction)."""
        
        # Temporarily disable Marker for simple extraction
        original_marker_setting = self.text_extractor.marker_enabled
        self.text_extractor.marker_enabled = False
        
        try:
            # Extract text (basic method)
            extraction = self.text_extractor.extract(file_path, mime_type)
            
            # Classify and detect language
            document_type, confidence = self.classifier.classify(
                extraction.text,
                original_filename,
                mime_type,
            )
            primary_language, detected_languages = self.classifier.detect_languages(
                extraction.text,
            )
            
            # Chunk text
            chunks = self.chunker.chunk(
                extraction.text,
                page_number=None,
                detected_languages=detected_languages,
            )
            
            # Generate embeddings
            chunk_texts = [c.text for c in chunks]
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                embeddings = loop.run_until_complete(
                    self.embedder.embed_chunks(chunk_texts)
                )
            finally:
                loop.close()
            
            processing_time = time.time() - start_time
            
            return ProcessingResult(
                strategy=ProcessingStrategy.SIMPLE,
                success=True,
                text=extraction.text,
                markdown=extraction.markdown,
                page_images=extraction.page_images,
                page_count=extraction.page_count,
                tables=extraction.tables,
                metadata={
                    "document_type": document_type,
                    "confidence": confidence,
                    "primary_language": primary_language,
                    "detected_languages": detected_languages,
                    "chunk_count": len(chunks),
                },
                embeddings=embeddings,
                processing_time_seconds=processing_time,
            )
        
        finally:
            # Restore original marker setting
            self.text_extractor.marker_enabled = original_marker_setting
    
    def _process_marker(
        self,
        file_path: str,
        mime_type: str,
        file_id: str,
        original_filename: str,
        start_time: float,
    ) -> ProcessingResult:
        """Process with MARKER strategy (enhanced PDF processing)."""
        
        if mime_type != "application/pdf":
            return ProcessingResult(
                strategy=ProcessingStrategy.MARKER,
                success=False,
                error="Marker only supports PDF files",
                processing_time_seconds=time.time() - start_time,
            )
        
        # Temporarily enable Marker
        original_marker_setting = self.text_extractor.marker_enabled
        self.text_extractor.marker_enabled = True
        
        try:
            # Extract with Marker
            extraction = self.text_extractor.extract(file_path, mime_type)
            
            # Classify and detect language
            document_type, confidence = self.classifier.classify(
                extraction.text,
                original_filename,
                mime_type,
            )
            primary_language, detected_languages = self.classifier.detect_languages(
                extraction.text,
            )
            
            # Chunk text
            chunks = self.chunker.chunk(
                extraction.text,
                page_number=None,
                detected_languages=detected_languages,
            )
            
            # Generate embeddings
            chunk_texts = [c.text for c in chunks]
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                embeddings = loop.run_until_complete(
                    self.embedder.embed_chunks(chunk_texts)
                )
            finally:
                loop.close()
            
            processing_time = time.time() - start_time
            
            return ProcessingResult(
                strategy=ProcessingStrategy.MARKER,
                success=True,
                text=extraction.text,
                markdown=extraction.markdown,
                page_images=extraction.page_images,
                page_count=extraction.page_count,
                tables=extraction.tables,
                metadata={
                    "document_type": document_type,
                    "confidence": confidence,
                    "primary_language": primary_language,
                    "detected_languages": detected_languages,
                    "chunk_count": len(chunks),
                },
                embeddings=embeddings,
                processing_time_seconds=processing_time,
            )
        
        finally:
            # Restore original marker setting
            self.text_extractor.marker_enabled = original_marker_setting
    
    def _process_colpali(
        self,
        file_path: str,
        mime_type: str,
        file_id: str,
        original_filename: str,
        start_time: float,
    ) -> ProcessingResult:
        """Process with COLPALI strategy (visual embeddings)."""
        
        # ColPali only works with PDFs and images
        supported_types = [
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/tiff",
        ]
        
        if mime_type not in supported_types:
            return ProcessingResult(
                strategy=ProcessingStrategy.COLPALI,
                success=False,
                error=f"ColPali only supports PDFs and images, got {mime_type}",
                processing_time_seconds=time.time() - start_time,
            )
        
        # Extract page images (needed for ColPali)
        extraction = self.text_extractor.extract(file_path, mime_type)
        
        if not extraction.page_images:
            return ProcessingResult(
                strategy=ProcessingStrategy.COLPALI,
                success=False,
                error="No page images extracted for ColPali processing",
                processing_time_seconds=time.time() - start_time,
            )
        
        # Generate ColPali visual embeddings
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            visual_embeddings = loop.run_until_complete(
                self.colpali.embed_pages(extraction.page_images)
            )
        finally:
            loop.close()
        
        if not visual_embeddings:
            return ProcessingResult(
                strategy=ProcessingStrategy.COLPALI,
                success=False,
                error="ColPali failed to generate visual embeddings",
                processing_time_seconds=time.time() - start_time,
            )
        
        processing_time = time.time() - start_time
        
        # Also extract basic text for metadata
        document_type, confidence = self.classifier.classify(
            extraction.text,
            original_filename,
            mime_type,
        )
        primary_language, detected_languages = self.classifier.detect_languages(
            extraction.text,
        )
        
        return ProcessingResult(
            strategy=ProcessingStrategy.COLPALI,
            success=True,
            text=extraction.text,  # Basic text for reference
            page_images=extraction.page_images,
            page_count=extraction.page_count,
            metadata={
                "document_type": document_type,
                "confidence": confidence,
                "primary_language": primary_language,
                "detected_languages": detected_languages,
                "visual_embedding_count": len(visual_embeddings),
                "embedding_dimension": len(visual_embeddings[0]) if visual_embeddings else 0,
            },
            visual_embeddings=visual_embeddings,
            processing_time_seconds=processing_time,
        )
    
    def get_best_strategy(
        self,
        results: Dict[str, ProcessingResult],
        optimization_goal: str = "balanced",
    ) -> Optional[str]:
        """
        Determine the best strategy based on results.
        
        Args:
            results: Dict of strategy name to ProcessingResult
            optimization_goal: "speed", "quality", or "balanced"
        
        Returns:
            Name of the best strategy, or None if no successful results
        """
        successful_results = {
            name: result
            for name, result in results.items()
            if result.success
        }
        
        if not successful_results:
            return None
        
        if optimization_goal == "speed":
            # Return fastest strategy
            return min(
                successful_results.items(),
                key=lambda x: x[1].processing_time_seconds
            )[0]
        
        elif optimization_goal == "quality":
            # Return strategy with most text extracted
            return max(
                successful_results.items(),
                key=lambda x: len(x[1].text) if x[1].text else 0
            )[0]
        
        else:  # balanced
            # Score based on speed and quality
            scores = {}
            max_time = max(r.processing_time_seconds for r in successful_results.values())
            max_text = max(len(r.text) if r.text else 0 for r in successful_results.values())
            
            for name, result in successful_results.items():
                # Normalize metrics (lower is better for time, higher is better for text)
                time_score = 1 - (result.processing_time_seconds / max_time if max_time > 0 else 0)
                text_score = (len(result.text) if result.text else 0) / max_text if max_text > 0 else 0
                
                # Combined score (50% speed, 50% quality)
                scores[name] = (time_score * 0.5) + (text_score * 0.5)
            
            return max(scores.items(), key=lambda x: x[1])[0]

