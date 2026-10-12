import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useSubmitGuard } from "../hooks/useSubmitGuard";
import { AuthCard } from "../layout/AuthCard";

export function RegisterPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [registered, setRegistered] = useState(false);
  const { isSubmitting, guard } = useSubmitGuard();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    await guard(async () => {
      try {
        await apiFetch("/api/v1/auth/register", {
          method: "POST",
          body: JSON.stringify({ email }),
        });
        // The response is deliberately the same whether this email was new
        // or already registered -- see core-api's /auth/register. Don't
        // navigate to /login: the account (if new) isn't usable until the
        // confirmation link is clicked and a password chosen there -- see
        // ConfirmRegistrationPage.
        setRegistered(true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Registration failed");
      }
    });
  }

  if (registered) {
    return (
      <AuthCard title="Check your email">
        <p className="text-ink-muted">
          If {email} isn't already registered, we've sent a link to confirm your account. Click it, then log in.
        </p>
        <Link to="/login" className="mt-6 block text-sm text-ink-muted hover:text-ink">
          Back to login
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Create an account">
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
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Create account
        </button>
      </form>
      <p className="mt-6 text-sm text-ink-muted">
        <Link to="/login" className="hover:text-ink">
          Already have an account? Log in
        </Link>
      </p>
    </AuthCard>
  );
}
