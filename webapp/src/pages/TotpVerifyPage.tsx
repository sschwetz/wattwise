import { type FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useSubmitGuard } from "../hooks/useSubmitGuard";
import { AuthCard } from "../layout/AuthCard";

interface LocationState {
  mfaToken: string;
}

interface VerifyResponse {
  access_token: string;
  token_type: string;
}

export function TotpVerifyPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { login } = useAuth();
  const mfaToken = (location.state as LocationState | null)?.mfaToken ?? null;

  const [code, setCode] = useState("");
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { isSubmitting, guard } = useSubmitGuard();

  if (!mfaToken) {
    return (
      <AuthCard title="Session expired">
        <p role="alert" className="text-ink-muted">
          Your session expired. Please log in again.
        </p>
      </AuthCard>
    );
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    await guard(async () => {
      try {
        const result = await apiFetch<VerifyResponse>("/api/v1/auth/totp/verify", {
          method: "POST",
          headers: { Authorization: `Bearer ${mfaToken}` },
          body: JSON.stringify({ code }),
        });
        login(result.access_token);
        navigate("/");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Incorrect code");
      }
    });
  }

  const codeLabel = useRecoveryCode ? "Recovery code" : "6-digit code";

  return (
    <AuthCard title={`Enter your ${useRecoveryCode ? "recovery" : "authenticator"} code`}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={codeLabel}
          aria-label={codeLabel}
          name="totp-code"
          autoComplete="one-time-code"
          inputMode={useRecoveryCode ? "text" : "numeric"}
          spellCheck={false}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Verify
        </button>
        <button
          type="button"
          onClick={() => setUseRecoveryCode((v) => !v)}
          className="w-full text-sm text-ink-muted underline hover:text-ink"
        >
          {useRecoveryCode ? "Use authenticator code instead" : "Use a recovery code instead"}
        </button>
      </form>
    </AuthCard>
  );
}
