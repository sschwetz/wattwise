import { useQuery } from "@tanstack/react-query";

export interface LicencePackage {
  name: string;
  version: string;
  ecosystem: "npm" | "python";
  license: string;
  text_id: string | null;
}

export interface LicenceDocument {
  generated_at: string;
  texts: Record<string, string>;
  packages: LicencePackage[];
}

async function fetchLicences(): Promise<LicenceDocument> {
  const response = await fetch(`${import.meta.env.BASE_URL}licences.json`);
  if (!response.ok) throw new Error(`licences.json returned ${response.status}`);
  return (await response.json()) as LicenceDocument;
}

export function useLicences(enabled: boolean) {
  return useQuery({
    queryKey: ["licences"],
    queryFn: fetchLicences,
    enabled,
    staleTime: Infinity,
  });
}
