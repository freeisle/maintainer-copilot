"""GitHub 读工具定义：Pydantic 参数 Schema + 描述。

描述编写原则(由工具选择评测集验证): 说明"何时用/何时不用", 而非复述参数。
"""
from pydantic import BaseModel, Field

from ..config import get_settings
from .github_client import GitHubClient
from .registry import ToolRegistry, ToolSpec


class RepoIssueArgs(BaseModel):
    repo: str = Field(description="仓库全名, 如 freeisle/12306")
    number: int = Field(description="Issue 编号")


class SearchIssuesArgs(BaseModel):
    repo: str = Field(description="仓库全名, 如 freeisle/12306")
    query: str = Field(description="GitHub 搜索语法, 如 is:issue 报错关键字")
    per_page: int = Field(default=30, description="返回条数, 1-100")


class ReadFileArgs(BaseModel):
    repo: str = Field(description="仓库全名")
    path: str = Field(description="仓库内文件路径, 如 pom.xml")
    ref: str | None = Field(default=None, description="分支/commit SHA, 默认主分支")


def build_github_tools(client: GitHubClient | None = None) -> ToolRegistry:
    client = client or GitHubClient(token=get_settings().github_token)
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="get_issue",
            description=(
                "读取指定编号 issue 的标题/正文/状态。查询里提到具体 issue 编号时使用;"
                "要查找相似/历史 issue 用 search_issues, 要文件内容用 read_file。"
            ),
            fn=client.get_issue,
            args_schema=RepoIssueArgs,
        )
    )
    registry.register(
        ToolSpec(
            name="search_issues",
            description=(
                "在仓库内按关键词搜索历史 issue(标题+正文)。查询提到找相似问题/判断是否重复/"
                "搜历史反馈时使用; 已知编号的单个 issue 用 get_issue, 代码或配置文件内容用 read_file。"
            ),
            fn=client.search_issues,
            args_schema=SearchIssuesArgs,
        )
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description=(
                "读取仓库内指定文件(查询中给出文件名/路径)的内容。需要某个文件的代码或配置时使用;"
                "未指定文件的代码定位或通用技术问题选 none(走知识库检索);"
                "issue 相关内容用 get_issue / search_issues。"
            ),
            fn=client.get_contents,
            args_schema=ReadFileArgs,
        )
    )
    return registry
