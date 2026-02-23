"""
Markdown to Telegram HTML converter.

Converts standard markdown (as output by LLMs) to the subset of HTML
that the Telegram Bot API supports:

    <b>, <i>, <u>, <s>, <code>, <pre>, <a href>, <blockquote>

Telegram does NOT support <br>, <p>, <h1-h6>, <ul>, <ol>, <li>, etc.
Line breaks are literal newlines.  Lists are rendered with Unicode
bullet characters (already natural for plain-text display).
"""

import re
import logging
from html import escape as html_escape

logger = logging.getLogger(__name__)

# Regex for fenced code blocks (``` with optional language)
_CODE_FENCE_RE = re.compile(
    r"^```(\w*)\s*\n(.*?)^```\s*$",
    re.MULTILINE | re.DOTALL,
)

# Regex for inline code (single backtick, non-greedy)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

# Regex for markdown links [text](url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Regex for bold (**text** or __text__)
_BOLD_ASTERISK_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__", re.DOTALL)

# Regex for italic (*text* or _text_) — must run after bold
_ITALIC_ASTERISK_RE = re.compile(r"\*(.+?)\*", re.DOTALL)
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!\w)_([^_]+?)_(?!\w)")

# Regex for strikethrough ~~text~~
_STRIKETHROUGH_RE = re.compile(r"~~(.+?)~~", re.DOTALL)

# Regex for headings (# ... at start of line)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Regex for blockquotes (> at start of line)
_BLOCKQUOTE_LINE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)

# Placeholder tokens to protect code blocks from inline formatting passes
_CODE_PLACEHOLDER = "\x00CODE_BLOCK_{}\x00"
_INLINE_CODE_PLACEHOLDER = "\x00INLINE_CODE_{}\x00"
_LINK_PLACEHOLDER = "\x00LINK_{}\x00"


def markdown_to_telegram_html(text: str) -> str:
    """
    Convert standard markdown to Telegram-compatible HTML.

    Handles: bold, italic, strikethrough, inline code, fenced code blocks,
    links, headings (as bold), blockquotes, and unordered/ordered lists.

    Characters that would conflict with HTML are escaped first, then
    markdown structures are converted to their HTML tag equivalents.
    """
    if not text or not text.strip():
        return text

    # ── 1. Extract code blocks to protect them from further processing ──
    code_blocks: list[str] = []
    inline_codes: list[str] = []
    links: list[str] = []

    def _stash_code_block(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = m.group(2)
        escaped_code = html_escape(code)
        if lang:
            html = f'<pre><code class="language-{html_escape(lang)}">{escaped_code}</code></pre>'
        else:
            html = f"<pre>{escaped_code}</pre>"
        idx = len(code_blocks)
        code_blocks.append(html)
        return _CODE_PLACEHOLDER.format(idx)

    text = _CODE_FENCE_RE.sub(_stash_code_block, text)

    def _stash_inline_code(m: re.Match) -> str:
        code = html_escape(m.group(1))
        html = f"<code>{code}</code>"
        idx = len(inline_codes)
        inline_codes.append(html)
        return _INLINE_CODE_PLACEHOLDER.format(idx)

    text = _INLINE_CODE_RE.sub(_stash_inline_code, text)

    # ── 2. Extract links before escaping ──

    def _stash_link(m: re.Match) -> str:
        link_text = m.group(1)
        url = m.group(2)
        idx = len(links)
        links.append((link_text, url))
        return _LINK_PLACEHOLDER.format(idx)

    text = _LINK_RE.sub(_stash_link, text)

    # ── 3. HTML-escape the remaining text ──
    text = html_escape(text, quote=False)

    # ── 4. Convert markdown structures to HTML ──

    # Headings -> bold
    text = _HEADING_RE.sub(lambda m: f"<b>{m.group(2)}</b>", text)

    # Blockquotes — collect consecutive > lines into one <blockquote>
    text = _convert_blockquotes(text)

    # Bold (** and __)
    text = _BOLD_ASTERISK_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_UNDERSCORE_RE.sub(r"<b>\1</b>", text)

    # Italic (* and _) — after bold so ** is consumed first
    text = _ITALIC_ASTERISK_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", text)

    # Strikethrough
    text = _STRIKETHROUGH_RE.sub(r"<s>\1</s>", text)

    # Unordered list markers -> bullet character
    text = re.sub(r"^[\-\*]\s+", "• ", text, flags=re.MULTILINE)

    # Horizontal rules (--- or ***) -> unicode line
    text = re.sub(r"^-{3,}$", "───", text, flags=re.MULTILINE)
    text = re.sub(r"^\*{3,}$", "───", text, flags=re.MULTILINE)

    # ── 5. Restore stashed elements ──

    for idx, (link_text, url) in enumerate(links):
        escaped_text = html_escape(link_text, quote=False)
        escaped_url = html_escape(url, quote=True)
        text = text.replace(
            _LINK_PLACEHOLDER.format(idx),
            f'<a href="{escaped_url}">{escaped_text}</a>',
        )

    for idx, html in enumerate(inline_codes):
        text = text.replace(_INLINE_CODE_PLACEHOLDER.format(idx), html)

    for idx, html in enumerate(code_blocks):
        text = text.replace(_CODE_PLACEHOLDER.format(idx), html)

    return text


def _convert_blockquotes(text: str) -> str:
    """Merge consecutive > lines into <blockquote> blocks."""
    lines = text.split("\n")
    result: list[str] = []
    quote_buf: list[str] = []

    def flush_quote():
        if quote_buf:
            inner = "\n".join(quote_buf)
            result.append(f"<blockquote>{inner}</blockquote>")
            quote_buf.clear()

    for line in lines:
        m = _BLOCKQUOTE_LINE_RE.match(line)
        if m:
            quote_buf.append(m.group(1))
        else:
            flush_quote()
            result.append(line)

    flush_quote()
    return "\n".join(result)
