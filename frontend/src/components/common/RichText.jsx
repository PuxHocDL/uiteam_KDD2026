// Minimal inline markdown — **bold**, `code`, and newlines. No dependency.
// Used for agent thoughts and answer summaries so **labels** render bold.
export default function RichText({ text }) {
  const lines = String(text ?? '').split('\n');
  return lines.map((line, i) => (
    <span key={i}>
      {i > 0 && <br />}
      <Inline text={line} />
    </span>
  ));
}

function Inline({ text }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => {
    if (p.startsWith('**')) return <b key={i}>{p.slice(2, -2)}</b>;
    if (p.startsWith('`')) return <code key={i} className="inline-code">{p.slice(1, -1)}</code>;
    return <span key={i}>{p}</span>;
  });
}
