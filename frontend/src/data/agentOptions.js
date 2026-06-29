// =============================================================================
// Agent configuration options for the Settings panel.
//
// These mirror the REAL Phase-1 KDD code in this repo:
//  • Endpoints → agent.api_base / agent.model / agent.api_key (config.py + configs/*.yaml)
//  • Solutions → agent.agent_mode ∈ { react, dragin, multi, hybrid_b } (config.py)
//  • Consensus → the `run-consensus` CLI path (merge best candidates over rounds)
// Editing these in the UI is equivalent to swapping the YAML a CLI run would load.
// =============================================================================

export const ENDPOINT_PRESETS = [
  { id: 'azure', label: 'Azure OpenAI', apiBase: 'https://<resource>.openai.azure.com/', model: 'gpt-4o', apiVersion: '2024-02-15-preview' },
  { id: 'openai', label: 'OpenAI', apiBase: 'https://api.openai.com/v1', model: 'gpt-4.1-mini', apiVersion: '' },
  { id: 'openrouter', label: 'OpenRouter', apiBase: 'https://openrouter.ai/api/v1', model: 'qwen/qwen-2.5-72b-instruct', apiVersion: '' },
  { id: 'local', label: 'Local (vLLM / Ollama)', apiBase: 'http://localhost:8000/v1', model: 'qwen2.5:7b', apiVersion: '' },
  { id: 'custom', label: 'Custom', apiBase: '', model: '', apiVersion: '' },
];

export const SOLUTIONS = [
  {
    id: 'react',
    name: 'ReAct',
    tag: 'single agent',
    desc: 'Reason → Act loop: the model thinks, calls one tool, observes, and repeats until it submits the answer. The tuned default baseline.',
    config: 'configs/config_openAI.yaml',
    strengths: ['Fastest', 'Low cost', 'Single source'],
    whenToUse: 'Direct lookups, filters and single-table aggregations.',
  },
  {
    id: 'dragin',
    name: 'DRAGIN',
    tag: 'adaptive retrieval',
    desc: 'ReAct + dynamic retrieval: pulls extra context only when the model is uncertain (RIND threshold). Strong on knowledge-heavy questions.',
    config: 'configs/dragin_baseline.yaml',
    strengths: ['Knowledge-heavy', 'Documents', 'Adaptive'],
    whenToUse: 'Open-ended or document questions where extra context helps.',
  },
  {
    id: 'multi',
    name: 'Multi-agent',
    tag: 'planner + analyst',
    desc: 'Hierarchical: a Planner drafts an execution plan, an Analyst carries it out. Better for complex, multi-step tasks.',
    config: 'configs/hierarchical_baseline.yaml',
    strengths: ['Multi-step', 'Cross-source', 'Planner + Analyst'],
    whenToUse: 'Complex tasks spanning multiple files, joins or many steps.',
  },
  {
    id: 'hybrid_b',
    name: 'Hybrid-B',
    tag: 'difficulty routing',
    desc: 'Routes each task by difficulty signals — a light path for easy questions, deeper reasoning for hard/extreme ones.',
    config: 'configs/hybrid_b.yaml',
    strengths: ['Auto-routing', 'Balanced'],
    whenToUse: 'Let difficulty signals pick a light or deep path automatically.',
  },
];

export const DEFAULT_SETTINGS = {
  endpoint: {
    preset: 'azure',
    model: 'gpt-4o',
    apiBase: 'https://<resource>.openai.azure.com/',
    apiKey: '',
    apiVersion: '2024-02-15-preview',
    temperature: 0.0,
    maxSteps: 18,
  },
  solution: 'react',
  consensus: false,
};

export const getSolution = (id) => SOLUTIONS.find((s) => s.id === id) || SOLUTIONS[0];

// Persist agent settings (model / endpoint / API key / solution) in the browser so
// they survive leaving a session. Stored in localStorage — plaintext, local only.
const SETTINGS_KEY = 'das-settings';

export function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const s = JSON.parse(raw);
    return {
      ...DEFAULT_SETTINGS, ...s,
      endpoint: { ...DEFAULT_SETTINGS.endpoint, ...(s.endpoint || {}) },
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch { /* ignore */ }
}
