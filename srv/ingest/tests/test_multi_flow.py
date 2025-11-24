"""
Tests for Multi-Flow Document Processing

Tests the parallel processing of documents through multiple strategies
(SIMPLE, MARKER, COLPALI) and comparison capabilities.

Run with: pytest tests/test_multi_flow.py -v
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict

import pytest
from PIL import Image, ImageDraw

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from processors.processing_strategy import (
    ProcessingStrategy,
    StrategySelector,
    ProcessingResult,
    StrategyConfig,
    STRATEGY_CONFIGS,
    compare_strategy_results,
    get_strategy_summary,
)
from processors.multi_flow_processor import MultiFlowProcessor


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def test_config():
    """Create test configuration."""
    return {
        "temp_dir": "/tmp/ingest_test",
        "marker_enabled": True,
        "colpali_enabled": True,
        "colpali_base_url": os.getenv("COLPALI_BASE_URL", "http://10.96.200.208:9006/v1"),
        "colpali_api_key": "EMPTY",
        "litellm_base_url": os.getenv("LITELLM_BASE_URL", "http://10.96.200.30:4000"),
        "embedding_model": "bge-large-en-v1.5",
        "chunk_size_min": 400,
        "chunk_size_max": 800,
        "chunk_overlap_pct": 0.12,
        "max_parallel_strategies": 3,
    }


@pytest.fixture
def sample_pdf():
    """Create a simple test PDF."""
    # Note: This is a minimal PDF - in real tests, use actual PDFs
    pdf_content = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/Resources <<
/Font <<
/F1 <<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
>>
>>
/MediaBox [0 0 612 792]
/Contents 4 0 R
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Test PDF Content) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000317 00000 n 
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
410
%%EOF
"""
    
    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    temp_file.write(pdf_content)
    temp_file.close()
    
    yield temp_file.name
    
    # Cleanup
    os.unlink(temp_file.name)


@pytest.fixture
def sample_text_file():
    """Create a simple text file."""
    content = """# Test Document

This is a test document with multiple paragraphs.

## Section 1
This is the first section with some content.

## Section 2
This is the second section with more content.
"""
    
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix=".txt", delete=False)
    temp_file.write(content)
    temp_file.close()
    
    yield temp_file.name
    
    # Cleanup
    os.unlink(temp_file.name)


@pytest.fixture
def sample_image():
    """Create a test image."""
    img = Image.new('RGB', (800, 600), color='white')
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 750, 550], outline='black', width=3)
    draw.text((100, 100), "Test Image Content", fill='black')
    
    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(temp_file.name, "PNG")
    
    yield temp_file.name
    
    # Cleanup
    os.unlink(temp_file.name)


# ============================================================================
# Test 1: Processing Strategy Configuration
# ============================================================================

class TestProcessingStrategy:
    """Test processing strategy enums and configurations."""
    
    def test_strategy_enum_values(self):
        """Test that strategy enum has expected values."""
        assert ProcessingStrategy.SIMPLE == "simple"
        assert ProcessingStrategy.MARKER == "marker"
        assert ProcessingStrategy.COLPALI == "colpali"
        
        print("\n✓ Strategy enum values are correct")
    
    def test_strategy_configs_exist(self):
        """Test that all strategies have configurations."""
        assert ProcessingStrategy.SIMPLE in STRATEGY_CONFIGS
        assert ProcessingStrategy.MARKER in STRATEGY_CONFIGS
        assert ProcessingStrategy.COLPALI in STRATEGY_CONFIGS
        
        print("\n✓ All strategies have configurations")
    
    def test_simple_strategy_config(self):
        """Test SIMPLE strategy configuration."""
        config = STRATEGY_CONFIGS[ProcessingStrategy.SIMPLE]
        
        assert config.strategy == ProcessingStrategy.SIMPLE
        assert config.enabled is True  # Always enabled
        assert not config.requires_gpu
        assert "application/pdf" in config.supported_mimetypes
        assert "text/plain" in config.supported_mimetypes
        
        print(f"\n✓ SIMPLE strategy config valid")
        print(f"  Supported MIME types: {len(config.supported_mimetypes)}")
    
    def test_marker_strategy_config(self):
        """Test MARKER strategy configuration."""
        config = STRATEGY_CONFIGS[ProcessingStrategy.MARKER]
        
        assert config.strategy == ProcessingStrategy.MARKER
        assert not config.requires_gpu
        assert "application/pdf" in config.supported_mimetypes
        assert config.average_speed == "slow"
        
        print(f"\n✓ MARKER strategy config valid")
        print(f"  Best for: {config.best_for}")
    
    def test_colpali_strategy_config(self):
        """Test COLPALI strategy configuration."""
        config = STRATEGY_CONFIGS[ProcessingStrategy.COLPALI]
        
        assert config.strategy == ProcessingStrategy.COLPALI
        assert config.requires_gpu is True
        assert "application/pdf" in config.supported_mimetypes
        assert "image/png" in config.supported_mimetypes
        
        print(f"\n✓ COLPALI strategy config valid")
        print(f"  Requires GPU: {config.requires_gpu}")
    
    def test_strategy_summary(self):
        """Test getting strategy summary."""
        summary = get_strategy_summary()
        
        assert "strategies" in summary
        assert "simple" in summary["strategies"]
        assert "marker" in summary["strategies"]
        assert "colpali" in summary["strategies"]
        
        simple_info = summary["strategies"]["simple"]
        assert "description" in simple_info
        assert "supported_mimetypes" in simple_info
        assert "requires_gpu" in simple_info
        
        print("\n✓ Strategy summary generated successfully")


# ============================================================================
# Test 2: Strategy Selector
# ============================================================================

class TestStrategySelector:
    """Test strategy selection logic."""
    
    def test_selector_initialization(self, test_config):
        """Test strategy selector initialization."""
        selector = StrategySelector(test_config)
        
        assert selector.simple_enabled is True
        assert selector.marker_enabled == test_config["marker_enabled"]
        assert selector.colpali_enabled == test_config["colpali_enabled"]
        
        print("\n✓ Strategy selector initialized correctly")
    
    def test_pdf_strategies(self, test_config):
        """Test strategies selected for PDF files."""
        selector = StrategySelector(test_config)
        strategies = selector.get_applicable_strategies("application/pdf")
        
        # PDF should support all three strategies
        assert ProcessingStrategy.SIMPLE in strategies
        assert ProcessingStrategy.MARKER in strategies
        assert ProcessingStrategy.COLPALI in strategies
        
        # SIMPLE should always be first
        assert strategies[0] == ProcessingStrategy.SIMPLE
        
        print(f"\n✓ PDF strategies: {[s.value for s in strategies]}")
    
    def test_text_strategies(self, test_config):
        """Test strategies selected for text files."""
        selector = StrategySelector(test_config)
        strategies = selector.get_applicable_strategies("text/plain")
        
        # Text files only support SIMPLE
        assert ProcessingStrategy.SIMPLE in strategies
        assert ProcessingStrategy.MARKER not in strategies
        assert ProcessingStrategy.COLPALI not in strategies
        
        print(f"\n✓ Text strategies: {[s.value for s in strategies]}")
    
    def test_image_strategies(self, test_config):
        """Test strategies selected for image files."""
        selector = StrategySelector(test_config)
        strategies = selector.get_applicable_strategies("image/png")
        
        # Images should support SIMPLE and COLPALI (not MARKER)
        assert ProcessingStrategy.SIMPLE in strategies
        assert ProcessingStrategy.COLPALI in strategies
        assert ProcessingStrategy.MARKER not in strategies
        
        print(f"\n✓ Image strategies: {[s.value for s in strategies]}")
    
    def test_disabled_marker(self, test_config):
        """Test strategy selection with Marker disabled."""
        config = test_config.copy()
        config["marker_enabled"] = False
        
        selector = StrategySelector(config)
        strategies = selector.get_applicable_strategies("application/pdf")
        
        assert ProcessingStrategy.SIMPLE in strategies
        assert ProcessingStrategy.MARKER not in strategies  # Disabled
        assert ProcessingStrategy.COLPALI in strategies
        
        print("\n✓ Marker correctly disabled")
    
    def test_disabled_colpali(self, test_config):
        """Test strategy selection with ColPali disabled."""
        config = test_config.copy()
        config["colpali_enabled"] = False
        
        selector = StrategySelector(config)
        strategies = selector.get_applicable_strategies("application/pdf")
        
        assert ProcessingStrategy.SIMPLE in strategies
        assert ProcessingStrategy.MARKER in strategies
        assert ProcessingStrategy.COLPALI not in strategies  # Disabled
        
        print("\n✓ ColPali correctly disabled")
    
    def test_strategy_support_check(self, test_config):
        """Test checking if strategy supports a MIME type."""
        selector = StrategySelector(test_config)
        
        # PDF
        assert selector.is_strategy_supported(ProcessingStrategy.SIMPLE, "application/pdf")
        assert selector.is_strategy_supported(ProcessingStrategy.MARKER, "application/pdf")
        assert selector.is_strategy_supported(ProcessingStrategy.COLPALI, "application/pdf")
        
        # Text (only SIMPLE)
        assert selector.is_strategy_supported(ProcessingStrategy.SIMPLE, "text/plain")
        assert not selector.is_strategy_supported(ProcessingStrategy.MARKER, "text/plain")
        assert not selector.is_strategy_supported(ProcessingStrategy.COLPALI, "text/plain")
        
        print("\n✓ Strategy support checks working correctly")


# ============================================================================
# Test 3: Processing Results
# ============================================================================

class TestProcessingResult:
    """Test processing result data structures."""
    
    def test_result_creation(self):
        """Test creating a processing result."""
        result = ProcessingResult(
            strategy=ProcessingStrategy.SIMPLE,
            success=True,
            text="Test document content",
            page_count=1,
            processing_time_seconds=1.5,
        )
        
        assert result.strategy == ProcessingStrategy.SIMPLE
        assert result.success is True
        assert result.text == "Test document content"
        assert result.page_count == 1
        assert result.processing_time_seconds == 1.5
        
        print("\n✓ Processing result created successfully")
    
    def test_result_to_dict(self):
        """Test converting result to dictionary."""
        result = ProcessingResult(
            strategy=ProcessingStrategy.MARKER,
            success=True,
            text="Sample text" * 100,
            markdown="# Sample\nContent",
            page_count=5,
            tables=[{"rows": 3}],
            embeddings=[[0.1] * 128] * 10,
            processing_time_seconds=2.3,
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["strategy"] == "marker"
        assert result_dict["success"] is True
        assert result_dict["text_length"] == len("Sample text" * 100)
        assert result_dict["has_markdown"] is True
        assert result_dict["page_count"] == 5
        assert result_dict["table_count"] == 1
        assert result_dict["embedding_count"] == 10
        assert result_dict["processing_time_seconds"] == 2.3
        
        print("\n✓ Result converted to dictionary")
        print(f"  Text length: {result_dict['text_length']}")
        print(f"  Embedding count: {result_dict['embedding_count']}")
    
    def test_failed_result(self):
        """Test creating a failed processing result."""
        result = ProcessingResult(
            strategy=ProcessingStrategy.COLPALI,
            success=False,
            error="ColPali service unavailable",
            processing_time_seconds=0.5,
        )
        
        assert result.success is False
        assert result.error == "ColPali service unavailable"
        assert result.text is None
        assert result.embeddings is None
        
        print("\n✓ Failed result created correctly")


# ============================================================================
# Test 4: Result Comparison
# ============================================================================

class TestResultComparison:
    """Test comparing results from different strategies."""
    
    def test_compare_simple_results(self):
        """Test comparing results from multiple strategies."""
        results = [
            ProcessingResult(
                strategy=ProcessingStrategy.SIMPLE,
                success=True,
                text="Short text",
                embeddings=[[0.1] * 128] * 5,
                processing_time_seconds=0.5,
            ),
            ProcessingResult(
                strategy=ProcessingStrategy.MARKER,
                success=True,
                text="Longer text with more content extracted",
                embeddings=[[0.1] * 128] * 10,
                processing_time_seconds=2.0,
            ),
        ]
        
        comparison = compare_strategy_results(results)
        
        assert comparison["strategies_compared"] == 2
        assert comparison["fastest"] == "simple"
        assert comparison["most_text"] == "marker"
        assert len(comparison["recommendations"]) > 0
        
        print("\n✓ Results compared successfully")
        print(f"  Fastest: {comparison['fastest']}")
        print(f"  Most text: {comparison['most_text']}")
        print(f"  Recommendations: {len(comparison['recommendations'])}")
    
    def test_compare_with_colpali(self):
        """Test comparison including ColPali visual embeddings."""
        results = [
            ProcessingResult(
                strategy=ProcessingStrategy.SIMPLE,
                success=True,
                text="Text content",
                embeddings=[[0.1] * 128] * 5,
                processing_time_seconds=1.0,
            ),
            ProcessingResult(
                strategy=ProcessingStrategy.COLPALI,
                success=True,
                text="Text content",
                visual_embeddings=[[[0.1] * 128] * 128] * 3,  # 3 pages
                processing_time_seconds=3.0,
            ),
        ]
        
        comparison = compare_strategy_results(results)
        
        assert "colpali" in comparison["results"]
        assert comparison["results"]["colpali"]["visual_embedding_count"] == 3
        
        # Check for ColPali recommendation
        recommendations = " ".join(comparison["recommendations"])
        assert "ColPali" in recommendations or "visual" in recommendations.lower()
        
        print("\n✓ ColPali comparison successful")
    
    def test_compare_with_failures(self):
        """Test comparison with some failed results."""
        results = [
            ProcessingResult(
                strategy=ProcessingStrategy.SIMPLE,
                success=True,
                text="Success",
                processing_time_seconds=1.0,
            ),
            ProcessingResult(
                strategy=ProcessingStrategy.MARKER,
                success=False,
                error="Marker failed",
                processing_time_seconds=0.5,
            ),
        ]
        
        comparison = compare_strategy_results(results)
        
        # Only successful results should be considered for "fastest", "most_text"
        assert comparison["fastest"] == "simple"
        assert comparison["most_text"] == "simple"
        
        print("\n✓ Comparison handles failures correctly")


# ============================================================================
# Test 5: Multi-Flow Processor (Unit Tests)
# ============================================================================

class TestMultiFlowProcessor:
    """Test multi-flow processor initialization and configuration."""
    
    def test_processor_initialization(self, test_config):
        """Test multi-flow processor initialization."""
        processor = MultiFlowProcessor(test_config)
        
        assert processor.config == test_config
        assert processor.strategy_selector is not None
        assert processor.text_extractor is not None
        assert processor.chunker is not None
        assert processor.embedder is not None
        assert processor.colpali is not None
        assert processor.classifier is not None
        assert processor.max_workers == 3
        
        print("\n✓ Multi-flow processor initialized")
        print(f"  Max workers: {processor.max_workers}")
    
    def test_processor_with_disabled_strategies(self, test_config):
        """Test processor with some strategies disabled."""
        config = test_config.copy()
        config["marker_enabled"] = False
        config["colpali_enabled"] = False
        
        processor = MultiFlowProcessor(config)
        
        assert not processor.strategy_selector.marker_enabled
        assert not processor.strategy_selector.colpali_enabled
        assert processor.strategy_selector.simple_enabled  # Always enabled
        
        print("\n✓ Processor respects disabled strategies")


# ============================================================================
# Test 6: Integration Tests
# ============================================================================

class TestMultiFlowIntegration:
    """Integration tests for multi-flow processing."""
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_process_text_file(self, test_config, sample_text_file):
        """Test processing a text file (only SIMPLE strategy applies)."""
        processor = MultiFlowProcessor(test_config)
        
        results = await processor.process_document(
            file_path=sample_text_file,
            mime_type="text/plain",
            file_id="test-001",
            original_filename="test.txt",
        )
        
        # Text files should only have SIMPLE strategy
        assert "simple" in results
        assert "marker" not in results
        assert "colpali" not in results
        
        simple_result = results["simple"]
        assert simple_result.success is True
        assert simple_result.text is not None
        assert len(simple_result.text) > 0
        
        print(f"\n✓ Text file processed with SIMPLE strategy")
        print(f"  Text length: {len(simple_result.text)}")
        print(f"  Processing time: {simple_result.processing_time_seconds:.2f}s")
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.slow
    async def test_process_pdf_all_strategies(self, test_config, sample_pdf):
        """Test processing a PDF with all strategies."""
        processor = MultiFlowProcessor(test_config)
        
        results = await processor.process_document(
            file_path=sample_pdf,
            mime_type="application/pdf",
            file_id="test-002",
            original_filename="test.pdf",
        )
        
        # PDF should have all strategies
        assert "simple" in results
        assert "marker" in results
        # ColPali might fail if service not available, which is OK for testing
        
        # Check SIMPLE result
        simple_result = results["simple"]
        print(f"\n✓ PDF processed with multiple strategies")
        print(f"  SIMPLE: {'✓' if simple_result.success else '✗'} ({simple_result.processing_time_seconds:.2f}s)")
        
        if "marker" in results:
            marker_result = results["marker"]
            print(f"  MARKER: {'✓' if marker_result.success else '✗'} ({marker_result.processing_time_seconds:.2f}s)")
        
        if "colpali" in results:
            colpali_result = results["colpali"]
            print(f"  COLPALI: {'✓' if colpali_result.success else '✗'} ({colpali_result.processing_time_seconds:.2f}s)")
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_best_strategy_selection(self, test_config, sample_text_file):
        """Test selecting the best strategy from results."""
        processor = MultiFlowProcessor(test_config)
        
        results = await processor.process_document(
            file_path=sample_text_file,
            mime_type="text/plain",
            file_id="test-003",
            original_filename="test.txt",
        )
        
        # Test different optimization goals
        best_speed = processor.get_best_strategy(results, optimization_goal="speed")
        best_quality = processor.get_best_strategy(results, optimization_goal="quality")
        best_balanced = processor.get_best_strategy(results, optimization_goal="balanced")
        
        assert best_speed is not None
        assert best_quality is not None
        assert best_balanced is not None
        
        print(f"\n✓ Best strategy selection")
        print(f"  Speed: {best_speed}")
        print(f"  Quality: {best_quality}")
        print(f"  Balanced: {best_balanced}")


# ============================================================================
# Diagnostic Utilities
# ============================================================================

@pytest.mark.asyncio
async def test_diagnostic_multi_flow():
    """Generate diagnostic report for multi-flow processing."""
    print("\n" + "="*70)
    print("Multi-Flow Processing Diagnostic Report")
    print("="*70)
    
    # 1. Strategy Configuration
    print("\n1. STRATEGY CONFIGURATION")
    summary = get_strategy_summary()
    for strategy_name, info in summary["strategies"].items():
        print(f"\n   {strategy_name.upper()}")
        print(f"   Description: {info['description']}")
        print(f"   Requires GPU: {info['requires_gpu']}")
        print(f"   Average Speed: {info['average_speed']}")
        print(f"   Supported MIME types: {len(info['supported_mimetypes'])}")
        print(f"   Best for: {', '.join(info['best_for'][:3])}...")
    
    # 2. Strategy Selector
    print("\n2. STRATEGY SELECTOR")
    config = {
        "marker_enabled": True,
        "colpali_enabled": True,
    }
    selector = StrategySelector(config)
    
    test_mimetypes = [
        "application/pdf",
        "text/plain",
        "image/png",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    
    for mimetype in test_mimetypes:
        strategies = selector.get_applicable_strategies(mimetype)
        print(f"   {mimetype}: {[s.value for s in strategies]}")
    
    # 3. Recommendations
    print("\n3. RECOMMENDATIONS")
    print("   • For PDFs: Use all 3 strategies to compare results")
    print("   • For text files: SIMPLE strategy is sufficient")
    print("   • For visual documents: Enable ColPali for semantic image search")
    print("   • For speed: Disable Marker (use only SIMPLE)")
    print("   • For quality: Enable all strategies and compare")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    # Run diagnostic
    asyncio.run(test_diagnostic_multi_flow())

