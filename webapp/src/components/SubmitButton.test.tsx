import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SubmitButton } from "./SubmitButton";

const idle = { isPending: false, isSuccess: false, submittedAt: 0 };

describe("SubmitButton", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("disables the button and shows Saving… while pending", () => {
    render(<SubmitButton mutation={{ ...idle, isPending: true }} buttonClass="b">Save</SubmitButton>);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");
  });

  it("shows Saved after success, then hides it", () => {
    render(<SubmitButton mutation={{ ...idle, isSuccess: true, submittedAt: 1 }} buttonClass="b">Save</SubmitButton>);
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
    act(() => {
      vi.advanceTimersByTime(4100);
    });
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("shows Saved again for a later save", () => {
    const { rerender } = render(
      <SubmitButton mutation={{ ...idle, isSuccess: true, submittedAt: 1 }} buttonClass="b">Save</SubmitButton>,
    );
    act(() => {
      vi.advanceTimersByTime(4100);
    });
    rerender(<SubmitButton mutation={{ ...idle, isSuccess: true, submittedAt: 2 }} buttonClass="b">Save</SubmitButton>);
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
  });
});
