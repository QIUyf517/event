# A股ETF公开资料与资金信号研究助手

第一阶段实现一个只支持 `510300` 的离线可运行原型：行情/份额数据入库、三个确定性资金信号、公开资料关键词检索、带证据的结构化研究报告，以及 Streamlit 页面。

## 快速开始

```powershell
C:\Users\29964\anaconda3\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m app.cli doctor
python -m app.cli --mode demo seed-demo
python -m app.cli --mode demo research
python -m streamlit run app/frontend/streamlit_app.py
python -m pytest
```

默认读取根目录 `.env`，并使用 `ETF_DATA_MODE=demo`。演示与真实模式分别写入 `*.demo.sqlite3` 和 `*.live.sqlite3`，不会混用。真实模式只在显式同步命令中联网；真实来源失败时记录失败，不回退到合成数据。

```powershell
python -m app.cli --mode live sync-market --start 2026-05-01 --end 2026-09-28
python -m app.cli --mode live research --as-of 2026-09-28 "510300资金信号"
```

行情优先使用AKShare东方财富接口，失败时可降级到AKShare新浪接口；两者都属于真实公开行情。ETF份额直接读取上交所公开规模接口，未发布日期保持缺失。

## 导入官方资料

支持PDF和HTML；PDF切片保留页码，HTML切片保留章节或正文位置。

```powershell
python -m app.cli --mode live ingest-local report.pdf --title "基金定期报告" --publisher "华泰柏瑞基金" --published-at 2026-08-30 --source-url "官方原文URL" --type fund_report

python -m app.cli --mode live ingest-url "上交所官方HTML或PDF地址" --title "公告标题" --publisher "上海证券交易所" --published-at 2026-02-02 --type fund_announcement
```

URL采集只接受已列入白名单的官方域名。扫描版PDF不会伪造文本，会明确要求先进行OCR。

## LLM边界

LLM默认关闭。只有 `.env` 中同时配置 `LLM_*` 并设置 `LLM_ENABLED=true` 时才会调用。模型只能生成带文档引用的“合理推测”；包含证据外数字或无效引用的内容会被删除。所有指标始终由Python计算。

建议始终使用项目 `.venv`，不要把项目依赖直接混装到承担Jupyter等其他任务的Anaconda基础环境中。

核心数据保存在 SQLite。原始数据、指标结果、文档和报告分开保存，并记录来源、数据日期、抓取时间和算法版本。份额变化是资金行为代理信号，不等同于资金净流入或特定主体买卖。项目不提供自动交易或个性化投资建议。

## 目录

```text
app/
  agents/       单 Agent 研究编排
  analysis/     指标和信号引擎
  data/         数据模型、采集器、SQLite仓储
  frontend/     Streamlit 页面
  models/       Pydantic 结构化模型
  rag/          文档解析、切分、关键词检索
  tools/        Agent 工具接口
tests/          单元和集成测试
evals/          固定评估问题
```

