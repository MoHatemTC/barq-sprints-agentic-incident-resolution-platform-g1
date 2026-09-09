import asyncio
import os

from app.clients.servicenow_client import ServiceNowClient
from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()
    sys_id = os.environ["SERVICENOW_TEST_INCIDENT_SYS_ID"]
    number = os.environ["SERVICENOW_TEST_INCIDENT_NUMBER"]
    async with ServiceNowClient(settings) as client:
        print(f"Fetching incident with sys_id: {sys_id}")
        incident = await client.get_incident(sys_id)
        print(incident)

        print(f"Fetching incident with number: {number}")
        incident_by_number = await client.find_incident_by_number(number)
        print(incident_by_number)


if __name__ == "__main__":
    asyncio.run(main())
