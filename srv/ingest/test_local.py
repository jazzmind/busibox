#!/usr/bin/env python3
"""
Local test script for text extraction and chunking.

Usage:
    python test_local.py <pdf_file>

This script tests the text extraction and chunking pipeline locally
without requiring database connections or other services.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from processors.text_extractor import TextExtractor
from processors.chunker import Chunker


def test_extraction_and_chunking(pdf_path: str):
    """Test text extraction and chunking on a PDF file."""
    
    print(f"\n{'='*80}")
    print(f"Testing: {pdf_path}")
    print(f"{'='*80}\n")
    
    # Initialize components
    config = {
        "temp_dir": "/tmp/ingest_test",
        "marker_enabled": False,  # Use pdfplumber for faster testing
        "colpali_enabled": False,
        "chunk_size_max": 512,
        "chunk_size_min": 100,
        "chunk_overlap_pct": 0.1,
    }
    
    extractor = TextExtractor(config)
    chunker = Chunker(config)
    
    # Extract text
    print("1. EXTRACTING TEXT")
    print("-" * 80)
    
    try:
        result = extractor.extract(pdf_path, "application/pdf")
        
        print(f"✓ Extraction complete")
        print(f"  - Pages: {result.page_count}")
        print(f"  - Text length: {len(result.text)} characters")
        print(f"  - Method: {result.metadata.get('extraction_method', 'unknown')}")
        print()
        
        # Show first 1000 characters
        print("First 1000 characters of extracted text:")
        print("-" * 80)
        print(result.text[:1000])
        print("-" * 80)
        print()
        
    except Exception as e:
        print(f"✗ Extraction failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Chunk text
    print("\n2. CHUNKING TEXT")
    print("-" * 80)
    
    try:
        chunks = chunker.chunk(result.text)
        
        print(f"✓ Chunking complete")
        print(f"  - Total chunks: {len(chunks)}")
        print(f"  - Total tokens: {sum(c.token_count for c in chunks)}")
        print(f"  - Avg tokens per chunk: {sum(c.token_count for c in chunks) / len(chunks):.1f}")
        print()
        
        # Show first 3 chunks
        print("First 3 chunks:")
        print("-" * 80)
        for i, chunk in enumerate(chunks[:3]):
            print(f"\n--- Chunk {i+1} (tokens: {chunk.token_count}, chars: {len(chunk.text)}) ---")
            print(chunk.text[:500])
            if len(chunk.text) > 500:
                print("...")
        print("-" * 80)
        print()
        
        # Check for issues
        print("\n3. VALIDATION")
        print("-" * 80)
        
        issues = []
        
        # Check for smashed words (no spaces)
        for i, chunk in enumerate(chunks):
            # Look for long sequences without spaces
            words = chunk.text.split()
            for word in words:
                if len(word) > 50:  # Suspiciously long "word"
                    issues.append(f"Chunk {i+1}: Long word detected (len={len(word)}): {word[:50]}...")
        
        # Check for proper heading formatting
        has_markdown_headings = any('#' in chunk.text for chunk in chunks)
        if not has_markdown_headings:
            issues.append("No markdown headings detected (expected # or ## for titles)")
        
        # Check chunk sizes
        for i, chunk in enumerate(chunks):
            if len(chunk.text) > 60000:
                issues.append(f"Chunk {i+1}: Exceeds safe size ({len(chunk.text)} chars)")
            if chunk.token_count > config["chunk_size_max"] * 1.5:
                issues.append(f"Chunk {i+1}: Exceeds token limit ({chunk.token_count} tokens)")
        
        if issues:
            print("⚠ Issues detected:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("✓ No issues detected")
        
        print()
        
    except Exception as e:
        print(f"✗ Chunking failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print(f"\n{'='*80}")
    print("Test complete!")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_local.py <pdf_file>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not Path(pdf_path).exists():
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
    
    test_extraction_and_chunking(pdf_path)

