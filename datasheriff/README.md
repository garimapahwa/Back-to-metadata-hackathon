# DataSheriff

DataSheriff is a natural-language AI agent that helps data teams query and operate on OpenMetadata using plain English. It combines an MCP server (tool layer), an Anthropic Claude reasoning loop, and two user interfaces (Slack + Web chat) so users can discover assets, analyze lineage impact, apply governance tags, and inspect observability signals without touching raw APIs.

The project is built as a production-quality hackathon stack with strong separation of concerns. The MCP server is the system core and exposes carefully typed tools that map directly to OpenMetadata capabilities. The agent orchestrates iterative tool calls with Claude Sonnet, while FastAPI, Slack Bolt, and React provide operator-friendly interfaces.

## Architecture

```text
+-----------------------+         +-----------------------------+
| Slack Bot (Bolt)      |         | React Web UI                |
| - app_mention / DM    |         | - ChatWindow / InputBar     |
+-----------+-----------+         +--------------+--------------+
            |                                      |
            +------------------+-------------------+
                               |
                     +---------v---------+
                     | FastAPI server.py |
                     | /chat /health /tools
                     +---------+---------+
                               |
                     +---------v---------+
                     | Agent (Claude)    |
                     | agent.run_agent   |
                     +---------+---------+
                               |
                    MCP stdio  |
                               v
                   +-----------+-----------+
                   | FastMCP server        |
                   | mcp_server/main.py    |
                   +-----------+-----------+
                               |
                     OpenMetadata REST API
                               |
                               v
                    +----------+-----------+
                    | OpenMetadata instance |
                    +-----------------------+
```

## Prerequisites

- Python 3.11+
- Node.js 18+
- OpenMetadata instance (local or hosted)
- Anthropic API key for Claude
- Optional: Slack app credentials for bot usage

## Project Structure

```text
datasheriff/
├── mcp_server/
├── agent/
├── slack_bot/
├── web_ui/
├── demo/
├── server.py
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## Setup

1. Clone and enter the project directory.
2. Create a Python virtual environment.
3. Install backend dependencies.
4. Install frontend dependencies.
5. Copy env vars from `.env.example` into `.env`.

```bash
cd datasheriff
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd web_ui && npm install && cd ..
cp .env.example .env
```

## Get OpenMetadata JWT Token

1. Open your OpenMetadata UI.
2. Go to your user profile settings.
3. Create a personal access token (JWT/PAT).
4. Set it in `.env` as `OPENMETADATA_JWT_TOKEN`.

## Run Locally

### Backend (FastAPI + MCP tools + Agent)

```bash
cd datasheriff
source .venv/bin/activate
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (React)

```bash
cd datasheriff/web_ui
npm run dev
```

- Web app: http://localhost:3000
- API docs: http://localhost:8000/docs

### Optional: Slack Bot

```bash
cd datasheriff
source .venv/bin/activate
python -m slack_bot.bot
```

Slack Socket Mode only requires `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`. Set `SLACK_SIGNING_SECRET` too if you also plan to expose HTTP event endpoints.

## Shared Slack + Excel Knowledge Base

You can make DataSheriff usable by everyone in your Slack workspace and let non-engineers maintain answers in a shared Excel file.

### 1) Make Slack app usable by everyone

- Install the Slack app to the full workspace (not just one user).
- Add the bot to channels where users ask questions.
- Keep `app_mention` enabled so anyone in those channels can query via mentions.
- Keep DM access enabled (`message.im`) so anyone can ask in direct messages.

### 2) Configure Excel-backed answers

In `.env`, set:

- `EXCEL_KB_PATH`: absolute path to your shared `.xlsx` file
- `EXCEL_KB_SHEET`: worksheet name (default `Sheet1`)
- `EXCEL_KB_QUESTION_COLUMN`: column containing user question patterns
- `EXCEL_KB_ANSWER_COLUMN`: column containing answers
- `EXCEL_KB_KEYWORDS_COLUMN`: optional comma-separated keywords

Expected sheet columns (header row):

- `question`
- `answer`
- `keywords` (optional)

When users edit and save the Excel file, DataSheriff automatically reloads it on the next question (no restart needed).

### 3) Example rows

- question: Who owns sales dashboard?
    answer: Sales Ops owns it. Contact @sales-analytics.
    keywords: owner,sales,dashboard
- question: How to request prod access?
    answer: Submit access request in JIRA with manager approval.
    keywords: access,production,permission

## Run with Docker

```bash
cd datasheriff
docker compose up --build
```

- Backend: http://localhost:8000
- Web UI: http://localhost:3000

## Deploy for Team Usage

To let everyone in your Slack workspace use the bot, run backend + bot as always-on services on one host (VM, ECS, Railway, Render, or any Docker server).

### 1) Configure production env

- Set all required values in `.env`:
    - `OPENMETADATA_HOST`
    - `OPENMETADATA_JWT_TOKEN`
    - `ANTHROPIC_API_KEY` (or keep fallback mode)
    - `SLACK_BOT_TOKEN`
    - `SLACK_APP_TOKEN`
    - `EXCEL_KB_PATH` (path available on the deployment host)

### 2) Start services

```bash
cd datasheriff
docker compose up -d --build datasheriff-backend datasheriff-bot
```

Optional UI:

```bash
docker compose up -d --build datasheriff-web
```

### 3) Verify service health

- API: `GET /health` on port 8000
- Bot logs:

```bash
docker logs -f datasheriff-bot
```

### 4) Make Slack app available to everyone

- Install app to workspace.
- Enable Event Subscriptions.
- Subscribe bot events: `app_mention`, `message.im`.
- Invite bot to team channels.

## Shared Excel in Production

For collaborative editing, keep the Excel file in a shared location that your deployment host can read.

Recommended patterns:

- Shared drive mounted on host (NAS/Drive sync folder)
- Cloud file share mounted in container/host volume

Point `EXCEL_KB_PATH` to that mounted `.xlsx` file. After users edit and save, bot answers update automatically on the next question.

## Example Queries

- Who owns `prod.ecommerce.orders`?
- Show me upstream lineage for `prod.ecommerce.order_items`.
- What breaks if I deprecate `prod.ecommerce.customers.email`?
- Find all untagged PII columns in schema `prod.ecommerce`.
- Apply tag `PII.Sensitive` to `prod.ecommerce.customers.email`.
- Run a governance health report for `prod.ecommerce`.
- Check freshness for `prod.ecommerce.orders`.

## Demo Seed Script

Use the script below to create sample demo assets and lineage when your catalog is sparse:

```bash
cd datasheriff
source .venv/bin/activate
python demo/seed_demo.py
```

It seeds:
- `prod.ecommerce.orders`
- `prod.ecommerce.customers` (email column left untagged intentionally)
- `prod.ecommerce.order_items`
- `analytics.reporting.revenue_dashboard` (as downstream consumer)

## MCP Tool Reference

| Category | Tool | Purpose |
|---|---|---|
| Discovery | `search_assets` | Search tables/dashboards/pipelines/topics |
| Discovery | `get_table_details` | Fetch rich table metadata |
| Discovery | `get_column_info` | Inspect column-level metadata and PII indicators |
| Discovery | `find_assets_by_owner` | List assets owned by user/team |
| Discovery | `get_asset_summary` | Fast one-paragraph table summary |
| Lineage | `get_upstream_lineage` | Trace dependencies feeding into a table |
| Lineage | `get_downstream_lineage` | Trace consumers impacted by changes |
| Lineage | `run_impact_analysis` | Group downstream impact by asset type/owner |
| Governance | `get_tags` | Inspect table and column tags |
| Governance | `apply_tag` | Apply table/column tag via JSON Patch |
| Governance | `find_untagged_pii_columns` | Detect likely PII fields missing tags |
| Governance | `assign_owner` | Set/replace owner metadata |
| Governance | `get_governance_report` | Compute metadata health KPIs |
| Observability | `get_data_quality` | Read quality suites and failures |
| Observability | `get_pipeline_status` | Inspect pipeline run health |
| Observability | `get_table_freshness` | Compute freshness/staleness status |
| Observability | `get_observability_summary` | Summarize data reliability posture |

## Notes

- The MCP server is the primary deliverable for this hackathon project.
- The agent loop supports iterative tool chains and tool-failure recovery.
- For production hardening, replace in-memory session history with Redis/Postgres.
