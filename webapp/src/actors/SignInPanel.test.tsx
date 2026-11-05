import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SignInPanel } from "./SignInPanel";

const code = { user_code: "ABCD-1234", verification_uri: "https://example.com/device" };

describe("SignInPanel", () => {
  it("says when a code in the future expires", () => {
    const expires = new Date(Date.now() + 10 * 60_000).toISOString();
    render(<SignInPanel auth={{ ...code, expires_at: expires }} timezone="Australia/Sydney" canEdit />);
    expect(screen.getByText(/The code expires at/)).toBeInTheDocument();
  });

  it("says plainly that a past code has expired", () => {
    const expired = new Date(Date.now() - 60_000).toISOString();
    render(<SignInPanel auth={{ ...code, expires_at: expired }} timezone="Australia/Sydney" canEdit />);
    expect(screen.getByText("This code has expired. A new one will appear shortly.")).toBeInTheDocument();
    expect(screen.queryByText(/The code expires at/)).not.toBeInTheDocument();
  });

  it("shows the owner the code and link", () => {
    const expires = new Date(Date.now() + 10 * 60_000).toISOString();
    render(<SignInPanel auth={{ ...code, expires_at: expires }} timezone="Australia/Sydney" canEdit />);
    expect(screen.getByText("ABCD-1234")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the Actron sign-in page" })).toHaveAttribute("href", code.verification_uri);
  });

  it("shows a viewer a plain line without a code or link", () => {
    const { container } = render(<SignInPanel auth={{ signing_in: true }} timezone="Australia/Sydney" canEdit={false} />);
    expect(screen.getByText("The owner is signing the air conditioner in to Actron.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/ABCD|example\.com/);
  });

  it("hides a code from a user who cannot edit", () => {
    const expires = new Date(Date.now() + 10 * 60_000).toISOString();
    render(<SignInPanel auth={{ ...code, expires_at: expires }} timezone="Australia/Sydney" canEdit={false} />);
    expect(screen.queryByText("ABCD-1234")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
