import { MarkerType, type Edge as RFEdge, type Node as RFNode } from "@xyflow/react";

import type { Fact, FactVersion, Graph, Layer } from "./types";

export interface FactNodeData {
  fact: Fact;
  layer: Layer;
  [key: string]: unknown;
}

export interface LayerNodeData {
  layer: Layer;
  [key: string]: unknown;
}

const CARD_W = 220;
const CARD_H = 104;
const GAP_X = 36;
const GAP_Y = 36;
const COLS = 3; // facts per row within a layer band
const HEADER_H = 52;
const PAD = 24;
const LAYER_GAP_Y = 56;

/** Best-effort human label for a fact-version's JSON content. */
export function contentLabel(version: FactVersion | undefined): string {
  if (!version) return "(no version)";
  const c = version.content ?? {};
  const preferred = c["claim"] ?? c["text"] ?? c["name"] ?? c["title"];
  if (typeof preferred === "string") return preferred;
  for (const value of Object.values(c)) {
    if (typeof value === "string") return value;
  }
  const json = JSON.stringify(c);
  return json.length > 90 ? json.slice(0, 87) + "…" : json;
}

/**
 * Convert a graph snapshot into React Flow nodes + edges.
 *
 * - Each layer becomes a (non-draggable) group node, stacked vertically by ordinal.
 * - Each fact becomes a child node laid out in a grid within its layer.
 * - Edges are drawn at the *fact* level, only between facts whose LATEST
 *   versions are the edge endpoints. Edges touching an older version are
 *   omitted from this default view (they surface in the version panel).
 */
export function buildFlow(graph: Graph): { nodes: RFNode[]; edges: RFEdge[] } {
  const nodes: RFNode[] = [];
  const fvToFact = new Map<string, string>();
  const latestFvIds = new Set<string>();

  for (const layer of graph.layers) {
    for (const fact of layer.facts) {
      for (const v of fact.versions) fvToFact.set(v.id, fact.id);
      if (fact.latest_version_id) latestFvIds.add(fact.latest_version_id);
    }
  }

  let cursorY = 0;
  for (const layer of graph.layers) {
    const count = layer.facts.length;
    const cols = Math.min(COLS, Math.max(1, count));
    const rows = Math.max(1, Math.ceil(count / COLS));
    const width = PAD * 2 + cols * CARD_W + (cols - 1) * GAP_X;
    const height = HEADER_H + PAD + rows * CARD_H + (rows - 1) * GAP_Y + PAD;
    const layerId = `layer:${layer.id}`;

    nodes.push({
      id: layerId,
      type: "layerGroup",
      position: { x: 0, y: cursorY },
      data: { layer } satisfies LayerNodeData,
      style: { width, height },
      draggable: false,
      selectable: false,
      // Parent nodes must precede their children in the array.
    });

    layer.facts.forEach((fact, i) => {
      const r = Math.floor(i / COLS);
      const c = i % COLS;
      nodes.push({
        id: `fact:${fact.id}`,
        type: "factNode",
        parentId: layerId,
        extent: "parent",
        position: {
          x: PAD + c * (CARD_W + GAP_X),
          y: HEADER_H + PAD + r * (CARD_H + GAP_Y),
        },
        data: { fact, layer } satisfies FactNodeData,
      });
    });

    cursorY += height + LAYER_GAP_Y;
  }

  const edges: RFEdge[] = [];
  const seen = new Set<string>();
  for (const e of graph.edges) {
    if (!latestFvIds.has(e.source_fv_id) || !latestFvIds.has(e.target_fv_id)) continue;
    const s = fvToFact.get(e.source_fv_id);
    const t = fvToFact.get(e.target_fv_id);
    if (!s || !t || s === t) continue;
    const id = `${s}->${t}:${e.edge_kind}`;
    if (seen.has(id)) continue;
    seen.add(id);
    edges.push({
      id,
      source: `fact:${s}`,
      target: `fact:${t}`,
      label: e.edge_kind,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: { stroke: "#94a3b8" },
    });
  }

  return { nodes, edges };
}
