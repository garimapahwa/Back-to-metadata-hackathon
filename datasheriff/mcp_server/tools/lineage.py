"""Lineage and impact-analysis tools for DataSheriff."""

from __future__ import annotations

from collections import defaultdict
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


def _extract_nodes(lineage: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for node in lineage.get("nodes", []):
        node_id = str(node.get("id") or node.get("fullyQualifiedName") or node.get("name"))
        nodes[node_id] = node
    root = lineage.get("entity") or {}
    if root:
        root_id = str(root.get("id") or root.get("fullyQualifiedName") or root.get("name"))
        nodes[root_id] = root
    return nodes


def _format_lineage_tree(lineage: dict[str, Any], direction: str) -> str:
    nodes = _extract_nodes(lineage)
    edges = lineage.get("upstreamEdges" if direction == "upstream" else "downstreamEdges", [])
    if not edges and not nodes:
        return "No lineage found."

    adjacency: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)

    for edge in edges:
        from_id = str(edge.get("fromEntity", {}).get("id") or edge.get("fromEntity"))
        to_id = str(edge.get("toEntity", {}).get("id") or edge.get("toEntity"))
        if direction == "upstream":
            adjacency[to_id].append(from_id)
            incoming[from_id] += 1
        else:
            adjacency[from_id].append(to_id)
            incoming[to_id] += 1

    roots = [node_id for node_id in nodes.keys() if incoming.get(node_id, 0) == 0]
    if not roots:
        roots = list(nodes.keys())[:1]

    def node_label(node_id: str) -> str:
        node = nodes.get(node_id, {})
        name = node.get("fullyQualifiedName") or node.get("name") or node_id
        entity_type = node.get("type") or node.get("entityType") or "asset"
        return f"{name} ({entity_type})"

    visited: set[str] = set()
    lines: list[str] = []

    def walk(current: str, depth: int) -> None:
        indent = "  " * depth
        lines.append(f"{indent}- {node_label(current)}")
        if current in visited:
            lines.append(f"{indent}  - ...")
            return
        visited.add(current)
        for nxt in adjacency.get(current, []):
            walk(nxt, depth + 1)

    for root in roots:
        walk(root, 0)

    return "\n".join(lines)


def register_lineage_tools(mcp: FastMCP) -> None:
    """Register lineage tools with the MCP server instance."""

    @mcp.tool()
    def get_upstream_lineage(table_fqn: str, depth: int = 2) -> str:
        """
        Get all upstream dependencies of a table - what data sources and
        pipelines feed INTO this table. depth controls how many hops to trace.
        Returns a formatted lineage tree.
        """
        try:
            lineage = _client().get_lineage("table", table_fqn, direction="upstream", depth=depth)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find lineage for '{table_fqn}'."
            return f"Failed to load upstream lineage: {exc}"

        tree = _format_lineage_tree(lineage, direction="upstream")
        return f"Upstream lineage for {table_fqn}:\n{tree}"

    @mcp.tool()
    def get_downstream_lineage(table_fqn: str, depth: int = 2) -> str:
        """
        Get all downstream consumers of a table - what dashboards, pipelines,
        and other tables depend on this table.
        Critical for impact analysis before making changes.
        """
        try:
            lineage = _client().get_lineage("table", table_fqn, direction="downstream", depth=depth)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find lineage for '{table_fqn}'."
            return f"Failed to load downstream lineage: {exc}"

        tree = _format_lineage_tree(lineage, direction="downstream")
        return f"Downstream lineage for {table_fqn}:\n{tree}"

    @mcp.tool()
    def run_impact_analysis(table_fqn: str, change_description: str) -> str:
        """
        Analyze the full impact of a proposed change to a table or column.
        Traces complete downstream lineage and groups affected assets by type
        (dashboards, pipelines, tables). Returns owners of each affected asset.
        Use this when a user says "what breaks if I change X" or "I want to deprecate Y".
        """
        try:
            lineage = _client().get_lineage("table", table_fqn, direction="downstream", depth=4)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{table_fqn}' for impact analysis."
            return f"Failed to run impact analysis: {exc}"

        affected: dict[str, list[str]] = defaultdict(list)
        for node in lineage.get("nodes", []):
            fqn = node.get("fullyQualifiedName") or node.get("name") or "unknown"
            if fqn == table_fqn:
                continue
            entity_type = node.get("type") or node.get("entityType") or "unknown"
            owner = node.get("owner", {}).get("name") or node.get("owner", {}).get("displayName") or "unassigned"
            affected[entity_type].append(f"{fqn} (owner=@{owner})")

        if not affected:
            return (
                f"Impact analysis for '{table_fqn}': no downstream assets found. "
                f"Proposed change: {change_description}"
            )

        lines = [f"Impact analysis for {table_fqn}", f"Proposed change: {change_description}"]
        total = 0
        for entity_type, items in sorted(affected.items(), key=lambda item: item[0]):
            total += len(items)
            lines.append(f"- {entity_type}: {len(items)} affected")
            for item in items[:25]:
                lines.append(f"  - {item}")
        lines.insert(2, f"Total impacted assets: {total}")
        return "\n".join(lines)
