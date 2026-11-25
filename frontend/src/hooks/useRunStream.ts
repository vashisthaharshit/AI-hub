// Connects to /ws/runs/{id} and collects the run's events as they arrive.
//
// Things this handles that a naive version would get wrong:
// - Reconnecting: if the connection drops mid-turn, it reconnects with
//   ?after=<last seq> so the backend only sends events we don't have yet.
// - Duplicates: events are ignored if their seq is not newer than the last one.
// - Follow-ups: the socket closes at the end of each turn. Bumping
//   `generation` reconnects for the next turn WITHOUT clearing earlier events.
// - Cleanup: switching runs or closing the view closes the socket. React's
//   StrictMode mounts components twice in dev; the cleanup makes that harmless.

import { useEffect, useRef, useState } from "react";
import { wsUrl } from "../api/client";
import type { EventMessage, RunInfo, SocketMessage } from "../api/types";

export type StreamStatus = "connecting" | "live" | "ended";

export function useRunStream(runId: string, generation = 0) {
  const [events, setEvents] = useState<EventMessage[]>([]);
  const [finalRun, setFinalRun] = useState<RunInfo | null>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const lastSeq = useRef(-1);

  // A different run: start from scratch.
  useEffect(() => {
    lastSeq.current = -1;
    setEvents([]);
  }, [runId]);

  // Connect (again) for the current turn.
  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let ended = false;
    let disposed = false;

    setFinalRun(null);
    setStatus("connecting");

    const connect = () => {
      socket = new WebSocket(wsUrl(`/ws/runs/${runId}?after=${lastSeq.current}`));
      socket.onopen = () => setStatus("live");
      socket.onmessage = (message) => {
        const msg = JSON.parse(message.data) as SocketMessage;
        if (msg.type === "event") {
          if (msg.seq <= lastSeq.current) return;
          lastSeq.current = msg.seq;
          setEvents((prev) => [...prev, msg]);
        } else if (msg.type === "end") {
          ended = true;
          setFinalRun(msg.run);
          setStatus("ended");
        }
      };
      socket.onclose = () => {
        if (disposed || ended) return;
        setStatus("connecting");
        retryTimer = setTimeout(connect, 1000); // try again in 1s
      };
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      socket?.close();
    };
  }, [runId, generation]);

  return { events, finalRun, status };
}
