import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { CasesPage } from "./pages/CasesPage";
import { ExperimentDetailPage } from "./pages/ExperimentDetailPage";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { OperationsPage } from "./pages/OperationsPage";
import { TrustPage } from "./pages/TrustPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="/cases" element={<CasesPage />} />
        <Route path="/cases/:id" element={<CaseDetailPage />} />
        <Route path="/experiments" element={<ExperimentsPage />} />
        <Route path="/experiments/:id" element={<ExperimentDetailPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/trust" element={<TrustPage />} />
        <Route path="/operations" element={<OperationsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
