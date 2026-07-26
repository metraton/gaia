// ─────────────────────────────────────────────────────────────────────────
// contrast-audit.cjs — the WCAG 2.1 contrast GUARDRAIL for the swappable
// palettes. @version 2.1.0 (part of the diagram-builder skill; keep in sync
// with the palette token blocks in index.html)
//
// Run: npm run contrast   (node tools/contrast-audit.cjs)
//
// WHY IT PARSES index.html INSTEAD OF CARRYING ITS OWN COPY OF THE COLOURS.
// A palette is a set of DESIGN TOKENS declared in index.html. If this tool
// duplicated those hex values it would drift the first time a token changed and
// then report ratios for colours nobody renders — a false pass, the exact trap
// the layout guardrail avoids by measuring the real render. So the token blocks
// in index.html are the SINGLE SOURCE OF TRUTH: this script extracts them, so a
// palette edit is audited by construction.
//
// WHAT IT ASSERTS. For every (palette × theme) it resolves the token set, then
// checks a declared list of REAL foreground/background pairs — the pairs the
// stylesheet actually renders (body copy on a box, the kicker on a tinted box,
// a zone title on the zone fill, a border against the canvas). Soft tints are
// `rgba()` over their real substrate, so the ratio is computed on the COMPOSITED
// colour a viewer actually sees, not on the translucent token in isolation.
//
// THRESHOLDS (WCAG 2.1):
//   AA text        4.5:1  — normal-size text. Every text role in this engine
//                           qualifies as normal-size: the largest is .bar h1 at
//                           23px/700, still under the 18.66px-bold large-text
//                           threshold's companion requirement, and every other
//                           role (kicker 10.5px, description 12px, box title
//                           15–17px, zone title 13–14.5px) is far below it. So
//                           no pair gets the 3:1 large-text exemption.
//   AA non-text    3.0:1  — UI component boundaries (WCAG 1.4.11): the cell,
//                           zone, and control borders that carry the grid's
//                           structure. If a border falls below this the layout
//                           itself stops being legible, independent of any text.
//
// WHAT `gates` MEANS, AND WHY neutral GATES NOTHING.
// `neutral` is the palette every already-published deck renders with. Reaching AA
// there would mean changing those tokens — which changes how every existing deck
// LOOKS, a harder constraint than the contrast target. So neutral's shortfalls are
// MEASURED AND REPORTED, never silently "fixed": it gates no load, the run prints
// its real numbers, and `contrast` exists as the palette that does guarantee AA.
// Reporting the gap is the honest outcome; hiding it behind a green would not be.
// ─────────────────────────────────────────────────────────────────────────
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

// ── token extraction ─────────────────────────────────────────────────────
// Strip CSS comments FIRST. index.html documents its own layout model at length
// and those comments quote CSS (`… { flex: var(--span,1) 1 0 }`), so braces
// inside comments would desync any rule scanner that did not remove them.
const CSS = (() => {
  const style = HTML.slice(HTML.indexOf('<style>') + 7, HTML.indexOf('</style>'));
  return style.replace(/\/\*[\s\S]*?\*\//g, '');
})();

// Every flat rule in the stylesheet as {selectors[], body}. Only flat rules are
// collected (a nested @container body contains braces and simply fails to match,
// which is fine — every palette block is top-level).
const RULES = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({
  selectors: m[1].split(',').map(s => s.trim()).filter(Boolean),
  body: m[2],
}));

// Pull the declaration block whose selector LIST contains `selector`, and parse
// its `--token: value;` pairs. Matching a selector inside a GROUPED list matters:
// the two Rosé Pine variants share one light block
// (`:root[data-palette="rose-pine"], :root[data-palette="rose-pine-moon"]`), and
// an exact-string scanner would miss both. Returns null when absent, so a renamed
// or deleted palette block fails loudly at the call site rather than auditing a
// silently empty token set.
function blockFor(selector) {
  const out = {};
  let found = false;
  for (const rule of RULES) {
    if (!rule.selectors.includes(selector)) continue;
    found = true;
    for (const m of rule.body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  }
  return found ? out : null;
}

// A palette's token set = the neutral base for that theme, overlaid by the
// palette's own block. Mirrors the CSS cascade exactly (the palette blocks
// redeclare the full token set, so this is a belt-and-braces merge).
function tokens(palette, theme) {
  const base = { ...(blockFor(':root') || {}), ...(theme === 'dark' ? (blockFor('html.dark') || {}) : {}) };
  if (palette === 'neutral') return base;
  const lightSel = `:root[data-palette="${palette}"]`;
  const darkSel = `html.dark[data-palette="${palette}"]`;
  const light = blockFor(lightSel);
  if (!light) throw new Error(`no light token block for palette "${palette}" (expected selector: ${lightSel})`);
  const merged = { ...base, ...light };
  if (theme === 'dark') {
    const dark = blockFor(darkSel);
    if (!dark) throw new Error(`no dark token block for palette "${palette}" (expected selector: ${darkSel})`);
    Object.assign(merged, dark);
  }
  return merged;
}

// ── colour maths ─────────────────────────────────────────────────────────
function parseColor(v) {
  const s = String(v).trim();
  let m = s.match(/^#([0-9a-f]{6})$/i);
  if (m) { const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1]; }
  m = s.match(/^#([0-9a-f]{3})$/i);
  if (m) return [...m[1]].map(c => parseInt(c + c, 16)).concat(1);
  m = s.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  throw new Error(`unparseable colour: ${v}`);
}
// Composite a possibly-translucent colour over an opaque substrate — the soft
// tints (`--crit-soft` etc.) are rgba, so the colour a viewer SEES is the
// composite, and that is what the ratio must be computed against.
function over(fg, bg) {
  const a = fg[3];
  return [0, 1, 2].map(i => fg[i] * a + bg[i] * (1 - a)).concat(1);
}
// WCAG 2.1 relative luminance + contrast ratio.
const chan = c => { const s = c / 255; return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
const lum = ([r, g, b]) => 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
function ratio(a, b) {
  const la = lum(a), lb = lum(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// ── the audited pairs ────────────────────────────────────────────────────
// Each pair names the RENDERED situation it stands for, its foreground token,
// its background as a stack (composited left→right, first entry opaque), the WCAG
// threshold that applies, and its `load` — which reading burden it carries:
//   primary  the deck's main reading load (titles, body copy, kickers,
//            descriptions, section headings) — text a viewer reads continuously
//   accent   a semantic accent used AS TEXT on its own tint (a coloured kicker)
//   nontext  a UI boundary (cell / zone / control border), WCAG 1.4.11
// A palette declares which loads it GATES (see PALETTES), so the audit can hold a
// palette to the bar it promises without pretending the frozen one meets it.
//
// Every pair below is a place the stylesheet really puts that foreground on that
// background — see the cited selector. Nothing hypothetical is audited.
//
// The TOKEN names kept a vocabulary the CLASS names dropped: `--crit` paints
// `.box.bad` / `.zone.bad`, `--olive` paints `.box.good` / `.zone.good`, and
// `--strong` paints `.box.accent`. So a pair's `fg` names the token while its
// `what` names the selector, and the two read differently on purpose.
const PAIRS = [
  // primary reading load
  { id: 'body-on-bg',       what: 'body copy on the canvas',                          fg: '--body',  bg: ['--bg'],                  min: 4.5, kind: 'AA text',     load: 'primary' },
  { id: 'ink-on-bg',        what: 'deck title on the canvas (.bar h1)',               fg: '--ink',   bg: ['--bg'],                  min: 4.5, kind: 'AA text',     load: 'primary' },
  { id: 'muted-on-bg',      what: 'deck subtitle / chip label (.sub, .chip)',         fg: '--muted', bg: ['--bg'],                  min: 4.5, kind: 'AA text',     load: 'primary' },
  { id: 'ink-on-surface',   what: 'box title (.box .t)',                              fg: '--ink',   bg: ['--surface'],             min: 4.5, kind: 'AA text',     load: 'primary' },
  { id: 'muted-on-surface', what: 'box kicker + description (.box .k, .box .m)',      fg: '--muted', bg: ['--surface'],             min: 4.5, kind: 'AA text',     load: 'primary' },
  { id: 'muted-on-surf2',   what: 'rail label / muted box (.rail-title, .box.muted)', fg: '--muted', bg: ['--surface2'],            min: 4.5, kind: 'AA text',     load: 'primary' },
  { id: 'ztitle-on-zone',   what: 'section title on the zone fill (.ztitle)',         fg: '--muted', bg: ['--bg', '--zone'],        min: 4.5, kind: 'AA text',     load: 'primary' },
  { id: 'body-on-surface',  what: 'detail panel copy (.p-summary)',                   fg: '--body',  bg: ['--surface'],             min: 4.5, kind: 'AA text',     load: 'primary' },

  // accent text on its own tinted substrate (the kicker inside a coloured box)
  { id: 'bad-kicker',    what: 'bad kicker on a bad box (.box.bad .k)',              fg: '--crit',   bg: ['--surface', '--crit-soft'],   min: 4.5, kind: 'AA text', load: 'accent' },
  { id: 'warn-kicker',   what: 'warn kicker on a warn box (.box.warn .k)',           fg: '--warn',   bg: ['--surface', '--warn-soft'],   min: 4.5, kind: 'AA text', load: 'accent' },
  { id: 'good-kicker',   what: 'good kicker on a good box (.box.good .k)',           fg: '--olive',  bg: ['--surface', '--olive-soft'],  min: 4.5, kind: 'AA text', load: 'accent' },
  { id: 'accent-kicker', what: 'accent kicker on an accent box (.box.accent .k)',    fg: '--strong', bg: ['--surface', '--strong-soft'], min: 4.5, kind: 'AA text', load: 'accent' },
  { id: 'bad-ztitle',    what: 'bad section title (.zone.bad .ztitle)',              fg: '--crit',   bg: ['--bg', '--crit-soft'],        min: 4.5, kind: 'AA text', load: 'accent' },
  { id: 'good-ztitle',   what: 'good section title (.zone.good .ztitle)',            fg: '--olive',  bg: ['--bg', '--olive-soft'],       min: 4.5, kind: 'AA text', load: 'accent' },
  { id: 'clay-kicker',   what: 'panel kicker / inline code (.p-kicker, code)',       fg: '--clay',   bg: ['--surface'],                  min: 4.5, kind: 'AA text', load: 'accent' },
  { id: 'chip-on-text',  what: 'active chip label (.chip.on)',                       fg: '--ink',    bg: ['--bg', '--clay-soft'],        min: 4.5, kind: 'AA text', load: 'accent' },

  // non-text: component boundaries must be discernible (WCAG 1.4.11)
  { id: 'line-on-bg',      what: 'box / control border against the canvas (--line)', fg: '--line',      bg: ['--bg'],      min: 3.0, kind: 'AA non-text', load: 'nontext' },
  { id: 'zoneline-on-bg',  what: 'section border against the canvas (--zone-line)',  fg: '--zone-line', bg: ['--bg'],      min: 3.0, kind: 'AA non-text', load: 'nontext' },
  { id: 'line-on-surface', what: 'box border against the box fill (--line)',         fg: '--line',      bg: ['--surface'], min: 3.0, kind: 'AA non-text', load: 'nontext' },
];

// Palettes to audit, each declaring which LOADS it gates. A gated load that falls
// below threshold FAILS the run; an ungated one is measured and reported only.
//   neutral         gates nothing — frozen so published decks keep their look.
//   rose-pine(-moon) gates primary + accent: it is offered as a usable skin, so
//                   the text a viewer reads must clear AA. `nontext` is NOT gated
//                   because Rosé Pine's highlight-* border roles are deliberately
//                   low-contrast hairlines (that restraint IS the palette's
//                   identity); forcing them to 3:1 would stop it being Rosé Pine.
//                   Reported, so the number is never hidden.
//   contrast        gates EVERYTHING — it exists precisely to guarantee AA.
const PALETTES = [
  { name: 'neutral',        gates: [] },
  { name: 'rose-pine',      gates: ['primary', 'accent'] },
  { name: 'rose-pine-moon', gates: ['primary', 'accent'] },
  { name: 'contrast',       gates: ['primary', 'accent', 'nontext'] },
];
const THEMES = ['light', 'dark'];

let failures = 0, shortfalls = 0, checked = 0;
const failLines = [];
console.log('\n══════════ PALETTE CONTRAST AUDIT (WCAG 2.1) ══════════');
console.log('Tokens parsed from index.html — the palette blocks are the single source of truth.\n');

for (const pal of PALETTES) {
  for (const theme of THEMES) {
    const t = tokens(pal.name, theme);
    const rows = [];
    for (const p of PAIRS) {
      // Resolve the background stack: the first entry is opaque, each later
      // entry composites over the result (a soft tint over a surface).
      let bg = parseColor(t[p.bg[0]]);
      for (const layer of p.bg.slice(1)) bg = over(parseColor(t[layer]), bg);
      const fg = over(parseColor(t[p.fg]), bg);   // fg may itself be translucent
      const r = ratio(fg, bg);
      const ok = r >= p.min;
      const gated = pal.gates.includes(p.load);
      checked++;
      if (!ok) { shortfalls++; if (gated) { failures++; failLines.push(`${pal.name}/${theme} ${p.id} ${r.toFixed(2)}:1 < ${p.min}`); } }
      rows.push({ p, r, ok, gated });
    }
    const worst = rows.reduce((a, b) => (b.r < a.r ? b : a));
    const bad = rows.filter(r => !r.ok);
    const badGated = bad.filter(r => r.gated).length;
    const verdict = bad.length === 0 ? 'ALL PASS'
      : badGated ? `${badGated} GATED FAILURE(S)`
      : `${bad.length} below threshold — all ungated, reported only`;
    console.log(`● ${pal.name} · ${theme}  —  ${verdict}   (worst: ${worst.p.id} ${worst.r.toFixed(2)}:1)`);
    for (const { p, r, ok, gated } of rows)
      console.log(`    [${ok ? 'PASS' : gated ? 'FAIL' : 'LOW '}] ${r.toFixed(2)}:1  (min ${p.min.toFixed(1)} ${p.kind}, ${p.load}${gated ? ', gated' : ''})  ${p.id} — ${p.what}`);
    console.log('');
  }
}

console.log('═══════════════════════════════════════════════════════');
console.log(`${checked} pairs measured across ${PALETTES.length} palettes × ${THEMES.length} themes.`);
if (failures === 0) {
  console.log(`ALL GATED PAIRS PASS${shortfalls ? ` — ${shortfalls} ungated shortfall(s) reported above (frozen neutral + Rosé Pine hairline borders).` : '.'}\n`);
  process.exit(0);
}
console.log(`FAIL — ${failures} GATED pair(s) below threshold:`);
for (const l of failLines) console.log(`    ${l}`);
console.log('');
process.exit(1);
