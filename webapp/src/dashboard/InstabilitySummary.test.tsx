import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InstabilitySummary } from "./InstabilitySummary";

const MINUTE_MS = 60_000;
const at = (iso: string) => new Date(iso).getTime();

describe("InstabilitySummary", () => {
  it("renders nothing when there are no windows", () => {
    const { container } = render(<InstabilitySummary windows={[]} timeZone="UTC" />);

    expect(container).toBeEmptyDOMElement();
  });

  it("names the time range of a single window", () => {
    const from = at("2026-10-20T06:30:00Z");
    render(<InstabilitySummary windows={[{ from, to: from + 150 * MINUTE_MS, level: "spike" }]} timeZone="UTC" />);

    expect(screen.getByText("Price instability expected 06:30 to 09:00")).toBeInTheDocument();
  });

  it("joins several windows with 'and'", () => {
    const first = at("2026-10-20T06:30:00Z");
    const second = at("2026-10-20T08:15:00Z");
    render(
      <InstabilitySummary
        windows={[
          { from: first, to: first + 30 * MINUTE_MS, level: "potential" },
          { from: second, to: second + 45 * MINUTE_MS, level: "wide-band" },
        ]}
        timeZone="UTC"
      />,
    );

    expect(screen.getByText("Price instability expected 06:30 to 07:00 and 08:15 to 09:00")).toBeInTheDocument();
  });

  it("formats times in the given time zone", () => {
    const from = at("2026-10-20T06:30:00Z"); // 17:30 in Sydney (UTC+11 in October)
    render(
      <InstabilitySummary windows={[{ from, to: from + 30 * MINUTE_MS, level: "spike" }]} timeZone="Australia/Sydney" />,
    );

    expect(screen.getByText("Price instability expected 17:30 to 18:00")).toBeInTheDocument();
  });
});
