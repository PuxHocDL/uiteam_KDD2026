import { useEffect, useRef, useState } from 'react';
import { Icon } from '../common/Icons';
import { useConfirm } from '../common/ConfirmDialog';
import { useToast } from '../common/Toast';
import ModeToggle from '../common/ModeToggle';
import TraceTimeline from './TraceTimeline';
import { MODE } from '../../lib/eventContract';
import { SAMPLE_QUESTION } from '../../data/mockData';
import { getSolution, SOLUTIONS } from '../../data/agentOptions';
import { recommendSolution } from '../../lib/api';
import RichText from '../common/RichText';

// Data-aware starter questions — gives non-technical users a one-click way in,
// grounded in whatever they've actually uploaded (end-user-first).
function suggestQuestions(files) {
  const list = files || [];
  if (list.length === 0) {
    return ['What kinds of questions can you answer about my data?'];
  }
  const find = (...kinds) => list.find((f) => kinds.includes(f.kind));
  const out = [];
  const tabular = find('csv', 'excel');
  if (tabular) {
    out.push(`Summarize the key insights in ${tabular.name}.`);
    out.push(`Are there any data-quality issues in ${tabular.name}?`);
    out.push(`What does each column in ${tabular.name} mean?`);
  }
  const db = find('sqlite');
  if (db) out.push(`What tables are in ${db.name} and how do they relate?`);
  const doc = find('pdf', 'md');
  if (doc) out.push(`Summarize ${doc.name} and list the key people, orgs and dates.`);
  const json = find('json');
  if (json) out.push(`What is the structure of ${json.name}?`);
  return [...new Set(out)].slice(0, 4);
}

export default function ChatPanel({ agent, settings, files, onUpload, onOpenSettings, tools, onOpenTools, liveCtl }) {
  const { mode, setMode, feed, busy, send, clear } = agent;
  const live = liveCtl?.live;
  const [text, setText] = useState('');
  const scrollRef = useRef(null);
  const confirm = useConfirm();
  const toast = useToast();
  // §12.5 — solution recommendation gate: hold the pending question while the
  // user reviews/picks the suggested solution, then run with their choice.
  const [pending, setPending] = useState(null);   // question string awaiting a pick
  const [rec, setRec] = useState(null);            // { recommended, reason, alternatives }
  const [recBusy, setRecBusy] = useState(false);
  const [chosen, setChosen] = useState('react');

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [feed]);

  const doClear = async () => {
    if (!clear) return;
    if (feed.length && !(await confirm({ title: 'Clear conversation?', message: 'This wipes the chat history for this session.', confirmText: 'Clear', danger: true }))) return;
    clear();
    setText('');
    toast('Conversation cleared');
  };

  const looksLikeQuestion = (t) => t.length > 8 && !/^(hi|hey|hello|thanks|thank you|yo|ok|okay)\b/i.test(t);

  const askRecommend = async (q) => {
    setPending(q); setRec(null); setRecBusy(true); setText('');
    try {
      const e = settings.endpoint;
      const r = await recommendSolution(q, liveCtl?.sid, {
        model: e.model, api_base: e.apiBase, api_key: e.apiKey, api_version: e.apiVersion || '',
      });
      setRec(r); setChosen(r.recommended || settings.solution || 'react');
    } catch {
      setPending(null); send(q);            // backend unavailable → just run with current settings
    } finally { setRecBusy(false); }
  };

  const runChosen = (solutionId) => {
    const q = pending; setPending(null); setRec(null);
    if (q) send(q, { solution: solutionId });
  };

  const cancelRec = () => { setText(pending || ''); setPending(null); setRec(null); };

  const submit = () => {
    if (busy || pending) return;
    const t = text.trim();
    if (!t) return;
    // Support `/clear` as a chat slash-command so the user never has to leave the keyboard.
    if (t.toLowerCase() === '/clear') { doClear(); return; }
    // §12.5 — recommend a solution before running (Live mode + a real question).
    if (live && liveCtl?.sid && looksLikeQuestion(t)) { askRecommend(t); return; }
    send(t);
    setText('');
  };

  // Run a ready-made question (suggestion chip / sample) through the same path as
  // a typed one, so the solution-recommendation gate still applies in Live mode.
  const ask = (q) => {
    if (busy || pending || !q) return;
    if (live && liveCtl?.sid && looksLikeQuestion(q)) { askRecommend(q); return; }
    send(q);
  };

  const copyText = async (t) => {
    try { await navigator.clipboard.writeText(t || ''); toast('Copied to clipboard'); }
    catch { toast('Copy failed', 'error'); }
  };

  return (
    <div className="panel chat-panel">
      <div className="chat-head" style={{ flexWrap: 'wrap' }}>
        <span className="chat-title"><Icon.bot width={17} height={17} style={{ color: 'var(--ds-teal-600)', verticalAlign: '-3px', marginRight: 6 }} />Conversation</span>
        <span style={{ flex: 1 }} />
        <button className="agent-chip" onClick={onOpenTools} title="Manage tools" data-tour="tour-tools">
          <Icon.tool width={14} height={14} />
          {tools.filter((t) => t.enabled).length} tools
        </button>
        <button className="agent-chip" onClick={onOpenSettings} title="Agent settings" data-tour="tour-settings">
          <Icon.settings width={14} height={14} />
          <b>{settings.endpoint.model || 'set model'}</b>
          <span className="ac-sep">·</span>
          {getSolution(settings.solution).name}
        </button>

        {/* Live backend toggle. The agent always runs on the user's uploaded files (the
            workspace session) — there is no benchmark-task picker. */}
        <button className={`agent-chip live-chip ${live ? 'on' : ''}`} onClick={liveCtl?.onToggleLive} disabled={busy} title="Run against the real backend">
          <span className={`live-dot ${live ? (liveCtl.health ? 'up' : 'down') : ''}`} />
          {live ? 'Live' : 'Demo'}
        </button>
        {live && liveCtl?.fileCount === 0 && (
          <span className="agent-chip" title="Upload data so the agent has something to analyze" style={{ color: 'var(--ds-warn)' }}>
            <Icon.alert width={13} height={13} /> No files yet
          </span>
        )}
        <span data-tour="tour-mode" style={{ display: 'inline-flex' }}><ModeToggle mode={mode} onChange={setMode} disabled={busy} /></span>
        <button
          className="agent-chip"
          onClick={doClear}
          disabled={busy || feed.length === 0}
          title="Clear the conversation (or type /clear)"
        >
          <Icon.trash width={13} height={13} /> Clear
        </button>
      </div>

      {live && liveCtl.health === false && (
        <div className="live-warn"><Icon.alert width={14} height={14} /> Backend offline. Start it: <code className="inline-code">uv run uvicorn server.app:app --port 8000</code></div>
      )}

      <div className="chat-scroll" ref={scrollRef}>
        {feed.length === 0 && (
          <WelcomeState
            mode={mode} live={live}
            fileCount={liveCtl?.fileCount || 0}
            hasKey={!!settings.endpoint.apiKey}
            suggestions={suggestQuestions(files)}
            onUpload={onUpload}
            onOpenSettings={onOpenSettings}
            onAsk={ask}
            onTry={() => ask(SAMPLE_QUESTION)}
          />
        )}
        {feed.map((entry) =>
          entry.type === 'user' ? (
            <div className="bubble user" key={entry.id}>{entry.text}</div>
          ) : entry.type === 'msg' ? (
            <div className="bubble agent" key={entry.id}>
              <div className="b-head"><Icon.bot width={14} height={14} /> Assistant <span className="b-spacer" />
                {!entry.pending && <button className="bubble-copy" title="Copy answer" onClick={() => copyText(entry.text)}><Icon.copy width={13} height={13} /></button>}
              </div>
              {entry.pending ? <span className="typing"><i /><i /><i /></span> : <RichText text={entry.text} />}
            </div>
          ) : (
            <div key={entry.id} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <TraceTimeline run={entry} controls={agent} />
              {entry.summary && (
                <div className="bubble agent">
                  <div className="b-head"><Icon.bot width={14} height={14} /> Answer <span className="b-spacer" />
                    <button className="bubble-copy" title="Copy answer" onClick={() => copyText(entry.summary)}><Icon.copy width={13} height={13} /></button>
                  </div>
                  <RichText text={entry.summary} />
                </div>
              )}
            </div>
          )
        )}
      </div>

      {pending && (
        <div className="solrec">
          <div className="solrec-top">
            <Icon.spark width={14} height={14} />
            <b>Suggested approach</b>
            {rec?.by && <span className="solrec-by">{rec.by === 'llm' ? 'AI' : 'rule-based'}</span>}
            <span className="spacer" style={{ flex: 1 }} />
            <button className="icon-btn" onClick={cancelRec} title="Cancel"><Icon.x width={14} height={14} /></button>
          </div>
          {recBusy || !rec ? (
            <div className="solrec-busy"><span className="typing"><i /><i /><i /></span> Picking the best fit for your question…</div>
          ) : (
            <>
              <div className="solrec-opts">
                {SOLUTIONS.map((s) => (
                  <button
                    key={s.id}
                    className={`solrec-opt ${chosen === s.id ? 'on' : ''}`}
                    onClick={() => setChosen(s.id)}
                    title={s.whenToUse}
                  >
                    <span className="solrec-name">
                      {s.name}
                      {s.id === rec.recommended && <span className="solrec-badge">Recommended</span>}
                    </span>
                    <span className="solrec-when">{s.whenToUse}</span>
                  </button>
                ))}
              </div>
              <div className="solrec-reason"><Icon.spark width={12} height={12} /> {rec.reason}</div>
              <div className="solrec-actions">
                <button className="btn btn-ghost btn-sm" onClick={cancelRec}>Cancel</button>
                <button className="btn btn-primary btn-sm" onClick={() => runChosen(chosen)}>
                  Run with {getSolution(chosen).name}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      <div className="chat-input-bar" data-tour="tour-input">
        <textarea
          className="chat-input"
          rows={1}
          placeholder={pending ? 'Choose an approach above to run…' : 'What would you like to know?  (type /clear to wipe the chat)'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
          disabled={busy || !!pending}
        />
        <button className="send-btn" onClick={submit} disabled={busy || !!pending} title="Send"><Icon.send width={18} height={18} /></button>
      </div>
    </div>
  );
}

// End-user-first welcome: a short intro + a live "getting started" checklist that
// nudges the two prerequisites (data + model), then one-click suggested questions.
function WelcomeState({ mode, live, fileCount, hasKey, suggestions, onUpload, onOpenSettings, onAsk, onTry }) {
  return (
    <div className="welcome">
      <div className="welcome-icon"><Icon.bot width={28} height={28} /></div>
      <div className="welcome-title">Ask anything about your data</div>
      <p className="welcome-sub">
        {live
          ? <>Your questions run on the real backend over the data <i>you</i> upload — nothing else.</>
          : <>You're in <b>{mode === MODE.COPILOT ? 'Co-pilot' : 'Autopilot'}</b> mode{mode === MODE.COPILOT ? ' — the agent pauses for your approval at each step.' : ' — watch the agent work end-to-end.'}</>}
      </p>

      {live && (
        <div className="gs-list">
          <GsItem n={1} done={fileCount > 0} label="Add your data"
            hint={fileCount > 0 ? `${fileCount} file${fileCount === 1 ? '' : 's'} ready` : 'CSV, Excel, JSON, SQLite or PDF'}
            actionLabel={fileCount > 0 ? null : 'Upload'} icon="upload" onAction={onUpload} />
          <GsItem n={2} done={hasKey} label="Connect a model"
            hint={hasKey ? 'Model connected' : 'Add your model + API key'}
            actionLabel={hasKey ? null : 'Settings'} icon="settings" onAction={onOpenSettings} />
          <GsItem n={3} done={false} current label="Ask your first question"
            hint="Type below, or pick a suggestion" />
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="suggest">
          <div className="suggest-label">Try asking</div>
          <div className="suggest-chips">
            {suggestions.map((q) => (
              <button key={q} className="suggest-chip" onClick={() => onAsk(q)} title="Ask this">
                <Icon.spark width={13} height={13} /> <span>{q}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {!live && (
        <button className="btn btn-ghost btn-sm" style={{ marginTop: 14 }} onClick={onTry}>
          <Icon.spark width={14} height={14} /> Try a sample question
        </button>
      )}
    </div>
  );
}

function GsItem({ n, done, current, label, hint, actionLabel, icon, onAction }) {
  const IconC = icon === 'upload' ? Icon.upload : Icon.settings;
  return (
    <div className={`gs-item ${done ? 'done' : ''} ${current ? 'current' : ''}`}>
      <span className="gs-mark">{done ? <Icon.check width={12} height={12} /> : n}</span>
      <div className="gs-text">
        <div className="gs-label">{label}</div>
        <div className="gs-hint">{hint}</div>
      </div>
      {actionLabel && onAction && (
        <button className="btn btn-ghost btn-sm gs-action" onClick={onAction}>
          <IconC width={13} height={13} /> {actionLabel}
        </button>
      )}
    </div>
  );
}
