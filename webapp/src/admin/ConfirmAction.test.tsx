import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmAction } from "./ConfirmAction";

describe("ConfirmAction", () => {
  it("describes the dialog with its body text", async () => {
    render(
      <ConfirmAction label="Remove" title="Remove it?" body="This cannot be undone." confirmLabel="Remove it" onConfirm={vi.fn()} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("alertdialog", { name: "Remove it?" });
    expect(dialog).toHaveAccessibleDescription("This cannot be undone.");
  });
});
