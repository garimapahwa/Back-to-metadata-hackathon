"""Seed demo entities and lineage in OpenMetadata for hackathon demos."""

from __future__ import annotations

import json
from typing import Any

from dotenv import load_dotenv

from mcp_server.config import get_settings
from mcp_server.openmetadata import OpenMetadataClient, OpenMetadataClientError

load_dotenv()


def _ensure_table(client: OpenMetadataClient, table_payload: dict[str, Any]) -> dict[str, Any]:
    fqn = table_payload.get("fullyQualifiedName") or table_payload.get("name")
    if not fqn:
        raise ValueError("Table payload missing fullyQualifiedName or name")

    try:
        return client.get_table(fqn)
    except OpenMetadataClientError as exc:
        if "status=404" not in str(exc):
            raise

    try:
        created = client.post("tables", table_payload)
        return created
    except OpenMetadataClientError as exc:
        # Some OpenMetadata installations require richer create payloads than the demo uses.
        # Fall back to a graceful no-op so the script can still seed lineage-compatible assets.
        print(f"[warn] Could not create table {fqn}: {exc}")
        return table_payload


def _safe_create_lineage(
    client: OpenMetadataClient,
    from_fqn: str,
    to_fqn: str,
    from_type: str = "table",
    to_type: str = "table",
) -> None:
    payload = {
        "edge": {
            "fromEntity": {"type": from_type, "fullyQualifiedName": from_fqn},
            "toEntity": {"type": to_type, "fullyQualifiedName": to_fqn},
        }
    }
    try:
        client.post("lineage", payload)
    except OpenMetadataClientError as exc:
        # If it already exists or is unsupported in this environment, continue.
        print(f"[warn] Could not create lineage {from_fqn} -> {to_fqn}: {exc}")
        return


def _ensure_dashboard_or_pipeline(client: OpenMetadataClient) -> tuple[str, str]:
    """Create a dashboard demo asset when possible; fallback to pipeline otherwise."""
    dashboard_fqn = "analytics.reporting.revenue_dashboard"
    dashboard_payload = {
        "name": "revenue_dashboard",
        "fullyQualifiedName": dashboard_fqn,
        "description": "Executive revenue dashboard built from ecommerce order metrics.",
    }

    # Try dashboard APIs first because this is the intended downstream entity for the demo.
    try:
        client.get(f"dashboards/name/{dashboard_fqn}")
        return dashboard_fqn, "dashboard"
    except OpenMetadataClientError as exc:
        if "status=404" not in str(exc):
            # Some OpenMetadata instances may not expose dashboard APIs.
            pass

    try:
        client.post("dashboards", dashboard_payload)
        return dashboard_fqn, "dashboard"
    except OpenMetadataClientError:
        pass

    try:
        client.get_pipeline(dashboard_fqn)
    except OpenMetadataClientError as exc:
        if "status=404" in str(exc):
            try:
                client.post("pipelines", dashboard_payload)
            except OpenMetadataClientError:
                pass

    return dashboard_fqn, "pipeline"


def seed() -> None:
    """Create sample assets and lineage for DataSheriff demos."""
    settings = get_settings()
    if not settings.has_openmetadata_auth:
        raise RuntimeError("OPENMETADATA_HOST and OPENMETADATA_JWT_TOKEN must be configured.")

    client = OpenMetadataClient(settings.openmetadata_host, settings.openmetadata_jwt_token)

    sample_tables = [
        {
            "name": "orders",
            "fullyQualifiedName": "prod.ecommerce.orders",
            "description": "Transactional order facts including status and payment lifecycle.",
            "columns": [
                {"name": "order_id", "dataType": "BIGINT", "description": "Primary order identifier."},
                {"name": "customer_id", "dataType": "BIGINT", "description": "Foreign key to customer."},
                {"name": "order_total", "dataType": "DECIMAL", "description": "Order gross value."},
                {"name": "order_ts", "dataType": "TIMESTAMP", "description": "Order creation timestamp."},
            ],
            "owner": {"type": "team", "name": "data-engineering"},
        },
        {
            "name": "customers",
            "fullyQualifiedName": "prod.ecommerce.customers",
            "description": "Customer profile and lifecycle attributes.",
            "columns": [
                {"name": "customer_id", "dataType": "BIGINT", "description": "Primary customer identifier."},
                {"name": "email", "dataType": "STRING", "description": "Customer email address."},
                {"name": "full_name", "dataType": "STRING", "description": "Customer full legal name."},
                {"name": "created_at", "dataType": "TIMESTAMP", "description": "Sign-up timestamp."},
            ],
            "owner": {"type": "team", "name": "data-engineering"},
        },
        {
            "name": "order_items",
            "fullyQualifiedName": "prod.ecommerce.order_items",
            "description": "Line-item grain table for products within each order.",
            "columns": [
                {"name": "order_id", "dataType": "BIGINT", "description": "Parent order identifier."},
                {"name": "sku", "dataType": "STRING", "description": "Product SKU."},
                {"name": "quantity", "dataType": "INT", "description": "Units purchased."},
                {"name": "item_price", "dataType": "DECIMAL", "description": "Item unit price."},
            ],
            "owner": {"type": "team", "name": "data-engineering"},
        },
    ]

    created_tables = [_ensure_table(client, payload) for payload in sample_tables]

    dashboard_fqn, dashboard_type = _ensure_dashboard_or_pipeline(client)

    _safe_create_lineage(client, "prod.ecommerce.customers", "prod.ecommerce.orders")
    _safe_create_lineage(client, "prod.ecommerce.orders", "prod.ecommerce.order_items")
    _safe_create_lineage(
        client,
        "prod.ecommerce.orders",
        dashboard_fqn,
        from_type="table",
        to_type=dashboard_type,
    )

    print(
        json.dumps(
            {
                "created_or_existing_tables": [
                    table.get("fullyQualifiedName") or table.get("name") for table in created_tables
                ],
                "lineage_seeded": [
                    "prod.ecommerce.customers -> prod.ecommerce.orders",
                    "prod.ecommerce.orders -> prod.ecommerce.order_items",
                    f"prod.ecommerce.orders -> {dashboard_fqn} ({dashboard_type})",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    seed()
