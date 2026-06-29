# Data Agent Studio — UI Style Guide

> Single source of truth for the app's look & feel. The live tokens live in
> [`frontend/src/styles/theme.css`](../frontend/src/styles/theme.css); component
> patterns live in [`frontend/src/styles/app.css`](../frontend/src/styles/app.css).
> This file is the human-readable reference — if a value here disagrees with
> `theme.css`, **`theme.css` wins**.

---

## 1. Brand foundations

The palette is built on two brand anchors, plus neutral grays and functional
status colors.

| Role | Token code | Hex | Used for |
|---|---|---|---|
| **Primary brand ink** | PC-2 Dark Purple | `#32005A` | Links, icons, headings, deep end of the brand gradient, table headers |
| **Bright accent** | PC-1 Turquoise | `#50DCE1` | CTA fills, brand mark, focus glow, active accents |
| Accent (darker) | PC-1-80 / turquoise-dark | `#2BC4C9` | Fill base / hover where dark text must stay legible |
| Neutral ink | SC-1 Anthracite | `#1D211C` | Body text, code panels, dark headers |
| Neutrals | SC-2 / SC-3 grays | see §2 | Text, surfaces, borders |

> **Naming note:** in code the variables are kept as `--ds-teal-*` and `--ds-magenta`
> for backwards compatibility — only the *values* carry the palette. `--ds-teal-600`
> is the deep **purple** (strong-ink role); `--ds-teal-400` is the **turquoise**.

### Brand gradient
```
linear-gradient(160deg, #3A0867, #32005A)   /* hero / "core" cards */
```

### Logo
- Asset: [`frontend/src/assets/Logo_UIT.png`](../frontend/src/assets/Logo_UIT.png) —
  the UIT mark, a square-ish emblem (not a wordmark).
- **Keep the intrinsic aspect ratio**: set a fixed `height` and let `width: auto`,
  so the mark is never stretched or squished.
- Reference sizes in app: top bar `height: 30px; width: auto`; login/register
  `height: 48px; width: auto`.

---

## 2. Color tokens — Light (default)

Canonical values from `:root` in `theme.css`.

### Brand ramp
| Token | Hex | Note |
|---|---|---|
| `--ds-teal-600` | `#32005A` | PC-2 — strong ink: links, icons, gradient deep end |
| `--ds-teal-500` | `#2BC4C9` | darker turquoise — fill base / hover |
| `--ds-teal-400` | `#50DCE1` | PC-1 brand — accents, brand mark, focus glow, CTA fill |
| `--ds-teal-200` | `#B9F1F3` | PC-1-40 |
| `--ds-teal-50`  | `#DCF8F9` | PC-1-20 (tint background) |
| `--ds-magenta`  | `#32005A` | PC-2 Dark Purple |
| `--ds-magenta-soft` | `#D6CCDE` | PC-2-20 |
| `--ds-amber` | `#FAAD14` | NC-3 Warning Yellow |

### Neutrals / surfaces
| Token | Hex | Role |
|---|---|---|
| `--ds-bg` | `#EDEDED` | App canvas (SC-3-40 Silver Gray) |
| `--ds-surface` | `#FFFFFF` | Panels / cards |
| `--ds-surface-2` | `#F6F6F6` | SC-3-20 |
| `--ds-surface-3` | `#EDEDED` | SC-3-40 |
| `--ds-text` | `#1D211C` | SC-1 Anthracite |
| `--ds-text-soft` | `#4A4D49` | SC-1-80 |
| `--ds-muted` | `#5C6062` | SC-2 Dark Gray |
| `--ds-faint` | `#9DA0A1` | SC-2-60 |
| `--ds-border` | `#E4E4E4` | SC-3-60 (default dividers) |
| `--ds-border-strong` | `#D2D2D2` | SC-3 (emphasis dividers, inputs) |

### Status (functional — error is ORANGE, not red)
| Token | Hex | Background token | Hex |
|---|---|---|---|
| `--ds-success` | `#00B050` | `--ds-success-bg` | `#E6FFF1` |
| `--ds-error` | `#FF5A00` | `--ds-error-bg` | `#FFDECC` |
| `--ds-warn` | `#FAAD14` | `--ds-warn-bg` | `#FEEFD0` |
| `--ds-info` | `#0000A5` | `--ds-info-bg` | `#CCCCED` |

### Table header (stable in both themes — does not flip)
| Token | Hex |
|---|---|
| `--ds-table-head` | `#32005A` (PC-2) |
| `--ds-table-head-hover` | `#5B337B` (PC-2-80) |

---

## 3. Color tokens — Dark mode

Activated by `[data-theme='dark']`. Black surfaces, white text. On black the PC-2
purple is too dark to read, so the **brand role flips to PC-1 Turquoise** for
links/icons; purple returns only as a light accent (PC-2-40).

### Neutrals / surfaces
| Token | Hex | Role |
|---|---|---|
| `--ds-bg` | `#000000` | BG-2 Black canvas |
| `--ds-surface` | `#0C0D0C` | Panels |
| `--ds-surface-2` | `#161816` | — |
| `--ds-surface-3` | `#232623` | — |
| `--ds-text` | `#FFFFFF` | — |
| `--ds-text-soft` | `#E4E4E4` | — |
| `--ds-muted` | `#9DA0A1` | SC-2-60 |
| `--ds-faint` | `#5C6062` | SC-2 |
| `--ds-border` | `#343834` | lifted off near-black surface so dividers read |
| `--ds-border-strong` | `#4D524D` | emphasis dividers / inputs |

> **Contrast fix:** `--ds-border` / `--ds-border-strong` were originally `#232623` /
> `#3A3D3A`, which sank into the near-black surfaces. They are lifted to `#343834` /
> `#4D524D` so lines stay visible.

### Brand ramp (dark)
| Token | Hex | Note |
|---|---|---|
| `--ds-teal-600` | `#50DCE1` | PC-1 — links/icons readable on black |
| `--ds-teal-500` | `#2BC4C9` | — |
| `--ds-teal-400` | `#50DCE1` | — |
| `--ds-teal-200` | `#B9F1F3` | — |
| `--ds-teal-50`  | `#0E2E30` | deep turquoise bg |
| `--ds-magenta`  | `#AD99BD` | PC-2-40 — light purple pop on black |
| `--ds-magenta-soft` | `#1A0A2E` | — |

### Status (dark — lighter ramp steps for contrast)
| Token | Hex | Background | Hex |
|---|---|---|---|
| `--ds-success` | `#00FF6E` | `--ds-success-bg` | `#06251A` |
| `--ds-error` | `#FF7B33` | `--ds-error-bg` | `#2E1407` |
| `--ds-warn` | `#FBBD43` | `--ds-warn-bg` | `#2E2410` |
| `--ds-info` | `#6666C9` | `--ds-info-bg` | `#14143F` |

---

## 4. Trace step colors

Used by the agent's Thought → Action → Observation → Answer timeline.

| Step | Light fg / bg | Dark fg / bg |
|---|---|---|
| Thought | `#2BC4C9` / `#DCF8F9` | `#50DCE1` / `#0E2E30` |
| Action | `#32005A` / `#EDE9F2` | `#AD99BD` / `#1A0A2E` |
| Observe | `#5B337B` / `#EDE9F2` | `#73E3E7` / `#0E2E30` |
| Answer | `#1D211C` / `#EDEDED` | `#FFFFFF` / `#161816` |

---

## 5. Typography

The UI uses **Inter** throughout, **JetBrains Mono** for code.

```css
--ds-font: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
--ds-mono: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
```

| Weight token | Value | Use |
|---|---|---|
| `--ds-fw-light` | 300 | Lead / hero subtext |
| `--ds-fw-regular` | 400 | Body |
| `--ds-fw-semibold` | 600 | Captions |
| `--ds-fw-bold` | 700 | H2–H6 |
| `--ds-fw-extrabold` | 800 | Hero / H1 |

Tracking: captions/kickers use `+0.5…+1.4px` letter-spacing, uppercase.

---

## 6. Radius, shadow, layout

| Token | Value |
|---|---|
| `--ds-r-sm` | `6px` |
| `--ds-r` | `10px` |
| `--ds-r-lg` | `16px` |
| `--ds-topbar-h` | `56px` |

### Shadows — Light
```css
--ds-shadow-sm: 0 1px 2px rgba(29,33,28,.05), 0 1px 3px rgba(29,33,28,.06);
--ds-shadow:    0 4px 16px rgba(29,33,28,.07);
--ds-shadow-lg: 0 18px 50px rgba(50,0,90,.16);
```
### Shadows — Dark
```css
--ds-shadow-sm: 0 1px 2px rgba(0,0,0,.6);
--ds-shadow:    0 6px 18px rgba(0,0,0,.7);
--ds-shadow-lg: 0 20px 55px rgba(0,0,0,.8);
```

---

## 7. Component recipes (app UI)

Common patterns pulled from [`frontend/src/styles/app.css`](../frontend/src/styles/app.css).

### Buttons
```css
.btn-primary { background: var(--ds-teal-400); color: #0a0a0a; }   /* turquoise CTA */
.btn-ghost   { background: var(--ds-surface); color: var(--ds-text);
               border: 1px solid var(--ds-border-strong); }
```

### Cards & panels
```css
.card  { background: var(--ds-surface); border: 1px solid var(--ds-border);
         border-radius: var(--ds-r-lg); box-shadow: var(--ds-shadow-sm); }
.panel { background: var(--ds-surface); border: 1px solid var(--ds-border);
         border-radius: var(--ds-r-lg); box-shadow: var(--ds-shadow-sm); }
```

### Inputs (theme-aware — text always contrasts its box)
```css
input, textarea, select {
  border: 1px solid var(--ds-border-strong);
  border-radius: var(--ds-r-sm);
  background: var(--ds-surface);
  color: var(--ds-text);
}
:focus { border-color: var(--ds-teal-500); box-shadow: 0 0 0 3px var(--ds-teal-50); }
```

### Pills / chips
```css
.count-pill { color: var(--ds-teal-600); background: var(--ds-teal-50);
              border: 1px solid var(--ds-border); border-radius: 999px; }
```

### Dark-mode legibility overrides (light fills that must keep dark text)
- `.mode-opt.active` → `color: var(--ds-text); background: var(--ds-surface-3)`
- `.bubble.user` → `background: var(--ds-teal-400); color: #0a0a0a`
- ER-node bodies use hardcoded light pastel fills, so their inner text/badges are
  pinned dark (`#0a0a0a` / `#1a1a1a`) in dark mode.

---

## 8. Usage rules

1. **Never hardcode hex in components** — reference `var(--ds-*)` tokens so light/dark
   both work automatically.
2. **Error = orange** (`#FF5A00`), not red. Success neon green darkens to `#00B050` on
   light for text legibility; shines as `#00FF6E` on dark.
3. **Brand role flips by theme**: purple ink on light, turquoise ink on dark. Don't put
   white text on `--ds-teal-600` — in dark mode it's turquoise and white won't read.
4. **Table headers** use the stable `--ds-table-head` (always purple, white text) so
   they stay legible in both themes.
5. **Logo**: keep the UIT mark's aspect ratio; set `width: auto` against a fixed height.
6. **Focus** = turquoise ring: `box-shadow: 0 0 0 3px var(--ds-teal-50)`.

---

### Source files
- Tokens: [`frontend/src/styles/theme.css`](../frontend/src/styles/theme.css)
- Component styles: [`frontend/src/styles/app.css`](../frontend/src/styles/app.css)
- Logo: [`frontend/src/assets/Logo_UIT.png`](../frontend/src/assets/Logo_UIT.png)
