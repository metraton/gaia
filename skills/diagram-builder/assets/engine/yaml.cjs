// yaml.cjs — reads the deck's YAML dialect into plain data, the way js-yaml's
// default schema reads the same text, and throws `<source>:<line>: <reason>`
// on anything outside the dialect instead of guessing.
//
// The dialect is what data/*.yaml and the skill's schema use: block mappings
// and sequences indented with spaces, `#` comments, plain / double-quoted /
// single-quoted scalars on one line, and `[...]` / `{...}` flow collections
// that may run over several lines. Anchors, aliases, tags, block scalars,
// multi-line plain scalars, documents markers and complex keys are refused,
// as are the plain scalars js-yaml would turn into a non-JSON type (timestamps,
// hex/octal/underscored numbers, .inf/.nan, the `<<` merge key).
//
// CommonJS for the same reason as chips.cjs: build-data.mjs, the static census
// and the tests all require it, with no require(esm) dependency.
'use strict';

class YamlError extends Error {}

function fail(source, line, reason) {
  throw new YamlError(`${source}:${line}: ${reason}`);
}

// Quote state only opens where a scalar can start, so an apostrophe inside a
// plain scalar (`title: don't`) is text, not an unterminated quote.
function scanLine(raw, state) {
  let out = '';
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i];
    if (state.quote === '"') {
      out += c;
      if (c === '\\') { out += raw[i + 1] ?? ''; i++; } else if (c === '"') state.quote = null;
      continue;
    }
    if (state.quote === "'") {
      out += c;
      if (c === "'" && raw[i + 1] === "'") { out += "'"; i++; } else if (c === "'") state.quote = null;
      continue;
    }
    if (c === '#' && (i === 0 || raw[i - 1] === ' ')) break;
    const prev = out.trimEnd().slice(-1);
    const atScalarStart = prev === '' || ':-[{,'.includes(prev);
    if ((c === '"' || c === "'") && atScalarStart) state.quote = c;
    else if ((c === '[' || c === '{') && (state.depth > 0 || atScalarStart)) state.depth++;
    else if ((c === ']' || c === '}') && state.depth > 0) state.depth--;
    out += c;
  }
  return out.trimEnd();
}

// Physical lines -> logical lines: comments stripped, blanks dropped, and a
// flow collection left open at a line end joined with the lines that close it.
function logicalLines(text, source) {
  const raw = text.replace(/^﻿/, '').split(/\r?\n/);
  const lines = [];
  for (let i = 0; i < raw.length; i++) {
    const n = i + 1;
    const indentText = raw[i].match(/^[ \t]*/)[0];
    if (indentText.includes('\t') && raw[i].trim() && !raw[i].trim().startsWith('#'))
      fail(source, n, 'a tab in the indentation (indent with spaces only)');
    const state = { quote: null, depth: 0 };
    let body = scanLine(raw[i], state).trim();
    if (!body) continue;
    if (/^(---|\.\.\.)(\s|$)/.test(body) || body.startsWith('%'))
      fail(source, n, 'document markers and directives are not part of the dialect (one document per file)');
    while (state.depth > 0 && !state.quote && i + 1 < raw.length) {
      i++;
      const more = scanLine(raw[i], state).trim();
      if (more) body += ' ' + more;
    }
    if (state.quote) fail(source, n, 'a quoted scalar must close on the line that opens it');
    if (state.depth > 0) fail(source, n, 'a flow collection is never closed');
    lines.push({ n, indent: indentText.length, body });
  }
  return lines;
}

const DQ_ESCAPES = { '\\': '\\', '"': '"', '/': '/', n: '\n', t: '\t', r: '\r', '0': '\0', b: '\b' };

// Reads a quoted scalar starting at text[pos]; returns { value, end }.
function readQuoted(text, pos, source, n) {
  const q = text[pos];
  let value = '';
  for (let i = pos + 1; i < text.length; i++) {
    const c = text[i];
    if (q === "'") {
      if (c === "'" && text[i + 1] === "'") { value += "'"; i++; continue; }
      if (c === "'") return { value, end: i + 1 };
      value += c;
      continue;
    }
    if (c === '"') return { value, end: i + 1 };
    if (c !== '\\') { value += c; continue; }
    const e = text[++i];
    if (e in DQ_ESCAPES) { value += DQ_ESCAPES[e]; continue; }
    const width = { x: 2, u: 4, U: 8 }[e];
    const hex = width && text.slice(i + 1, i + 1 + width);
    if (!width || !/^[0-9a-fA-F]+$/.test(hex) || hex.length !== width)
      fail(source, n, `unsupported escape "\\${e ?? ''}" in a double-quoted scalar`);
    value += String.fromCodePoint(parseInt(hex, 16));
    i += width;
  }
  return fail(source, n, 'unterminated quoted scalar');
}

// js-yaml's default schema, restricted to the JSON types the deck carries.
function plainScalar(text, source, n) {
  if (/^[&*!|>@`?%]/.test(text))
    fail(source, n, `"${text[0]}" starts an anchor, alias, tag, block scalar or reserved indicator — not part of the dialect`);
  if (/^[-+]?0[xob][0-9a-f_]+$/i.test(text) || (/^[-+]?[0-9][0-9_]*(\.[0-9_]*)?$/.test(text) && text.includes('_'))
    || /^[-+]?\.(inf|nan)$/i.test(text) || text === '<<' || /^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}([Tt ]|$)/.test(text))
    fail(source, n, `"${text}" would not read as plain text or a decimal number — quote it`);
  if (/^(~|null|Null|NULL)$/.test(text)) return null;
  if (/^(true|True|TRUE)$/.test(text)) return true;
  if (/^(false|False|FALSE)$/.test(text)) return false;
  if (/^[-+]?[0-9]+$/.test(text)) return parseInt(text, 10);
  if (/^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$/.test(text)) return parseFloat(text);
  return text;
}

// ── flow collections: [a, b] and {k: v} ──────────────────────────────────
function parseFlow(text, source, n) {
  let pos = 0;
  const ws = () => { while (text[pos] === ' ') pos++; };
  const node = () => {
    ws();
    const c = text[pos];
    if (c === '[') return seq();
    if (c === '{') return map();
    if (c === '"' || c === "'") { const q = readQuoted(text, pos, source, n); pos = q.end; return q.value; }
    const start = pos;
    while (pos < text.length && !',]}'.includes(text[pos])) {
      if (text[pos] === ':' && (text[pos + 1] === ' ' || ',]}'.includes(text[pos + 1] ?? ',')))
        fail(source, n, 'a `key: value` pair inside a flow sequence — use a `{...}` mapping');
      pos++;
    }
    const plain = text.slice(start, pos).trim();
    if (!plain) fail(source, n, 'an empty entry in a flow collection');
    return plainScalar(plain, source, n);
  };
  const key = () => {
    ws();
    if (text[pos] === '"' || text[pos] === "'") { const q = readQuoted(text, pos, source, n); pos = q.end; return q.value; }
    const start = pos;
    while (pos < text.length && text[pos] !== ':' && !',]}'.includes(text[pos])) pos++;
    const k = text.slice(start, pos).trim();
    if (!k) fail(source, n, 'an empty key in a flow mapping');
    return k;
  };
  const seq = () => {
    pos++;
    const out = [];
    for (;;) {
      ws();
      if (text[pos] === ']') { pos++; return out; }
      out.push(node());
      ws();
      if (text[pos] === ',') { pos++; continue; }
      if (text[pos] === ']') { pos++; return out; }
      fail(source, n, `expected "," or "]" in a flow sequence, found "${text[pos] ?? 'end of line'}"`);
    }
  };
  const map = () => {
    pos++;
    const out = {};
    for (;;) {
      ws();
      if (text[pos] === '}') { pos++; return out; }
      const k = key();
      ws();
      if (text[pos] !== ':') fail(source, n, `flow mapping key "${k}" has no ":" value`);
      pos++;
      setKey(out, k, node(), source, n);
      ws();
      if (text[pos] === ',') { pos++; continue; }
      if (text[pos] === '}') { pos++; return out; }
      fail(source, n, `expected "," or "}" in a flow mapping, found "${text[pos] ?? 'end of line'}"`);
    }
  };
  const value = node();
  ws();
  if (pos < text.length) fail(source, n, `unexpected "${text.slice(pos)}" after a flow collection`);
  return value;
}

function setKey(obj, k, v, source, n) {
  if (k === '<<') fail(source, n, 'the `<<` merge key is not part of the dialect');
  if (Object.prototype.hasOwnProperty.call(obj, k)) fail(source, n, `duplicated mapping key "${k}"`);
  obj[k] = v;
}

// A one-line value: a flow collection, a quoted scalar, or a plain scalar.
function inlineValue(text, source, n) {
  if (text[0] === '[' || text[0] === '{') return parseFlow(text, source, n);
  if (text[0] === '"' || text[0] === "'") {
    const q = readQuoted(text, 0, source, n);
    if (q.end !== text.length) fail(source, n, `unexpected "${text.slice(q.end)}" after a quoted scalar`);
    return q.value;
  }
  if (/:( |$)/.test(text)) fail(source, n, `"${text}" holds ": " — quote the scalar, or nest the mapping on its own lines`);
  return plainScalar(text, source, n);
}

// Splits `key: rest` / `key:`; returns null when the text is not a mapping entry.
function splitEntry(text, source, n) {
  if (text[0] === '"' || text[0] === "'") {
    const q = readQuoted(text, 0, source, n);
    if (text[q.end] !== ':' || !(text[q.end + 1] === undefined || text[q.end + 1] === ' ')) return null;
    return { key: q.value, rest: text.slice(q.end + 1).trim() };
  }
  if (text[0] === '[' || text[0] === '{') return null;
  const m = text.match(/^([^:]*?):( |$)/);
  if (!m) return null;
  if (text[0] === '?') fail(source, n, 'complex (`?`) keys are not part of the dialect');
  return { key: m[1].trim(), rest: text.slice(m[0].length).trim() };
}

const isItem = body => body === '-' || body.startsWith('- ');

// ── block collections ─────────────────────────────────────────────────────
function parseBlock(st) {
  const line = st.lines[st.i];
  if (isItem(line.body)) return parseSeq(st, line.indent);
  if (!splitEntry(line.body, st.source, line.n)) {
    st.i++;
    const v = inlineValue(line.body, st.source, line.n);
    noDeeper(st, line.indent, line.n);
    return v;
  }
  return parseMap(st, line.indent);
}

// After a one-line value nothing may be nested under it: that would be a
// multi-line plain scalar or a mis-indented key, and both are refused.
function noDeeper(st, indent, n) {
  const next = st.lines[st.i];
  if (next && next.indent > indent)
    fail(st.source, next.n, `unexpected indentation under the value on line ${n} (multi-line plain scalars are not part of the dialect)`);
}

// The value of `key:` or `-` with nothing after it: the nested block, a
// sequence at the key's own indent, or null.
function nestedValue(st, indent, allowSameIndentSeq) {
  const next = st.lines[st.i];
  if (next && next.indent > indent) return parseBlock(st);
  if (next && allowSameIndentSeq && next.indent === indent && isItem(next.body)) return parseSeq(st, indent);
  return null;
}

function parseSeq(st, indent) {
  const out = [];
  while (st.i < st.lines.length) {
    const line = st.lines[st.i];
    if (line.indent < indent) break;
    if (line.indent > indent) fail(st.source, line.n, 'unexpected indentation inside a sequence');
    if (!isItem(line.body)) break;
    const rest = line.body.slice(1).trim();
    if (!rest) { st.i++; out.push(nestedValue(st, indent, false)); continue; }
    const offset = line.body.length - line.body.slice(1).trimStart().length;
    if (isItem(rest) || splitEntry(rest, st.source, line.n)) {
      st.lines[st.i] = { n: line.n, indent: indent + offset, body: rest };
      out.push(parseBlock(st));
      continue;
    }
    st.i++;
    out.push(inlineValue(rest, st.source, line.n));
    noDeeper(st, indent, line.n);
  }
  return out;
}

function parseMap(st, indent) {
  const out = {};
  while (st.i < st.lines.length) {
    const line = st.lines[st.i];
    if (line.indent < indent) break;
    if (line.indent > indent) fail(st.source, line.n, 'unexpected indentation inside a mapping');
    if (isItem(line.body)) fail(st.source, line.n, 'a sequence item where a mapping key was expected');
    const entry = splitEntry(line.body, st.source, line.n);
    if (!entry) fail(st.source, line.n, `"${line.body}" is not a \`key: value\` entry`);
    st.i++;
    if (entry.rest) {
      setKey(out, entry.key, inlineValue(entry.rest, st.source, line.n), st.source, line.n);
      noDeeper(st, indent, line.n);
    } else {
      setKey(out, entry.key, nestedValue(st, indent, true), st.source, line.n);
    }
  }
  return out;
}

// Returns the document's value (null for an empty document); throws YamlError.
function parse(text, source = '<yaml>') {
  const st = { lines: logicalLines(String(text), source), i: 0, source };
  if (!st.lines.length) return null;
  if (st.lines[0].indent !== 0) fail(source, st.lines[0].n, 'the document must start at column 0');
  const value = parseBlock(st);
  if (st.i < st.lines.length) fail(source, st.lines[st.i].n, 'unexpected content after the document');
  return value;
}

module.exports = { parse, YamlError };
