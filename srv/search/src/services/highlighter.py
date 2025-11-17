"""
Text highlighting service for search results.
"""

import re
import structlog
from typing import List, Dict, Tuple, Optional
import nltk
from nltk.tokenize import word_tokenize
from nltk.stem import PorterStemmer

logger = structlog.get_logger()

# Download required NLTK data
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', quiet=True)


class HighlightingService:
    """Service for highlighting search terms in text."""
    
    def __init__(self, config: Dict):
        """Initialize highlighting service."""
        self.config = config
        self.stemmer = PorterStemmer()
        self.pre_tag = config.get("highlight_pre_tag", "<mark>")
        self.post_tag = config.get("highlight_post_tag", "</mark>")
        self.fragment_size = config.get("highlight_fragment_size", 200)
        self.num_fragments = config.get("highlight_num_fragments", 3)
    
    def highlight(
        self,
        query: str,
        text: str,
        fragment_size: Optional[int] = None,
        num_fragments: Optional[int] = None,
    ) -> List[Dict]:
        """
        Highlight query terms in text and extract fragments.
        
        Args:
            query: Search query
            text: Text to highlight
            fragment_size: Characters per fragment (None = use config default)
            num_fragments: Max fragments to return (None = use config default)
        
        Returns:
            List of highlighted fragments with scores and offsets
        """
        fragment_size = fragment_size or self.fragment_size
        num_fragments = num_fragments or self.num_fragments
        
        try:
            # Tokenize query
            query_tokens = self._tokenize_and_stem(query)
            
            if not query_tokens:
                return []
            
            # Find all matches in text
            matches = self._find_matches(query_tokens, text)
            
            if not matches:
                # No matches, return first fragment without highlighting
                fragment = text[:fragment_size]
                if len(text) > fragment_size:
                    fragment += "..."
                return [{
                    "fragment": fragment,
                    "score": 0.0,
                    "start_offset": 0,
                    "end_offset": min(fragment_size, len(text)),
                }]
            
            # Extract and score fragments around matches
            fragments = self._extract_fragments(
                text=text,
                matches=matches,
                fragment_size=fragment_size,
                num_fragments=num_fragments,
            )
            
            return fragments
        
        except Exception as e:
            logger.error(
                "Highlighting failed",
                error=str(e),
                exc_info=True,
            )
            # Return first fragment without highlighting on error
            return [{
                "fragment": text[:fragment_size],
                "score": 0.0,
                "start_offset": 0,
                "end_offset": min(fragment_size, len(text)),
            }]
    
    def _tokenize_and_stem(self, text: str) -> List[str]:
        """
        Tokenize and stem text.
        
        Args:
            text: Input text
        
        Returns:
            List of stemmed tokens
        """
        try:
            tokens = word_tokenize(text.lower())
            # Filter out punctuation and short tokens
            tokens = [t for t in tokens if t.isalnum() and len(t) > 1]
            # Stem tokens
            stems = [self.stemmer.stem(t) for t in tokens]
            return stems
        except Exception as e:
            logger.error("Tokenization failed", error=str(e))
            # Fallback to simple split
            return text.lower().split()
    
    def _find_matches(
        self,
        query_tokens: List[str],
        text: str,
    ) -> List[Tuple[int, int, str, float]]:
        """
        Find all matches of query tokens in text.
        
        Args:
            query_tokens: Stemmed query tokens
            text: Text to search
        
        Returns:
            List of (start, end, matched_term, score) tuples
        """
        matches = []
        
        # Tokenize text while preserving offsets
        text_lower = text.lower()
        words = re.finditer(r'\b\w+\b', text)
        
        for word_match in words:
            word = word_match.group()
            start = word_match.start()
            end = word_match.end()
            
            # Skip very short words
            if len(word) < 2:
                continue
            
            # Stem the word
            stemmed = self.stemmer.stem(word)
            
            # Check if it matches any query token
            for query_token in query_tokens:
                if stemmed == query_token:
                    # Exact stem match
                    score = 1.0
                    matches.append((start, end, word, score))
                    break
                elif query_token in stemmed or stemmed in query_token:
                    # Partial match
                    score = 0.7
                    matches.append((start, end, word, score))
                    break
                elif self._edit_distance(stemmed, query_token) <= 2:
                    # Fuzzy match (edit distance <= 2)
                    score = 0.5
                    matches.append((start, end, word, score))
                    break
        
        return matches
    
    def _extract_fragments(
        self,
        text: str,
        matches: List[Tuple[int, int, str, float]],
        fragment_size: int,
        num_fragments: int,
    ) -> List[Dict]:
        """
        Extract text fragments around matches.
        
        Args:
            text: Full text
            matches: List of (start, end, term, score) tuples
            fragment_size: Characters per fragment
            num_fragments: Max fragments to return
        
        Returns:
            List of fragment dicts with highlighted text
        """
        if not matches:
            return []
        
        # Group matches into clusters
        clusters = self._cluster_matches(matches, fragment_size)
        
        # Score clusters by number and quality of matches
        scored_clusters = []
        for cluster in clusters:
            cluster_score = sum(m[3] for m in cluster) / len(cluster)
            scored_clusters.append((cluster_score, cluster))
        
        # Sort by score and take top fragments
        scored_clusters.sort(reverse=True)
        top_clusters = scored_clusters[:num_fragments]
        
        # Extract and highlight fragments
        fragments = []
        for score, cluster in top_clusters:
            fragment_dict = self._create_fragment(
                text=text,
                matches=cluster,
                fragment_size=fragment_size,
            )
            fragment_dict["score"] = score
            fragments.append(fragment_dict)
        
        # Sort by position in document
        fragments.sort(key=lambda x: x["start_offset"])
        
        return fragments
    
    def _cluster_matches(
        self,
        matches: List[Tuple[int, int, str, float]],
        fragment_size: int,
    ) -> List[List[Tuple[int, int, str, float]]]:
        """
        Cluster matches that are close together.
        
        Args:
            matches: List of (start, end, term, score) tuples
            fragment_size: Size of fragments (used as clustering threshold)
        
        Returns:
            List of match clusters
        """
        if not matches:
            return []
        
        # Sort matches by position
        sorted_matches = sorted(matches, key=lambda x: x[0])
        
        clusters = []
        current_cluster = [sorted_matches[0]]
        
        for match in sorted_matches[1:]:
            # If this match is close to the last match in current cluster, add it
            if match[0] - current_cluster[-1][1] < fragment_size // 2:
                current_cluster.append(match)
            else:
                # Start a new cluster
                clusters.append(current_cluster)
                current_cluster = [match]
        
        # Add last cluster
        if current_cluster:
            clusters.append(current_cluster)
        
        return clusters
    
    def _create_fragment(
        self,
        text: str,
        matches: List[Tuple[int, int, str, float]],
        fragment_size: int,
    ) -> Dict:
        """
        Create a highlighted fragment around matches.
        
        Args:
            text: Full text
            matches: List of (start, end, term, score) tuples in this fragment
            fragment_size: Size of fragment
        
        Returns:
            Fragment dict with highlighted text and offsets
        """
        # Find center of matches
        first_match = matches[0][0]
        last_match = matches[-1][1]
        center = (first_match + last_match) // 2
        
        # Calculate fragment boundaries
        start = max(0, center - fragment_size // 2)
        end = min(len(text), start + fragment_size)
        
        # Adjust start if we hit the end
        if end == len(text):
            start = max(0, end - fragment_size)
        
        # Find word boundaries
        if start > 0:
            # Find next word boundary
            while start < len(text) and not text[start].isspace():
                start += 1
            start += 1  # Skip the space
        
        if end < len(text):
            # Find previous word boundary
            while end > 0 and not text[end - 1].isspace():
                end -= 1
        
        # Extract fragment
        fragment_text = text[start:end]
        
        # Apply highlighting
        # Adjust match offsets relative to fragment
        adjusted_matches = [
            (max(0, m[0] - start), min(len(fragment_text), m[1] - start), m[2], m[3])
            for m in matches
            if m[1] > start and m[0] < end
        ]
        
        highlighted = self._apply_highlights(fragment_text, adjusted_matches)
        
        # Add ellipsis if needed
        if start > 0:
            highlighted = "..." + highlighted
        if end < len(text):
            highlighted = highlighted + "..."
        
        return {
            "fragment": highlighted,
            "start_offset": start,
            "end_offset": end,
        }
    
    def _apply_highlights(
        self,
        text: str,
        matches: List[Tuple[int, int, str, float]],
    ) -> str:
        """
        Apply highlight tags to text.
        
        Args:
            text: Text to highlight
            matches: List of (start, end, term, score) tuples
        
        Returns:
            Highlighted text with HTML tags
        """
        if not matches:
            return text
        
        # Sort matches by position
        sorted_matches = sorted(matches, key=lambda x: x[0])
        
        # Build highlighted text
        result = []
        last_end = 0
        
        for start, end, _, _ in sorted_matches:
            # Add text before match
            if start > last_end:
                result.append(text[last_end:start])
            
            # Add highlighted match
            result.append(self.pre_tag)
            result.append(text[start:end])
            result.append(self.post_tag)
            
            last_end = end
        
        # Add remaining text
        if last_end < len(text):
            result.append(text[last_end:])
        
        return "".join(result)
    
    def _edit_distance(self, s1: str, s2: str) -> int:
        """
        Calculate edit distance between two strings.
        
        Args:
            s1: First string
            s2: Second string
        
        Returns:
            Edit distance (Levenshtein distance)
        """
        if len(s1) < len(s2):
            return self._edit_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                # Cost of insertions, deletions, or substitutions
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]

