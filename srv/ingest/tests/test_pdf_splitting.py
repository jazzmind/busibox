"""
PDF Splitting Tests

Tests the PDF splitting functionality that handles large PDFs by splitting them
into smaller chunks (default: 5 pages) before processing.

Uses REAL PDF files from the busibox-testdocs repository.
No mocking - these are integration tests with actual PDF files.

Test Categories:
1. PDFSplitter unit tests - splitting logic
2. TextExtractor integration tests - extraction with splitting
3. Large PDF processing tests - real documents
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import List

import pytest

from processors.pdf_splitter import PDFSplitter, PDFSplitContext, DEFAULT_PAGES_PER_SPLIT
from processors.text_extractor import TextExtractor

# Add test_utils to path for shared testing utilities
_srv_dir = Path(__file__).parent.parent.parent
if str(_srv_dir) not in sys.path:
    sys.path.insert(0, str(_srv_dir))

from testing.environment import get_test_doc_repo_path


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def samples_dir() -> Path:
    """Get the samples directory (busibox-testdocs)."""
    return get_test_doc_repo_path()


@pytest.fixture
def pdf_splitter() -> PDFSplitter:
    """Create a PDFSplitter with default settings."""
    return PDFSplitter(pages_per_split=5)


@pytest.fixture
def text_extractor_with_splitting() -> TextExtractor:
    """Create a TextExtractor with PDF splitting enabled."""
    config = {
        "temp_dir": tempfile.mkdtemp(prefix="ingest_test_"),
        "marker_enabled": False,  # Use pdfplumber for faster tests
        "pdf_split_enabled": True,
        "pdf_split_pages": 5,
    }
    return TextExtractor(config)


@pytest.fixture
def text_extractor_no_splitting() -> TextExtractor:
    """Create a TextExtractor with PDF splitting disabled."""
    config = {
        "temp_dir": tempfile.mkdtemp(prefix="ingest_test_"),
        "marker_enabled": False,  # Use pdfplumber for faster tests
        "pdf_split_enabled": False,
    }
    return TextExtractor(config)


# ============================================================================
# Test Data - PDFs from busibox-testdocs
# ============================================================================

# Small PDFs (5 or fewer pages - should NOT be split with default 5-page limit)
# Based on actual page counts from busibox-testdocs:
# - doc01_rfp_project_management: 2 pages
# - doc05_rslzva1_datasheet: 3 pages
# - doc03_chartparser_paper: 5 pages
SMALL_PDFS = [
    ("pdf/general/doc01_rfp_project_management/source.pdf", "RFP document (2 pages)"),
    ("pdf/general/doc05_rslzva1_datasheet/source.pdf", "Datasheet (3 pages)"),
    ("pdf/general/doc03_chartparser_paper/source.pdf", "Academic paper (5 pages)"),
]

# Medium PDFs (6-20 pages - will be split into 2-4 chunks)
# - doc02_polymer_nanocapsules_patent: 10 pages
MEDIUM_PDFS = [
    ("pdf/general/doc02_polymer_nanocapsules_patent/source.pdf", "Patent (10 pages)"),
]

# Large PDFs (20+ pages - multiple splits)
# - doc06_urgent_care_whitepaper: 26 pages
# - doc07_nasa_composite_boom: 29 pages
# - doc08_us_bancorp_q4_2023_presentation: 31 pages
# - doc04_zero_shot_reasoners: 42 pages
LARGE_PDFS = [
    ("pdf/general/doc06_urgent_care_whitepaper/source.pdf", "Whitepaper (26 pages)"),
    ("pdf/general/doc07_nasa_composite_boom/source.pdf", "Conference paper (29 pages)"),
    ("pdf/general/doc08_us_bancorp_q4_2023_presentation/source.pdf", "Presentation (31 pages)"),
    ("pdf/general/doc04_zero_shot_reasoners/source.pdf", "Academic paper (42 pages)"),
]

# Very large PDFs (50+ pages)
# - pdf/text/inthebeginning.pdf: 65 pages
# - doc10_nestle_2022_financial_statements: 140 pages
# - A22+Solicitation...IDIQ.pdf: 179 pages
VERY_LARGE_PDFS = [
    ("pdf/text/inthebeginning.pdf", "Text PDF (65 pages)"),
    ("pdf/general/doc10_nestle_2022_financial_statements/source.pdf", "Financial report (140 pages)"),
    ("pdf/rfp/A22+Solicitation+and+Specifications+Upper+Mississippi+River+Mechanical+Dredging+IDIQ.pdf", "Large RFP (179 pages)"),
]


# ============================================================================
# PDFSplitter Unit Tests
# ============================================================================

class TestPDFSplitter:
    """Test the PDFSplitter class directly."""
    
    def test_default_pages_per_split(self):
        """Test default pages_per_split value."""
        assert DEFAULT_PAGES_PER_SPLIT == 5
        
        splitter = PDFSplitter()
        assert splitter.pages_per_split == 5
    
    def test_custom_pages_per_split(self):
        """Test custom pages_per_split configuration."""
        splitter = PDFSplitter(pages_per_split=10)
        assert splitter.pages_per_split == 10
    
    def test_get_page_count(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test getting page count from a PDF."""
        # Use a known PDF
        pdf_path = samples_dir / "pdf" / "general" / "doc01_rfp_project_management" / "source.pdf"
        if not pdf_path.exists():
            pytest.skip(f"Test PDF not found: {pdf_path}")
        
        page_count = pdf_splitter.get_page_count(str(pdf_path))
        assert page_count > 0
        print(f"PDF has {page_count} pages")
    
    def test_needs_splitting_small_pdf(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test that small PDFs don't need splitting."""
        # Find a small PDF
        for rel_path, desc in SMALL_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                page_count = pdf_splitter.get_page_count(str(pdf_path))
                if page_count <= 5:
                    needs_split = pdf_splitter.needs_splitting(str(pdf_path))
                    assert not needs_split, f"{desc} ({page_count} pages) should not need splitting"
                    print(f"✓ {desc}: {page_count} pages - no splitting needed")
                    return
        
        pytest.skip("No small PDFs found for testing")
    
    def test_needs_splitting_large_pdf(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test that large PDFs need splitting."""
        for rel_path, desc in LARGE_PDFS + VERY_LARGE_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                page_count = pdf_splitter.get_page_count(str(pdf_path))
                if page_count > 5:
                    needs_split = pdf_splitter.needs_splitting(str(pdf_path))
                    assert needs_split, f"{desc} ({page_count} pages) should need splitting"
                    print(f"✓ {desc}: {page_count} pages - splitting needed")
                    return
        
        pytest.skip("No large PDFs found for testing")
    
    def test_split_creates_correct_number_of_chunks(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test that split creates the correct number of chunks."""
        for rel_path, desc in MEDIUM_PDFS + LARGE_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                page_count = pdf_splitter.get_page_count(str(pdf_path))
                if page_count > 5:
                    expected_splits = (page_count + 4) // 5  # Ceiling division
                    
                    splits = pdf_splitter.split(str(pdf_path))
                    
                    try:
                        assert len(splits) == expected_splits, \
                            f"Expected {expected_splits} splits for {page_count} pages, got {len(splits)}"
                        
                        print(f"✓ {desc}: {page_count} pages -> {len(splits)} splits")
                        
                        # Verify each split has the correct page range
                        for i, (split_path, start_page, end_page) in enumerate(splits):
                            expected_start = i * 5 + 1
                            expected_end = min((i + 1) * 5, page_count)
                            
                            assert start_page == expected_start, \
                                f"Split {i+1} start page: expected {expected_start}, got {start_page}"
                            assert end_page == expected_end, \
                                f"Split {i+1} end page: expected {expected_end}, got {end_page}"
                            
                            # Verify split file exists
                            assert os.path.exists(split_path), f"Split file not found: {split_path}"
                        
                        return
                    finally:
                        # Clean up split files
                        pdf_splitter.cleanup_splits(splits, str(pdf_path))
        
        pytest.skip("No suitable PDFs found for testing")
    
    def test_split_files_have_correct_page_count(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test that each split file has the correct number of pages."""
        for rel_path, desc in LARGE_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                page_count = pdf_splitter.get_page_count(str(pdf_path))
                if page_count > 10:  # Need enough pages for multiple splits
                    splits = pdf_splitter.split(str(pdf_path))
                    
                    try:
                        for i, (split_path, start_page, end_page) in enumerate(splits):
                            expected_pages = end_page - start_page + 1
                            actual_pages = pdf_splitter.get_page_count(split_path)
                            
                            assert actual_pages == expected_pages, \
                                f"Split {i+1}: expected {expected_pages} pages, got {actual_pages}"
                        
                        print(f"✓ {desc}: All {len(splits)} splits have correct page counts")
                        return
                    finally:
                        pdf_splitter.cleanup_splits(splits, str(pdf_path))
        
        pytest.skip("No suitable PDFs found for testing")
    
    def test_cleanup_removes_split_files(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test that cleanup removes split files."""
        for rel_path, desc in MEDIUM_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                page_count = pdf_splitter.get_page_count(str(pdf_path))
                if page_count > 5:
                    splits = pdf_splitter.split(str(pdf_path))
                    split_paths = [s[0] for s in splits if s[0] != str(pdf_path)]
                    
                    # Verify splits exist
                    for path in split_paths:
                        assert os.path.exists(path), f"Split file should exist: {path}"
                    
                    # Clean up
                    pdf_splitter.cleanup_splits(splits, str(pdf_path))
                    
                    # Verify splits are removed
                    for path in split_paths:
                        assert not os.path.exists(path), f"Split file should be removed: {path}"
                    
                    # Original file should still exist
                    assert os.path.exists(pdf_path), "Original file should not be removed"
                    
                    print(f"✓ Cleanup removed {len(split_paths)} split files")
                    return
        
        pytest.skip("No suitable PDFs found for testing")


class TestPDFSplitContext:
    """Test the PDFSplitContext context manager."""
    
    def test_context_manager_cleans_up(self, samples_dir: Path):
        """Test that context manager cleans up split files automatically."""
        for rel_path, desc in MEDIUM_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                splitter = PDFSplitter(pages_per_split=5)
                page_count = splitter.get_page_count(str(pdf_path))
                
                if page_count > 5:
                    split_paths = []
                    
                    with PDFSplitContext(str(pdf_path), pages_per_split=5) as splits:
                        split_paths = [s[0] for s in splits if s[0] != str(pdf_path)]
                        
                        # Verify splits exist inside context
                        for path in split_paths:
                            assert os.path.exists(path), f"Split should exist in context: {path}"
                    
                    # Verify splits are cleaned up after context
                    for path in split_paths:
                        assert not os.path.exists(path), f"Split should be cleaned up: {path}"
                    
                    print(f"✓ Context manager cleaned up {len(split_paths)} splits")
                    return
        
        pytest.skip("No suitable PDFs found for testing")


# ============================================================================
# TextExtractor Integration Tests
# ============================================================================

class TestTextExtractorWithSplitting:
    """Test TextExtractor with PDF splitting enabled."""
    
    def test_small_pdf_not_split(
        self, samples_dir: Path, text_extractor_with_splitting: TextExtractor
    ):
        """Test that small PDFs are not split."""
        for rel_path, desc in SMALL_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                result = text_extractor_with_splitting.extract(str(pdf_path), "application/pdf")
                
                # Should not have split_processing metadata
                assert result.metadata.get("split_processing") is not True, \
                    f"Small PDF should not be split: {desc}"
                
                assert len(result.text) > 0, "Should extract text"
                print(f"✓ {desc}: extracted {len(result.text)} chars without splitting")
                return
        
        pytest.skip("No small PDFs found for testing")
    
    def test_large_pdf_split(
        self, samples_dir: Path, text_extractor_with_splitting: TextExtractor
    ):
        """Test that large PDFs are split for processing."""
        for rel_path, desc in LARGE_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                result = text_extractor_with_splitting.extract(str(pdf_path), "application/pdf")
                
                # Should have split_processing metadata
                assert result.metadata.get("split_processing") is True, \
                    f"Large PDF should be split: {desc}"
                
                assert result.metadata.get("num_splits", 0) > 1, \
                    "Should have multiple splits"
                
                assert len(result.text) > 0, "Should extract text"
                assert result.page_count > 5, "Should have correct page count"
                
                print(f"✓ {desc}: {result.page_count} pages -> {result.metadata.get('num_splits')} splits, "
                      f"extracted {len(result.text)} chars")
                return
        
        pytest.skip("No large PDFs found for testing")
    
    def test_split_extraction_produces_same_content(
        self, samples_dir: Path, 
        text_extractor_with_splitting: TextExtractor,
        text_extractor_no_splitting: TextExtractor,
    ):
        """Test that split extraction produces similar content to non-split."""
        for rel_path, desc in MEDIUM_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                # Extract with splitting
                result_split = text_extractor_with_splitting.extract(str(pdf_path), "application/pdf")
                
                # Extract without splitting
                result_no_split = text_extractor_no_splitting.extract(str(pdf_path), "application/pdf")
                
                # Page counts should match
                assert result_split.page_count == result_no_split.page_count, \
                    f"Page counts should match: {result_split.page_count} vs {result_no_split.page_count}"
                
                # Text lengths should be similar (within 10% tolerance for page boundary differences)
                len_split = len(result_split.text)
                len_no_split = len(result_no_split.text)
                tolerance = 0.1
                
                assert abs(len_split - len_no_split) < len_no_split * tolerance, \
                    f"Text lengths differ too much: {len_split} vs {len_no_split}"
                
                print(f"✓ {desc}: split={len_split} chars, no-split={len_no_split} chars "
                      f"(diff={abs(len_split - len_no_split)})")
                return
        
        pytest.skip("No suitable PDFs found for testing")


# ============================================================================
# Large PDF Processing Tests
# ============================================================================

class TestLargePDFProcessing:
    """Test processing of large PDFs with splitting."""
    
    def test_process_all_large_pdfs(
        self, samples_dir: Path, text_extractor_with_splitting: TextExtractor
    ):
        """Test processing all large PDFs in the test suite."""
        results = []
        
        all_pdfs = MEDIUM_PDFS + LARGE_PDFS + VERY_LARGE_PDFS
        
        for rel_path, desc in all_pdfs:
            pdf_path = samples_dir / rel_path
            if not pdf_path.exists():
                results.append({
                    "path": rel_path,
                    "description": desc,
                    "status": "SKIPPED",
                    "reason": "File not found",
                })
                continue
            
            try:
                result = text_extractor_with_splitting.extract(str(pdf_path), "application/pdf")
                
                results.append({
                    "path": rel_path,
                    "description": desc,
                    "status": "SUCCESS",
                    "page_count": result.page_count,
                    "text_length": len(result.text),
                    "split_processing": result.metadata.get("split_processing", False),
                    "num_splits": result.metadata.get("num_splits", 1),
                })
                
            except Exception as e:
                results.append({
                    "path": rel_path,
                    "description": desc,
                    "status": "FAILED",
                    "error": str(e),
                })
        
        # Print summary
        print("\n" + "=" * 80)
        print("LARGE PDF PROCESSING TEST RESULTS")
        print("=" * 80)
        
        success_count = 0
        for r in results:
            if r["status"] == "SUCCESS":
                success_count += 1
                split_info = f"splits={r['num_splits']}" if r.get("split_processing") else "no split"
                print(f"✓ {r['description']}: {r['page_count']} pages, "
                      f"{r['text_length']:,} chars ({split_info})")
            elif r["status"] == "SKIPPED":
                print(f"⊘ {r['description']}: {r['reason']}")
            else:
                print(f"✗ {r['description']}: {r.get('error', 'Unknown error')}")
        
        print("=" * 80)
        print(f"Total: {len(results)}, Success: {success_count}, "
              f"Skipped: {len([r for r in results if r['status'] == 'SKIPPED'])}, "
              f"Failed: {len([r for r in results if r['status'] == 'FAILED'])}")
        print("=" * 80)
        
        # At least some PDFs should be processed
        assert success_count > 0, "At least some PDFs should be processed successfully"
    
    def test_very_large_pdf_memory_stability(
        self, samples_dir: Path, text_extractor_with_splitting: TextExtractor
    ):
        """Test that very large PDFs can be processed without memory issues."""
        for rel_path, desc in VERY_LARGE_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                # Get initial page count
                splitter = PDFSplitter()
                page_count = splitter.get_page_count(str(pdf_path))
                
                if page_count < 50:
                    continue
                
                print(f"\nProcessing very large PDF: {desc}")
                print(f"  Pages: {page_count}")
                print(f"  Expected splits: {(page_count + 4) // 5}")
                
                result = text_extractor_with_splitting.extract(str(pdf_path), "application/pdf")
                
                assert result.page_count == page_count, "Page count should match"
                assert len(result.text) > 0, "Should extract text"
                assert result.metadata.get("split_processing") is True, "Should use splitting"
                
                print(f"  ✓ Successfully processed: {len(result.text):,} chars")
                print(f"  ✓ Used {result.metadata.get('num_splits')} splits")
                return
        
        pytest.skip("No very large PDFs found for testing")


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Test edge cases for PDF splitting."""
    
    def test_exactly_5_pages(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test PDF with exactly 5 pages (boundary case)."""
        # This tests the boundary condition - 5 pages should NOT be split
        for rel_path, desc in SMALL_PDFS + MEDIUM_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                page_count = pdf_splitter.get_page_count(str(pdf_path))
                if page_count == 5:
                    needs_split = pdf_splitter.needs_splitting(str(pdf_path))
                    assert not needs_split, "Exactly 5 pages should not need splitting"
                    print(f"✓ Found PDF with exactly 5 pages: {desc}")
                    return
        
        print("⊘ No PDF with exactly 5 pages found in test suite")
    
    def test_6_pages(self, samples_dir: Path, pdf_splitter: PDFSplitter):
        """Test PDF with 6 pages (first to be split)."""
        for rel_path, desc in SMALL_PDFS + MEDIUM_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                page_count = pdf_splitter.get_page_count(str(pdf_path))
                if page_count == 6:
                    needs_split = pdf_splitter.needs_splitting(str(pdf_path))
                    assert needs_split, "6 pages should need splitting"
                    
                    splits = pdf_splitter.split(str(pdf_path))
                    try:
                        assert len(splits) == 2, "6 pages should split into 2 chunks"
                        assert splits[0][1:] == (1, 5), "First chunk: pages 1-5"
                        assert splits[1][1:] == (6, 6), "Second chunk: page 6"
                        print(f"✓ Found PDF with 6 pages: {desc}")
                        return
                    finally:
                        pdf_splitter.cleanup_splits(splits, str(pdf_path))
        
        print("⊘ No PDF with exactly 6 pages found in test suite")
    
    def test_custom_pages_per_split(
        self, samples_dir: Path, text_extractor_with_splitting: TextExtractor
    ):
        """Test with custom pages_per_split value."""
        # Create extractor with 10 pages per split
        config = {
            "temp_dir": tempfile.mkdtemp(prefix="ingest_test_"),
            "marker_enabled": False,
            "pdf_split_enabled": True,
            "pdf_split_pages": 10,  # Custom value
        }
        extractor = TextExtractor(config)
        
        for rel_path, desc in LARGE_PDFS:
            pdf_path = samples_dir / rel_path
            if pdf_path.exists():
                splitter = PDFSplitter()
                page_count = splitter.get_page_count(str(pdf_path))
                
                if page_count > 15:  # Need more than 10 pages for meaningful test
                    result = extractor.extract(str(pdf_path), "application/pdf")
                    
                    expected_splits = (page_count + 9) // 10  # Ceiling division by 10
                    actual_splits = result.metadata.get("num_splits", 1)
                    
                    assert actual_splits == expected_splits, \
                        f"Expected {expected_splits} splits with 10 pages/split, got {actual_splits}"
                    
                    print(f"✓ {desc}: {page_count} pages -> {actual_splits} splits (10 pages each)")
                    return
        
        pytest.skip("No suitable large PDF found for testing")


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

