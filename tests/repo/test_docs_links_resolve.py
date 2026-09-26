"""Every relative link in a tracked markdown file must resolve (#36).

`../../milestone/1`, `../../pull/1` and `../../issues/N` were written as though the
document lived one directory deeper than it does, so the milestone and PR links in
README and TEAM 404'd, and several docs pointed at `file:///d:/.../src/...` paths
that only exist on the machine that wrote them.

The rules: no `file:///` links, and every relative target resolves against the
document's own directory. External links are not checked here — that needs the
network and would make CI flaky.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

LINK = re.compile(r"\]\(([^)\s]+)\)")  # also matches the outer link of a [![badge](img)](target)
SKIP_SCHEMES = ("http://", "https://", "#", "mailto:")


def _tracked_markdown() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split()
    assert out, "no tracked markdown found — the check is not running"
    return [pathlib.Path(p) for p in out]


def test_no_file_urls_in_docs() -> None:
    offenders = []
    for doc in _tracked_markdown():
        for target in LINK.findall(doc.read_text(encoding="utf-8", errors="ignore")):
            if target.startswith("file:///"):
                offenders.append(f"{doc}: {target}")
    assert offenders == [], "\n".join(offenders)


def test_every_relative_link_resolves() -> None:
    offenders = []
    for doc in _tracked_markdown():
        for target in LINK.findall(doc.read_text(encoding="utf-8", errors="ignore")):
            if target.startswith(SKIP_SCHEMES):
                continue
            rel = target.split("#")[0]
            if not rel:
                continue  # in-page anchor
            if not (doc.parent / rel).resolve().exists():
                offenders.append(f"{doc}: {target}")
    assert offenders == [], "\n".join(offenders)
