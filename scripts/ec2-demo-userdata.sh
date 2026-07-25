#!/bin/bash
# ec2-demo-userdata.sh — one-box demo bootstrap for mcp-bookmarks (CloudFront edition)
# ---------------------------------------------------------------------------
# Target AMI : Amazon Linux 2023 (x86_64)
# Instance   : t3.micro (1 GB)
# Topology   : podman[ mcp-bookmarks (SQLite) ] listening on :8000
#              TLS + public HTTPS is terminated by CloudFront (see terraform/demo/).
#              The security group only admits CloudFront's origin-facing prefix list,
#              so :8000 is "public" on paper but only CloudFront can reach it.
# Image      : pulled from GHCR (built by .github/workflows/publish-image.yml).
#              First boot is now a ~10s pull, not a 3-min on-box build.
# Reachable  : https://<distribution>.cloudfront.net/sse   (free default cert)
# ---------------------------------------------------------------------------
set -euo pipefail
exec > >(tee /var/log/mcp-bootstrap.log) 2>&1   # tail -f via SSM to watch progress

# ── Config knobs ────────────────────────────────────────────────────────────
IMAGE="ghcr.io/kayaman/mcp-bookmarks:latest"    # public package → anonymous pull
DATA_DIR="/opt/mcp-bookmarks-data"              # on the root EBS vol → survives stop/start
APP_PORT="8000"

# ── 0. Small swap: cheap insurance for runtime memory spikes (trafilatura) ───
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ── 1. Podman ────────────────────────────────────────────────────────────────
dnf -y install podman
install -d -m 0755 "$DATA_DIR"

# ── 2. Pull the prebuilt image from GHCR ─────────────────────────────────────
podman pull "$IMAGE"

# ── 3. Run the MCP server — SQLite mode, published on :8000 ───────────────────
#    Bound to 0.0.0.0 so the CloudFront origin can reach it; the security group
#    restricts inbound :8000 to the CloudFront origin-facing managed prefix list.
podman rm -f mcp-bookmarks 2>/dev/null || true
podman run -d --name mcp-bookmarks --restart=always \
  -p ${APP_PORT}:8000 \
  -v "$DATA_DIR":/data \
  -e MCP_HOST=0.0.0.0 -e MCP_PORT=8000 \
  -e BOOKMARKS_DB_PATH=/data/bookmarks.db \
  "$IMAGE"

# ── 4. Bring the container back after a reboot / stop-start ───────────────────
systemctl enable --now podman-restart.service

echo "Bootstrap complete. Origin is http://0.0.0.0:${APP_PORT}; CloudFront fronts it with HTTPS."
