import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from app.clients.servicenow_client import ServiceNowClient
from app.core.config import Settings
from app.core.logging import configure_logging
from app.exceptions.servicenow import ServiceNowError
from app.models.execution_log import (
    ExecutionAction,
    ExecutionLogCreatePayload,
    ExecutionStatus,
)
from app.models.incident import AIProcessingState, IncidentUpdatePayload

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)

logger = structlog.get_logger(__name__)


@dataclass
class StepResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    results: list[StepResult] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append(StepResult(name, passed, detail))
        status = "PASS" if passed else "FAIL"
        logger.info(f"[{status}] {name}", detail=detail)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def print_summary(self) -> None:
        print("\n--- SUMMARY ---")
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] {r.name}" + (f" — {r.detail}" if r.detail else ""))
        n_fail = sum(1 for r in self.results if not r.passed)
        print(f"\n{len(self.results) - n_fail}/{len(self.results)} steps passed.")


async def run(sys_id: str, number: str, allow_writes: bool) -> Report:
    report = Report()
    settings = Settings()

    async with ServiceNowClient(settings) as client:
        # 1. Fetch by sys_id
        try:
            incident = await client.get_incident(sys_id)
            report.record("get_incident", True, f"number={incident.number} state={incident.state}")
        except ServiceNowError as exc:
            report.record("get_incident", False, str(exc))
            return report  # can't proceed without a real incident

        # 2. Fetch by number
        try:
            incident_by_number = await client.find_incident_by_number(number)
            report.record(
                "find_incident_by_number",
                incident_by_number is not None,
                "found" if incident_by_number else "not found",
            )
        except ServiceNowError as exc:
            report.record("find_incident_by_number", False, str(exc))

        if not allow_writes:
            report.record(
                "writes_skipped",
                True,
                "--allow-writes not set; update/complete/log steps skipped",
            )
            return report

        # 3. Update fields
        try:
            payload = IncidentUpdatePayload(
                ai_processing_state=AIProcessingState.IN_PROGRESS,
                ai_classification="software_issue",
                ai_confidence=0.88,
                ai_processing_start=datetime.now(UTC),
            )
            updated = await client.update_incident(sys_id, payload)
            report.record("update_incident", True, f"state={updated.ai_processing_state}")
        except ServiceNowError as exc:
            report.record("update_incident", False, str(exc))

        # 4. Work note
        try:
            note = f"Automated test note added at {datetime.now(UTC):%Y-%m-%d %H:%M:%S UTC}"
            await client.add_work_note(sys_id, note)
            report.record("add_work_note", True)
        except ServiceNowError as exc:
            report.record("add_work_note", False, str(exc))

        # 5. Completion (never invent a resolution; require explicit opt-in text)
        try:
            resolution = os.environ.get("SERVICENOW_TEST_RESOLUTION_TEXT")
            if not resolution:
                report.record(
                    "update_incident_complete",
                    False,
                    "SERVICENOW_TEST_RESOLUTION_TEXT not set; refusing to fabricate a resolution",
                )
            else:
                complete_payload = IncidentUpdatePayload(
                    ai_processing_state=AIProcessingState.COMPLETE,
                    ai_processing_end=datetime.now(UTC),
                    ai_resolution=resolution,
                )
                final = await client.update_incident(sys_id, complete_payload)
                report.record(
                    "update_incident_complete",
                    True,
                    f"resolution={final.ai_resolution}",
                )
        except ServiceNowError as exc:
            report.record("update_incident_complete", False, str(exc))

        # 6. Execution log (succeeded)
        try:
            log_payload = ExecutionLogCreatePayload(
                incident_sys_id=sys_id,
                execution_id=f"exec_test_{int(datetime.now(UTC).timestamp())}",
                agent="test_client_script",
                action=ExecutionAction.EXECUTE,
                status=ExecutionStatus.SUCCEEDED,
                timestamp=datetime.now(UTC),
                result="Execution log entry successfully validated from test_client.py",
            )
            entry = await client.write_execution_log(log_payload)
            report.record(
                "write_execution_log_succeeded",
                entry is not None,
                (f"sys_id={entry.sys_id}" if entry else "write_execution_log returned None"),
            )
        except ServiceNowError as exc:
            report.record("write_execution_log_succeeded", False, str(exc))

        # 7. Execution log (abandoned)
        try:
            abandoned_payload = ExecutionLogCreatePayload(
                incident_sys_id=sys_id,
                execution_id=f"exec_abandoned_{int(datetime.now(UTC).timestamp())}",
                agent="test_client_script",
                action=ExecutionAction.ESCALATE,
                status=ExecutionStatus.ABANDONED,
                timestamp=datetime.now(UTC),
                result="Agent abandoned execution due to lack of response context.",
            )
            abandoned_entry = await client.write_execution_log(abandoned_payload)
            report.record(
                "write_execution_log_abandoned",
                abandoned_entry is not None,
                (f"sys_id={abandoned_entry.sys_id}" if abandoned_entry else "returned None"),
            )
        except ServiceNowError as exc:
            report.record("write_execution_log_abandoned", False, str(exc))

    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Perform update/complete/log-write steps against the target incident. "
        "Without this flag, only read-only steps (get/find) run.",
    )
    args = parser.parse_args()

    sys_id = os.environ.get("SERVICENOW_TEST_INCIDENT_SYS_ID")
    number = os.environ.get("SERVICENOW_TEST_INCIDENT_NUMBER")
    if not sys_id or not number:
        logger.error(
            "Missing environment variables",
            required=[
                "SERVICENOW_TEST_INCIDENT_SYS_ID",
                "SERVICENOW_TEST_INCIDENT_NUMBER",
            ],
        )
        sys.exit(1)

    if args.allow_writes:
        logger.warning(
            "allow_writes is set — this WILL mutate a real ServiceNow incident",
            sys_id=sys_id,
        )

    try:
        report = asyncio.run(run(sys_id, number, args.allow_writes))
    except Exception:
        logger.exception("Unexpected error occurred during client test")
        sys.exit(1)

    report.print_summary()
    sys.exit(0 if report.all_passed else 1)


if __name__ == "__main__":
    main()
