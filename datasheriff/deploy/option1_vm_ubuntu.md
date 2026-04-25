# Option 1: Deploy DataSheriff on a Single Ubuntu VM

This guide runs DataSheriff backend + Slack bot as always-on Docker services so everyone in your Slack workspace can use it.

## 1) Create VM

Use any Ubuntu 22.04+ VM (AWS Lightsail/EC2, DigitalOcean, Azure VM, GCP Compute).

Recommended minimum:
- 2 vCPU
- 4 GB RAM
- 30 GB disk

Open inbound ports:
- 22 (SSH)
- 8000 (optional, only if you want API access externally)

## 2) SSH into VM and install Docker

Run on VM:

sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER

Log out and SSH back in once after group update.

## 3) Clone repo

cd ~
git clone <YOUR_REPO_URL> datasheriff
cd datasheriff

## 4) Configure production env

cp .env.example .env
nano .env

Set at least:
- OPENMETADATA_HOST
- OPENMETADATA_JWT_TOKEN
- ANTHROPIC_API_KEY (or keep DATASHERIFF_FALLBACK_MODE=true)
- SLACK_BOT_TOKEN
- SLACK_APP_TOKEN
- EXCEL_KB_PATH

For EXCEL_KB_PATH, place your shared Excel file at a stable location on VM, for example:
- /opt/datasheriff/shared/excel_kb.xlsx

## 5) Copy Excel file to VM

On your local machine:

scp /local/path/excel_kb.xlsx <vm_user>@<vm_ip>:/tmp/excel_kb.xlsx

On VM:

sudo mkdir -p /opt/datasheriff/shared
sudo mv /tmp/excel_kb.xlsx /opt/datasheriff/shared/excel_kb.xlsx
sudo chown -R $USER:$USER /opt/datasheriff/shared

Update EXCEL_KB_PATH in .env:
- EXCEL_KB_PATH=/opt/datasheriff/shared/excel_kb.xlsx

## 6) Start services

docker compose up -d --build datasheriff-backend datasheriff-bot

Optional web UI:

docker compose up -d --build datasheriff-web

## 7) Verify

docker ps
docker logs -f datasheriff-bot
curl -s http://localhost:8000/health

Expected bot log includes:
- Bolt app is running

## 8) Slack app checklist (for everyone)

In Slack app settings:
- Socket Mode: enabled
- Event Subscriptions: enabled
- Bot events: app_mention, message.im
- App installed to workspace
- Bot invited to channels users will ask in

## 9) Updating Excel later

Replace the file at EXCEL_KB_PATH. The bot reloads it automatically on the next question.

Example update:

scp /local/path/new_excel_kb.xlsx <vm_user>@<vm_ip>:/tmp/excel_kb.xlsx
mv /tmp/excel_kb.xlsx /opt/datasheriff/shared/excel_kb.xlsx

No restart required.

## 10) Upgrade app

cd ~/datasheriff
git pull
docker compose up -d --build datasheriff-backend datasheriff-bot
