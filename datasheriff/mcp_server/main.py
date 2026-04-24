"""DataSheriff MCP server entrypoint."""

from mcp.server.fastmcp import FastMCP

from tools.discovery import register_discovery_tools
from tools.governance import register_governance_tools
from tools.lineage import register_lineage_tools
from tools.observability import register_observability_tools

mcp = FastMCP("datasheriff")

register_discovery_tools(mcp)
register_lineage_tools(mcp)
register_governance_tools(mcp)
register_observability_tools(mcp)


if __name__ == "__main__":
    mcp.run()
