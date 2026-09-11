import asyncio
import os
import sys
from datetime import UTC, datetime

import structlog

from app.clients.servicenow_client import ServiceNowClient
from app.core.config import Settings
from app.exceptions.servicenow import ServiceNowError
from app.models.execution_log import ExecutionLogCreatePayload, ExecutionStatus
from app.models.incident import AIProcessingState, IncidentUpdatePayload

logger = structlog.get_logger(__name__)


async def test_servicenow_client() -> None:
    settings = Settings()

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

    async with ServiceNowClient(settings) as client:
        try:
            # 1. Test Fetching by SYS_ID
            logger.info("Testing get_incident()", sys_id=sys_id)
            incident = await client.get_incident(sys_id)
            logger.info(
                "Successfully retrieved incident",
                number=incident.number,
                state=incident.state,
            )

            # 2. Test Fetching by Number
            logger.info("Testing find_incident_by_number()", number=number)
            incident_by_number = await client.find_incident_by_number(number)
            if incident_by_number:
                logger.info(
                    "Successfully found incident",
                    sys_id=incident_by_number.sys_id,
                )
            else:
                logger.warning("Incident not found by number", number=number)

            # 3. Test Updating the Incident fields
            logger.info("Testing update_incident()")
            payload = IncidentUpdatePayload(
                ai_processing_state=AIProcessingState.IN_PROGRESS,
                ai_classification="software_issue",
                ai_confidence=0.88,
                ai_processing_start=datetime.now(UTC),
            )
            updated_incident = await client.update_incident(sys_id, payload)
            logger.info(
                "Successfully updated incident",
                ai_processing_state=updated_incident.ai_processing_state,
                ai_confidence=updated_incident.ai_confidence,
            )

            # 4. Test Adding Work Notes via dedicated method
            logger.info("Testing add_work_note()")
            work_note_text = f"Automated test note added at {
                datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
            }"
            incident_with_note = await client.add_work_note(sys_id, work_note_text)
            logger.info(
                "Successfully added work note",
                sys_id=incident_with_note.sys_id,
            )

            # 5. Test Completion Validation Rule
            logger.info("Testing update_incident() (Marking Complete)")
            complete_payload = IncidentUpdatePayload(
                ai_processing_state=AIProcessingState.COMPLETE,
                ai_processing_end=datetime.now(UTC),
                ai_resolution="Restarted the application server to clear the cache loop.",
            )
            final_incident = await client.update_incident(sys_id, complete_payload)
            logger.info(
                "Successfully marked incident as complete",
                resolution=final_incident.ai_resolution,
            )

            # 6. Test Creating AI Execution Log
            logger.info("Testing write_execution_log()")
            log_payload = ExecutionLogCreatePayload(
                incident_sys_id=sys_id,
                execution_id=f"exec_test_{int(datetime.now(UTC).timestamp())}",
                agent="test_client_script",
                action="verify_execution_log",
                status=ExecutionStatus.SUCCEEDED,
                timestamp=datetime.now(UTC),
                result="Execution log entry successfully validated from test_client.py",
            )
            log_entry = await client.write_execution_log(log_payload)
            if log_entry:
                logger.info(
                    "Successfully created execution log entry",
                    sys_id=log_entry.sys_id,
                    execution_id=log_entry.execution_id,
                )
            else:
                logger.error(
                    "Failed to create execution log entry. "
                    "Verify that table x_2215032_ai_inc_0_ai_execution_log exists in ServiceNow."
                )

        except ServiceNowError as exc:
            logger.error(
                "ServiceNow API Error",
                error=str(exc),
                status_code=getattr(exc, "status_code", None),
                details=getattr(exc, "details", None),
            )
        except Exception:
            logger.exception("Unexpected error occurred during client test")


if __name__ == "__main__":
    asyncio.run(test_servicenow_client())
