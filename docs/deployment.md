# Manual deployments from the CLI

Pushing code alone does not deploy. The manually triggered `deploy.yml` workflow
builds and tests a Linux amd64 image, publishes a commit tag and the `lab` tag to
GHCR, and the Ubuntu timer checks that tag every two minutes. No inbound SSH
connection from GitHub is needed. The server must have outbound access to GHCR.

## 1. Commit and push from your development machine

Review and commit the application changes and deployment files, then push to main:

```powershell
git add src/impedance_analyzer/app.py tests/test_measurement_model.py .github/workflows/deploy.yml compose.deploy.yml scripts/update-lab.sh .gitattributes docs/deployment.md
git commit -m "Add measurement views and manual container deployment"
git push origin main
gh workflow run deploy.yml --ref main
gh run list --workflow deploy.yml --limit 5
gh run watch
```

Wait for the workflow to succeed before continuing. The workflow must first exist
on the default branch. No extra GitHub Actions secret is needed: publishing uses
the workflow's GITHUB_TOKEN.

## 2. Make the container package public once

A new GHCR package is private by default even when its repository is public.
Open your GitHub profile's Packages page, select `impedance-analyzer-4294a`, then
Package settings → Change visibility → Public. This makes the image downloadable
on Ubuntu without credentials. The image contains the application source, which
is already public; do not include credentials in the image.

Alternatively, keep it private and run `sudo docker login ghcr.io` on Ubuntu
using a GitHub username and a classic token with `read:packages`. The timer runs
as root, so the registry login must also use sudo.

## 3. Update the existing Ubuntu checkout

SSH into the server and enter the directory where you currently run Compose.
Use that same checkout so Compose retains its existing project name and `.env`.

```bash
cd /your/existing/impedance-analyzer-4294A
git pull --ff-only
docker compose version
uname -m
```

These instructions expect Docker Compose with `--wait` support and an x86_64
server. The workflow currently builds Linux amd64 images.

If your instrument IP or ROOT_PATH was previously supplied only on the command
line, save those values in this checkout's `.env` first. Existing `.env` values
are used by the deployment Compose file too. Keep `.env` on the server.

Export any unsaved measurements and finish any active sweep before the first
update. Container replacement disconnects the analyzer and clears in-memory data.

```bash
sudo bash scripts/update-lab.sh
docker compose -f compose.deploy.yml ps
```

This pulls the published image, replaces the existing service, and waits for
HTTP health. Caddy configuration and the published port remain the same.
If this fails, inspect `docker compose -f compose.deploy.yml logs --tail 100`.
The health check confirms HTTP readiness, not analyzer connectivity.

## 4. Install the Ubuntu timer

Run this while still in that checkout. It records the checkout's absolute path:

```bash
DEPLOY_DIR="$(pwd -P)"
sudo tee /etc/systemd/system/impedance-analyzer-update.service >/dev/null <<EOF
[Unit]
Description=Pull and deploy the manually published impedance analyzer image
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$DEPLOY_DIR
ExecStart=/bin/bash "$DEPLOY_DIR/scripts/update-lab.sh"
TimeoutStartSec=15min
EOF

sudo tee /etc/systemd/system/impedance-analyzer-update.timer >/dev/null <<'EOF'
[Unit]
Description=Check for manually published impedance analyzer updates

[Timer]
OnBootSec=2min
OnUnitInactiveSec=2min
Unit=impedance-analyzer-update.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now impedance-analyzer-update.timer
systemctl list-timers impedance-analyzer-update.timer
```

If you previously installed the optional README systemd service that runs the
old `docker compose up -d`, disable that service for future boots (without stopping
the running container), or update it to use `-f compose.deploy.yml`. Docker's
`restart: unless-stopped` already handles container startup after reboot.

## 5. Future deployments from your development machine

```powershell
git push origin main
gh workflow run deploy.yml --ref main
gh run watch
```

Commit your changes before pushing. Only triggering the workflow publishes an
update; ordinary pushes do not. After publication, allow around two minutes plus
image download/startup time for Ubuntu to deploy it. An unchanged image does not
restart the container. GitHub's successful run means publication succeeded;
server deployment is checked separately:

```bash
journalctl -u impedance-analyzer-update.service -n 60 --no-pager
docker compose -f compose.deploy.yml ps
docker inspect impedance-analyzer --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
```

Server Compose/configuration changes still require a checkout update. Routine
application changes arrive entirely through the container image.

## Pause and roll back

Pause updates before long measurements if needed:

```bash
sudo systemctl stop impedance-analyzer-update.timer
```

To roll back, stop the timer, wait for any running update service to finish, then
use the full SHA from a previous successful workflow run:

```bash
sudo env IMAGE_TAG=sha-FULL_COMMIT_SHA bash scripts/update-lab.sh
```

There is no automatic rollback. A failed health check is recorded in the service
logs. Keep the timer stopped after rollback, as resuming it applies the current
`lab` image again. Once a corrected image is published, resume with:

```bash
sudo systemctl start impedance-analyzer-update.timer
```
