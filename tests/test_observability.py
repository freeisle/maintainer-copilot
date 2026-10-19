import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import pytest
from maintainer_copilot.config import Settings
from maintainer_copilot.observability import publish_evaluation, traced


@pytest.mark.asyncio
async def test_real_local_otlp_receiver_and_scores(tmp_path):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path,dict(self.headers),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    settings = Settings(_env_file=None,observability_enabled=True,trace_path=tmp_path/'traces.jsonl',
        langfuse_url=f'http://127.0.0.1:{server.server_port}',langfuse_public_key='test-public',langfuse_secret_key='test-secret')
    class Worker:
        @traced('parent')
        async def run(self,secret):
            return await self.child(secret)
        @traced('llm','generation')
        async def child(self,secret):
            return SimpleNamespace(model='test-model',used_fallback=False)
    worker=Worker(); worker.settings=settings
    try:
        await worker.run('never export this password')
        tid=await publish_evaluation('retrieval',{'hit@3':.625,'samples':24,'question':'secret'},settings)
    finally:
        await asyncio.to_thread(server.shutdown); thread.join(); server.server_close()
    assert len(received)==5
    child=received[0][2]['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
    parent=received[1][2]['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
    assert child['traceId']==parent['traceId'] and child['parentSpanId']==parent['spanId']
    assert received[-1][2]['traceId']==tid
    assert received[0][1]['x-langfuse-ingestion-version']=='4'
    text=settings.trace_path.read_text(encoding='utf-8')
    assert 'never export' not in text and 'test-secret' not in text and 'question' not in text


@pytest.mark.asyncio
async def test_export_failure_does_not_mask_result_or_error(tmp_path):
    class Worker:
        settings=Settings(_env_file=None,observability_enabled=True,trace_path=tmp_path/'trace',langfuse_url='http://127.0.0.1:1',langfuse_public_key='a',langfuse_secret_key='b')
        @traced('failure')
        async def run(self):
            raise ValueError('business error')
    with pytest.raises(ValueError,match='business error'):
        await Worker().run()
    assert json.loads((tmp_path/'trace').read_text())['payload']['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['status']['code']==2
