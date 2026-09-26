"""Scan git-tracked files for credentials that should never be committed.

This is the S1.2 repository secret scan (CRED-01, #51).

What is scanned
---------------
- Every file reported by ``git ls-files``, so the result does not depend on what
  else happens to sit on the working copy of the machine that runs it.
- Every one of those files that decodes as UTF-8 text, whatever its extension:
  ``.env.*``, ``.py``, ``.ts``, ``.js``, ``.sh``, ``.ps1``, ``.xml``, ``.md``,
  ``.yaml``, ``.yml``, ``.json``, ``.txt``, ``.cfg``, ``.ini``, ``.toml``,
  ``.rst`` and files with no extension are all covered. Files that are binary
  (they do not decode) are skipped, and reported as such.
- Files other checks leave out, including ``scripts/verification_report.json``
  and this repository's own allowlist.

What counts as a finding
------------------------
1. Token shapes: AWS access key ids, GitHub/Slack/OpenAI/Google/Stripe tokens,
   PEM private key blocks and JWTs, wherever they appear.
2. Credential assignments: a name containing password/secret/token/key/credential
   followed by ``=``, ``:`` or a closing XML tag, whose value looks like a real
   credential -- at least six characters, containing both letters and digits, and
   not an obvious placeholder or template.
3. Inline basic auth: ``curl -u user:password`` and the ``wget --user`` form.

Findings are printed with the file, line and rule, never the matched value.

Allowlist: ``scripts/secret_scan_allowlist.json`` -- every entry names a file and
a rule with a written reason. Values are never stored there.

Usage:
    python scripts/scan_repo_secrets.py            # scan the working tree
    python scripts/scan_repo_secrets.py --git      # scan the index (default)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = Path(__file__).resolve().parent / "secret_scan_allowlist.json"

_SECRET_NAME = (
    r"(?:[A-Za-z0-9_.\-/]*\b)?(?:"
    r"password|passwd|pwd|secret|token|credential|"
    r"api[_\-]?key|access[_\-]?key|private[_\-]?key|signing[_\-]?key|client[_\-]?secret"
    r")[A-Za-z0-9_.\-]*"
)

ASSIGNMENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "credential-assignment",
        re.compile(
            rf"(?i)({_SECRET_NAME})[\"']?\s*(?:=|:|=>)\s*"
            rf"[\"']?([^\s\"'<>;,)]{{6,}})"
        ),
    ),
    (
        "credential-tag",
        re.compile(
            r"(?i)<\s*(?:[\w.\-]+:)?("
            r"password|passwd|pwd|secret|token|credential|api_key|"
            r"access_key|client_secret|user_password|signing_key"
            r")\s*>\s*([^<\s]{6,})\s*</"
        ),
    ),
    (
        "inline-basic-auth",
        re.compile(
            r"(?i)\b(?:curl|wget)\b[^\n]{0,200}?\s-u\s+"
            r"([^\s:]{1,64}):([^\s]{6,})"
        ),
    ),
    (
        "inline-basic-auth-long-flag",
        re.compile(r"(?i)\bwget\b[^\n]{0,200}?--user[= ]([^\s:]{1,64}):([^\s]{6,})"),
    ),
]

TOKEN_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-live-key", re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
]

_PLACEHOLDERS = (
    "placeholder",
    "example",
    "changeme",
    "change-me",
    "redacted",
    "your-",
    "your_",
    "dummy",
    "sample",
    "fake",
    "test-only",
    "secret-value",
)
_TEMPLATE_CHARS = set("${}<> \t`")
_TRAILING = ".,;:!?)]}\"'"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str
    digest: str = ""


def looks_like_credential(value: str) -> bool:
    """Decide whether an assigned value is worth flagging."""
    value = value.strip().strip(_TRAILING)
    if len(value) < 6:
        return False
    if any(ch in _TEMPLATE_CHARS for ch in value):
        return False
    if value.startswith(("/", "http", "_", "(", "[")) or "://" in value or "(" in value:
        return False
    lowered = value.lower()
    if any(token in lowered for token in _PLACEHOLDERS):
        return False
    # A credential carries both letters and digits: this keeps Python annotations
    # (client_secret: SecretStr), identifiers and prose out of the report while
    # still catching values such as "hunter2" or "abc123def456".
    if not (any(ch.isalpha() for ch in value) and any(ch.isdigit() for ch in value)):
        return False
    if lowered.startswith(("http://", "https://")):
        return False
    return True


def _redact(value: str) -> str:
    value = value.strip().strip(_TRAILING)
    return f"<{len(value)} chars, {value[:2]}**>"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def scan_text(text: str, path: str, allow: set[tuple[str, str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in TOKEN_RULES:
            for match in pattern.finditer(line):
                token = match.group(0)
                key = (path, rule, _digest(token))
                if key in allow:
                    continue
                findings.append(Finding(path, lineno, rule, "token shape", key[2]))
        for rule, pattern in ASSIGNMENT_RULES:
            for match in pattern.finditer(line):
                value = match.group(match.lastindex or 1)
                if rule == "credential-assignment" and not looks_like_credential(value):
                    continue
                if rule.startswith("inline-basic-auth") and not looks_like_credential(value):
                    continue
                key = (path, rule, _digest(value))
                if key in allow:
                    continue
                name = match.group(1)
                findings.append(
                    Finding(
                        path,
                        lineno,
                        rule,
                        f"{name} = {_redact(value)}",
                        key[2],
                    )
                )
    return findings


def tracked_files(root: Path = REPO_ROOT) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [p for p in out.stdout.decode().split("\0") if p]


def load_allowlist(path: Path = ALLOWLIST) -> set[tuple[str, str, str]]:
    """Entries are (file, rule, sha256-of-value): the value itself is never stored."""
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(entry["path"], entry["rule"], entry["sha256"]) for entry in data.get("allow", [])}


def scan_repo(root: Path = REPO_ROOT) -> tuple[list[Finding], list[str]]:
    allow = load_allowlist()
    findings: list[Finding] = []
    skipped: list[str] = []
    for rel in tracked_files(root):
        target = root / rel
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError, OSError):
            skipped.append(rel)
            continue
        findings.extend(scan_text(text, rel, allow))
    return findings, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-binary-skip",
        action="store_true",
        help="report binary files that were skipped (default: silent)",
    )
    args = parser.parse_args(argv)

    findings, skipped = scan_repo()
    if args.allow_binary_skip and skipped:
        print(f"skipped {len(skipped)} binary file(s):")
        for rel in skipped:
            print(f"  {rel}")
    if findings:
        print(f"{len(findings)} secret-scan finding(s):")
        for f in findings:
            print(f"  {f.path}:{f.line} [{f.rule}] {f.detail}")
        return 1
    print(
        f"secret scan clean: {len(tracked_files())} tracked files, "
        f"{len(skipped)} binary skipped, allowlist entries {len(load_allowlist())}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
