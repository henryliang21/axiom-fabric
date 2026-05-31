import {
  Background,
  Controls,
  type Edge as RFEdge,
  MiniMap,
  type Node as RFNode,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchGraph, fetchHealth } from "./api";
import { buildFlow } from "./layout";
import { FactNode } from "./nodes/FactNode";
import { LayerGroupNode } from "./nodes/LayerGroupNode";
import type { Graph, Health } from "./types";
import { VersionPanel } from "./VersionPanel";

type Status = "loading" | "error" | "ready";

const nodeTypes = { factNode: FactNode, layerGroup: LayerGroupNode };

export default function App() {
  const [status, setStatus] = useState<Status>("loading");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [graph, setGraph] = useState<Graph | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [selectedFactId, setSelectedFactId] = useState<string | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<RFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<RFEdge>([]);

  const load = useCallback(async () => {
    setStatus("loading");
    setSelectedFactId(null);
    try {
      const h = await fetchHealth();
      setHealth(h);
      if (!h.initialized) {
        setErrorMsg(h.message);
        setStatus("error");
        return;
      }
      const g = await fetchGraph();
      setGraph(g);
      const flow = buildFlow(g);
      setNodes(flow.nodes);
      setEdges(flow.edges);
      setStatus("ready");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setStatus("error");
    }
  }, [setNodes, setEdges]);

  useEffect(() => {
    void load();
  }, [load]);

  const onNodeClick = useCallback((_: unknown, node: RFNode) => {
    if (node.type === "factNode") {
      const id = node.id.startsWith("fact:") ? node.id.slice("fact:".length) : node.id;
      setSelectedFactId(id);
    }
  }, []);

  const { selectedFact, selectedLayer } = useMemo(() => {
    if (!graph || !selectedFactId) return { selectedFact: null, selectedLayer: null };
    for (const layer of graph.layers) {
      const fact = layer.facts.find((f) => f.id === selectedFactId);
      if (fact) return { selectedFact: fact, selectedLayer: layer };
    }
    return { selectedFact: null, selectedLayer: null };
  }, [graph, selectedFactId]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__brand">
          Axiom Fabric <span className="topbar__sub">truth ledger</span>
        </div>
        <div className="topbar__status">
          {health && (
            <span className={`pill pill--${health.status}`}>
              {health.database_backend}
              {graph ? ` · ${graph.fact_count} facts · ${graph.fact_version_count} versions` : ""}
            </span>
          )}
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </header>

      <main className="stage">
        {status === "loading" && <div className="center muted">Loading…</div>}

        {status === "error" && (
          <div className="center">
            <div className="errorcard">
              <h2>Can’t load the truth store</h2>
              <p>{errorMsg}</p>
              <button className="btn" onClick={() => void load()}>
                Try again
              </button>
            </div>
          </div>
        )}

        {status === "ready" && (
          <>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={onNodeClick}
              onPaneClick={() => setSelectedFactId(null)}
              nodeTypes={nodeTypes}
              fitView
              minZoom={0.1}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={20} color="#e2e8f0" />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
            <VersionPanel
              fact={selectedFact}
              layer={selectedLayer}
              onClose={() => setSelectedFactId(null)}
            />
          </>
        )}
      </main>
    </div>
  );
}
