import { useEffect, useState } from "react";

import { fetchFactVersionEdges } from "./api";
import { contentLabel } from "./layout";
import type { Fact, FactVersionEdges, Layer } from "./types";

interface Props {
  fact: Fact | null;
  layer: Layer | null;
  onClose: () => void;
}

export function VersionPanel({ fact, layer, onClose }: Props) {
  const [openVersionId, setOpenVersionId] = useState<string | null>(null);
  const [edges, setEdges] = useState<FactVersionEdges | null>(null);
  const [edgesError, setEdgesError] = useState<string | null>(null);

  // Reset drill-down when the selected fact changes.
  useEffect(() => {
    setOpenVersionId(null);
    setEdges(null);
    setEdgesError(null);
  }, [fact?.id]);

  useEffect(() => {
    if (!openVersionId) return;
    let cancelled = false;
    setEdges(null);
    setEdgesError(null);
    fetchFactVersionEdges(openVersionId)
      .then((data) => !cancelled && setEdges(data))
      .catch((err) => !cancelled && setEdgesError(String(err.message ?? err)));
    return () => {
      cancelled = true;
    };
  }, [openVersionId]);

  if (!fact) return null;

  const latestId = fact.latest_version_id;
  // Newest first.
  const versions = [...fact.versions].reverse();

  return (
    <aside className="panel">
      <header className="panel__head">
        <div>
          <div className="panel__title">Fact</div>
          <div className="panel__subtitle">
            {layer ? layer.display_name || layer.name : ""} · {fact.versions.length}{" "}
            {fact.versions.length === 1 ? "version" : "versions"}
          </div>
        </div>
        <button className="panel__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>

      <div className="panel__id">
        {fact.schema_ref ? <code>{fact.schema_ref}</code> : <em>no schema</em>}
        <code className="panel__uuid">{fact.id}</code>
      </div>

      <ol className="versions">
        {versions.map((v) => {
          const isLatest = v.id === latestId;
          const isOpen = v.id === openVersionId;
          return (
            <li key={v.id} className={`version${isLatest ? " version--latest" : ""}`}>
              <button
                className="version__row"
                onClick={() => setOpenVersionId(isOpen ? null : v.id)}
              >
                <span className="version__num">v{v.version}</span>
                {isLatest && <span className="version__badge">latest</span>}
                <span className="version__weight">w{v.weight}</span>
                {v.temperature != null && (
                  <span className="version__temp">T={v.temperature.toFixed(2)}</span>
                )}
                <span className="version__chevron">{isOpen ? "▾" : "▸"}</span>
              </button>
              <div className="version__claim">{contentLabel(v)}</div>
              {isOpen && (
                <div className="version__detail">
                  <pre className="version__json">{JSON.stringify(v.content, null, 2)}</pre>
                  {v.note && <div className="version__note">note: {v.note}</div>}
                  <div className="version__created">
                    created {new Date(v.created_at).toLocaleString()}
                  </div>
                  <EdgesView edges={edges} error={edgesError} />
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

function EdgesView({ edges, error }: { edges: FactVersionEdges | null; error: string | null }) {
  if (error) return <div className="edges edges--error">Failed to load edges: {error}</div>;
  if (!edges) return <div className="edges edges--loading">Loading edges…</div>;
  return (
    <div className="edges">
      <div className="edges__group">
        <strong>Derived from ({edges.outgoing.length})</strong>
        {edges.outgoing.length === 0 ? (
          <span className="edges__empty"> none</span>
        ) : (
          <ul>
            {edges.outgoing.map((e) => (
              <li key={e.target_fv_id}>
                {e.edge_kind} → <code>{e.target_fv_id.slice(0, 8)}</code>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="edges__group">
        <strong>Used by ({edges.incoming.length})</strong>
        {edges.incoming.length === 0 ? (
          <span className="edges__empty"> none</span>
        ) : (
          <ul>
            {edges.incoming.map((e) => (
              <li key={e.source_fv_id}>
                <code>{e.source_fv_id.slice(0, 8)}</code> → {e.edge_kind}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
