import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunHistory } from "./RunHistory";
import type { ActorRun, RunOutcome } from "./types";

function run(id: number, overrides: Partial<ActorRun> = {}): ActorRun {
  return {
    id, schedule_id: 2, kind: "action", trigger_at: "2026-11-05T03:00:00Z", started_at: null, finished_at: null,
    outcome: "ok", message: null, attempts: 1, parent_run_id: null, snapshot: null, steps: null, ...overrides,
  };
}

const odd = (o: object) => o as Partial<ActorRun>;
const props = { timezone: "Australia/Sydney", labels: { summary: "Turn the system off", of: (id: string) => ({ system_off: "Turn the system off", set_zone_temperature: "Set zone" })[id] ?? id } };

beforeEach(() => vi.useFakeTimers({ now: new Date("2026-11-05T03:00:00Z") }));
afterEach(() => vi.useRealTimers());

describe("RunHistory", () => {
  it("labels each outcome with the right text", () => {
    const outcomes: RunOutcome[] = ["ok", "skipped", "failed", "overridden", "pending", "running"];
    render(<RunHistory {...props} runs={outcomes.map((outcome, i) => run(i + 1, { outcome }))} />);
    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows.map((r) => within(r).getByTestId("outcome").textContent)).toEqual([
      "OK", "Skipped", "Failed", "You took over", "Queued", "Running",
    ]);
  });

  it("uses the capabilities label for actions, with the message", () => {
    render(<RunHistory {...props} runs={[run(1, { message: "Was 22" }), run(2, { kind: "restore" })]} />);
    expect(screen.getByText("Turn the system off: Was 22")).toBeInTheDocument();
    expect(screen.getByText("Restored")).toBeInTheDocument();
  });

  it("shows attempts on a failed run", () => {
    render(<RunHistory {...props} runs={[run(1, { outcome: "failed", attempts: 3, message: "No response" })]} />);
    expect(screen.getByText("Turn the system off: No response (after 3 tries)")).toBeInTheDocument();
  });

  it("renders times in the site zone with Today and Yesterday", () => {
    const runs = [run(1, { trigger_at: "2026-11-04T23:30:00Z" }), run(2, { trigger_at: "2026-11-04T05:00:00Z" })];
    render(<RunHistory {...props} runs={runs} />);
    const times = screen.getAllByText(/Today|Yesterday/);
    expect(times.map((t) => t.textContent)).toEqual(["Today 10:30", "Yesterday 16:00"]);
    expect(times[0]).toHaveAttribute("datetime", "2026-11-04T23:30:00Z");
  });

  it("never renders undefined for an unknown kind or outcome", () => {
    const runs = [run(1, odd({ kind: "migrate", outcome: "cancelled", message: "m" }))];
    render(<RunHistory {...props} runs={runs} />);
    expect(screen.getByText("Run: m")).toBeInTheDocument();
    expect(screen.getByTestId("outcome")).toHaveTextContent("cancelled");
    expect(document.body.textContent).not.toContain("undefined");
  });

  it("shows an empty state", () => {
    render(<RunHistory {...props} runs={[]} />);
    expect(screen.getByText("No runs yet.")).toBeInTheDocument();
  });

  it("says when the list is at the 50 run limit", () => {
    const runs = Array.from({ length: 50 }, (_, i) => run(i + 1));
    render(<RunHistory {...props} runs={runs} />);
    expect(screen.getByText("Showing the 50 newest runs.")).toBeInTheDocument();
  });

  it("has no limit note below 50", () => {
    render(<RunHistory {...props} runs={[run(1)]} />);
    expect(screen.queryByText(/newest runs/)).not.toBeInTheDocument();
  });

  it("history lists one line per step, failures first", () => {
    const steps = [
      { index: 0, action_id: "system_off", params: {}, outcome: "ok" as const, message: null, attempts: 1 },
      { index: 1, action_id: "set_zone_temperature", params: {}, outcome: "failed" as const, message: "Out of range", attempts: 2 },
    ];
    render(<RunHistory {...props} runs={[run(1, { outcome: "partial", message: "1 of 2 ran", steps })]} />);
    const lines = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(lines.map((l) => l.textContent)).toEqual(["Set zone: Out of range", "Turn the system off"]);
    expect(screen.getByTestId("outcome")).toHaveTextContent("Partly done");
  });

  it("history copes with a run that has no steps", () => {
    render(<RunHistory {...props} runs={[run(1, { steps: null }), run(2, { steps: [] })]} />);
    expect(screen.queryByRole("list", { name: "Steps" })).not.toBeInTheDocument();
    expect(screen.getAllByTestId("outcome")).toHaveLength(2);
  });
});
