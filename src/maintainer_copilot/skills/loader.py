"""per-repo Skill 加载：SKILL.md 常驻 + 参考文档按需加载。

目录约定: skills/{skill_name}/SKILL.md + references/*.md
按需加载原则: 会话开始只读 SKILL.md(角色/语气/规则); FAQ/标签体系等
大文件由工具 read_skill_doc 按需读取, 控制上下文开销。
"""
from pathlib import Path

from ..config import get_settings


class RepoSkill:
    def __init__(self, name: str) -> None:
        self.root = get_settings().skill_dir / name
        self.md = (self.root / "SKILL.md").read_text(encoding="utf-8")

    def list_references(self) -> list[str]:
        refs = self.root / "references"
        if not refs.exists():
            return []
        return sorted(p.name for p in refs.glob("*.md"))

    def read_reference(self, name: str) -> str:
        path = (self.root / "references" / name).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"非法引用路径: {name}")
        return path.read_text(encoding="utf-8")


def load_skill(name: str) -> RepoSkill:
    return RepoSkill(name)
