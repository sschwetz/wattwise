import QRCode from "qrcode";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useSubmitGuard } from "../hooks/useSubmitGuard";
import { AuthCard } from "../layout/AuthCard";

interface LocationState {
  setupToken: string;
}

interface SetupStartResponse {
  secret: string;
  otpauth_uri: string;
}

interface SetupConfirmResponse {
  access_token: string;
  token_type: string;
  recovery_codes: string[];
}

export function TotpSetupPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { login } = useAuth();
  const setupToken = (location.state as LocationState | null)?.setupToken ?? null;

  const [secret, setSecret] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { isSubmitting: confirmPending, guard } = useSubmitGuard();
  const pendingAccessToken = useRef<string | null>(null);

  useEffect(() => {
    if (!setupToken) return;
    apiFetch<SetupStartResponse>("/api/v1/auth/totp/setup/start", {
      method: "POST",
      headers: { Authorization: `Bearer ${setupToken}` },
    })
      .then(async (result) => {
        setSecret(result.secret);
        setQrDataUrl(await QRCode.toDataURL(result.otpauth_uri));
      })
      .catch(() => {
        setLoadError("Failed to load your authenticator setup. Please log in again.");
      });
  }, [setupToken]);

  if (!setupToken) {
    return (
      <AuthCard title="Session expired">
        <p role="alert" className="text-ink-muted">
          Your session expired. Please log in again.
        </p>
      </AuthCard>
    );
  }

  async function handleConfirm(event: FormEvent) {
    event.preventDefault();
    setError(null);
    await guard(async () => {
      try {
        const result = await apiFetch<SetupConfirmResponse>("/api/v1/auth/totp/setup/confirm", {
          method: "POST",
          headers: { Authorization: `Bearer ${setupToken}` },
          body: JSON.stringify({ code }),
        });
        pendingAccessToken.current = result.access_token;
        setRecoveryCodes(result.recovery_codes);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Incorrect code");
      }
    });
  }

  function handleContinue() {
    if (pendingAccessToken.current) {
      login(pendingAccessToken.current);
      navigate("/");
    }
  }

  if (recoveryCodes) {
    return (
      <AuthCard title="Save your recovery codes" maxWidth="max-w-md">
        <p className="text-ink-muted">Store these somewhere safe. Each works once if you lose your authenticator device.</p>
        <ul className="mt-4 space-y-1 rounded-md border border-line bg-surface-2 p-4 font-mono text-sm text-ink">
          {recoveryCodes.map((rc) => (
            <li key={rc}>{rc}</li>
          ))}
        </ul>
        <label className="mt-4 flex items-center gap-2 text-ink-muted">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
          I've saved these codes
        </label>
        <button
          disabled={!acknowledged}
          onClick={handleContinue}
          className="mt-4 w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Continue
        </button>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Set up two-factor authentication">
      {loadError && (
        <p role="alert" className="mb-4 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {loadError}
        </p>
      )}
      {qrDataUrl && (
        <img src={qrDataUrl} alt="TOTP QR code" width={200} height={200} className="mx-auto rounded-md border border-line" />
      )}
      {secret && <p className="mt-3 text-center font-mono text-sm text-ink-muted">Manual entry key: {secret}</p>}
      <form onSubmit={handleConfirm} className="mt-6 space-y-4">
        {error && <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="6-digit code"
          aria-label="6-digit code"
          name="totp-code"
          autoComplete="one-time-code"
          inputMode="numeric"
          spellCheck={false}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <button
          type="submit"
          disabled={confirmPending}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Verify
        </button>
      </form>
    </AuthCard>
  );
}
