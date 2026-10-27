interface Selection {
  selected: number[];
  onChange: (selected: number[]) => void;
}

export function CheckboxGroup({ legend, options, selection }: { legend: string; options: { value: number; label: string }[]; selection: Selection }) {
  const { selected, onChange } = selection;
  function toggle(value: number) {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value].sort((a, b) => a - b));
  }
  return (
    <fieldset className="col-span-full flex flex-wrap gap-x-4 gap-y-2 text-sm text-ink-muted">
      <legend className="mb-1.5">{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className="flex items-center gap-1.5 text-ink">
          <input type="checkbox" className="accent-solar" checked={selected.includes(option.value)} onChange={() => toggle(option.value)} />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}
