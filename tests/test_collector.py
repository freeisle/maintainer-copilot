"""采集器单测：分页 / PR 过滤 / 评论按需拉取 / 幂等续采。"""
import json
from pathlib import Path

import httpx
import pytest
import respx

from maintainer_copilot.tools.github_client import GitHubClient
from scripts.collect_github_data import collect_issues, load_existing_numbers

ISSUES_URL = "https://api.github.com/repos/x/y/issues"


def make_issue(number: int, comments: int = 0, is_pr: bool = False) -> dict:
    issue = {
        "number": number,
        "title": f"issue {number}",
        "state": "closed",
        "labels": [],
        "comments": comments,
        "body": "b",
    }
    if is_pr:
        issue["pull_request"] = {}
    return issue


@pytest.mark.asyncio
async def test_collect_pagination_pr_filter_and_dedup(tmp_path: Path) -> None:
    out = tmp_path / "issues.jsonl"
    client = GitHubClient(token="test")
    page1 = [make_issue(2, comments=1), make_issue(1), make_issue(99, is_pr=True)]
    page2 = [make_issue(3)]
    with respx.mock() as mock:
        mock.get(ISSUES_URL, params={"state": "all", "page": 1, "per_page": 2}).mock(
            return_value=httpx.Response(200, json=page1)
        )
        mock.get(ISSUES_URL, params={"state": "all", "page": 2, "per_page": 2}).mock(
            return_value=httpx.Response(200, json=page2)
        )
        mock.get("https://api.github.com/repos/x/y/issues/2/comments").mock(
            return_value=httpx.Response(200, json=[{"id": 1, "body": "c"}])
        )
        stats = await collect_issues(client, "x/y", out, per_page=2)
    assert stats["collected"] == 3
    assert stats["comments_fetched"] == 1  # 只有 issue2 有评论
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    issue2 = next(json.loads(l) for l in lines if json.loads(l)["number"] == 2)
    assert issue2["comments_data"] == [{"id": 1, "body": "c"}]

    # 幂等续采: 换新客户端(无缓存), 已采集编号全部跳过
    with respx.mock() as mock:
        mock.get(ISSUES_URL, params={"state": "all", "page": 1, "per_page": 2}).mock(
            return_value=httpx.Response(200, json=page1)
        )
        mock.get(ISSUES_URL, params={"state": "all", "page": 2, "per_page": 2}).mock(
            return_value=httpx.Response(200, json=page2)
        )
        stats2 = await collect_issues(GitHubClient(token="test"), "x/y", out, per_page=2)
    assert stats2["collected"] == 0
    assert stats2["skipped"] == 3


def test_load_existing_numbers_skips_dirty_lines(tmp_path: Path) -> None:
    out = tmp_path / "issues.jsonl"
    out.write_text('{"number": 1}\nnot-json\n{"number": 2}\n', encoding="utf-8")
    assert load_existing_numbers(out) == {1, 2}
