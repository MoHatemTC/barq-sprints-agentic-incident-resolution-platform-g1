"""CLI helper to list, inspect, approve, or refuse incidents against the running platform."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import urllib.request
from pathlib import Path

from dotenv import dotenv_values


def get_token(base_url: str, env: dict) -> str:
    client_id = env.get("OPERATOR_CLIENT_ID", "barq-operator")
    secret = env.get("WEBHOOK_AUTH_TOKEN")
    if not secret:
        raise ValueError("WEBHOOK_AUTH_TOKEN not found in .env")

    auth_header = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    token_req = urllib.request.Request(
        f"{base_url}/api/v1/oauth/token",
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(token_req) as resp:
        return json.loads(resp.read())["access_token"]


async def get_sys_id_for_number(number: str) -> str:
    from app.clients.servicenow_client import ServiceNowClient
    from app.core.config import get_settings

    async with ServiceNowClient(get_settings()) as client:
        inc = await client.find_incident_by_number(number)
        if not inc:
            raise ValueError(f"Incident {number} not found in ServiceNow")
        return inc.sys_id


def resolve_execution_id(
    base_url: str,
    headers: dict,
    number: str | None,
    execution_id: str | None,
) -> str:
    if execution_id:
        return execution_id
    if not number:
        raise ValueError("Must provide either --number INCXXXX or --execution-id <UUID>")

    sys_id = asyncio.run(get_sys_id_for_number(number))
    req = urllib.request.Request(
        f"{base_url}/api/v1/incidents/{sys_id}/executions",
        headers=headers,
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
        executions = data.get("executions", [])

    # Find execution awaiting approval
    for ex in executions:
        if ex.get("status") == "awaiting_approval":
            return ex["execution_id"]

    # Otherwise return the most recent
    if executions:
        return executions[0]["execution_id"]
    raise ValueError(f"No executions found for incident {number}")


def list_pending_approvals(base_url: str, headers: dict) -> None:
    # Query Postgres directly for executions awaiting approval
    import psycopg

    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    conn_str = (
        f"postgresql://{env.get('POSTGRES_USER','postgres')}:"
        f"{env.get('POSTGRES_PASSWORD','postgres')}@"
        f"{env.get('POSTGRES_HOST','localhost')}:"
        f"{env.get('POSTGRES_PORT','5432')}/"
        f"{env.get('POSTGRES_DB','barq_incident_dev')}"
    )

    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT execution_id, incident_sys_id, started_at "
                    "FROM executions WHERE status = 'awaiting_approval' "
                    "ORDER BY started_at DESC LIMIT 20;"
                )
                rows = cur.fetchall()

        if not rows:
            print("\nNo incidents currently awaiting approval.")
            return

        print(f"\nFound {len(rows)} execution(s) awaiting approval:\n")
        print(f"{'Incident sys_id':<34} | {'Execution ID':<38} | {'Started At'}")
        print("-" * 100)
        for row in rows:
            print(f"{row[1]:<34} | {row[0]:<38} | {row[2]}")
    except Exception as e:
        print("Note: Could not query database directly:", e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve or refuse incidents on EC2")
    parser.add_argument("--number", help="ServiceNow incident number (e.g. INC0010170)")
    parser.add_argument("--execution-id", help="Execution UUID")
    parser.add_argument(
        "--action",
        choices=["inspect", "approve", "reject", "list"],
        default="inspect",
    )
    parser.add_argument("--reason", default="Approved by support engineer")
    parser.add_argument(
        "--solution",
        help="Human resolution solution text (recommended for approvals)",
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    env_path = Path(__file__).resolve().parents[1] / ".env"
    env = dotenv_values(env_path)
    token = get_token(args.base_url, env)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if args.action == "list":
        list_pending_approvals(args.base_url, headers)
        return

    execution_id = resolve_execution_id(args.base_url, headers, args.number, args.execution_id)

    # Inspect
    pending_req = urllib.request.Request(
        f"{args.base_url}/api/v1/approvals/pending/{execution_id}",
        headers=headers,
    )
    with urllib.request.urlopen(pending_req) as resp:
        pending_data = json.loads(resp.read())

    incident_info = (pending_data.get("facts") or {}).get("incident") or {}
    print("\n=======================================================")
    num = incident_info.get("number", "Unknown")
    desc = incident_info.get("short_description", "")
    print(f"Incident:    {num} ({desc})")
    print(f"Execution:   {execution_id}")
    print(f"Status:      {pending_data.get('decision') or 'awaiting_approval'}")
    if pending_data.get("brief"):
        print(f"AI Brief:    {pending_data['brief'].get('incident_summary')}")
        print(f"Judgment:    {pending_data['brief'].get('judgment_required')}")
    print("=======================================================\n")

    if args.action == "inspect":
        print("To approve: run with --action approve [--solution 'your resolution text']")
        print("To refuse:  run with --action reject [--reason 'why rejected']")
        return

    # Decide
    decision = "approved" if args.action == "approve" else "rejected"
    payload = {
        "decision": decision,
        "reason": args.reason,
    }
    if args.solution:
        payload["solution"] = args.solution

    decide_req = urllib.request.Request(
        f"{args.base_url}/api/v1/approvals/{execution_id}/decide",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(decide_req) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print("Error submitting decision:", e.read().decode())
        return

    print("================ DECISION APPLIED ================")
    print(f"Status:      {result.get('status')}")
    print(f"Verdict:     {result.get('decision')} by {result.get('decided_by')}")
    print(f"ServiceNow:  {result.get('servicenow_write')}")
    if result.get("knowledge_capture"):
        art_num = result["knowledge_capture"].get("article_number")
        art_status = result["knowledge_capture"].get("status")
        print(f"KB Learned:  {art_num} ({art_status})")
    print("==================================================")
    print("Graph execution resumed and saved in database!")


if __name__ == "__main__":
    main()
