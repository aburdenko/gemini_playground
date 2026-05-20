import os
from mcp.server.fastmcp import FastMCP
from app.tools import download_pubmed_papers

port = int(os.environ.get("PORT", 8080))
mcp = FastMCP("PubMed Connector", host="0.0.0.0", port=port)

# Expose the tool globally
mcp.tool()(download_pubmed_papers)

if __name__ == "__main__":
    if "PORT" in os.environ:
        mcp.run(transport="sse")
    else:
        mcp.run()
