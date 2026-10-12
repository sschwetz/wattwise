import { type FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useSubmitGuard } from "../hooks/useSubmitGuard";
import { AuthCard } from "../layout/AuthCard";

export function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { isSubmitting, guard } = useSubmitGuard();
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    await guard(async () => {
      try {
        await apiFetch("/api/v1/auth/password-reset/confirm", {
          method: "POST",
          body: JSON.stringify({ token, new_password: newPassword }),
        });
        navigate("/login");
      } catch (err) {
        setError(err instanceof Error ? err.message : "This reset link is invalid or has expired");
      }
    });
  }

  return (
    <AuthCard title="Set a new password">
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
        <input
          type="password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="New password"
          aria-label="New password"
          name="new-password"
          autoComplete="new-password"
          spellCheck={false}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Reset password
        </button>
      </form>
    </AuthCard>
  );
}
