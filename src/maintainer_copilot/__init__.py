"""Maintainer Copilot: 面向开源维护者的 AI 协作者。"""

__version__ = "0.1.0"

import sys

if sys.platform == "win32":
    # psycopg 异步模式在 Windows 上不兼容默认的 ProactorEventLoop,
    # 统一切换 Selector 策略(uvicorn/httpx/psycopg 均兼容)
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
