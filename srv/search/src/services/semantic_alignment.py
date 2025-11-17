"""
Semantic alignment service for visualizing query-document similarity.
"""

import structlog
from typing import List, Dict, Tuple, Optional
import numpy as np
from sentence_transformers import SentenceTransformer

logger = structlog.get_logger()


class SemanticAlignmentService:
    """Service for computing semantic alignment between queries and documents."""
    
    def __init__(self, config: Dict):
        """Initialize semantic alignment service."""
        self.config = config
        # Use a smaller model for token-level embeddings
        self.model_name = "sentence-transformers/all-MiniLM-L6-v2"
        self.model = None
        self._load_model()
    
    def _load_model(self):
        """Load the sentence transformer model."""
        try:
            logger.info(
                "Loading semantic alignment model",
                model=self.model_name,
            )
            self.model = SentenceTransformer(self.model_name)
            logger.info("Semantic alignment model loaded")
        except Exception as e:
            logger.error(
                "Failed to load semantic alignment model",
                error=str(e),
                exc_info=True,
            )
    
    def compute_alignment(
        self,
        query: str,
        document: str,
        threshold: float = 0.5,
    ) -> Dict:
        """
        Compute semantic alignment between query and document.
        
        Args:
            query: Search query
            document: Document text
            threshold: Minimum score for matched spans
        
        Returns:
            Alignment dictionary with tokens, matrix, and matched spans
        """
        if not self.model:
            return self._empty_alignment(query)
        
        try:
            # Tokenize
            query_tokens = self._tokenize(query)
            doc_tokens = self._tokenize(document)
            
            if not query_tokens or not doc_tokens:
                return self._empty_alignment(query)
            
            # Generate embeddings for tokens
            query_embeds = self.model.encode(query_tokens, convert_to_tensor=False)
            doc_embeds = self.model.encode(doc_tokens, convert_to_tensor=False)
            
            # Compute alignment matrix (cosine similarity)
            alignment_matrix = self._compute_similarity_matrix(
                query_embeds,
                doc_embeds,
            )
            
            # Find high-confidence matches
            matched_spans = self._extract_matched_spans(
                query_tokens=query_tokens,
                doc_tokens=doc_tokens,
                document=document,
                alignment_matrix=alignment_matrix,
                threshold=threshold,
            )
            
            return {
                "query_tokens": query_tokens,
                "document_tokens": doc_tokens[:50],  # Limit for response size
                "alignment_matrix": alignment_matrix[:, :50].tolist(),  # Limit columns
                "matched_spans": matched_spans,
            }
        
        except Exception as e:
            logger.error(
                "Failed to compute semantic alignment",
                error=str(e),
                exc_info=True,
            )
            return self._empty_alignment(query)
    
    def _tokenize(self, text: str, max_tokens: int = 50) -> List[str]:
        """
        Tokenize text into meaningful units.
        
        Args:
            text: Input text
            max_tokens: Maximum number of tokens
        
        Returns:
            List of tokens
        """
        # Simple word-based tokenization
        # Could be enhanced with spaCy for better linguistic analysis
        tokens = text.lower().split()
        # Remove very short tokens and punctuation
        tokens = [t.strip('.,!?;:()[]{}') for t in tokens]
        tokens = [t for t in tokens if len(t) > 1]
        return tokens[:max_tokens]
    
    def _compute_similarity_matrix(
        self,
        query_embeds: np.ndarray,
        doc_embeds: np.ndarray,
    ) -> np.ndarray:
        """
        Compute cosine similarity matrix between query and document embeddings.
        
        Args:
            query_embeds: Query token embeddings (N x D)
            doc_embeds: Document token embeddings (M x D)
        
        Returns:
            Similarity matrix (N x M)
        """
        # Normalize embeddings
        query_norm = query_embeds / (np.linalg.norm(query_embeds, axis=1, keepdims=True) + 1e-8)
        doc_norm = doc_embeds / (np.linalg.norm(doc_embeds, axis=1, keepdims=True) + 1e-8)
        
        # Compute cosine similarity
        similarity = np.dot(query_norm, doc_norm.T)
        
        return similarity
    
    def _extract_matched_spans(
        self,
        query_tokens: List[str],
        doc_tokens: List[str],
        document: str,
        alignment_matrix: np.ndarray,
        threshold: float,
    ) -> List[Dict]:
        """
        Extract high-confidence matched spans.
        
        Args:
            query_tokens: Query tokens
            doc_tokens: Document tokens
            document: Full document text
            alignment_matrix: Alignment similarity matrix
            threshold: Minimum score for matches
        
        Returns:
            List of matched span dicts
        """
        matched_spans = []
        
        # For each query token, find best matching document tokens
        for i, query_token in enumerate(query_tokens):
            # Find document tokens with high similarity
            doc_similarities = alignment_matrix[i, :]
            high_match_indices = np.where(doc_similarities >= threshold)[0]
            
            if len(high_match_indices) == 0:
                continue
            
            # Get best match
            best_idx = high_match_indices[np.argmax(doc_similarities[high_match_indices])]
            best_token = doc_tokens[best_idx]
            best_score = float(doc_similarities[best_idx])
            
            # Find position in original document
            start, end = self._find_token_position(document, best_token)
            
            if start != -1:
                matched_spans.append({
                    "query_token": query_token,
                    "doc_span": best_token,
                    "score": best_score,
                    "start": start,
                    "end": end,
                })
        
        # Sort by score
        matched_spans.sort(key=lambda x: x["score"], reverse=True)
        
        # Return top matches
        return matched_spans[:10]
    
    def _find_token_position(self, text: str, token: str) -> Tuple[int, int]:
        """
        Find the position of a token in text.
        
        Args:
            text: Full text
            token: Token to find
        
        Returns:
            (start, end) tuple, or (-1, -1) if not found
        """
        text_lower = text.lower()
        token_lower = token.lower()
        
        start = text_lower.find(token_lower)
        if start == -1:
            return (-1, -1)
        
        end = start + len(token)
        return (start, end)
    
    def _empty_alignment(self, query: str) -> Dict:
        """Return empty alignment structure."""
        return {
            "query_tokens": query.split()[:10],
            "document_tokens": [],
            "alignment_matrix": [],
            "matched_spans": [],
        }
    
    def compute_token_level_scores(
        self,
        query: str,
        document: str,
    ) -> List[float]:
        """
        Compute relevance scores for each document token relative to query.
        
        Args:
            query: Search query
            document: Document text
        
        Returns:
            List of scores (one per document token)
        """
        if not self.model:
            return []
        
        try:
            query_tokens = self._tokenize(query)
            doc_tokens = self._tokenize(document)
            
            if not query_tokens or not doc_tokens:
                return []
            
            query_embeds = self.model.encode(query_tokens, convert_to_tensor=False)
            doc_embeds = self.model.encode(doc_tokens, convert_to_tensor=False)
            
            alignment_matrix = self._compute_similarity_matrix(query_embeds, doc_embeds)
            
            # For each document token, get max similarity to any query token
            token_scores = np.max(alignment_matrix, axis=0)
            
            return token_scores.tolist()
        
        except Exception as e:
            logger.error(
                "Failed to compute token-level scores",
                error=str(e),
            )
            return []

