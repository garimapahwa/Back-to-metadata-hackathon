"""Tools for querying internet-accessible databases by URL."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError


_ALLOWED_SCHEMES = {
    "postgresql",
    "postgresql+psycopg2",
    "mysql",
    "mysql+pymysql",
    "sqlite",
}


def _sanitize_db_url(db_url: str) -> str:
    return (db_url or "").strip()


def _url_scheme(db_url: str) -> str:
    return _sanitize_db_url(db_url).split("://", 1)[0].lower()


def _validate_db_url(db_url: str) -> str | None:
    normalized = _sanitize_db_url(db_url)
    if not normalized:
        return "db_url is required."
    if "://" not in normalized:
        return "db_url must include a scheme (for example postgresql://... or mysql://... or sqlite:///...)."

    scheme = _url_scheme(normalized)
    if scheme not in _ALLOWED_SCHEMES:
        return (
            "Unsupported database URL scheme. "
            f"Allowed: {', '.join(sorted(_ALLOWED_SCHEMES))}"
        )

    if scheme.startswith("sqlite") and "mode=ro" not in normalized and ":memory:" not in normalized:
        # For SQLite, prefer explicit read-only mode if user provides file path URL.
        # We still allow standard sqlite URLs for usability.
        return None

    return None


def _engine(db_url: str):
    normalized = _sanitize_db_url(db_url)
    # pool_pre_ping avoids stale-connections on remote instances.
    return create_engine(normalized, pool_pre_ping=True)


def _is_read_only_sql(sql: str) -> bool:
    cleaned = (sql or "").strip().lower()
    if not cleaned:
        return False

    # Block multi-statements and obvious write/admin operations.
    blocked = [
        "insert ",
        "update ",
        "delete ",
        "drop ",
        "alter ",
        "create ",
        "truncate ",
        "grant ",
        "revoke ",
        "merge ",
        "call ",
        "copy ",
    ]
    if ";" in cleaned:
        return False
    if any(token in cleaned for token in blocked):
        return False

    return cleaned.startswith("select") or cleaned.startswith("with") or cleaned.startswith("show")


def _rows_to_text(rows: Iterable[Any], max_rows: int) -> str:
    lines: list[str] = []
    for idx, row in enumerate(rows):
        if idx >= max_rows:
            lines.append(f"... truncated to {max_rows} row(s)")
            break
        if hasattr(row, "_mapping"):
            row_dict = dict(row._mapping)
            lines.append(str(row_dict))
        else:
            lines.append(str(row))
    return "\n".join(lines)


def _extract_table_name(question: str) -> str | None:
    quoted = re.search(r"`([^`]+)`", question)
    if quoted:
        return quoted.group(1).strip()
    plain = re.search(r"(?:table|from|in)\s+([a-zA-Z0-9_.]+)", question, flags=re.IGNORECASE)
    if plain:
        return plain.group(1).strip()
    return None


def register_remote_db_tools(mcp: FastMCP) -> None:
    """Register direct database URL tools."""

    @mcp.tool()
    def describe_remote_db(db_url: str, sample_tables: int = 8) -> str:
        """
        Describe tables and columns in a remote SQL database via URL.
        Supports postgresql://, mysql://, and sqlite:/// URLs.
        """
        validation_error = _validate_db_url(db_url)
        if validation_error:
            return f"Database URL validation failed: {validation_error}"

        try:
            engine = _engine(db_url)
            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            if not table_names:
                return "Connected successfully, but no tables were found."

            shown = table_names[: max(1, min(sample_tables, 50))]
            lines = [f"Connected. Found {len(table_names)} table(s). Showing {len(shown)}:"]
            for table in shown:
                cols = inspector.get_columns(table)
                col_desc = ", ".join(f"{c.get('name')}:{c.get('type')}" for c in cols[:12])
                if len(cols) > 12:
                    col_desc += ", ..."
                lines.append(f"- {table} | columns: {col_desc or 'none'}")
            return "\n".join(lines)
        except SQLAlchemyError as exc:
            return f"Failed to connect or inspect database: {exc}"

    @mcp.tool()
    def query_remote_db(db_url: str, sql: str, max_rows: int = 30) -> str:
        """
        Run a read-only SQL query against a remote database URL.
        Only SELECT/WITH/SHOW queries are allowed.
        """
        validation_error = _validate_db_url(db_url)
        if validation_error:
            return f"Database URL validation failed: {validation_error}"

        if not _is_read_only_sql(sql):
            return "Only read-only single-statement SELECT/WITH/SHOW SQL is allowed."

        row_cap = max(1, min(max_rows, 200))
        try:
            engine = _engine(db_url)
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                rows = result.fetchall()
                if not rows:
                    return "Query executed successfully. No rows returned."
                preview = _rows_to_text(rows, row_cap)
                return f"Returned {len(rows)} row(s).\n{preview}"
        except SQLAlchemyError as exc:
            return f"Query failed: {exc}"

    @mcp.tool()
    def ask_remote_db(db_url: str, question: str) -> str:
        """
        Ask a simple natural-language question about a remote DB URL.
        Best for: list tables, describe table columns, row counts.
        """
        validation_error = _validate_db_url(db_url)
        if validation_error:
            return f"Database URL validation failed: {validation_error}"

        q = (question or "").strip().lower()
        try:
            engine = _engine(db_url)
            inspector = inspect(engine)
            tables = inspector.get_table_names()

            if not tables:
                return "Connected successfully, but no tables were found."

            if "list" in q and "table" in q:
                return "Tables:\n" + "\n".join(f"- {t}" for t in tables[:100])

            table = _extract_table_name(question)
            if table and table in tables and ("column" in q or "schema" in q or "describe" in q):
                cols = inspector.get_columns(table)
                if not cols:
                    return f"No columns found for table '{table}'."
                return "\n".join(
                    [f"Columns for {table}:"] + [f"- {c.get('name')} ({c.get('type')})" for c in cols]
                )

            if table and table in tables and ("count" in q or "how many" in q or "rows" in q):
                with engine.connect() as conn:
                    count = conn.execute(text(f"SELECT COUNT(*) AS row_count FROM {table}"))
                    row = count.fetchone()
                    value = row[0] if row else "n/a"
                    return f"Row count for {table}: {value}"

            # Graceful fallback for other prompts.
            return (
                "I can answer this DB directly if you ask one of these forms:\n"
                "- list tables\n"
                "- describe table <table_name>\n"
                "- row count for <table_name>\n"
                "Or use query_remote_db with an explicit read-only SQL query."
            )
        except SQLAlchemyError as exc:
            return f"Failed to answer from database: {exc}"
