"""Tests for the Markdown → ServiceNow HTML publish converter."""

import re
from pathlib import Path

import pytest

from app.publishing.html import markdown_to_html
from app.retrieval.extraction import html_to_markdown
from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/barq_articles.json")


def test_empty_and_none_input_yield_empty_html() -> None:
    assert markdown_to_html("") == ""
    assert markdown_to_html("   ") == ""
    assert markdown_to_html(None) == ""


def test_heading_levels_map_to_h_tags() -> None:
    html_out = markdown_to_html("# Title\n\n## Section\n\n### Sub")
    assert "<h1>Title</h1>" in html_out
    assert "<h2>Section</h2>" in html_out
    assert "<h3>Sub</h3>" in html_out


def test_numbered_list_becomes_ordered_list() -> None:
    html_out = markdown_to_html(
        "## Resolution\n\n1. Stop the service\n2. Drain the pool\n3. Restart"
    )
    assert "<ol>" in html_out
    assert html_out.count("<li>") == 3
    assert "<li>Stop the service</li>" in html_out
    assert "<li>Drain the pool</li>" in html_out


def test_unordered_list_becomes_unordered_list() -> None:
    html_out = markdown_to_html("- one\n- two")
    assert "<ul>" in html_out
    assert html_out.count("<li>") == 2


def test_bold_and_inline_code_render() -> None:
    html_out = markdown_to_html("Do **not** restart; run `pg_stat_activity` first.")
    assert "<strong>not</strong>" in html_out
    assert "<code>pg_stat_activity</code>" in html_out


def test_code_fence_preserved_with_language() -> None:
    markdown = "Run:\n\n```sql\nSELECT 1;\n```"
    html_out = markdown_to_html(markdown)
    assert '<pre><code class="language-sql">' in html_out
    assert "SELECT 1;" in html_out


def test_html_specials_are_escaped() -> None:
    html_out = markdown_to_html("Check a<b and logs & errors")
    assert "a&lt;b" in html_out
    assert " &amp; " in html_out
    assert "<h2>" not in html_out.split("<p>")[0]  # no stray tags injected


def test_consecutive_paragraphs_render_separately() -> None:
    html_out = markdown_to_html("First paragraph.\n\nSecond paragraph.")
    assert "<p>First paragraph.</p>" in html_out
    assert "<p>Second paragraph.</p>" in html_out


def _md_headings(markdown: str) -> list[str]:
    return re.findall(r"^#{1,6}\s+(.*)$", markdown, flags=re.MULTILINE)


def _md_steps(markdown: str) -> list[str]:
    return [
        m.group(1).strip() for m in re.finditer(r"^\d+\.\s+(.*)$", markdown, flags=re.MULTILINE)
    ]


def _html_steps(html: str) -> list[str]:
    return re.findall(r"<li>(.*?)</li>", html, flags=re.DOTALL)


def test_corpus_round_trip_preserves_structure_for_all_articles() -> None:
    """markdown → HTML → markdown keeps every heading and resolution step."""
    articles = LocalJSONSource(CORPUS_PATH).load_articles()

    for article in articles:
        html_out = markdown_to_html(article.body)
        back = html_to_markdown(html_out)

        assert _md_headings(back) == _md_headings(article.body), article.article_id
        assert _md_steps(back) == _md_steps(article.body), article.article_id
        # html side carries the same headings and step count
        assert html_out.count("<h2>") == len(_md_headings(article.body)), article.article_id
        assert len(_html_steps(html_out)) == len(_md_steps(article.body)), article.article_id


def test_fixture_round_trip_preserves_code_blocks() -> None:
    """Fixtures carry fenced code blocks — the round trip must keep them."""
    from fixtures.articles import POSTGRES_V2

    html_out = markdown_to_html(POSTGRES_V2["body"])
    assert 'class="language-sql"' in html_out
    assert "ALTER ROLE svc_app CONNECTION LIMIT 200;" in html_out
    back = html_to_markdown(html_out)
    assert "ALTER ROLE svc_app CONNECTION LIMIT 200;" in back
    assert "psql -U postgres" in back


@pytest.mark.parametrize(
    ("markdown", "must_contain", "must_not_contain"),
    [
        ("Text with <script>alert(1)</script> inside", "&lt;script&gt;", "<script>"),
        ("Value < 10 and > 5", "&lt; 10", "< 10"),
    ],
)
def test_dangerous_content_is_never_injected(
    markdown: str, must_contain: str, must_not_contain: str
) -> None:
    html_out = markdown_to_html(markdown)
    assert must_contain in html_out
    assert must_not_contain not in html_out
