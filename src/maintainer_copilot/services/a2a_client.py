"""Configured A2A retrieval client with discovery, bounded timeout and no retries."""
import uuid
from urllib.parse import urlparse
import httpx


class A2AError(RuntimeError):
    pass


class A2ARetriever:
    def __init__(self, url, token="", timeout=25, transport=None):
        self.url, self.token, self.timeout, self.transport = url.rstrip("/"), token, timeout, transport

    async def retrieve(self, query, repo, top_k=8, **kwargs):
        headers = {"Authorization":"Bearer " + self.token} if self.token else {}
        async with httpx.AsyncClient(timeout=self.timeout,headers=headers,transport=self.transport,trust_env=False) as client:
            card = await client.get(self.url + "/.well-known/agent-card.json")
            card.raise_for_status()
            card = card.json()
            endpoint = card.get("url", "")
            # Discovery cannot redirect credentials to another host.
            if urlparse(endpoint).netloc != urlparse(self.url).netloc or urlparse(endpoint).scheme != urlparse(self.url).scheme:
                raise A2AError("Discovery endpoint does not match configured origin")
            if card.get("protocolVersion") != "0.3.0" or card.get("preferredTransport") != "JSONRPC":
                raise A2AError("Unsupported A2A protocol")
            async def rpc(method, params):
                rid = uuid.uuid4().hex
                response = await client.post(endpoint,json={"jsonrpc":"2.0","id":rid,"method":method,"params":params})
                response.raise_for_status()
                body = response.json()
                if body.get("id") != rid:
                    raise A2AError("Mismatched JSON-RPC response")
                if "error" in body:
                    raise A2AError(str(body["error"]))
                return body["result"]
            task = await rpc("message/send", {"message":{"kind":"message","role":"user","messageId":uuid.uuid4().hex,
                             "parts":[{"kind":"data","data":{"repo":repo,"query":query,"top_k":top_k}}]}})
            if task.get("status",{}).get("state") != "completed":
                task = await rpc("tasks/get", {"id":task["id"]})
            if task.get("status",{}).get("state") != "completed":
                raise A2AError("Task did not complete")
            return task["artifacts"][0]["parts"][0]["data"]["docs"]
