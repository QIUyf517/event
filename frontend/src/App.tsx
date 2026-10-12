import { BarChart3, BookOpen, CodeXml, Database, Search, ShieldCheck } from "lucide-react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { OperationsPage } from "./pages/OperationsPage";
import { EventSourcesPage } from "./pages/EventSourcesPage";
import { ResearchPage } from "./pages/ResearchPage";

export default function App() {
  return (
    <div className="app-frame">
      <aside className="side-rail">
        <div className="brand-mark"><BarChart3 /><div><strong>公开资料研究台</strong><small>来源与证据</small></div></div>
        <nav aria-label="主导航">
          <NavLink to="/research"><BookOpen /><span>研究工作台</span></NavLink>
          <NavLink to="/event-sources"><Search /><span>企业资料检索</span></NavLink>
          <NavLink to="/operations"><Database /><span>数据与记录</span></NavLink>
        </nav>
        <div className="rail-note"><ShieldCheck /><p>数字由程序计算<br />结论保留来源与边界</p></div>
        <a className="rail-link" href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer"><CodeXml />查看API文档</a>
      </aside>
      <div className="content-area">
        <Routes>
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/event-sources" element={<EventSourcesPage />} />
          <Route path="/operations" element={<OperationsPage />} />
          <Route path="*" element={<Navigate to="/research" replace />} />
        </Routes>
        <footer>研究观察，不构成投资建议 · 本地数据与模型调用均由用户主动触发</footer>
      </div>
    </div>
  );
}
