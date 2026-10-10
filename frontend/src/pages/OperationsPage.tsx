import { Activity, AlertTriangle, Database, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ApiKeyField } from "../components/ApiKeyField";
import { formatIndicator, indicatorLabels, readableAlert, signalLabels, statusLabels, syncStatusLabels, today, yearStart } from "../lib";
import type { AlertRow, ComparisonRow, DataMode, MonitorRun, SignalHistoryItem, SyncRun } from "../types";

export function OperationsPage() {
  const [mode, setMode] = useState<DataMode>("demo");
  const [etf, setEtf] = useState("510300");
  const [comparison, setComparison] = useState<ComparisonRow[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [monitorRuns, setMonitorRuns] = useState<MonitorRun[]>([]);
  const [syncRuns, setSyncRuns] = useState<SyncRun[]>([]);
  const [history, setHistory] = useState<SignalHistoryItem[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [comp, alertData, monitorData, syncData, signalData] = await Promise.all([
        api.comparison(mode, today()), api.alerts(mode), api.monitorRuns(mode),
        api.syncRuns(mode), api.signals(etf, mode)
      ]);
      setComparison(comp.rows); setWarnings(comp.warnings ?? []); setAlerts(alertData.items);
      setMonitorRuns(monitorData.items); setSyncRuns(syncData.items); setHistory(signalData.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取运行记录失败");
    }
  }, [mode, etf]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function runAll() {
    setBusy("monitor"); setError("");
    try { await api.runMonitor(mode, today()); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "检查失败"); }
    finally { setBusy(""); }
  }

  async function syncSelected() {
    setBusy("sync"); setError("");
    try { await api.syncEtf(etf, yearStart(), today()); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "收集失败"); }
    finally { setBusy(""); }
  }

  return (
    <main className="page-shell">
      <header className="page-header operations-header">
        <div><div className="eyebrow">DATA LEDGER · 数据审计</div><h1>数据日期与运行记录。</h1></div>
        <p>打开页面只读取本地数据。只有点击收集或检查按钮，才会写入数据库；真实模式会访问公开数据源。</p>
      </header>

      <section className="operations-toolbar">
        <label>数据模式<select value={mode} onChange={(event) => setMode(event.target.value as DataMode)}>
          <option value="demo">演示数据</option><option value="live">真实数据</option>
        </select></label>
        <label>查看基金<select value={etf} onChange={(event) => setEtf(event.target.value)}>
          <option value="510300">510300 · 沪深300ETF</option>
          <option value="510050">510050 · 华夏上证50ETF</option>
          <option value="510500">510500 · 南方中证500ETF</option>
        </select></label>
        <ApiKeyField />
        <div className="operation-actions">
          {mode === "live" && <button className="secondary-button" onClick={syncSelected} disabled={Boolean(busy)}>
            <Database size={17} />{busy === "sync" ? "正在收集" : "收集所选基金"}
          </button>}
          <button className="primary-button" onClick={runAll} disabled={Boolean(busy)}>
            {busy === "monitor" ? <RefreshCw className="spin" /> : <Activity />} {mode === "live" ? "收集并检查三只基金" : "检查演示数据"}
          </button>
        </div>
      </section>
      {error && <div className="error-banner">{error}</div>}

      <section>
        <div className="section-title"><span>三只基金横向观察</span><small>日期不同的行不能直接比较强弱</small></div>
        <div className="table-wrap"><table><thead><tr>
          <th>基金</th><th>观察标签</th><th>行情日期</th><th>份额日期</th>
          <th>{indicatorLabels.price_position_60d}</th><th>{indicatorLabels.share_change_5d}</th><th>{indicatorLabels.turnover_percentile}</th>
        </tr></thead><tbody>{comparison.map((row) => <tr key={row.etf_code}>
          <td><strong>{row.etf_code}</strong><small>{row.name}</small></td>
          <td>{signalLabels[row.signal] ?? row.signal}</td>
          <td>{row.source_dates.market ?? "暂无"}</td><td>{row.source_dates.shares ?? "暂无"}</td>
          <td>{formatIndicator("price_position_60d", row.indicators.price_position_60d)}</td>
          <td>{formatIndicator("share_change_5d", row.indicators.share_change_5d)}</td>
          <td>{formatIndicator("turnover_percentile", row.indicators.turnover_percentile)}</td>
        </tr>)}</tbody></table></div>
        {warnings.map((warning, index) => <p className="inline-warning" key={index}><AlertTriangle size={15} />{warning}</p>)}
      </section>

      <div className="operations-grid">
        <section>
          <div className="section-title"><span>历史观察</span><small>{etf} · 最近30条</small></div>
          {history.length ? <div className="history-list">{history.slice().reverse().map((item) => <article key={item.as_of_date}>
            <time>{item.as_of_date}</time><strong>{signalLabels[item.signal] ?? item.signal}</strong>
            <small>行情 {item.source_dates.market ?? "暂无"} · 份额 {item.source_dates.shares ?? "暂无"}</small>
          </article>)}</div> : <div className="empty-state">尚未保存历史观察。</div>}
        </section>
        <section>
          <div className="section-title"><span>本地提醒</span><small>最近20条</small></div>
          {alerts.length ? <div className="alert-list">{alerts.map((item) => <article key={item.alert_id} className={`severity-${item.severity}`}>
            <strong>{item.etf_code} · {item.title}</strong><p>{readableAlert(item.message)}</p><time>{item.as_of_date}</time>
          </article>)}</div> : <div className="empty-state">当前没有本地提醒。</div>}
        </section>
      </div>

      <section>
        <div className="section-title"><span>运行记录</span><small>过程完成不代表数据已经更新</small></div>
        <div className="log-grid">
          <div><h3>手动检查</h3>{monitorRuns.length ? monitorRuns.map((run) => <article className="log-row" key={run.run_id}>
            <span>{run.as_of_date}</span><strong>{statusLabels[run.status] ?? run.status}</strong><small>{run.finished_at}</small>
          </article>) : <div className="empty-state">暂无检查记录。</div>}</div>
          <div><h3>数据收集</h3>{syncRuns.length ? syncRuns.map((run) => <article className="log-row" key={run.sync_id}>
            <span>{run.etf_code}</span><strong>{syncStatusLabels[run.market_status] ?? run.market_status} / {syncStatusLabels[run.share_status] ?? run.share_status}</strong><small>{run.end_date}</small>
          </article>) : <div className="empty-state">暂无收集记录。</div>}</div>
        </div>
      </section>
    </main>
  );
}
