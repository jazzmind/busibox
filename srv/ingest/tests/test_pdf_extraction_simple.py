"""
Simple PDF Extraction Test - No Database Dependencies

Tests basic PDF extraction on all 10 test documents without requiring
database connections or full worker setup.
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Mock dependencies that require database/network
sys.modules['asyncpg'] = type(sys)('asyncpg')
sys.modules['pymilvus'] = type(sys)('pymilvus')
sys.modules['redis'] = type(sys)('redis')
sys.modules['minio'] = type(sys)('minio')

# Now import after mocks
from processors.text_extractor import TextExtractor
from shared.config import Config

# Test document definitions
TEST_DOCUMENTS = [
    ("doc01_rfp_project_management", "RFP", "low"),
    ("doc02_polymer_nanocapsules_patent", "Patent", "medium"),
    ("doc03_chartparser_paper", "Academic Paper", "high"),
    ("doc04_zero_shot_reasoners", "Academic Paper", "high"),
    ("doc05_rslzva1_datasheet", "Datasheet", "medium"),
    ("doc06_urgent_care_whitepaper", "White Paper", "medium"),
    ("doc07_nasa_composite_boom", "Conference Paper", "high"),
    ("doc08_us_bancorp_q4_2023_presentation", "Presentation", "high"),
    ("doc09_visit_phoenix_destination_brochure", "Brochure", "high"),
    ("doc10_nestle_2022_financial_statements", "Financial", "very_high"),
]


def test_pdf_extraction():
    """Test basic PDF extraction on all documents."""
    
    # Get samples directory - use env var if set (from Makefile), otherwise find repo root
    samples_dir_env = os.environ.get("SAMPLES_DIR")
    if samples_dir_env:
        samples_dir = Path(samples_dir_env)
        print(f"Using SAMPLES_DIR from environment: {samples_dir}")
    else:
        # Try to find repo root (go up from tests -> src -> srv -> busibox)
        repo_root = Path(__file__).parent.parent.parent.parent
        samples_dir = repo_root / "samples" / "docs"
        print(f"Using repo root path: {samples_dir}")
    
    print("\n" + "="*80)
    print("PDF EXTRACTION TEST - SIMPLE STRATEGY")
    print("="*80)
    print(f"Samples directory: {samples_dir}")
    print(f"Samples directory exists: {samples_dir.exists()}")
    print()
    
    # Create config - explicitly disable Marker to use SIMPLE strategy
    config = {
        "temp_dir": "/tmp/ingest",
        "chunk_size_min": 400,
        "chunk_size_max": 800,
        "chunk_overlap_pct": 0.12,
        "marker_enabled": False,  # Force SIMPLE strategy (pdfplumber only)
    }
    
    # Create extractor
    extractor = TextExtractor(config)
    
    results = []
    passed = 0
    failed = 0
    
    for doc_id, doc_type, difficulty in TEST_DOCUMENTS:
        pdf_path = samples_dir / doc_id / "source.pdf"
        eval_path = samples_dir / doc_id / "eval.json"
        
        print(f"\n📄 Testing: {doc_id}")
        print(f"   Type: {doc_type}, Difficulty: {difficulty}")
        
        # Check PDF exists
        if not pdf_path.exists():
            print(f"   ❌ PDF not found: {pdf_path}")
            failed += 1
            results.append({
                "id": doc_id,
                "status": "MISSING",
                "error": "PDF file not found"
            })
            continue
        
        file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
        print(f"   Size: {file_size_mb:.2f} MB")
        
        try:
            # Extract text
            result = extractor.extract(str(pdf_path), "application/pdf")
            
            # Basic validations
            assert result.text is not None, "No text extracted"
            assert len(result.text) > 100, f"Text too short: {len(result.text)} chars"
            assert result.page_count > 0, "No pages counted"
            
            # Calculate metrics
            text_length = len(result.text)
            page_count = result.page_count
            avg_chars_per_page = text_length / page_count
            
            print(f"   ✅ SUCCESS")
            print(f"      Pages: {page_count}")
            print(f"      Text: {text_length:,} characters")
            print(f"      Avg: {avg_chars_per_page:.0f} chars/page")
            if result.tables:
                print(f"      Tables: {len(result.tables)}")
            
            passed += 1
            results.append({
                "id": doc_id,
                "type": doc_type,
                "difficulty": difficulty,
                "status": "PASS",
                "page_count": page_count,
                "text_length": text_length,
                "avg_chars_per_page": avg_chars_per_page,
                "has_tables": len(result.tables) > 0 if result.tables else False,
            })
            
        except Exception as e:
            print(f"   ❌ FAILED: {str(e)}")
            failed += 1
            results.append({
                "id": doc_id,
                "status": "FAIL",
                "error": str(e)
            })
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Total Documents: {len(TEST_DOCUMENTS)}")
    print(f"Passed: {passed} ✅")
    print(f"Failed: {failed} ❌")
    print()
    
    # Detailed results
    if passed > 0:
        print("\n" + "-"*80)
        print("EXTRACTION METRICS")
        print("-"*80)
        
        for r in results:
            if r["status"] == "PASS":
                print(f"\n{r['id']} ({r['difficulty']}):")
                print(f"  Pages: {r['page_count']}, Text: {r['text_length']:,} chars, Avg: {r['avg_chars_per_page']:.0f} chars/page")
    
    # Save results
    results_file = Path(__file__).parent / "extraction_results_simple.json"
    with open(results_file, 'w') as f:
        json.dump({
            "test_date": str(Path(__file__).stat().st_mtime),
            "strategy": "SIMPLE",
            "llm_cleanup": False,
            "total": len(TEST_DOCUMENTS),
            "passed": passed,
            "failed": failed,
            "results": results
        }, f, indent=2)
    
    print(f"\nResults saved to: {results_file}")
    print("="*80)
    print()
    
    # Assert all documents passed
    assert passed == len(TEST_DOCUMENTS), f"Only {passed}/{len(TEST_DOCUMENTS)} documents passed extraction"


if __name__ == "__main__":
    try:
        test_pdf_extraction()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)

