# A股ETF公开资料与资金信号研究助手

> 下一阶段将在当前代码库内逐步改造成“大模型驱动的多 Agent 企业事件研究系统”。分阶段实施内容和验收条件见 [AI原生事件研究改造路线](docs/ai_native_event_research_roadmap.md)。该文档是路线图，其中尚未验收的能力不属于当前已完成功能。

支持 `510300`、`510050`、`510500` 三只ETF：行情和份额数据入库、五项确定性观察指标、公开资料检索、带证据的结构化研究报告，以及 React 研究台。网页可显式选择规则研究或大模型驱动的 AI 协作研究；AI 路径使用有边界的工具调用和程序核验。第二阶段的原四角色规则流程仍可使用。第三阶段提供网页手动收集、数据检查、历史信号、ETF对比和本地提醒。第四阶段已加入 FastAPI、用户偏好、报告存档与 PDF、监控日志、固定评测和容器化配置。演示模式使用合成数据；真实模式只使用采集到的公开数据，两种模式的数据库相互隔离。原 Streamlit 页面继续保留，便于对照和故障排查。

## 快速开始

```powershell
# 首次准备：在项目根目录使用 Python 3.11；已有 .venv 时跳过创建环境
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m app.cli doctor
.\.venv\Scripts\python.exe -m app.cli --mode demo seed-demo

# 终端一：启动 FastAPI
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000

# 终端二：首次运行先安装前端依赖，然后启动 React
cd frontend
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173/`。研究台分为“研究工作台”和“数据与记录”两个页面。默认读取根目录 `.env`，并使用 `ETF_DATA_MODE=demo`；没有 `.env` 时也可读取演示数据。需要生成报告、手动收集或运行检查时，在页面填写 `.env` 中的 `ETF_API_WRITE_KEY`。密钥只保存在当前浏览器标签页的 `sessionStorage`，不会写入前端源码。

演示与真实模式分别写入 `*.demo.sqlite3` 和 `*.live.sqlite3`，不会混用。真实行情仅在用户点击收集或检查按钮时联网，URL资料导入也会联网；真实来源失败时记录失败，不回退到合成数据。原 Streamlit 页面仍可用 `.\.venv\Scripts\python.exe -m streamlit run app/frontend/streamlit_app.py` 启动。

网页侧栏可以直接在“演示数据”和“真实数据”之间切换，不需要修改 `.env` 或重启程序。`.env` 中的 `ETF_DATA_MODE` 只决定页面首次打开时的默认选项。切换到真实数据后，可点击“手动收集所选基金数据”按需更新一只基金，或点击“手动收集并检查三只基金”更新全部三只基金并生成检查结果；打开或刷新网页不会联网收集。公开公告仍需通过导入命令加入资料库。收集的不是盘中实时行情推送。

“研究问题”会决定报告先回答哪个主题，以及是否检索已入库资料。当前支持综合资金信号、最新价格与60日价格位置、最新份额与5个可用披露日份额变化、成交额热度、已入库公告或报告。空问题不能提交；指定其他期限、跨ETF比较、涨跌预测等超出当前计算范围的问题会明确提示，不会套用固定指标冒充答案。默认问题询问当前信号，避免把当前截面说成20日信号序列。

## 第四阶段：API、存档与部署准备

复制 `.env.example` 为 `.env`，把 `ETF_API_WRITE_KEY` 改成随机长字符串。读取接口无需密钥；生成报告、执行监控和修改偏好的写接口必须发送 `X-API-Key`。未配置密钥时写接口返回 `503`，不会以无鉴权方式开放。

```powershell
# 启动 HTTP API 和交互文档
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/docs

# 运行30个固定问题的响应契约评测
python -m app.cli evaluate --as-of 2026-09-30

# 已安装Docker的机器可启动持久化API服务
docker compose up --build
```

API 提供 ETF 列表、行情序列、ETF横向观察、历史信号、研究生成、报告分页存档、单份报告 JSON/PDF、监控运行记录、同步记录、提醒、个人偏好和最近一次评测结果。React 页面通过 API 读取这些数据，并提供规则研究、AI协作研究、手动收集和检查入口。Streamlit 页面仍可用于保存默认选项和辅助排查。

固定评测检查数据模式、查询日期、算法版本、使用边界、证据地址、未来数据泄漏和不支持问题的降级行为。它不能证明研究结论在语义上完全正确，也不能替代真实数据人工抽查。当前部署配置为单实例作品集演示，使用 API Key 做写接口保护；多用户登录、角色权限、PostgreSQL和实际云部署仍属于后续工作。第四阶段验收证据见[第四阶段验收记录](docs/phase4_acceptance_2026-10-07.md)和[实际运行验收补充记录](docs/phase4_runtime_acceptance_2026-10-08.md)。

## 第三阶段：网页手动收集与数据检查

网页侧栏可点击“手动收集所选基金数据”，页面底部可点击“手动收集并检查三只基金”（真实模式）或“检查三只基金的演示数据（不联网）”。真实模式只在点击后尝试联网；已知休市日的三基金检查跳过联网。三基金检查会将历史信号、异常和本地提醒写入当前模式的SQLite数据库。页面还展示三只基金对比、所选基金的历史信号和最近提醒。各基金来源日期不一致时提示，不进行信号强弱排名。

五项指标是近60日价格位置、近5个可用披露日份额变化、成交额历史分位、近5个可用收盘价变化，以及最近5日相对前5日的成交量比。页面展示各项的数值、贡献方向和触发解释。这是观察规则，不是实际资金净流入额或涨跌预测。行情跨来源时，仅对采集器已统一为“份”的东方财富与新浪成交量作连续比较；其他混合来源保留缺失。

```powershell
python -m app.cli --mode demo monitor-once --no-sync
python -m app.cli --mode live monitor-once
python -m app.cli --mode live monitor-once --no-sync
python -m app.cli --mode live compare
python -m app.cli --mode live signal-history --etf 510300
python -m app.cli --mode live alerts
```

`monitor-once` 每调用一次运行一次。`--no-sync` 只使用已有本地数据；真实模式省略该选项时可能联网并写入真实模式数据库。若来源失败、返回零条新记录，或数据日期滞后，结果与提醒会注明。

原先的 Windows 自动任务已停用，后续数据由用户在网页上按需收集。以前的定时任务记录保留为历史证据，不会因为停用任务而删除。每次手动收集仍会检查本地最近30个交易日的份额，能够补回行情已入库但份额稍晚发布的记录。当前提醒保存在本地数据库并展示在页面，没有接入邮件、微信或钉钉推送。原调度设计见[第三阶段历史说明](docs/phase3_automation.md)。

可运行 `python -m scripts.accept_phase3_live --as-of 2026-10-07` 在真实数据库的临时副本上复验监控、历史回填、ETF对比和网页手动按钮。该脚本只读原库，不联网；截至2026-10-07，本地快照三只ETF各回填181条历史信号，页面按钮通过。这里的“运行成功”表示流程执行完成，不表示已有10月7日行情；该快照的最新行情仍为2026-09-30。

## 第二阶段：多 Agent 执行

协调 Agent 根据问题制定计划。数据分析 Agent 与公开资料 Agent 在需要时并行运行，各自使用独立的 SQLite 只读连接；主线程负责保存指标和报告。核验与报告 Agent 等待两边结束，核对资料日期、来源地址和引用编号。命中文档列入 `source_excerpts`，不直接标为 `verified_facts`；模型生成的文字只有能在所引文档中逐字定位时才列入 `source_quotes`，不会当作资金变化的因果结论。页面“研究过程”可展开查看四个角色的输入摘要、输出摘要、错误和证据编号；报告结构化记录与 `tool_calls` 表也保留执行记录。

资料检索失败时，仍给出可核验的行情计算结果并标注资料缺失；行情分析失败时，不输出资金信号结论；协调或并行调度失败时，跳过无法确定或取得的研究任务；核验与报告失败时，保存只说明失败的降级报告。四个角色的失败都会进入执行轨迹，并以 `completed_with_warnings` 标记任务。空问题属于输入错误，不会创建研究任务。超出支持范围的问题跳过行情和资料读取。官方域名仅按已知白名单识别，其他来源显示为“官方性质未核实”。逐字核对证明的是文字出现在入库片段中，不能证明来源本身真实或片段解释了市场变化。

三只ETF的第二阶段真实数据流程已在实时数据库的临时副本上验收，并于2026-10-07在证据分类和轨迹改动后复验。可用 `python -m scripts.accept_phase2_live --as-of 2026-10-07` 重跑，方法、结果和限制见 [验收记录](docs/phase2_live_acceptance_2026-10-06.md)。脚本只读原实时库；研究和页面测试只写临时副本。

演示数据是合成的，不应视为真实行情。演示日历按[上交所2026年休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml)排除当年休市日；其他年份只排除周末，跨年演示不用于验证真实交易日历。

```powershell
python -m app.cli --mode live sync-market --etf 510300 --start 2026-01-01 --end 2026-09-30
python -m app.cli --mode live sync-market --etf 510050 --start 2026-01-01 --end 2026-09-30
python -m app.cli --mode live sync-market --etf 510500 --start 2026-01-01 --end 2026-09-30
python -m app.cli --mode live research --etf 510300 --as-of 2026-09-30 "510300资金信号"
python -m app.cli --mode live research --etf 510050 --as-of 2026-09-30 "510050资金信号"
python -m app.cli --mode live research --etf 510500 --as-of 2026-09-30 "510500资金信号"
```

行情优先使用AKShare东方财富接口，失败时可降级到AKShare新浪接口；两者都属于真实公开行情。ETF份额直接读取上交所公开规模接口，未发布日期保持缺失。

三个ETF的基础信息按[上交所ETF列表](https://etf.sse.com.cn/fundlist/)设置。2026-10-04本地验收时，三只ETF的行情和最近份额记录均已更新至2026-09-30；这只是本地数据库的快照，运行时应查看报告列出的各来源截止日期。份额变化计算的是最近5个**可用披露日**，当披露有缺口时不把它描述为连续5个交易日。份额变化不是资金净流入额，也无法识别具体买卖主体。

## 导入官方资料

支持PDF和HTML；PDF切片保留页码，HTML切片保留章节或正文位置。

```powershell
python -m app.cli --mode live ingest-local report.pdf --title "基金定期报告" --publisher "华泰柏瑞基金" --published-at 2026-08-30 --source-url "官方原文URL" --type fund_report

python -m app.cli --mode live ingest-url "上交所官方HTML或PDF地址" --title "公告标题" --publisher "上海证券交易所" --published-at 2026-02-02 --type fund_announcement
```

URL采集只接受已列入白名单的官方域名。扫描版PDF不会伪造文本，会明确要求先进行OCR。

本地真实数据库已导入三只ETF各一份上交所公告，分别为[510300公告](https://www.sse.com.cn/disclosure/announcement/general/jjzssgg/c/c_20260202_10808073.shtml)、[510050公告](https://www.sse.com.cn/disclosure/announcement/general/jjzssgg/c/c_20260512_10818314.shtml)和[510500公告](https://www.sse.com.cn/disclosure/announcement/general/jjzssgg/c/c_20260626_10823597.shtml)。这些公告仅能证明其中明确披露的做市服务事项，不能证明近期价格或份额变化的成因。`data/*.sqlite3` 不随Git提交；换机器后须重新同步并导入资料。

## 可选的 AI 协作研究

网页的“研究方式”默认是“规则研究”。选择“AI 协作研究”并点击“生成研究简报”后，才会调用模型服务；只打开或刷新网页不会调用模型。先在本机 `.env` 填写 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL_ID`。例如 DeepSeek 的地址是 `https://api.deepseek.com`；密钥不要提交到 Git。AI 按钮是单次显式授权，不需要把 `LLM_ENABLED` 改成 `true`。旧规则路径若设置 `LLM_ENABLED=true`，仍只调用模型做原文摘录。

AI 路径按“模型规划 → 模型数据分析与资料检索 → 程序核验 → 模型复核”运行。模型只可调用 `get_signal`、`search_documents`、`get_document_chunk` 三种只读工具；工具参数、ETF、日期、检索范围和调用次数由程序限制。五项指标和直接回答由 Python 计算、生成；模型可选择查询词和补充检索，但不能写数据库、联网抓取新资料或自行发布数字。模型提供的原文摘录必须在所引片段中逐字定位。报告会保留角色、工具调用、来源日期与失败降级记录。当前 AI 路径是本地作品集原型，不能据文档推断资金变化的成因。

第一次体验可先选“演示数据”，输入“510300当前资金信号和已入库资料分别能说明什么？”，选择“AI 协作研究”，点击“生成研究简报”。演示数据和资料都是合成的。可在临时演示库里执行一次真实模型试跑：

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_ai_research
```

已核对的三份上交所资料清单在 `data/curated_sources.json`。以下命令先联网抓取并核对正文，不写真实数据库；加 `--apply` 后会先建立 SQLite 备份、检查完整性，再一次性导入资料。它不会收集行情和份额。

```powershell
.\.venv\Scripts\python.exe -m scripts.import_curated_sources
.\.venv\Scripts\python.exe -m scripts.import_curated_sources --apply
```

`evals/semantic_cases.json` 提供12个手写检查场景，覆盖数字事实、资料引用、综合研究和能力边界。以下命令只用临时演示库比较规则、单模型和多 Agent 路径；`--ai` 会产生模型费用，不加时只验证规则流程和评分器。完整记录保存到 `output/semantic_comparison.json`，便于阅读的汇总与人工检查表保存到 `output/semantic_comparison_summary.md`。自动检查覆盖预期状态、信号、行情与份额日期、资料命中、引用编号、必要表述、禁止结论和因果限制；仍需人工逐条核对回答与原文。演示数据评测不能证明真实数据的新鲜度或线上质量。

```powershell
# 免费预检：不调用模型
.\.venv\Scripts\python.exe -m scripts.evaluate_research_methods

# 完整对照：会调用已配置的真实模型并产生费用
.\.venv\Scripts\python.exe -m scripts.evaluate_research_methods --ai
```

本次实现、真实数据快照和抽样核对结果见[AI 协作研究验收记录](docs/ai_agent_acceptance_2026-10-10.md)。可用 `.\.venv\Scripts\python.exe -m scripts.smoke_ai_live_snapshot` 只读复制真实库到临时库，并在副本上做一次真实模型试跑；此命令不更新行情或原库报告。

建议始终使用项目 `.venv`，不要把项目依赖直接混装到承担Jupyter等其他任务的Anaconda基础环境中。

核心数据保存在 SQLite。原始数据、指标结果、文档和报告分开保存，并记录来源、数据日期、抓取时间和算法版本。项目不提供自动交易或个性化投资建议。

## 目录

```text
app/
  agents/       四角色研究编排与问题路由
  analysis/     指标和信号引擎
  api/          FastAPI接口与OpenAPI文档
  data/         数据模型、采集器、SQLite仓储
  evaluation/   固定问题评测
  frontend/     Streamlit 页面
  models/       Pydantic 结构化模型
  rag/          文档解析、切分、关键词检索
  reporting/    PDF报告生成
  tools/        Agent 工具接口
frontend/       React + TypeScript 研究台
tests/          单元和集成测试
evals/          固定评估问题
```

