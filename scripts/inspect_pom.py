"""Inspect a local POM using the real Java MCP service (read-only)."""
import argparse
import asyncio
import json
from pathlib import Path
from maintainer_copilot.tools.java_client import JavaToolsClient

if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("path",type=Path)
    p.add_argument("--url",default="http://127.0.0.1:8020/mcp")
    a=p.parse_args()
    print(json.dumps(asyncio.run(JavaToolsClient(a.url).inspect_pom(a.path.read_text(encoding="utf-8"))),ensure_ascii=False,indent=2))
