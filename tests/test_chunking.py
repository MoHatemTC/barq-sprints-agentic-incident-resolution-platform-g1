"""Unit tests for the markdown chunking engine (src/app/retrieval/chunking.py)."""

import json
from pathlib import Path

import pytest

from app.models.knowledge import Article, ArticleChunk, KnowledgePayload
from app.retrieval.chunking import (
    balance_code_fences,
    chunk_article,
    chunk_articles,
    chunk_markdown,
    extract_section_name,
)


def test_chunk_empty_or_whitespace() -> None:
    assert chunk_markdown("", article_id="KB-TEST-001-v1.0") == []
    assert chunk_markdown("   \n\n\t  ", article_id="KB-TEST-001-v1.0") == []


def test_chunk_plain_markdown_without_headers() -> None:
    text = "This is a simple article body without any markdown headers."
    chunks = chunk_markdown(text, article_id="KB-TEST-001-v1.0")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.article_id == "KB-TEST-001-v1.0"
    assert chunk.chunk_index == 0
    assert chunk.total_chunks == 1
    assert chunk.section == "General"
    assert chunk.text == text
    assert chunk.chunk_id == "KB-TEST-001-v1.0#c0"


def test_chunk_standard_three_section_article() -> None:
    content = (
        "## Symptom\n"
        "PostgreSQL client reports connection timeout.\n\n"
        "## Root Cause\n"
        "Exceeded max_connections limit of 100.\n\n"
        "## Resolution\n"
        "Execute the following to increase connection limits:\n"
        "```sql\n"
        "ALTER SYSTEM SET max_connections = 200;\n"
        "```\n"
    )
    chunks = chunk_markdown(content, article_id="KB-DB-001-v1.0")

    assert len(chunks) == 3
    assert [c.section for c in chunks] == ["Symptom", "Root Cause", "Resolution"]
    for i, c in enumerate(chunks):
        assert c.chunk_index == i
        assert c.total_chunks == 3
        assert c.article_id == "KB-DB-001-v1.0"
        assert c.chunk_id == f"KB-DB-001-v1.0#c{i}"
    assert "ALTER SYSTEM SET max_connections" in chunks[2].text


def test_chunk_ignores_headers_inside_code_fences() -> None:
    content = (
        "## Symptom\n"
        "Bash script fails unexpectedly:\n"
        "```bash\n"
        "# This is a bash comment\n"
        "## This looks like H2 but is inside a code block\n"
        "### This looks like H3\n"
        "echo 'running diagnostic'\n"
        "```\n\n"
        "## Resolution\n"
        "Fix the script path.\n"
    )
    chunks = chunk_markdown(content, article_id="KB-OPS-001-v1.0")

    assert len(chunks) == 2
    assert chunks[0].section == "Symptom"
    assert chunks[1].section == "Resolution"
    assert "## This looks like H2" in chunks[0].text
    assert "```bash" in chunks[0].text


def test_chunk_nested_subsections_creates_breadcrumbs() -> None:
    content = (
        "# Database Operations\n\n"
        "General overview of maintenance.\n\n"
        "## Maintenance Procedures\n\n"
        "### Step 1: Drain Traffic\n"
        "Redirect traffic away from the replica.\n\n"
        "### Step 2: Vacuum Analyze\n"
        "Run maintenance on key tables.\n"
    )
    chunks = chunk_markdown(content, article_id="KB-DB-002-v1.0")

    assert len(chunks) == 3
    assert chunks[0].section == "Overview"
    assert chunks[1].section == "Maintenance Procedures > Step 1: Drain Traffic"
    assert chunks[2].section == "Maintenance Procedures > Step 2: Vacuum Analyze"


def test_extract_section_name_hierarchy() -> None:
    assert extract_section_name({}) == "General"
    assert extract_section_name({"Title": "Guide"}) == "Overview"
    assert extract_section_name({"Section": "Resolution"}) == "Resolution"
    assert (
        extract_section_name({"Section": "Resolution", "Subsection": "Step 1"})
        == "Resolution > Step 1"
    )
    assert extract_section_name({"Subsection": "Step 1"}) == "Step 1"


def test_chunk_oversized_section_splits_recursively() -> None:
    long_paragraph = "High traffic spikes overwhelm redis cache layer. " * 30
    content = f"## Incident Details\n{long_paragraph}"

    chunks = chunk_markdown(content, article_id="KB-REDIS-001-v1.0", chunk_size=300)

    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert c.section == "Incident Details"
        assert c.chunk_index == i
        assert c.total_chunks == len(chunks)
        assert len(c.text) <= 350


def test_balance_code_fences_single_or_empty() -> None:
    assert balance_code_fences([]) == []
    assert balance_code_fences(["simple text"]) == ["simple text"]


def test_balance_code_fences_repairs_split_blocks() -> None:
    part1 = "Check logs:\n```bash\njournalctl -u postgresql -f\n"
    part2 = "grep ERROR\n```\nAll done."

    repaired = balance_code_fences([part1, part2])

    assert len(repaired) == 2
    # Part 1 closed
    assert repaired[0].endswith("```\n")
    # Part 2 reopened with ```bash
    assert repaired[1].startswith("```bash\n")
    # Both parts have even number of ```
    assert repaired[0].count("```") % 2 == 0
    assert repaired[1].count("```") % 2 == 0


def test_chunk_article_model_integration(sample_articles: list[Article]) -> None:
    article = sample_articles[0]
    chunks = chunk_article(article)

    assert len(chunks) >= 1
    for chunk in chunks:
        assert isinstance(chunk, ArticleChunk)
        assert chunk.article_id == article.article_id
        assert chunk.chunk_index < chunk.total_chunks
        # Ensure it converts cleanly to KnowledgePayload
        payload = KnowledgePayload.from_chunk(article, chunk)
        assert payload.article_id == article.article_id
        assert payload.section == chunk.section
        assert payload.chunk_text == chunk.text


def test_chunk_articles_batch(sample_articles: list[Article]) -> None:
    chunks = chunk_articles(sample_articles)
    assert len(chunks) >= len(sample_articles)
    article_ids = {c.article_id for c in chunks}
    for a in sample_articles:
        assert a.article_id in article_ids


def test_chunk_all_corpus_articles() -> None:
    corpus_file = Path("data/corpus/barq_articles.json")
    if not corpus_file.exists():
        pytest.skip("Real barq_articles.json not yet extracted")

    with open(corpus_file, encoding="utf-8") as f:
        data = json.load(f)

    articles = [Article.model_validate(item) for item in data]
    chunks = chunk_articles(articles)

    assert len(articles) == 11
    assert len(chunks) > 0
    for c in chunks:
        assert c.chunk_index < c.total_chunks


def test_chunk_large_complex_article_with_subsections_and_code_blocks() -> None:
    content = (
        "# PostgreSQL Connection Pool & Memory Saturation Recovery\n\n"
        "Comprehensive runbook for diagnosing and remediating connection spikes.\n\n"
        "## Symptom\n"
        "Application services report HTTP 500 errors.\n"
        "```sql\n"
        "-- Diagnostic query to check backend states\n"
        "-- Note: ## comments inside code blocks must NOT cause false splits!\n"
        "FATAL: 53300: remaining connection slots are reserved\n"
        "```\n\n"
        "## Root Cause\n"
        "A surge in checkout API requests generated over 800 unpooled backend connections.\n\n"
        "## Resolution\n\n"
        "### Step 1: Terminate Blocking Transactions\n"
        "First, identify and terminate the oldest blocking backends holding locks:\n"
        "```sql\n"
        "SELECT pid, pg_terminate_backend(pid) FROM pg_stat_activity;\n"
        "```\n\n"
        "### Step 2: Configure PgBouncer Pool Limits\n"
        "Edit the PgBouncer configuration file:\n"
        "```ini\n"
        "[pgbouncer]\n"
        "pool_mode = transaction\n"
        "max_client_conn = 2000\n"
        "```\n"
        "Reload PgBouncer:\n"
        "```bash\n"
        "sudo systemctl reload pgbouncer\n"
        "```\n\n"
        "### Step 3: Post-Remediation Health Checks\n"
        "Verify connection saturation drops:\n"
        "```bash\n"
        "psql -p 6432 -U pgbouncer -c 'SHOW POOLS;'\n"
        "```\n"
    )
    chunks = chunk_markdown(content, article_id="KB-DB-005-v1.0")

    assert len(chunks) == 6
    assert chunks[0].section == "Overview"
    assert chunks[1].section == "Symptom"
    assert chunks[2].section == "Root Cause"
    assert chunks[3].section == "Resolution > Step 1: Terminate Blocking Transactions"
    assert chunks[4].section == "Resolution > Step 2: Configure PgBouncer Pool Limits"
    assert chunks[5].section == "Resolution > Step 3: Post-Remediation Health Checks"

    for i, c in enumerate(chunks):
        assert c.chunk_index == i
        assert c.total_chunks == 6
        assert c.chunk_id == f"KB-DB-005-v1.0#c{i}"
        fences = [ln for ln in c.text.splitlines() if ln.strip().startswith("```")]
        assert len(fences) % 2 == 0
