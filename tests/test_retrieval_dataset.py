import json
from pathlib import Path


def test_extension_preserves_baseline_and_adds_coverage():
    root = Path(__file__).resolve().parents[1]
    read = lambda name: [json.loads(x) for x in (root / "eval/datasets" / name).read_text(encoding="utf-8").splitlines()]
    old, new = read("retrieval.jsonl"), read("retrieval-v2.jsonl")
    assert new[:len(old)] == old
    assert len(new) == 24 and len({x["id"] for x in new}) == 24
    assert len({x["repo"] for x in new}) == 3
    assert len({x.get("query_type") for x in new[8:]}) >= 6
    assert all(x["relevant"] and x["evidence"] for x in new[8:])
    for item in new[8:]:
        if item["repo"] == "local/maintainer-copilot":
            for path in item["relevant"]:
                assert item["evidence"] in (root / path).read_text(encoding="utf-8")
