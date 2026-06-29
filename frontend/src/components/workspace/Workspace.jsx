import { useEffect, useRef, useState } from 'react';
import FilesPanel from './FilesPanel';
import PlanPanel from './PlanPanel';
import ChatPanel from './ChatPanel';
import ResultsPanel from './ResultsPanel';
import UploadModal from './UploadModal';
import DataPreviewModal from './DataPreviewModal';
import SettingsModal from './SettingsModal';
import ToolsModal from './ToolsModal';
import RelationshipGraph from './RelationshipGraph';
import DataSearchModal from './DataSearchModal';
import ExploreModal from './ExploreModal';
import TextKnowledgeGraphModal from './TextKnowledgeGraphModal';
import { useConfirm } from '../common/ConfirmDialog';
import { useToast } from '../common/Toast';
import Resizer from '../common/Resizer';
import { useAgentRun } from '../../hooks/useAgentRun';
import { useAgentRunLive } from '../../hooks/useAgentRunLive';
import { useWorkspaceSession } from '../../hooks/useWorkspaceSession';
import { loadSettings, saveSettings } from '../../data/agentOptions';
import { BUILTIN_TOOLS, mapServerTools } from '../../data/tools';
import { checkHealth, listTools } from '../../lib/api';

// Resizable workspace layout — persisted so the user's column/row sizes stick.
const LAYOUT_KEY = 'das-workspace-layout';
const LAYOUT_DEFAULT = { left: 290, right: 400, filesH: 320 };
const LAYOUT_BOUNDS = { left: [220, 600], right: [280, 760], filesH: [140, 1000] };
const _clampLayout = (v, [lo, hi]) => Math.max(lo, Math.min(hi, v));
const _loadLayout = () => {
  try { return { ...LAYOUT_DEFAULT, ...JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') }; }
  catch { return { ...LAYOUT_DEFAULT }; }
};

export default function Workspace() {
  const [settings, setSettings] = useState(loadSettings);
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return; } // don't re-save the loaded value
    saveSettings(settings);
  }, [settings]);
  const [live, setLive] = useState(true);  // Live mode is the default — Demo replays a fixed scripted example and is opt-in.

  const ws = useWorkspaceSession();                 // real workspace session (files live here)
  const confirm = useConfirm();
  const pushToast = useToast();

  const mockAgent = useAgentRun();
  const liveAgent = useAgentRunLive({ settings, sessionId: ws.sid });
  const agent = live ? liveAgent : mockAgent; // both hooks always run (rules of hooks)

  const [health, setHealth] = useState(null); // null=unknown, false=down, obj=up

  const [showUpload, setShowUpload] = useState(false);
  const [preview, setPreview] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [tools, setTools] = useState(() => BUILTIN_TOOLS.map((t) => ({ ...t })));
  const [showTools, setShowTools] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [showExplore, setShowExplore] = useState(false);
  const [textKgFile, setTextKgFile] = useState(null);  // §12.2a — the PDF/MD/TXT to graph
  const flash = (msg, type = 'success') => pushToast(msg, type);

  // Draggable panel sizing (Files / Chat / Results widths + Files/Plan split).
  const [layout, setLayout] = useState(_loadLayout);
  useEffect(() => { try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch { /* ignore */ } }, [layout]);
  const resize = (key) => (d, reset) => setLayout((l) => ({
    ...l,
    [key]: reset ? LAYOUT_DEFAULT[key] : _clampLayout(l[key] + (key === 'right' ? -d : d), LAYOUT_BOUNDS[key]),
  }));

  // When live is switched on, probe the backend so the chat shows an up/down dot.
  useEffect(() => {
    if (!live) return;
    let alive = true;
    (async () => {
      const h = await checkHealth();
      if (alive) setHealth(h || false);
    })();
    return () => { alive = false; };
  }, [live]);

  // Populate the Tools panel from the REAL registry (GET /api/tools) so it shows
  // every tool the engine can call. Preserves the user's enable toggles and any
  // custom tools; falls back to the offline BUILTIN_TOOLS list if the fetch fails.
  useEffect(() => {
    let alive = true;
    listTools()
      .then((res) => {
        if (!alive || !res?.tools?.length) return;
        setTools((prev) => {
          const prevByName = Object.fromEntries(prev.map((t) => [t.name, t]));
          const server = mapServerTools(res.tools).map((t) => (
            prevByName[t.name] ? { ...t, enabled: prevByName[t.name].enabled } : t
          ));
          const custom = prev.filter((t) => !t.builtin);
          return [...server, ...custom];
        });
      })
      .catch(() => { /* keep the BUILTIN_TOOLS fallback */ });
    return () => { alive = false; };
  }, []);

  return (
    <div className="workspace">
      <div className="wcol wcol-left" style={{ width: layout.left }}>
        <div className="wpanel-wrap" style={{ flex: `0 0 ${layout.filesH}px` }} data-tour="tour-files">
          <FilesPanel
            files={ws.files} ready={ws.ready} error={ws.error}
            onUpload={() => setShowUpload(true)}
            onView={setPreview}
            onRemove={ws.remove}
            onShowGraph={() => setShowGraph(true)}
            onSearch={() => setShowSearch(true)}
            onExplore={() => setShowExplore(true)}
            onBuildKG={(f) => {
              if (!settings.endpoint.apiKey) { flash('Add your API key in Settings first.', 'info'); setShowSettings(true); return; }
              setTextKgFile(f);
            }}
            onReset={async () => {
              if (!(await confirm({ title: 'Reset workspace?', message: 'Every uploaded file, saved analysis and setting will be permanently deleted.', confirmText: 'Reset', danger: true }))) return;
              try { await ws.reset(); flash('Workspace reset — fresh session.'); }
              catch (e) { flash(`Reset failed: ${e.message || e}`, 'error'); }
            }}
          />
        </div>
        <Resizer axis="y" onResize={resize('filesH')} title="Drag to resize Files / Plan (double-click to reset)" />
        <div className="wpanel-wrap" style={{ flex: '1 1 0' }} data-tour="tour-plan">
          <PlanPanel agent={agent} />
        </div>
      </div>

      <Resizer axis="x" onResize={resize('left')} title="Drag to resize the Files column (double-click to reset)" />

      <div className="wcol wcol-mid">
        <ChatPanel
          agent={agent}
          settings={settings}
          files={ws.files}
          onUpload={() => setShowUpload(true)}
          onOpenSettings={() => setShowSettings(true)}
          tools={tools}
          onOpenTools={() => setShowTools(true)}
          liveCtl={{ live, onToggleLive: () => setLive((v) => !v), health, sessionReady: !!ws.sid, fileCount: ws.files.length, sid: ws.sid }}
        />
      </div>

      <Resizer axis="x" onResize={resize('right')} title="Drag to resize the Results column (double-click to reset)" />

      <div className="wcol wcol-right" style={{ width: layout.right }} data-tour="tour-results">
        <ResultsPanel agent={agent} tools={tools} sid={ws.sid} files={ws.files} settings={settings} onFilesChanged={ws.refresh} />
      </div>

      {showUpload && <UploadModal sid={ws.sid} onClose={() => setShowUpload(false)} onUploaded={() => { ws.refresh(); flash('Upload successful'); }} />}
      {preview && <DataPreviewModal sid={ws.sid} file={preview} onClose={() => setPreview(null)} />}
      {showSettings && (
        <SettingsModal settings={settings} onClose={() => setShowSettings(false)} onSave={(s) => { setSettings(s); setShowSettings(false); flash('Agent settings saved'); }} />
      )}
      {showTools && <ToolsModal tools={tools} onChange={setTools} onClose={() => setShowTools(false)} />}
      {showGraph && <RelationshipGraph sid={ws.sid} onClose={() => setShowGraph(false)} />}
      {showSearch && <DataSearchModal onClose={() => setShowSearch(false)} />}
      {showExplore && <ExploreModal sid={ws.sid} onClose={() => { setShowExplore(false); ws.refresh(); }} />}
      {textKgFile && (
        <TextKnowledgeGraphModal
          sid={ws.sid}
          file={textKgFile}
          settings={{ model: settings.endpoint.model, endpoint: settings.endpoint.apiBase,
                      apiKey: settings.endpoint.apiKey, apiVersion: settings.endpoint.apiVersion }}
          onClose={() => setTextKgFile(null)}
        />
      )}
    </div>
  );
}
