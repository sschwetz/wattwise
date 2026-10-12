import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetchBlob } from "../api/client";

/** The caller's own avatar image as a blob: URL, or null while there isn't one
 * (no avatar set, still loading, or load failed). Revokes every object URL it
 * creates -- on a new fetch, on hasAvatar flipping false, and on unmount -- so
 * this never leaks blob: URLs across uploads/removals or repeated mounts. */
export function useAvatarImage(hasAvatar: boolean): string | null {
  const query = useQuery({
    queryKey: ["me", "avatar"],
    queryFn: () => apiFetchBlob("/api/v1/users/me/avatar"),
    enabled: hasAvatar,
  });
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!hasAvatar || !query.data) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [hasAvatar, query.data]);

  return url;
}
