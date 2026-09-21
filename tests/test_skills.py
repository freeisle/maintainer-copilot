"""Skill 加载器单测: 缺失/损坏/禁用/正常加载。"""
from pathlib import Path

import pytest

from maintainer_copilot.config import Settings
from maintainer_copilot.skills import loader


@pytest.fixture
def tmp_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill_dir = tmp_path / "skills"
    monkeypatch.setattr(
        loader, "get_settings", lambda: Settings(skill_dir=skill_dir, data_dir=tmp_path)
    )
    return skill_dir


def _write_skill(
    skill_dir: Path, repo: str, name: str = "测试", content: str = "术语内容", enabled: bool = True
) -> None:
    p = skill_dir / loader._slug(repo)
    p.mkdir(parents=True)
    (p / "skill.yaml").write_text(
        f"name: {name}\ndescription: 领域知识\ncontent_file: terms.md\nenabled: {enabled}",
        encoding="utf-8",
    )
    (p / "terms.md").write_text(content, encoding="utf-8")


def test_load_skill_missing_returns_none(tmp_skills: Path) -> None:
    assert loader.load_skill("freeisle/12306") is None
    assert loader.skill_context("freeisle/12306") == ""


def test_load_and_context(tmp_skills: Path) -> None:
    _write_skill(tmp_skills, "freeisle/12306")
    skill = loader.load_skill("freeisle/12306")
    assert skill is not None
    assert skill["name"] == "测试"
    ctx = loader.skill_context("freeisle/12306")
    assert "仓库专属 Skill" in ctx
    assert "术语内容" in ctx


def test_disabled_skill_yields_empty_context(tmp_skills: Path) -> None:
    _write_skill(tmp_skills, "freeisle/12306", enabled=False)
    assert loader.load_skill("freeisle/12306") is not None
    assert loader.skill_context("freeisle/12306") == ""


def test_broken_yaml_returns_none(tmp_skills: Path) -> None:
    p = tmp_skills / loader._slug("freeisle/12306")
    p.mkdir(parents=True)
    (p / "skill.yaml").write_text("name: [未闭合", encoding="utf-8")
    assert loader.load_skill("freeisle/12306") is None


def test_missing_content_file_yields_empty_context(tmp_skills: Path) -> None:
    p = tmp_skills / loader._slug("freeisle/12306")
    p.mkdir(parents=True)
    (p / "skill.yaml").write_text(
        "name: x\ndescription: y\ncontent_file: nope.md\nenabled: true", encoding="utf-8"
    )
    assert loader.skill_context("freeisle/12306") == ""
