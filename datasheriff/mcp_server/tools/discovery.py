"""Discovery tools for searching and summarizing data assets."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from mcp_server.config import get_settings
    from mcp_server.openmetadata.client import OpenMetadataClient, OpenMetadataClientError
except ModuleNotFoundError:
    from config import get_settings
    from openmetadata.client import OpenMetadataClient, OpenMetadataClientError


def _client() -> OpenMetadataClient:
    settings = get_settings()
    return OpenMetadataClient(settings.openmetadata_host, settings.openmetadata_jwt_token)


def _owner_name(item: dict[str, Any]) -> str:
    owner = item.get("owner") or {}
    return owner.get("name") or owner.get("displayName") or "unassigned"


def _asset_fqn(item: dict[str, Any]) -> str:
    return item.get("fullyQualifiedName") or item.get("name") or "unknown"


def _matches_owner(item: dict[str, Any], owner_name: str) -> bool:
    owner = item.get("owner") or {}
    owner_display = str(owner.get("displayName") or "").lower()
    owner_name_value = str(owner.get("name") or "").lower()
    team_name = str((owner.get("team") or {}).get("name") or "").lower()
    query = owner_name.lower()
    return any(
        query in candidate
        for candidate in (owner_display, owner_name_value, team_name, str(_asset_fqn(item)).lower())
    )


def _normalize_table_fqn(raw: str) -> str:
    """Extract a table FQN from free-text input when possible."""
    text = (raw or "").strip().strip("`")
    match = re.search(r"\b([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+){2,})\b", text)
    return match.group(1) if match else text


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register discovery tools with the MCP server instance."""

    @mcp.tool()
    def search_assets(query: str, entity_type: str = "table", limit: int = 10) -> str:
        """
        Search for data assets (tables, dashboards, pipelines, topics) in OpenMetadata.
        Use this when the user wants to find or discover data assets by keyword or description.
        Returns asset names, owners, descriptions, tags, and quality scores.
        entity_type options: "table", "dashboard", "pipeline", "topic", "all"
        """
        try:
            results = _client().search(query=query, entity_type=entity_type, limit=limit)
        except OpenMetadataClientError as exc:
            return f"I could not search assets right now: {exc}"

        if not results:
            return f"No assets found for query '{query}'. Try a broader keyword."

        lines = [f"Found {len(results)} asset(s) for '{query}':"]
        for idx, asset in enumerate(results, start=1):
            tags = asset.get("tags") or []
            tag_names = [t.get("tagFQN") or t.get("tag", {}).get("tagFQN") for t in tags]
            tag_names = [name for name in tag_names if name]
            description = (asset.get("description") or "").strip() or "No description"
            lines.append(
                f"{idx}. {_asset_fqn(asset)} | owner=@{_owner_name(asset)} | "
                f"tags={', '.join(tag_names[:4]) if tag_names else 'none'} | "
                f"quality={asset.get('quality', 'n/a')} | desc={description[:120]}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def get_table_details(table_fqn: str) -> str:
        """
        Get complete metadata for a specific table using its fully qualified name.
        FQN format: service_name.database_name.schema_name.table_name
        Returns: description, owner, tags, column count, row count, quality score, last updated.
        """
        normalized_fqn = _normalize_table_fqn(table_fqn)
        try:
            table = _client().get_table(normalized_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{normalized_fqn}'. Try search_assets for similar names."
            return f"Failed to fetch table details: {exc}"

        columns = table.get("columns") or []
        tags = table.get("tags") or []
        tag_names = [t.get("tagFQN") or t.get("tag", {}).get("tagFQN") for t in tags]
        tag_names = [name for name in tag_names if name]
        profile = table.get("profile") or {}

        return "\n".join(
            [
                f"Table: {table.get('fullyQualifiedName', normalized_fqn)}",
                f"Description: {(table.get('description') or 'No description').strip()}",
                f"Owner: @{_owner_name(table)}",
                f"Tags: {', '.join(tag_names) if tag_names else 'none'}",
                f"Column count: {len(columns)}",
                f"Row count: {profile.get('rowCount', 'n/a')}",
                f"Quality score: {profile.get('qualityScore', table.get('quality', 'n/a'))}",
                f"Last updated: {table.get('updatedAt', 'n/a')}",
            ]
        )

    @mcp.tool()
    def get_column_info(table_fqn: str) -> str:
        """
        Get all column-level metadata for a table including names, data types,
        descriptions, tags, and whether columns are flagged as PII.
        """
        normalized_fqn = _normalize_table_fqn(table_fqn)
        try:
            table = _client().get_table(normalized_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{normalized_fqn}'."
            return f"Failed to load column metadata: {exc}"

        columns = table.get("columns") or []
        if not columns:
            return f"No columns found for table '{normalized_fqn}'."

        lines = [f"Column metadata for {table.get('fullyQualifiedName', normalized_fqn)}:"]
        for col in columns:
            tags = col.get("tags") or []
            tag_names = [t.get("tagFQN") or t.get("tag", {}).get("tagFQN") for t in tags]
            tag_names = [name for name in tag_names if name]
            pii_flag = any("pii" in name.lower() or "personal" in name.lower() for name in tag_names)
            lines.append(
                f"- {col.get('name')} ({col.get('dataType', 'unknown')}) | "
                f"PII={'yes' if pii_flag else 'no'} | "
                f"tags={', '.join(tag_names) if tag_names else 'none'} | "
                f"desc={(col.get('description') or 'No description').strip()}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def find_assets_by_owner(owner_name: str, entity_type: str = "table") -> str:
        """
        Find all data assets owned by a specific person or team.
        owner_name can be a username or team name.
        """
        try:
            client = _client()
            search_results = client.search(query=owner_name or "*", entity_type=entity_type, limit=100)
            list_results = client.list_tables(limit=300) if entity_type in {"table", "all"} else []
        except OpenMetadataClientError as exc:
            return f"Failed to search by owner: {exc}"

        merged: dict[str, dict[str, Any]] = {}
        for item in [*search_results, *list_results]:
            merged[_asset_fqn(item)] = item

        results = [item for item in merged.values() if _matches_owner(item, owner_name)]

        if not results:
            return f"No {entity_type} assets found for owner '{owner_name}'."

        lines = [f"Assets owned by {owner_name} ({len(results)}):"]
        for item in results:
            owner = _owner_name(item)
            lines.append(f"- {_asset_fqn(item)} | owner=@{owner}")
        return "\n".join(lines)

    @mcp.tool()
    def get_asset_summary(table_fqn: str) -> str:
        """
        Get a concise human-readable summary of a table: what it contains,
        who owns it, when it was last updated, and its quality health.
        Perfect for quick checks before using a dataset.
        """
        normalized_fqn = _normalize_table_fqn(table_fqn)
        try:
            table = _client().get_table(normalized_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find '{normalized_fqn}'. Try search_assets to discover the exact FQN."
            return f"Unable to summarize table: {exc}"

        description = (table.get("description") or "No description").strip()
        owner = _owner_name(table)
        updated = table.get("updatedAt", "n/a")
        columns = table.get("columns") or []
        profile = table.get("profile") or {}
        quality = profile.get("qualityScore", table.get("quality", "n/a"))

        return (
            f"{table.get('fullyQualifiedName', normalized_fqn)} is owned by @{owner}. "
            f"It has {len(columns)} columns and quality score {quality}. "
            f"Last updated at {updated}. Summary: {description[:220]}"
        )
