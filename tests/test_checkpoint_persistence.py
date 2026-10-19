import os
import subprocess
import sys
from pathlib import Path

import pytest

from maintainer_copilot.graph.checkpoint import PersistentSaver

PROGRAM = '''
import asyncio, sys
from pathlib import Path
from langgraph.types import Command
from maintainer_copilot.graph import supervisor
from maintainer_copilot.graph.checkpoint import PersistentSaver
async def worker(state):
    return {"draft": "evidence [1]", "citations": [{"path":"a.py"}], "draft_meta": {"issue_number": 1}}
async def reflect(state):
    return {"reflection": {"passed": True}}
supervisor.triage_node = worker
supervisor.reflector_node = reflect
async def main():
    saver = PersistentSaver(Path(sys.argv[1]))
    graph = supervisor.build_graph(saver)
    config = {"configurable": {"thread_id": "persistent-test"}}
    if sys.argv[2] == "start":
        result = await graph.ainvoke({"repo":"a/b", "task_type":"triage", "messages":[{"role":"user","content":"help"}]}, config)
        assert result["__interrupt__"]
        assert (await graph.aget_state(config)).next == ("gate",)
    else:
        assert (await graph.aget_state(config)).values["draft"] == "evidence [1]"
        result = await graph.ainvoke(Command(resume={"decision": sys.argv[2], "edited_draft":"edited evidence"}), config)
        assert not (await graph.aget_state(config)).next
        assert result["approval"] == sys.argv[2]
        assert result["final_action"]["executed"] == (sys.argv[2] != "rejected")
        if sys.argv[2] == "edited":
            assert result["final_action"]["actions"]["comment"]["body"] == "edited evidence"
        assert not (await graph.aget_state({"configurable":{"thread_id":"other"}})).values
        assert len([x async for x in saver.alist(config)]) > 1
    saver.close()
asyncio.run(main())
'''


@pytest.mark.parametrize("decision", ["approved", "edited", "rejected"])
def test_actual_graph_survives_process_restart(tmp_path, decision):
    env = {**os.environ, "MC_DRY_RUN": "true", "MC_DATA_DIR": str(tmp_path)}
    for action in ("start", decision):
        run = subprocess.run([sys.executable, "-c", PROGRAM, str(tmp_path / "graph.sqlite"), action],
                             env=env, capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr


def test_invalid_storage_fails_loudly(tmp_path):
    file = tmp_path / "file"
    file.write_text("not a directory")
    with pytest.raises(OSError):
        PersistentSaver(file / "checkpoints.sqlite")
