"""
ColPali PDF page embedder for visual search.

Generates pooled embeddings for PDF page images using ColPali v1.3.
ColPali is based on PaliGemma-3B with ColBERT-style multi-vector representations.
Each page produces multiple patch embeddings (128 dims each) which are mean-pooled
into a single 128-d vector for efficient storage and retrieval.

Reference: https://huggingface.co/vidore/colpali-v1.3
"""

import base64
import os
from typing import List, Optional

import httpx
import numpy as np
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger()


class ColPaliEmbedder:
    """Generate ColPali visual embeddings for PDF pages."""
    
    def __init__(self, config: dict):
        """
        Initialize ColPali embedder.
        
        Args:
            config: Configuration dictionary with colpali_base_url
        """
        self.config = config
        self.colpali_base_url = config.get("colpali_base_url", "http://vllm-lxc:9006/v1")
        self.api_key = config.get("colpali_api_key", "EMPTY")
        self.enabled = config.get("colpali_enabled", True)
        
        logger.info(
            "ColPali embedder initialized",
            base_url=self.colpali_base_url,
            enabled=self.enabled,
            api_key_set=bool(self.api_key and self.api_key != "EMPTY"),
        )
    
    async def embed_pages(
        self,
        page_image_paths: List[str],
    ) -> Optional[List[List[float]]]:
        """
        Generate ColPali embeddings for PDF page images.
        
        Args:
            page_image_paths: List of paths to page image files
        
        Returns:
            List of page embeddings, each page is a single 128-d pooled vector
            Returns None if ColPali not available or disabled
        """
        if not self.enabled or not page_image_paths:
            return None
        
        logger.info(
            "Generating ColPali embeddings",
            page_count=len(page_image_paths),
        )
        
        try:
            # Check if ColPali service is available
            health_check = await self.check_health()
            if not health_check:
                logger.warning(
                    "ColPali service not available, skipping visual embeddings",
                    base_url=self.colpali_base_url,
                    health_endpoint=f"{self.colpali_base_url.replace('/v1', '')}/health",
                )
                return None
            
            logger.info("ColPali service health check passed", base_url=self.colpali_base_url)
            
            # Read and encode images as base64
            encoded_images = []
            valid_page_indices = []
            for idx, image_path in enumerate(page_image_paths):
                try:
                    # Check if file exists and has content
                    if not os.path.exists(image_path):
                        logger.warning(
                            "Image file not found, skipping",
                            image_path=image_path,
                            page_index=idx,
                        )
                        continue
                    
                    file_size = os.path.getsize(image_path)
                    if file_size == 0:
                        logger.warning(
                            "Empty image file, skipping",
                            image_path=image_path,
                            page_index=idx,
                        )
                        continue
                    
                    # Minimum valid image size (at least a few KB for a real image)
                    if file_size < 100:
                        logger.warning(
                            "Image file too small, likely corrupted",
                            image_path=image_path,
                            file_size=file_size,
                            page_index=idx,
                        )
                        continue
                    
                    with open(image_path, "rb") as f:
                        image_data = f.read()
                        encoded_image = base64.b64encode(image_data).decode("utf-8")
                        encoded_images.append(encoded_image)
                        valid_page_indices.append(idx)
                except Exception as e:
                    logger.warning(
                        "Failed to read image file, skipping",
                        image_path=image_path,
                        page_index=idx,
                        error=str(e),
                    )
                    continue
            
            if not encoded_images:
                logger.warning(
                    "No valid images to process",
                    original_count=len(page_image_paths),
                )
                return None
            
            logger.info(
                "Prepared images for ColPali",
                valid_count=len(encoded_images),
                skipped_count=len(page_image_paths) - len(encoded_images),
            )
            
            # Process in batches to avoid OOM on GPU
            # ColPali (PaliGemma-3B) uses ~5GB VRAM, each page adds ~200MB
            batch_size = int(os.getenv("COLPALI_BATCH_SIZE", "3"))  # Conservative default
            all_embeddings = []
            pooling_method = self.config.get("colpali_pooling_method", "mean")
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                for batch_start in range(0, len(encoded_images), batch_size):
                    batch_end = min(batch_start + batch_size, len(encoded_images))
                    batch_images = encoded_images[batch_start:batch_end]
                    
                    logger.debug(
                        "Processing ColPali batch",
                        batch_start=batch_start,
                        batch_size=len(batch_images),
                        total_pages=len(encoded_images),
                    )
                    
                    response = await client.post(
                        f"{self.colpali_base_url}/embeddings",
                        json={
                            "input": batch_images,
                            "model": "colpali",
                            "encoding_format": "float",
                        },
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    
                    if response.status_code != 200:
                        logger.warning(
                            "ColPali API batch failed",
                            status_code=response.status_code,
                            response=response.text,
                            batch_start=batch_start,
                        )
                        # Continue with other batches instead of failing entirely
                        continue
                    
                    result = response.json()
                    
                    # Extract embeddings from response
                    # Response format: {"data": [{"embedding": [...], "index": 0}, ...]}
                    patch_dim = 128
                    
                    for item in sorted(result.get("data", []), key=lambda x: x["index"]):
                        embedding = item["embedding"]
                        # ColPali returns flattened multi-vector embeddings
                        # Each patch has 128 dimensions
                        # Number of patches varies based on image size (typically 128-1024+)
                        
                        # Check if embedding length is divisible by patch_dim
                        if len(embedding) % patch_dim != 0:
                            logger.warning(
                                "Embedding length not divisible by patch dimension",
                                embedding_length=len(embedding),
                                patch_dim=patch_dim,
                            )
                            continue
                        
                        # Calculate actual number of patches
                        num_patches = len(embedding) // patch_dim
                        
                        # Reshape to [num_patches, patch_dim]
                        reshaped = np.array(embedding).reshape(num_patches, patch_dim)
                        
                        # Pool patches into single vector using mean pooling
                        # This preserves overall page "gist" while reducing storage
                        if pooling_method == "max":
                            pooled_vector = np.max(reshaped, axis=0)
                        else:  # mean (default)
                            pooled_vector = np.mean(reshaped, axis=0)
                        
                        # Convert to list for JSON serialization
                        all_embeddings.append(pooled_vector.tolist())
            
            if not all_embeddings:
                logger.warning("No ColPali embeddings generated from any batch")
                return None
            
            logger.info(
                "ColPali embeddings generated and pooled successfully",
                page_count=len(all_embeddings),
                pooling_method=pooling_method,
            )
            
            return all_embeddings
        
        except Exception as e:
            logger.warning(
                "ColPali embedding generation failed",
                error=str(e),
                page_count=len(page_image_paths),
                exc_info=True,
            )
            return None
    
    async def check_health(self) -> bool:
        """Check if ColPali service is available."""
        health_url = f"{self.colpali_base_url.replace('/v1', '')}/health"
        try:
            logger.debug("Checking ColPali health", url=health_url)
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(health_url)
                is_healthy = response.status_code == 200
                
                if is_healthy:
                    logger.debug("ColPali health check succeeded", status_code=response.status_code)
                else:
                    logger.warning(
                        "ColPali health check returned non-200 status",
                        status_code=response.status_code,
                        response_text=response.text[:200] if response.text else None,
                    )
                
                return is_healthy
        except httpx.ConnectError as e:
            logger.warning(
                "ColPali health check failed - connection error",
                url=health_url,
                error=str(e),
                error_type="ConnectError",
            )
            return False
        except httpx.TimeoutException as e:
            logger.warning(
                "ColPali health check failed - timeout",
                url=health_url,
                error=str(e),
                error_type="TimeoutException",
            )
            return False
        except Exception as e:
            logger.warning(
                "ColPali health check failed - unexpected error",
                url=health_url,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            return False

