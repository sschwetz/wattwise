import { selectClass } from "./adminUi";
import { useIsSavingSettings, type MailProvider } from "./useAdminMail";

interface Props {
  providers: MailProvider[];
  selected: string;
  onSelect: (provider: string) => void;
}

export function ProviderPicker({ providers, selected, onSelect }: Props) {
  const saving = useIsSavingSettings();
  return (
    <label className="flex flex-col gap-1 text-sm text-ink">
      Provider
      <select value={selected} disabled={saving} onChange={(e) => onSelect(e.target.value)} className={`${selectClass} max-w-xs`}>
        {providers.map((p) => (
          <option key={p.provider} value={p.provider}>
            {p.available ? p.label : `${p.label} (not available)`}
          </option>
        ))}
      </select>
    </label>
  );
}
