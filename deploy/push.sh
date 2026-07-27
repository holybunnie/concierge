#!/usr/bin/env bash
#
# Deploy this working tree to the VPS. Use this instead of a hand-written rsync.
#
# WHY THIS EXISTS. On 2026-07-26 a hand-written deploy used `rsync -a --delete-excluded`, which
# does not mean "skip these paths" — it means "skip them in the source AND delete them on the
# destination". It removed /opt/concierge/.env (every production credential), .venv, .onchainos
# (the OKX wallet session) and a2a/ (the pinned marketplace CLI). The app died, the A2A daemon
# lost its login, and the listing went offline. Everything except the wallet session was
# recoverable from the box itself; the session needed a human to log in through a browser.
#
# So this script is built so that failure cannot recur:
#   * it NEVER passes --delete or --delete-excluded, in any form;
#   * it refuses to run if either flag is somehow present in the assembled command;
#   * it backs up the live secrets and identity state BEFORE touching anything;
#   * it verifies afterwards that the app, the daemon and answerability are all actually up,
#     and says so loudly if not. A deploy that finishes silently is not a deploy that worked.
#
# Usage:  deploy/push.sh [--restart-a2a]
#   --restart-a2a   also restart the transport daemon (default: leave it running; the daemon
#                   holds the XMTP session and does not need a bounce for app-side changes)

set -euo pipefail

HOST="${CONCIERGE_VPS:-root@38.49.216.59}"
KEY="${CONCIERGE_VPS_KEY:-$HOME/.ssh/concierge_deploy}"
REMOTE=/opt/concierge
SSH=(ssh -i "$KEY" -o BatchMode=yes "$HOST")

# Never synced, never deleted. `--exclude` alone leaves the destination copy untouched — which is
# the entire point, and is only true because --delete-excluded is not used below.
EXCLUDES=(
  --exclude '.git'
  --exclude '.env'                 # production credentials, different from the local ones
  --exclude '.venv'
  --exclude '__pycache__'
  --exclude '.onchainos'           # OKX wallet session — losing it needs a human to re-login
  --exclude '.okx-agent-task'      # XMTP identity + daemon state
  --exclude 'a2a'                  # our pinned copy of the marketplace CLI (never the global one)
  --exclude '.a2a_readiness_state.json'
  --exclude '.a2a_provider_applied.json'
)

RESTART_A2A=0
[[ "${1:-}" == "--restart-a2a" ]] && RESTART_A2A=1

RSYNC=(rsync -a "${EXCLUDES[@]}" -e "ssh -i $KEY -o BatchMode=yes" ./ "$HOST:$REMOTE/")

# The guard. Belt and braces: if a future edit reintroduces a delete flag, this stops the deploy
# rather than discovering it afterwards in the way we discovered it the first time.
if printf '%s\n' "${RSYNC[@]}" | grep -qE -- '--delete'; then
  echo "REFUSING TO DEPLOY: the rsync command contains a --delete flag." >&2
  echo "That is what destroyed the live credentials and the wallet session on 2026-07-26." >&2
  exit 2
fi

echo "==> backing up live state on $HOST"
"${SSH[@]}" '
  set -e
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  dir=/root/concierge-backups/$ts
  mkdir -p "$dir"
  cp -a /opt/concierge/.env "$dir/env" 2>/dev/null || echo "  (no .env to back up)"
  cp -a /opt/concierge/.a2a_provider_applied.json "$dir/a2a-provider-applied.json" \
    2>/dev/null || true
  tar -czf "$dir/onchainos.tar.gz" -C /opt/concierge .onchainos 2>/dev/null || true
  tar -czf "$dir/okx-agent-task.tar.gz" -C /opt/concierge .okx-agent-task 2>/dev/null || true
  chmod -R go-rwx /root/concierge-backups
  ls -1 /root/concierge-backups | tail -3 | sed "s/^/  backup: /"
  # Keep the last 10 so this cannot fill the disk on a shared box.
  ls -1dt /root/concierge-backups/* | tail -n +11 | xargs -r rm -rf
'

echo "==> syncing code (no deletes)"
"${RSYNC[@]}"

echo "==> fixing ownership and restarting the app"
"${SSH[@]}" "
  set -e
  chown -R concierge:concierge $REMOTE/concierge $REMOTE/deploy $REMOTE/docs $REMOTE/verify.py 2>/dev/null || true
  # The queue was once written by a root-run recovery command. A root-owned 0600 queue makes the
  # real concierge service report Permission denied forever, so repair only these exact app-owned
  # state files—not the shared box or another project's HOME.
  chown concierge:concierge \
    $REMOTE/.onchainos/task/pending-decisions-new.json \
    $REMOTE/.onchainos/task/pending-decisions-new.lock 2>/dev/null || true
  # Handler sessions must be able to run exactly the marketplace CLIs and deterministic pricing
  # command named in their role file. This repository owns that narrow service-account policy.
  install -o concierge -g concierge -m 600 \
    $REMOTE/deploy/asp-handler/settings.json $REMOTE/.claude/settings.json
  install -d -o concierge -g concierge -m 750 /opt/concierge-asp
  install -o concierge -g concierge -m 640 \
    $REMOTE/deploy/asp-handler/CLAUDE.md /opt/concierge-asp/CLAUDE.md
  install -o root -g root -m 644 \
    $REMOTE/deploy/concierge-a2a-buyer.service /etc/systemd/system/concierge-a2a-buyer.service
  install -o root -g root -m 644 \
    $REMOTE/deploy/concierge-a2a-buyer.timer /etc/systemd/system/concierge-a2a-buyer.timer
  install -o root -g root -m 644 \
    $REMOTE/deploy/concierge-a2a-provider.service /etc/systemd/system/concierge-a2a-provider.service
  install -o root -g root -m 644 \
    $REMOTE/deploy/concierge-a2a-provider.timer /etc/systemd/system/concierge-a2a-provider.timer
  # The provisioning pair was verified below but never installed here, so an edit to its timer
  # synced to the repo copy and silently changed nothing on the running box. Measured 2026-07-27.
  install -o root -g root -m 644 \
    $REMOTE/deploy/concierge-a2a-provision.service /etc/systemd/system/concierge-a2a-provision.service
  install -o root -g root -m 644 \
    $REMOTE/deploy/concierge-a2a-provision.timer /etc/systemd/system/concierge-a2a-provision.timer
  systemctl daemon-reload
  systemctl enable --now concierge-a2a-buyer.timer
  systemctl enable --now concierge-a2a-provider.timer
  systemctl enable --now concierge-a2a-provision.timer
  cd $REMOTE
  sudo -u concierge -H $REMOTE/.venv/bin/python -c 'from concierge import db; db.migrate()'
  systemctl restart concierge
  $([[ $RESTART_A2A == 1 ]] && echo 'systemctl restart concierge-a2a; sleep 60' || true)
  sleep 4
"

echo "==> verifying (an unverified deploy is not a finished deploy)"
"${SSH[@]}" '
  fail=0
  for u in concierge concierge-a2a concierge-a2a-buyer.timer concierge-a2a-provider.timer concierge-a2a-provision.timer concierge-a2a-readiness.timer \
           concierge-scheduler.timer concierge-watchdog.timer; do
    s=$(systemctl is-active $u); echo "  $u: $s"
    [ "$s" = active ] || fail=1
  done
  echo "  readyz: $(curl -s --max-time 10 https://app.quietdesks.com/readyz)"
  curl -s --max-time 10 https://app.quietdesks.com/readyz | grep -q "\"status\":\"ready\"" || fail=1
  systemctl start concierge-a2a-readiness || true
  sleep 25
  journalctl -u concierge-a2a-readiness -n 5 --no-pager | grep -E "answerable|NOT ANSWERABLE" | tail -2
  journalctl -u concierge-a2a-readiness -n 5 --no-pager | grep -q "\"answerable\": true" || fail=1
  if [ $fail -ne 0 ]; then echo "DEPLOY VERIFICATION FAILED — see above" >&2; exit 1; fi
  echo "  all green"
'
echo "==> done"
