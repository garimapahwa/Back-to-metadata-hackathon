"""FastAPI backend for DataSheriff chat and health endpoints."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent.agent import list_available_tools, run_agent
from mcp_server.config import get_settings
from mcp_server.openmetadata import OpenMetadataClient, OpenMetadataClientError


class ChatRequest(BaseModel):
    """Incoming chat payload from Slack/web clients."""

    message: str = Field(min_length=1)
    session_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Chat endpoint response."""

    response: str
    session_id: str


app = FastAPI(title="DataSheriff API", version="1.0.0")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session history store; suitable for hackathon/demo deployment.
SESSION_STORE: dict[str, list[dict[str, Any]]] = {}


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    """Run DataSheriff agent with per-session memory."""
    sid = payload.session_id or str(uuid4())
    stored_history = SESSION_STORE.get(sid, [])

    # Prevent duplicate growth for stateful clients that send history while a server session exists.
    merged_history = stored_history if stored_history else list(payload.history)
    response_text = await run_agent(payload.message, conversation_history=merged_history)

    SESSION_STORE[sid] = [
        *merged_history,
        {"role": "user", "content": payload.message},
        {"role": "assistant", "content": response_text},
    ]

    return ChatResponse(response=response_text, session_id=sid)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Service liveness and OpenMetadata connectivity status."""
    openmetadata_connected = False
    if settings.has_openmetadata_auth:
        try:
            client = OpenMetadataClient(settings.openmetadata_host, settings.openmetadata_jwt_token)
            client.search(query="*", entity_type="table", limit=1)
            openmetadata_connected = True
        except OpenMetadataClientError:
            openmetadata_connected = False

    return {
        "status": "ok",
        "openmetadata_connected": openmetadata_connected,
        "version": "1.0.0",
    }


@app.get("/tools")
async def tools() -> list[dict[str, Any]]:
    """List all available MCP tools as exposed to the agent."""
    try:
        return await list_available_tools()
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"Failed to list tools: {exc}"}]
