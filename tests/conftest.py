"""pytest 配置：把仓库根加入 sys.path, 使根级 eval/ 包可导入。"""
import sys
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
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


@pytest.fixture
def pg_database():
    """Real PostgreSQL; writes/cleanup restricted to a random test schema."""
    url = os.environ.get('MC_TEST_DATABASE_URL')
    if not url:
        pytest.skip('MC_TEST_DATABASE_URL is required for real PostgreSQL tests')
    with psycopg.connect(url, connect_timeout=5) as admin:
        name = admin.execute('SELECT current_database()').fetchone()[0]
        if not name.startswith('mc_test_'):
            pytest.fail('Refusing writes: test database name must start with mc_test_')
        admin.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
        schema = 'pgvector_test_' + uuid.uuid4().hex
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    scoped = make_conninfo(url, options=f'-c search_path={schema},public -c statement_timeout=15000')
    try:
        yield scoped
    finally:
        with psycopg.connect(url, connect_timeout=5) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
