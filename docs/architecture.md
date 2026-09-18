# Architecture（公开版）

> 详细设计决策见 `docs/architecture.md` 各节；个人开发记录存放于本地 devlog（不入库）。

## 1. 总体架构

```mermaid
flowchart TB
    subgraph T["触发入口"]
        WH["GitHub Webhook"]
        CLI["CLI 交互"]
        UI["Web 审核台 (HITL)"]
    end
    subgraph C["Agent 核心 · LangGraph 状态机"]
        RT["Route 意图路由"]
        TW["TriageWorker"]
        SW["SolverWorker"]
        RW["ReviewWorker"]
        RF["Reflector 自审"]
        HG["HumanGate 闸门"]
        EX["Executor (唯一写执行器)"]
    end
    subgraph TL["工具层"]
        REG["Tool Registry"]
        GHC["GitHub 客户端 (限流感知)"]
        IDX["本地检索服务"]
        MCP["MCP Server (FastMCP)"]
    end
    subgraph D["存储与外部"]
        PG[("PostgreSQL + pgvector")]
        SK[("per-repo Skill 包")]
        MEM[("长期记忆")]
        GH[("GitHub REST API")]
        LLM[("LLM (DeepSeek / Ollama 兜底)")]
    end
    WH --> RT
    CLI --> RT
    UI --> HG
    RT --> TW & SW & RW
    TW & SW & RW --> RF --> HG
    HG -->|批准| EX --> REG --> GHC --> GH
    HG -->|驳回/编辑| RT
    TW & SW & RW --> REG
    REG --> IDX --> PG
    REG --> MCP
    C --> LLM
```

## 2. 状态机

```mermaid
stateDiagram-v2
    [*] --> Route
    Route --> Triage: issue 事件
    Route --> Solve: 问答请求
    Route --> Review: PR 事件
    state Solve {
        [*] --> QueryRewrite
        QueryRewrite --> Retrieve: 混合检索 + Rerank
        Retrieve --> Answer: 带引用回答
    }
    Triage --> Reflector
    Solve --> Reflector
    Review --> Reflector
    Reflector --> HumanGate: 自审通过
    Reflector --> Triage: 重写 (≤2 次)
    Reflector --> Solve: 重写 (≤2 次)
    Reflector --> Review: 重写 (≤2 次)
    Reflector --> HumanGate: 失败降级仅提示
    HumanGate --> Execute: 批准
    HumanGate --> [*]: 驳回
    Execute --> [*]
```

## 3. 关键设计决策与理由

| 决策 | 选择 | 理由 |
|---|---|---|
| 单 Agent vs Multi-Agent | Supervisor + 3 Worker 子图 | 上下文隔离、职责单一可独立评测、状态机显式可控；单 Agent + 多工具在工具数增长后选择准确率下降且评测困难 |
| A2A vs 子图通信 | 子图 + 类型化状态 | 单机部署无跨服务边界；A2A 留给工具服务化阶段（roadmap） |
| 框架 | LangGraph | checkpoint / HITL interrupt / 子图三大原语成熟 |
| 向量库 | pgvector | 规模不需要分布式 Milvus；SQL 管理索引、单 docker 服务 |
| 混合检索 | pgvector + 进程内 BM25 + 自实现 RRF + BGE-reranker | RRF 融合可解释可控，避免 Qdrant/ES 黑盒与额外服务 |
| GitHub 接入 | 自研 httpx 客户端（8 个端点） | 限流/重试/缓存细节可控；不引第三方 SDK |
| MCP | FastMCP 暴露同一工具层（双接口） | 内部直调 + 外部协议互不干扰 |
| 写安全 | 写工具不进 LLM 工具列表，仅 Executor 执行 | 结构性杜绝 LLM 直接触碰写权限 |

## 4. 核心难点与解法

1. **代码库检索答非所问**：三源异构索引（代码 AST 切分 / 文档标题切分 / issue 结构化切分）+ 混合检索 + 领域化查询改写（报错串精确检索优先）+ rerank + 引用精确率硬指标。
2. **幻觉对外发布**：引用强制 → Reflector 三查（有出处 / 答所问 / 符合规范）→ 重写 ≤2 次 → 失败降级仅提示 → HITL 闸门 → 灰度策略。
3. **GitHub 限流与外部依赖不可靠**：检索本地化 + 限流感知客户端（429 降级不重试、5xx/网络错误指数退避）+ webhook 幂等去重 + LLM 主备双通道。

## 5. 评测体系

- **分诊集**：真实历史 issue 的 label 作弱标注，macro-F1 每类；
- **问答集**：被维护者关闭的历史 issue，LLM-as-judge 三维 rubric（正确性/引用支撑/可用性）+ 引用精确率程序硬指标 + 人工抽检 20%；
- **工具选择集**：查询 → 期望工具，验证 tool description 质量；
- **判官偏差缓解**：双判官、位置交换、temperature=0。

## 6. 演进路线

见 [roadmap.md](roadmap.md)。
