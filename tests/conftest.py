"""pytest 配置：把仓库根加入 sys.path, 使根级 eval/ 包可导入。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _isolate_adoption_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """每个测试用临时库隔离 HITL 决策事件单例, 不污染真实采纳率数据。"""
    from maintainer_copilot.metrics import adoption

    store = adoption.DecisionStore(tmp_path / "adoption.sqlite")
    monkeypatch.setattr(adoption, "_store", store)
    yield store
    monkeypatch.setattr(adoption, "_store", None)
