#!/usr/bin/env bash
# Start, stop or link the finance tracker.
#
#   ./run.sh          build and start the container (http://127.0.0.1:8787)
#   ./run.sh link     open the Plaid Link page at http://localhost:8765 (link a bank, change accounts)
#   ./run.sh sync     sync with Plaid now
#   ./run.sh down     stop the container (data in ./data is kept)
#   ./run.sh logs     follow the container's logs
#   ./run.sh password set the phone unlock password (also the way back in; turns Face ID off)
#
# Plaid keys are read from ~/.claude-secrets/env/ into exported shell variables and passed through
# to compose by name, so they appear on no command line and in no file this project writes.
set -euo pipefail
cd "$(dirname "$0")"

load_keys() {
  local dir="$HOME/.claude-secrets/env" name
  for name in PLAID_CLIENT_ID PLAID_SECRET PLAID_SANDBOX_SECRET; do
    if [[ ! -r "$dir/$name" ]]; then
      echo "Missing $dir/$name" >&2
      exit 1
    fi
    export "$name=$(<"$dir/$name")"
  done
}

up() {
  load_keys
  mkdir -p data
  HOST_UID="$(id -u)" HOST_GID="$(id -g)" docker compose up -d --build
  echo "Waiting for http://127.0.0.1:8787/health ..."
  for _ in $(seq 30); do
    if curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1; then
      echo "Up: http://127.0.0.1:8787"
      return
    fi
    sleep 1
  done
  echo "Container started but /health did not answer; see ./run.sh logs" >&2
  exit 1
}

running() { [[ -n "$(docker compose ps -q --status running app 2>/dev/null)" ]]; }

case "${1:-up}" in
  up) up ;;
  link)
    running || up
    echo "Open http://localhost:8765 in a browser on this computer. Ctrl+C when done."
    docker compose exec app python -m finance.link
    ;;
  sync)
    running || up
    curl -fsS -X POST -H 'Content-Type: application/json' -d '{"force":true}' http://127.0.0.1:8787/api/sync
    echo
    ;;
  down) docker compose down ;;
  logs) docker compose logs -f ;;
  password)
    running || up
    docker compose exec -it app python -m finance.lock /app/data/lock.json
    ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
