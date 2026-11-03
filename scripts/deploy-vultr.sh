#!/usr/bin/env bash
# Deploy the Agent Rush control layer to a Vultr Cloud Compute instance.
#
# Status: written, syntax-checked, NOT yet executed against a live account
# (the account balance is negative, so instance creation is refused).
#
# Usage:
#   VULTR_API_KEY=... ./scripts/deploy-vultr.sh
#
# Requires: curl, jq, ssh, git.
# Creates one instance, installs Docker via cloud-init, builds the image from
# this repository and starts the service on port 8000.

set -euo pipefail

API="https://api.vultr.com/v2"
REGION="ewr"
PLAN="vc2-1c-0.5gb-v6"
OS_ID="2284"                 # Ubuntu 24.04 LTS x64
LABEL="halim-rush-demo"
SSH_PUB="/home/kllosha/.ssh/avokatim_hack_deploy.pub"
REPO="https://github.com/arifiarber-sYn/vultr-agent-rush.git"

for b in curl jq ssh git; do
  command -v "$b" >/dev/null 2>&1 || { echo "missing dependency: $b" >&2; exit 1; }
done
[ -n "${VULTR_API_KEY:-}" ] || { echo "VULTR_API_KEY is not set" >&2; exit 1; }
[ -f "$SSH_PUB" ] || { echo "ssh public key not found: $SSH_PUB" >&2; exit 1; }

auth() { printf 'Authorization: Bearer %s' "$VULTR_API_KEY"; }
api()  { curl -sS -m 30 -H "$(auth)" -H "Content-Type: application/json" "$@"; }

echo "== account"
api "$API/account" | jq -r '.account | "balance=\(.balance) pending=\(.pending_charges) email=\(.email)"'

BAL=$(api "$API/account" | jq -r '.account.balance')
case "$BAL" in
  -*) echo "balance is negative ($BAL); Vultr will refuse to create an instance" >&2
      echo "top up first, then re-run" >&2
      exit 2 ;;
esac

echo "== ssh key"
KEY_BODY=$(cat "$SSH_PUB")
KEY_ID=$(api "$API/ssh-keys" | jq -r --arg n "halim-rush" '.ssh_keys[] | select(.name==$n) | .id' | head -1)
if [ -z "$KEY_ID" ]; then
  KEY_ID=$(api -X POST "$API/ssh-keys" -d "$(jq -n --arg n halim-rush --arg k "$KEY_BODY" '{name:$n,ssh_key:$k}')" | jq -r '.ssh_key.id')
fi
echo "   key id: $KEY_ID"

echo "== cloud-init"
CLOUD_INIT=$(cat <<'CI'
#cloud-config
package_update: true
packages: [git, docker.io, curl]
runcmd:
  - systemctl enable --now docker
  - git clone https://github.com/arifiarber-sYn/vultr-agent-rush.git /opt/rush
  - cd /opt/rush && docker build -t agent-rush:latest .
  - cp -n /opt/rush/.env.example /opt/rush/.env || true
  - >-
    docker run -d --name rush --restart unless-stopped -p 8000:8000
    --security-opt label=disable
    --group-add "$(getent group docker | cut -d: -f3)"
    -v /var/run/docker.sock:/var/run/docker.sock
    --env-file /opt/rush/.env agent-rush:latest
  - sleep 5 && curl -fsS http://127.0.0.1:8000/health || true
CI
)

echo "== create instance"
BODY=$(jq -n --arg r "$REGION" --arg p "$PLAN" --arg os "$OS_ID" --arg l "$LABEL" \
            --arg k "$KEY_ID" --arg ci "$CLOUD_INIT" \
  '{region:$r, plan:$p, os_id:$os, label:$l, sshkey_id:[$k], user_data:($ci|@base64), backups:"disabled", enable_ipv6:false}')

CREATED=$(api -X POST "$API/instances" -d "$BODY")
ID=$(printf '%s' "$CREATED" | jq -r '.instance.id // empty')
[ -n "$ID" ] || { printf '%s\n' "$CREATED" | jq . >&2; exit 3; }
echo "   instance id: $ID"

echo "== wait for ip"
IP=""
for i in $(seq 1 60); do
  IP=$(api "$API/instances/$ID" | jq -r '.instance.main_ip // "0.0.0.0"')
  [ "$IP" != "0.0.0.0" ] && [ -n "$IP" ] && break
  sleep 5
done
echo "   ip: $IP"

echo "== wait for health"
for i in $(seq 1 40); do
  if curl -fsS -m 5 "http://$IP:8000/health" >/dev/null 2>&1; then
    echo "UP: http://$IP:8000/health"
    exit 0
  fi
  sleep 10
done
echo "instance $ID is up at $IP but /health did not answer yet" >&2
echo "inspect with: ssh root@$IP journalctl -u docker --no-pager | tail -30" >&2
exit 4
