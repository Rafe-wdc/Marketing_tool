# Deploying to a server with GitHub Actions

Push to `main` → GitHub Actions verifies the build, SSHes in as `root`, enters the
app directory, pulls that exact commit, installs any missing dependencies,
`pm2 restart`s the process and health-checks it. **If the health check fails it
automatically rolls back** to the previous commit and the workflow fails red.

| File | What it is |
|------|------------|
| [`../.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) | The GitHub Actions workflow. |
| [`deploy.sh`](deploy.sh) | Runs on the server (piped in over SSH). Pull → install → pm2 restart → health-check → rollback. |
| [`pm2.config.json`](pm2.config.json) | pm2 process definition — how uvicorn is launched, and what pm2 restarts on boot/crash. |
| [`nginx.conf.example`](nginx.conf.example) | Reverse proxy + TLS front, with the 60s timeout Script Lab needs. |

`brand-brain.service` is the systemd alternative to pm2. Use **one or the other** —
if both are enabled they fight over port 3001.

---

## 1. One-time server setup

Ubuntu/Debian, as `root` (the Vultr default login).

```bash
# --- packages -------------------------------------------------------------
apt update
apt install -y python3 python3-venv python3-pip git curl nginx

# --- pm2 (needs node) -----------------------------------------------------
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt install -y nodejs
npm install -g pm2

# --- clone the repo -------------------------------------------------------
git clone https://github.com/Rafe-wdc/Marketing_tool.git /opt/brand-brain
cd /opt/brand-brain

# --- config ---------------------------------------------------------------
cp .env.example .env
nano .env                 # set GEMINI_API_KEY, API_KEY, ALLOWED_ORIGINS, MONGODB_URI
chmod 600 .env

# --- virtualenv -----------------------------------------------------------
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# --- start under pm2 ------------------------------------------------------
mkdir -p /var/log/brand-brain
pm2 start deploy/pm2.config.json
pm2 save                  # remember this process list
pm2 startup               # prints a command — run it, to restart pm2 on boot

curl http://127.0.0.1:3001/health          # -> {"ok":true,"model":"gemini-2.5-flash"}
```

> If the repo is private, clone over SSH with a deploy key instead of HTTPS so the
> workflow's `git fetch` can authenticate.

### pm2 is per-user — start it as the same user the workflow logs in as

Each Linux user gets their own pm2 daemon with its own process list. `pm2 list` as
`root` will not show a process that `deploy` started, and `pm2 restart brand-brain`
would then fail with *process not found*, after which `deploy.sh` starts a **second**
copy that dies on the already-bound port.

The workflow logs in as `root`, so do the setup above as `root` too. Check with:

```bash
pm2 list                  # brand-brain must be here, status "online"
```

If you already started it as another user, move it: `sudo -u deploy pm2 delete
brand-brain`, then run the pm2 block above as root.

### If `pm2: command not found` during a deploy

A non-interactive `ssh root@IP "command"` does not read `~/.bashrc`, so an
nvm-installed pm2 is invisible even though it works when you log in by hand.
`deploy.sh` sources `~/.nvm/nvm.sh` itself to cover this. If pm2 lives somewhere
else, the cleanest fix is a system-wide install (`npm install -g pm2` under the
NodeSource node above), which lands in `/usr/bin` and is always on `PATH`.

### Nginx + TLS

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/brand-brain
sudo nano /etc/nginx/sites-available/brand-brain      # set your server_name
sudo ln -s /etc/nginx/sites-available/brand-brain /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.yourdomain.com
```

---

## 2. SSH access for GitHub Actions

The workflow logs in as **root with the Vultr password**, using `sshpass`. Two
things to check on the server before wiring it up:

```bash
# Password logins must be enabled for root (the Vultr default, but confirm):
sudo grep -E '^(PermitRootLogin|PasswordAuthentication)' /etc/ssh/sshd_config
#   PermitRootLogin yes
#   PasswordAuthentication yes

# Grab the host key fingerprint for SSH_KNOWN_HOSTS:
ssh-keyscan -p 22 <server-ip>
```

Verify the login works before pushing:

```bash
# Note the quoted form — this is exactly how the workflow runs, so it also proves
# pm2 is on PATH non-interactively (see §1 if it is not).
ssh root@<server-ip> 'pm2 restart brand-brain && echo ok'
```

> A root password in CI is weaker than a key: it is reusable, it grants full
> control of the box, and anything that can read the runner's environment gets it.
> If you want to tighten this later, switch back to a dedicated `deploy` user with
> an SSH key — `deploy.sh` still supports it (it uses `sudo` whenever it is not
> running as root).

---

## 3. GitHub secrets and variables

**Settings → Secrets and variables → Actions**

Secrets:

| Secret | Value |
|--------|-------|
| `SSH_HOST` | server IP or hostname |
| `SSH_PASSWORD` | the root password |
| `SSH_KNOWN_HOSTS` | output of `ssh-keyscan -p 22 <server-ip>` |

`SSH_KNOWN_HOSTS` is technically optional, but with password auth it matters more
than usual: without it the workflow sends the root password to whatever host
answers on that IP, with no way to detect a man-in-the-middle. Set it. Re-run
`ssh-keyscan` if you ever rebuild the server.

Variables (only if your setup differs from the defaults):

| Variable | Default |
|----------|---------|
| `SSH_USER` | `root` |
| `SSH_PORT` | `22` |
| `APP_DIR` | `/opt/brand-brain` |
| `PM2_NAME` | `brand-brain` — must match `name` in `pm2.config.json` |
| `HEALTH_URL` | `http://127.0.0.1:3001/health` |

The deploy job uses a `production` environment. GitHub creates it on the first run;
add required reviewers under **Settings → Environments → production** if you want a
manual approval gate before each deploy.

---

## 4. Running a deploy

- **Automatic** — push to `main`. Doc-only, `frontend/` and `.gitignore` changes are
  skipped, since they do not affect the running service.
- **Manual** — **Actions → Deploy to server → Run workflow**. Deploys the HEAD of
  the branch you pick.

Deploys are serialised per branch (`concurrency`), so two pushes in a row queue
rather than fighting over the server.

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Missing repository secret(s)` | Secrets not set, or set on the wrong repo/environment. |
| `Permission denied, please try again` | Wrong `SSH_PASSWORD`, or `PasswordAuthentication`/`PermitRootLogin` is `no` in `/etc/ssh/sshd_config`. |
| `Host key verification failed` | `SSH_KNOWN_HOSTS` is stale — re-run `ssh-keyscan` (it changes if the server is rebuilt). |
| `detected dubious ownership` | `git config --global --add safe.directory /opt/brand-brain` as the deploying user. `deploy.sh` does this itself, so it usually means it ran on a different path. |
| `pm2 is not on PATH` | pm2 installed under nvm, or not installed for `root`. See §1 above. |
| `pm2 process not found — starting it` on every deploy | pm2 was started as a different user, or `PM2_NAME` does not match `name` in `pm2.config.json`. Check with `pm2 list` as root. |
| `HEALTH CHECK FAILED` then rollback | The new commit does not boot. The workflow log prints the last 60 pm2 log lines; usually a missing env var — most often `GEMINI_API_KEY`, which makes `app.py` raise at startup. |
| pm2 shows `errored` / restart count climbing | `pm2 logs brand-brain --lines 50`. A wrong `interpreter` path in `pm2.config.json` (pm2 defaulting to node) shows up as an instant syntax error. |
| `$APP_DIR/.env is missing` | Config was never created on the server, or `git reset --hard` ran in the wrong directory. `.env` is gitignored, so deploys never overwrite it. |
| Deploy succeeds but the API 502s | Nginx is pointing at the wrong port, or the app binds `127.0.0.1` while Nginx proxies a different address. |

Useful on the server:

```bash
pm2 list                                       # is it online, how many restarts
pm2 logs brand-brain --lines 50                # recent output
pm2 logs brand-brain                           # live logs
pm2 describe brand-brain                       # the exact command pm2 is running
cd /opt/brand-brain && git log -1 --oneline    # which commit is live
```

**Manual deploy / rollback** — same script the workflow pipes in:

```bash
cd /opt/brand-brain
bash deploy/deploy.sh                          # deploy the tip of main
DEPLOY_SHA=<good-sha> bash deploy/deploy.sh    # roll back to a known-good commit
```

---

## Notes

- The server checkout is reset with `git reset --hard` on every deploy — never edit
  files directly on the server, they will be wiped. `.env` and `.venv/` are
  gitignored and survive.
- `.env` is never touched by the workflow. Changing config is a server-side edit
  plus `pm2 restart brand-brain --update-env` — env is read once at startup.
- `pm2 save` runs after every deploy, so the saved process list stays current. Run
  `pm2 startup` once at setup or pm2 will not come back after a reboot.
- `deploy.sh` is piped in from the checkout, so the server always runs the deploy
  script from the commit being deployed. Changes to it take effect immediately.
