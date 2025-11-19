"""
LLM-based text cleanup processor.

Fixes text quality issues using LLM:
- Smashed words (actuallyunderstood → actually understood)
- Missing spaces between sentences
- Incorrect line breaks
- Preserves markdown formatting
"""

import re
from typing import List
import httpx
import structlog
from shared.model_registry import get_registry

logger = structlog.get_logger()


class LLMCleanup:
    """
    Clean up text chunks using LLM.
    
    Fixes spacing, line breaks, and formatting issues while preserving content.
    """
    
    SYSTEM_PROMPT = """You are an expert text editor specializing in document cleanup and formatting.

Your task is to fix formatting and spacing issues in text extracted from documents (PDFs, Word docs, etc.).

WHAT TO FIX:
1. Smashed words: "actuallyunderstood" → "actually understood"
2. Missing spaces: "word.Another" → "word. Another"  
3. Incorrect line breaks: Fix awkward mid-sentence breaks
4. Poor paragraph spacing: Add proper paragraph breaks
5. Inconsistent markdown: Standardize heading levels, list formatting

WHAT TO PRESERVE:
1. All original content and meaning - DO NOT summarize or remove content
2. Markdown formatting (# headings, *emphasis*, **bold**, lists)
3. Technical terms and proper nouns
4. Numbers, dates, and citations
5. Document structure and flow

RULES:
- Only return the cleaned text, no explanations or meta-commentary
- Do not add or remove content
- Do not change technical terminology
- Maintain the document's original voice and style
- If text is already clean, return it unchanged

Output clean, well-formatted markdown."""
    
    def __init__(self, config: dict):
        """
        Initialize LLM cleanup processor.
        
        Args:
            config: Configuration dictionary with:
                - llm_cleanup_enabled: Enable/disable cleanup (default: False)
                - litellm_base_url: LiteLLM base URL
        """
        self.config = config
        self.enabled = config.get("llm_cleanup_enabled", False)
        # Default to production IP, will be overridden by config
        self.litellm_base_url = config.get("litellm_base_url", "http://10.96.200.207:4000")
        
        # Get model from registry
        try:
            registry = get_registry()
            self.model = registry.get_model("cleanup")
            self.model_config = registry.get_config("cleanup")
            logger.info(
                "LLM cleanup initialized",
                enabled=self.enabled,
                model=self.model,
                base_url=self.litellm_base_url
            )
        except Exception as e:
            logger.error("Failed to get cleanup model from registry", error=str(e))
            self.model = "qwen3-30b-instruct"  # Fallback to our actual deployed model
            self.model_config = {"temperature": 0.1, "max_tokens": 32768}
            logger.warning("Using fallback cleanup model", model=self.model)
    
    async def cleanup_chunk(self, text: str) -> str:
        """
        Clean up a single chunk of text.
        
        Args:
            text: Text to clean
            
        Returns:
            Cleaned text (or original if cleanup disabled/failed)
        """
        if not self.enabled:
            return text
        
        # Skip empty or very short text
        if not text or len(text.strip()) < 10:
            return text
        
        # Skip if text looks clean (no long words)
        if not self._needs_cleanup(text):
            logger.debug("Chunk looks clean, skipping LLM cleanup")
            return text
        
        try:
            logger.debug(
                "Cleaning chunk with LLM",
                text_length=len(text),
                model=self.model
            )
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.litellm_base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user", "content": text}
                        ],
                        "temperature": self.model_config.get("temperature", 0.1),
                        "max_tokens": self.model_config.get("max_tokens", 32768),
                    }
                )
                
                if response.status_code != 200:
                    logger.error(
                        "LLM cleanup failed",
                        status=response.status_code,
                        response=response.text[:200]
                    )
                    return text  # Return original on error
                
                result = response.json()
                cleaned_text = result["choices"][0]["message"]["content"].strip()
                
                # Validate cleaned text isn't empty or drastically different length
                if not cleaned_text:
                    logger.warning("LLM returned empty text, using original")
                    return text
                
                length_ratio = len(cleaned_text) / len(text)
                if length_ratio < 0.5 or length_ratio > 2.0:
                    logger.warning(
                        "Cleaned text length suspicious, using original",
                        original_length=len(text),
                        cleaned_length=len(cleaned_text),
                        ratio=length_ratio
                    )
                    return text
                
                logger.info(
                    "Chunk cleaned successfully",
                    original_length=len(text),
                    cleaned_length=len(cleaned_text),
                    model=self.model
                )
                
                return cleaned_text
        
        except httpx.TimeoutException:
            logger.error("LLM cleanup timeout", timeout=60.0)
            return text
        except Exception as e:
            logger.error("LLM cleanup error", error=str(e), exc_info=True)
            return text  # Return original on error
    
    def _needs_cleanup(self, text: str) -> bool:
        """
        Check if text needs cleanup (has long words or missing spaces).
        
        Args:
            text: Text to check
            
        Returns:
            True if text needs cleanup, False otherwise
        """
        # Check 1: Find words longer than 20 characters (likely smashed words)
        # Common smashed words are 20-40 chars: "actuallyunderstood" (20), "actuallyunderstoodsmashed" (28)
        long_words = re.findall(r'\b\w{20,}\b', text)
        
        # Check 2: Missing spaces after punctuation (e.g., "word.Another" or "sentence.Yetanother")
        missing_spaces = re.findall(r'[.!?][A-Za-z]', text)
        
        if long_words or missing_spaces:
            logger.debug(
                "Text needs cleanup",
                long_word_count=len(long_words),
                missing_space_count=len(missing_spaces),
                examples=long_words[:3] if long_words else None
            )
            return True
        
        return False
    
    async def cleanup_chunks(self, chunks: List) -> List:
        """
        Clean up multiple chunks.
        
        Args:
            chunks: List of Chunk objects
            
        Returns:
            List of cleaned Chunk objects
        """
        if not self.enabled:
            logger.info("LLM cleanup disabled, skipping")
            return chunks
        
        logger.info(
            "Starting LLM cleanup for chunks",
            chunk_count=len(chunks),
            model=self.model
        )
        
        cleaned_chunks = []
        cleaned_count = 0
        
        for i, chunk in enumerate(chunks):
            cleaned_text = await self.cleanup_chunk(chunk.text)
            
            # Update chunk text if it was cleaned
            if cleaned_text != chunk.text:
                chunk.text = cleaned_text
                cleaned_count += 1
            
            cleaned_chunks.append(chunk)
            
            if (i + 1) % 10 == 0:
                logger.info(
                    "Cleanup progress",
                    processed=i + 1,
                    total=len(chunks),
                    cleaned=cleaned_count
                )
        
        logger.info(
            "LLM cleanup complete",
            total_chunks=len(chunks),
            cleaned_chunks=cleaned_count,
            skipped_chunks=len(chunks) - cleaned_count
        )
        
        return cleaned_chunks

