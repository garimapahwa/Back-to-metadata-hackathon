"""Governance and metadata stewardship tools for DataSheriff."""

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


PII_PATTERNS = [
    r"email",
    r"phone",
    r"mobile",
    r"ssn",
    r"social_security",
    r"address",
    r"passport",
    r"dob",
    r"birth",
    r"tax",
    r"national_id",
]


def _client() -> OpenMetadataClient:
    settings = get_settings()
    return OpenMetadataClient(settings.openmetadata_host, settings.openmetadata_jwt_token)


def _is_pii_tag(tag_value: str) -> bool:
    lowered = tag_value.lower()
    return "pii" in lowered or "personal" in lowered or "sensitive" in lowered


def register_governance_tools(mcp: FastMCP) -> None:
    """Register governance tools with the MCP server instance."""

    @mcp.tool()
    def get_tags(table_fqn: str) -> str:
        """
        Get all tags and classifications applied to a table and its columns.
        Shows governance status including PII classification, data sensitivity level,
        and any custom tags.
        """
        try:
            table = _client().get_table(table_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{table_fqn}'."
            return f"Failed to read tags: {exc}"

        table_tags = [
            tag.get("tagFQN") or tag.get("tag", {}).get("tagFQN")
            for tag in (table.get("tags") or [])
        ]
        table_tags = [tag for tag in table_tags if tag]

        lines = [f"Tags for {table_fqn}", f"Table tags: {', '.join(table_tags) if table_tags else 'none'}"]
        for col in table.get("columns") or []:
            col_tags = [
                tag.get("tagFQN") or tag.get("tag", {}).get("tagFQN")
                for tag in (col.get("tags") or [])
            ]
            col_tags = [tag for tag in col_tags if tag]
            if col_tags:
                lines.append(f"- {col.get('name')}: {', '.join(col_tags)}")

        return "\n".join(lines)

    @mcp.tool()
    def apply_tag(table_fqn: str, tag_fqn: str, column_name: str | None = None) -> str:
        """
        Apply a tag to a table or a specific column within a table.
        tag_fqn examples: "PII.Sensitive", "Tier.Tier1", "PersonalData.Email"
        If column_name is provided, applies to that column only.
        """
        client = _client()
        try:
            table = client.get_table(table_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{table_fqn}'."
            return f"Failed to load table for tagging: {exc}"

        table_id = table.get("id")
        if not table_id:
            return f"Unable to tag '{table_fqn}' because its ID was missing."

        tag_payload = {"tagFQN": tag_fqn}
        patch_ops: list[dict[str, Any]] = []

        if column_name:
            columns = table.get("columns") or []
            target_index = next(
                (idx for idx, col in enumerate(columns) if col.get("name", "").lower() == column_name.lower()),
                None,
            )
            if target_index is None:
                return f"Column '{column_name}' was not found in '{table_fqn}'."

            existing_tags = (columns[target_index] or {}).get("tags")
            if existing_tags:
                patch_ops.append({"op": "add", "path": f"/columns/{target_index}/tags/-", "value": tag_payload})
            else:
                patch_ops.append({"op": "add", "path": f"/columns/{target_index}/tags", "value": [tag_payload]})
        else:
            existing_tags = table.get("tags")
            if existing_tags:
                patch_ops.append({"op": "add", "path": "/tags/-", "value": tag_payload})
            else:
                patch_ops.append({"op": "add", "path": "/tags", "value": [tag_payload]})

        try:
            client.update_table(table_id, patch_ops)
        except OpenMetadataClientError as exc:
            return f"Failed to apply tag '{tag_fqn}' on '{table_fqn}': {exc}"

        if column_name:
            return f"Applied tag '{tag_fqn}' to column '{column_name}' in '{table_fqn}'."
        return f"Applied tag '{tag_fqn}' to table '{table_fqn}'."

    @mcp.tool()
    def find_untagged_pii_columns(schema_fqn: str | None = None) -> str:
        """
        Scan tables to find columns that likely contain PII based on column name patterns
        (email, phone, ssn, address, dob, passport, etc.) but are NOT tagged as PII.
        Returns a prioritized list with table owner information.
        schema_fqn optional - scans all if not provided.
        """
        try:
            tables = _client().list_tables(limit=300)
        except OpenMetadataClientError as exc:
            return f"Failed to scan for untagged PII: {exc}"

        findings: list[tuple[str, str, str]] = []
        for table in tables:
            fqn = table.get("fullyQualifiedName") or ""
            if schema_fqn and schema_fqn.lower() not in fqn.lower():
                continue
            owner = (table.get("owner") or {}).get("name") or (table.get("owner") or {}).get("displayName") or "unassigned"
            for col in table.get("columns") or []:
                col_name = str(col.get("name", ""))
                if not col_name:
                    continue
                if not any(re.search(pattern, col_name, re.IGNORECASE) for pattern in PII_PATTERNS):
                    continue
                tags = [
                    tag.get("tagFQN") or tag.get("tag", {}).get("tagFQN")
                    for tag in (col.get("tags") or [])
                ]
                tags = [tag for tag in tags if tag]
                if any(_is_pii_tag(tag) for tag in tags):
                    continue
                findings.append((fqn, col_name, owner))

        if not findings:
            scope = schema_fqn or "all schemas"
            return f"No untagged likely-PII columns found in {scope}."

        lines = [f"Found {len(findings)} likely untagged PII columns:"]
        for fqn, col_name, owner in findings[:200]:
            lines.append(f"- {fqn}.{col_name} | owner=@{owner}")
        return "\n".join(lines)

    @mcp.tool()
    def assign_owner(table_fqn: str, owner_name: str, owner_type: str = "user") -> str:
        """
        Assign or update the owner of a data asset.
        owner_type: "user" or "team"
        """
        if owner_type not in {"user", "team"}:
            return "owner_type must be 'user' or 'team'."

        client = _client()
        try:
            table = client.get_table(table_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{table_fqn}'."
            return f"Failed to load table: {exc}"

        table_id = table.get("id")
        if not table_id:
            return "Cannot assign owner because table ID is missing."

        owner_ref = {"type": owner_type, "name": owner_name}
        patch_ops = [{"op": "add", "path": "/owner", "value": owner_ref}]

        try:
            client.update_table(table_id, patch_ops)
        except OpenMetadataClientError as exc:
            return f"Failed to assign owner: {exc}"

        return f"Assigned @{owner_name} as {owner_type} owner for '{table_fqn}'."

    @mcp.tool()
    def get_governance_report(schema_fqn: str | None = None) -> str:
        """
        Generate a governance health report showing:
        - % of assets with owners assigned
        - % of assets with descriptions
        - % of sensitive columns tagged as PII
        - Assets missing critical metadata
        Great for compliance audits and team health checks.
        """
        try:
            tables = _client().list_tables(limit=300)
        except OpenMetadataClientError as exc:
            return f"Failed to generate governance report: {exc}"

        scoped_tables = [
            table
            for table in tables
            if not schema_fqn or schema_fqn.lower() in str(table.get("fullyQualifiedName", "")).lower()
        ]
        if not scoped_tables:
            return "No tables found for governance report scope."

        owner_count = 0
        desc_count = 0
        sensitive_total = 0
        sensitive_tagged = 0
        missing: list[str] = []

        for table in scoped_tables:
            fqn = table.get("fullyQualifiedName") or table.get("name") or "unknown"
            if table.get("owner"):
                owner_count += 1
            else:
                missing.append(f"{fqn} (missing owner)")

            if (table.get("description") or "").strip():
                desc_count += 1
            else:
                missing.append(f"{fqn} (missing description)")

            for col in table.get("columns") or []:
                col_name = str(col.get("name", ""))
                if any(re.search(pattern, col_name, re.IGNORECASE) for pattern in PII_PATTERNS):
                    sensitive_total += 1
                    tags = [
                        tag.get("tagFQN") or tag.get("tag", {}).get("tagFQN")
                        for tag in (col.get("tags") or [])
                    ]
                    tags = [tag for tag in tags if tag]
                    if any(_is_pii_tag(tag) for tag in tags):
                        sensitive_tagged += 1
                    else:
                        missing.append(f"{fqn}.{col_name} (likely PII but untagged)")

        total = len(scoped_tables)
        owner_pct = (owner_count / total) * 100
        desc_pct = (desc_count / total) * 100
        pii_pct = (sensitive_tagged / sensitive_total) * 100 if sensitive_total else 100.0

        lines = [
            "Governance Health Report",
            f"Scope: {schema_fqn or 'all schemas'}",
            f"Assets with owners: {owner_count}/{total} ({owner_pct:.1f}%)",
            f"Assets with descriptions: {desc_count}/{total} ({desc_pct:.1f}%)",
            (
                f"Sensitive columns tagged as PII: {sensitive_tagged}/{sensitive_total} "
                f"({pii_pct:.1f}%)"
            ),
            "Assets missing critical metadata:",
        ]
        lines.extend([f"- {item}" for item in missing[:200]] or ["- none"])
        return "\n".join(lines)
