// Archival shell; omitted management pages are not linked or imported.
import About from "./pages/About.jsx";
import Login from "./pages/Login.jsx";
export default function App() {
  return <main><p role="status">工程归档：上游调用和凭证管理未包含，不能作为生产版本部署。</p><About onCheckUpdate={() => {}} checkingUpdate={false} /><Login onSuccess={() => {}} /></main>;
}
