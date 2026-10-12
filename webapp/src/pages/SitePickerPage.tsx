import { type FormEvent, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { AuthCard } from "../layout/AuthCard";
import { useCreateSite, useSites } from "../sites/useSites";

function OnboardingForm() {
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("");
  const createSite = useCreateSite();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createSite.mutate({ name, timezone });
  }

  if (createSite.isSuccess) {
    return <Navigate to={`/sites/${createSite.data.id}`} replace />;
  }

  return (
    <AuthCard title="Create your first site" fill>
      <form onSubmit={handleSubmit} className="space-y-4">
        {createSite.isError && (
          <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
            {createSite.error instanceof Error ? createSite.error.message : "Failed to create site. Please try again."}
          </p>
        )}
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Site name"
          aria-label="Site name"
          name="site-name"
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <input
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          placeholder="Timezone (e.g. Australia/Sydney)"
          aria-label="Timezone"
          name="timezone"
          autoComplete="off"
          spellCheck={false}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <button
          type="submit"
          disabled={createSite.isPending}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Create site
        </button>
      </form>
    </AuthCard>
  );
}

export function SitePickerPage() {
  const { data: sites, isLoading } = useSites();

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center bg-bg">
        <p className="text-ink-muted">Loading your sites…</p>
      </div>
    );
  }
  if (!sites || sites.length === 0) {
    return <OnboardingForm />;
  }
  if (sites.length === 1) {
    return <Navigate to={`/sites/${sites[0].id}`} replace />;
  }
  return (
    <AuthCard title="Choose a site" fill>
      <ul className="space-y-2">
        {sites.map((site) => (
          <li key={site.id}>
            <Link
              to={`/sites/${site.id}`}
              className="block rounded-md border border-line px-3 py-2 text-ink hover:border-solar hover:bg-surface-2"
            >
              {site.name}
            </Link>
          </li>
        ))}
      </ul>
    </AuthCard>
  );
}
