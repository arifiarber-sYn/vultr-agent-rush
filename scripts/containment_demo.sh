#!/usr/bin/env bash
# Containment demo: four payloads, four different ways to be contained.
# Usage: bash scripts/containment_demo.sh [BASE_URL]
set -u

BASE="${1:-http://127.0.0.1:8000}"

post_task() {
  local label="$1" code="$2"
  printf '\n=== %s ===\n' "$label"
  curl -sS -X POST "$BASE/tasks" \
    -H 'Content-Type: application/json' \
    --data-binary "$(python3 -c 'import json,sys; print(json.dumps({"code": sys.argv[1]}))' "$code")" \
    | python3 -m json.tool
}

post_task "1. normal execution" "print(6 * 7)"
post_task "2. network attempt (must fail: no interface)" "import socket; socket.create_connection(('1.1.1.1', 80), timeout=2)"
post_task "3. host write attempt (must fail: read-only root)" "open('/etc/passwd', 'a').write('x')"
post_task "4. scratch write (allowed, dies with the container)" "open('/work/notes.txt', 'w').write('ok'); print('wrote to tmpfs')"

printf '\n=== 5. timeout demo (20 s wall clock) ===\n'
curl -sS -X POST "$BASE/tasks" -H 'Content-Type: application/json' \
  --data-binary '{"code": "while True: pass"}' | python3 -m json.tool

printf '\nContainment record is returned with every job; compare the stdout/stderr with the flags.\n'
