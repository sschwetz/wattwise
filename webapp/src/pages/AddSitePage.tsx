import { BackLink } from "../layout/BackLink";
import { SiteForm } from "../sites/SiteForm";

export function AddSitePage() {
  return (
    <div className="flex flex-1 flex-col bg-bg">
      <div className="mx-auto w-full max-w-sm px-4 pt-6">
        <BackLink to="/sites" />
      </div>
      <SiteForm title="Add a new site" />
    </div>
  );
}
