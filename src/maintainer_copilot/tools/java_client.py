"""Python MCP client for the optional Spring Boot POM inspector."""
import asyncio
import inspect
import json
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
try:  # MCP SDK v2 uses httpx2; v1 uses httpx.
    import httpx2 as http_client
except ImportError:
    import httpx as http_client


class JavaToolsClient:
    def __init__(self, url="http://127.0.0.1:8020/mcp", timeout=10):
        self.url, self.timeout = url, timeout

    async def inspect_pom(self, xml: str):
        if not xml.strip() or len(xml) > 200000:
            raise ValueError("POM must contain 1..200000 characters")
        async def call():
            # Spring AI 1.1 implements the initialize handshake, not 2026 discovery.
            options = {"mode": "legacy"} if "mode" in inspect.signature(Client).parameters else {}
            transport = StreamableHttpTransport(self.url,
                httpx_client_factory=lambda **kw: http_client.AsyncClient(**kw, trust_env=False))
            async with Client(transport, timeout=self.timeout, **options) as client:
                tools = await client.list_tools()
                if "inspect_pom" not in {t.name for t in tools}:
                    raise RuntimeError("Java server does not advertise inspect_pom")
                result = await client.call_tool("inspect_pom", {"xml":xml})
                if result.data is not None:
                    return result.data
                # Spring AI 1.1 serializes Map tool output as MCP TextContent.
                return json.loads(next(c.text for c in result.content if c.type == "text"))
        return await asyncio.wait_for(call(), self.timeout)
