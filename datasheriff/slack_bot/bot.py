"""Slack Bolt Socket Mode bot for DataSheriff."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import re

from dotenv import load_dotenv
from slack_bolt.error import BoltError
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from agent.agent import run_agent
from slack_bot.formatter import format_response_blocks

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

app_kwargs = {"token": SLACK_BOT_TOKEN}
if SLACK_SIGNING_SECRET:
    app_kwargs["signing_secret"] = SLACK_SIGNING_SECRET

app = App(**app_kwargs)

# Per-DM database URL context so users can send URL once and ask follow-up questions.
DM_DB_URL_CONTEXT: dict[str, str] = {}
DB_URL_PATTERN = re.compile(
    r"\b((?:postgresql(?:\+psycopg2)?|mysql(?:\+pymysql)?|sqlite)://[^\s`]+)",
    flags=re.IGNORECASE,
)


def _strip_mention(text: str) -> str:
    """Remove leading @bot mentions from a message body."""
    return re.sub(r"<@[^>]+>", "", text or "").strip()


def _send_typing_indicator(channel: str) -> None:
    """Send a best-effort typing indicator without breaking the response flow."""
    typing_fn = getattr(app.client, "send_typing", None)
    if callable(typing_fn):
        try:
            typing_fn(channel=channel)
        except Exception:  # noqa: BLE001
            return


def _run_agent_sync(message: str) -> str:
    """Run async agent from sync Slack event handlers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_agent(message))

    # If an event loop is already running in this thread, execute in a worker thread.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(run_agent(message)))
        return future.result()


def _extract_db_url(text: str) -> str | None:
    """Extract a DB URL from free text when present."""
    match = DB_URL_PATTERN.search(text or "")
    return match.group(1).strip() if match else None


def _apply_dm_db_context(channel: str, user_text: str) -> tuple[str, str | None]:
    """Attach remembered DB URL to DM follow-up questions when URL is omitted."""
    cleaned = (user_text or "").strip()
    lowered = cleaned.lower()

    if lowered in {"clear db", "reset db", "forget db", "clear database", "reset database"}:
        DM_DB_URL_CONTEXT.pop(channel, None)
        return "", None

    incoming_url = _extract_db_url(cleaned)
    if incoming_url:
        DM_DB_URL_CONTEXT[channel] = incoming_url
        return cleaned, incoming_url

    remembered_url = DM_DB_URL_CONTEXT.get(channel)
    if not remembered_url:
        return cleaned, None

    # Prefix remembered URL so remote-db tools can trigger in the agent.
    return f"{remembered_url} {cleaned}", remembered_url


@app.event("app_mention")
def handle_app_mention(event: dict, say) -> None:  # type: ignore[no-untyped-def]
    """Handle channel mentions addressed to DataSheriff."""
    user_text = _strip_mention(event.get("text", ""))
    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")

    if channel:
        _send_typing_indicator(channel)

    placeholder = say(channel=channel, thread_ts=thread_ts, text="🤠 Investigating...")

    try:
        response = _run_agent_sync(user_text)
    except Exception as exc:  # noqa: BLE001
        response = f"I hit an internal error while processing that request: {exc}"
    blocks = format_response_blocks(response)

    app.client.chat_update(
        channel=channel,
        ts=placeholder.get("ts"),
        text=response,
        blocks=blocks,
    )


@app.event("message")
def handle_dm(event: dict, say) -> None:  # type: ignore[no-untyped-def]
    """Handle direct messages sent to the bot."""
    if event.get("channel_type") not in {"im", "app_home"}:
        return
    if event.get("subtype") is not None:
        return

    user_text = (event.get("text") or "").strip()
    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")

    if not channel:
        return

    contextual_message, active_db_url = _apply_dm_db_context(channel, user_text)

    if not contextual_message:
        response = (
            "Cleared your remembered database URL for this DM. "
            "Send a new DB URL to start again."
        )
        blocks = format_response_blocks(response)
        say(channel=channel, thread_ts=thread_ts, text=response, blocks=blocks)
        return

    _send_typing_indicator(channel)

    placeholder = say(channel=channel, thread_ts=thread_ts, text="🤠 Investigating...")

    try:
        response = _run_agent_sync(contextual_message)
    except Exception as exc:  # noqa: BLE001
        response = f"I hit an internal error while processing that request: {exc}"

    if active_db_url and not _extract_db_url(user_text):
        response = f"Using remembered DB URL for this DM.\n\n{response}"

    blocks = format_response_blocks(response)

    app.client.chat_update(
        channel=channel,
        ts=placeholder.get("ts"),
        text=response,
        blocks=blocks,
    )


if __name__ == "__main__":
    if not (SLACK_BOT_TOKEN and SLACK_APP_TOKEN):
        raise RuntimeError("Missing Slack credentials. Configure SLACK_BOT_TOKEN and SLACK_APP_TOKEN.")
    try:
        SocketModeHandler(app, SLACK_APP_TOKEN).start()
    except BoltError as exc:
        raise RuntimeError(
            "Slack auth failed. Check that SLACK_BOT_TOKEN is a valid bot token for the app, "
            "and that the app has Socket Mode enabled with the correct SLACK_APP_TOKEN."
        ) from exc
