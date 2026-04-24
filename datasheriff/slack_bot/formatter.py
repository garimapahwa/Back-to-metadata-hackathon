"""Slack Block Kit formatting utilities for DataSheriff responses."""

from __future__ import annotations

import re


_FQN_PATTERN = re.compile(r"(?<!`)\b[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+){1,4}\b(?!`)")


def _markdown_to_slack(text: str) -> str:
    """Convert a subset of Markdown formatting to Slack mrkdwn."""
    converted = text
    converted = re.sub(r"\*\*(.*?)\*\*", r"*\1*", converted)
    converted = re.sub(r"`([^`]+)`", r"`\1`", converted)
    return converted


def _wrap_asset_names(text: str) -> str:
    """Wrap likely asset FQNs in inline code for better Slack readability."""

    return _FQN_PATTERN.sub(lambda match: f"`{match.group(0)}`", text)


def format_response_blocks(response: str) -> list[dict]:
    """Format DataSheriff response into Slack blocks with footer."""
    text = _wrap_asset_names(_markdown_to_slack(response.strip()))
    blocks: list[dict] = []

    lines = [line.rstrip() for line in text.splitlines()]
    if any(line.lstrip().startswith("-") or re.match(r"^\d+\.", line.lstrip()) for line in lines):
        bullet_text = "\n".join(line for line in lines if line.strip())
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": bullet_text}})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Powered by DataSheriff 🤠 | OpenMetadata",
                }
            ],
        }
    )

    return blocks
