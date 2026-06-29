// Turn a raw error (Error or string) into a plain-language message an end user can
// act on, while keeping the original text for an optional "Details" expander
// (human-friendly messages, technical detail hidden behind a layer).
export function humanizeError(err) {
  const raw = (err && err.message) ? String(err.message) : String(err ?? '');
  const low = raw.toLowerCase();

  let message;
  if (!raw.trim()) {
    message = 'Something went wrong.';
  } else if (low.includes('failed to fetch') || low.includes('networkerror') || low.includes('load failed')
      || low.includes('err_connection') || low.includes('econnrefused') || low.includes('network request failed')) {
    message = "Can't reach the server. Make sure the backend is running, then try again.";
  } else if (low.includes('not authenticated') || low.includes(' 401') || low.includes('unauthorized')) {
    message = 'Your session has expired — please sign in again.';
  } else if (low.includes('429') || low.includes('rate limit') || low.includes('insufficient_quota') || low.includes('quota')) {
    message = 'The model is rate-limited or out of quota. Wait a moment, or check your API plan.';
  } else if (low.includes('413') || low.includes('too large') || low.includes('exceeds')) {
    message = 'That file is too large — the limit is 50 MB.';
  } else if (low.includes('missing model api key') || low.includes('no api key') || (low.includes('api key') && low.includes('required'))) {
    message = 'Add your model name and API key in Settings first.';
  } else if (low.includes('timeout') || low.includes('timed out')) {
    message = 'The request timed out. Try again.';
  } else if (/\b5\d\d\b/.test(raw) || low.includes('internal server error')) {
    message = 'The server hit an error. Try again in a moment.';
  } else if (low.includes('404') || low.includes('not found')) {
    message = 'That item could not be found — it may have been deleted.';
  } else if (low.includes('415') || low.includes('could not read') || low.includes('unsupported')) {
    message = "That file type can't be read here. Try CSV, Excel, JSON, SQLite, PDF or text.";
  } else {
    message = raw.length <= 160 ? raw : `${raw.slice(0, 160)}…`;
  }

  return { message, detail: raw };
}
