import { Handle, Position, type NodeProps } from "@xyflow/react";

import { contentLabel, type FactNodeData } from "../layout";

export function FactNode({ data, selected }: NodeProps) {
  const { fact } = data as FactNodeData;
  const latest = fact.versions[fact.versions.length - 1];
  const versionCount = fact.versions.length;
  const multi = versionCount > 1;

  return (
    <div className={`fact-card${multi ? " fact-card--stacked" : ""}${selected ? " fact-card--selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <header className="fact-card__head">
        <span className="fact-card__weight" title="change-cost weight">
          w{latest?.weight ?? "?"}
        </span>
        <span className="fact-card__versions">
          v{latest?.version ?? 1}
          {multi ? ` · ${versionCount} versions` : ""}
        </span>
        <span className="fact-card__menu" aria-hidden>
          ⋯
        </span>
      </header>
      <div className="fact-card__body" title={contentLabel(latest)}>
        {contentLabel(latest)}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
