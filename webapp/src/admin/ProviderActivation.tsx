import { ConfirmAction } from "./ConfirmAction";
import { MutationError, mutedClass, primaryButtonClass } from "./adminUi";
import { useActivateProvider, useDeactivateProvider, useIsSavingSettings, type MailProvider } from "./useAdminMail";

interface Props {
  provider: MailProvider;
  /** The provider that is active now, if any. */
  active: MailProvider | undefined;
}

function ActivateButton({ provider, active }: Props) {
  const activate = useActivateProvider(provider.provider);
  const replaces = active && active.provider !== provider.provider ? active : undefined;
  const saving = useIsSavingSettings();
  const disabled = !provider.available || activate.isPending || saving;
  return (
    <div className="space-y-2">
      {replaces ? (
        <ConfirmAction
          label="Activate"
          title={`Activate ${provider.label}?`}
          body={`Activating ${provider.label} turns off ${replaces.label}. Email will go through ${provider.label} from now on.`}
          confirmLabel={`Activate ${provider.label}`}
          disabled={disabled}
          onConfirm={() => activate.mutate()}
        />
      ) : (
        <button type="button" disabled={disabled} onClick={() => activate.mutate()} className={primaryButtonClass}>
          Activate
        </button>
      )}
      {!provider.available && <p className={mutedClass}>The mail actor reports this provider as not available.</p>}
      <MutationError error={activate.error} fallback="Could not activate the provider. Please try again." />
    </div>
  );
}

function DeactivateButton({ provider }: { provider: MailProvider }) {
  const deactivate = useDeactivateProvider(provider.provider);
  const saving = useIsSavingSettings();
  return (
    <div className="space-y-2">
      <ConfirmAction
        label="Deactivate"
        title={`Deactivate ${provider.label}?`}
        body="Email goes back through the built-in mail relay."
        confirmLabel="Deactivate"
        disabled={deactivate.isPending || saving}
        onConfirm={() => deactivate.mutate()}
      />
      <MutationError error={deactivate.error} fallback="Could not deactivate the provider. Please try again." />
    </div>
  );
}

export function ProviderActivation({ provider, active }: Props) {
  if (provider.is_active) return <DeactivateButton provider={provider} />;
  return <ActivateButton provider={provider} active={active} />;
}
