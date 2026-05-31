import { type NodeProps } from "@xyflow/react";

import type { LayerNodeData } from "../layout";

export function LayerGroupNode({ data }: NodeProps) {
  const { layer } = data as LayerNodeData;
  const latestVersion = layer.versions[layer.versions.length - 1];

  return (
    <div className="layer-group">
      <header className="layer-group__head">
        <span className="layer-group__name">{layer.display_name || layer.name}</span>
        <span className="layer-group__meta">
          weight {layer.weight} · ordinal {layer.ordinal}
          {latestVersion ? ` · layer v${latestVersion.version}` : ""} ·{" "}
          {layer.facts.length} {layer.facts.length === 1 ? "fact" : "facts"}
        </span>
      </header>
    </div>
  );
}
