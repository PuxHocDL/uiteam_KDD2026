import { useCallback } from 'react';

/**
 * Thin drag handle for splitter layouts. `axis="x"` resizes width (col-resize),
 * `axis="y"` resizes height (row-resize). Calls `onResize(deltaPx)` with the
 * incremental movement on each pointer move, so the parent clamps & stores the
 * running size. Pointer capture keeps the drag alive outside the handle.
 */
export default function Resizer({ axis = 'x', onResize, title }) {
  const onPointerDown = useCallback((e) => {
    e.preventDefault();
    let last = axis === 'x' ? e.clientX : e.clientY;
    const move = (ev) => {
      const cur = axis === 'x' ? ev.clientX : ev.clientY;
      const d = cur - last;
      if (d) { last = cur; onResize(d); }
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('is-resizing');
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    document.body.classList.add('is-resizing');
  }, [axis, onResize]);

  return (
    <div
      className={`resizer resizer-${axis}`}
      onPointerDown={onPointerDown}
      onDoubleClick={() => onResize(0, true)}
      role="separator"
      aria-orientation={axis === 'x' ? 'vertical' : 'horizontal'}
      title={title || 'Drag to resize'}
    />
  );
}
