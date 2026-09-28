# A股ETF公开资料与资金信号研究助手

第一阶段实现一个只支持 `510300` 的离线可运行原型：行情/份额数据入库、三个确定性资金信号、公开资料关键词检索、带证据的结构化研究报告，以及 Streamlit 页面。

## 快速开始

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -e ".[dev,market]"
python -m app.cli demo
streamlit run app/frontend/streamlit_app.py
pytest
```

默认读取根目录 `.env`，并使用 `ETF_DATA_MODE=demo` 的内置演示数据；演示数据只用于验证流程，不代表真实市场结论。将该值改成 `auto` 或 `live` 后，CLI 会尝试通过 AKShare 同步行情，失败时保留明确的降级状态。页面侧边栏也可以手动触发同步。

建议为本项目单独创建虚拟环境。不要把项目依赖直接混装到承担 Jupyter 等其他任务的 Anaconda 基础环境中。

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

