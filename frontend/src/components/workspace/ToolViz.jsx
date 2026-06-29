import { Fragment } from 'react';
import { Icon } from '../common/Icons';
import { toolPlaybook } from '../../data/tools';

// Small illustrated "what the agent does" pipeline shown when a tool is expanded
// in the Tools modal. Input → action → output, animated in, tinted by category.
const ICONS = {
  list: Icon.list, file: Icon.file, table: Icon.table, data: Icon.data, code: Icon.code,
  spark: Icon.spark, check: Icon.check, search: Icon.search, tool: Icon.tool,
  globe: Icon.globe, plug: Icon.plug, eye: Icon.eye,
};

export default function ToolViz({ name, category }) {
  const { gist, stages, example } = toolPlaybook(name, category);
  return (
    <div className={`tool-viz cat-${category}`}>
      <div className="tv-gist">{gist}</div>

      <div className="tv-flow" role="img" aria-label={`How ${name} works`}>
        {stages.map((s, i) => {
          const IconC = ICONS[s.icon] || Icon.tool;
          return (
            <Fragment key={i}>
              <div className={`tv-stage ${s.act ? 'act' : ''}`} style={{ animationDelay: `${i * 0.12}s` }}>
                <span className="tv-ic"><IconC width={16} height={16} /></span>
                <span className="tv-label">{s.label}</span>
              </div>
              {i < stages.length - 1 && (
                <span className="tv-arrow" style={{ animationDelay: `${i * 0.12 + 0.06}s` }}>
                  <Icon.arrowRight width={14} height={14} />
                </span>
              )}
            </Fragment>
          );
        })}
      </div>

      {example && (
        <div className="tv-example">
          <code className="tv-in">{example.in}</code>
          <span className="tv-ex-arrow"><Icon.arrowRight width={12} height={12} /></span>
          <span className="tv-out">{example.out}</span>
        </div>
      )}
    </div>
  );
}
