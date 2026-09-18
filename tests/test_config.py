"""配置加载单测：MC_ 前缀环境变量与 .env 文件。"""
from pathlib import Path

from maintainer_copilot.config import Settings


def test_env_prefix_loads_from_dotenv(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "MC_GITHUB_TOKEN=ghp_test123\nMC_GITHUB_WEBHOOK_SECRET=secret456\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env)
    assert settings.github_token == "ghp_test123"
    assert settings.github_webhook_secret == "secret456"
