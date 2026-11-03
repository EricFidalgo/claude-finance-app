#!/usr/bin/env bash
# Start or stop the finance tracker. Banks are added, fixed and removed on the app's Money screen.
#
#   ./run.sh          build and start the container (http://127.0.0.1:8787)
#   ./run.sh sync     sync with Plaid now
#   ./run.sh down     stop the container (your data is kept)
#   ./run.sh logs     follow the container's logs
#   ./run.sh password set the phone unlock password (also the way back in; turns Face ID off)
#
# Your data (database, Plaid tokens, plan) lives in ~/.local/share/finance-tracker, outside this repo, so
# nothing private can be committed or pushed. Set FINANCE_DATA to keep it somewhere else.
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

export FINANCE_DATA="${FINANCE_DATA:-$HOME/.local/share/finance-tracker}"

# Earlier versions kept the data in ./data inside the repo. Move it out once, with the app stopped.
move_old_data() {
  [[ -d data ]] || return 0
  if [[ -z "$(ls -A data)" ]]; then
    rmdir data
    return 0
  fi
  if [[ -e "$FINANCE_DATA" && -n "$(ls -A "$FINANCE_DATA")" ]]; then
    echo "Both ./data and $FINANCE_DATA hold data. Keep one, delete the other, and run this again." >&2
    exit 1
  fi
  echo "Moving your data from ./data to $FINANCE_DATA ..."
  docker compose down
  mkdir -p "$(dirname "$FINANCE_DATA")"
  [[ -d "$FINANCE_DATA" ]] && rmdir "$FINANCE_DATA"
  mv data "$FINANCE_DATA"
}

up() {
  load_keys
  move_old_data
  # Made here, owned by you and closed to other users; left to Docker it would be created as root.
  mkdir -p -m 700 "$FINANCE_DATA"
  chmod 700 "$FINANCE_DATA"
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
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
