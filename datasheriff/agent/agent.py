"""Anthropic-powered agent loop that uses MCP tools from DataSheriff."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.prompts import SYSTEM_PROMPT

load_dotenv()

MODEL_NAME = "claude-sonnet-4-20250514"
MAX_TOOL_ROUNDS = 8


def _extract_block_type(block: Any) -> str:
    raw = getattr(block, "type", None)
    if raw is None and isinstance(block, dict):
        raw = block.get("type")
    return str(raw or "")


def _extract_text_from_content(content: list[Any]) -> str:
    chunks: list[str] = []
    for block in content:
        block_type = _extract_block_type(block)
        if block_type == "text":
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks).strip()


def _normalize_input_schema(raw_schema: Any) -> dict[str, Any]:
    if isinstance(raw_schema, dict):
        return raw_schema
    if hasattr(raw_schema, "model_dump"):
        return raw_schema.model_dump()  # type: ignore[no-any-return]
    return {"type": "object", "properties": {}}


def _serialize_response_content(content: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for block in content:
        block_type = _extract_block_type(block)
        if block_type == "text":
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            serialized.append({"type": "text", "text": str(text or "")})
        elif block_type == "tool_use":
            serialized.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else None),
                    "name": getattr(block, "name", None)
                    or (block.get("name") if isinstance(block, dict) else None),
                    "input": getattr(block, "input", None)
                    or (block.get("input") if isinstance(block, dict) else {}),
                }
            )
    return serialized


def _tool_uses_from_response(content: list[Any]) -> list[dict[str, Any]]:
    tool_uses: list[dict[str, Any]] = []
    for block in content:
        if _extract_block_type(block) != "tool_use":
            continue
        tool_uses.append(
            {
                "id": getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else None),
                "name": getattr(block, "name", None)
                or (block.get("name") if isinstance(block, dict) else None),
                "input": getattr(block, "input", None)
                or (block.get("input") if isinstance(block, dict) else {}),
            }
        )
    return tool_uses


def _to_anthropic_messages(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in conversation_history or []:
        role = str(item.get("role", "")).strip().lower()
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if isinstance(content, list):
            messages.append({"role": role, "content": content})
        else:
            messages.append({"role": role, "content": str(content or "")})

    messages.append({"role": "user", "content": user_message})
    return messages


def _mcp_server_path() -> Path:
    return Path(__file__).resolve().parents[1] / "mcp_server" / "main.py"


def _extract_table_hint(user_message: str) -> str | None:
    """Extract a table hint from plain language prompts."""
    fqn_match = re.search(r"`([^`]+\.[^`]+)`|\b([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+){2,})\b", user_message)
    if fqn_match:
        return (fqn_match.group(1) or fqn_match.group(2) or "").strip() or None

    simple_match = re.search(r"\b([a-zA-Z0-9_]+)\s+table\b", user_message, flags=re.IGNORECASE)
    if simple_match:
        return simple_match.group(1).strip()
    return None


def _guess_change_description(user_message: str) -> str:
    """Generate a concise impact-analysis change description from user input."""
    cleaned = " ".join(user_message.strip().split())
    return cleaned[:180] if cleaned else "User-requested schema or data contract change."


async def _call_tool_text(session: ClientSession, tool_name: str, tool_input: dict[str, Any]) -> str:
    """Call a tool and normalize response content as plain text."""
    try:
        result = await session.call_tool(tool_name, tool_input)
        raw_content = getattr(result, "content", result)
        if isinstance(raw_content, list):
            parts: list[str] = []
            for item in raw_content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item.get("text")))
                elif hasattr(item, "text"):
                    parts.append(str(getattr(item, "text")))
                else:
                    parts.append(json.dumps(item, default=str))
            return "\n".join(parts).strip() or "Tool returned no output."
        return str(raw_content).strip() or "Tool returned no output."
    except Exception as exc:  # noqa: BLE001
        return f"Tool '{tool_name}' failed: {exc}"


def _first_asset_fqn(search_output: str) -> str | None:
    """Extract the first discovered asset FQN from search_assets text output."""
    match = re.search(r"\n\d+\.\s+([^|\n]+?)\s*\|", f"\n{search_output}")
    if match:
        return match.group(1).strip()
    return None


async def _run_fallback_agent(user_message: str, session: ClientSession) -> str:
    """Route common prompts directly to MCP tools when LLM is unavailable."""
    text = user_message.strip()
    lower = text.lower()
    table_hint = _extract_table_hint(text)

    # Prefer shared spreadsheet answers for common operational FAQs.
    excel_answer = await _call_tool_text(session, "answer_from_excel", {"question": text, "min_score": 0.45})
    if excel_answer and not excel_answer.startswith("NO_MATCH:") and "Tool 'answer_from_excel' failed" not in excel_answer:
        return excel_answer

    if "untagged" in lower and "pii" in lower:
        result = await _call_tool_text(session, "find_untagged_pii_columns", {})
        return (
            f"{result}\n\n"
            "Running in fallback mode (no paid LLM required). "
            "Once an LLM key is available, richer reasoning will be enabled automatically."
        )

    if any(term in lower for term in ["impact", "break", "deprecat", "downstream"]):
        if not table_hint:
            return (
                "I need a table name for impact analysis. "
                "Example: 'What breaks if I modify prod.ecommerce.customers?'"
            )
        impact = await _call_tool_text(
            session,
            "run_impact_analysis",
            {"table_fqn": table_hint, "change_description": _guess_change_description(text)},
        )
        lineage = await _call_tool_text(session, "get_downstream_lineage", {"table_fqn": table_hint, "depth": 3})
        return f"{impact}\n\n{lineage}"

    if "governance" in lower and "report" in lower:
        return await _call_tool_text(session, "get_governance_report", {})

    if any(term in lower for term in ["owner", "owns"]):
        if table_hint:
            details = await _call_tool_text(session, "get_table_details", {"table_fqn": table_hint})
            if "couldn't find table" not in details.lower():
                return details
        query = table_hint or text
        search_output = await _call_tool_text(
            session,
            "search_assets",
            {"query": query, "entity_type": "table", "limit": 5},
        )
        first_fqn = _first_asset_fqn(search_output)
        if first_fqn:
            return await _call_tool_text(session, "get_table_details", {"table_fqn": first_fqn})
        return search_output

    search_output = await _call_tool_text(
        session,
        "search_assets",
        {"query": text or "*", "entity_type": "all", "limit": 8},
    )
    return (
        f"{search_output}\n\n"
        "Running in fallback mode (tool-routed). "
        "For full natural-language reasoning, configure a funded LLM key later."
    )


async def _list_tools(session: ClientSession) -> list[dict[str, Any]]:
    tools_resp = await session.list_tools()
    raw_tools = getattr(tools_resp, "tools", tools_resp)

    tools: list[dict[str, Any]] = []
    for tool in raw_tools:
        name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
        description = getattr(tool, "description", None) or (
            tool.get("description") if isinstance(tool, dict) else ""
        )
        schema = getattr(tool, "inputSchema", None) or (
            tool.get("inputSchema") if isinstance(tool, dict) else None
        )
        if not name:
            continue
        tools.append(
            {
                "name": name,
                "description": description or "",
                "input_schema": _normalize_input_schema(schema),
            }
        )
    return tools


async def list_available_tools() -> list[dict[str, Any]]:
    """Return MCP tools currently available from the server."""
    server_path = _mcp_server_path()
    server_params = StdioServerParameters(command=sys.executable, args=[str(server_path)])

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await _list_tools(session)


async def run_agent(user_message: str, conversation_history: list | None = None) -> str:
    """Run the DataSheriff agent loop against Claude + MCP tools."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    fallback_mode = os.getenv("DATASHERIFF_FALLBACK_MODE", "").strip().lower() in {"1", "true", "yes"}
    client = AsyncAnthropic(api_key=api_key) if api_key and not fallback_mode else None
    messages = _to_anthropic_messages(user_message, conversation_history)

    server_params = StdioServerParameters(command=sys.executable, args=[str(_mcp_server_path())])

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            if client is None:
                return await _run_fallback_agent(user_message, session)

            anthropic_tools = await _list_tools(session)

            for _ in range(MAX_TOOL_ROUNDS):
                try:
                    response = await client.messages.create(
                        model=MODEL_NAME,
                        max_tokens=1400,
                        system=SYSTEM_PROMPT,
                        messages=cast(Any, messages),
                        tools=cast(Any, anthropic_tools),
                    )
                except Exception as exc:  # noqa: BLE001
                    # Fall back to deterministic tool routing when LLM access is unavailable.
                    fallback_response = await _run_fallback_agent(user_message, session)
                    return f"{fallback_response}\n\nLLM provider error: {exc}"

                content = list(response.content)
                tool_uses = _tool_uses_from_response(content)

                if not tool_uses:
                    text = _extract_text_from_content(content)
                    return text or "I could not generate a response from the model."

                messages.append({"role": "assistant", "content": _serialize_response_content(content)})

                tool_results: list[dict[str, Any]] = []
                for tool_use in tool_uses:
                    tool_name = str(tool_use.get("name") or "")
                    tool_input = tool_use.get("input") or {}
                    tool_use_id = str(tool_use.get("id") or "")

                    try:
                        result = await session.call_tool(tool_name, tool_input)
                        raw_content = getattr(result, "content", result)
                        if isinstance(raw_content, list):
                            result_text_parts: list[str] = []
                            for item in raw_content:
                                if isinstance(item, dict) and item.get("text"):
                                    result_text_parts.append(str(item.get("text")))
                                elif hasattr(item, "text"):
                                    result_text_parts.append(str(getattr(item, "text")))
                                else:
                                    result_text_parts.append(json.dumps(item, default=str))
                            result_text = "\n".join(result_text_parts)
                        else:
                            result_text = str(raw_content)

                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": result_text or "Tool returned no output.",
                                "is_error": False,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": f"Tool '{tool_name}' failed: {exc}",
                                "is_error": True,
                            }
                        )

                messages.append({"role": "user", "content": tool_results})

    return (
        "I could not complete the request within the tool-call limit. "
        "Please narrow your question and try again."
    )
