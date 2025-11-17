"""
Reranking service using cross-encoder models.
"""

import structlog
from typing import List, Dict, Tuple
from sentence_transformers import CrossEncoder

logger = structlog.get_logger()


class RerankingService:
    """Service for reranking search results using cross-encoders."""
    
    def __init__(self, config: Dict):
        """Initialize reranking service."""
        self.config = config
        self.model_name = config.get("reranker_model", "BAAI/bge-reranker-v2-m3")
        self.device = config.get("reranker_device", "cpu")
        self.enabled = config.get("enable_reranking", True)
        self.model = None
        
        if self.enabled:
            self._load_model()
    
    def _load_model(self):
        """Load the reranker model."""
        try:
            logger.info(
                "Loading reranker model",
                model=self.model_name,
                device=self.device,
            )
            
            self.model = CrossEncoder(
                self.model_name,
                device=self.device,
                max_length=512,
            )
            
            logger.info("Reranker model loaded successfully")
        except Exception as e:
            logger.error(
                "Failed to load reranker model",
                error=str(e),
                exc_info=True,
            )
            self.enabled = False
            raise
    
    def rerank(
        self,
        query: str,
        results: List[Dict],
        top_k: int = None,
    ) -> List[Dict]:
        """
        Rerank search results using cross-encoder.
        
        Args:
            query: Search query
            results: List of search results with 'text' field
            top_k: Number of top results to return (None = all)
        
        Returns:
            Reranked results with 'rerank_score' field
        """
        if not self.enabled or not results:
            return results
        
        try:
            logger.info(
                "Reranking results",
                query=query[:100],
                num_results=len(results),
                top_k=top_k,
            )
            
            # Create query-document pairs
            pairs = [(query, result["text"]) for result in results]
            
            # Get reranking scores
            scores = self.model.predict(pairs)
            
            # Add rerank scores to results
            reranked_results = []
            for result, score in zip(results, scores):
                reranked_result = {
                    **result,
                    "rerank_score": float(score),
                }
                reranked_results.append(reranked_result)
            
            # Sort by rerank score
            reranked_results.sort(key=lambda x: x["rerank_score"], reverse=True)
            
            # Take top-k if specified
            if top_k is not None:
                reranked_results = reranked_results[:top_k]
            
            logger.info(
                "Reranking completed",
                result_count=len(reranked_results),
            )
            
            return reranked_results
        
        except Exception as e:
            logger.error(
                "Reranking failed",
                error=str(e),
                exc_info=True,
            )
            # Return original results on failure
            return results
    
    def compute_pairwise_scores(
        self,
        query: str,
        documents: List[str],
    ) -> List[float]:
        """
        Compute reranking scores for query-document pairs.
        
        Args:
            query: Search query
            documents: List of document texts
        
        Returns:
            List of scores (one per document)
        """
        if not self.enabled:
            return [0.0] * len(documents)
        
        try:
            pairs = [(query, doc) for doc in documents]
            scores = self.model.predict(pairs)
            return [float(s) for s in scores]
        except Exception as e:
            logger.error(
                "Failed to compute pairwise scores",
                error=str(e),
            )
            return [0.0] * len(documents)
    
    def explain_score(
        self,
        query: str,
        document: str,
    ) -> Dict:
        """
        Explain the reranking score for a query-document pair.
        
        Args:
            query: Search query
            document: Document text
        
        Returns:
            Explanation dictionary with score and breakdown
        """
        if not self.enabled:
            return {"score": 0.0, "explanation": "Reranking disabled"}
        
        try:
            score = self.model.predict([(query, document)])[0]
            
            return {
                "score": float(score),
                "model": self.model_name,
                "explanation": (
                    f"Cross-encoder relevance score between query and document. "
                    f"Higher scores indicate stronger semantic relevance."
                ),
            }
        except Exception as e:
            logger.error(
                "Failed to explain score",
                error=str(e),
            )
            return {"score": 0.0, "explanation": f"Error: {str(e)}"}
    
    def health_check(self) -> bool:
        """Check if reranker is healthy."""
        if not self.enabled:
            return True  # Not enabled, so not unhealthy
        
        try:
            # Test with a simple pair
            test_score = self.model.predict([("test query", "test document")])[0]
            return True
        except Exception as e:
            logger.error("Reranker health check failed", error=str(e))
            return False

