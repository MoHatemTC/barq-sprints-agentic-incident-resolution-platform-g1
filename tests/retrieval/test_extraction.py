"""Adversarial and functional test suite for HTML to Markdown extraction."""

from app.retrieval.extraction import html_to_markdown


def test_empty_or_none_input() -> None:
    assert html_to_markdown(None) == ""
    assert html_to_markdown("") == ""
    assert html_to_markdown("   \n\t  ") == ""


def test_nbsp_preservation_does_not_corrupt_cli_flags() -> None:
    # Crucial test from plan: &nbsp; must become standard space, not merged or non-breaking unicode
    raw_html = "<p>Execute <code>psql -U admin&nbsp;-h localhost&nbsp;-p 5432</code></p>"
    md = html_to_markdown(raw_html)
    assert "`psql -U admin -h localhost -p 5432`" in md
    assert "admin-h" not in md
    assert "\xa0" not in md


def test_fenced_code_block_with_language_class() -> None:
    raw_html = (
        "<h2>Resolution</h2>\n"
        '<pre><code class="language-sql">'
        "ALTER ROLE svc_app CONNECTION LIMIT 200;\n"
        "SELECT count(*) FROM pg_stat_activity;\n"
        "</code></pre>"
    )
    md = html_to_markdown(raw_html)
    assert "## Resolution" in md
    assert "```sql" in md
    assert "ALTER ROLE svc_app CONNECTION LIMIT 200;" in md
    assert "SELECT count(*) FROM pg_stat_activity;" in md
    assert md.endswith("```")


def test_inline_code_conversion() -> None:
    raw_html = (
        "<p>Configure <code>maxmemory-policy</code> to <code>allkeys-lru</code> in Redis.</p>"
    )
    md = html_to_markdown(raw_html)
    assert "`maxmemory-policy`" in md
    assert "`allkeys-lru`" in md


def test_html_entity_decoding() -> None:
    raw_html = "<p>Logs show error &lt;53300&gt; &amp; &quot;FATAL: too many connections&quot;.</p>"
    md = html_to_markdown(raw_html)
    assert '<53300> & "FATAL: too many connections"' in md


def test_atx_heading_styles() -> None:
    raw_html = "<h1>Main Title</h1><h2>Symptom</h2><h3>Detail</h3><p>Text</p>"
    md = html_to_markdown(raw_html)
    assert "# Main Title" in md
    assert "## Symptom" in md
    assert "### Detail" in md


def test_strips_scripts_and_styles() -> None:
    raw_html = (
        "<script>evil_tracker();</script>"
        "<style>body { color: red; }</style>"
        "<p>Safe production resolution text.</p>"
    )
    md = html_to_markdown(raw_html)
    assert "Safe production resolution text." in md
    assert "evil_tracker" not in md
    assert "color: red" not in md


def test_bullet_and_numbered_lists() -> None:
    raw_html = "<ul><li>First check connections</li><li>Second reload conf</li></ul>"
    md = html_to_markdown(raw_html)
    assert "- First check connections" in md
    assert "- Second reload conf" in md


def test_code_block_with_bash_comments_survives() -> None:
    raw_html = (
        '<pre><code class="language-bash">'
        "# Step 1: verify status\n"
        "systemctl status postgresql\n"
        "## Step 2: reload\n"
        "psql -U postgres -c 'SELECT pg_reload_conf();'\n"
        "</code></pre>"
    )
    md = html_to_markdown(raw_html)
    assert "```bash" in md
    assert "# Step 1: verify status" in md
    assert "## Step 2: reload" in md
    assert "SELECT pg_reload_conf();" in md
