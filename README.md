# A股ETF公开资料与资金信号研究助手

支持 `510300`、`510050`、`510500` 三只ETF：行情和份额数据入库、五项确定性观察指标、公开资料关键词检索、带证据的结构化研究报告，以及 Streamlit 页面。第二阶段已增加协调、数据分析、公开资料、核验与报告四个角色。第三阶段提供日终自动更新、条件重试、手动监控、历史信号、ETF对比和本地提醒。演示模式使用合成数据；真实模式只使用采集到的公开数据，两种模式的数据库相互隔离。

## 快速开始

```powershell
C:\Users\29964\anaconda3\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m app.cli doctor
python -m app.cli --mode demo seed-demo
python -m app.cli --mode demo research
python -m app.cli --mode demo research --etf 510050
python -m app.cli --mode demo research --etf 510500
python -m streamlit run app/frontend/streamlit_app.py
python -m pytest
```

默认读取根目录 `.env`，并使用 `ETF_DATA_MODE=demo`。演示与真实模式分别写入 `*.demo.sqlite3` 和 `*.live.sqlite3`，不会混用。真实行情仅在显式同步时联网，URL资料导入也会联网；真实来源失败时记录失败，不回退到合成数据。

网页侧栏可以直接在“演示数据”和“真实数据”之间切换，不需要修改 `.env` 或重启程序。`.env` 中的 `ETF_DATA_MODE` 只决定页面首次打开时的默认选项。切换到真实数据后，可点击“更新所选基金的真实数据”按需更新日线和份额；公开公告仍需通过导入命令加入资料库。同步不是盘中实时行情推送。

“研究问题”会决定报告先回答哪个主题，以及是否检索已入库资料。当前支持综合资金信号、最新价格与60日价格位置、最新份额与5个可用披露日份额变化、成交额热度、已入库公告或报告。空问题不能提交；指定其他期限、跨ETF比较、涨跌预测等超出当前计算范围的问题会明确提示，不会套用固定指标冒充答案。默认问题询问当前信号，避免把当前截面说成20日信号序列。

## 第三阶段：日终资金监控

网页底部可点击“手动更新并检查三只基金”（真实模式）或“检查三只基金的演示数据（不联网）”。真实模式只在点击后尝试同步；已知休市日跳过联网。运行会将三只ETF的历史信号、异常和本地提醒写入当前模式的SQLite数据库。页面还展示三只基金对比、所选基金的历史信号和最近提醒。各基金来源日期不一致时提示，不进行信号强弱排名。

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

自动任务在工作日18:30执行主更新；20:30任务先检查主任务结果，仅在当日行情或份额不完整时重试。周末和已经登记的上交所休市日跳过。同一日期、同一时段的任务具有幂等执行记录，Windows重复触发不会重复运行；任务错过后由Windows“可用时立即启动”设置补跑。每次同步都会检查本地最近30个交易日的份额，能够补回行情已入库但份额稍晚发布的记录。

```powershell
# 安装或更新当前Windows用户的两个定时任务
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_tasks.ps1

# 查看任务
Get-ScheduledTask -TaskName "ETFResearchMonitor-*"

# 卸载任务；不会删除数据库和历史记录
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_windows_tasks.ps1
```

任务使用项目 `.venv`，以当前Windows用户的交互会话运行，不要求管理员权限。电脑关机时无法运行；下次登录后Windows会按“可用时立即启动”补跑。18:30以前启动的补跑处理最近一个已知交易日，避免把早晨尚未收盘的日期误记为已更新。休市表目前明确覆盖2026年，后续年份在上交所发布年度安排后需要更新；未登记年份的工作日仍会尝试同步，但不会混入演示数据。

可以直接验证调度逻辑；重复执行同一命令默认返回已有记录，只有显式加 `--force` 才重跑：

```powershell
python -m app.cli --mode live scheduled-monitor --slot primary --as-of 2026-10-08
python -m app.cli --mode live scheduled-monitor --slot retry --as-of 2026-10-08
```

设计、失败处理和验收点见[第三阶段自动更新说明](docs/phase3_automation.md)。当前提醒保存在本地数据库并展示在页面，没有接入邮件、微信或钉钉推送。

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

## LLM边界

LLM默认关闭。只有 `.env` 中同时配置 `LLM_*` 并设置 `LLM_ENABLED=true` 时才会调用。模型只请求带文档引用的连续原文摘录；无法在所引片段中逐字定位的内容会被删除。系统不会自动发布模型的因果推断。所有指标始终由Python计算。

建议始终使用项目 `.venv`，不要把项目依赖直接混装到承担Jupyter等其他任务的Anaconda基础环境中。

核心数据保存在 SQLite。原始数据、指标结果、文档和报告分开保存，并记录来源、数据日期、抓取时间和算法版本。项目不提供自动交易或个性化投资建议。

## 目录

```text
app/
  agents/       四角色研究编排与问题路由
  analysis/     指标和信号引擎
  data/         数据模型、采集器、SQLite仓储
  frontend/     Streamlit 页面
  models/       Pydantic 结构化模型
  rag/          文档解析、切分、关键词检索
  tools/        Agent 工具接口
tests/          单元和集成测试
evals/          固定评估问题
```

