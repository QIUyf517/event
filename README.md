# 多Agent企业事件研究助手

本仓库当前的开发主线是**大模型驱动的多 Agent 企业事件研究原型**。阶段一至三已完成相应范围的本地验收，阶段四在三家公司固定历史样本通过复核；阶段五、六的工程功能已实现，适合作为实习项目演示。用户决定将人工逐条语义验收移出本次交付范围，因此解释准确性没有独立签发，公开报告只展示可定位原文、候选证据及程序生成的保守说明。见[当前实习交付范围](docs/event_research_internship_scope_2026-10-27.md)、[阶段六历史交付记录](docs/event_research_phase6_acceptance_2026-10-23.md)及[架构说明](docs/event_research_architecture.md)。

旧 ETF 页面、行情信号、定时监控、PDF 报告、API 和 CLI 流程已清理。资料解析、研究对象、行情读取、证据检索、模型客户端与鉴权等可复用部分已用于企业事件研究。清理范围与兼容项见[迁移记录](docs/event_research_etf_cleanup_2026-10-13.md)。

## 研究范围与当前进度

第一版研究对象为 `600519` 贵州茅台、`601318` 中国平安、`688981` 中芯国际。每次研究窗口不超过 30 天。用户主动发起资料采集或研究；当前企业事件流程没有后台定时采集。模型负责规划研究任务，Python 负责对象、日期、工具、预算和引用边界。

| 阶段 | 内容 | 状态与证据 |
| --- | --- | --- |
| 一 | 研究契约与人工标注基准 | 已完成本地验收；20 道合成题有标注，尚未作为真实来源回答准确率。[验收记录](docs/event_research_phase1_acceptance_2026-10-12.md) |
| 二 | 可追溯资料层 | 已完成本地及真实来源样本验收；固定窗口内有 6 份快照和 603 个切片。[验收记录](docs/event_research_phase2_acceptance_2026-10-12.md) |
| 三 | 模型规划与并行取证 | 已完成固定历史快照上的本地验收；四类问题形成四种任务图，不代表事件结论正确。[验收记录](docs/event_research_phase3_acceptance_2026-10-12.md) |
| 四 | 时间线与证据图谱 | 三家公司固定历史样本通过逐条人工底稿、逐字引文和浏览器核验；其他资料另验。[进度记录](docs/event_research_phase45_progress_2026-10-23.md) |
| 五 | 假设与反方核验 | 结构化选择、反方检索和保守降级已实现，三家公司固定图谱已复跑；人工逐条语义验收移出实习范围，解释质量未独立验证。[修正记录](docs/event_research_phase5_semantics_2026-10-23.md) |
| 六 | 研究界面与对照评测 | 工程交付完成；20道合成题两轮实验及三家公司真实图谱假设阶段对照已执行。人工审核移出实习范围；生产端到端优势未证明。[交付记录](docs/event_research_phase6_acceptance_2026-10-23.md) |

主入口为 React `/research`。`POST /api/v1/event-research/runs` 鉴权后启动本次研究，`GET /api/v1/event-research/runs/{run_id}` 只读查询计划、阶段状态、工具轨迹、原文图谱、候选证据与保守报告。运行ID写入URL，刷新不会重新调用模型；服务重启后未完成任务标记中断。已有 `plan-run`、`timeline/build`、`debate/build` 和只读查询接口继续保留。系统不输出已证实的市场因果或投资建议。

研究页的候选解释现在使用证据卡展示模型选作支持或反对的原文、资料发布日期、图谱发生日及冲突/转载提示。卡片明确标为“关系未验证”，不把可定位的引用换算成解释成立的百分比；旧历史运行也保持保守标注。

## 一条命令查看历史演示

安装下面的Python依赖后执行：

```powershell
.\.venv\Scripts\python.exe scripts/start_event_demo.py
```

打开 `http://127.0.0.1:8000/research`，选择三家公司查看固定历史事件、原文、冲突、候选证据与保守报告。脚本自动安装缺失的前端依赖并构建页面，需要Node.js和npm；首次安装依赖需要网络。演示包随代码提供，全部模型调用被禁用，不改现有资料库。它只含历史摘录及公开投影，没有完整来源库和原规划轨迹。已构建可加 `--skip-build`，换端口用 `--port 8006`。

正式运行可先执行 `npm --prefix frontend run build`，然后启动API，同一个8000端口会提供网页与API；也可使用下面的Vite开发方式。

## 本地运行

使用 Python 3.11 和项目虚拟环境。以下安装、启动命令不采集企业资料；首次安装依赖需要包源网络访问。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

另开终端启动 React 页面：

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173/`；API 文档在 `http://127.0.0.1:8000/docs`。企业资料检索只读取本地事件资料库。可用 `.env` 中的 `EVENT_RESEARCH_DATABASE_PATH` 指向已准备好的数据库；`EVENT_GRAPH_DATABASE_PATH` 和 `EVENT_DEBATE_DATABASE_PATH` 分别指向独立的图谱与报告库。Git 不包含本地 SQLite、原文快照、`.env` 和模型执行记录；新克隆仓库在准备资料库前不会显示企业资料。

需要复现固定窗口样本时，按[阶段二验收记录](docs/event_research_phase2_acceptance_2026-10-12.md)和[阶段三验收记录](docs/event_research_phase3_acceptance_2026-10-12.md)操作。这些采集命令会联网，并只写各自的 `output/` 验收目录；先确认目标目录不存在并保存已有本地数据。

调用 `plan-run`、生成时间线或生成反方报告前，还需配置 `LLM_ENABLED=true`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID` 及 `EVENT_API_WRITE_KEY`。写入接口需要 `X-API-Key`，会调用模型服务并可能产生费用；只打开或刷新网页不会调用它。阶段五单次模型费用估算上限可用 `EVENT_DEBATE_MAX_USD_PER_RUN` 设置。写入密钥仅使用 `EVENT_API_WRITE_KEY`；本机旧密钥名已迁移，密钥不提交到 Git。

CLI 使用 `python -m app.cli --help` 查看命令；`collect-event-source` 会联网并写入指定事件资料库，`search-event` 只读，`plan-event` 调用模型，`build-event-timeline` 调用模型并写入独立图谱库。运行 Docker Compose 时使用新的 `event-data` 和 `event-output` 卷；旧卷未删除，也不会自动迁移。

## 验收与限制

- 阶段二的固定样本是 2026-08-14 至 2026-09-13 的六份公开资料，包含财务资料与新闻，暂无独立公告样本；它不能代表三家公司完整的历史资料。
- 阶段四三家公司的固定快照产生 9 个事件、33 条逐字定位的引用；真实片段没有足够完整的发生日期，9 个事件目前均显示日期待确认。茅台报道内部的季度净利润算式不一致已标为待核实，原因尚未查明。详见[阶段四验收记录](docs/event_research_phase4_acceptance_2026-10-12.md)。
- 阶段五隔离样本补入三份公告，并在不同的 30 天窗口产生 18 个事件、34 条引文、3 个有动作原文的已知日期；三份报告仍为 `partial`。这不等于阶段四或阶段五完成真实资料验收，详见[阶段五验收记录](docs/event_research_phase5_acceptance_2026-10-13.md)。
- 2026-10-13 的历史补充复跑改进了公告覆盖和事件归组，当时输出 46 个事件、83 条引用、5 个已知发生日期；当时仍有语义失败，见[补充复核记录](docs/event_research_phase45_recheck_2026-10-13.md)。
- 2026-10-23 的最新固定图谱归组为 31 个事件、84 条逐字引用和 6 个有动作原文的日期；人工底稿的 46 个原始事件、87 条事实与浏览器展开检查通过。阶段五仍有模型自由文本误述，见[最新进度记录](docs/event_research_phase45_progress_2026-10-23.md)。
- 20道合成题已执行规则、单角色、多角色两轮共120次实验。单角色与多角色各39次完成；共同成功的38对中，引文逐字匹配率为98.68%与99.12%，事件标签覆盖率为71.57%与67.65%，平均耗时为3.52秒与7.42秒。多角色没有整体更优的证据。这是提示词角色消融，不是生产多Agent准确率。逐次结果、原始请求与回复、未审核记录保存在 `evals/event_phase6_*_20261023.json`；人工审核已移出本次交付范围。
- 经用户明确授权，三家公司固定真实图谱完成两轮单Agent与多Agent假设阶段对照，共12次；来源哈希前后相同。多Agent产生15条反方核查项，平均耗时9.69秒（单Agent为6.16秒），一例候选数量无效后降级。它只比较共享图谱之后的假设阶段，不证明端到端效果或语义准确率。详见[阶段六交付记录](docs/event_research_phase6_acceptance_2026-10-23.md)。
- 公司历史行情使用保存的固定窗口快照；来源页面不是逐日记录的永久链接。正式研究结论前仍需独立复核逐日价格。

运行测试可用 `.\.venv\Scripts\python.exe -m pytest -q` 和 `npm --prefix frontend test`。截至2026-10-27，62项Python测试、5项前端测试、生产构建及三家公司浏览器回放检查通过；手机宽度无横向溢出。测试通过只说明其覆盖的代码行为通过，不代表实时数据新鲜或研究语义正确。当前可用简历表述见[实习范围说明](docs/event_research_internship_scope_2026-10-27.md)。

本次项目总览与进一步清理结果见[2026-10-27 项目审计](docs/event_research_project_audit_2026-10-27.md)。
