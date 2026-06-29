import { Icon } from './Icons';

export default function Modal({ title, onClose, children, size }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className={`modal ${size || ''}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <span className="spacer" />
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon.x /></button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
