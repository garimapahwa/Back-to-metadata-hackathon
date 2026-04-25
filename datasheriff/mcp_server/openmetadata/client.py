"""OpenMetadata REST client used by DataSheriff tools."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import quote

import httpx


class OpenMetadataClientError(RuntimeError):
    """Raised when OpenMetadata API requests fail."""


@dataclass
class RequestOptions:
    """HTTP request options for OpenMetadata calls."""

    timeout: float = 20.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.4


class OpenMetadataClient:
    """Thin client around OpenMetadata REST APIs."""

    def __init__(self, host: str, jwt_token: str, options: RequestOptions | None = None):
        self.base_url = host.rstrip("/") + "/api/v1"
        self.headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }
        self.options = options or RequestOptions()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request and return parsed JSON."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        merged_headers = {**self.headers, **(headers or {})}
        retries = max(0, self.options.max_retries)

        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=self.options.timeout) as client:
                    response = client.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_body,
                        headers=merged_headers,
                    )
                    if response.status_code >= 400:
                        if (
                            response.status_code in {429, 500, 502, 503, 504}
                            and attempt < retries
                        ):
                            time.sleep(self.options.retry_backoff_seconds * (2**attempt))
                            continue

                        detail = response.text
                        raise OpenMetadataClientError(
                            f"OpenMetadata {method} {endpoint} failed: "
                            f"status={response.status_code}, detail={detail}"
                        )

                    if not response.text:
                        return {}
                    return response.json()
            except httpx.TimeoutException as exc:
                if attempt < retries:
                    time.sleep(self.options.retry_backoff_seconds * (2**attempt))
                    continue
                raise OpenMetadataClientError("OpenMetadata request timed out") from exc
            except httpx.HTTPError as exc:
                if attempt < retries:
                    time.sleep(self.options.retry_backoff_seconds * (2**attempt))
                    continue
                raise OpenMetadataClientError(f"HTTP error while contacting OpenMetadata: {exc}") from exc
            except ValueError as exc:
                raise OpenMetadataClientError("Invalid JSON response from OpenMetadata") from exc

        raise OpenMetadataClientError("OpenMetadata request failed after retries")

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GET request."""
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """Execute a POST request."""
        return self._request("POST", endpoint, json_body=body)

    def patch(self, endpoint: str, body: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
        """Execute a PATCH request, including JSON Patch support."""
        patch_headers = {"Content-Type": "application/json-patch+json"}
        return self._request("PATCH", endpoint, json_body=body, headers=patch_headers)

    def search(self, query: str, entity_type: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Search OpenMetadata assets with optional entity type filtering."""
        index_by_type = {
            "table": "table_search_index",
            "dashboard": "dashboard_search_index",
            "pipeline": "pipeline_search_index",
            "topic": "topic_search_index",
        }

        if entity_type in (None, "all"):
            indexes = list(index_by_type.values())
        else:
            indexes = [index_by_type.get(entity_type, "table_search_index")]

        merged_results: list[dict[str, Any]] = []
        for index in indexes:
            payload = self.get(
                "search/query",
                params={"q": query or "*", "index": index, "size": limit},
            )
            hits = payload.get("hits", {}).get("hits", [])
            for hit in hits:
                source = hit.get("_source", {})
                source["_score"] = hit.get("_score")
                source["_index"] = hit.get("_index", index)
                merged_results.append(source)

        # Preserve order by score and cap globally.
        merged_results.sort(key=lambda item: item.get("_score", 0), reverse=True)
        return merged_results[:limit]

    def get_table(self, fqn: str) -> dict[str, Any]:
        """Get table metadata by fully qualified name."""
        encoded = quote(fqn, safe="")
        return self.get(f"tables/name/{encoded}", params={"fields": "columns,tags,followers,joins"})

    def get_table_by_id(self, table_id: str) -> dict[str, Any]:
        """Get table metadata by UUID."""
        encoded = quote(table_id, safe="")
        return self.get(f"tables/{encoded}", params={"fields": "columns,tags,followers,joins"})

    def get_lineage(
        self,
        entity_type: str,
        fqn: str,
        direction: str,
        depth: int = 2,
    ) -> dict[str, Any]:
        """Get lineage graph for an entity."""
        encoded = quote(fqn, safe="")
        return self.get(
            f"lineage/{entity_type}/name/{encoded}",
            params={"direction": direction, "depth": max(1, depth)},
        )

    def update_table(self, table_id: str, patch_ops: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply JSON patch operations to a table."""
        encoded = quote(table_id, safe="")
        return self.patch(f"tables/{encoded}", patch_ops)

    def get_test_suites(self, table_fqn: str) -> dict[str, Any]:
        """List data quality suites and include table-filtered subset when possible."""
        payload = self.get("dataQuality/testSuites", params={"testSuiteType": "logical", "limit": 100})
        suites = payload.get("data", [])
        filtered = [
            suite
            for suite in suites
            if table_fqn.lower() in str(suite.get("name", "")).lower()
            or table_fqn.lower() in str(suite.get("fullyQualifiedName", "")).lower()
            or table_fqn.lower() in str(suite.get("entityLink", "")).lower()
        ]
        payload["tableFiltered"] = filtered
        return payload

    def list_tables(
        self,
        database: str | None = None,
        schema: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List tables with optional database and schema filters."""
        payload = self.get(
            "tables",
            params={"fields": "tags,columns", "limit": min(limit, 500)},
        )
        tables = payload.get("data", [])

        if database:
            tables = [
                item
                for item in tables
                if database.lower() in str(item.get("database", {}).get("name", "")).lower()
                or database.lower() in str(item.get("fullyQualifiedName", "")).lower()
            ]

        if schema:
            tables = [
                item
                for item in tables
                if schema.lower() in str(item.get("databaseSchema", {}).get("name", "")).lower()
                or schema.lower() in str(item.get("fullyQualifiedName", "")).lower()
            ]

        return tables[:limit]

    def get_pipeline(self, pipeline_fqn: str) -> dict[str, Any]:
        """Get a pipeline by FQN with status fields."""
        encoded = quote(pipeline_fqn, safe="")
        return self.get(f"pipelines/name/{encoded}", params={"fields": "pipelineStatus"})
