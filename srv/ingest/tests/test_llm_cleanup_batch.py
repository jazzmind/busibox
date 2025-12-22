#!/usr/bin/env python3
"""
Test script to determine optimal batch size for LLM cleanup.

This test:
1. Creates sample chunks of varying sizes
2. Tests different concurrency levels (1, 2, 3, 5, 10)
3. Measures throughput, latency, and failure rates
4. Reports optimal batch size for current LLM configuration

Run: python test_llm_cleanup_batch.py
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Tuple

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import httpx

# Sample text chunks of varying complexity
SAMPLE_CHUNKS = [
    # Short simple chunk
    "The company reported Q3 earnings of $2.5 billion, exceeding analyst expectations.",
    
    # Medium chunk with some OCR-like errors
    "Tbe quarterly rev3nue grew by 15% year-over-year, driven primari1y by strong demand in the enterprise segment. Management noted that supply chain challenges have been mitigated.",
    
    # Longer chunk
    """The financial statements presented herein have been prepared in accordance with 
    Generally Accepted Accounting Principles (GAAP). Revenue recognition follows ASC 606 
    guidelines, with performance obligations identified at contract inception. The company 
    uses the modified retrospective method for adoption of new accounting standards.""",
    
    # Chunk with table-like content
    "Product A: $45.2M (+12%) | Product B: $38.7M (+8%) | Product C: $22.1M (-3%)",
    
    # Technical content
    "API response latency p99: 45ms, throughput: 12,500 req/s, error rate: 0.02%",
]


@dataclass
class BatchTestResult:
    """Result from testing a specific batch size."""
    batch_size: int
    total_chunks: int
    successful: int
    failed: int
    total_time_seconds: float
    avg_latency_seconds: float
    throughput_per_second: float
    errors: List[str]


async def _process_single_chunk(
    client: httpx.AsyncClient,
    text: str,
    litellm_url: str,
    model: str,
    api_key: str = None,
) -> Tuple[bool, float, str]:
    """Process cleanup of a single chunk, return (success, latency, error_msg)."""
    start = time.time()
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    system_prompt = """You are a text cleanup assistant. Fix OCR errors, formatting issues, 
and improve readability while preserving the original meaning. Return only the cleaned text."""
    
    try:
        response = await client.post(
            f"{litellm_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                "temperature": 0.1,
                "max_tokens": min(len(text) * 2, 4096),
            },
            timeout=60.0
        )
        
        latency = time.time() - start
        
        if response.status_code == 200:
            return True, latency, None
        else:
            return False, latency, f"HTTP {response.status_code}: {response.text[:200]}"
            
    except httpx.TimeoutException:
        return False, time.time() - start, "Timeout"
    except Exception as e:
        return False, time.time() - start, str(e)


async def _run_batch_test(
    batch_size: int,
    num_chunks: int,
    litellm_url: str,
    model: str,
    api_key: str = None,
) -> BatchTestResult:
    """Run a specific batch size test with concurrent requests."""
    
    # Create test chunks by cycling through samples
    test_chunks = [SAMPLE_CHUNKS[i % len(SAMPLE_CHUNKS)] for i in range(num_chunks)]
    
    semaphore = asyncio.Semaphore(batch_size)
    results = []
    errors = []
    
    async def process_chunk(chunk: str, index: int):
        async with semaphore:
            async with httpx.AsyncClient() as client:
                success, latency, error = await _process_single_chunk(
                    client, chunk, litellm_url, model, api_key
                )
                return success, latency, error, index
    
    start_time = time.time()
    
    # Run all chunks with concurrency limit
    tasks = [process_chunk(chunk, i) for i, chunk in enumerate(test_chunks)]
    task_results = await asyncio.gather(*tasks)
    
    total_time = time.time() - start_time
    
    # Analyze results
    successful = 0
    latencies = []
    
    for success, latency, error, index in task_results:
        if success:
            successful += 1
            latencies.append(latency)
        else:
            errors.append(f"Chunk {index}: {error}")
    
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    throughput = successful / total_time if total_time > 0 else 0
    
    return BatchTestResult(
        batch_size=batch_size,
        total_chunks=num_chunks,
        successful=successful,
        failed=num_chunks - successful,
        total_time_seconds=total_time,
        avg_latency_seconds=avg_latency,
        throughput_per_second=throughput,
        errors=errors[:5],  # Only keep first 5 errors
    )


async def run_batch_size_tests():
    """Run tests for different batch sizes and report optimal."""
    
    # Configuration
    litellm_url = os.getenv("LITELLM_BASE_URL", "http://10.96.200.207:4000")
    model = os.getenv("LLM_CLEANUP_MODEL", "cleanup")  # Model name from registry
    api_key = os.getenv("LITELLM_API_KEY", "")
    
    # Test parameters
    batch_sizes = [1, 2, 3, 5, 8, 10]
    num_chunks = 20  # Total chunks per test
    
    print("=" * 70)
    print("LLM Cleanup Batch Size Test")
    print("=" * 70)
    print(f"LiteLLM URL: {litellm_url}")
    print(f"Model: {model}")
    print(f"Chunks per test: {num_chunks}")
    print(f"Batch sizes to test: {batch_sizes}")
    print("=" * 70)
    print()
    
    # First, verify connectivity
    print("Verifying LLM connectivity...")
    async with httpx.AsyncClient() as client:
        success, latency, error = await _process_single_chunk(
            client, "Test connection", litellm_url, model, api_key
        )
        if not success:
            print(f"ERROR: Cannot connect to LLM: {error}")
            print("Check LITELLM_BASE_URL and LITELLM_API_KEY environment variables")
            return
        print(f"✓ Connected (latency: {latency:.2f}s)")
    print()
    
    # Run tests
    results = []
    for batch_size in batch_sizes:
        print(f"Testing batch_size={batch_size}...", end=" ", flush=True)
        result = await _run_batch_test(batch_size, num_chunks, litellm_url, model, api_key)
        results.append(result)
        print(f"done ({result.total_time_seconds:.1f}s, {result.successful}/{result.total_chunks} ok)")
        
        # Small delay between tests
        await asyncio.sleep(1)
    
    # Report results
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"{'Batch':<8} {'Success':<10} {'Time(s)':<10} {'Throughput':<12} {'Avg Lat(s)':<12}")
    print("-" * 70)
    
    best_throughput = 0
    best_batch_size = 1
    
    for r in results:
        success_pct = f"{r.successful}/{r.total_chunks}"
        print(f"{r.batch_size:<8} {success_pct:<10} {r.total_time_seconds:<10.1f} {r.throughput_per_second:<12.2f} {r.avg_latency_seconds:<12.2f}")
        
        # Track best (considering success rate)
        effective_throughput = r.throughput_per_second * (r.successful / r.total_chunks)
        if effective_throughput > best_throughput and r.failed <= r.total_chunks * 0.1:  # Max 10% failure
            best_throughput = effective_throughput
            best_batch_size = r.batch_size
    
    print("-" * 70)
    print(f"\n✓ RECOMMENDED BATCH SIZE: {best_batch_size}")
    print(f"  (Best effective throughput: {best_throughput:.2f} chunks/sec)")
    
    # Show any errors
    all_errors = []
    for r in results:
        all_errors.extend(r.errors)
    
    if all_errors:
        print(f"\nSample errors ({len(all_errors)} total):")
        for err in all_errors[:5]:
            print(f"  - {err}")
    
    return best_batch_size


if __name__ == "__main__":
    optimal = asyncio.run(run_batch_size_tests())
    print(f"\nTo use this batch size, set LLM_CLEANUP_BATCH_SIZE={optimal} in environment")


