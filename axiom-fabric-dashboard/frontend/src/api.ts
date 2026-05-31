import type { FactVersionEdges, Graph, Health } from "./types";

async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: { Accept: "application/json" } });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body; keep the status text
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const fetchHealth = () => getJSON<Health>("/api/health");
export const fetchGraph = () => getJSON<Graph>("/api/graph");
export const fetchFactVersionEdges = (fvId: string) =>
  getJSON<FactVersionEdges>(`/api/fact-versions/${fvId}/edges`);
