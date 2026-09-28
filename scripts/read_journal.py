import asyncio
from app.clients.servicenow_client import ServiceNowClient
from app.core.config import get_settings


async def main():
    async with ServiceNowClient(get_settings()) as client:
        inc = await client.find_incident_by_number("INC0010170")
        resp = await client._request(
            "GET",
            "/api/now/table/sys_journal_field",
            params={
                "sysparm_query": f"element_id={inc.sys_id}",
                "sysparm_fields": "element,value,sys_created_on",
            },
        )
        journals = resp if isinstance(resp, list) else resp.get("result", [])
        print(f"FOUND {len(journals)} JOURNAL ENTRIES:")
        for j in journals:
            print(f"[{j.get('sys_created_on')}] {j.get('element')}: {j.get('value')}")


if __name__ == "__main__":
    asyncio.run(main())
