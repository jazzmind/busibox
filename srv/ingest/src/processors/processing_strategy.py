"""
Processing Strategy Framework

Defines different document processing strategies that can be run in parallel
to compare effectiveness across different document types.

Strategies:
1. SIMPLE - Basic text extraction (fast, reliable fallback)
2. MARKER - Enhanced PDF processing with Marker (better structure/tables)
3. COLPALI - Visual embeddings for PDFs/images (semantic visual search)

Each document can be processed with multiple strategies to enable
comparison and optimization.
"""

from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import structlog

logger = structlog.get_logger()


class ProcessingStrategy(str, Enum):
    """Document processing strategies."""
    
    SIMPLE = "simple"      # Basic text extraction (pypdf, python-docx, etc.)
    MARKER = "marker"      # Enhanced Marker-based extraction (PDF only)
    COLPALI = "colpali"    # Visual embeddings (PDF/images only)


@dataclass
class StrategyConfig:
    """Configuration for a processing strategy."""
    
    strategy: ProcessingStrategy
    enabled: bool
    description: str
    supported_mimetypes: List[str]
    requires_gpu: bool = False
    average_speed: str = "fast"  # fast, medium, slow
    best_for: List[str] = None  # Document types this excels at
    
    def __post_init__(self):
        if self.best_for is None:
            self.best_for = []


# Strategy configurations
STRATEGY_CONFIGS = {
    ProcessingStrategy.SIMPLE: StrategyConfig(
        strategy=ProcessingStrategy.SIMPLE,
        enabled=True,  # Always enabled as fallback
        description="Basic text extraction using standard libraries",
        supported_mimetypes=[
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/html",
            "text/markdown",
            "text/csv",
            "application/json",
        ],
        requires_gpu=False,
        average_speed="fast",
        best_for=[
            "Simple text documents",
            "Well-formatted PDFs",
            "Text files",
            "Markdown",
        ],
    ),
    
    ProcessingStrategy.MARKER: StrategyConfig(
        strategy=ProcessingStrategy.MARKER,
        enabled=True,  # Can be disabled to save memory
        description="Enhanced PDF processing with Marker (better tables, structure, formulas)",
        supported_mimetypes=[
            "application/pdf",
        ],
        requires_gpu=False,
        average_speed="slow",
        best_for=[
            "Complex PDFs",
            "Scientific papers",
            "Documents with tables",
            "Documents with formulas",
            "Scanned documents",
        ],
    ),
    
    ProcessingStrategy.COLPALI: StrategyConfig(
        strategy=ProcessingStrategy.COLPALI,
        enabled=True,  # Requires ColPali service
        description="Visual embeddings for semantic image search",
        supported_mimetypes=[
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/tiff",
        ],
        requires_gpu=True,
        average_speed="medium",
        best_for=[
            "Visual documents",
            "Infographics",
            "Charts and diagrams",
            "Scanned documents",
            "Mixed content (text + images)",
        ],
    ),
}


class StrategySelector:
    """Select applicable processing strategies for a document."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize strategy selector.
        
        Args:
            config: Configuration dict with strategy enable/disable flags
        """
        self.config = config
        
        # Check which strategies are enabled
        self.simple_enabled = True  # Always enabled
        self.marker_enabled = config.get("marker_enabled", False)
        self.colpali_enabled = config.get("colpali_enabled", True)
        
        logger.info(
            "Strategy selector initialized",
            simple_enabled=self.simple_enabled,
            marker_enabled=self.marker_enabled,
            colpali_enabled=self.colpali_enabled,
        )
    
    def get_applicable_strategies(
        self,
        mime_type: str,
        force_all: bool = False,
    ) -> List[ProcessingStrategy]:
        """
        Get list of applicable strategies for a document.
        
        Args:
            mime_type: MIME type of the document
            force_all: If True, return all supported strategies regardless of config
        
        Returns:
            List of applicable ProcessingStrategy values
        """
        strategies = []
        
        for strategy, config in STRATEGY_CONFIGS.items():
            # Check if mimetype is supported
            if mime_type not in config.supported_mimetypes:
                continue
            
            # Check if strategy is enabled
            if not force_all:
                if strategy == ProcessingStrategy.SIMPLE and not self.simple_enabled:
                    continue
                if strategy == ProcessingStrategy.MARKER and not self.marker_enabled:
                    continue
                if strategy == ProcessingStrategy.COLPALI and not self.colpali_enabled:
                    continue
            
            strategies.append(strategy)
        
        # Ensure SIMPLE is always first (as baseline)
        if ProcessingStrategy.SIMPLE in strategies:
            strategies.remove(ProcessingStrategy.SIMPLE)
            strategies.insert(0, ProcessingStrategy.SIMPLE)
        
        logger.debug(
            "Selected strategies for document",
            mime_type=mime_type,
            strategies=[s.value for s in strategies],
            force_all=force_all,
        )
        
        return strategies
    
    def get_strategy_config(self, strategy: ProcessingStrategy) -> StrategyConfig:
        """Get configuration for a strategy."""
        return STRATEGY_CONFIGS[strategy]
    
    def is_strategy_supported(
        self,
        strategy: ProcessingStrategy,
        mime_type: str,
    ) -> bool:
        """Check if a strategy supports a given MIME type."""
        config = STRATEGY_CONFIGS.get(strategy)
        if not config:
            return False
        
        return mime_type in config.supported_mimetypes


@dataclass
class ProcessingResult:
    """Result from a processing strategy."""
    
    strategy: ProcessingStrategy
    success: bool
    text: Optional[str] = None
    markdown: Optional[str] = None
    page_images: Optional[List[str]] = None
    page_count: int = 0
    tables: Optional[List[Dict]] = None
    metadata: Optional[Dict] = None
    embeddings: Optional[List[List[float]]] = None  # For text chunks
    visual_embeddings: Optional[List[List[List[float]]]] = None  # For ColPali
    error: Optional[str] = None
    processing_time_seconds: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "strategy": self.strategy.value,
            "success": self.success,
            "text_length": len(self.text) if self.text else 0,
            "has_markdown": bool(self.markdown),
            "page_count": self.page_count,
            "table_count": len(self.tables) if self.tables else 0,
            "has_embeddings": bool(self.embeddings),
            "has_visual_embeddings": bool(self.visual_embeddings),
            "embedding_count": len(self.embeddings) if self.embeddings else 0,
            "visual_embedding_count": len(self.visual_embeddings) if self.visual_embeddings else 0,
            "error": self.error,
            "processing_time_seconds": self.processing_time_seconds,
        }


def get_strategy_summary() -> Dict[str, Any]:
    """Get a summary of all available strategies."""
    return {
        "strategies": {
            strategy.value: {
                "description": config.description,
                "supported_mimetypes": config.supported_mimetypes,
                "requires_gpu": config.requires_gpu,
                "average_speed": config.average_speed,
                "best_for": config.best_for,
            }
            for strategy, config in STRATEGY_CONFIGS.items()
        }
    }


def compare_strategy_results(
    results: List[ProcessingResult],
) -> Dict[str, Any]:
    """
    Compare results from different strategies.
    
    Args:
        results: List of ProcessingResult objects
    
    Returns:
        Comparison dict with metrics and recommendations
    """
    if not results:
        return {"error": "No results to compare"}
    
    comparison = {
        "strategies_compared": len(results),
        "results": {},
        "fastest": None,
        "most_text": None,
        "most_chunks": None,
        "recommendations": [],
    }
    
    fastest_time = float('inf')
    most_text_length = 0
    most_chunks = 0
    
    for result in results:
        strategy_name = result.strategy.value
        
        # Track metrics
        if result.success:
            if result.processing_time_seconds < fastest_time:
                fastest_time = result.processing_time_seconds
                comparison["fastest"] = strategy_name
            
            text_length = len(result.text) if result.text else 0
            if text_length > most_text_length:
                most_text_length = text_length
                comparison["most_text"] = strategy_name
            
            chunk_count = len(result.embeddings) if result.embeddings else 0
            if chunk_count > most_chunks:
                most_chunks = chunk_count
                comparison["most_chunks"] = strategy_name
        
        # Store result summary
        comparison["results"][strategy_name] = result.to_dict()
    
    # Generate recommendations
    if comparison["most_text"] and comparison["fastest"]:
        if comparison["most_text"] == comparison["fastest"]:
            comparison["recommendations"].append(
                f"{comparison['most_text']} is both fastest and extracted the most text"
            )
        else:
            comparison["recommendations"].append(
                f"Use {comparison['fastest']} for speed, {comparison['most_text']} for completeness"
            )
    
    # Check for ColPali
    has_colpali = any(r.strategy == ProcessingStrategy.COLPALI for r in results)
    if has_colpali:
        colpali_result = next(
            (r for r in results if r.strategy == ProcessingStrategy.COLPALI),
            None
        )
        if colpali_result and colpali_result.success and colpali_result.visual_embeddings:
            comparison["recommendations"].append(
                f"ColPali generated {len(colpali_result.visual_embeddings)} visual embeddings for semantic image search"
            )
    
    return comparison

