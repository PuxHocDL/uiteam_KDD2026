import { useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import { ENDPOINT_PRESETS, SOLUTIONS } from '../../data/agentOptions';

// Choose the agent: LLM endpoint (model + API key) and which Phase-1 solution
// (agent_mode) to run. Equivalent to the YAML a CLI run would load.
export default function SettingsModal({ settings, onClose, onSave }) {
  const [draft, setDraft] = useState(settings);
  const [showKey, setShowKey] = useState(false);

  const ep = draft.endpoint;
  const setEp = (patch) => setDraft((d) => ({ ...d, endpoint: { ...d.endpoint, ...patch } }));

  const applyPreset = (id) => {
    const p = ENDPOINT_PRESETS.find((x) => x.id === id);
    setEp({ preset: id, ...(p && id !== 'custom' ? { apiBase: p.apiBase, model: p.model, apiVersion: p.apiVersion || '' } : {}) });
  };

  return (
    <Modal size="md" onClose={onClose} title={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}><Icon.settings width={16} height={16} /> Agent Settings</span>}>
      <div className="settings-body">
        {/* ---------------- LLM endpoint ---------------- */}
        <div className="settings-section">
          <div className="set-label">LLM Endpoint</div>

          <div className="seg presets">
            {ENDPOINT_PRESETS.map((p) => (
              <button key={p.id} className={`seg-opt ${ep.preset === p.id ? 'active' : ''}`} onClick={() => applyPreset(p.id)}>{p.label}</button>
            ))}
          </div>

          <div className="field-row">
            <Field label="Model name">
              <input value={ep.model} onChange={(e) => setEp({ model: e.target.value, preset: 'custom' })} placeholder="e.g. gpt-4.1-mini" />
            </Field>
            <Field label="API base URL">
              <input value={ep.apiBase} onChange={(e) => setEp({ apiBase: e.target.value, preset: 'custom' })} placeholder="https://…/v1" />
            </Field>
          </div>

          <div className="field-row">
            <Field label="API key">
              <div className="api-key-row">
                <input type={showKey ? 'text' : 'password'} value={ep.apiKey} onChange={(e) => setEp({ apiKey: e.target.value })} placeholder="sk-… / Azure key" />
                <button className="btn btn-ghost btn-sm" onClick={() => setShowKey((s) => !s)}>{showKey ? 'Hide' : 'Show'}</button>
              </div>
            </Field>
            <Field label="API version (Azure)">
              <input value={ep.apiVersion} onChange={(e) => setEp({ apiVersion: e.target.value })} placeholder="2024-02-15-preview" />
            </Field>
          </div>
          <div className="dim-note">Saved in this browser (localStorage) so it persists across sessions, and sent only to your local backend. Stored as plaintext — clear it on a shared machine.</div>

          <div className="field-row">
            <Field label={`Temperature · ${ep.temperature.toFixed(1)}`}>
              <input type="range" min="0" max="1" step="0.1" value={ep.temperature} onChange={(e) => setEp({ temperature: parseFloat(e.target.value) })} />
            </Field>
            <Field label="Max steps">
              <input type="number" min="1" max="60" value={ep.maxSteps} onChange={(e) => setEp({ maxSteps: parseInt(e.target.value || '0', 10) })} />
            </Field>
          </div>
        </div>

        {/* ---------------- Agent solution ---------------- */}
        <div className="settings-section">
          <div className="set-label">Agent Solution <span className="dim-note">— from the KDD code in this repo</span></div>
          <div className="solution-grid">
            {SOLUTIONS.map((s) => (
              <button key={s.id} className={`solution-card ${draft.solution === s.id ? 'selected' : ''}`} onClick={() => setDraft((d) => ({ ...d, solution: s.id }))}>
                <div className="sol-top">
                  <span className="sol-name">{s.name}</span>
                  <span className="sol-tag">{s.tag}</span>
                  {draft.solution === s.id && <Icon.check width={15} height={15} className="sol-check" />}
                </div>
                <div className="sol-desc">{s.desc}</div>
                <div className="sol-config"><Icon.file width={11} height={11} /> {s.config}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="settings-foot">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={() => onSave(draft)}><Icon.check width={15} height={15} /> Save settings</button>
        </div>
      </div>
    </Modal>
  );
}

function Field({ label, children }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
    </div>
  );
}
