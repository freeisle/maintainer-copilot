"""Run after `mvn -f services/java-tools/pom.xml package` (real TCP MCP)."""
import asyncio
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest
from maintainer_copilot.tools.java_client import JavaToolsClient

JAR = Path(__file__).resolve().parents[1] / "services/java-tools/target/java-tools-0.1.0.jar"


@pytest.mark.skipif(not JAR.exists() or not shutil.which("java"), reason="Build Java MCP jar first")
def test_real_spring_mcp_process(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with (tmp_path / "java.log").open("w") as log:
        process = subprocess.Popen(["java", "-jar", str(JAR), f"--server.port={port}"],
            stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for _ in range(300):
                if process.poll() is not None:
                    pytest.fail("Java service exited; inspect java.log")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                pytest.fail("Java service startup timed out")

            async def exercise():
                client = JavaToolsClient(f"http://127.0.0.1:{port}/mcp")
                data = await client.inspect_pom('<project><groupId>demo</groupId><artifactId>app</artifactId><version>1</version></project>')
                assert data["artifactId"] == "app" and data["resolved"] is False
                with pytest.raises(Exception, match="(?i)(invalid|doctype|failed|error)"):
                    await client.inspect_pom('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><project>&e;</project>')
                with pytest.raises(Exception):
                    await JavaToolsClient(f"http://127.0.0.1:{port}/missing", timeout=.2).inspect_pom('<project/>')
            asyncio.run(exercise())
        finally:
            process.terminate()
            process.wait(timeout=15)
