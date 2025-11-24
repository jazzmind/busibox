"""
Comprehensive ColPali Visual Embedding Tests

Tests for the ColPali visual document embedding service, including:
- Service availability and health checks
- Model loading and configuration
- Image encoding and processing
- Embedding generation and validation
- API compatibility
- Error handling and edge cases
- Performance benchmarks

Run with: pytest tests/test_colpali.py -v
"""

import base64
import io
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from processors.colpali import ColPaliEmbedder
from shared.config import Config


# ============================================================================
# Test Configuration
# ============================================================================

# Test environment variables (override defaults for testing)
TEST_COLPALI_BASE_URL = os.getenv("COLPALI_BASE_URL", "http://10.96.200.208:9006/v1")
TEST_COLPALI_HEALTH_URL = TEST_COLPALI_BASE_URL.replace("/v1", "/health")
TEST_COLPALI_API_KEY = os.getenv("COLPALI_API_KEY", "EMPTY")

# Test timeouts
HEALTH_TIMEOUT = 5.0
EMBEDDING_TIMEOUT = 60.0

# Expected embedding dimensions
EXPECTED_PATCH_DIM = 128
MIN_PATCHES = 32  # Minimum patches for small images
MAX_PATCHES = 2048  # Maximum patches for large images


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def colpali_config():
    """Create ColPali configuration for testing."""
    return {
        "colpali_base_url": TEST_COLPALI_BASE_URL,
        "colpali_api_key": TEST_COLPALI_API_KEY,
        "colpali_enabled": True,
    }


@pytest.fixture
def colpali_embedder(colpali_config):
    """Create ColPaliEmbedder instance."""
    return ColPaliEmbedder(colpali_config)


@pytest.fixture
def sample_image() -> Tuple[str, Image.Image]:
    """Create a simple test image in memory."""
    # Create a 800x600 image with some text
    img = Image.new('RGB', (800, 600), color='white')
    draw = ImageDraw.Draw(img)
    
    # Draw some content
    draw.rectangle([50, 50, 750, 550], outline='black', width=3)
    draw.text((100, 100), "ColPali Test Document", fill='black')
    draw.text((100, 200), "This is a sample page for testing", fill='black')
    draw.text((100, 300), "visual document embeddings", fill='black')
    
    # Save to temporary file
    temp_path = "/tmp/colpali_test_image.png"
    img.save(temp_path, "PNG")
    
    return temp_path, img


@pytest.fixture
def sample_pdf_image() -> str:
    """Create a PDF-like test image (A4 aspect ratio)."""
    # A4 aspect ratio: 210mm x 297mm ≈ 595x842 pixels at 72dpi
    img = Image.new('RGB', (595, 842), color='white')
    draw = ImageDraw.Draw(img)
    
    # Draw document structure
    draw.rectangle([50, 50, 545, 792], outline='black', width=2)
    draw.text((70, 70), "Document Title", fill='black')
    draw.line([70, 100, 525, 100], fill='black', width=1)
    
    # Add some text content
    for i in range(5):
        y = 120 + i * 50
        draw.text((70, y), f"Line {i+1}: Sample document content", fill='black')
    
    temp_path = "/tmp/colpali_test_pdf_page.png"
    img.save(temp_path, "PNG")
    
    return temp_path


@pytest.fixture
def multiple_images() -> List[str]:
    """Create multiple test images."""
    images = []
    for i in range(3):
        img = Image.new('RGB', (600, 400), color='white')
        draw = ImageDraw.Draw(img)
        draw.rectangle([25, 25, 575, 375], outline='blue', width=2)
        draw.text((50, 50), f"Test Image {i+1}", fill='blue')
        
        temp_path = f"/tmp/colpali_test_image_{i}.png"
        img.save(temp_path, "PNG")
        images.append(temp_path)
    
    return images


# ============================================================================
# Test 1: Service Availability
# ============================================================================

class TestServiceAvailability:
    """Test ColPali service availability and health checks."""
    
    @pytest.mark.asyncio
    async def test_health_check_endpoint(self):
        """Test that health check endpoint is accessible."""
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            try:
                response = await client.get(TEST_COLPALI_HEALTH_URL)
                assert response.status_code == 200, f"Health check failed: {response.status_code}"
                
                data = response.json()
                assert "status" in data
                assert data["status"] == "healthy"
                assert "model" in data
                assert "device" in data
                
                print(f"\n✓ Health check passed")
                print(f"  Model: {data.get('model')}")
                print(f"  Device: {data.get('device')}")
            except httpx.ConnectError as e:
                pytest.skip(f"ColPali service not available: {e}")
    
    @pytest.mark.asyncio
    async def test_embedder_health_check(self, colpali_embedder):
        """Test health check through ColPaliEmbedder class."""
        is_healthy = await colpali_embedder.check_health()
        
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        assert is_healthy, "ColPali service health check failed"
        print("\n✓ ColPaliEmbedder health check passed")
    
    @pytest.mark.asyncio
    async def test_service_timeout(self):
        """Test that health check handles timeouts properly."""
        # Use a very short timeout to test timeout handling
        async with httpx.AsyncClient(timeout=0.001) as client:
            try:
                response = await client.get(TEST_COLPALI_HEALTH_URL)
                # If we get here, the service responded very fast (good!)
                assert response.status_code == 200
            except httpx.TimeoutException:
                # Expected - timeout handling works
                print("\n✓ Timeout handling works correctly")
                pass


# ============================================================================
# Test 2: Image Encoding and Processing
# ============================================================================

class TestImageProcessing:
    """Test image encoding and base64 conversion."""
    
    def test_image_to_base64(self, sample_image):
        """Test converting image to base64."""
        image_path, _ = sample_image
        
        # Read and encode
        with open(image_path, "rb") as f:
            image_data = f.read()
            encoded = base64.b64encode(image_data).decode("utf-8")
        
        assert len(encoded) > 0
        assert isinstance(encoded, str)
        
        # Verify it can be decoded back
        decoded_data = base64.b64decode(encoded)
        assert decoded_data == image_data
        
        print(f"\n✓ Image encoded to base64: {len(encoded)} chars")
    
    def test_multiple_image_encoding(self, multiple_images):
        """Test encoding multiple images."""
        encoded_images = []
        
        for image_path in multiple_images:
            with open(image_path, "rb") as f:
                image_data = f.read()
                encoded = base64.b64encode(image_data).decode("utf-8")
                encoded_images.append(encoded)
        
        assert len(encoded_images) == len(multiple_images)
        assert all(len(e) > 0 for e in encoded_images)
        
        print(f"\n✓ Encoded {len(encoded_images)} images")
    
    def test_image_size_limits(self):
        """Test encoding images of various sizes."""
        sizes = [(100, 100), (800, 600), (1920, 1080), (2048, 2048)]
        
        for width, height in sizes:
            img = Image.new('RGB', (width, height), color='white')
            temp_path = f"/tmp/colpali_test_{width}x{height}.png"
            img.save(temp_path, "PNG")
            
            with open(temp_path, "rb") as f:
                image_data = f.read()
                encoded = base64.b64encode(image_data).decode("utf-8")
            
            assert len(encoded) > 0
            print(f"  {width}x{height}: {len(encoded)} chars")
            
            # Cleanup
            os.remove(temp_path)
        
        print(f"\n✓ Tested {len(sizes)} different image sizes")


# ============================================================================
# Test 3: Embedding Generation
# ============================================================================

class TestEmbeddingGeneration:
    """Test ColPali embedding generation."""
    
    @pytest.mark.asyncio
    async def test_single_image_embedding(self, colpali_embedder, sample_image):
        """Test generating embedding for a single image."""
        image_path, _ = sample_image
        
        # Check health first
        is_healthy = await colpali_embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        # Generate embedding
        embeddings = await colpali_embedder.embed_pages([image_path])
        
        assert embeddings is not None, "Embedding generation returned None"
        assert len(embeddings) == 1, f"Expected 1 embedding, got {len(embeddings)}"
        
        # Check embedding structure
        page_embedding = embeddings[0]
        assert isinstance(page_embedding, list), "Embedding should be a list of patches"
        assert len(page_embedding) >= MIN_PATCHES, f"Too few patches: {len(page_embedding)}"
        assert len(page_embedding) <= MAX_PATCHES, f"Too many patches: {len(page_embedding)}"
        
        # Check patch dimensions
        for patch in page_embedding:
            assert isinstance(patch, list), "Each patch should be a list"
            assert len(patch) == EXPECTED_PATCH_DIM, f"Expected {EXPECTED_PATCH_DIM} dims, got {len(patch)}"
            assert all(isinstance(v, (int, float)) for v in patch), "Patch values should be numeric"
        
        print(f"\n✓ Generated embedding for single image")
        print(f"  Patches: {len(page_embedding)}")
        print(f"  Dimensions per patch: {len(page_embedding[0])}")
        print(f"  Total values: {len(page_embedding) * len(page_embedding[0])}")
    
    @pytest.mark.asyncio
    async def test_multiple_image_embeddings(self, colpali_embedder, multiple_images):
        """Test generating embeddings for multiple images."""
        is_healthy = await colpali_embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        # Generate embeddings
        start_time = time.time()
        embeddings = await colpali_embedder.embed_pages(multiple_images)
        elapsed = time.time() - start_time
        
        assert embeddings is not None
        assert len(embeddings) == len(multiple_images)
        
        # Check each embedding
        for i, page_embedding in enumerate(embeddings):
            assert isinstance(page_embedding, list)
            assert len(page_embedding) >= MIN_PATCHES
            
            for patch in page_embedding:
                assert len(patch) == EXPECTED_PATCH_DIM
        
        print(f"\n✓ Generated embeddings for {len(multiple_images)} images")
        print(f"  Time: {elapsed:.2f}s ({elapsed/len(multiple_images):.2f}s per image)")
    
    @pytest.mark.asyncio
    async def test_pdf_page_embedding(self, colpali_embedder, sample_pdf_image):
        """Test embedding for PDF-like page image."""
        is_healthy = await colpali_embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        embeddings = await colpali_embedder.embed_pages([sample_pdf_image])
        
        assert embeddings is not None
        assert len(embeddings) == 1
        
        page_embedding = embeddings[0]
        print(f"\n✓ Generated PDF page embedding")
        print(f"  Patches: {len(page_embedding)}")
        print(f"  Total dimensions: {len(page_embedding) * EXPECTED_PATCH_DIM}")


# ============================================================================
# Test 4: API Compatibility
# ============================================================================

class TestAPICompatibility:
    """Test OpenAI-compatible API endpoints."""
    
    @pytest.mark.asyncio
    async def test_embeddings_endpoint_structure(self, sample_image):
        """Test the structure of the embeddings API response."""
        image_path, _ = sample_image
        
        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = f.read()
            encoded_image = base64.b64encode(image_data).decode("utf-8")
        
        # Make API request
        async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{TEST_COLPALI_BASE_URL}/embeddings",
                    json={
                        "input": [encoded_image],
                        "model": "colpali",
                        "encoding_format": "float",
                    },
                    headers={
                        "Authorization": f"Bearer {TEST_COLPALI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                )
                
                assert response.status_code == 200, f"API request failed: {response.status_code}"
                
                data = response.json()
                
                # Check response structure (OpenAI-compatible)
                assert "object" in data
                assert data["object"] == "list"
                assert "data" in data
                assert "model" in data
                assert "usage" in data
                
                # Check data array
                assert isinstance(data["data"], list)
                assert len(data["data"]) == 1
                
                # Check embedding data
                embedding_data = data["data"][0]
                assert "object" in embedding_data
                assert embedding_data["object"] == "embedding"
                assert "embedding" in embedding_data
                assert "index" in embedding_data
                assert embedding_data["index"] == 0
                
                # Check embedding values
                embedding = embedding_data["embedding"]
                assert isinstance(embedding, list)
                assert len(embedding) > 0
                assert len(embedding) % EXPECTED_PATCH_DIM == 0
                
                print(f"\n✓ API response structure is OpenAI-compatible")
                print(f"  Embedding length: {len(embedding)}")
                
            except httpx.ConnectError as e:
                pytest.skip(f"ColPali service not available: {e}")
    
    @pytest.mark.asyncio
    async def test_batch_embeddings(self, multiple_images):
        """Test batch embedding generation via API."""
        # Encode all images
        encoded_images = []
        for image_path in multiple_images:
            with open(image_path, "rb") as f:
                image_data = f.read()
                encoded_image = base64.b64encode(image_data).decode("utf-8")
                encoded_images.append(encoded_image)
        
        # Make batch API request
        async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as client:
            try:
                response = await client.post(
                    f"{TEST_COLPALI_BASE_URL}/embeddings",
                    json={
                        "input": encoded_images,
                        "model": "colpali",
                        "encoding_format": "float",
                    },
                    headers={
                        "Authorization": f"Bearer {TEST_COLPALI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                )
                
                assert response.status_code == 200
                
                data = response.json()
                assert len(data["data"]) == len(multiple_images)
                
                # Check indices are correct
                for i, item in enumerate(data["data"]):
                    assert item["index"] == i
                
                print(f"\n✓ Batch embeddings successful")
                print(f"  Images: {len(multiple_images)}")
                
            except httpx.ConnectError as e:
                pytest.skip(f"ColPali service not available: {e}")


# ============================================================================
# Test 5: Error Handling
# ============================================================================

class TestErrorHandling:
    """Test error handling and edge cases."""
    
    @pytest.mark.asyncio
    async def test_empty_image_list(self, colpali_embedder):
        """Test handling of empty image list."""
        embeddings = await colpali_embedder.embed_pages([])
        assert embeddings is None
        print("\n✓ Empty list handled correctly")
    
    @pytest.mark.asyncio
    async def test_nonexistent_image(self, colpali_embedder):
        """Test handling of nonexistent image file."""
        is_healthy = await colpali_embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        embeddings = await colpali_embedder.embed_pages(["/nonexistent/image.png"])
        assert embeddings is None
        print("\n✓ Nonexistent image handled correctly")
    
    @pytest.mark.asyncio
    async def test_disabled_embedder(self, colpali_config):
        """Test embedder when disabled."""
        config = colpali_config.copy()
        config["colpali_enabled"] = False
        
        embedder = ColPaliEmbedder(config)
        embeddings = await embedder.embed_pages(["/tmp/test.png"])
        
        assert embeddings is None
        print("\n✓ Disabled embedder returns None")
    
    @pytest.mark.asyncio
    async def test_invalid_base_url(self):
        """Test handling of invalid base URL."""
        config = {
            "colpali_base_url": "http://invalid-host:9999/v1",
            "colpali_api_key": "EMPTY",
            "colpali_enabled": True,
        }
        
        embedder = ColPaliEmbedder(config)
        is_healthy = await embedder.check_health()
        
        assert is_healthy is False
        print("\n✓ Invalid URL handled correctly")
    
    @pytest.mark.asyncio
    async def test_corrupted_image_data(self):
        """Test handling of corrupted image data."""
        # Create a file with invalid image data
        corrupted_path = "/tmp/colpali_corrupted.png"
        with open(corrupted_path, "wb") as f:
            f.write(b"NOT A VALID PNG FILE")
        
        async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as client:
            try:
                # Try to encode the corrupted data
                with open(corrupted_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
                
                response = await client.post(
                    f"{TEST_COLPALI_BASE_URL}/embeddings",
                    json={
                        "input": [encoded],
                        "model": "colpali",
                    },
                    headers={"Authorization": f"Bearer {TEST_COLPALI_API_KEY}"},
                )
                
                # Should get an error response
                assert response.status_code in [400, 422, 500]
                print(f"\n✓ Corrupted image rejected with status {response.status_code}")
                
            except httpx.ConnectError:
                pytest.skip("ColPali service not available")
            finally:
                os.remove(corrupted_path)


# ============================================================================
# Test 6: Performance Benchmarks
# ============================================================================

class TestPerformance:
    """Performance and load testing."""
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_embedding_latency(self, colpali_embedder, sample_image):
        """Measure embedding generation latency."""
        is_healthy = await colpali_embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        image_path, _ = sample_image
        
        # Warm-up request
        await colpali_embedder.embed_pages([image_path])
        
        # Measure latency over multiple requests
        latencies = []
        num_requests = 5
        
        for _ in range(num_requests):
            start_time = time.time()
            await colpali_embedder.embed_pages([image_path])
            latency = time.time() - start_time
            latencies.append(latency)
        
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        
        print(f"\n✓ Embedding latency benchmark ({num_requests} requests)")
        print(f"  Average: {avg_latency:.3f}s")
        print(f"  Min: {min_latency:.3f}s")
        print(f"  Max: {max_latency:.3f}s")
        
        # Assert reasonable performance (< 5 seconds per image)
        assert avg_latency < 5.0, f"Average latency too high: {avg_latency:.3f}s"
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_batch_performance(self, colpali_embedder):
        """Test performance with different batch sizes."""
        is_healthy = await colpali_embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        batch_sizes = [1, 2, 4, 8]
        results = []
        
        for batch_size in batch_sizes:
            # Create test images
            images = []
            for i in range(batch_size):
                img = Image.new('RGB', (600, 400), color='white')
                temp_path = f"/tmp/colpali_batch_test_{i}.png"
                img.save(temp_path, "PNG")
                images.append(temp_path)
            
            # Measure time
            start_time = time.time()
            await colpali_embedder.embed_pages(images)
            elapsed = time.time() - start_time
            
            time_per_image = elapsed / batch_size
            results.append((batch_size, elapsed, time_per_image))
            
            # Cleanup
            for img_path in images:
                os.remove(img_path)
        
        print(f"\n✓ Batch performance benchmark")
        print(f"  {'Batch':<10} {'Total (s)':<12} {'Per Image (s)':<12}")
        print(f"  {'-'*34}")
        for batch_size, total_time, per_image in results:
            print(f"  {batch_size:<10} {total_time:<12.3f} {per_image:<12.3f}")
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_memory_usage(self, colpali_embedder, sample_image):
        """Test that embeddings can be processed without memory issues."""
        is_healthy = await colpali_embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        image_path, _ = sample_image
        
        # Generate embeddings multiple times
        num_iterations = 10
        for i in range(num_iterations):
            embeddings = await colpali_embedder.embed_pages([image_path])
            assert embeddings is not None
            # Don't keep references to allow garbage collection
            del embeddings
        
        print(f"\n✓ Memory test: {num_iterations} iterations completed")


# ============================================================================
# Test 7: Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests with actual configuration."""
    
    def test_config_loading(self):
        """Test that configuration loads correctly."""
        config = Config()
        
        assert hasattr(config, 'colpali_base_url')
        assert hasattr(config, 'colpali_api_key')
        assert hasattr(config, 'colpali_enabled')
        
        print(f"\n✓ Configuration loaded")
        print(f"  Base URL: {config.colpali_base_url}")
        print(f"  Enabled: {config.colpali_enabled}")
    
    @pytest.mark.asyncio
    async def test_full_workflow(self, sample_pdf_image):
        """Test the complete workflow from config to embeddings."""
        # 1. Load config
        config = Config()
        config_dict = config.to_dict()
        
        # 2. Create embedder
        embedder = ColPaliEmbedder(config_dict)
        
        # 3. Check health
        is_healthy = await embedder.check_health()
        if not is_healthy:
            pytest.skip("ColPali service not available")
        
        # 4. Generate embeddings
        embeddings = await embedder.embed_pages([sample_pdf_image])
        
        # 5. Validate results
        assert embeddings is not None
        assert len(embeddings) == 1
        assert len(embeddings[0]) >= MIN_PATCHES
        
        print(f"\n✓ Full workflow successful")
        print(f"  Health: OK")
        print(f"  Embeddings: {len(embeddings)} pages")
        print(f"  Patches per page: {len(embeddings[0])}")


# ============================================================================
# Diagnostic Utilities
# ============================================================================

@pytest.mark.asyncio
async def test_diagnostic_report():
    """Generate a comprehensive diagnostic report for ColPali."""
    print("\n" + "="*70)
    print("ColPali Diagnostic Report")
    print("="*70)
    
    # 1. Configuration
    print("\n1. CONFIGURATION")
    print(f"   Base URL: {TEST_COLPALI_BASE_URL}")
    print(f"   Health URL: {TEST_COLPALI_HEALTH_URL}")
    print(f"   API Key: {'Set' if TEST_COLPALI_API_KEY != 'EMPTY' else 'Not set'}")
    
    # 2. Service Health
    print("\n2. SERVICE HEALTH")
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            response = await client.get(TEST_COLPALI_HEALTH_URL)
            if response.status_code == 200:
                data = response.json()
                print(f"   Status: ✓ {data.get('status', 'unknown')}")
                print(f"   Model: {data.get('model', 'unknown')}")
                print(f"   Device: {data.get('device', 'unknown')}")
            else:
                print(f"   Status: ✗ HTTP {response.status_code}")
                print(f"   Response: {response.text[:200]}")
    except httpx.ConnectError as e:
        print(f"   Status: ✗ Connection failed")
        print(f"   Error: {e}")
    except Exception as e:
        print(f"   Status: ✗ Error")
        print(f"   Error: {e}")
    
    # 3. Test Embedding
    print("\n3. TEST EMBEDDING")
    try:
        # Create a simple test image
        img = Image.new('RGB', (400, 300), color='white')
        temp_path = "/tmp/colpali_diagnostic.png"
        img.save(temp_path, "PNG")
        
        config = {
            "colpali_base_url": TEST_COLPALI_BASE_URL,
            "colpali_api_key": TEST_COLPALI_API_KEY,
            "colpali_enabled": True,
        }
        embedder = ColPaliEmbedder(config)
        
        start_time = time.time()
        embeddings = await embedder.embed_pages([temp_path])
        elapsed = time.time() - start_time
        
        if embeddings:
            print(f"   Status: ✓ Embedding generated")
            print(f"   Time: {elapsed:.3f}s")
            print(f"   Patches: {len(embeddings[0])}")
            print(f"   Dimensions: {len(embeddings[0][0])}")
        else:
            print(f"   Status: ✗ Embedding generation failed")
        
        os.remove(temp_path)
    except Exception as e:
        print(f"   Status: ✗ Error")
        print(f"   Error: {e}")
    
    # 4. Recommendations
    print("\n4. RECOMMENDATIONS")
    
    # Check if service is accessible
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            response = await client.get(TEST_COLPALI_HEALTH_URL)
            if response.status_code != 200:
                print("   • Check that ColPali service is running:")
                print("     systemctl status colpali")
                print("   • Check service logs:")
                print("     journalctl -u colpali -n 50 --no-pager")
    except httpx.ConnectError:
        print("   • ColPali service is not accessible")
        print("   • Verify the service is running on vllm-lxc container")
        print("   • Check network connectivity:")
        print("     ping 10.96.200.208")
        print("     curl http://10.96.200.208:9006/health")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    # Run diagnostic report
    import asyncio
    asyncio.run(test_diagnostic_report())

