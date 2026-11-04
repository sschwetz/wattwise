import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SignInPanel } from "./SignInPanel";

const code = { user_code: "ABCD-1234", verification_uri: "https://example.com/device" };

describe("SignInPanel", () => {
  it("says when a code in the future expires", () => {
    const expires = new Date(Date.now() + 10 * 60_000).toISOString();
    render(<SignInPanel auth={{ ...code, expires_at: expires }} timezone="Australia/Sydney" />);
    expect(screen.getByText(/The code expires at/)).toBeInTheDocument();
  });

  it("says plainly that a past code has expired", () => {
    const expired = new Date(Date.now() - 60_000).toISOString();
    render(<SignInPanel auth={{ ...code, expires_at: expired }} timezone="Australia/Sydney" />);
    expect(screen.getByText("This code has expired. A new one will appear shortly.")).toBeInTheDocument();
    expect(screen.queryByText(/The code expires at/)).not.toBeInTheDocument();
  });
});
