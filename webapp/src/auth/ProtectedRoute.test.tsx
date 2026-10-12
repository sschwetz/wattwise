import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AuthProvider } from "./AuthContext";
import { ProtectedRoute } from "./ProtectedRoute";

function renderWithRoute(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div>Login page</div>} />
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<div>Dashboard</div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  it("redirects to /login when there is no token", () => {
    localStorage.clear();
    renderWithRoute("/");
    expect(screen.getByText("Login page")).toBeInTheDocument();
  });

  it("renders the nested route when a token is present", () => {
    localStorage.setItem("access_token", "a-token");
    renderWithRoute("/");
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    localStorage.clear();
  });
});
