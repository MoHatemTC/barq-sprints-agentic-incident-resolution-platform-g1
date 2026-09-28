import asyncio
from app.clients.servicenow_client import ServiceNowClient
from app.core.config import get_settings


async def main():
    async with ServiceNowClient(get_settings()) as client:
        inc = await client.find_incident_by_number("INC0010170")
        print("FOUND INCIDENT:", inc.number, inc.sys_id)
        raw = await client.get_incident(inc.sys_id)
        d = raw.model_dump()
        for k in sorted(d.keys()):
            if "ai_" in k or "state" in k or "work_note" in k or "note" in k:
                print(f"  {k}: {d[k]}")


if __name__ == "__main__":
    asyncio.run(main())
