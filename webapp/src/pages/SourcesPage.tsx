import { type FormEvent, type ReactNode, useCallback, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { BackLink } from "../layout/BackLink";
import { RateWindowsPanel } from "../sources/RateWindowsPanel";
import { type Source, useCreateSource, useDeleteSource, useSources, useUpdateSource } from "../sources/useSources";

// The only providers with real collector support today -- kept in sync with
// each collector's own kind (envoy-collector polls kind=energy, etc.).
const PROVIDERS_BY_KIND: Record<string, string[]> = {
  energy: ["envoy"],
  pricing: ["amber", "local"],
  weather: ["weatherlink_live"],
};

function KindAndProviderFields({
  value,
  onChange,
}: {
  value: { kind: string; provider: string };
  onChange: { kind: (kind: string) => void; provider: (provider: string) => void };
}) {
  return (
    <>
      <label className="flex flex-col text-sm text-ink-muted">
        Kind
        <select
          aria-label="Kind"
          value={value.kind}
          onChange={(e) => onChange.kind(e.target.value)}
          className="rounded-md border border-line bg-surface px-3 py-2 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
        >
          <option value="energy">energy</option>
          <option value="pricing">pricing</option>
          <option value="weather">weather</option>
        </select>
      </label>
      <label className="flex flex-col text-sm text-ink-muted">
        Provider
        <select
          aria-label="Provider"
          value={value.provider}
          onChange={(e) => onChange.provider(e.target.value)}
          className="rounded-md border border-line bg-surface px-3 py-2 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
        >
          {PROVIDERS_BY_KIND[value.kind].map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

function CreateSourceForm({ siteId }: { siteId: number }) {
  const [kind, setKind] = useState("energy");
  const [provider, setProvider] = useState(PROVIDERS_BY_KIND.energy[0]);
  const [name, setName] = useState("");
  const createSource = useCreateSource(siteId);

  function handleKindChange(newKind: string) {
    setKind(newKind);
    setProvider(PROVIDERS_BY_KIND[newKind][0]);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createSource.mutate({ kind, provider, name: name.trim() || undefined });
  }

  return (
    <div className="space-y-4">
      {createSource.isError && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {createSource.error instanceof Error ? createSource.error.message : "Failed to create source. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <KindAndProviderFields
          value={{ kind, provider }}
          onChange={{ kind: handleKindChange, provider: setProvider }}
        />
        {provider === "local" ? (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Tariff name"
            aria-label="Tariff name"
            required
            maxLength={100}
            autoComplete="off"
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          />
        ) : (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Label (optional)"
            aria-label="Label (optional)"
            autoComplete="off"
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          />
        )}
        <button
          type="submit"
          disabled={createSource.isPending || (provider === "local" && !name.trim())}
          className="rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Create source
        </button>
      </form>
      {createSource.isSuccess && (
        <p className="rounded-md border border-flow/30 bg-flow-soft p-4 text-sm text-ink">
          Source created — the matching collector will start collecting for it automatically within a few minutes.
        </p>
      )}
    </div>
  );
}

function ConfigFormShell({
  onSubmit,
  isPending,
  error,
  children,
}: {
  onSubmit: (event: FormEvent) => void;
  isPending: boolean;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-2 border-t border-line p-3 text-sm">
      {error && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-danger">
          {error}
        </p>
      )}
      {children}
      <button
        type="submit"
        disabled={isPending}
        className="self-start rounded-md bg-solar px-2 py-1 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
      >
        Save config
      </button>
    </form>
  );
}

interface ConfigFieldSpec {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "password";
}

function ConfigField({ field }: { field: ConfigFieldSpec }) {
  const { label, value, onChange, type = "text" } = field;
  return (
    <label className="flex flex-col text-ink-muted">
      {label}
      <input
        type={type}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
        spellCheck={false}
        className="rounded-md border border-line bg-surface px-2 py-1 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
      />
    </label>
  );
}

// Matches CERT_REPIN_WINDOW in envoy-collector: inside it, the collector
// accepts the Envoy's renewed certificate on its own.
const CERT_REPIN_WINDOW_DAYS = 100;
const MS_PER_DAY = 24 * 60 * 60 * 1000;
const CERT_DATE_FORMAT = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });

function CertificateDetails({ expiresAt, serial }: { expiresAt: string; serial?: string }) {
  const [now] = useState(() => Date.now());
  const expires = new Date(expiresAt);
  if (Number.isNaN(expires.getTime())) return null;
  const daysLeft = Math.floor((expires.getTime() - now) / MS_PER_DAY);
  const remaining = daysLeft < 0 ? `expired ${-daysLeft} days ago` : `${daysLeft} days left`;
  return (
    <div className="flex flex-col text-ink-muted">
      {serial && (
        <>
          <span>Certificate serial</span>
          <p className="font-mono text-ink">{serial}</p>
        </>
      )}
      <span>Certificate expires</span>
      <p className="text-ink">
        {CERT_DATE_FORMAT.format(expires)} ({remaining})
      </p>
      <p>
        The Envoy renews its certificate within {CERT_REPIN_WINDOW_DAYS} days of expiry
        {daysLeft <= CERT_REPIN_WINDOW_DAYS ? ". The collector will accept the new one." : ", not before."}
      </p>
    </div>
  );
}

function EnvoyConfigForm({
  config,
  onSave,
  isPending,
}: {
  config: Record<string, string | boolean>;
  onSave: (config: Record<string, string | boolean>) => void;
  isPending: boolean;
}) {
  const [host, setHost] = useState((config.host as string) ?? "");
  const [token, setToken] = useState((config.token as string) ?? "");
  const [username, setUsername] = useState((config.username as string) ?? "");
  const [password, setPassword] = useState((config.password as string) ?? "");
  const [serialNum, setSerialNum] = useState((config.serial_num as string) ?? "");
  const [invertConsumptionCt, setInvertConsumptionCt] = useState(Boolean(config.invert_consumption_ct));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    // Sent only when filled in: an empty box means "not known yet", and the
    // collector fills the serial in itself once it can reach the Envoy.
    const serial = serialNum.trim();
    onSave({ host, token, username, password, ...(serial && { serial_num: serial }), invert_consumption_ct: invertConsumptionCt });
  }

  return (
    <ConfigFormShell onSubmit={handleSubmit} isPending={isPending}>
      <ConfigField field={{ label: "Host", value: host, onChange: setHost }} />
      <ConfigField field={{ label: "Token", value: token, onChange: setToken, type: "password" }} />
      <ConfigField field={{ label: "Enlighten email", value: username, onChange: setUsername }} />
      <ConfigField field={{ label: "Enlighten password", value: password, onChange: setPassword, type: "password" }} />
      <p className="text-ink-muted">
        Optional. With both filled in, the collector can renew the token automatically, so Token can stay empty.
      </p>
      <ConfigField field={{ label: "Serial number", value: serialNum, onChange: setSerialNum }} />
      <p className="text-ink-muted">
        The collector fills this in when it can read it from the Envoy. If it stays empty, enter the serial number
        printed on the Envoy or shown in Enlighten. The collector needs it to renew the token.
      </p>
      {typeof config.cert_expires_at === "string" && (
        <CertificateDetails
          expiresAt={config.cert_expires_at}
          serial={typeof config.cert_serial === "string" ? config.cert_serial : undefined}
        />
      )}
      <label className="flex items-center gap-2 text-ink-muted">
        <input
          type="checkbox"
          checked={invertConsumptionCt}
          onChange={(e) => setInvertConsumptionCt(e.target.checked)}
        />
        My consumption numbers are flipped (importing shows as negative)
      </label>
    </ConfigFormShell>
  );
}

function AmberConfigForm({
  config,
  onSave,
  isPending,
}: {
  config: Record<string, string | boolean>;
  onSave: (config: Record<string, string | boolean>) => void;
  isPending: boolean;
}) {
  const [amberSiteId, setAmberSiteId] = useState((config.amber_site_id as string) ?? "");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSave({ amber_site_id: amberSiteId });
  }

  return (
    <ConfigFormShell onSubmit={handleSubmit} isPending={isPending}>
      <ConfigField field={{ label: "Amber site ID", value: amberSiteId, onChange: setAmberSiteId }} />
    </ConfigFormShell>
  );
}

function WeatherlinkConfigForm({
  config,
  onSave,
  isPending,
}: {
  config: Record<string, string | boolean>;
  onSave: (config: Record<string, string | boolean>) => void;
  isPending: boolean;
}) {
  const [host, setHost] = useState((config.host as string) ?? "");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSave({ host });
  }

  return (
    <ConfigFormShell onSubmit={handleSubmit} isPending={isPending}>
      <ConfigField field={{ label: "Host", value: host, onChange: setHost }} />
    </ConfigFormShell>
  );
}

function RawConfigForm({
  config,
  onSave,
  isPending,
}: {
  config: Record<string, string | boolean>;
  onSave: (config: Record<string, string | boolean>) => void;
  isPending: boolean;
}) {
  const [text, setText] = useState(JSON.stringify(config, null, 2));
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      onSave(JSON.parse(text));
    } catch {
      setError("Invalid JSON.");
    }
  }

  return (
    <ConfigFormShell onSubmit={handleSubmit} isPending={isPending} error={error}>
      <label className="flex flex-col text-ink-muted">
        Config (JSON)
        <textarea
          aria-label="Config (JSON)"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          className="rounded-md border border-line bg-surface px-2 py-1 font-mono text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
        />
      </label>
    </ConfigFormShell>
  );
}

const CONFIG_FORMS: Record<
  string,
  (props: { config: Record<string, string | boolean>; onSave: (config: Record<string, string | boolean>) => void; isPending: boolean }) => ReactNode
> = {
  envoy: EnvoyConfigForm,
  amber: AmberConfigForm,
  weatherlink_live: WeatherlinkConfigForm,
};

function SourceConfigForm({
  source,
  onSave,
  isPending,
}: {
  source: Source;
  onSave: (config: Record<string, string | boolean>) => void;
  isPending: boolean;
}) {
  const Form = CONFIG_FORMS[source.provider] ?? RawConfigForm;
  return <Form config={source.config} onSave={onSave} isPending={isPending} />;
}

interface SourceRowActions {
  onToggle: (source: Source) => void;
  onSaveConfig: (source: Source, config: Record<string, string | boolean>) => void;
  onSaveName: (source: Source, name: string) => void;
  onDelete: (source: Source) => void;
}

function SourceLabelField({
  value,
  onSave,
  isPending,
}: {
  value: string;
  onSave: (name: string) => void;
  isPending: boolean;
}) {
  const [draft, setDraft] = useState(value);

  return (
    <div className="flex items-end gap-2 pt-1 text-sm">
      <label className="flex flex-col text-ink-muted">
        Label
        <input
          aria-label="Label"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          autoComplete="off"
          className="rounded-md border border-line bg-surface px-2 py-1 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
        />
      </label>
      <button onClick={() => onSave(draft)} disabled={isPending} className="text-ink-muted underline hover:text-ink disabled:opacity-50">
        Save label
      </button>
    </div>
  );
}

function SourceRow({
  source,
  actions,
  isPending,
}: {
  source: Source;
  actions: SourceRowActions;
  isPending: boolean;
}) {
  const [editingConfig, setEditingConfig] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const isLocal = source.provider === "local";

  return (
    <li className="rounded-xl border border-line bg-surface p-4">
      <div className="flex justify-between">
        <span className="text-ink">
          {source.kind} — <span>{source.provider}</span>
          {source.name && <span className="text-ink-muted"> — {source.name}</span>}
        </span>
        <span className="flex gap-3 text-sm">
          {!isLocal && (
            <button onClick={() => setEditingConfig((v) => !v)} className="text-ink-muted underline hover:text-ink">
              Edit config
            </button>
          )}
          <button
            onClick={() => actions.onToggle(source)}
            disabled={isPending}
            className="text-ink-muted underline hover:text-ink disabled:opacity-50"
          >
            {source.enabled ? "Disable" : "Enable"}
          </button>
          {confirmingDelete ? (
            <>
              <button
                onClick={() => actions.onDelete(source)}
                disabled={isPending}
                className="font-semibold text-danger underline hover:text-danger/80 disabled:opacity-50"
              >
                Confirm delete
              </button>
              <button onClick={() => setConfirmingDelete(false)} className="text-ink-muted underline hover:text-ink">
                Cancel
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirmingDelete(true)}
              disabled={isPending}
              className="text-ink-muted underline hover:text-ink disabled:opacity-50"
            >
              Delete
            </button>
          )}
        </span>
      </div>
      <SourceLabelField
        value={source.name ?? ""}
        onSave={(name) => actions.onSaveName(source, name)}
        isPending={isPending}
      />
      {editingConfig && (
        <SourceConfigForm
          source={source}
          onSave={(config) => actions.onSaveConfig(source, config)}
          isPending={isPending}
        />
      )}
      {isLocal && <RateWindowsPanel siteId={source.site_id} sourceId={source.id} />}
    </li>
  );
}

export function SourcesPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);
  const { data: sources } = useSources(parsedSiteId);
  const updateSource = useUpdateSource(parsedSiteId);
  const deleteSource = useDeleteSource(parsedSiteId);

  const handleToggle = useCallback(
    (source: Source) => {
      updateSource.mutate({ sourceId: source.id, enabled: !source.enabled });
    },
    [updateSource],
  );

  const handleSaveConfig = useCallback(
    (source: Source, config: Record<string, string | boolean>) => {
      updateSource.mutate({ sourceId: source.id, config });
    },
    [updateSource],
  );

  const handleSaveName = useCallback(
    (source: Source, name: string) => {
      updateSource.mutate({ sourceId: source.id, name });
    },
    [updateSource],
  );

  const handleDelete = useCallback(
    (source: Source) => {
      deleteSource.mutate(source.id);
    },
    [deleteSource],
  );

  const actions = useMemo(
    () => ({ onToggle: handleToggle, onSaveConfig: handleSaveConfig, onSaveName: handleSaveName, onDelete: handleDelete }),
    [handleToggle, handleSaveConfig, handleSaveName, handleDelete],
  );

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-2">
        <BackLink to={`/sites/${siteId}`} />
        <h1 className="font-display text-2xl font-semibold text-ink">Sources</h1>
      </div>
      {updateSource.isError && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {updateSource.error instanceof Error ? updateSource.error.message : "Failed to update source. Please try again."}
        </p>
      )}
      {deleteSource.isError && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {deleteSource.error instanceof Error ? deleteSource.error.message : "Failed to delete source. Please try again."}
        </p>
      )}
      {sources && sources.length === 0 && <p className="text-ink-muted">No sources yet.</p>}
      <ul className="space-y-3">
        {(sources ?? []).map((source) => (
          <SourceRow
            key={source.id}
            source={source}
            actions={actions}
            isPending={updateSource.isPending || deleteSource.isPending}
          />
        ))}
      </ul>
      <CreateSourceForm siteId={parsedSiteId} />
    </div>
  );
}
