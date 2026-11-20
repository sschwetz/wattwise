import { Navigate, Outlet } from "react-router-dom";
import { useMe } from "../account/useAccount";

/** Renders its child routes for an admin and sends everyone else to the home page. */
export function AdminRoute() {
  const { data: me, isLoading } = useMe();
  if (isLoading) return null;
  if (!me?.is_admin) return <Navigate to="/" replace />;
  return <Outlet />;
}
