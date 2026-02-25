"""
Comprehensive PDF Processing Test Suite

Tests different processing strategies (SIMPLE, MARKER, COLPALI) against
a diverse set of real-world PDF documents with known evaluation criteria.

Test documents cover:
- Simple text documents (low difficulty)
- Technical documents with tables/formulas (medium difficulty)
- Complex financial documents (high/very high difficulty)
- Multi-column academic papers (high difficulty)
- Presentation slides (high difficulty)
"""

import json
import os
import pytest
from pathlib import Path
from typing import Dict, List

from processors.text_extractor import TextExtractor
from processors.processing_strategy import (
    ProcessingStrategy,
    StrategySelector,
    STRATEGY_CONFIGS,
)
from shared.config import Config


# Test document definitions from testdocs/pdf/general/ (via samples symlink)
TEST_DOCUMENTS = [
    {
        "id": "doc01_rfp_project_management",
        "title": "Sample Request for Proposals (RFP) for Project Management Services",
        "type": "RFP",
        "difficulty": "low",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "RFP title and issuing agency correctly identified",
            "All numbered services/tasks preserved in order",
            "Section headers captured as headings",
            "Grant/program language captured without corruption",
            "No loss or duplication from page headers/footers",
        ],
        "best_strategy": ProcessingStrategy.SIMPLE,  # Simple text, no complex layout
        "key_features": ["single-column", "numbered lists", "section headers"],
    },
    {
        "id": "doc02_polymer_nanocapsules_patent",
        "title": "Polymer Nanocapsules Entrapping Metal Nanoparticles (US 9,248,441 B2)",
        "type": "Patent",
        "difficulty": "medium",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Full abstract preserved as coherent block",
            "Section titles correctly segmented",
            "Claim numbers and sub-clauses preserved with hierarchy",
            "Figure captions and references captured correctly",
            "Chemical and measurement symbols preserved",
        ],
        "best_strategy": ProcessingStrategy.MARKER,  # Complex legal structure, figures
        "key_features": ["two-column sections", "nested claims", "figure references", "technical symbols"],
    },
    {
        "id": "doc03_chartparser_paper",
        "title": "ChartParser: Automatic Chart Parsing for Print-Impaired",
        "type": "Academic Paper (CS)",
        "difficulty": "high",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Two columns merged in correct reading order",
            "Abstract captured as contiguous segment",
            "Table structures preserved with numerical values",
            "Figure captions linked to correct figure numbers",
            "Section headers captured correctly",
        ],
        "best_strategy": ProcessingStrategy.MARKER,  # Two-column, tables, figures
        "key_features": ["two-column layout", "tables", "figures", "equations"],
    },
    {
        "id": "doc04_zero_shot_reasoners",
        "title": "Large Language Models are Zero-Shot Reasoners",
        "type": "Academic Paper (ML)",
        "difficulty": "high",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Two-column reading order preserved",
            "Mathematical notation preserved",
            "Tables with experimental results intact",
            "Algorithm pseudocode correctly extracted",
            "References section complete",
        ],
        "best_strategy": ProcessingStrategy.MARKER,  # Two-column, math, tables
        "key_features": ["two-column layout", "mathematical notation", "tables", "algorithms"],
    },
    {
        "id": "doc05_rslzva1_datasheet",
        "title": "RSLZVA1 Socket Datasheet (Harmony Electromechanical Relays)",
        "type": "Technical Datasheet",
        "difficulty": "medium",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "All spec names and values mapped correctly",
            "Measurement units preserved",
            "Section titles and groupings preserved",
            "Connection diagrams detected with captions",
            "Multilingual content separated cleanly",
        ],
        "best_strategy": ProcessingStrategy.MARKER,  # Technical tables, diagrams
        "key_features": ["spec tables", "diagrams", "measurement units", "structured parameters"],
    },
    {
        "id": "doc06_urgent_care_whitepaper",
        "title": "2023 Urgent Care Industry White Paper",
        "type": "Industry White Paper",
        "difficulty": "medium",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Executive summary captured completely",
            "Statistical data and percentages preserved",
            "Charts and graphs detected",
            "Section headings maintained",
            "Footnotes and citations preserved",
        ],
        "best_strategy": ProcessingStrategy.MARKER,  # Charts, statistics
        "key_features": ["charts", "statistics", "multi-section", "data visualization"],
    },
    {
        "id": "doc07_nasa_composite_boom",
        "title": "A Multifunctional Bistable Ultrathin Composite Boom for In-Space Monitoring",
        "type": "Technical Conference Paper",
        "difficulty": "high",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Figure captions associated with images",
            "Section headings captured cleanly",
            "Equations and symbols preserved",
            "Numeric values from tables extracted",
            "Abstract separated from main body",
        ],
        "best_strategy": ProcessingStrategy.MARKER,  # Technical paper with figures
        "key_features": ["engineering figures", "equations", "technical diagrams", "measurements"],
    },
    {
        "id": "doc08_us_bancorp_q4_2023_presentation",
        "title": "U.S. Bancorp 4Q23 Earnings Conference Call Presentation",
        "type": "Corporate Investor Presentation",
        "difficulty": "high",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Slide-level text extracted in readable order",
            "Footnotes under charts preserved",
            "Table structures captured correctly",
            "No duplication from background images",
            "Key quantitative figures reconstructible",
        ],
        "best_strategy": ProcessingStrategy.COLPALI,  # Highly visual presentation
        "key_features": ["landscape slides", "charts", "financial tables", "visual-heavy"],
    },
    {
        "id": "doc09_visit_phoenix_destination_brochure",
        "title": "Visit Phoenix Destination Brochure",
        "type": "Marketing Brochure",
        "difficulty": "high",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Marketing copy extracted without image overlay text",
            "Location names and addresses preserved",
            "Structured lists maintained",
            "Map text detected if possible",
            "Visual layout not disrupting text flow",
        ],
        "best_strategy": ProcessingStrategy.COLPALI,  # Highly visual, image-heavy
        "key_features": ["visual design", "images", "marketing layout", "mixed text-image"],
    },
    {
        "id": "doc10_nestle_2022_financial_statements",
        "title": "Consolidated Financial Statements of the Nestlé Group 2022",
        "type": "Corporate Financial Statements",
        "difficulty": "very_high",
        "mime_type": "application/pdf",
        "expected_strategies": [ProcessingStrategy.SIMPLE, ProcessingStrategy.MARKER, ProcessingStrategy.COLPALI],
        "eval_criteria": [
            "Table headers and subheaders captured correctly",
            "Numeric values aligned to correct line items and years",
            "Negative values/parentheses preserved",
            "Note references attached to correct line items",
            "No table rows truncated or duplicated at page breaks",
        ],
        "best_strategy": ProcessingStrategy.MARKER,  # Complex multi-column tables
        "key_features": ["complex tables", "multi-column", "financial data", "cross-references"],
    },
]


@pytest.fixture
def config():
    """Load test configuration as Config object (some fixtures need both dict and object)."""
    return Config()


@pytest.fixture
def samples_dir():
    """Get path to testdocs/pdf/general directory."""
    from testing.environment import get_test_doc_repo_path
    
    # Get the test docs path from environment or sibling repo
    test_doc_path = get_test_doc_repo_path()
    
    # Test docs are in pdf/general/ subdirectory
    samples_path = test_doc_path / "pdf" / "general"
    if samples_path.exists():
        return samples_path
    
    # Fallback to old location for local development
    repo_root = Path(__file__).parent.parent.parent.parent
    return repo_root / "samples" / "docs"


@pytest.fixture
def text_extractor(config):
    """Create text extractor instance."""
    return TextExtractor(config.to_dict())


class TestPDFProcessingSuite:
    """Test suite for PDF processing strategies."""

    def test_all_documents_downloaded(self, samples_dir):
        """Verify all test PDFs are downloaded."""
        if not samples_dir.exists():
            pytest.skip(f"Test documents directory not found: {samples_dir}")
        
        for doc in TEST_DOCUMENTS:
            pdf_path = samples_dir / doc["id"] / "source.pdf"
            assert pdf_path.exists(), f"PDF not found: {doc['id']}"
            assert pdf_path.stat().st_size > 0, f"PDF is empty: {doc['id']}"

    def test_all_evals_present(self, samples_dir):
        """Verify all eval files are present."""
        if not samples_dir.exists():
            pytest.skip(f"Test documents directory not found: {samples_dir}")
        
        for doc in TEST_DOCUMENTS:
            eval_json = samples_dir / doc["id"] / "eval.json"
            eval_md = samples_dir / doc["id"] / "eval.md"
            assert eval_json.exists(), f"eval.json not found: {doc['id']}"
            assert eval_md.exists(), f"eval.md not found: {doc['id']}"

    @pytest.mark.parametrize("doc_info", TEST_DOCUMENTS, ids=lambda d: d["id"])
    def test_strategy_selection(self, doc_info, config):
        """Test that appropriate strategies are selected for each document."""
        # Create selector with all strategies enabled for testing
        test_config = config.to_dict()
        test_config["marker_enabled"] = True
        test_config["colpali_enabled"] = True
        
        selector = StrategySelector(test_config)
        applicable = selector.get_applicable_strategies(
            mime_type=doc_info["mime_type"],
            force_all=True,  # Get all supported strategies
        )
        
        # Verify expected strategies are applicable
        for expected in doc_info["expected_strategies"]:
            assert expected in applicable, (
                f"{doc_info['id']}: Expected strategy {expected} not applicable"
            )

    @pytest.mark.parametrize("doc_info", TEST_DOCUMENTS, ids=lambda d: d["id"])
    def test_simple_extraction(self, doc_info, samples_dir, text_extractor):
        """Test SIMPLE strategy extraction on each document."""
        if not samples_dir.exists():
            pytest.skip(f"Test documents directory not found: {samples_dir}")
        
        pdf_path = samples_dir / doc_info["id"] / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"PDF not found: {pdf_path}")
        
        result = text_extractor.extract(str(pdf_path), doc_info["mime_type"])
        
        # Basic extraction assertions
        assert result.text is not None, f"{doc_info['id']}: No text extracted"
        assert len(result.text) > 100, f"{doc_info['id']}: Text too short ({len(result.text)} chars)"
        assert result.page_count > 0, f"{doc_info['id']}: Page count is zero"
        
        # Document-specific checks based on difficulty
        if doc_info["difficulty"] == "low":
            # Simple documents should extract well with SIMPLE strategy
            assert len(result.text) > 1000, f"{doc_info['id']}: Expected more text for low difficulty"

    @pytest.mark.parametrize("doc_info", TEST_DOCUMENTS, ids=lambda d: d["id"])
    def test_extraction_quality_metrics(self, doc_info, samples_dir, text_extractor):
        """Test quality metrics for extraction."""
        if not samples_dir.exists():
            pytest.skip(f"Test documents directory not found: {samples_dir}")
        
        pdf_path = samples_dir / doc_info["id"] / "source.pdf"
        
        if not pdf_path.exists():
            pytest.skip(f"PDF not found: {pdf_path}")
        
        result = text_extractor.extract(str(pdf_path), doc_info["mime_type"])
        
        # Calculate basic quality metrics
        metrics = {
            "text_length": len(result.text),
            "page_count": result.page_count,
            "avg_chars_per_page": len(result.text) / max(result.page_count, 1),
            "has_tables": len(result.tables) > 0 if result.tables else False,
            "has_images": len(result.page_images) > 0 if result.page_images else False,
        }
        
        # Difficulty-based expectations
        if doc_info["difficulty"] in ["high", "very_high"]:
            # Complex documents should have substantial content
            assert metrics["avg_chars_per_page"] > 500, (
                f"{doc_info['id']}: Low chars/page for high difficulty doc"
            )
        
        # Store metrics for comparison (would be used in actual test runs)
        print(f"\n{doc_info['id']} metrics: {json.dumps(metrics, indent=2)}")

    def test_difficulty_distribution(self):
        """Verify test suite covers range of difficulty levels."""
        difficulties = [doc["difficulty"] for doc in TEST_DOCUMENTS]
        difficulty_counts = {
            "low": difficulties.count("low"),
            "medium": difficulties.count("medium"),
            "high": difficulties.count("high"),
            "very_high": difficulties.count("very_high"),
        }
        
        # Ensure we have documents at each difficulty level
        assert difficulty_counts["low"] >= 1, "Need at least 1 low difficulty doc"
        assert difficulty_counts["medium"] >= 2, "Need at least 2 medium difficulty docs"
        assert difficulty_counts["high"] >= 3, "Need at least 3 high difficulty docs"
        assert difficulty_counts["very_high"] >= 1, "Need at least 1 very high difficulty doc"

    def test_document_type_coverage(self):
        """Verify test suite covers diverse document types."""
        types = set(doc["type"] for doc in TEST_DOCUMENTS)
        
        # Should have variety
        assert len(types) >= 6, f"Need more document type diversity (have {len(types)})"
        
        # Check for specific important types
        assert any("Patent" in t for t in types), "Need patent document"
        assert any("Academic" in t or "Paper" in t for t in types), "Need academic paper"
        assert any("Financial" in t for t in types), "Need financial document"
        assert any("Presentation" in t for t in types), "Need presentation"


class TestStrategyComparison:
    """Compare different processing strategies on the same documents."""

    @pytest.mark.parametrize("doc_info", TEST_DOCUMENTS[:3], ids=lambda d: d["id"])  # Test on first 3 docs
    def test_compare_simple_vs_marker(self, doc_info, samples_dir):
        """Compare SIMPLE vs MARKER strategies (when both are applicable)."""
        if ProcessingStrategy.MARKER not in doc_info["expected_strategies"]:
            pytest.skip(f"MARKER not applicable for {doc_info['id']}")
        
        pdf_path = samples_dir / doc_info["id"] / "source.pdf"
        
        # Would compare outputs from different strategies
        # This is a placeholder for actual comparison logic
        print(f"\n{doc_info['id']}: Best strategy = {doc_info['best_strategy']}")


def generate_test_report(samples_dir: Path) -> Dict:
    """Generate comprehensive test report for all documents."""
    report = {
        "total_documents": len(TEST_DOCUMENTS),
        "difficulty_breakdown": {},
        "type_breakdown": {},
        "strategy_recommendations": {},
        "documents": []
    }
    
    for doc in TEST_DOCUMENTS:
        pdf_path = samples_dir / doc["id"] / "source.pdf"
        eval_path = samples_dir / doc["id"] / "eval.json"
        
        doc_report = {
            "id": doc["id"],
            "title": doc["title"],
            "type": doc["type"],
            "difficulty": doc["difficulty"],
            "file_exists": pdf_path.exists(),
            "file_size_mb": pdf_path.stat().st_size / (1024 * 1024) if pdf_path.exists() else 0,
            "eval_criteria_count": len(doc["eval_criteria"]),
            "recommended_strategy": doc["best_strategy"].value,
            "key_features": doc["key_features"],
        }
        
        report["documents"].append(doc_report)
        
        # Update breakdowns
        difficulty = doc["difficulty"]
        report["difficulty_breakdown"][difficulty] = report["difficulty_breakdown"].get(difficulty, 0) + 1
        
        doc_type = doc["type"]
        report["type_breakdown"][doc_type] = report["type_breakdown"].get(doc_type, 0) + 1
        
        strategy = doc["best_strategy"].value
        report["strategy_recommendations"][strategy] = report["strategy_recommendations"].get(strategy, 0) + 1
    
    return report


if __name__ == "__main__":
    """Run tests and generate report."""
    import sys
    
    # Get samples directory
    repo_root = Path(__file__).parent.parent.parent.parent
    samples_dir = repo_root / "samples" / "docs"
    
    # Generate and print report
    report = generate_test_report(samples_dir)
    print("\n" + "="*80)
    print("PDF PROCESSING TEST SUITE REPORT")
    print("="*80)
    print(json.dumps(report, indent=2))
    print("="*80)
    
    # Run pytest
    pytest.main([__file__, "-v"])

