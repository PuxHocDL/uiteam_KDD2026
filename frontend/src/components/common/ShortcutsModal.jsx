import Modal from './Modal';
import { Icon } from './Icons';

// Discoverable keyboard shortcuts — opened with `?` or the help button in the
// top bar. Keep this list in sync with the handlers in App.jsx / the chat input.
const GROUPS = [
  {
    title: 'Asking',
    items: [
      { keys: ['/'], desc: 'Jump to the question box' },
      { keys: ['Enter'], desc: 'Send your question' },
      { keys: ['Shift', 'Enter'], desc: 'New line without sending' },
      { keys: ['/clear'], desc: 'Clear the conversation', typed: true },
    ],
  },
  {
    title: 'Everywhere',
    items: [
      { keys: ['?'], desc: 'Open this shortcuts panel' },
      { keys: ['Esc'], desc: 'Close a dialog or cancel' },
      { keys: ['←', '→'], desc: 'Back / Next during the guided tour' },
    ],
  },
];

export default function ShortcutsModal({ onClose }) {
  return (
    <Modal size="md" onClose={onClose} title={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Icon.zap width={16} height={16} /> Keyboard shortcuts
      </span>
    }>
      <div className="shortcuts-body">
        {GROUPS.map((g) => (
          <div className="shortcut-group" key={g.title}>
            <div className="shortcut-group-title">{g.title}</div>
            {g.items.map((it) => (
              <div className="shortcut-row" key={it.desc}>
                <span className="shortcut-desc">{it.desc}</span>
                <span className="shortcut-keys">
                  {it.typed
                    ? <kbd className="kbd typed">{it.keys[0]}</kbd>
                    : it.keys.map((k, i) => (
                        <span key={k}>
                          {i > 0 && <span className="kbd-plus">+</span>}
                          <kbd className="kbd">{k}</kbd>
                        </span>
                      ))}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Modal>
  );
}
