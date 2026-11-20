import { useState } from "react";
import { QueryError } from "../components/QueryError";
import { MailSummaryLine } from "./MailSummaryLine";
import { ProviderActivation } from "./ProviderActivation";
import { ProviderFieldsForm } from "./ProviderFieldsForm";
import { ProviderPicker } from "./ProviderPicker";
import { TestEmailRow } from "./TestEmailRow";
import { headingClass, mutedClass, sectionClass } from "./adminUi";
import { useMailProviders, type MailProvider } from "./useAdminMail";

function ActiveLine({ active }: { active: MailProvider | undefined }) {
  return (
    <p className="text-sm text-ink">
      {active ? `Email goes through ${active.label}.` : "No provider is active. Email goes through the built-in mail relay."}
    </p>
  );
}

function ProviderSection({ providers }: { providers: MailProvider[] }) {
  const active = providers.find((p) => p.is_active);
  const [choice, setChoice] = useState<string | null>(null);
  const provider = providers.find((p) => p.provider === choice) ?? active ?? providers[0];
  return (
    <section className={sectionClass}>
      <h2 className={headingClass}>Provider</h2>
      <ActiveLine active={active} />
      <ProviderPicker providers={providers} selected={provider.provider} onSelect={setChoice} />
      <ProviderFieldsForm key={provider.provider} provider={provider} />
      <ProviderActivation provider={provider} active={active} />
      {/* Keyed so the last test result does not follow a change of provider. */}
      <TestEmailRow key={`test-${provider.provider}`} hasActive={active !== undefined} />
    </section>
  );
}

export function EmailTab() {
  const { data: providers, isLoading, isError, error, refetch } = useMailProviders();
  if (isError && !providers) {
    return <QueryError error={error} fallback="Could not load the email providers." onRetry={() => void refetch()} />;
  }
  if (isLoading || !providers) return <p className={mutedClass}>Loading...</p>;
  return (
    <div className="space-y-4">
      <MailSummaryLine />
      {providers.length === 0 ? (
        <p className={mutedClass}>
          No email provider has been declared yet. Start the mail-actor container; it lists the providers it supports when it starts.
        </p>
      ) : (
        <ProviderSection providers={providers} />
      )}
    </div>
  );
}
