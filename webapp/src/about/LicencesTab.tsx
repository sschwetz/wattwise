import { useMemo, useState } from "react";
import { type LicenceDocument, type LicencePackage, useLicences } from "./useLicences";

const keyOf = (p: LicencePackage) => `${p.ecosystem}:${p.name}@${p.version}`;

interface ListProps {
  packages: LicencePackage[];
  selectedKey: string | undefined;
  onSelect: (key: string) => void;
}

function nextKey(packages: LicencePackage[], current: string | undefined, key: string): string | null {
  const index = packages.findIndex((p) => keyOf(p) === current);
  const target =
    key === "ArrowDown" ? index + 1 : key === "ArrowUp" ? index - 1 : key === "Home" ? 0 : key === "End" ? packages.length - 1 : null;
  if (target === null || packages.length === 0) return null;
  return keyOf(packages[Math.min(Math.max(target, 0), packages.length - 1)]);
}

interface OptionProps {
  pkg: LicencePackage;
  selected: boolean;
  onSelect: (key: string) => void;
}

function PackageOption({ pkg, selected, onSelect }: OptionProps) {
  const key = keyOf(pkg);
  return (
    <li
      id={optionId(key)}
      role="option"
      aria-selected={selected}
      onClick={() => onSelect(key)}
      className={`cursor-pointer px-3 py-1.5 text-sm text-ink hover:bg-surface-2 ${selected ? "bg-surface-2 font-medium" : ""}`}
    >
      {pkg.name} {pkg.version}
    </li>
  );
}

function PackageList({ packages, selectedKey, onSelect }: ListProps) {
  function handleKeyDown(event: React.KeyboardEvent<HTMLUListElement>) {
    const key = nextKey(packages, selectedKey, event.key);
    if (key === null) return;
    event.preventDefault();
    onSelect(key);
    requestAnimationFrame(() => document.getElementById(optionId(key))?.scrollIntoView?.({ block: "nearest" }));
  }
  return (
    <ul
      role="listbox"
      aria-label="Packages"
      tabIndex={0}
      aria-activedescendant={selectedKey ? optionId(selectedKey) : undefined}
      onKeyDown={handleKeyDown}
      className="max-h-48 overflow-y-auto rounded-md border border-line sm:max-h-none sm:flex-1"
    >
      {packages.map((p) => (
        <PackageOption key={keyOf(p)} pkg={p} selected={keyOf(p) === selectedKey} onSelect={onSelect} />
      ))}
    </ul>
  );
}

const optionId = (key: string) => `licence-option-${key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;

function LicenceDetail({ pkg, text }: { pkg: LicencePackage | undefined; text: string | undefined }) {
  if (!pkg) return <p className="p-3 text-sm text-ink-muted">No packages match.</p>;
  return (
    <div className="flex min-h-0 flex-col gap-2 sm:flex-[2]">
      <h3 className="text-sm font-medium text-ink">
        {pkg.name} {pkg.version}
      </h3>
      <p className="text-xs text-ink-muted">
        {pkg.ecosystem} - {pkg.license}
      </p>
      <pre className="max-h-64 flex-1 overflow-auto whitespace-pre-wrap rounded-md border border-line p-3 text-xs text-ink sm:max-h-none">
        {text ?? "No licence text available."}
      </pre>
    </div>
  );
}

function LicenceBrowser({ doc }: { doc: LicenceDocument }) {
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<string>();
  const matches = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return doc.packages.filter((p) => p.name.toLowerCase().includes(needle));
  }, [doc.packages, filter]);
  const selected = matches.find((p) => keyOf(p) === picked) ?? matches[0];
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <input
        type="text"
        aria-label="Filter packages"
        placeholder="Filter packages"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink"
      />
      <div className="flex min-h-0 flex-1 flex-col gap-3 sm:flex-row">
        <PackageList packages={matches} selectedKey={selected && keyOf(selected)} onSelect={setPicked} />
        <LicenceDetail pkg={selected} text={selected?.text_id ? doc.texts[selected.text_id] : undefined} />
      </div>
    </div>
  );
}

export function LicencesTab() {
  const { data, isError } = useLicences(true);
  if (isError) return <p role="alert" className="p-3 text-sm text-red-400">Could not load the licence list.</p>;
  if (!data) return <p className="p-3 text-sm text-ink-muted">Loading...</p>;
  return <LicenceBrowser doc={data} />;
}
