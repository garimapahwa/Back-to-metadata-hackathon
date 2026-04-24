"""Observability tools for data quality, pipeline status, and freshness."""

from __future__ import annotations

from datetime import datetime, timezone
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


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # OpenMetadata often emits ms epoch timestamps.
        seconds = float(value) / 1000 if value > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def register_observability_tools(mcp: FastMCP) -> None:
    """Register observability tools with the MCP server instance."""

    @mcp.tool()
    def get_data_quality(table_fqn: str) -> str:
        """
        Get data quality test results for a table including:
        - Overall quality score (0-100)
        - Individual test results (null checks, uniqueness, range checks, etc.)
        - Recent test run history and trend
        - Failed tests with details
        """
        client = _client()
        try:
            table = client.get_table(table_fqn)
            suites_payload = client.get_test_suites(table_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{table_fqn}'."
            return f"Failed to fetch data quality metrics: {exc}"

        profile = table.get("profile") or {}
        score = profile.get("qualityScore", "n/a")
        suites = suites_payload.get("tableFiltered") or suites_payload.get("data") or []

        lines = [f"Data quality for {table_fqn}", f"Overall quality score: {score}"]
        if not suites:
            lines.append("No test suites found for this table.")
            return "\n".join(lines)

        failed_count = 0
        for suite in suites[:30]:
            suite_name = suite.get("fullyQualifiedName") or suite.get("name") or "unnamed_suite"
            summary = suite.get("summary") or {}
            passed = summary.get("success", summary.get("passed", "n/a"))
            failed = summary.get("failed", summary.get("failures", 0))
            failed_count += failed if isinstance(failed, int) else 0
            lines.append(f"- {suite_name}: passed={passed}, failed={failed}")

        lines.append(f"Total failed tests (reported): {failed_count}")
        return "\n".join(lines)

    @mcp.tool()
    def get_pipeline_status(pipeline_fqn: str) -> str:
        """
        Get the execution status of a data pipeline including:
        - Last run status (success/failed/running)
        - Last run timestamp and duration
        - Recent run history
        - Any error messages from failed runs
        """
        try:
            pipeline = _client().get_pipeline(pipeline_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find pipeline '{pipeline_fqn}'."
            return f"Failed to get pipeline status: {exc}"

        status = pipeline.get("pipelineStatus") or {}
        executions = status.get("taskStatus") or status.get("executions") or []
        latest = executions[0] if executions else {}

        lines = [
            f"Pipeline status for {pipeline_fqn}",
            f"Last run status: {latest.get('executionStatus', latest.get('status', 'n/a'))}",
            f"Last run timestamp: {latest.get('timestamp', latest.get('startTime', 'n/a'))}",
            f"Duration: {latest.get('duration', 'n/a')}",
        ]

        if latest.get("reason"):
            lines.append(f"Error: {latest.get('reason')}")

        if executions:
            lines.append("Recent runs:")
            for item in executions[:10]:
                lines.append(
                    "- "
                    f"status={item.get('executionStatus', item.get('status', 'n/a'))}, "
                    f"ts={item.get('timestamp', item.get('startTime', 'n/a'))}, "
                    f"duration={item.get('duration', 'n/a')}"
                )

        return "\n".join(lines)

    @mcp.tool()
    def get_table_freshness(table_fqn: str) -> str:
        """
        Check how fresh/stale a table's data is.
        Returns last updated timestamp, update frequency, and whether
        the table is overdue for an update based on its expected freshness SLA.
        """
        try:
            table = _client().get_table(table_fqn)
        except OpenMetadataClientError as exc:
            if "status=404" in str(exc):
                return f"I couldn't find table '{table_fqn}'."
            return f"Failed to check freshness: {exc}"

        updated_at = _parse_ts(table.get("updatedAt"))
        now = datetime.now(tz=timezone.utc)
        if not updated_at:
            return f"Freshness unavailable for '{table_fqn}': missing updatedAt metadata."

        age_hours = (now - updated_at).total_seconds() / 3600
        extension = table.get("extension") or {}
        expected_frequency_hours = extension.get("freshnessSlaHours", 24)
        is_stale = age_hours > float(expected_frequency_hours)

        return "\n".join(
            [
                f"Freshness for {table_fqn}",
                f"Last updated: {updated_at.isoformat()}",
                f"Data age: {age_hours:.1f}h",
                f"Expected update frequency: every {expected_frequency_hours}h",
                f"Status: {'STALE' if is_stale else 'FRESH'}",
            ]
        )

    @mcp.tool()
    def get_observability_summary(schema_fqn: str | None = None) -> str:
        """
        Get a system-wide observability overview:
        - Tables with failing quality tests
        - Stale tables (not updated within expected window)
        - Pipelines that failed recently
        - Overall data health score
        """
        client = _client()
        try:
            tables = client.list_tables(limit=300)
            pipelines = client.search(query="*", entity_type="pipeline", limit=50)
        except OpenMetadataClientError as exc:
            return f"Failed to generate observability summary: {exc}"

        scoped_tables = [
            table
            for table in tables
            if not schema_fqn or schema_fqn.lower() in str(table.get("fullyQualifiedName", "")).lower()
        ]

        stale_tables: list[str] = []
        low_quality_tables: list[str] = []

        now = datetime.now(tz=timezone.utc)
        for table in scoped_tables:
            fqn = table.get("fullyQualifiedName") or table.get("name") or "unknown"
            updated_at = _parse_ts(table.get("updatedAt"))
            if updated_at and (now - updated_at).total_seconds() > 24 * 3600:
                stale_tables.append(fqn)
            quality = (table.get("profile") or {}).get("qualityScore")
            if isinstance(quality, (int, float)) and quality < 80:
                low_quality_tables.append(f"{fqn} ({quality})")

        failed_pipelines: list[str] = []
        for pipeline in pipelines:
            status = str(pipeline.get("pipelineStatus") or pipeline.get("status") or "").lower()
            if "fail" in status:
                failed_pipelines.append(pipeline.get("fullyQualifiedName") or pipeline.get("name") or "unknown")

        total_assets = max(1, len(scoped_tables) + len(pipelines))
        penalty = len(stale_tables) + len(low_quality_tables) + len(failed_pipelines)
        score = max(0, int((1 - (penalty / total_assets)) * 100))

        lines = [
            "Observability Summary",
            f"Scope: {schema_fqn or 'all schemas'}",
            f"Overall data health score: {score}/100",
            f"Tables with failing/low quality: {len(low_quality_tables)}",
            f"Stale tables: {len(stale_tables)}",
            f"Recently failed pipelines: {len(failed_pipelines)}",
        ]

        if low_quality_tables:
            lines.append("Low quality tables:")
            lines.extend([f"- {item}" for item in low_quality_tables[:50]])
        if stale_tables:
            lines.append("Stale tables:")
            lines.extend([f"- {item}" for item in stale_tables[:50]])
        if failed_pipelines:
            lines.append("Failed pipelines:")
            lines.extend([f"- {item}" for item in failed_pipelines[:50]])

        return "\n".join(lines)
