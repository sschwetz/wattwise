import { useEffect, useRef, useState } from "react";
import { ContainersTab } from "./ContainersTab";
import { LicencesTab } from "./LicencesTab";

const TABS = ["Containers", "Licences"] as const;
type Tab = (typeof TABS)[number];

const FOCUSABLE = 'button, a[href], input, [tabindex]';

function trapTab(event: KeyboardEvent, panel: HTMLElement) {
  const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.tabIndex >= 0);
  if (items.length === 0) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!panel.contains(active)) {
    event.preventDefault();
    first.focus();
  } else if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

function useDialogKeys(onClose: () => void, panelRef: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      else if (event.key === "Tab" && panelRef.current) trapTab(event, panelRef.current);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, panelRef]);
}

function TabBar({ tab, onChange }: { tab: Tab; onChange: (tab: Tab) => void }) {
  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = TABS[(TABS.indexOf(tab) + step + TABS.length) % TABS.length];
    onChange(next);
    requestAnimationFrame(() => document.getElementById(`about-tab-${next}`)?.focus());
  }
  return (
    <div role="tablist" aria-label="About sections" onKeyDown={handleKeyDown} className="flex gap-1 border-b border-line">
      {TABS.map((name) => (
        <button
          key={name}
          id={`about-tab-${name}`}
          type="button"
          role="tab"
          aria-selected={tab === name}
          aria-controls="about-panel"
          tabIndex={tab === name ? 0 : -1}
          onClick={() => onChange(name)}
          className={`px-3 py-2 text-sm text-ink hover:bg-surface-2 ${tab === name ? "border-b-2 border-solar font-medium" : "text-ink-muted"}`}
        >
          {name}
        </button>
      ))}
    </div>
  );
}

export function AboutDialog({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("Containers");
  const panelRef = useRef<HTMLDivElement>(null);
  useDialogKeys(onClose, panelRef);
  useEffect(() => panelRef.current?.querySelector<HTMLElement>('[role="tab"][tabindex="0"]')?.focus(), []);

  return (
    <div
      data-testid="about-backdrop"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="about-title"
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[85vh] w-full max-w-3xl flex-col gap-3 rounded-md border border-line bg-surface p-4 text-ink shadow-lg"
      >
        <div className="flex items-center justify-between">
          <h2 id="about-title" className="text-lg font-semibold">About WattWise</h2>
          <button type="button" onClick={onClose} className="rounded-md px-2 py-1 text-sm hover:bg-surface-2">
            Close
          </button>
        </div>
        <TabBar tab={tab} onChange={setTab} />
        <div id="about-panel" role="tabpanel" aria-labelledby={`about-tab-${tab}`} className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {tab === "Containers" ? <ContainersTab /> : <LicencesTab />}
        </div>
      </div>
    </div>
  );
}
