"""全局配置加载：环境变量 + .env + YAML 三来源。

- 环境变量前缀 MC_（DeepSeek Key 兼容 DEEPSEEK_API_KEY 裸名）
- config/*.yaml 存放非敏感静态配置（仓库清单、模型列表）
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_prefix="MC_", extra="ignore"
    )

    # LLM 主备双通道（OpenAI 兼容协议）
    deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("MC_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    )
    deepseek_base_url: str = "https://api.deepseek.com"
    primary_model: str = "deepseek-chat"
    ollama_base_url: str = "http://localhost:11434/v1"
    fallback_model: str = "qwen2.5:7b"

    # GitHub
    github_token: str = ""
    github_webhook_secret: str = ""

    # 服务与存储
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = "postgresql://postgres:postgres@localhost:5432/maintainer_copilot"

    # 本地路径
    data_dir: Path = PROJECT_ROOT / "data"
    skill_dir: Path = PROJECT_ROOT / "skills"
    checkpoint_dir: Path = PROJECT_ROOT / "data" / "checkpoints"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_yaml(name: str) -> dict:
    path = PROJECT_ROOT / "config" / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
