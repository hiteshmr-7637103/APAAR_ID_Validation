import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { useAuth } from "./hooks/useAuth";
import { t } from "./i18n";
import { AREA_ROLES, homePath, type Area } from "./lib/roles";
import { LoginPage } from "./pages/LoginPage";
import { PortalHome } from "./pages/PortalHome";
import { ApprovalsPage } from "./pages/Approvals";
import { AuditLogPage } from "./pages/AuditLog";
import { InstitutionDashboardPage } from "./pages/InstitutionDashboard";
import { StudentRecordFormPage } from "./pages/StudentRecordForm";
import { UploadWizardPage } from "./pages/UploadWizard";

function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8 text-slate-500">Loading…</div>;
  return <Navigate to={user ? homePath(user.role) : "/login"} replace />;
}

const AREAS: Area[] = ["aicte", "institution", "verifier", "student"];

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/login" element={<LoginPage />} />
      {AREAS.map((area) => (
        <Route
          key={area}
          path={`/${area}/*`}
          element={
            <ProtectedRoute roles={AREA_ROLES[area]}>
              <PortalHome area={area} />
            </ProtectedRoute>
          }
        />
      ))}
      <Route
        path="/institution/records/new"
        element={
          <ProtectedRoute roles={["INST_MAKER"]}>
            <StudentRecordFormPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/institution/dashboard"
        element={
          <ProtectedRoute roles={["INST_MAKER", "INST_CHECKER"]}>
            <InstitutionDashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/institution/uploads"
        element={
          <ProtectedRoute roles={["INST_MAKER"]}>
            <UploadWizardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/institution/approvals"
        element={
          <ProtectedRoute roles={["INST_CHECKER"]}>
            <ApprovalsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/aicte/audit"
        element={
          <ProtectedRoute roles={["AICTE_ADMIN", "AICTE_REVIEWER"]}>
            <AuditLogPage />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<div className="p-8">{t("errors.notFound")}</div>} />
    </Routes>
  );
}
