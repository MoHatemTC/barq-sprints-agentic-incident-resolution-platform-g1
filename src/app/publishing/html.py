"""Markdown to ServiceNow HTML conversion.

`kb_knowledge.text` stores the article body as HTML, while our canonical
corpus stores Markdown. This module is the publish direction — the inverse
of `app.retrieval.extraction` (HTML → Markdown, used when reading articles
back from ServiceNow).

The converter covers the controlled Markdown subset the corpus pipeline
produces (ATX headings, ordered/unordered lists, paragraphs, bold, inline
code, fenced code blocks) rather than a general-purpose dialect: anything
outside the subset is rendered as plain text, never silently dropped.
"""

from __future__ import annotations

import html
import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ORDERED_ITEM_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_UNORDERED_ITEM_RE = re.compile(r"^\s*[-*]\s+(.*)$")


def _render_inline(text: str) -> str:
    """Escape HTML specials, then apply bold and inline code markup."""
    escaped = html.escape(text, quote=False)
    parts: list[str] = []
    is_code = False
    for segment in escaped.split("`"):
        if is_code:
            parts.append(f"<code>{segment}</code>")
        else:
            parts.append(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", segment))
        is_code = not is_code
    return "".join(parts)


def _render_code_fence(lang: str, code_lines: list[str]) -> str:
    lang_attr = f' class="language-{lang}"' if lang else ""
    code = html.escape("\n".join(code_lines), quote=False)
    return f"<pre><code{lang_attr}>\n{code}\n</code></pre>"


def markdown_to_html(markdown: str | None) -> str:
    """Convert canonical Markdown into kb_knowledge-compatible HTML.

    Args:
        markdown: Canonical Markdown body (as stored in barq_articles.json).

    Returns:
        HTML string with an ``<h2>/<ol>/<p>`` structure. Empty input
        yields an empty string.
    """
    if not markdown or not markdown.strip():
        return ""

    lines = markdown.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if line.lstrip().startswith("```"):
            lang = line.lstrip()[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append(_render_code_fence(lang, code_lines))
            i += 1  # skip the closing fence
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_render_inline(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        if _ORDERED_ITEM_RE.match(line):
            items: list[str] = []
            while i < len(lines) and (match := _ORDERED_ITEM_RE.match(lines[i])):
                items.append(f"<li>{_render_inline(match.group(1).strip())}</li>")
                i += 1
            blocks.append(f"<ol>\n{chr(10).join(items)}\n</ol>")
            continue

        if _UNORDERED_ITEM_RE.match(line):
            items = []
            while i < len(lines) and (match := _UNORDERED_ITEM_RE.match(lines[i])):
                items.append(f"<li>{_render_inline(match.group(1).strip())}</li>")
                i += 1
            blocks.append(f"<ul>\n{chr(10).join(items)}\n</ul>")
            continue

        paragraph: list[str] = []
        while i < len(lines) and lines[i].strip():
            current = lines[i]
            if (
                current.lstrip().startswith("```")
                or _HEADING_RE.match(current)
                or _ORDERED_ITEM_RE.match(current)
                or _UNORDERED_ITEM_RE.match(current)
            ):
                break
            paragraph.append(current.strip())
            i += 1
        blocks.append(f"<p>{_render_inline(' '.join(paragraph))}</p>")

    return "\n".join(blocks)
