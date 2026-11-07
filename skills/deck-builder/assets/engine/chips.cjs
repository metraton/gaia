// Core chips: the deck-level `filters:` of document.yaml, inherited by every
// page. The build writes each page's resolved list into the bundle and the
// static census resolves the authored deck the same way, so both read ONE
// function and the static gate describes the deck exactly as it was built.
//
// CommonJS on purpose: the ESM build imports it as a default export and the CJS
// census requires it, with no require(esm) dependency on the Node version.
'use strict';

const sameSteps = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/**
 * Resolve a page's chips: the core chips first, in their declared order, minus
 * the ones the page's manifest entry omits, then the page's own chips.
 * Throws on a core key redeclared with another label or steps, on an omitted
 * key that is not a core key, and on an omitted key the page declares again.
 */
function resolvePageFilters(coreFilters, omitFilters, pageFilters, pageId) {
  const core = coreFilters || [];
  const omit = omitFilters || [];
  const own = pageFilters || [];
  const coreByKey = new Map(core.map(f => [f.key, f]));
  const where = `[strict-schema] page "${pageId}"`;
  if (!Array.isArray(omit) || omit.some(k => typeof k !== 'string'))
    throw new Error(`${where}: \`omit_filters\` must be a LIST of core chip keys`);
  for (const k of omit) {
    if (!coreByKey.has(k))
      throw new Error(`${where}: omit_filters names "${k}", which is not a core chip ` +
        `(document.yaml filters: ${[...coreByKey.keys()].join(', ') || 'none'})`);
    if (omit.indexOf(k) !== omit.lastIndexOf(k))
      throw new Error(`${where}: omit_filters names "${k}" twice`);
  }
  const local = [];
  for (const f of own) {
    const c = coreByKey.get(f && f.key);
    if (!c) { local.push(f); continue; }
    if (omit.includes(f.key))
      throw new Error(`${where}: chip "${f.key}" is a core chip this page omits, and the page declares it ` +
        `again. A core key means one thing on every page: pick another key for the local chip.`);
    if (f.label !== c.label || !sameSteps(f.steps, c.steps))
      throw new Error(`${where}: chip "${f.key}" redeclares the core chip with a different ` +
        `${f.label !== c.label ? `label ("${f.label}" vs "${c.label}")` : 'steps'}. ` +
        `A core key means one thing on every page: drop the page's copy, or pick another key.`);
  }
  return [...core.filter(f => !omit.includes(f.key)), ...local];
}

module.exports = { resolvePageFilters };
