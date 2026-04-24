"""OpenMetadata client package."""

try:
	from mcp_server.openmetadata.client import OpenMetadataClient, OpenMetadataClientError
except ModuleNotFoundError:
	from openmetadata.client import OpenMetadataClient, OpenMetadataClientError

__all__ = ["OpenMetadataClient", "OpenMetadataClientError"]
