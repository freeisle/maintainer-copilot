import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import httpx
import pytest
from maintainer_copilot.services.a2a import create_app
from maintainer_copilot.services.a2a_client import A2ARetriever, A2AError
from maintainer_copilot.rag.local import LocalRetriever


def snapshot(path):
    path.write_text("\n".join(json.dumps(dict(repo="a/b",id=str(i),source="doc",path=f"{i}.md",text=t))
                             for i,t in enumerate(["needle evidence","other","unrelated"])),encoding="utf-8")


@pytest.mark.asyncio
async def test_protocol_errors_and_auth(tmp_path):
    path=tmp_path/"snapshot"; snapshot(path)
    app=create_app(LocalRetriever(path),allowed_repos=["a/b"],token="test",url="http://local/a2a")
    transport=httpx.ASGITransport(app)
    retriever=A2ARetriever("http://local",token="test",transport=transport)
    assert (await retriever.retrieve("needle","a/b"))[0]["path"] == "0.md"
    with pytest.raises(httpx.HTTPStatusError):
        await A2ARetriever("http://local",transport=transport).retrieve("needle","a/b")
    with pytest.raises(httpx.HTTPStatusError):
        await retriever.retrieve("needle","private/repo")
    async with httpx.AsyncClient(transport=transport,base_url="http://local",headers={"Authorization":"Bearer test"}) as c:
        for method,params,code in [("tasks/get",{"id":"missing"},-32001),("unknown",{},-32601),("message/send",{},-32602)]:
            r=await c.post("/a2a",json={"jsonrpc":"2.0","id":1,"method":method,"params":params})
            assert r.json()["error"]["code"] == code


@pytest.mark.asyncio
async def test_timeout_and_completed_task_contract():
    class Backend:
        async def retrieve(self, query, repo, **kwargs):
            if query == "slow":
                await asyncio.sleep(1)
            return []
    app=create_app(Backend(),allowed_repos=["a/b"],token="",url="http://local/a2a",timeout=.01)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://local") as c:
        async def rpc(method,params):
            return (await c.post("/a2a",json={"jsonrpc":"2.0","id":1,"method":method,"params":params})).json()
        params={"message":{"role":"user","messageId":"one","parts":[{"kind":"data","data":{"repo":"a/b","query":"slow"}}]}}
        assert (await rpc("message/send",params))["error"]["code"] == -32603
        params["message"]["parts"][0]["data"]["query"]="quick"
        task=(await rpc("message/send",params))["result"]
        assert (await rpc("tasks/get",{"id":task["id"]}))["result"] == task
        assert (await rpc("tasks/cancel",{"id":task["id"]}))["error"]["code"] == -32002


def test_actual_separate_service_process(tmp_path):
    path=tmp_path/"snapshot"; snapshot(path)
    with socket.socket() as s:
        s.bind(("127.0.0.1",0)); port=s.getsockname()[1]
    base=f"http://127.0.0.1:{port}"
    env={**os.environ,"MC_RETRIEVAL_BACKEND":"local","MC_LOCAL_INDEX_PATH":str(path),
         "MC_A2A_ALLOWED_REPOS":'["a/b"]',"MC_A2A_PUBLIC_URL":base+"/a2a","MC_A2A_TOKEN":"", "MC_FEDERATION_REPOS":"{}"}
    with (tmp_path/"server.log").open("w") as log:
        proc=subprocess.Popen([sys.executable,"-m","uvicorn","maintainer_copilot.services.a2a:create_app","--factory","--host","127.0.0.1","--port",str(port)],
                              env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            with httpx.Client(trust_env=False,timeout=1) as c:
                for _ in range(150):
                    try:
                        if c.get(base+"/.well-known/agent-card.json").status_code==200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.1)
                else:
                    pytest.fail("A2A service failed to start")
            docs=asyncio.run(A2ARetriever(base).retrieve("needle","a/b"))
            assert docs[0]["repo"]=="a/b" and "needle" in docs[0]["text"]
        finally:
            proc.terminate(); proc.wait(timeout=15)
