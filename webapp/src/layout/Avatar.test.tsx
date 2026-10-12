import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Avatar } from "./Avatar";

describe("Avatar", () => {
  it("shows initials from the first two words of a display name", () => {
    render(<Avatar name="Ada Lovelace" email="ada@example.com" />);
    expect(screen.getByText("AL")).toBeInTheDocument();
  });

  it("falls back to the email's first letter when no name is set", () => {
    render(<Avatar name={null} email="ada@example.com" />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("falls back to the email's first letter when the name is blank", () => {
    render(<Avatar name="   " email="ada@example.com" />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("uses a single initial for a one-word name", () => {
    render(<Avatar name="Ada" email="ada@example.com" />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("picks the same color for the same input across renders", () => {
    const { unmount } = render(<Avatar name="Ada Lovelace" email="ada@example.com" />);
    const firstClassName = screen.getByText("AL").className;
    unmount();

    render(<Avatar name="Ada Lovelace" email="ada@example.com" />);
    expect(screen.getByText("AL").className).toBe(firstClassName);
  });
});
