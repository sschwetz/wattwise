import { Outlet } from "react-router-dom";
import { Header } from "./Header";

/** Shared chrome for every protected route: the account header, above
 * whatever page-specific layout (or bare page) the matched route renders. */
export function ProtectedLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <Outlet />
    </div>
  );
}
