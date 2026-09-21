"""per-repo Skill 加载器: skills/{repo-slug}/skill.yaml + 正文 markdown。

设计:
- 按 repo 键组织(Skill 包与知识库键一一对应), Worker 生成回答前按需注入
- Skill 只增强不兜底: 文件缺失/损坏/禁用一律返回空, 不阻断主链路
- 内容按上限截断, 避免挤占检索上下文的 token 预算
"""
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from ..config import get_settings

logger = logging.getLogger(__name__)

CONTENT_LIMIT = 4000  # 注入上下文的最大字符数


def _slug(repo: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", repo)


def skill_path(repo: str) -> Path:
    """Skill 包目录: skills/{repo-slug}/。"""
    return get_settings().skill_dir / _slug(repo)


def load_skill(repo: str) -> dict[str, Any] | None:
    """加载 repo 对应 Skill 包; 不存在或损坏返回 None。"""
    path = skill_path(repo)
    meta = path / "skill.yaml"
    if not meta.exists():
        return None
    try:
        data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Skill 元数据损坏, 忽略: %s", exc)
        return None
    content = ""
    content_file = data.get("content_file")
    if content_file:
        try:
            content = (path / content_file).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Skill 正文读取失败, 忽略: %s", exc)
    return {
        "name": str(data.get("name", path.name)),
        "description": str(data.get("description", "")),
        "content": content[:CONTENT_LIMIT],
        "enabled": bool(data.get("enabled", True)),
    }


def skill_context(repo: str) -> str:
    """给 Worker 注入的上下文块; 无 Skill / 禁用 / 空正文时返回空串。"""
    skill = load_skill(repo)
    if not skill or not skill.get("enabled") or not skill.get("content"):
        return ""
    return (
        f"[仓库专属 Skill: {skill['name']}]\n"
        f"{skill['description']}\n"
        f"{skill['content']}\n"
    )
