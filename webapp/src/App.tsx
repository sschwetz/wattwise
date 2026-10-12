import { Navigate, Route, Routes } from "react-router-dom";
import { HelpPage } from "./help/HelpPage";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { ProtectedLayout } from "./layout/ProtectedLayout";
import { SiteLayout } from "./layout/SiteLayout";
import { AccountPage } from "./pages/AccountPage";
import { AddSitePage } from "./pages/AddSitePage";
import { BillingPage } from "./pages/BillingPage";
import { ConfirmEmailChangePage } from "./pages/ConfirmEmailChangePage";
import { ConfirmRegistrationPage } from "./pages/ConfirmRegistrationPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { SitePickerPage } from "./pages/SitePickerPage";
import { SourcesPage } from "./pages/SourcesPage";
import { TotpSetupPage } from "./pages/TotpSetupPage";
import { TotpVerifyPage } from "./pages/TotpVerifyPage";

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
          <Route path="/account" element={<AccountPage />} />
          <Route path="/help/:slug?" element={<HelpPage />} />
          <Route path="/sites/:siteId" element={<SiteLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="sources" element={<SourcesPage />} />
            <Route path="billing" element={<BillingPage />} />
            <Route path="help/:slug?" element={<HelpPage />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
