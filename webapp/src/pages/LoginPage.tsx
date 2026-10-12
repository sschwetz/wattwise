import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useSubmitGuard } from "../hooks/useSubmitGuard";
import { AuthCard } from "../layout/AuthCard";

interface LoginChallenge {
  status: "totp_setup_required" | "totp_required";
  setup_token: string | null;
  mfa_token: string | null;
}

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { isSubmitting, guard } = useSubmitGuard();
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    await guard(async () => {
      try {
        const result = await apiFetch<LoginChallenge>("/api/v1/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        });
        if (result.status === "totp_setup_required") {
          navigate("/totp-setup", { state: { setupToken: result.setup_token } });
        } else {
          navigate("/totp-verify", { state: { mfaToken: result.mfa_token } });
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Login failed");
      }
    });
  }

  return (
    <AuthCard title="Log in">
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          aria-label="Email"
          name="email"
          autoComplete="email"
          spellCheck={false}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          aria-label="Password"
          name="password"
          autoComplete="current-password"
          spellCheck={false}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Log in
        </button>
      </form>
      <p className="mt-6 flex justify-between text-sm text-ink-muted">
        <Link to="/forgot-password" className="hover:text-ink">
          Forgot password?
        </Link>
        <Link to="/register" className="hover:text-ink">
          Create an account
        </Link>
      </p>
    </AuthCard>
  );
}
