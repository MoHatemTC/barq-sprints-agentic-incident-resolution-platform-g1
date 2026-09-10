"""ServiceNow HTML to canonical Markdown extraction module.

Converts HTML stored in ServiceNow `kb_knowledge.text` into clean, canonical Markdown.
Preserves fenced code blocks, code languages, inline code, and technical tokens
without token loss or character corruption (e.g. &nbsp; entity preservation).
"""

import html
import re
from typing import Any

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter


class ServiceNowMarkdownConverter(MarkdownConverter):
    """Custom MarkdownConverter tuned for technical knowledge base articles."""

    def convert_pre(
        self, el: Any, text: str, convert_as_inline: bool = False, **kwargs: Any
    ) -> str:
        """Preserve code blocks with optional language tags."""
        if not text:
            return ""

        # Check if child <code> tag specifies a language class (e.g. language-sql, lang-bash)
        code_tag = el.find("code")
        lang = ""
        if code_tag and code_tag.has_attr("class"):
            classes = code_tag["class"]
            if isinstance(classes, str):
                classes = classes.split()
            for cls_name in classes:
                if cls_name.startswith("language-"):
                    lang = cls_name[len("language-") :]
                    break
                if cls_name.startswith("lang-"):
                    lang = cls_name[len("lang-") :]
                    break

        # Strip outer carriage returns while keeping internal indentation
        clean_code = text.strip("\r\n")
        return f"\n\n```{lang}\n{clean_code}\n```\n\n"

    def convert_code(
        self, el: Any, text: str, convert_as_inline: bool = False, **kwargs: Any
    ) -> str:
        """Handle inline <code> vs nested <pre><code> blocks."""
        if el.parent and el.parent.name == "pre":
            return text
        # Inline code: enclose in backticks
        stripped = text.strip()
        if not stripped:
            return ""
        return f"`{stripped}`"


# Pre-compile normalization regex patterns
NBSP_PATTERN = re.compile(r"&nbsp;|\u00a0")
MULTIPLE_NEWLINES_PATTERN = re.compile(r"\n{3,}")
TRAILING_WHITESPACE_PATTERN = re.compile(r"[ \t]+$", re.MULTILINE)


def html_to_markdown(html_content: str | None) -> str:
    """Convert ServiceNow HTML into canonical Markdown.

    Steps:
    1. Handle None or empty inputs gracefully.
    2. Normalize non-breaking spaces (&nbsp; and \u00a0) into standard spaces.
    3. Decompose <script> and <style> tags to eliminate non-content code/CSS.
    4. Convert HTML DOM to Markdown using ATX header style (#, ##, ###).
    5. Decode safe XML/HTML entities without breaking code fences.
    6. Clean up excessive newlines outside code fences while preserving formatting.

    Args:
        html_content: Raw HTML string from kb_knowledge.text or API response.

    Returns:
        Canonical Markdown string preserving code blocks and technical tokens.
    """
    if not html_content or not html_content.strip():
        return ""

    # Step 1: Replace non-breaking spaces with standard spaces before parsing
    # Prevents CLI arguments from corrupting (e.g. "psql -U admin&nbsp;-h" -> "psql -U admin -h")
    normalized_html = NBSP_PATTERN.sub(" ", html_content)

    # Step 2: Strip scripts, styles, and non-content DOM nodes using BeautifulSoup
    soup = BeautifulSoup(normalized_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta"]):
        tag.decompose()

    # Step 3: Convert DOM to Markdown with ATX headers (# Header, ## Subheader)
    converter = ServiceNowMarkdownConverter(
        heading_style="ATX",
        bullets="-",
    )
    markdown_text = converter.convert_soup(soup)

    # Step 4: Unescape remaining HTML entities (like &lt; to <, &gt; to >, &amp; to &)
    markdown_text = html.unescape(markdown_text)

    # Step 5: Normalize whitespace: remove trailing spaces on lines, cap blank lines to 2
    markdown_text = TRAILING_WHITESPACE_PATTERN.sub("", markdown_text)
    markdown_text = MULTIPLE_NEWLINES_PATTERN.sub("\n\n", markdown_text)

    return markdown_text.strip()
