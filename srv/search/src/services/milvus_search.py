"""
Milvus search service with hybrid search support.
"""

import structlog
from typing import List, Dict, Optional
from pymilvus import Collection, connections

logger = structlog.get_logger()


class MilvusSearchService:
    """Service for searching in Milvus vector database."""
    
    def __init__(self, config: Dict):
        """Initialize Milvus search service."""
        self.config = config
        self.host = config.get("milvus_host", "localhost")
        self.port = config.get("milvus_port", 19530)
        self.collection_name = config.get("milvus_collection", "document_embeddings")
        self.connected = False
        self.collection = None
    
    def connect(self):
        """Connect to Milvus."""
        if self.connected:
            return
        
        try:
            connections.connect(
                alias="default",
                host=self.host,
                port=self.port,
            )
            self.collection = Collection(self.collection_name)
            self.collection.load()
            self.connected = True
            
            logger.info(
                "Connected to Milvus",
                host=self.host,
                port=self.port,
                collection=self.collection_name,
            )
        except Exception as e:
            logger.error("Failed to connect to Milvus", error=str(e), exc_info=True)
            raise
    
    def disconnect(self):
        """Disconnect from Milvus."""
        if self.connected:
            try:
                connections.disconnect(alias="default")
                self.connected = False
                logger.info("Disconnected from Milvus")
            except Exception as e:
                logger.error("Error disconnecting from Milvus", error=str(e))
    
    def keyword_search(
        self,
        query: str,
        user_id: str,
        top_k: int = 10,
        filters: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Pure BM25 keyword search.
        
        Args:
            query: Search query string
            user_id: User ID for permission filtering
            top_k: Number of results to return
            filters: Additional filters
        
        Returns:
            List of search results with scores
        """
        if not self.connected:
            self.connect()
        
        try:
            logger.info(
                "Performing keyword search",
                user_id=user_id,
                query=query[:100],
                top_k=top_k,
            )
            
            # Build filter expression
            filter_expr = f'user_id == "{user_id}" && modality == "text"'
            if filters and filters.get("file_ids"):
                file_ids_str = '", "'.join(filters["file_ids"])
                filter_expr += f' && file_id in ["{file_ids_str}"]'
            
            # BM25 search using text_sparse field
            # Note: This assumes the text_sparse field is populated with BM25 embeddings
            # during ingestion using Milvus BM25 function
            search_params = {
                "metric_type": "IP",  # Inner product for sparse vectors
                "params": {},
            }
            
            results = self.collection.search(
                data=[[query]],  # Text query for BM25
                anns_field="text_sparse",
                param=search_params,
                limit=top_k,
                expr=filter_expr,
                output_fields=[
                    "file_id",
                    "chunk_index",
                    "page_number",
                    "text",
                    "metadata",
                ],
            )
            
            # Process results
            search_results = self._process_results(results, include_sparse_score=True)
            
            logger.info(
                "Keyword search completed",
                user_id=user_id,
                result_count=len(search_results),
            )
            
            return search_results
        
        except Exception as e:
            logger.error(
                "Keyword search failed",
                user_id=user_id,
                error=str(e),
                exc_info=True,
            )
            raise
    
    def semantic_search(
        self,
        query_embedding: List[float],
        user_id: str,
        top_k: int = 10,
        filters: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Pure dense vector semantic search.
        
        Args:
            query_embedding: Dense embedding vector
            user_id: User ID for permission filtering
            top_k: Number of results to return
            filters: Additional filters
        
        Returns:
            List of search results with scores
        """
        if not self.connected:
            self.connect()
        
        try:
            logger.info(
                "Performing semantic search",
                user_id=user_id,
                top_k=top_k,
            )
            
            # Build filter expression
            filter_expr = f'user_id == "{user_id}" && modality == "text"'
            if filters and filters.get("file_ids"):
                file_ids_str = '", "'.join(filters["file_ids"])
                filter_expr += f' && file_id in ["{file_ids_str}"]'
            
            # Dense vector search
            search_params = {
                "metric_type": "COSINE",
                "params": {"nprobe": 10},
            }
            
            results = self.collection.search(
                data=[query_embedding],
                anns_field="text_dense",
                param=search_params,
                limit=top_k,
                expr=filter_expr,
                output_fields=[
                    "file_id",
                    "chunk_index",
                    "page_number",
                    "text",
                    "metadata",
                ],
            )
            
            # Process results
            search_results = self._process_results(results, include_dense_score=True)
            
            logger.info(
                "Semantic search completed",
                user_id=user_id,
                result_count=len(search_results),
            )
            
            return search_results
        
        except Exception as e:
            logger.error(
                "Semantic search failed",
                user_id=user_id,
                error=str(e),
                exc_info=True,
            )
            raise
    
    def hybrid_search(
        self,
        query_embedding: List[float],
        query_text: str,
        user_id: str,
        top_k: int = 10,
        rerank_k: int = 100,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
        filters: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Hybrid search combining dense and sparse (BM25) search with RRF fusion.
        
        Args:
            query_embedding: Dense embedding vector
            query_text: Text query for BM25
            user_id: User ID for permission filtering
            top_k: Final number of results to return
            rerank_k: Number of candidates to retrieve (will be fused)
            dense_weight: Weight for dense search
            sparse_weight: Weight for sparse search
            filters: Additional filters
        
        Returns:
            List of search results with fused scores
        """
        if not self.connected:
            self.connect()
        
        try:
            logger.info(
                "Performing hybrid search",
                user_id=user_id,
                top_k=top_k,
                rerank_k=rerank_k,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
            )
            
            # Run dense search
            dense_results = self.semantic_search(
                query_embedding=query_embedding,
                user_id=user_id,
                top_k=rerank_k,
                filters=filters,
            )
            
            # Run sparse search
            sparse_results = self.keyword_search(
                query=query_text,
                user_id=user_id,
                top_k=rerank_k,
                filters=filters,
            )
            
            # Fuse results using Reciprocal Rank Fusion (RRF)
            fused_results = self._fuse_results_rrf(
                dense_results=dense_results,
                sparse_results=sparse_results,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
                k=60,  # RRF constant
            )
            
            # Take top-k after fusion
            fused_results = fused_results[:top_k]
            
            logger.info(
                "Hybrid search completed",
                user_id=user_id,
                result_count=len(fused_results),
            )
            
            return fused_results
        
        except Exception as e:
            logger.error(
                "Hybrid search failed",
                user_id=user_id,
                error=str(e),
                exc_info=True,
            )
            raise
    
    def _process_results(
        self,
        results,
        include_dense_score: bool = False,
        include_sparse_score: bool = False,
    ) -> List[Dict]:
        """
        Process raw Milvus results into structured format.
        
        Args:
            results: Raw Milvus search results
            include_dense_score: Include dense score in output
            include_sparse_score: Include sparse score in output
        
        Returns:
            List of processed results
        """
        search_results = []
        
        for hits in results:
            for hit in hits:
                result = {
                    "file_id": hit.entity.get("file_id"),
                    "chunk_index": hit.entity.get("chunk_index"),
                    "page_number": hit.entity.get("page_number", -1),
                    "text": hit.entity.get("text"),
                    "metadata": hit.entity.get("metadata") or {},
                    "score": float(hit.score),
                }
                
                if include_dense_score:
                    result["dense_score"] = float(hit.score)
                if include_sparse_score:
                    result["sparse_score"] = float(hit.score)
                
                search_results.append(result)
        
        return search_results
    
    def _fuse_results_rrf(
        self,
        dense_results: List[Dict],
        sparse_results: List[Dict],
        dense_weight: float,
        sparse_weight: float,
        k: int = 60,
    ) -> List[Dict]:
        """
        Fuse results using Reciprocal Rank Fusion (RRF).
        
        RRF formula: score(d) = Σ(w_i / (k + rank_i(d)))
        
        Args:
            dense_results: Results from dense search
            sparse_results: Results from sparse search
            dense_weight: Weight for dense results
            sparse_weight: Weight for sparse results
            k: RRF constant (typically 60)
        
        Returns:
            Fused and sorted results
        """
        # Build rank maps
        dense_ranks = {
            (r["file_id"], r["chunk_index"]): (i + 1, r)
            for i, r in enumerate(dense_results)
        }
        
        sparse_ranks = {
            (r["file_id"], r["chunk_index"]): (i + 1, r)
            for i, r in enumerate(sparse_results)
        }
        
        # Get all unique documents
        all_docs = set(dense_ranks.keys()) | set(sparse_ranks.keys())
        
        # Calculate RRF scores
        fused_results = []
        for doc_key in all_docs:
            rrf_score = 0.0
            dense_score = None
            sparse_score = None
            
            # Get result object
            if doc_key in dense_ranks:
                rank, result = dense_ranks[doc_key]
                rrf_score += dense_weight / (k + rank)
                dense_score = result.get("dense_score") or result["score"]
            
            if doc_key in sparse_ranks:
                rank, result = sparse_ranks[doc_key]
                rrf_score += sparse_weight / (k + rank)
                sparse_score = result.get("sparse_score") or result["score"]
            
            # Use result from whichever search found it (prefer dense)
            result = dense_ranks.get(doc_key, sparse_ranks.get(doc_key))[1]
            
            # Create fused result
            fused_result = {
                **result,
                "score": rrf_score,
                "dense_score": dense_score,
                "sparse_score": sparse_score,
            }
            
            fused_results.append(fused_result)
        
        # Sort by RRF score
        fused_results.sort(key=lambda x: x["score"], reverse=True)
        
        return fused_results
    
    def get_document(self, file_id: str, chunk_index: int, user_id: str) -> Optional[Dict]:
        """
        Get a specific document chunk.
        
        Args:
            file_id: File ID
            chunk_index: Chunk index
            user_id: User ID for permission check
        
        Returns:
            Document data or None if not found
        """
        if not self.connected:
            self.connect()
        
        try:
            filter_expr = (
                f'user_id == "{user_id}" && '
                f'file_id == "{file_id}" && '
                f'chunk_index == {chunk_index}'
            )
            
            results = self.collection.query(
                expr=filter_expr,
                output_fields=[
                    "file_id",
                    "chunk_index",
                    "page_number",
                    "text",
                    "text_dense",
                    "metadata",
                ],
                limit=1,
            )
            
            if results:
                return results[0]
            return None
        
        except Exception as e:
            logger.error(
                "Failed to get document",
                file_id=file_id,
                chunk_index=chunk_index,
                error=str(e),
            )
            return None
    
    def health_check(self) -> bool:
        """Check if Milvus is healthy."""
        try:
            if not self.connected:
                self.connect()
            
            # Try a simple query
            self.collection.query(expr="chunk_index >= 0", limit=1)
            return True
        except Exception as e:
            logger.error("Milvus health check failed", error=str(e))
            return False

