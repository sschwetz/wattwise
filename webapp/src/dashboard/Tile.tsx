const ACCENT_TEXT: Record<string, string> = {
  solar: "text-solar",
  flow: "text-flow",
  grid: "text-grid",
  danger: "text-danger",
};


const GLOW_CLASS: Record<"solar" | "flow" | "danger", string> = {
  solar: "solar-glow",
  flow: "flow-glow",
  danger: "danger-glow",
};

export function Tile({
  label,
  value,
  accent,
  glow,
  hero = false,
  backgroundImage,
}: {
  label: string;
  value: string;
  accent?: "solar" | "flow" | "grid" | "danger";
  glow?: "solar" | "flow" | "danger";
  hero?: boolean;
  backgroundImage?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-line bg-surface ${glow ? GLOW_CLASS[glow] : ""} ${hero ? "p-6" : "p-4"}`}
      style={backgroundImage ? { backgroundImage } : undefined}
    >
      <p className="text-sm text-ink-muted">{label}</p>
      <p
        className={`mt-1 font-display tabular-nums font-semibold ${hero ? "text-4xl" : "text-2xl"} ${
          accent ? ACCENT_TEXT[accent] : "text-ink"
        }`}
      >
        {value}
      </p>
    </div>
  );
}
