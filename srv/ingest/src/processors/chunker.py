"""
Text chunking with semantic boundaries and language awareness.

Chunks text into 400-800 token segments with 10-15% overlap,
respecting semantic boundaries (sentences, paragraphs) and language boundaries.

Converts semantic structures to markdown:
- Headings (ALL CAPS, numbered sections) → # Markdown headings
- Lists → - Markdown lists
- Preserves paragraph structure with blank lines
"""

import re
from typing import Dict, List, Optional

import spacy
import structlog
import tiktoken

logger = structlog.get_logger()


class Chunk:
    """Chunk metadata."""
    
    def __init__(
        self,
        text: str,
        chunk_index: int,
        token_count: int,
        char_offset: int,
        page_number: Optional[int] = None,
        section_heading: Optional[str] = None,
        language: Optional[str] = None,
    ):
        self.text = text
        self.chunk_index = chunk_index
        self.token_count = token_count
        self.char_offset = char_offset
        self.page_number = page_number
        self.section_heading = section_heading
        self.language = language
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "text": self.text,
            "chunk_index": self.chunk_index,
            "token_count": self.token_count,
            "char_offset": self.char_offset,
            "page_number": self.page_number,
            "section_heading": self.section_heading,
            "language": self.language,
        }


class Chunker:
    """Chunk text into smaller segments for embedding."""
    
    def __init__(self, config: dict):
        """
        Initialize chunker with configuration.
        
        Args:
            config: Configuration dictionary with chunk_size_min, chunk_size_max, chunk_overlap_pct
        """
        self.config = config
        self.min_tokens = config.get("chunk_size_min", 400)
        self.max_tokens = config.get("chunk_size_max", 800)
        self.overlap_pct = config.get("chunk_overlap_pct", 0.12)
        
        # Initialize tokenizer
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning("Failed to load tiktoken, using fallback", error=str(e))
            self.tokenizer = None
        
        # Initialize spaCy (lazy loading)
        self.nlp = None
        self.nlp_lang = None
    
    def _load_spacy_model(self, language: str = "en"):
        """Load spaCy model for language (lazy loading)."""
        if self.nlp and self.nlp_lang == language:
            return self.nlp
        
        try:
            # Try language-specific model
            model_name = f"{language}_core_web_sm"
            self.nlp = spacy.load(model_name)
            self.nlp_lang = language
            logger.info("Loaded spaCy model", language=language, model=model_name)
        except OSError:
            # Fallback to English
            try:
                self.nlp = spacy.load("en_core_web_sm")
                self.nlp_lang = "en"
                logger.warning("Fell back to English spaCy model", requested_language=language)
            except OSError:
                logger.error("Failed to load spaCy model - install with: python -m spacy download en_core_web_sm")
                self.nlp = None
        
        return self.nlp
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        else:
            # Fallback: rough estimate (4 chars per token)
            return len(text) // 4
    
    def chunk(
        self,
        text: str,
        page_number: Optional[int] = None,
        detected_languages: Optional[List[str]] = None,
    ) -> List[Chunk]:
        """
        Chunk text into segments with semantic boundaries.
        
        Args:
            text: Input text
            page_number: PDF page number (if applicable)
            detected_languages: List of detected languages for language-aware chunking
        
        Returns:
            List of Chunk objects with metadata
        """
        if not text.strip():
            return []
        
        # Load spaCy model (use primary language if provided)
        primary_lang = detected_languages[0] if detected_languages else "en"
        nlp = self._load_spacy_model(primary_lang)
        
        if not nlp:
            # Fallback: simple sentence splitting
            logger.info(
                "Using simple chunking (spaCy not available)",
                text_length=len(text),
            )
            return self._chunk_simple(text, page_number)
        
        # Parse document with spaCy
        logger.debug(
            "Using semantic chunking with spaCy",
            text_length=len(text),
            language=primary_lang,
        )
        doc = nlp(text)
        
        # Group into paragraphs first (for better semantic boundaries)
        paragraphs = self._extract_paragraphs(doc)
        
        logger.debug(
            "Extracted paragraphs from document",
            paragraph_count=len(paragraphs),
            text_length=len(text),
        )
        
        # If we only got 1 paragraph, check if it needs to be split
        # This handles documents without proper paragraph breaks
        if len(paragraphs) == 1:
            para_tokens = self._count_tokens(paragraphs[0]["text"])
            # If the single paragraph exceeds max tokens, fall back to simple chunking
            if para_tokens > self.max_tokens or len(text) > 5000:
                logger.info(
                    "Only 1 paragraph detected but exceeds limits, using simple chunking",
                    text_length=len(text),
                    tokens=para_tokens,
                    max_tokens=self.max_tokens,
                )
                return self._chunk_simple(text, page_number)
        
        chunks = []
        current_chunk_sentences = []
        current_tokens = 0
        char_offset = 0
        chunk_index = 0
        
        for para in paragraphs:
            para_text = para["text"]
            para_tokens = self._count_tokens(para_text)
            para_sentences = para["sentences"]
            
            # Check if adding paragraph would exceed max tokens
            if current_tokens + para_tokens > self.max_tokens and current_tokens >= self.min_tokens:
                # Save current chunk with markdown formatting
                chunk_text = " ".join(current_chunk_sentences)
                markdown_text = self._convert_to_markdown(chunk_text)
                
                # Truncate if too long (safety check for Milvus varchar limit)
                if len(markdown_text) > 65000:
                    logger.warning(
                        "Chunk exceeds Milvus limit, truncating",
                        original_length=len(markdown_text),
                        truncated_length=65000,
                    )
                    markdown_text = markdown_text[:65000] + "... [truncated]"
                
                chunk = Chunk(
                    text=markdown_text,
                    chunk_index=chunk_index,
                    token_count=self._count_tokens(markdown_text),
                    char_offset=char_offset,
                    page_number=page_number,
                    section_heading=self._extract_section_heading(chunk_text),
                    language=primary_lang,
                )
                chunks.append(chunk)
                chunk_index += 1
                
                # Calculate overlap
                overlap_tokens = int(current_tokens * self.overlap_pct)
                current_chunk_sentences, current_tokens, char_offset = self._get_overlap(
                    current_chunk_sentences,
                    overlap_tokens,
                    char_offset,
                )
            
            # Add paragraph sentences
            for sent_info in para_sentences:
                current_chunk_sentences.append(sent_info["text"])
                current_tokens += sent_info["tokens"]
                char_offset = sent_info["end_char"]
        
        # Add final chunk with markdown formatting
        if current_chunk_sentences:
            chunk_text = " ".join(current_chunk_sentences)
            markdown_text = self._convert_to_markdown(chunk_text)
            
            # Truncate if too long (safety check for Milvus varchar limit)
            if len(markdown_text) > 65000:
                logger.warning(
                    "Final chunk exceeds Milvus limit, truncating",
                    original_length=len(markdown_text),
                    truncated_length=65000,
                )
                markdown_text = markdown_text[:65000] + "... [truncated]"
            
            chunk = Chunk(
                text=markdown_text,
                chunk_index=chunk_index,
                token_count=self._count_tokens(markdown_text),
                char_offset=char_offset - len(chunk_text),
                page_number=page_number,
                section_heading=self._extract_section_heading(chunk_text),
                language=primary_lang,
            )
            chunks.append(chunk)
        
        logger.info(
            "Text chunked",
            chunk_count=len(chunks),
            total_tokens=sum(c.token_count for c in chunks),
            avg_tokens=sum(c.token_count for c in chunks) / len(chunks) if chunks else 0,
        )
        
        return chunks
    
    def _extract_paragraphs(self, doc) -> List[Dict]:
        """Extract paragraphs with sentence boundaries."""
        paragraphs = []
        current_para = {"sentences": [], "text": ""}
        prev_sent_end = 0
        
        for sent in doc.sents:
            sent_text = sent.text.strip()
            if not sent_text:
                continue
            
            # Check for paragraph break by looking at whitespace between sentences
            # If there are 2+ newlines between previous sentence and this one, it's a new paragraph
            between_text = doc.text[prev_sent_end:sent.start_char]
            newline_count = between_text.count('\n')
            
            # Start new paragraph if:
            # 1. Multiple newlines between sentences (paragraph break)
            # 2. Special heading/section marker
            should_break = (
                newline_count >= 2 or
                self._is_paragraph_break(sent)
            )
            
            if should_break and current_para["sentences"]:
                current_para["text"] = " ".join(s["text"] for s in current_para["sentences"])
                paragraphs.append(current_para)
                current_para = {"sentences": [], "text": ""}
            
            sent_tokens = self._count_tokens(sent_text)
            current_para["sentences"].append({
                "text": sent_text,
                "tokens": sent_tokens,
                "start_char": sent.start_char,
                "end_char": sent.end_char,
            })
            
            prev_sent_end = sent.end_char
        
        # Add final paragraph
        if current_para["sentences"]:
            current_para["text"] = " ".join(s["text"] for s in current_para["sentences"])
            paragraphs.append(current_para)
        
        return paragraphs
    
    def _is_paragraph_break(self, sent) -> bool:
        """Check if sentence indicates paragraph break."""
        # Check for headings (all caps, short, ends with colon)
        text = sent.text.strip()
        if len(text) < 100 and text.isupper() and text.endswith(":"):
            return True
        
        # Check for section markers
        if re.match(r"^(chapter|section|part)\s+\d+", text, re.IGNORECASE):
            return True
        
        return False
    
    def _get_overlap(
        self,
        sentences: List[Dict],
        overlap_tokens: int,
        current_char_offset: int,
    ) -> tuple:
        """Get overlap sentences for next chunk."""
        overlap_sentences = []
        overlap_token_count = 0
        
        # Take sentences from end until we reach overlap token count
        for sent in reversed(sentences):
            if overlap_token_count >= overlap_tokens:
                break
            overlap_sentences.insert(0, sent)
            overlap_token_count += sent["tokens"]
        
        # Recalculate char offset
        if overlap_sentences:
            new_char_offset = overlap_sentences[0]["start_char"]
        else:
            new_char_offset = current_char_offset
        
        return overlap_sentences, overlap_token_count, new_char_offset
    
    def _extract_section_heading(self, text: str) -> Optional[str]:
        """Extract section heading from chunk text."""
        lines = text.split("\n")
        for line in lines[:3]:  # Check first 3 lines
            line_stripped = line.strip()
            if (
                len(line_stripped) < 100
                and line_stripped.isupper()
                and line_stripped.endswith(":")
            ):
                return line_stripped
        
        # Check for numbered sections
        match = re.match(r"^(chapter|section|part)\s+\d+[:\s]+(.+)", text, re.IGNORECASE)
        if match:
            return match.group(0)
        
        return None
    
    def _convert_to_markdown(self, text: str) -> str:
        """
        Convert semantic structures in text to markdown format.
        
        Converts:
        - Document titles (first line, centered) → #
        - Author bylines (second line, centered) → *Author*
        - ALL CAPS HEADINGS: → ##
        - Chapter/Section markers → ##
        - Numbered lists (1., 2.) → 1. Item
        - Bullet points (•, -, *) → - Item
        - Multiple blank lines → Single blank line
        
        Args:
            text: Input text with semantic structures
        
        Returns:
            Markdown-formatted text
        """
        lines = text.split("\n")
        markdown_lines = []
        
        # Track if we're at the start of the document (for title/byline detection)
        is_document_start = True
        found_title = False
        found_byline = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            if not stripped:
                # Preserve single blank lines, remove multiple
                if not markdown_lines or markdown_lines[-1] != "":
                    markdown_lines.append("")
                # After first blank line, we're no longer at document start
                if is_document_start and markdown_lines:
                    is_document_start = False
                continue
            
            # Detect document title (first non-empty line, often centered)
            # Characteristics: Short (< 100 chars), not all caps, at start
            if is_document_start and not found_title and len(stripped) < 100:
                # Check if next non-empty line looks like an author name
                next_line_idx = i + 1
                while next_line_idx < len(lines) and not lines[next_line_idx].strip():
                    next_line_idx += 1
                
                if next_line_idx < len(lines):
                    next_line = lines[next_line_idx].strip()
                    # If next line is short and looks like a name (2-4 words, capitalized)
                    words = next_line.split()
                    if (len(words) >= 2 and len(words) <= 4 and 
                        all(w[0].isupper() for w in words if w) and
                        len(next_line) < 50):
                        # This is likely a title followed by author
                        markdown_lines.append(f"# {stripped}")
                        markdown_lines.append("")
                        found_title = True
                        continue
            
            # Detect author byline (follows title, short, looks like a name)
            if found_title and not found_byline and len(stripped) < 50:
                words = stripped.split()
                # Name pattern: 2-4 capitalized words
                if len(words) >= 2 and len(words) <= 4 and all(w[0].isupper() for w in words if w):
                    markdown_lines.append(f"*{stripped}*")
                    markdown_lines.append("")
                    found_byline = True
                    is_document_start = False
                    continue
            
            # After title/byline, we're no longer at document start
            if found_title or found_byline:
                is_document_start = False
            
            # Convert ALL CAPS headings to markdown
            if len(stripped) < 100 and stripped.isupper() and not stripped.isdigit():
                # Remove trailing colon if present
                heading_text = stripped.rstrip(":")
                # Use ## for section headings (# reserved for document title)
                markdown_lines.append(f"## {heading_text}")
                markdown_lines.append("")  # Blank line after heading
                continue
            
            # Convert numbered section markers
            section_match = re.match(r"^(chapter|section|part)\s+(\d+)[:\s]*(.*)$", stripped, re.IGNORECASE)
            if section_match:
                section_type = section_match.group(1).title()
                section_num = section_match.group(2)
                section_title = section_match.group(3).strip()
                if section_title:
                    markdown_lines.append(f"## {section_type} {section_num}: {section_title}")
                else:
                    markdown_lines.append(f"## {section_type} {section_num}")
                markdown_lines.append("")
                continue
            
            # Convert bullet points to markdown lists
            bullet_match = re.match(r"^[•\-\*]\s+(.+)$", stripped)
            if bullet_match:
                markdown_lines.append(f"- {bullet_match.group(1)}")
                continue
            
            # Numbered lists are already markdown-compatible
            if re.match(r"^\d+\.\s+", stripped):
                markdown_lines.append(stripped)
                continue
            
            # Regular paragraph text
            markdown_lines.append(stripped)
        
        # Join and clean up multiple blank lines
        markdown_text = "\n".join(markdown_lines)
        markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)  # Max 2 newlines
        
        return markdown_text.strip()
    
    def _chunk_simple(self, text: str, page_number: Optional[int]) -> List[Chunk]:
        """Simple chunking fallback (when spaCy not available)."""
        # Split by paragraphs - try multiple strategies
        # First try double newlines (proper paragraphs)
        paragraphs = re.split(r"\n\s*\n", text)
        
        logger.debug(
            "Initial paragraph split",
            paragraph_count=len(paragraphs),
            text_length=len(text),
        )
        
        # If we only got 1 paragraph, the text might not have double newlines
        # Try splitting by sentences if the text is substantial
        if len(paragraphs) == 1 and len(text) > 1000:
            logger.info(
                "Text has no paragraph breaks, splitting by sentences",
                text_length=len(text),
            )
            # Split by sentence-ending punctuation followed by space/newline
            paragraphs = re.split(r'([.!?]+[\s\n]+)', text)
            # Recombine punctuation with sentences
            combined = []
            for i in range(0, len(paragraphs) - 1, 2):
                if i + 1 < len(paragraphs):
                    combined.append(paragraphs[i] + paragraphs[i + 1])
                else:
                    combined.append(paragraphs[i])
            if combined:
                paragraphs = combined
            else:
                # Last resort: split into fixed-size chunks
                chunk_size = 2000  # characters
                paragraphs = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
                logger.warning(
                    "Using fixed-size chunking as fallback",
                    text_length=len(text),
                    chunk_count=len(paragraphs),
                )
        
        chunks = []
        current_chunk = []
        current_tokens = 0
        char_offset = 0
        chunk_index = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            para_tokens = self._count_tokens(para)
            
            # Force chunk if we're approaching Milvus varchar limit (65535 chars)
            # Or if we've exceeded max tokens
            chunk_text_preview = "\n\n".join(current_chunk)
            should_chunk = (
                (current_tokens + para_tokens > self.max_tokens and current_tokens >= self.min_tokens)
                or len(chunk_text_preview) > 60000  # Safety margin before 65535 limit
            )
            
            if should_chunk:
                # Save chunk with markdown formatting
                chunk_text = "\n\n".join(current_chunk)
                markdown_text = self._convert_to_markdown(chunk_text)
                
                # Truncate if still too long (safety check)
                if len(markdown_text) > 65000:
                    logger.warning(
                        "Chunk exceeds Milvus limit, truncating",
                        original_length=len(markdown_text),
                        truncated_length=65000,
                    )
                    markdown_text = markdown_text[:65000] + "... [truncated]"
                
                chunk = Chunk(
                    text=markdown_text,
                    chunk_index=chunk_index,
                    token_count=self._count_tokens(markdown_text),
                    char_offset=char_offset,
                    page_number=page_number,
                )
                chunks.append(chunk)
                chunk_index += 1
                
                # Overlap
                overlap_tokens = int(current_tokens * self.overlap_pct)
                current_chunk, current_tokens, char_offset = self._get_overlap_simple(
                    current_chunk,
                    overlap_tokens,
                    char_offset,
                )
            
            current_chunk.append(para)
            current_tokens += para_tokens
            char_offset += len(para) + 2  # +2 for paragraph separator
        
        # Final chunk with markdown formatting
        if current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            markdown_text = self._convert_to_markdown(chunk_text)
            
            # Truncate if too long (safety check)
            if len(markdown_text) > 65000:
                logger.warning(
                    "Final chunk exceeds Milvus limit, truncating",
                    original_length=len(markdown_text),
                    truncated_length=65000,
                )
                markdown_text = markdown_text[:65000] + "... [truncated]"
            
            chunk = Chunk(
                text=markdown_text,
                chunk_index=chunk_index,
                token_count=self._count_tokens(markdown_text),
                char_offset=char_offset - len(chunk_text),
                page_number=page_number,
            )
            chunks.append(chunk)
        
        return chunks
    
    def _get_overlap_simple(
        self,
        paragraphs: List[str],
        overlap_tokens: int,
        current_char_offset: int,
    ) -> tuple:
        """Get overlap paragraphs for simple chunking."""
        overlap_paras = []
        overlap_token_count = 0
        
        for para in reversed(paragraphs):
            if overlap_token_count >= overlap_tokens:
                break
            overlap_paras.insert(0, para)
            overlap_token_count += self._count_tokens(para)
        
        new_char_offset = current_char_offset - sum(len(p) + 2 for p in overlap_paras)
        
        return overlap_paras, overlap_token_count, new_char_offset
