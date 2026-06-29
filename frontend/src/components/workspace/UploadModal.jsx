import { useEffect, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import { uploadFile, listSamples, importSample } from '../../lib/api';

// Real upload (§12.4): pick or drag-drop files from disk → POST to the workspace
// session, with per-file progress. No hard-coded/staged names.
export default function UploadModal({ sid, onClose, onUploaded }) {
  const [items, setItems] = useState([]);   // { file, status: 'pending'|'uploading'|'done'|'error', error? }
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [samples, setSamples] = useState([]);
  const [importing, setImporting] = useState({}); // name -> 'busy' | 'done' | 'error'
  const inputRef = useRef(null);

  useEffect(() => { listSamples().then(setSamples).catch(() => setSamples([])); }, []);

  const addFiles = (fileList) => {
    const incoming = Array.from(fileList || []).map((file) => ({ file, status: 'pending' }));
    if (incoming.length) setItems((cur) => [...cur, ...incoming]);
  };

  const upload = async () => {
    if (!sid || busy) return;
    setBusy(true);
    let uploadedAny = false;
    for (let i = 0; i < items.length; i++) {
      if (items[i].status === 'done') continue;
      setItems((cur) => cur.map((it, k) => (k === i ? { ...it, status: 'uploading' } : it)));
      try {
        await uploadFile(sid, items[i].file);
        uploadedAny = true;
        setItems((cur) => cur.map((it, k) => (k === i ? { ...it, status: 'done' } : it)));
      } catch (e) {
        setItems((cur) => cur.map((it, k) => (k === i ? { ...it, status: 'error', error: String(e.message || e) } : it)));
      }
    }
    setBusy(false);
    if (uploadedAny) onUploaded?.();
  };

  const useSample = async (name) => {
    if (!sid || importing[name] === 'busy') return;
    setImporting((s) => ({ ...s, [name]: 'busy' }));
    try {
      await importSample(sid, name);
      setImporting((s) => ({ ...s, [name]: 'done' }));
      onUploaded?.();
    } catch {
      setImporting((s) => ({ ...s, [name]: 'error' }));
    }
  };

  const allDone = items.length > 0 && items.every((it) => it.status === 'done');
  const pending = items.filter((it) => it.status === 'pending' || it.status === 'error').length;

  return (
    <Modal title="Upload data" onClose={onClose}>
      <div
        className={`dropzone ${drag ? 'drag' : ''}`}
        style={{ padding: 34 }}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
      >
        <Icon.upload width={26} height={26} />
        <div style={{ marginTop: 10 }}>Drag &amp; drop files here, or <u>browse</u>.</div>
        <div className="dim-note" style={{ marginTop: 4 }}>CSV · Excel · JSON · SQLite · PDF · text</div>
      </div>
      <input ref={inputRef} type="file" multiple hidden
        onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }} />

      {samples.length > 0 && (
        <div className="sample-list">
          <div className="plan-section-label" style={{ margin: '14px 0 8px' }}>
            Or try a built-in sample <span className="dim-note">({samples.length})</span>
          </div>
          {/* Scroll the samples inside a bounded box so a long list can't push the
              manual-upload list and the Upload/Cancel buttons off-screen. */}
          <div className="sample-scroll">
            {samples.map((s) => {
              const status = importing[s.name];
              return (
                <div className="file-item" key={s.name}>
                  <div className="file-ic"><Icon.file width={15} height={15} /></div>
                  <div className="file-meta">
                    <div className="file-name" title={s.name}>{s.name}</div>
                    <div className="file-size">{Math.max(1, Math.round(s.bytes / 1024))} KB · {s.description || s.kind.toUpperCase()}</div>
                  </div>
                  <button className="btn btn-ghost" disabled={!sid || status === 'busy' || status === 'done'}
                    onClick={() => useSample(s.name)} title="Add this sample to the workspace">
                    {status === 'done' ? <><Icon.check width={14} height={14} /> Added</>
                      : status === 'busy' ? 'Adding…'
                      : status === 'error' ? 'Retry'
                      : 'Use sample'}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="upload-list">
          {items.map((it, k) => (
            <div className="file-item" key={k}>
              <div className="file-ic"><Icon.file width={15} height={15} /></div>
              <div className="file-meta">
                <div className="file-name" title={it.file.name}>{it.file.name}</div>
                <div className="file-size">{(it.file.size / 1024).toFixed(0)} KB
                  {it.status === 'error' && <span className="up-err"> · {it.error}</span>}
                </div>
              </div>
              <span className={`up-status ${it.status}`}>
                {it.status === 'done' ? <Icon.check width={15} height={15} />
                  : it.status === 'uploading' ? <span className="up-spin" />
                  : it.status === 'error' ? <Icon.alert width={15} height={15} />
                  : <button className="icon-btn" title="Remove" onClick={() => setItems((cur) => cur.filter((_, j) => j !== k))}><Icon.x width={14} height={14} /></button>}
              </span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 18 }}>
        <button className="btn btn-ghost" onClick={onClose}>{allDone ? 'Done' : 'Cancel'}</button>
        <button className="btn btn-primary" disabled={!sid || busy || pending === 0} onClick={upload}>
          <Icon.upload width={15} height={15} /> {busy ? 'Uploading…' : `Upload${pending ? ` (${pending})` : ''}`}
        </button>
      </div>
    </Modal>
  );
}
