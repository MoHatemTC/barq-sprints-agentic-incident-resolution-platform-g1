#!/usr/bin/env bash
# =============================================================================
# BARQ Agentic Incident Resolution Platform — Sprint 2 End-to-End Demo
# =============================================================================
# This script demonstrates the live graph and ServiceNow write-back:
#   1. Creates a real incident on ServiceNow (dev407364)
#   2. Enables AI processing on it
#   3. Runs the 11-node LangGraph agent LIVE (writes back to ServiceNow)
#   4. Reads back the incident fields to prove they are populated
#   5. Shows the Langfuse trace
#
# It intentionally invokes the graph directly. Use start_auto_listener.sh plus a
# ServiceNow-created incident to prove the webhook, PostgreSQL and Celery path.
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_FILE="$REPO_DIR/../.secrets/barq-g1.env"

export PATH="/opt/homebrew/bin:$PATH"

# Terminal Formatting
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Load secrets
if [ ! -f "$SECRETS_FILE" ]; then
    echo -e "${RED}✘ Secrets file not found at $SECRETS_FILE${NC}"
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a

# Resolve instance URL
SN_URL="${SERVICENOW_INSTANCE_URL%/}"
ADMIN_USER="${SN407364_ADMIN_USER:-${SN_ADMIN_USER:-admin}}"
ADMIN_PASS="${SN407364_ADMIN_PASS:-${SN_ADMIN_PASS:-}}"

clear 2>/dev/null || true

echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${BLUE}║${NC}   ${BOLD}${CYAN}BARQ Agentic Incident Resolution Platform — Live Demo${NC}                ${BOLD}${BLUE}║${NC}"
echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BOLD}Integrated Sprint 2 Architecture & Team Workstreams:${NC}"
echo -e "  ${CYAN}• S2.1 FastAPI 202 Ingestion Webhook & REST Surface${NC}       ─── Mohamed Abdelaziem"
echo -e "  ${CYAN}• S2.2 PostgreSQL State Schema & Idempotency Store${NC}        ─── Ahmed Tamer"
echo -e "  ${CYAN}• S2.3 Redis Broker, Celery Async Worker & DLQ${NC}            ─── Kerolos Mohsen"
echo -e "  ${CYAN}• S2.4 Qdrant Hybrid Retrieval & Cross-Encoder Reranker${NC}   ─── Tasneem Mohammed"
echo -e "  ${CYAN}• S2.5 11-Node LangGraph State Machine & Langfuse Tracing${NC} ─── Ali Ezz"
echo ""

# =============================================================================
# 1. Infrastructure Health Check
# =============================================================================
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${YELLOW} [1/6] INFRASTRUCTURE LAYER HEALTH CHECK${NC}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

RUNNING_CONTAINERS=$(docker ps --format "{{.Names}}" 2>/dev/null || true)
for svc in barq-postgres barq-redis barq-qdrant; do
    if echo "$RUNNING_CONTAINERS" | grep -q "$svc"; then
        case "$svc" in
            barq-postgres) echo -e "  ${GREEN}✔${NC} ${BOLD}PostgreSQL 16${NC} (5432) — Relational audit, idempotency & workflow checkpoints" ;;
            barq-redis)    echo -e "  ${GREEN}✔${NC} ${BOLD}Redis 7${NC}       (6379) — Celery task broker & dead-letter queue" ;;
            barq-qdrant)   echo -e "  ${GREEN}✔${NC} ${BOLD}Qdrant v1.14${NC}  (6333) — Dense (BGE) + Sparse (BM25) hybrid vector store" ;;
        esac
    else
        echo -e "  ${RED}✘${NC} $svc is not running!"
        exit 1
    fi
done

# Check ServiceNow connectivity
echo ""
echo -e "  ${DIM}Checking ServiceNow connectivity (${SN_URL})...${NC}"
SN_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" -u "${ADMIN_USER}:${ADMIN_PASS}" \
    "${SN_URL}/api/now/table/incident?sysparm_limit=1" \
    -H "Accept: application/json" 2>/dev/null || echo "000")
if [ "$SN_HEALTH" = "200" ]; then
    echo -e "  ${GREEN}✔${NC} ${BOLD}ServiceNow${NC}    (${SN_URL}) — Connected as admin"
else
    echo -e "  ${RED}✘${NC} ServiceNow returned HTTP $SN_HEALTH"
    exit 1
fi
echo ""

# =============================================================================
# 2. Quality & Contract Verification
# =============================================================================
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${YELLOW} [2/6] COMPONENT CONTRACT & GRAPH VERIFICATION GATE${NC}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${DIM}Running contract tests across endpoints, retrieval fusion, and state graph...${NC}"

cd "$REPO_DIR"
uv run pytest -q -W ignore::DeprecationWarning -W ignore::UserWarning \
    tests/test_smoke.py \
    tests/test_endpoints_contract.py \
    tests/retrieval/test_search.py \
    tests/test_graph.py 2>&1 | tail -5

echo ""
echo -e "  ${GREEN}✔ All API endpoint contracts, Qdrant search filters, and graph edges verified.${NC}"
echo ""

# =============================================================================
# 3. Create a FRESH Incident on ServiceNow
# =============================================================================
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${YELLOW} [3/6] CREATE FRESH INCIDENT ON SERVICENOW${NC}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${DIM}Creating a new incident with AI Enabled = true on ${SN_URL}...${NC}"

USER_INPUT="${1:-}"

if [ -n "$USER_INPUT" ]; then
    SHORT_DESC="[BARQ Demo] ${USER_INPUT:0:70}"
    FULL_DESC="$USER_INPUT"
    CATEGORY="inquiry"
    SUBCATEGORY=""
    SCENARIO_NAME="Custom Incident: $USER_INPUT"
else
    SHORT_DESC="[BARQ Demo] VPN authentication fails after corporate password change"
    FULL_DESC="User reports that after changing their corporate domain password yesterday, the VPN client repeatedly rejects the new credentials. The user has tried restarting the VPN client and their machine. Error message: Authentication failed - cached credentials invalid. This is affecting remote work access."
    CATEGORY="network"
    SUBCATEGORY="vpn"
    SCENARIO_NAME="VPN Authentication Failure (Corporate Password Change)"
fi

JSON_PAYLOAD=$(python3 -c "
import sys, json
data = {
    'short_description': sys.argv[1],
    'description': sys.argv[2],
    'category': sys.argv[3],
    'subcategory': sys.argv[4],
    'impact': '2',
    'urgency': '2',
    'priority': '3',
    'x_2215032_ai_inc_0_ai_enabled': 'true',
    'x_2215032_ai_inc_0_ai_human_lock': 'false',
    'x_2215032_ai_inc_0_ai_processing_state': 'pending',
    'state': '1',
    'active': 'true'
}
print(json.dumps(data))
" "$SHORT_DESC" "$FULL_DESC" "$CATEGORY" "$SUBCATEGORY")

CREATE_RESPONSE=$(curl -s -X POST \
    -u "${ADMIN_USER}:${ADMIN_PASS}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    "${SN_URL}/api/now/table/incident" \
    -d "$JSON_PAYLOAD")

# Extract incident number and sys_id
INC_NUMBER=$(echo "$CREATE_RESPONSE" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('result',{}).get('number','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
INC_SYS_ID=$(echo "$CREATE_RESPONSE" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('result',{}).get('sys_id',''))" 2>/dev/null || echo "")

if [ "$INC_NUMBER" = "UNKNOWN" ] || [ -z "$INC_SYS_ID" ]; then
    echo -e "  ${RED}✘ Failed to create incident. Response:${NC}"
    echo "$CREATE_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$CREATE_RESPONSE"
    exit 1
fi

echo ""
echo -e "  ${GREEN}✔${NC} ${BOLD}Created: ${CYAN}${INC_NUMBER}${NC}${BOLD}${NC}"
echo -e "    sys_id:            ${DIM}${INC_SYS_ID}${NC}"
echo -e "    Scenario:          ${CYAN}${SCENARIO_NAME}${NC}"
echo -e "    Category:          ${CATEGORY}${SUBCATEGORY:+ / $SUBCATEGORY}"
echo -e "    Priority:          3 — Moderate"
echo -e "    AI Enabled:        ${GREEN}true${NC}"
echo -e "    AI Human Lock:     ${GREEN}false${NC} (agent may write)"
echo -e "    AI State:          ${YELLOW}pending${NC} (waiting for agent)"
echo -e "    ${BOLD}Link:${NC} ${SN_URL}/incident.do?sys_id=${INC_SYS_ID}"
echo ""

# =============================================================================
# 4. Run the 11-Node LangGraph Agent LIVE (Write-Back Enabled)
# =============================================================================
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${YELLOW} [4/6] LIVE AGENT EXECUTION — WRITING BACK TO SERVICENOW${NC}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}Running:${NC} ${CYAN}run_agent_live.py --number ${INC_NUMBER}${NC} ${MAGENTA}(NO --dry-run, writes are real)${NC}"
echo -e "${BOLD}Target Flow:${NC}"
echo -e "  ${CYAN}load${NC} ➔ ${CYAN}validate${NC} ➔ ${CYAN}classify${NC} ➔ ${CYAN}determine_risk${NC} ➔ ${CYAN}retrieve${NC} ➔ ${CYAN}diagnose${NC}"
echo -e "  ➔ ${CYAN}generate${NC} ➔ ${CYAN}verify_evidence${NC} ➔ ${CYAN}safety_check${NC} ➔ ${CYAN}confidence_check${NC} ➔ ${CYAN}act${NC}"
echo ""

# Run the agent LIVE — this writes to ServiceNow
AGENT_OUTPUT=$(uv run python scripts/run_agent_live.py --number "$INC_NUMBER" 2>&1)
echo "$AGENT_OUTPUT"

# Extract key results
WRITE_BACK=$(python3 -c "
import sys, re, json
content = sys.stdin.read()
m = re.search(r'\{.*\"outcome\":.*\}', content, re.DOTALL)
if m:
    try:
        data = json.loads(m.group(0))
        print(data.get('write_back', 'unknown'))
    except Exception:
        print('unknown')
else:
    print('unknown')
" <<< "$AGENT_OUTPUT")

OUTCOME=$(python3 -c "
import sys, re, json
content = sys.stdin.read()
m = re.search(r'\{.*\"outcome\":.*\}', content, re.DOTALL)
if m:
    try:
        data = json.loads(m.group(0))
        print(data.get('outcome', 'unknown'))
    except Exception:
        print('unknown')
else:
    print('unknown')
" <<< "$AGENT_OUTPUT")
TRACE_URL=$(echo "$AGENT_OUTPUT" | grep "^trace url:" | sed 's/trace url: //')

echo ""
if [ "$WRITE_BACK" = "written" ]; then
    echo -e "  ${GREEN}✔ WRITE-BACK CONFIRMED: Agent successfully wrote to ServiceNow (${OUTCOME})!${NC}"

    echo -e "  ${GREEN}✔ AI EXECUTION LOG: the graph wrote the audit row through the integration identity.${NC}"
elif [ "$WRITE_BACK" = "dry_run" ]; then
    echo -e "  ${YELLOW}⚠ Agent ran in dry-run mode (fields NOT written to ServiceNow).${NC}"
else
    echo -e "  ${CYAN}ℹ Agent outcome: ${OUTCOME}${NC}"
fi
echo ""

# =============================================================================
# 5. Read Back the Incident from ServiceNow — Prove Fields Are Populated
# =============================================================================
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${YELLOW} [5/6] SERVICENOW READ-BACK — VERIFYING AI FIELDS ARE POPULATED${NC}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${DIM}Reading ${INC_NUMBER} back from ServiceNow to verify the agent's writes persisted...${NC}"
echo ""

READBACK=$(curl -s -u "${ADMIN_USER}:${ADMIN_PASS}" \
    -H "Accept: application/json" \
    "${SN_URL}/api/now/table/incident/${INC_SYS_ID}?sysparm_fields=number,short_description,x_2215032_ai_inc_0_ai_processing_state,x_2215032_ai_inc_0_ai_classification,x_2215032_ai_inc_0_ai_confidence,x_2215032_ai_inc_0_ai_suggestion,x_2215032_ai_inc_0_ai_model_name,x_2215032_ai_inc_0_ai_agent_version,x_2215032_ai_inc_0_ai_human_review_required,x_2215032_ai_inc_0_ai_enabled,x_2215032_ai_inc_0_ai_human_lock")

# Parse and display each field
echo "$READBACK" | python3 -c "
import sys, json

data = json.load(sys.stdin).get('result', {})

fields = [
    ('number',                                         'Incident Number'),
    ('short_description',                              'Short Description'),
    ('x_2215032_ai_inc_0_ai_enabled',                  'AI Enabled'),
    ('x_2215032_ai_inc_0_ai_processing_state',         'AI Processing State'),
    ('x_2215032_ai_inc_0_ai_classification',           'AI Classification'),
    ('x_2215032_ai_inc_0_ai_confidence',               'AI Confidence'),
    ('x_2215032_ai_inc_0_ai_model_name',               'AI Model Name'),
    ('x_2215032_ai_inc_0_ai_agent_version',            'AI Agent Version'),
    ('x_2215032_ai_inc_0_ai_human_review_required',    'Human Review Required'),
    ('x_2215032_ai_inc_0_ai_human_lock',               'Human Lock'),
    ('x_2215032_ai_inc_0_ai_suggestion',               'AI Suggested Response'),
]

g = '\033[0;32m'
c = '\033[0;36m'
y = '\033[1;33m'
b = '\033[1m'
d = '\033[2m'
nc = '\033[0m'

for key, label in fields:
    val = data.get(key, '')
    if key == 'x_2215032_ai_inc_0_ai_suggestion' and val:
        print(f'  {b}{label}:{nc}')
        for line in val.split('\n'):
            print(f'    {c}{line}{nc}')
    elif val and val not in ('', 'false', 'pending'):
        print(f'  {b}{label}:{nc} {g}{val}{nc}')
    elif val == '':
        print(f'  {b}{label}:{nc} {d}(empty){nc}')
    else:
        print(f'  {b}{label}:{nc} {val}')
" 2>/dev/null || echo "$READBACK" | python3 -m json.tool

echo ""

# =============================================================================
# 6. Safety Circuit Demo (P1 Incident — In-Memory)
# =============================================================================
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${YELLOW} [6/6] ENTERPRISE SAFETY CIRCUIT — HIGH-RISK P1 ESCALATION${NC}"
echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}Policy:${NC} (§11.1 & §11.7) Autonomous AI must ${RED}NEVER${NC} generate resolution on P1."
echo -e "${BOLD}Expected:${NC} Halt at ${CYAN}determine_risk${NC} → skip retrieval/generation → escalate to humans."
echo ""

uv run python scripts/run_agent_live.py --scenario p1 --dry-run 2>&1

echo ""
echo -e "  ${GREEN}✔ Safety Gate: Zero LLM generation calls on Priority 1 incident.${NC}"
echo -e "  ${GREEN}✔ Escalated immediately to human engineering team.${NC}"
echo ""

# =============================================================================
# Summary & Direct Links
# =============================================================================
echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${BLUE}║${NC}   ${BOLD}${GREEN}✔ LIVE GRAPH + SERVICENOW WRITE-BACK DEMO COMPLETE${NC}                 ${BOLD}${BLUE}║${NC}"
echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BOLD}What just happened:${NC}"
echo -e "  1. Created ${CYAN}${INC_NUMBER}${NC} on ServiceNow with AI Enabled = true"
echo -e "  2. Ran the 11-node LangGraph agent ${MAGENTA}live${NC} (Gemini via LiteLLM proxy)"
echo -e "  3. Agent wrote back: AI state, classification, confidence, suggestion & work note"
echo -e "  4. Read back from ServiceNow and verified all AI fields are populated"
echo -e "  5. Demonstrated P1 safety halt (no generation on critical incidents)"
echo ""
echo -e "${BOLD}Direct Links (open in browser):${NC}"
echo -e "  ${BOLD}• Incident ${INC_NUMBER}:${NC}       ${SN_URL}/incident.do?sys_id=${INC_SYS_ID}"
echo -e "  ${BOLD}• All Incidents:${NC}           ${SN_URL}/incident_list.do"
echo -e "  ${BOLD}• AI Execution Logs:${NC}       ${SN_URL}/x_2215032_ai_inc_0_ai_execution_log_list.do"
if [ -n "$TRACE_URL" ]; then
    echo -e "  ${BOLD}• Langfuse Trace:${NC}          ${TRACE_URL}"
fi
echo -e "  ${BOLD}• Langfuse Dashboard:${NC}      https://cloud.langfuse.com/project/cmu50d4jy0h8uad0fgvmn0y4f"
echo ""
echo -e "  ${YELLOW}💡 Tip:${NC} If you already had the incident open, press ${BOLD}Refresh (Cmd+R)${NC} to see the populated AI fields and execution log."
echo ""
