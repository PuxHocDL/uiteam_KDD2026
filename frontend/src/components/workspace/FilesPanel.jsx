import { Icon } from '../common/Icons';
import { useConfirm } from '../common/ConfirmDialog';
import { useToast } from '../common/Toast';
import ErrorNote from '../common/ErrorNote';

// Real files for the workspace session (§12.4) — no mock data, no placeholders.
const KIND_ICON = { csv: Icon.table, excel: Icon.table, json: Icon.data, sqlite: Icon.data, md: Icon.file, pdf: Icon.file };
// Files that the text-knowledge-graph extractor (§12.2a) understands.
const TEXT_KINDS = new Set(['pdf', 'md']);

export default function FilesPanel({ files = [], ready, error, onUpload, onView, onShowGraph, onSearch, onExplore, onBuildKG, onRemove, onReset }) {
  const confirm = useConfirm();
  const toast = useToast();
  return (
    <div className="panel files-panel">
      <div className="panel-head">
        <span className="title"><Icon.file width={16} height={16} /> Files {files.length > 0 && <span className="count-pill">{files.length}</span>}</span>
        <span className="spacer" />
        <button className="icon-btn" title="Explore — statistics &amp; charts" onClick={onExplore}><Icon.bars width={16} height={16} /></button>
        <button className="icon-btn" title="Search across data" onClick={onSearch}><Icon.search width={15} height={15} /></button>
        <button className="icon-btn" title="Data relationships" onClick={onShowGraph}><Icon.network width={16} height={16} /></button>
        <button className="icon-btn" title="Upload" onClick={onUpload} data-tour="tour-upload"><Icon.upload width={16} height={16} /></button>
        {onReset && (
          <button className="icon-btn danger" title="Reset workspace — delete every file, analysis &amp; setting in this session" onClick={onReset}>
            <Icon.trash width={15} height={15} />
          </button>
        )}
      </div>
      <div className="panel-body">
        {error && <ErrorNote error={error} />}

        {/* Scrollable list — keeps the dropzone (and the PlanPanel below) in view
            even when the user uploads many files. */}
        <div className="files-list">
          {files.map((f) => {
            const IconC = KIND_ICON[f.kind] || Icon.file;
            const isText = TEXT_KINDS.has(f.kind);
            return (
              <div className="file-item" key={f.id}>
                <div className={`file-ic ${f.kind === 'json' ? 'json' : f.kind === 'md' ? 'md' : ''}`}><IconC width={16} height={16} /></div>
                <div className="file-meta">
                  <div className="file-name" title={f.name}>{f.name}</div>
                  <div className="file-size">{f.size}{f.rowCount != null ? ` · ${f.rowCount} rows` : ''}</div>
                </div>
                <div className="file-actions">
                  {isText && onBuildKG && (
                    <button className="icon-btn" title="Build knowledge graph from this document"
                      onClick={() => onBuildKG(f)}>
                      <Icon.network width={15} height={15} />
                    </button>
                  )}
                  <button className="icon-btn" title="Preview data" onClick={() => onView(f)}><Icon.eye width={15} height={15} /></button>
                  <button className="icon-btn danger" title="Remove file"
                    onClick={async () => { if (await confirm({ title: 'Remove file?', message: `“${f.name}” will be deleted from the workspace.`, confirmText: 'Remove', danger: true })) { try { await onRemove(f.id); toast('File removed'); } catch (e) { toast(String(e.message || e), 'error'); } } }}>
                    <Icon.trash width={15} height={15} />
                  </button>
                </div>
              </div>
            );
          })}

          {ready && files.length === 0 && !error && (
            <div className="dim-note" style={{ padding: '6px 2px 10px' }}>No files yet — upload a CSV, Excel, JSON, SQLite or text file.</div>
          )}
        </div>

        <div className="dropzone" onClick={onUpload}>
          <Icon.upload width={18} height={18} /><div style={{ marginTop: 6 }}>Drag &amp; drop or <u>browse</u></div>
        </div>
      </div>
    </div>
  );
}
