#!/usr/bin/env bash
#
# Runs ON THE SERVER. It is piped in over SSH by .github/workflows/deploy.yml:
#
#     ssh root@<ip> "APP_DIR=... DEPLOY_SHA=... bash -s" < deploy/deploy.sh
#
# You can also run it by hand on the server for a manual deploy:
#
#     cd /opt/brand-brain && bash deploy/deploy.sh
#
# What it does: enter the app directory, pull the exact commit CI verified,
# install any missing dependencies, restart the pm2 process, health-check it —
# and roll back to the previous commit if the new one does not come up healthy.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/brand-brain}"
PM2_NAME="${PM2_NAME:-brand-brain}"
BRANCH="${BRANCH:-main}"
DEPLOY_SHA="${DEPLOY_SHA:-}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:3001/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-15}"   # x 2s = up to 30s for the app to boot
PYTHON="${PYTHON:-python3}"

log() { printf '\n==> %s\n' "$*"; }

# --- preflight --------------------------------------------------------------
cd "$APP_DIR"

if [ ! -d .git ]; then
  echo "ERROR: $APP_DIR is not a git checkout. See deploy/README.md for first-time setup." >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "ERROR: $APP_DIR/.env is missing — the service cannot start without GEMINI_API_KEY." >&2
  exit 1
fi

# pm2 is per-user: root's pm2 and deploy's pm2 are separate daemons with separate
# process lists. This script talks to whichever belongs to the user running it.
#
# A non-interactive `ssh host "cmd"` does not source ~/.bashrc, so an nvm-installed
# pm2 is missing from PATH here even though it works fine when you log in by hand.
# That is the usual cause of "pm2: command not found" in a deploy that works
# manually — so load nvm ourselves before giving up.
if ! command -v pm2 >/dev/null 2>&1; then
  for nvm_sh in "$HOME/.nvm/nvm.sh" /usr/local/nvm/nvm.sh; do
    if [ -s "$nvm_sh" ]; then
      # shellcheck disable=SC1090
      . "$nvm_sh" >/dev/null 2>&1 || true
      break
    fi
  done
fi

if ! command -v pm2 >/dev/null 2>&1; then
  echo "ERROR: pm2 is not on PATH for $(whoami)." >&2
  echo "       Install it with 'npm install -g pm2', or find it with" >&2
  echo "       'command -v pm2' when logged in and add that directory to PATH." >&2
  exit 1
fi

log "Using pm2 at $(command -v pm2) as $(whoami)"

# git refuses to touch a repo owned by another user ("dubious ownership"), which
# is exactly the case when root deploys a checkout owned by the service user.
# (--add is guarded, or every deploy would append another duplicate line.)
if ! git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$APP_DIR"; then
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
fi

# Whoever owns the checkout is who the app runs as; deploying as root would
# otherwise leave root-owned files behind for it to choke on.
OWNER="$(stat -c '%U:%G' .)"

PREVIOUS_SHA="$(git rev-parse HEAD)"
log "Current commit: $(git log -1 --oneline)"

log "Fetching origin"
git fetch --prune --quiet origin

# The workflow passes the exact commit it verified. Falling back to the branch tip
# only matters for manual runs.
if [ -z "$DEPLOY_SHA" ]; then
  DEPLOY_SHA="$(git rev-parse "origin/$BRANCH")"
fi

if [ "$DEPLOY_SHA" = "$PREVIOUS_SHA" ]; then
  log "Already at $DEPLOY_SHA — reinstalling and restarting anyway"
fi

# --- helpers ----------------------------------------------------------------

# Check out a commit, install missing dependencies, restart the pm2 process.
release() {
  local sha="$1"

  log "Checking out $sha"
  git checkout --quiet "$BRANCH" 2>/dev/null || git checkout --quiet -B "$BRANCH" "origin/$BRANCH"
  git reset --hard --quiet "$sha"

  if [ ! -x .venv/bin/python ]; then
    log "Creating virtualenv"
    "$PYTHON" -m venv .venv
  fi

  # pip only downloads what is missing or version-mismatched, so this is cheap on
  # a deploy that did not touch requirements.txt.
  log "Installing dependencies"
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt

  if [ "$(id -u)" -eq 0 ] && [ "$OWNER" != "root:root" ]; then
    log "Restoring ownership to $OWNER"
    chown -R "$OWNER" "$APP_DIR"
  fi

  # 'pm2 restart' fails if the process was never registered (first deploy, or the
  # pm2 daemon was reset), so fall back to starting it from the config file.
  if pm2 describe "$PM2_NAME" >/dev/null 2>&1; then
    log "Restarting pm2 process $PM2_NAME"
    pm2 restart "$PM2_NAME" --update-env
  else
    log "pm2 process $PM2_NAME not found — starting it from deploy/pm2.config.json"
    pm2 start deploy/pm2.config.json
  fi

  # Persist the process list so it survives a reboot (needs 'pm2 startup' once).
  pm2 save --force >/dev/null 2>&1 || true
}

# Poll /health until it answers or we run out of retries.
healthy() {
  local i
  for i in $(seq 1 "$HEALTH_RETRIES"); do
    if curl --fail --silent --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

# --- deploy -----------------------------------------------------------------
release "$DEPLOY_SHA"

if healthy; then
  log "Healthy. Deployed: $(git log -1 --oneline)"
  curl --silent --max-time 5 "$HEALTH_URL" || true
  echo
  pm2 list
  exit 0
fi

# --- rollback ---------------------------------------------------------------
log "HEALTH CHECK FAILED at $HEALTH_URL — rolling back to $PREVIOUS_SHA"
pm2 list || true
pm2 logs "$PM2_NAME" --lines 60 --nostream || true

release "$PREVIOUS_SHA"

if healthy; then
  log "Rolled back to $(git log -1 --oneline) and it is healthy. The new commit was NOT deployed."
else
  log "ROLLBACK IS ALSO UNHEALTHY — the service is down and needs manual attention."
fi
exit 1
