import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { HelpPage } from "./help/HelpPage";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { ProtectedLayout } from "./layout/ProtectedLayout";
import { SiteLayout } from "./layout/SiteLayout";
import { AccountPage } from "./pages/AccountPage";
import { AddSitePage } from "./pages/AddSitePage";
import { ConfirmEmailChangePage } from "./pages/ConfirmEmailChangePage";
import { ConfirmRegistrationPage } from "./pages/ConfirmRegistrationPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ReportingPage } from "./pages/ReportingPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { SitePickerPage } from "./pages/SitePickerPage";
import { SettingsPage } from "./settings/SettingsPage";
import { TotpSetupPage } from "./pages/TotpSetupPage";
import { TotpVerifyPage } from "./pages/TotpVerifyPage";

/** Sends an old per-site URL to the matching settings tab. */
function SettingsTabRedirect({ tab }: { tab: string }) {
  const { siteId } = useParams<{ siteId: string }>();
  return <Navigate to={`/settings/sites/${siteId}/${tab}`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/confirm-registration" element={<ConfirmRegistrationPage />} />
      <Route path="/totp-setup" element={<TotpSetupPage />} />
      <Route path="/totp-verify" element={<TotpVerifyPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/confirm-email-change" element={<ConfirmEmailChangePage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<ProtectedLayout />}>
          <Route path="/" element={<SitePickerPage />} />
          <Route path="/sites" element={<SitePickerPage />} />
          <Route path="/sites/new" element={<AddSitePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/settings/sites/:siteId/:tab" element={<SettingsPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/help/:slug?" element={<HelpPage />} />
          <Route path="/sites/:siteId" element={<SiteLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="reporting" element={<ReportingPage />} />
            <Route path="sources" element={<SettingsTabRedirect tab="collectors" />} />
            <Route path="billing" element={<SettingsTabRedirect tab="billing" />} />
            <Route path="help/:slug?" element={<HelpPage />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
