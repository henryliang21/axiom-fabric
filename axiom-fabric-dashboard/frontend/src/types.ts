// Mirrors the Pydantic response schemas in axiom_fabric_dashboard.schemas.

export interface FactVersion {
  id: string;
  version: number;
  weight: number;
  content: Record<string, unknown>;
  layer_version_id: string;
  justification: Record<string, unknown> | null;
  temperature: number | null;
  note: string | null;
  created_at: string;
}

export interface Fact {
  id: string;
  layer_id: string;
  schema_ref: string | null;
  created_at: string;
  latest_version_id: string | null;
  versions: FactVersion[];
}

export interface LayerVersion {
  id: string;
  version: number;
  weight: number;
  ordinal: number;
  notes: string | null;
  created_at: string;
}

export interface Layer {
  id: string;
  name: string;
  display_name: string | null;
  weight: number;
  ordinal: number;
  created_at: string;
  versions: LayerVersion[];
  facts: Fact[];
}

export interface Edge {
  source_fv_id: string;
  target_fv_id: string;
  edge_kind: string;
  created_at: string;
}

export interface Graph {
  layers: Layer[];
  edges: Edge[];
  fact_count: number;
  fact_version_count: number;
}

export interface Health {
  status: "ok" | "uninitialized" | "error";
  initialized: boolean;
  database_backend: string;
  revision: string | null;
  layer_count: number | null;
  message: string;
}

export interface FactVersionEdges {
  fact_version_id: string;
  outgoing: Edge[];
  incoming: Edge[];
}
