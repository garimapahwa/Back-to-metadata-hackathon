#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash deploy/bootstrap_ubuntu_vm.sh <REPO_URL>
# Example:
#   bash deploy/bootstrap_ubuntu_vm.sh https://github.com/<user>/<repo>.git

REPO_URL="${1:-}"
if [[ -z "$REPO_URL" ]]; then
  echo "Usage: bash deploy/bootstrap_ubuntu_vm.sh <REPO_URL>"
  exit 1
fi

APP_DIR="$HOME/datasheriff"

echo "[1/5] Installing Docker Engine + Compose plugin"
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"

echo "[2/5] Cloning repository"
if [[ -d "$APP_DIR/.git" ]]; then
  echo "Repository already exists at $APP_DIR, pulling latest"
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

echo "[3/5] Preparing env file"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

echo "[4/5] Creating shared Excel directory"
sudo mkdir -p /opt/datasheriff/shared
sudo chown -R "$USER":"$USER" /opt/datasheriff/shared

echo "[5/5] Done"
echo "IMPORTANT NEXT STEPS:"
echo "- Log out and SSH back in once (docker group update)."
echo "- Edit $APP_DIR/.env and set all required keys."
echo "- Put Excel KB file at /opt/datasheriff/shared/excel_kb.xlsx and set EXCEL_KB_PATH."
echo "- Start services with: docker compose up -d --build datasheriff-backend datasheriff-bot"
