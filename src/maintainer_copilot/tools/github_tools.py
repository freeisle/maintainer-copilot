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
                "读取单个 issue 的标题/正文/状态。当需要查看某个 issue 的完整内容时使用;"
                "若只想找相似问题, 用 search_issues。"
            ),
            fn=client.get_issue,
            args_schema=RepoIssueArgs,
        )
    )
    registry.register(
        ToolSpec(
            name="search_issues",
            description=(
                "在指定仓库搜索 issue(标题+正文)。当需要找与当前问题相似的历史 issue、"
                "判断是否重复提交时使用; 不支持查代码, 查代码用 read_file。"
            ),
            fn=client.search_issues,
            args_schema=SearchIssuesArgs,
        )
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description=(
                "读取仓库内指定文件内容。当回答涉及具体配置/实现细节时使用;"
                "目录浏览用 list_dir, 文件历史用 get_commit_history。"
            ),
            fn=client.get_contents,
            args_schema=ReadFileArgs,
        )
    )
    return registry
