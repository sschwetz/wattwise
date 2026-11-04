import { type FormEvent, useState } from "react";
import { errorMessage } from "../api/client";
import { SubmitButton } from "../components/SubmitButton";
import { useCreateInstance } from "./useActors";
import { cardClass, inputClass, primaryButtonClass } from "./styles";

const PROVIDER = "actron_neo";

/** Empty state: the owner names the air conditioner to set up; a viewer is told none exists. */
export function AddInstanceForm({ siteId, canEdit }: { siteId: number; canEdit: boolean }) {
  const create = useCreateInstance(siteId);
  const [name, setName] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate({ provider: PROVIDER, name: name.trim() });
  }

  return (
    <section className={`${cardClass} space-y-3`}>
      <h2 className="text-lg font-medium text-ink">Schedule</h2>
      {!canEdit ? (
        <p className="text-sm text-ink-muted">No device is set up for this site yet.</p>
      ) : (
        <form onSubmit={submit} className="max-w-sm space-y-3">
          <p className="text-sm text-ink-muted">Add your air conditioner to run it on a schedule.</p>
          <label className="block text-sm text-ink">
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} required className={`${inputClass} mt-1`} />
          </label>
          {create.isError && (
            <p role="alert" className="text-sm text-danger">
              {errorMessage(create.error, "Could not add the air conditioner.")}
            </p>
          )}
          <SubmitButton mutation={create} buttonClass={primaryButtonClass}>
            Add air conditioner
          </SubmitButton>
        </form>
      )}
    </section>
  );
}
