"""
Document classification and language detection.

Classifies document type (report, article, email, etc.) and detects languages.
"""

import re
from typing import Dict, List, Tuple

import structlog
from langdetect import detect_langs

logger = structlog.get_logger()

# Document type patterns
DOCUMENT_PATTERNS = {
    "report": [
        r"\b(report|summary|analysis|findings|conclusion)\b",
        r"\b(executive\s+)?summary\b",
        r"\bq[1-4]\s*(quarter|financial)\s*report\b",
    ],
    "article": [
        r"\b(article|paper|publication|journal|research)\b",
        r"\babstract\b",
        r"\breferences?\b",
    ],
    "email": [
        r"^(from|to|subject|date):",
        r"\b(sent|received|reply|forward)\b",
    ],
    "code": [
        r"^(def|class|function|import|export|const|let|var)\b",
        r"#include\s*<",
        r"package\s+\w+",
    ],
    "presentation": [
        r"\b(slide|presentation|agenda)\b",
        r"slide\s+\d+",
    ],
    "spreadsheet": [
        r"\b(cell|row|column|formula|sum|average)\b",
    ],
    "manual": [
        r"\b(manual|guide|instructions|tutorial|how\s+to)\b",
        r"\bchapter\s+\d+\b",
        r"\bsection\s+\d+",
    ],
}


class DocumentClassifier:
    """Classify document type and detect languages."""
    
    def __init__(self, config: dict):
        """Initialize classifier."""
        self.config = config
    
    def classify(
        self,
        text: str,
        filename: str,
        mime_type: str,
    ) -> Tuple[str, float]:
        """
        Classify document type.
        
        Args:
            text: Document text content
            filename: Original filename
            mime_type: MIME type
        
        Returns:
            Tuple of (document_type, confidence)
        """
        # File extension hints
        ext_hints = {
            ".pdf": {"report", "article", "manual"},
            ".docx": {"report", "article", "manual"},
            ".pptx": {"presentation"},
            ".xlsx": {"spreadsheet"},
            ".py": {"code"},
            ".js": {"code"},
            ".ts": {"code"},
            ".java": {"code"},
            ".go": {"code"},
            ".rs": {"code"},
        }
        
        # Get extension hint
        filename_lower = filename.lower()
        ext_hint = None
        for ext, types in ext_hints.items():
            if filename_lower.endswith(ext):
                ext_hint = types
                break
        
        # Score each document type
        scores = {}
        text_lower = text.lower()
        filename_lower = filename.lower()
        
        for doc_type, patterns in DOCUMENT_PATTERNS.items():
            score = 0.0
            
            # Pattern matching
            for pattern in patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    score += 0.3
            
            # Filename hints
            for pattern in patterns:
                if re.search(pattern, filename_lower, re.IGNORECASE):
                    score += 0.2
            
            # Extension hints
            if ext_hint and doc_type in ext_hint:
                score += 0.2
            
            scores[doc_type] = min(score, 1.0)
        
        # MIME type hints
        if mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
            scores["presentation"] = scores.get("presentation", 0) + 0.3
        elif mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            scores["spreadsheet"] = scores.get("spreadsheet", 0) + 0.3
        
        # Find best match
        if not scores or max(scores.values()) < 0.3:
            return "other", 0.5
        
        best_type = max(scores.items(), key=lambda x: x[1])
        return best_type[0], min(best_type[1], 1.0)
    
    def detect_languages(
        self,
        text: str,
        threshold: float = 0.1,
    ) -> Tuple[str, List[str]]:
        """
        Detect primary language and all languages in document.
        
        Args:
            text: Document text
            threshold: Minimum probability threshold for language detection
        
        Returns:
            Tuple of (primary_language, all_languages)
        """
        try:
            # Sample text for detection (first 1000 chars for speed)
            sample_text = text[:1000] if len(text) > 1000 else text
            
            if not sample_text.strip():
                return "unknown", []
            
            # Detect languages
            detections = detect_langs(sample_text)
            
            # Filter by threshold
            languages = [
                d.lang
                for d in detections
                if d.prob >= threshold
            ]
            
            # Get primary language (highest probability)
            primary = detections[0].lang if detections else "unknown"
            
            logger.info(
                "Language detection",
                primary=primary,
                all_languages=languages,
                detections=[{"lang": d.lang, "prob": d.prob} for d in detections[:5]],
            )
            
            return primary, languages
        
        except Exception as e:
            logger.warning("Language detection failed", error=str(e))
            return "unknown", []

