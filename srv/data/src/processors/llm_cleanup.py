"""
LLM-based text cleanup processor.

Fixes text quality issues using LLM:
- Smashed words (actuallyunderstood → actually understood)
- Missing spaces between sentences
- Incorrect line breaks
- Preserves markdown formatting

Also provides page-level assessment for the progressive pipeline:
- Whether a page needs vision model re-extraction (tables, formulas, charts, etc.)
"""

import json
import re
import os
from dataclasses import dataclass
from typing import List, Optional
import httpx
import structlog

from busibox_common.llm import get_registry

logger = structlog.get_logger()


@dataclass
class CleanupAssessment:
    """Result of LLM cleanup with vision re-extraction assessment."""
    cleaned_text: str
    needs_marker: bool
    reason: str
    changed: bool
    needs_vision: bool = False


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
6. Unicode garbage from custom PDF font encodings (e.g., "TaŊ Eïěučō" should be "Tax Equity"). Replace with correct English text if inferable from context, or remove entirely if not.
7. Remove "==> picture [...] intentionally omitted <==" placeholders
8. Remove "----- Start/End of picture text -----" blocks if they contain garbled text

WHAT TO PRESERVE:
1. All meaningful content and meaning - DO NOT summarize or remove content
2. Markdown formatting (# headings, *emphasis*, **bold**, lists)
3. Markdown image references ![...](...) - keep these exactly as-is
4. Technical terms and proper nouns
5. Numbers, dates, and citations
6. Document structure and flow

RULES:
- Only return the cleaned text, no explanations or meta-commentary
- Do not add new content
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
        
        # Get LiteLLM URL from config or environment variable
        # Priority: config dict > LITELLM_BASE_URL env var > default IP
        # Ansible sets LITELLM_BASE_URL in .env file, which Config class reads
        self.litellm_base_url = (
            config.get("litellm_base_url") 
            or os.getenv("LITELLM_BASE_URL") 
            or "http://10.96.200.207:4000"  # Fallback to litellm-lxc IP
        )
        
        # Get LiteLLM API key (required for authenticated LiteLLM servers)
        self.litellm_api_key = (
            config.get("litellm_api_key")
            or os.getenv("LITELLM_API_KEY")
            or os.getenv("LITELLM_MASTER_KEY")  # Fallback to master key if set
            or ""
        )
        
        # Get model config from registry, but use PURPOSE NAME for LiteLLM
        # LiteLLM is configured with model_name: cleanup, parsing, etc.
        # The registry tells us config (temp, max_tokens), but we call LiteLLM with purpose name
        registry = get_registry()
        try:
            self.model_config = registry.get_config("cleanup")
            self.model = "cleanup"  # Use purpose name for LiteLLM, not underlying model
            logger.info(
                "LLM cleanup initialized",
                enabled=self.enabled,
                litellm_model=self.model,  # What we send to LiteLLM
                underlying_model=self.model_config.get("model"),  # What LiteLLM routes to
                base_url=self.litellm_base_url
            )
        except (ValueError, KeyError) as e:
            # Fallback to "parsing" model if "cleanup" not found
            try:
                logger.warning("Cleanup model not found, trying parsing model", error=str(e))
                self.model_config = registry.get_config("parsing")
                self.model = "parsing"  # Use purpose name for LiteLLM
                logger.info(
                    "LLM cleanup initialized with parsing model",
                    enabled=self.enabled,
                    litellm_model=self.model,
                    underlying_model=self.model_config.get("model"),
                    base_url=self.litellm_base_url
                )
            except Exception as e2:
                # Final fallback - use cleanup as model name (should work with LiteLLM)
                logger.error("Failed to get cleanup or parsing model from registry", error=str(e2))
                self.model = "cleanup"  # LiteLLM model name (not underlying model)
                self.model_config = {"temperature": 0.1, "max_tokens": 32768}
                logger.warning("Using fallback model config", model=self.model)
    
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
            
            # Prepare headers with API key if available
            headers = {"Content-Type": "application/json"}
            if self.litellm_api_key:
                headers["Authorization"] = f"Bearer {self.litellm_api_key}"
            
            # Calculate max_tokens based on input - cleanup output should be similar length
            # qwen3.5-35b-multi/cleanup model has ~16K context, so we need input + output < 16K tokens
            # Rough estimate: 4 chars per token
            estimated_input_tokens = len(text) // 4
            system_prompt_tokens = len(self.SYSTEM_PROMPT) // 4
            
            # Cap output tokens to leave room for input + system prompt
            # Model context: ~16000, leave buffer for safety
            available_for_output = 14000 - estimated_input_tokens - system_prompt_tokens
            max_output_tokens = min(max(available_for_output, 512), 4096)
            
            # Skip cleanup if input is too long for the model
            if estimated_input_tokens + system_prompt_tokens > 9000:
                logger.warning(
                    "Input too long for cleanup model, skipping",
                    estimated_tokens=estimated_input_tokens,
                    text_length=len(text),
                )
                return text
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.litellm_base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user", "content": text}
                        ],
                        "temperature": self.model_config.get("temperature", 0.1),
                        "max_tokens": max_output_tokens,
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
        Check if text needs cleanup (has long words, missing spaces, or unicode garbage).
        
        Args:
            text: Text to check
            
        Returns:
            True if text needs cleanup, False otherwise
        """
        # Check 1: Find words longer than 20 characters (likely smashed words)
        long_words = re.findall(r'\b\w{20,}\b', text)
        
        # Check 2: Missing spaces after punctuation
        missing_spaces = re.findall(r'[.!?][A-Za-z]', text)
        
        # Check 3: Unicode garbage from custom font encodings
        # Count non-ASCII letter characters in "word" positions
        letters = [c for c in text if c.isalpha()]
        non_ascii_letters = sum(1 for c in letters if ord(c) > 127)
        has_unicode_junk = len(letters) > 20 and non_ascii_letters / len(letters) > 0.05

        # Check 4: Leftover image placeholders
        has_placeholders = "intentionally omitted" in text or "picture text" in text.lower()
        
        if long_words or missing_spaces or has_unicode_junk or has_placeholders:
            logger.debug(
                "Text needs cleanup",
                long_word_count=len(long_words),
                missing_space_count=len(missing_spaces),
                non_ascii_pct=f"{non_ascii_letters/max(len(letters),1)*100:.1f}%",
                has_placeholders=has_placeholders,
                examples=long_words[:3] if long_words else None
            )
            return True
        
        return False
    
    async def cleanup_chunks(
        self, 
        chunks: List, 
        max_concurrent: int = None,
        on_chunk_cleaned: callable = None,
    ) -> List:
        """
        Clean up multiple chunks in parallel with incremental progress.
        
        Args:
            chunks: List of Chunk objects
            max_concurrent: Maximum concurrent LLM requests (default from env or 3)
            on_chunk_cleaned: Optional callback(chunk_index, cleaned_text) called 
                              as each chunk completes - use to save progress incrementally
            
        Returns:
            List of cleaned Chunk objects
        """
        import asyncio
        
        if not self.enabled:
            logger.info("LLM cleanup disabled, skipping")
            return chunks
        
        # Get batch size from env or use sensible default
        if max_concurrent is None:
            max_concurrent = int(os.getenv("LLM_CLEANUP_BATCH_SIZE", "3"))
        
        logger.info(
            "Starting LLM cleanup for chunks",
            chunk_count=len(chunks),
            model=self.model,
            max_concurrent=max_concurrent
        )
        
        # Create semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(max_concurrent)
        
        # Track progress
        cleaned_count = 0
        failed_count = 0
        completed_count = 0
        
        async def cleanup_with_semaphore(chunk, index):
            """Clean a single chunk with concurrency limiting."""
            nonlocal cleaned_count, failed_count, completed_count
            
            async with semaphore:
                try:
                    cleaned_text = await self.cleanup_chunk(chunk.text)
                    
                    # Update chunk immediately
                    if cleaned_text != chunk.text:
                        chunk.text = cleaned_text
                        cleaned_count += 1
                    
                    # Call progress callback if provided (for incremental saves)
                    if on_chunk_cleaned:
                        try:
                            await on_chunk_cleaned(index, cleaned_text)
                        except Exception as cb_err:
                            logger.warning(
                                "Chunk cleaned callback failed",
                                chunk_index=index,
                                error=str(cb_err)
                            )
                    
                    completed_count += 1
                    
                    # Log progress every 5 chunks
                    if completed_count % 5 == 0:
                        logger.info(
                            "LLM cleanup progress",
                            completed=completed_count,
                            total=len(chunks),
                            cleaned=cleaned_count,
                            failed=failed_count
                        )
                    
                    return index, cleaned_text, None
                    
                except Exception as e:
                    failed_count += 1
                    completed_count += 1
                    logger.warning(
                        "Chunk cleanup failed",
                        chunk_index=index,
                        error=str(e),
                        completed=completed_count,
                        total=len(chunks)
                    )
                    return index, chunk.text, e  # Return original on failure
        
        # Process all chunks in parallel (limited by semaphore)
        # Use asyncio.as_completed to process results as they arrive
        tasks = [
            asyncio.create_task(cleanup_with_semaphore(chunk, i))
            for i, chunk in enumerate(chunks)
        ]
        
        # Wait for all tasks, but they update chunks in place as they complete
        await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info(
            "LLM cleanup complete",
            total_chunks=len(chunks),
            cleaned_chunks=cleaned_count,
            skipped_chunks=len(chunks) - cleaned_count - failed_count,
            failed_chunks=failed_count
        )
        
        return chunks
    
    ASSESSMENT_SYSTEM_PROMPT = """You are an expert text editor specializing in document cleanup and formatting.

You will receive text extracted from a single PDF page via OCR. Perform TWO tasks:

TASK 1 - CLEAN THE TEXT:
Fix formatting and spacing issues:
1. Smashed words: "actuallyunderstood" → "actually understood"
2. Missing spaces: "word.Another" → "word. Another"
3. Incorrect line breaks: Fix awkward mid-sentence breaks
4. Poor paragraph spacing: Add proper paragraph breaks
5. Inconsistent markdown: Standardize heading levels, list formatting

CRITICAL - UNICODE GARBAGE:
PDFs with custom font encodings produce garbled characters like: TaŊ Eïěučō, Oéé¿ñčě±učō, Pañč±eñÿhuéÿ, Caéuča�, Mañ�eč, I±ļeÿč«e±č, etc.
These are NOT foreign languages - they are encoding artifacts. You MUST:
- Replace garbled words with the correct English text if inferable from context
- If you cannot determine the correct text, remove the garbled text entirely
- Do NOT preserve garbled unicode - it is always an extraction error

CRITICAL - IMAGE REFERENCES AND PLACEHOLDERS:
- Lines like "![Image N](images/image_N.png)" are valid image refs - PRESERVE them exactly
- Lines like "==> picture [W x H] intentionally omitted <==" are raw placeholders - remove them
- Blocks between "----- Start of picture text -----" and "----- End of picture text -----": if the text inside is garbled, remove the entire block including markers. If readable, keep only the readable text.

PRESERVE: All meaningful content, markdown image references ![...](...), technical terms, numbers, dates, citations.
DO NOT add new content. If text is already clean, keep it unchanged.

TASK 2 - ASSESS WHETHER THIS PAGE NEEDS VISION RE-EXTRACTION:
A vision model can re-analyse the original page image to recover content
that text extraction missed. Set needs_vision=true if ANY of these apply:
- Complex tables with multi-row/column spans that OCR garbled
- Multi-column layouts where OCR merged columns incorrectly
- Mathematical formulas or equations that OCR couldn't represent
- Significant visible OCR artifacts that cleaning couldn't fix
- Charts, graphs, or diagrams that were not captured as text
- Image placeholders but no descriptive text for what the images show
- The page seems to be a scanned image with very little extractable text

Set needs_vision=false if:
- Text is mostly prose/paragraphs (even if OCR quality is imperfect)
- Simple bullet lists or numbered lists
- Basic headings and sections
- Minor formatting issues that cleanup already fixed
- Tables were successfully cleaned up
- Image placeholders already have descriptive alt-text

Respond with ONLY valid JSON (no markdown code fences):
{
  "cleaned_text": "the cleaned text here",
  "needs_vision": true or false,
  "reason": "brief explanation of vision decision"
}"""

    DUAL_TEXT_SYSTEM_PROMPT = """You are an expert text editor specializing in document cleanup and formatting.

You will receive TWO versions of text extracted from the same PDF page using different methods:
- VERSION A (Layout extraction): Has better structure, headings, formatting, and reading order but may have inaccurate character-level text (garbled words, missing text in scanned areas).
- VERSION B (OCR extraction): Has more accurate character-level text from optical character recognition but may have worse structure, lost formatting, and incorrect reading order.

Perform TWO tasks:

TASK 1 - MERGE THE BEST OF BOTH VERSIONS:
Create the best possible text by combining both versions:
1. Use VERSION A's structure: headings, paragraph breaks, list formatting, table layout, reading order
2. Use VERSION B's text accuracy: correct spelling, complete words, proper character recognition
3. Fix any remaining issues: smashed words, missing spaces, incorrect line breaks
4. If both versions agree on text, keep it as-is
5. If VERSION A has better formatting but VERSION B has better text, use VERSION B's text within VERSION A's structure

CRITICAL - UNICODE GARBAGE:
PDFs with custom font encodings produce garbled characters like: TaŊ Eïěučō, Oéé¿ñčě±učō, Pañč±eñÿhuéÿ, Caéuča�, Mañ�eč, I±ļeÿč«e±č, Rečěñ±ÿ, Vuÿěa�uśu±g, etc.
These are NOT foreign languages - they are encoding artifacts. You MUST:
- Replace garbled headings/words with the correct English text if you can infer the meaning from context (e.g., "TaŊ Eïěučō" → "Tax Equity", "Mañ�eč" → "Market", "Oéé¿ñčě±učō" → "Opportunity")
- If you cannot determine the correct text, remove the garbled text entirely rather than preserving it
- The other version often has the correct text for the same section - use it

CRITICAL - IMAGE PLACEHOLDERS:
- Lines like "![Image N](images/image_N.png)" are valid image references - PRESERVE them exactly as-is
- Lines like "==> picture [W x H] intentionally omitted <==" are raw placeholders - remove them (they've already been replaced with proper image refs where possible)

CRITICAL - PICTURE TEXT BLOCKS:
- Blocks wrapped in "----- Start of picture text -----" / "----- End of picture text -----" contain OCR text from images. If the text inside is garbled unicode junk, remove the entire block. If it contains meaningful readable text (like numbers, labels), keep only the readable text without the start/end markers.

CRITICAL - NO DUPLICATE CONTENT:
- Do NOT repeat or duplicate content. Each section should appear exactly once in the output.
- If the same content appears in both versions, include it only once.

PRESERVE: All meaningful content, markdown image references ![...](...), technical terms, numbers, dates, citations.
DO NOT add new content. DO NOT duplicate sections.

TASK 2 - ASSESS WHETHER THIS PAGE NEEDS VISION RE-EXTRACTION:
A vision model can re-analyse the original page image to recover content
that text extraction missed. Set needs_vision=true if ANY of these apply:
- Complex tables with multi-row/column spans that neither version captured
- Multi-column layouts that both versions got wrong
- Mathematical formulas or equations that OCR couldn't represent
- Charts, graphs, or diagrams not captured as meaningful text by either version
- Image placeholders exist without descriptive content
- Tables remain garbled after merging both versions
- The page is primarily visual content (scanned, infographic, etc.)

Set needs_vision=false if the merged result adequately captures all content.

Respond with ONLY valid JSON (no markdown code fences):
{
  "cleaned_text": "the merged/cleaned text here",
  "needs_vision": true or false,
  "reason": "brief explanation of vision decision"
}"""
    
    async def cleanup_page_with_assessment(
        self, page_text: str, page_number: int = 0,
        layout_text: Optional[str] = None,
    ) -> CleanupAssessment:
        """
        Clean up a page's text AND assess whether it needs Marker re-extraction.
        
        Used in Pass 3 of the progressive pipeline. When layout_text is provided,
        sends both the layout-rich extraction (Pass 1) and OCR text (Pass 2) to the
        LLM to merge the best of both: layout structure from Pass 1 + text accuracy
        from Pass 2.
        
        Args:
            page_text: Current best text for the page (typically OCR from Pass 2)
            page_number: Page number for logging
            layout_text: Optional Pass 1 layout-rich text for comparison/merging
            
        Returns:
            CleanupAssessment with cleaned text and marker recommendation
        """
        if not self.enabled:
            return CleanupAssessment(
                cleaned_text=page_text,
                needs_marker=False,
                reason="LLM cleanup disabled",
                changed=False,
            )
        
        if not page_text or len(page_text.strip()) < 10:
            return CleanupAssessment(
                cleaned_text=page_text,
                needs_marker=False,
                reason="Page too short for assessment",
                changed=False,
            )
        
        try:
            headers = {"Content-Type": "application/json"}
            if self.litellm_api_key:
                headers["Authorization"] = f"Bearer {self.litellm_api_key}"
            
            use_dual = layout_text is not None and layout_text.strip() and layout_text != page_text
            if use_dual:
                system_prompt = self.DUAL_TEXT_SYSTEM_PROMPT
                user_content = (
                    "=== VERSION A (Layout extraction) ===\n"
                    f"{layout_text}\n\n"
                    "=== VERSION B (OCR extraction) ===\n"
                    f"{page_text}"
                )
            else:
                system_prompt = self.ASSESSMENT_SYSTEM_PROMPT
                user_content = page_text

            estimated_input_tokens = len(user_content) // 4
            system_prompt_tokens = len(system_prompt) // 4
            
            if estimated_input_tokens + system_prompt_tokens > 9000:
                logger.warning(
                    "Page text too long for assessment model",
                    page=page_number,
                    estimated_tokens=estimated_input_tokens,
                    dual_mode=use_dual,
                )
                return CleanupAssessment(
                    cleaned_text=page_text,
                    needs_marker=False,
                    reason="Text too long for LLM assessment",
                    changed=False,
                )
            
            available_for_output = 10000 - estimated_input_tokens - system_prompt_tokens
            max_output_tokens = min(max(available_for_output, 512), 6144)
            
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    f"{self.litellm_base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.1,
                        "max_tokens": max_output_tokens,
                    },
                )
                
                if response.status_code != 200:
                    logger.error(
                        "LLM assessment failed",
                        page=page_number,
                        status=response.status_code,
                        response=response.text[:200],
                    )
                    return CleanupAssessment(
                        cleaned_text=page_text,
                        needs_marker=False,
                        reason="LLM request failed",
                        changed=False,
                    )
                
                result = response.json()
                raw_content = result["choices"][0]["message"]["content"].strip()
                
                # Strip markdown code fences if present
                if raw_content.startswith("```"):
                    lines = raw_content.split("\n")
                    lines = [l for l in lines if not l.strip().startswith("```")]
                    raw_content = "\n".join(lines).strip()
                
                parsed = json.loads(raw_content)
                cleaned_text = parsed.get("cleaned_text", page_text)
                # Accept both old (needs_marker) and new (needs_vision) keys
                needs_vision = parsed.get("needs_vision", False) or parsed.get("needs_marker", False)
                reason = parsed.get("reason", "")
                
                if not cleaned_text:
                    cleaned_text = page_text
                
                length_ratio = len(cleaned_text) / max(len(page_text), 1)
                if length_ratio < 0.5 or length_ratio > 2.0:
                    logger.warning(
                        "Assessment cleaned text length suspicious, using original",
                        page=page_number,
                        ratio=length_ratio,
                    )
                    cleaned_text = page_text
                
                changed = cleaned_text != page_text
                
                logger.info(
                    "Page assessment complete",
                    page=page_number,
                    needs_vision=needs_vision,
                    reason=reason,
                    changed=changed,
                    original_length=len(page_text),
                    cleaned_length=len(cleaned_text),
                )
                
                return CleanupAssessment(
                    cleaned_text=cleaned_text,
                    needs_marker=needs_vision,
                    needs_vision=needs_vision,
                    reason=reason,
                    changed=changed,
                )
        
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse LLM assessment JSON, falling back to cleanup only",
                page=page_number,
                error=str(e),
            )
            cleaned = await self.cleanup_chunk(page_text)
            return CleanupAssessment(
                cleaned_text=cleaned,
                needs_marker=False,
                reason="JSON parse failed, used standard cleanup",
                changed=cleaned != page_text,
            )
        except httpx.TimeoutException:
            logger.error("LLM assessment timeout", page=page_number)
            return CleanupAssessment(
                cleaned_text=page_text,
                needs_marker=False,
                reason="LLM timeout",
                changed=False,
            )
        except Exception as e:
            logger.error(
                "LLM assessment error",
                page=page_number,
                error=str(e),
                exc_info=True,
            )
            return CleanupAssessment(
                cleaned_text=page_text,
                needs_marker=False,
                reason=f"Error: {e}",
                changed=False,
            )

