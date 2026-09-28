import asyncio
from datetime import datetime, UTC
from app.clients.servicenow_client import ServiceNowClient
from app.models.incident import AIProcessingState, IncidentUpdatePayload
from app.core.config import get_settings


async def main():
    async with ServiceNowClient(get_settings()) as client:
        inc = await client.find_incident_by_number("INC0010170")
        print("UPDATING INCIDENT:", inc.number, inc.sys_id)
        solution = "Restart VPN gateway service and clear client DNS/session cache."
        payload = IncidentUpdatePayload(
            ai_processing_state=AIProcessingState.COMPLETE,
            ai_processing_end=datetime.now(UTC),
            ai_resolution=solution,
            ai_suggestion=solution,
            ai_human_review_required=False,
            work_notes=f"AI Suggested Response: human resolution approved by operator. Resolution: {solution}",
        )
        updated = await client.update_incident(inc.sys_id, payload)
        print("UPDATE SUCCESSFUL! Current state:", updated.ai_processing_state, "Resolution:", updated.ai_resolution)


if __name__ == "__main__":
    asyncio.run(main())
