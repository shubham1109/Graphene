#!/usr/bin/env bash
# One-command launcher for the Graphene Benchmarker.
#
#   ./run.sh            dev mode: FastAPI + Vite dev server, hot reload
#   ./run.sh --prod     build the SPA and serve it from FastAPI on one port
#   ./run.sh --no-seed  skip loading reference data
#
# Honours MONGODB_URI, BACKEND_PORT and FRONTEND_PORT from the environment.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
MODE=dev
SEED=1
MONGO_CONTAINER=graphene-mongo

for arg in "$@"; do
  case "$arg" in
    --prod) MODE=prod ;;
    --no-seed) SEED=0 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- prerequisites
command -v python3 >/dev/null || die "python3 not found. Install Python 3.11 or newer."
python3 - <<'PY' || die "Python 3.11+ required (the app uses 3.10+ syntax and numpy 2.x wheels)."
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
# Both modes need npm: dev runs the Vite server, prod builds the bundle.
command -v npm >/dev/null || die "npm not found. Install Node 20 or newer."

pick_port() {
  python3 - "$1" <<'PY'
import socket, sys
pref = int(sys.argv[1])
for port in range(pref, pref + 40):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port); break
else:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])
PY
}

API_PORT="${BACKEND_PORT:-$(pick_port 8000)}"
WEB_PORT="${FRONTEND_PORT:-$(pick_port 5173)}"

# ------------------------------------------------------------------ backend deps
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating virtualenv at backend/.venv"
  python3 -m venv "$VENV"
fi
PY="$VENV/bin/python"

# Reinstall only when requirements.txt is newer than the last successful install.
STAMP="$VENV/.requirements.sha"
REQ_SHA="$($PY - "$BACKEND/requirements.txt" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())
PY
)"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$REQ_SHA" ]; then
  say "Installing Python dependencies (a few minutes on first run)"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r "$BACKEND/requirements.txt"
  printf '%s' "$REQ_SHA" > "$STAMP"
else
  say "Python dependencies up to date"
fi

# ------------------------------------------------------------------------- .env
if [ ! -f "$BACKEND/.env" ]; then
  say "Writing backend/.env with a freshly generated JWT_SECRET"
  "$PY" - "$BACKEND/.env.example" "$BACKEND/.env" <<'PY'
import secrets, sys
src, dst = sys.argv[1], sys.argv[2]
out = []
for line in open(src):
    if line.startswith("JWT_SECRET="):
        line = f"JWT_SECRET={secrets.token_urlsafe(48)}\n"
    out.append(line)
open(dst, "w").writelines(out)
PY
fi

# ---------------------------------------------------------------------- MongoDB
# Ask the app itself for the effective URI so .env and env vars are respected
# exactly as the running server will see them.
MONGO_URI="$(cd "$BACKEND" && "$PY" -c 'from app.config import get_settings; print(get_settings().mongodb_uri)')"

mongo_up() {
  (cd "$BACKEND" && "$PY" - "$1" <<'PY'
import sys
from pymongo import MongoClient
try:
    MongoClient(sys.argv[1], serverSelectionTimeoutMS=3000).admin.command("ping")
except Exception:
    sys.exit(1)
PY
  )
}

redact() { printf '%s' "$1" | sed -E 's#://[^@/]+@#://***:***@#'; }

if mongo_up "$MONGO_URI"; then
  say "MongoDB reachable at $(redact "$MONGO_URI")"
elif printf '%s' "$MONGO_URI" | grep -qE 'localhost|127\.0\.0\.1' && command -v docker >/dev/null; then
  say "No local MongoDB; starting the '$MONGO_CONTAINER' container"
  if [ -n "$(docker ps -aq -f "name=^${MONGO_CONTAINER}$")" ]; then
    docker start "$MONGO_CONTAINER" >/dev/null
  else
    docker run -d --name "$MONGO_CONTAINER" -p 27017:27017 -v "${MONGO_CONTAINER}-data:/data/db" mongo:7 >/dev/null
  fi
  for _ in $(seq 30); do mongo_up "$MONGO_URI" && break; sleep 1; done
  mongo_up "$MONGO_URI" || die "container started but MongoDB never answered on 27017"
  say "MongoDB container ready"
else
  die "Cannot reach MongoDB at $(redact "$MONGO_URI").
  Start one of these, then re-run:
    - Docker:  docker run -d -p 27017:27017 mongo:7
    - macOS:   brew services start mongodb-community
    - Atlas:   MONGODB_URI='mongodb+srv://user:pass@cluster.mongodb.net/' ./run.sh"
fi

if [ "$SEED" = 1 ]; then
  say "Seeding reference data (idempotent)"
  (cd "$BACKEND" && "$PY" -m seed.seed >/dev/null) || warn "seeding failed; the app will run with empty reference collections"
fi

# ----------------------------------------------------------------- frontend deps
if [ ! -d "$FRONTEND/node_modules" ]; then
  say "Installing frontend dependencies"
  (cd "$FRONTEND" && npm ci --no-audit --no-fund)
fi

# ------------------------------------------------------------------------ launch
# Job control puts each background job in its own process group, so killing the
# negated pid takes down the whole tree. Without this, uvicorn's reloader child
# and npm's node child survive Ctrl-C and keep holding the ports.
set -m

PIDS=""
cleanup() {
  trap - EXIT INT TERM
  for p in $PIDS; do
    kill -TERM -"$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
  done
  # Escalate on anything still alive after a grace period.
  for _ in 1 2 3 4 5 6; do
    alive=""
    for p in $PIDS; do kill -0 -"$p" 2>/dev/null && alive=1; done
    [ -z "$alive" ] && break
    sleep 0.5
  done
  for p in $PIDS; do kill -KILL -"$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for_http() {
  for _ in $(seq 60); do
    curl -sf -o /dev/null "$1" && return 0
    sleep 1
  done
  return 1
}

if [ "$MODE" = prod ]; then
  say "Building the SPA"
  (cd "$FRONTEND" && npm run build)
  say "Starting FastAPI (serving API + SPA) on port $API_PORT"
  (cd "$BACKEND" && STATIC_DIR="$FRONTEND/dist" \
     "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT") &
  PIDS="$!"
  wait_for_http "http://localhost:$API_PORT/api/health" \
    || die "backend never became healthy; scroll up for its traceback"
  say "Ready:  http://localhost:$API_PORT   (API docs at /docs)"
else
  say "Starting FastAPI on port $API_PORT"
  (cd "$BACKEND" && CORS_ORIGINS="http://localhost:$WEB_PORT,http://127.0.0.1:$WEB_PORT" \
     "$PY" -m uvicorn app.main:app --port "$API_PORT" --reload) &
  API_PID="$!"; PIDS="$API_PID"
  wait_for_http "http://localhost:$API_PORT/api/health" \
    || die "backend never became healthy; scroll up for its traceback"

  say "Starting Vite on port $WEB_PORT"
  (cd "$FRONTEND" && VITE_API_TARGET="http://127.0.0.1:$API_PORT" \
     npm run dev -- --port "$WEB_PORT" --strictPort) &
  WEB_PID="$!"; PIDS="$API_PID $WEB_PID"
  wait_for_http "http://localhost:$WEB_PORT/" \
    || die "Vite never served a page; scroll up for its output"

  say "Ready:  http://localhost:$WEB_PORT   (API docs at http://localhost:$API_PORT/docs)"
fi

curl -s "http://localhost:$API_PORT/api/health"; echo
echo "Press Ctrl-C to stop."

# Exit as soon as either process dies, so a crash is not mistaken for running.
while :; do
  for p in $PIDS; do
    kill -0 -"$p" 2>/dev/null || { warn "process group $p exited; shutting down"; exit 1; }
  done
  sleep 2
done
