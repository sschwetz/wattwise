import { useMemo, useState } from "react";
import { QueryError } from "../components/QueryError";
import { Loading } from "./Loading";
import { type LicenceDocument, type LicencePackage, useLicences } from "./useLicences";

const keyOf = (p: LicencePackage) => `${p.ecosystem}:${p.name}@${p.version}`;
const optionId = (key: string, index: number) => `licence-option-${index}-${key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;

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
  index: number;
  selected: boolean;
  onSelect: (key: string) => void;
}

function PackageOption({ pkg, index, selected, onSelect }: OptionProps) {
  const key = keyOf(pkg);
  return (
    <li
      id={optionId(key, index)}
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
    const id = optionId(key, packages.findIndex((p) => keyOf(p) === key));
    requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView?.({ block: "nearest" }));
  }
  const activeIndex = packages.findIndex((p) => keyOf(p) === selectedKey);
  const activeId = selectedKey && activeIndex >= 0 ? optionId(selectedKey, activeIndex) : undefined;
  return (
    <ul
      role="listbox"
      aria-label="Packages"
      tabIndex={0}
      aria-activedescendant={activeId}
      onKeyDown={handleKeyDown}
      className="max-h-48 overflow-y-auto rounded-md border border-line sm:max-h-none sm:flex-1"
    >
      {packages.map((p, index) => (
        <PackageOption key={`${keyOf(p)}#${index}`} pkg={p} index={index} selected={keyOf(p) === selectedKey} onSelect={onSelect} />
      ))}
    </ul>
  );
}

function LicenceDetail({ pkg, text }: { pkg: LicencePackage | undefined; text: string | undefined }) {
  if (!pkg) return <p className="p-3 text-sm text-ink-muted">No packages match.</p>;
  return (
    <div className="flex min-h-0 min-w-0 flex-col gap-2 sm:flex-[2]">
      <h3 className="text-sm font-medium text-ink">
        {pkg.name} {pkg.version}
      </h3>
      <p className="text-xs text-ink-muted">
        {pkg.ecosystem} - {pkg.license}
      </p>
      <pre
        tabIndex={0}
        aria-label={`Licence text for ${pkg.name} ${pkg.version}`}
        className="max-h-64 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line p-3 text-xs text-ink sm:max-h-none"
      >
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
  const { data, isError, error, refetch } = useLicences();
  if (isError) {
    return (
      <div className="p-3">
        <QueryError error={error} fallback="Could not load the licence list." onRetry={() => void refetch()} />
      </div>
    );
  }
  if (!data) return <Loading />;
  return <LicenceBrowser doc={data} />;
}
