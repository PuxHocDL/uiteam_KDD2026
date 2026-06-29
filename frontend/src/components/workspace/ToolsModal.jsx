import { useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import RichText from '../common/RichText';
import ToolViz from './ToolViz';
import { HANDLER_LABEL, groupTools } from '../../data/tools';

const ICONS = { list: Icon.list, file: Icon.file, table: Icon.table, data: Icon.data, code: Icon.code, spark: Icon.spark, check: Icon.check, search: Icon.search, tool: Icon.tool, globe: Icon.globe, plug: Icon.plug };

// Lets the end user inspect and REGISTER tools the agent may use — the UI face
// of the plug-and-play Tool Registry. Adding a tool here is the
// no-code equivalent of dropping a tool entry into configs/tools/*.yaml.
export default function ToolsModal({ tools, onChange, onClose }) {
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(null); // name of the expanded tool (one at a time)

  const toggle = (name) => onChange(tools.map((t) => (t.name === name ? { ...t, enabled: !t.enabled } : t)));
  const remove = (name) => onChange(tools.filter((t) => t.name !== name));
  const add = (tool) => { onChange([...tools, tool]); setAdding(false); };

  // Group the tools, then assign a global running number (badged on each icon's
  // corner) in group order so duplicate icons stay easy to tell apart.
  let _n = 0;
  const grouped = groupTools(tools).map((g) => ({
    ...g, tools: g.tools.map((t) => ({ t, num: ++_n })),
  }));

  return (
    <Modal size="md" onClose={onClose} title={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Icon.tool width={16} height={16} /> Tools the agent can use
        <span className="dim-note">{tools.filter((t) => t.enabled).length} enabled</span>
      </span>
    }>
      <div className="tools-body">
        <div className="tool-list">
          {grouped.map((g) => (
            <div className="tool-group" key={g.id}>
              <div className="tool-group-head">{g.label} <span className="tool-group-count">{g.tools.length}</span></div>
              {g.tools.map(({ t, num }) => {
                const IconC = ICONS[t.icon] || Icon.tool;
                const isOpen = open === t.name;
                return (
                  <div className={`tool-row ${t.enabled ? '' : 'off'} ${isOpen ? 'open' : ''}`} key={t.name}>
                    <div className="tool-head" onClick={() => setOpen(isOpen ? null : t.name)}
                      role="button" aria-expanded={isOpen} title={isOpen ? 'Hide details' : 'See what the agent does'}>
                      <span className={`tool-ic cat-${t.category || 'scan'}`}>
                        <IconC width={16} height={16} />
                        <span className="tool-ic-num">{num}</span>
                      </span>
                      <div className="tool-info">
                        <div className="tool-name">
                          {t.name}
                          {t.builtin ? <span className="tag built">built-in</span> : <span className="tag custom">custom</span>}
                          {t.requiresApproval && <span className="tag approve">needs approval</span>}
                        </div>
                        <div className={`tool-desc ${isOpen ? '' : 'clamp'}`}><RichText text={t.desc} /></div>
                        <div className="tool-handler"><Icon.plug width={11} height={11} /> {HANDLER_LABEL[t.handler] || t.handler}{t.endpoint ? ` · ${t.endpoint}` : ''}</div>
                      </div>
                      <div className="tool-actions" onClick={(e) => e.stopPropagation()}>
                        <span className={`switch sm ${t.enabled ? 'on' : ''}`} onClick={() => toggle(t.name)}><span className="knob" /></span>
                        {!t.builtin && <button className="icon-btn danger" title="Remove" onClick={() => remove(t.name)}><Icon.trash width={15} height={15} /></button>}
                      </div>
                      <Icon.chevron className="tool-caret" width={15} height={15} />
                    </div>
                    {isOpen && <ToolViz name={t.name} category={t.category} />}
                  </div>
                );
              })}
            </div>
          ))}
        </div>

        {adding ? (
          <AddToolForm existing={tools.map((t) => t.name)} onCancel={() => setAdding(false)} onAdd={add} />
        ) : (
          <button className="btn btn-ghost add-tool-btn" onClick={() => setAdding(true)}><Icon.plus width={15} height={15} /> Register a new tool</button>
        )}
      </div>
    </Modal>
  );
}

function AddToolForm({ existing, onAdd, onCancel }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [handler, setHandler] = useState('python');
  const [endpoint, setEndpoint] = useState('');
  const [schema, setSchema] = useState('{\n  "path": "relative/file.csv"\n}');
  const [approval, setApproval] = useState(false);
  const [err, setErr] = useState('');

  const submit = () => {
    const clean = name.trim().replace(/\s+/g, '_');
    if (!clean) return setErr('Name is required.');
    if (existing.includes(clean)) return setErr('A tool with that name already exists.');
    try { JSON.parse(schema); } catch { return setErr('Input schema must be valid JSON.'); }
    onAdd({
      name: clean, desc: desc.trim() || 'Custom tool.', handler, endpoint: endpoint.trim(),
      icon: handler === 'rest' ? 'globe' : handler === 'mcp' ? 'plug' : 'code',
      category: handler === 'python' ? 'python' : 'scan',
      inputSchema: schema, requiresApproval: approval, builtin: false, enabled: true,
    });
  };

  return (
    <div className="add-tool-form">
      <div className="set-label">New tool</div>
      <div className="field-row">
        <div className="field"><label>Name</label><input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. fetch_weather" /></div>
        <div className="field">
          <label>Handler</label>
          <select className="handler-select" value={handler} onChange={(e) => setHandler(e.target.value)}>
            <option value="python">Python callable</option>
            <option value="rest">REST endpoint</option>
            <option value="mcp">MCP tool</option>
          </select>
        </div>
      </div>
      <div className="field"><label>Description <span className="dim-note">(the LLM reads this to decide when to call it)</span></label>
        <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="What the tool does + when to use it" />
      </div>
      {handler === 'rest' && (
        <div className="field"><label>Endpoint URL</label><input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://api.example.com/run" /></div>
      )}
      <div className="field"><label>Input schema (JSON)</label>
        <textarea className="schema-input" value={schema} onChange={(e) => setSchema(e.target.value)} spellCheck={false} />
      </div>
      <label className="approval-check">
        <input type="checkbox" checked={approval} onChange={(e) => setApproval(e.target.checked)} />
        Requires approval before running (write / external / sensitive)
      </label>
      {err && <div className="form-err">{err}</div>}
      <div className="form-actions">
        <button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={submit}><Icon.plus width={14} height={14} /> Add tool</button>
      </div>
    </div>
  );
}
