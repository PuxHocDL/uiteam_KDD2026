// Lightweight chat-intent router so the agent behaves like a chatbot: greetings
// and small talk get a conversational reply, only real data questions trigger an
// agent run. Pure client-side heuristics — no extra LLM call.

const GREET = /^(hi|hii+|hello|hey+|yo|hallo|howdy|sup|good\s+(morning|afternoon|evening)|chào|xin\s*chào)\b/i;
const THANKS = /^(thanks|thank you|thx|ty|cảm ơn|cám ơn)\b/i;
const BYE = /^(bye|goodbye|see\s*you|cya|tạm biệt)\b/i;
const IDENTITY = /(who are you|what are you|your name|are you (a|an)\s*(ai|bot|agent)|bạn là ai)/i;
const HELP = /(^help\b|what can you do|how (do|does) (this|it|you) work|how to use|hướng dẫn|bạn (làm|giúp) (được )?gì)/i;

// Signals that a message is an actual data question.
const QUESTION_HINT = /\?|how many|how much|list\b|show\b|what('?s| is| are)|which\b|when\b|where\b|who\b|average|avg\b|count\b|total\b|sum\b|number of|percentage|proportion|compare|top\s*\d|each\b|per\b|group|highest|lowest|date\b|find\b|give\b|state\b/i;

export function classifyIntent(text) {
  const t = (text || '').trim();
  if (!t) return 'question';            // empty box = explicit "run the task"
  if (GREET.test(t)) return 'greeting';
  if (THANKS.test(t)) return 'thanks';
  if (BYE.test(t)) return 'bye';
  if (IDENTITY.test(t)) return 'identity';
  if (HELP.test(t)) return 'help';
  if (QUESTION_HINT.test(t)) return 'question';
  // Short message with no data signal → treat as small talk, not a task.
  if (t.split(/\s+/).length <= 4) return 'smalltalk';
  return 'question';
}

export function chatReply(intent) {
  switch (intent) {
    case 'greeting':
      return "Hi! 👋 I'm your **data agent**. Ask me a question about the loaded data — a count, a list, a comparison — and I'll explore the files and answer with a **table you can verify**, showing every step. What would you like to know?";
    case 'thanks':
      return "You're welcome! Ask another question whenever you're ready.";
    case 'bye':
      return "Goodbye! 👋 Come back anytime you need to dig into the data.";
    case 'identity':
      return "I'm a **data-analysis agent**. I read the task's files (CSV / JSON / SQLite + `knowledge.md`), run SQL/Python through tools, and return an answer table — and you can follow every **Thought → Action → Observation** step in the trace.";
    case 'help':
      return "Ask a natural-language question about the data and I'll do the analysis. For example:\n- **How many** events had more than 10 attendees?\n- **List** the members who are officers.\n- **What is** the average consumption in 2013?\n\nYou can preview data in **Files**, add capabilities in **Tools**, and switch **Demo ↔ Live** to run the real engine.";
    default:
      return "I'm a data agent — try asking a question about the data, like *“How many …?”* or *“List the … where …”*. I'll analyse the files and answer with a table.";
  }
}
