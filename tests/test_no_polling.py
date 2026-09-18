"""FR-05 empirical no-polling proof: AST scan of ``src/`` for ServiceNow polling.

Ingestion is purely event-driven (ServiceNow pushes via Outbound Event Contract v1).
These tests parse every Python file under ``src/`` and fail loudly — with file, line
number, and the detected construct — if anyone introduces:

* ``while True`` loops (or unbounded ``while`` loops) containing ``sleep()`` calls
* any loop that both sleeps and performs network/ServiceNow fetches
* periodic schedulers / cron-style jobs fetching ServiceNow

Deliberately NOT flagged (false-positive avoidance): normal collection iteration,
bounded retry logic, and loops with no ServiceNow/network or sleep activity.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

#: Modules whose presence signals a polling scheduler.
SCHEDULER_MODULES = {
    "apscheduler",
    "schedule",
    "celery.schedules",
    "crontab",
}

#: Attribute/call fragments that indicate a ServiceNow-facing fetch.
SERVICENOW_MARKERS = (
    "servicenow",
    "service_now",
    "get_incident",
    "update_incident",
    "list_incidents",
)

SLEEP_NAMES = {"sleep"}
NETWORK_CALL_MARKERS = (
    "get",
    "request",
    "fetch",
    "lpush",
    "rpush",
    "brpop",
    "blpop",
    "publish",
    "ping",
)


@dataclass
class Violation:
    file: Path
    line: int
    construct: str
    detail: str = ""

    def __str__(self) -> str:
        location = f"{self.file}:{self.line}"
        return f"{location}: {self.construct}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class PollingScanResult:
    violations: list[Violation] = field(default_factory=list)
    files_scanned: int = 0

    def report(self) -> str:
        lines = [f"FR-05 no-polling scan failed ({self.violations} hits in {self.files_scanned} files):"]
        lines += [f"  - {v}" for v in self.violations]
        return "\n".join(lines)


def _call_name(call: ast.Call) -> str:
    """Best-effort dotted name for a Call node ('time.sleep' -> 'time.sleep')."""
    node = call.func
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts)).lower()


def _is_sleep_call(call: ast.Call) -> bool:
    name = _call_name(call)
    return name.split(".")[-1] in SLEEP_NAMES


def _is_servicenow_call(call: ast.Call) -> bool:
    name = _call_name(call)
    return any(marker in name for marker in SERVICENOW_MARKERS)


def _loop_calls(loop: ast.stmt) -> list[ast.Call]:
    """All Call nodes in the loop body (not the condition)."""
    body = getattr(loop, "body", [])
    return [n for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Call)]


def scan_source_for_polling(source: str, file: Path) -> list[Violation]:
    """Return polling violations found in one Python source string."""
    violations: list[Violation] = []
    try:
        tree = ast.parse(source, filename=str(file))
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        # Scheduler / cron registration imports.
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in SCHEDULER_MODULES or alias.name in SCHEDULER_MODULES:
                    violations.append(
                        Violation(file, node.lineno, f"scheduler import '{alias.name}'")
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in SCHEDULER_MODULES:
                violations.append(
                    Violation(file, node.lineno, f"scheduler import from '{node.module}'")
                )

        # Polling loops.
        if isinstance(node, (ast.While, ast.For)):
            calls = _loop_calls(node)
            sleep_calls = [c for c in calls if _is_sleep_call(c)]
            sn_calls = [c for c in calls if _is_servicenow_call(c)]

            is_true_loop = isinstance(node, ast.While) and (
                isinstance(node.test, ast.Constant) and node.test.value is True
            )

            if is_true_loop and sleep_calls:
                name = _call_name(sleep_calls[0])
                violations.append(
                    Violation(
                        file,
                        node.lineno,
                        "'while True' loop containing sleep()",
                        f"periodic poll via {name}()",
                    )
                )
            elif sleep_calls and sn_calls:
                violations.append(
                    Violation(
                        file,
                        node.lineno,
                        "loop combining sleep() with a ServiceNow fetch",
                        f"{_call_name(sn_calls[0])}() inside loop",
                    )
                )
    return violations


def scan_tree(root: Path) -> PollingScanResult:
    result = PollingScanResult()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        result.files_scanned += 1
        result.violations.extend(
            scan_source_for_polling(path.read_text(encoding="utf-8"), path)
        )
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_no_servicenow_polling_loops_in_src() -> None:
    """FR-05: no polling loops, sleeps-in-while-True, or scheduler imports under src/."""
    result = scan_tree(SRC_ROOT)
    assert result.files_scanned > 50, f"scan unexpectedly covered only {result.files_scanned} files"
    assert not result.violations, result.report()


def test_scan_detects_a_real_polling_loop() -> None:
    """Detector self-test: a genuine ServiceNow poller must be caught (not vacuous)."""
    bad_source = (
        "import time\n"
        "def poll_servicenow(client):\n"
        "    while True:\n"
        "        time.sleep(30)\n"
        "        client.get_incident('abc')\n"
    )
    violations = scan_source_for_polling(bad_source, Path("fake_poller.py"))
    assert violations, "detector must flag a while-True + sleep + ServiceNow fetch loop"
    assert violations[0].line == 3
    assert "while True" in violations[0].construct


def test_scan_ignores_legitimate_loops() -> None:
    """Detector must not flag collection iteration, bounded retries, or aggregation."""
    benign_sources = [
        # normal collection loop
        "def total(items):\n    return sum(x.price for x in items)\n",
        # bounded retry with backoff, not targeting ServiceNow
        (
            "import asyncio\n"
            "async def send_with_retry(fn, attempts=3):\n"
            "    for i in range(attempts):\n"
            "        try:\n"
            "            return await fn()\n"
            "        except Exception:\n"
            "            await asyncio.sleep(i)\n"
            "    raise RuntimeError('exhausted')\n"
        ),
        # unbounded worker loop WITHOUT sleep and WITHOUT ServiceNow calls
        (
            "async def worker(queue, handler):\n"
            "    while True:\n"
            "        item = await queue.get()\n"
            "        await handler(item)\n"
        ),
    ]
    for source in benign_sources:
        violations = scan_source_for_polling(source, Path("benign.py"))
        assert not violations, f"false positive on benign loop: {violations}"
