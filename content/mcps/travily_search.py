import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient

from core import config


def get_tools():
    client = MultiServerMCPClient(
        {
            "tavily": {
                "transport": "streamable_http",
                "url": "https://mcp.tavily.com/mcp/",
                "headers": {"Authorization": f"Bearer {config.TAVILY_SEARCH_KEY}"},
            }
        },
    )
    return asyncio.run(asyncio.wait_for(client.get_tools(), timeout=15))
