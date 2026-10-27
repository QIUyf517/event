import { CodeXml, FileSearch, Search, ShieldCheck, Timeline } from "lucide-react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { EventSourcesPage } from "./pages/EventSourcesPage";
import { EventTimelinePage } from "./pages/EventTimelinePage";
import { ResearchPage } from "./pages/ResearchPage";

export default function App() {
  return (
    <div className="app-frame">
      <aside className="side-rail">
        <div className="brand-mark"><FileSearch /><div><strong>企业事件研究台</strong><small>来源与证据</small></div></div>
        <nav aria-label="主导航">
          <NavLink to="/research"><FileSearch /><span>发起事件研究</span></NavLink>
          <NavLink to="/event-timeline"><Timeline /><span>企业时间线</span></NavLink>
          <NavLink to="/event-sources"><Search /><span>企业资料检索</span></NavLink>
        </nav>
        <div className="rail-note"><ShieldCheck /><p>原文逐条可回查<br />不确定信息明确标记</p></div>
        <a className="rail-link" href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer"><CodeXml />查看API文档</a>
      </aside>
      <div className="content-area">
        <Routes>
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/event-timeline" element={<EventTimelinePage />} />
          <Route path="/event-sources" element={<EventSourcesPage />} />
          <Route path="*" element={<Navigate to="/research" replace />} />
        </Routes>
        <footer>企业事件资料研究，不构成投资建议 · 模型调用由用户主动触发</footer>
      </div>
    </div>
  );
}
