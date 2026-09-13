"""Guard against the drift that #38 and #57 document.

Both files name paths. When a workstream moves or renames something, the entry
here keeps matching nothing and the failure is silent: a PR gets no label, or a
security-sensitive file ends up with no owner. These tests make that loud.

A pattern is allowed to match nothing only if it is listed in ``PLANNED``, which
records why and keeps the exemption visible in review.

``PLANNED`` entries expire by themselves: the moment the path they are waiting for
exists, the test *fails* until the entry is deleted. Without that, an exemption added
to cover an open PR would keep skipping after that PR merged, and the check would
quietly stop checking the paths it was written to protect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Patterns that intentionally match nothing yet, with the reason.
#: Remove the entry in the PR that creates the path.
PLANNED: dict[str, str] = {
    # Arrive with open pull requests. Drop the entry in the PR that lands the path.
    "scripts/verification_report.json": "lands with the S1.2 rework, PR #32",
    "servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_3.xml": (
        "lands with S1.3, PR #30"
    ),
    "src/app/publishing/**": "lands with S1.4 KB publishing, PR #39",
    "scripts/publish_kb.py": "lands with S1.4 KB publishing, PR #39",
    "tests/publishing/**": "lands with S1.4 KB publishing, PR #39",
}


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def _matches(pattern: str, files: list[str]) -> bool:
    """True if any tracked file matches a labeler-style or CODEOWNERS-style path."""
    p = pattern.strip().lstrip("/")
    if p.endswith("/**"):
        prefix = p[:-3].rstrip("/") + "/"
        return any(f.startswith(prefix) for f in files)
    if p.endswith("/"):
        return any(f.startswith(p) for f in files)
    if "*" in p:
        from fnmatch import fnmatch

        return any(fnmatch(f, p) for f in files)
    return any(f == p or f.startswith(p.rstrip("/") + "/") for f in files)


def _resolve_planned(pattern: str, files: list[str]) -> None:
    """Skip a still-pending exemption; fail once the path it waited for exists.

    This is what makes the exemption temporary. A ``PLANNED`` entry that outlives the
    PR it names would silently exempt a live path forever.
    """
    if pattern not in PLANNED:
        return
    if _matches(pattern, files):
        pytest.fail(
            f"{pattern!r} now matches a tracked file, so its PLANNED exemption is stale "
            f"({PLANNED[pattern]}). Delete the entry from PLANNED in this test."
        )
    pytest.skip(f"planned path: {PLANNED[pattern]}")


def _labeler_patterns() -> list[tuple[str, str]]:
    cfg = yaml.safe_load((REPO_ROOT / ".github" / "labeler.yml").read_text())
    found: list[tuple[str, str]] = []
    for label, rules in cfg.items():
        for rule in rules:
            for globs in rule.get("changed-files", []):
                for pattern in globs.get("any-glob-to-any-file", []):
                    found.append((label, pattern))
    return found


def _codeowners_patterns() -> list[str]:
    lines = (REPO_ROOT / ".github" / "CODEOWNERS").read_text().splitlines()
    out: list[str] = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pattern = line.split()[0]
        if pattern == "*":  # the fallback owner, matches everything by design
            continue
        out.append(pattern)
    return out


@pytest.mark.parametrize(
    ("label", "pattern"),
    _labeler_patterns(),
    ids=lambda v: str(v).replace("/", "_"),
)
def test_labeler_glob_matches_a_tracked_file(label: str, pattern: str) -> None:
    files = _tracked_files()
    _resolve_planned(pattern, files)
    assert _matches(pattern, files), (
        f"labeler.yml: {label!r} glob {pattern!r} matches no tracked file. "
        "Point it at where the code actually lives, remove it, or add it to "
        "PLANNED in this test with a reason."
    )


@pytest.mark.parametrize("pattern", _codeowners_patterns(), ids=lambda v: str(v).replace("/", "_"))
def test_codeowners_path_matches_a_tracked_file(pattern: str) -> None:
    files = _tracked_files()
    _resolve_planned(pattern, files)
    assert _matches(pattern, files), (
        f"CODEOWNERS: {pattern!r} matches no tracked file, so the rule owns nothing. "
        "Correct it, remove it, or add it to PLANNED in this test with a reason."
    )
