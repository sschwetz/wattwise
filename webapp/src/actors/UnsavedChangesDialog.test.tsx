import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { UnsavedChangesDialog } from "./UnsavedChangesDialog";

function renderDialog() {
  const onKeep = vi.fn();
  const onDiscard = vi.fn();
  render(<UnsavedChangesDialog onKeep={onKeep} onDiscard={onDiscard} />);
  return { onKeep, onDiscard };
}

describe("UnsavedChangesDialog", () => {
  it("is a labelled modal alert dialog", () => {
    renderDialog();
    const dialog = screen.getByRole("alertdialog", { name: "Discard unsaved changes?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("You have unsaved changes to this schedule. If you leave now, they are lost.");
  });

  it("focuses Keep editing first", () => {
    renderDialog();
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
  });

  it("keeps focus inside the dialog", async () => {
    renderDialog();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Discard changes" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Discard changes" })).toHaveFocus();
  });

  it("calls onKeep for the button, Escape and the backdrop", async () => {
    const { onKeep, onDiscard } = renderDialog();
    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByTestId("unsaved-changes-backdrop"));
    expect(onKeep).toHaveBeenCalledTimes(3);
    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("does not treat a click inside the panel as a backdrop click", async () => {
    const { onKeep } = renderDialog();
    await userEvent.click(screen.getByText("Discard unsaved changes?"));
    expect(onKeep).not.toHaveBeenCalled();
  });

  it("calls onDiscard for Discard changes", async () => {
    const { onKeep, onDiscard } = renderDialog();
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(onDiscard).toHaveBeenCalledTimes(1);
    expect(onKeep).not.toHaveBeenCalled();
  });

  it("returns focus to the control that was focused before it opened", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Leave</button>
          {open && <UnsavedChangesDialog onKeep={() => setOpen(false)} onDiscard={() => setOpen(false)} />}
        </>
      );
    }
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave" })).toHaveFocus();
  });
});
