"""Verify PR191 approvals against real services using isolated PDI fixtures and a local database.

Reads the private workspace env. Admin credentials are used only for fixture setup;
the API and worker use the integration identity. No EC2 deployment is performed.
"""

import json
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import URL, create_engine, text


def main() -> None:
    root = Path.cwd()
    secrets = dotenv_values(root.parent / ".secrets/barq-g1.env")
    env = {**os.environ, **{k: v for k, v in dotenv_values(root / ".env").items() if v is not None}}
    for key in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        env["SERVICENOW_" + key] = secrets["PDI_SERVICENOW_" + key]
    env["SERVICENOW_INSTANCE_URL"] = secrets["PDI_INSTANCE_URL"]
    env.update(
        POSTGRES_DB="barq_pr191_live",
        AGENT_GRAPH_BACKEND="langgraph",
        AGENT_CHECKPOINTER_BACKEND="postgres",
        AGENT_CONFIDENCE_FLOOR="0.99",
        AGENT_QUERY_REWRITE_ENABLED="true",
        RETRIEVAL_MODE="hybrid_reranked",
        RETRIEVAL_MMR_ENABLED="true",
    )
    url = URL.create(
        "postgresql+psycopg",
        username=env.get("POSTGRES_USER", "postgres"),
        password=env["POSTGRES_PASSWORD"],
        host=env.get("POSTGRES_HOST", "localhost"),
        port=int(env.get("POSTGRES_PORT", 5432)),
        database="postgres",
    )
    env["BARQ_DATABASE_URL"] = url.set(
        drivername="postgresql+asyncpg", database=env["POSTGRES_DB"]
    ).render_as_string(hide_password=False)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        if not conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname='barq_pr191_live'")
        ).scalar():
            conn.execute(text("CREATE DATABASE barq_pr191_live"))
    engine.dispose()
    log = open("/tmp/barq191_live_stack.log", "w")
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"], env=env, stdout=log, stderr=log, check=True
    )
    processes = []
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "instance": env["SERVICENOW_INSTANCE_URL"],
        "stack": "real local FastAPI/Celery/PostgreSQL/Redis/Qdrant/Gemini/ServiceNow",
        "database": env["POSTGRES_DB"],
        "cases": [],
    }
    base = "http://127.0.0.1:8101"
    P = "x_2215032_ai_inc_0"
    try:
        processes.append(
            subprocess.Popen(
                ["uv", "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8101"],
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    "uv",
                    "run",
                    "celery",
                    "-A",
                    "app.workers.celery_app",
                    "worker",
                    "--pool=threads",
                    "--concurrency=1",
                    "--loglevel=INFO",
                ],
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        )
        for _ in range(60):
            try:
                if httpx.get(base + "/ready", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Live API not ready; inspect local stack log")

        def mint(client: str, secret: str) -> dict[str, str]:
            r = httpx.post(
                base + "/api/v1/oauth/token",
                auth=(client, secret),
                data={"grant_type": "client_credentials"},
                timeout=15,
            )
            r.raise_for_status()
            return {"Authorization": "Bearer " + r.json()["access_token"]}

        webhook = mint(env["WEBHOOK_OAUTH_CLIENT_ID"], env["WEBHOOK_OAUTH_CLIENT_SECRET"])
        operator = mint(env.get("OPERATOR_CLIENT_ID", "barq-operator"), env["WEBHOOK_AUTH_TOKEN"])
        # Admin is used ONLY to create isolated test fixtures. Worker/API authenticate
        # exclusively as the PDI integration service account configured above.
        admin = httpx.Client(
            base_url=env["SERVICENOW_INSTANCE_URL"],
            auth=(secrets["SN_ADMIN_USER"], secrets["SN_ADMIN_PASS"]),
            timeout=40,
        )
        for scenario, priority in [("approve_checkpointed_draft", 3), ("reject_pre_retrieval", 1)]:
            setup = admin.post(
                "/api/now/table/incident",
                json={
                    "short_description": "VPN authentication fails after password reset",
                    "description": (
                        "Internet works but the VPN client reports invalid credentials "
                        "after changing the password. Error 691."
                    ),
                    "category": "network",
                    "priority": str(priority),
                    "impact": "1" if priority == 1 else "3",
                    "urgency": "1" if priority == 1 else "3",
                    "active": True,
                    f"{P}_ai_enabled": True,
                    f"{P}_ai_human_lock": False,
                    f"{P}_ai_processing_state": "pending",
                },
            )
            setup.raise_for_status()
            record = setup.json()["result"]
            event = {
                "event_id": str(uuid.uuid4()),
                "sys_id": record["sys_id"],
                "number": record["number"],
                "event_type": "incident.created",
            }
            correlation = "pr191-" + event["event_id"]
            sent = httpx.post(
                base + "/api/v1/webhook/incident",
                headers={**webhook, "X-Correlation-ID": correlation},
                json=event,
                timeout=20,
            )
            sent.raise_for_status()
            lookup = create_engine(url.set(database=env["POSTGRES_DB"]))
            with lookup.connect() as conn:
                execution_id = str(
                    conn.execute(
                        text(
                            "SELECT e.execution_id FROM executions e JOIN events v "
                            "ON e.event_record_id=v.id WHERE v.event_id=:event"
                        ),
                        {"event": event["event_id"]},
                    ).scalar_one()
                )
            lookup.dispose()
            for _ in range(120):
                pending = httpx.get(
                    base + "/api/v1/approvals/pending/" + execution_id, headers=operator, timeout=10
                )
                if pending.status_code == 200:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Execution did not pause: " + scenario + " " + execution_id)
            case = {
                "scenario": scenario,
                "incident": record["number"],
                "sys_id": record["sys_id"],
                "execution_id": execution_id,
                "correlation_id": correlation,
                "pending_http": pending.status_code,
                "paused_outcome": pending.json()["facts"]["outcome"],
                "checkpointed_step_count": len(
                    (pending.json()["facts"].get("draft") or {}).get("steps", [])
                ),
            }
            decision = {
                "decision": "approved" if priority == 3 else "rejected",
                "reason": "PR191 controlled integration verification.",
            }
            if priority == 1:
                invalid = httpx.post(
                    base + "/api/v1/approvals/" + execution_id + "/decide",
                    headers=operator,
                    json={"decision": "approved"},
                    timeout=30,
                )
                case["empty_approval_http"] = invalid.status_code
                assert invalid.status_code == 409
            decided = httpx.post(
                base + "/api/v1/approvals/" + execution_id + "/decide",
                headers=operator,
                json=decision,
                timeout=150,
            )
            case["decision_http"] = decided.status_code
            if decided.status_code != 200:
                raise RuntimeError("Decision failed " + str(decided.status_code))
            after = admin.get(
                "/api/now/table/incident/" + record["sys_id"],
                params={
                    "sysparm_fields": ",".join(
                        f"{P}_{f}"
                        for f in [
                            "ai_processing_state",
                            "ai_resolution",
                            "ai_suggestion",
                            "ai_human_review_required",
                            "ai_processing_end",
                        ]
                    )
                },
            )
            after.raise_for_status()
            fields = after.json()["result"]
            case.update(
                processing_state=fields[f"{P}_ai_processing_state"],
                resolution_chars=len(fields.get(f"{P}_ai_resolution", "")),
                suggestion_chars=len(fields.get(f"{P}_ai_suggestion", "")),
                human_review_required=fields[f"{P}_ai_human_review_required"],
                processing_end=fields[f"{P}_ai_processing_end"],
            )
            assert case["processing_state"] == ("complete" if priority == 3 else "failed")
            if priority == 3:
                assert case["resolution_chars"] > 0
            else:
                assert case["resolution_chars"] == case["suggestion_chars"] == 0
            duplicate = httpx.post(
                base + "/api/v1/approvals/" + execution_id + "/decide",
                headers=operator,
                json=decision,
                timeout=20,
            )
            case["duplicate_http"] = duplicate.status_code
            assert duplicate.status_code == 409
            check_engine = create_engine(url.set(database=env["POSTGRES_DB"]))
            with check_engine.connect() as conn:
                dbrow = (
                    conn.execute(
                        text(
                            "SELECT status,termination_cause FROM executions WHERE execution_id=:id"
                        ),
                        {"id": execution_id},
                    )
                    .mappings()
                    .one()
                )
                retrieval = (
                    conn.execute(
                        text(
                            "SELECT decision FROM workflow_state WHERE execution_id=:id "
                            "AND node_name='retrieve' ORDER BY sequence_number DESC LIMIT 1"
                        ),
                        {"id": execution_id},
                    ).scalar()
                    or {}
                )
                case["retrieval_mmr_applied"] = retrieval.get("mmr_applied", False)
                case["retrieval_mmr_lambda"] = retrieval.get("mmr_lambda")
                case["database_status"] = dbrow["status"]
                case["termination_cause"] = dbrow["termination_cause"]
                case["approval_rows"] = conn.execute(
                    text("SELECT count(*) FROM approvals WHERE execution_id=:id"),
                    {"id": execution_id},
                ).scalar()
            check_engine.dispose()
            assert case["database_status"] == ("succeeded" if priority == 3 else "failed")
            assert case["approval_rows"] == 1
            report["cases"].append(case)
            print(scenario + ": PASS " + record["number"], flush=True)
        admin.close()
        report["result"] = "passed"
    finally:
        for proc in processes:
            try:
                os.killpg(proc.pid, 15)
            except ProcessLookupError:
                pass
        for proc in processes:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, 9)
        log.close()
        (root / "docs/evidence/pr191_live_approval_regressions.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
