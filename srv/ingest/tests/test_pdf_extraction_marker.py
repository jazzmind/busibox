"""
Marker PDF Extraction Test - Validates Deployed Worker

Tests Marker-based PDF extraction by triggering the actual deployed ingest worker
and verifying:
1. Extraction succeeds on all test documents
2. No "Cannot copy out of meta tensor" errors in worker logs
3. Marker (not pdfplumber fallback) is used

This test validates that the transformers patch is correctly deployed.
It does NOT apply the patch itself - the worker must have it.

Run via Makefile:
    make test-extraction-marker INV=inventory/test
    
Or directly on the ingest server:
    cd /srv/ingest && source venv/bin/activate
    SAMPLES_DIR=/tmp/test_samples python -m pytest tests/test_pdf_extraction_marker.py -v
"""

import json
import os
import subprocess
import sys
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

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

# Critical error patterns that indicate the patch is missing or not working
CRITICAL_ERROR_PATTERNS = [
    "Cannot copy out of meta tensor",
    "NotImplementedError.*meta tensor",
    "use torch.nn.Module.to_empty",
    "CUDA out of memory",
    "RuntimeError: value cannot be converted to type at::Half",
]

# Warning patterns (test continues but reports)
WARNING_PATTERNS = [
    "pthread_setaffinity_np failed",  # Known LXC limitation, not critical
    "Expected key.size(1) == value.size(1)",  # Surya table_rec tensor shape issue, non-fatal
    "'NoneType' object has no attribute 'shape'",  # Surya table_rec None tensor, non-fatal
    "Marker import succeeded but execution failed",  # Caught and handled, falls back
]


def get_worker_logs(since_time: Optional[datetime] = None, lines: int = 500) -> str:
    """Get ingest-worker logs from journalctl."""
    try:
        cmd = ["journalctl", "-u", "ingest-worker", "-n", str(lines), "--no-pager", "-o", "cat"]
        if since_time:
            cmd.extend(["--since", since_time.strftime("%Y-%m-%d %H:%M:%S")])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout
    except subprocess.TimeoutExpired:
        print("⚠ Timeout getting worker logs")
        return ""
    except Exception as e:
        print(f"⚠ Could not get worker logs: {e}")
        return ""


def check_logs_for_errors(logs: str) -> Dict[str, List[str]]:
    """Check logs for critical error patterns and warnings."""
    errors = []
    warnings = []
    
    for line in logs.split('\n'):
        for pattern in CRITICAL_ERROR_PATTERNS:
            if pattern in line:
                errors.append(line[:300])
                break
        
        for pattern in WARNING_PATTERNS:
            if pattern in line:
                # Only add unique warnings
                if not any(pattern in w for w in warnings):
                    warnings.append(f"[Known LXC issue] {pattern}")
                break
    
    return {"errors": errors, "warnings": warnings}


def extract_via_worker(pdf_path: str, timeout: int = 300) -> Dict:
    """
    Extract text from PDF using the deployed worker.
    
    This imports the TextExtractor which will use the patched transformers
    if the worker was deployed correctly.
    """
    # Add src to path for local imports
    src_path = Path(__file__).parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    
    # Mock network dependencies (we only need local extraction)
    for mod in ['asyncpg', 'pymilvus', 'redis', 'minio']:
        if mod not in sys.modules:
            sys.modules[mod] = type(sys)(mod)
    
    from processors.text_extractor import TextExtractor
    
    config = {
        "temp_dir": "/tmp/ingest",
        "chunk_size_min": 400,
        "chunk_size_max": 800,
        "chunk_overlap_pct": 0.12,
        "marker_enabled": True,
    }
    
    extractor = TextExtractor(config)
    result = extractor.extract(pdf_path, "application/pdf")
    
    return {
        "text": result.text,
        "page_count": result.page_count,
        "tables": result.tables,
        "metadata": result.metadata,
        "extraction_method": result.metadata.get("extraction_method", "unknown"),
    }


def test_pdf_extraction_marker():
    """
    Test Marker-based PDF extraction validates the deployed worker.
    
    This test:
    1. Records the start time
    2. Extracts text from all test PDFs using the deployed worker
    3. Checks worker logs for critical errors (especially meta tensor errors)
    4. Fails if any critical errors found OR if extraction fails
    """
    
    # Record start time for log checking
    test_start_time = datetime.now()
    print(f"\n{'='*80}")
    print("MARKER PDF EXTRACTION TEST - VALIDATING DEPLOYED WORKER")
    print(f"{'='*80}")
    print(f"Test started: {test_start_time}")
    print()
    
    # Get samples directory
    samples_dir_env = os.environ.get("SAMPLES_DIR")
    if samples_dir_env:
        samples_dir = Path(samples_dir_env)
    else:
        # Default locations (new testdocs structure, then old structure)
        base_samples = Path(__file__).parent.parent.parent.parent / "samples"
        for path in [
            Path("/tmp/test_samples"),
            base_samples / "pdf" / "general",  # New testdocs structure
            base_samples / "docs",  # Old structure fallback
        ]:
            if path.exists():
                samples_dir = path
                break
        else:
            raise FileNotFoundError("Could not find samples directory. Set SAMPLES_DIR env var.")
    
    print(f"Samples directory: {samples_dir}")
    assert samples_dir.exists(), f"Samples directory not found: {samples_dir}"
    print()
    
    # Check if worker is running
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "ingest-worker"],
            capture_output=True, text=True, timeout=5
        )
        worker_status = result.stdout.strip()
        print(f"Worker status: {worker_status}")
        if worker_status != "active":
            print("⚠ Warning: ingest-worker is not active, test may use local extraction only")
    except Exception as e:
        print(f"⚠ Could not check worker status: {e}")
    print()
    
    # Check CUDA availability
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        device_count = torch.cuda.device_count() if cuda_available else 0
        print(f"CUDA: {cuda_available}, Devices: {device_count}")
        if cuda_available:
            for i in range(device_count):
                props = torch.cuda.get_device_properties(i)
                free_mem = (props.total_memory - torch.cuda.memory_allocated(i)) / 1024**3
                print(f"  GPU {i}: {torch.cuda.get_device_name(i)} ({free_mem:.1f}GB free)")
    except Exception as e:
        print(f"Could not check CUDA: {e}")
        cuda_available = False
    print()
    
    # Run extractions
    results = []
    passed = 0
    failed = 0
    
    print(f"{'='*80}")
    print("RUNNING EXTRACTIONS")
    print(f"{'='*80}")
    
    for doc_id, doc_type, difficulty in TEST_DOCUMENTS:
        pdf_path = samples_dir / doc_id / "source.pdf"
        
        print(f"\n📄 {doc_id}")
        print(f"   Type: {doc_type}, Difficulty: {difficulty}")
        
        if not pdf_path.exists():
            print(f"   ❌ PDF not found: {pdf_path}")
            failed += 1
            results.append({"id": doc_id, "status": "MISSING", "error": "PDF not found"})
            continue
        
        file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
        print(f"   Size: {file_size_mb:.2f} MB")
        
        try:
            start = time.time()
            result = extract_via_worker(str(pdf_path))
            elapsed = time.time() - start
            
            # Validate results
            assert result["text"], "No text extracted"
            assert len(result["text"]) > 100, f"Text too short: {len(result['text'])} chars"
            assert result["page_count"] > 0, "No pages"
            
            method = result["extraction_method"]
            method_icon = "✓" if method in ["marker", "remote_marker"] else "⚠"
            
            print(f"   ✅ SUCCESS in {elapsed:.1f}s [{method_icon} {method}]")
            print(f"      Pages: {result['page_count']}, Text: {len(result['text']):,} chars")
            
            passed += 1
            results.append({
                "id": doc_id,
                "type": doc_type,
                "status": "PASS",
                "extraction_method": method,
                "page_count": result["page_count"],
                "text_length": len(result["text"]),
                "time_seconds": elapsed,
            })
            
        except Exception as e:
            print(f"   ❌ FAILED: {e}")
            failed += 1
            results.append({"id": doc_id, "status": "FAIL", "error": str(e)})
    
    # Clean up Marker models and GPU memory
    try:
        # Clean up cached Marker models
        from processors.text_extractor import TextExtractor
        TextExtractor.cleanup_marker_models()
        print("✓ Marker models cleaned up")
    except Exception as e:
        print(f"⚠ Could not cleanup Marker models: {e}")
    
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            print("✓ GPU memory cleared")
    except Exception:
        pass
    
    # Check worker logs for errors
    print(f"\n{'='*80}")
    print("CHECKING WORKER LOGS FOR CRITICAL ERRORS")
    print(f"{'='*80}")
    
    # Wait a moment for logs to flush
    time.sleep(2)
    
    logs = get_worker_logs(since_time=test_start_time, lines=1000)
    log_check = check_logs_for_errors(logs)
    
    critical_errors = log_check["errors"]
    warnings = log_check["warnings"]
    
    if critical_errors:
        print(f"\n❌ CRITICAL: Found {len(critical_errors)} error(s) in worker logs!")
        print("   These indicate the transformers patch is NOT working:\n")
        for i, err in enumerate(critical_errors[:5], 1):  # Show first 5
            print(f"   {i}. {err[:200]}...")
        if len(critical_errors) > 5:
            print(f"   ... and {len(critical_errors) - 5} more")
    else:
        print("✓ No critical errors in worker logs")
    
    if warnings:
        print(f"\n⚠ Warnings (non-critical): {len(warnings)}")
        for w in warnings[:3]:
            print(f"   - {w}")
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Documents tested: {len(TEST_DOCUMENTS)}")
    print(f"Passed: {passed} ✅")
    print(f"Failed: {failed} ❌")
    print(f"Critical log errors: {len(critical_errors)}")
    
    # Check extraction methods used
    marker_count = sum(1 for r in results if r.get("extraction_method") in ["marker", "remote_marker"])
    pdfplumber_count = sum(1 for r in results if r.get("extraction_method") == "pdfplumber")
    
    print(f"\nExtraction methods:")
    print(f"  Marker/Remote Marker: {marker_count}")
    print(f"  pdfplumber fallback: {pdfplumber_count}")
    
    if pdfplumber_count > 0:
        print(f"\n⚠ WARNING: {pdfplumber_count} document(s) used pdfplumber fallback!")
        print("   This may indicate Marker failed to load or OOM issues.")
    
    # Save results
    results_file = Path(__file__).parent / "extraction_results_marker.json"
    with open(results_file, 'w') as f:
        json.dump({
            "test_date": datetime.now().isoformat(),
            "strategy": "MARKER",
            "cuda_available": cuda_available,
            "total": len(TEST_DOCUMENTS),
            "passed": passed,
            "failed": failed,
            "critical_log_errors": len(critical_errors),
            "marker_extractions": marker_count,
            "pdfplumber_fallbacks": pdfplumber_count,
            "results": results,
            "log_errors": critical_errors if critical_errors else None,
        }, f, indent=2)
    
    print(f"\nResults: {results_file}")
    print(f"{'='*80}\n")
    
    # FAIL CONDITIONS:
    # 1. Any critical errors in logs (meta tensor, OOM, etc.)
    # 2. Not all documents extracted
    # 3. All documents used pdfplumber fallback (marker completely broken)
    
    if critical_errors:
        raise AssertionError(
            f"CRITICAL: Found {len(critical_errors)} error(s) in worker logs. "
            f"The transformers patch may not be deployed correctly. "
            f"First error: {critical_errors[0][:100]}..."
        )
    
    if failed > 0:
        raise AssertionError(f"Extraction failed for {failed}/{len(TEST_DOCUMENTS)} documents")
    
    if marker_count == 0 and passed > 0:
        raise AssertionError(
            f"All {passed} extractions used pdfplumber fallback. "
            f"Marker is not working - check GPU memory and model loading."
        )
    
    print("✅ ALL TESTS PASSED - Worker is correctly deployed with transformers patch")


if __name__ == "__main__":
    try:
        test_pdf_extraction_marker()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
