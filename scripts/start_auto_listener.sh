#!/usr/bin/env bash
# BARQ live ServiceNow -> FastAPI -> Celery listener.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_FILE="$REPO_DIR/../.secrets/barq-g1.env"
NGROK_LOG="/tmp/barq_ngrok.log"
UVICORN_LOG="/tmp/barq_uvicorn.log"
CELERY_LOG="/tmp/barq_celery.log"

PROPERTY_ID="4163a03d83570b10b5309e80ceaad344"
REST_MESSAGE_ID="04b4db0d83970710b5309e80ceaad35a"
OAUTH_ENTITY_ID="051632a7475b8b50c148497f316d4353"

export PATH="/opt/homebrew/bin:$PATH"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

for command in curl ngrok uv; do
    command -v "$command" >/dev/null 2>&1 || fail "$command is required"
done
[ -f "$SECRETS_FILE" ] || fail "secrets file not found: $SECRETS_FILE"

# Export directly to child processes. Do not copy the credential store into the
# repository or pass secret values on command lines.
set -a
# shellcheck disable=SC1090
source "$SECRETS_FILE"
set +a

for name in \
    SERVICENOW_INSTANCE_URL SN407364_ADMIN_USER SN407364_ADMIN_PASS \
    WEBHOOK_AUTH_TOKEN WEBHOOK_OAUTH_CLIENT_ID WEBHOOK_OAUTH_CLIENT_SECRET \
    WEBHOOK_OAUTH_SIGNING_KEY; do
    [ -n "${!name:-}" ] || fail "$name is required"
done

SN_URL="${SERVICENOW_INSTANCE_URL%/}"
SN_AUTH="${SN407364_ADMIN_USER}:${SN407364_ADMIN_PASS}"

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port 8000 is already in use"
fi
if pgrep -f "$REPO_DIR/.venv/bin/celery -A app.workers.celery_app" >/dev/null 2>&1; then
    fail "a BARQ Celery worker is already running; stop it before starting this listener"
fi

api_value() {
    local table="$1" record="$2" field="$3"
    curl -fsS -u "$SN_AUTH" \
        "$SN_URL/api/now/table/$table/$record?sysparm_fields=$field" |
        python3 -c 'import json,sys; data=json.load(sys.stdin)["result"]; print(data[sys.argv[1]])' \
            "$field"
}

json_value() {
    python3 -c 'import json,sys; print(json.dumps({sys.argv[1]: sys.argv[2]}))' "$1" "$2"
}

patch_value() {
    local table="$1" record="$2" field="$3" value="$4"
    curl -fsS -X PATCH -u "$SN_AUTH" \
        -H 'Content-Type: application/json' \
        "$SN_URL/api/now/table/$table/$record" \
        -d "$(json_value "$field" "$value")" >/dev/null
}

ORIGINAL_PROPERTY="$(api_value sys_properties "$PROPERTY_ID" value)"
ORIGINAL_REST_ENDPOINT="$(api_value sys_rest_message "$REST_MESSAGE_ID" rest_endpoint)"
ORIGINAL_TOKEN_URL="$(api_value oauth_entity "$OAUTH_ENTITY_ID" token_url)"

UVICORN_PID=""
CELERY_PID=""
NGROK_PID=""
CONFIG_CHANGED=0

cleanup() {
    local exit_status=$?
    trap - EXIT INT TERM
    [ -z "$UVICORN_PID" ] || kill "$UVICORN_PID" 2>/dev/null || true
    [ -z "$CELERY_PID" ] || kill "$CELERY_PID" 2>/dev/null || true
    [ -z "$NGROK_PID" ] || kill "$NGROK_PID" 2>/dev/null || true

    if [ "$CONFIG_CHANGED" -eq 1 ]; then
        printf 'Restoring the three ServiceNow endpoint values...\n'
        patch_value sys_properties "$PROPERTY_ID" value "$ORIGINAL_PROPERTY" || true
        patch_value sys_rest_message "$REST_MESSAGE_ID" rest_endpoint \
            "$ORIGINAL_REST_ENDPOINT" || true
        patch_value oauth_entity "$OAUTH_ENTITY_ID" token_url "$ORIGINAL_TOKEN_URL" || true
    fi
    exit "$exit_status"
}
trap cleanup EXIT INT TERM

cd "$REPO_DIR"
: >"$UVICORN_LOG"
: >"$CELERY_LOG"
: >"$NGROK_LOG"

uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 >"$UVICORN_LOG" 2>&1 &
UVICORN_PID=$!

for _ in {1..30}; do
    if curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1; then
        break
    fi
    kill -0 "$UVICORN_PID" 2>/dev/null || fail "FastAPI exited; see $UVICORN_LOG"
    sleep 1
done
curl -fsS http://127.0.0.1:8000/ready >/dev/null || fail "FastAPI never became ready"

uv run celery -A app.workers.celery_app worker \
    --pool=solo --concurrency=1 --queues=barq:incident:events --loglevel=INFO \
    >"$CELERY_LOG" 2>&1 &
CELERY_PID=$!

for _ in {1..30}; do
    if grep -q 'ready\.' "$CELERY_LOG"; then
        break
    fi
    kill -0 "$CELERY_PID" 2>/dev/null || fail "Celery exited; see $CELERY_LOG"
    sleep 1
done
grep -q 'ready\.' "$CELERY_LOG" || fail "Celery never became ready"

ngrok http 8000 --log=stdout --log-format=logfmt >"$NGROK_LOG" 2>&1 &
NGROK_PID=$!

TUNNEL_URL=""
for _ in {1..30}; do
    TUNNEL_URL="$(curl -fsS http://127.0.0.1:4040/api/tunnels 2>/dev/null |
        python3 -c 'import json,sys; data=json.load(sys.stdin); print(next((t["public_url"] for t in data["tunnels"] if t["proto"]=="https"), ""))' \
        2>/dev/null || true)"
    [ -z "$TUNNEL_URL" ] || break
    kill -0 "$NGROK_PID" 2>/dev/null || fail "ngrok exited; see $NGROK_LOG"
    sleep 1
done
[ -n "$TUNNEL_URL" ] || fail "ngrok did not publish an HTTPS tunnel"

patch_value sys_properties "$PROPERTY_ID" value \
    "$TUNNEL_URL/api/v1/webhook/incident"
CONFIG_CHANGED=1
patch_value sys_rest_message "$REST_MESSAGE_ID" rest_endpoint \
    "$TUNNEL_URL/api/v1/webhook/incident"
patch_value oauth_entity "$OAUTH_ENTITY_ID" token_url \
    "$TUNNEL_URL/api/v1/oauth/token"

printf 'Automatic pipeline is ready at %s\n' "$TUNNEL_URL"
printf 'ServiceNow endpoint changes will be restored on shutdown. Press Ctrl+C to stop.\n'
tail -F "$UVICORN_LOG" "$CELERY_LOG"
