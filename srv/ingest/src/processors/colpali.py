"""
ColPali PDF page embedder for visual search.

Generates multi-vector embeddings for PDF page images using ColPali v1.3.
ColPali is based on PaliGemma-3B with ColBERT-style multi-vector representations.
Each page produces 128 patch embeddings of 128 dimensions each.

Reference: https://huggingface.co/vidore/colpali-v1.3
"""

import base64
import os
from typing import List, Optional

import httpx
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
        self.colpali_base_url = config.get("colpali_base_url", "http://vllm-lxc:8000/v1")
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
    ) -> Optional[List[List[List[float]]]]:
        """
        Generate ColPali embeddings for PDF page images.
        
        Args:
            page_image_paths: List of paths to page image files
        
        Returns:
            List of page embeddings, each page has 128 patch embeddings (128 dims each)
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
            for image_path in page_image_paths:
                try:
                    with open(image_path, "rb") as f:
                        image_data = f.read()
                        encoded_image = base64.b64encode(image_data).decode("utf-8")
                        encoded_images.append(encoded_image)
                except Exception as e:
                    logger.warning(
                        "Failed to read image file",
                        image_path=image_path,
                        error=str(e),
                    )
                    return None
            
            # Call ColPali API
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.colpali_base_url}/embeddings",
                    json={
                        "input": encoded_images,
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
                        "ColPali API request failed",
                        status_code=response.status_code,
                        response=response.text,
                    )
                    return None
                
                result = response.json()
                
                # Extract embeddings from response
                # Response format: {"data": [{"embedding": [...], "index": 0}, ...]}
                embeddings = []
                for item in sorted(result.get("data", []), key=lambda x: x["index"]):
                    embedding = item["embedding"]
                    # ColPali returns flattened multi-vector embeddings
                    # Reshape to [128 patches, 128 dims]
                    num_patches = 128
                    patch_dim = 128
                    if len(embedding) == num_patches * patch_dim:
                        reshaped = [
                            embedding[i * patch_dim : (i + 1) * patch_dim]
                            for i in range(num_patches)
                        ]
                        embeddings.append(reshaped)
                    else:
                        logger.warning(
                            "Unexpected embedding dimension",
                            expected=num_patches * patch_dim,
                            actual=len(embedding),
                        )
                        return None
                
                logger.info(
                    "ColPali embeddings generated successfully",
                    page_count=len(embeddings),
                )
                
                return embeddings
        
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

