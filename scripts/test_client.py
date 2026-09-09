import asyncio
import os

from app.clients.servicenow_client import ServiceNowClient
from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()
    sys_id = os.environ["SERVICENOW_TEST_INCIDENT_SYS_ID"]

    async with ServiceNowClient(settings) as client:
        incident = await client.get_incident(sys_id)
        print(incident)


if __name__ == "__main__":
    asyncio.run(main())
