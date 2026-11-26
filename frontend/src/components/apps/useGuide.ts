// Loads an app's guide (used by the Guide AND Commands tabs). Shows the
// basic one at once; if it isn't the AI-written one yet, asks the backend
// to write it (once per repo, cheapest model) and swaps it in when ready.

import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../api/client";
import type { AppGuide } from "../../api/types";

export function useGuide(appId: string) {
  const [guide, setGuide] = useState<AppGuide | null>(null);
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const write = useCallback(async (force: boolean) => {
    setWriting(true);
    setError(null);
    try {
      setGuide(await api.writeGuide(appId, force));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setWriting(false);
    }
  }, [appId]);

  useEffect(() => {
    let active = true;
    api.guide(appId).then((g) => {
      if (!active) return;
      setGuide(g);
      if (g.source === "basic") void write(false);
    }).catch((e) => active && setError(errorText(e)));
    return () => {
      active = false;
    };
  }, [appId, write]);

  return { guide, writing, error, write };
}

export type GuideState = ReturnType<typeof useGuide>;
