/**
 * The CHANGELOG.md edit release:prepare makes for a target version.
 *
 * A stable X.Y.Z collects [Unreleased] and every X.Y.Z-<pre> section into one
 * dated section and leaves an empty [Unreleased] above it; a pre-release only
 * inserts its dated header, so [Unreleased] keeps accumulating for the stable.
 * Either way the first versioned header becomes the target version, which
 * bin/pre-publish-validate.js requires to equal package.json's.
 */

const SECTION_START = /^(?=##(?!#))/m;
const SUBSECTION_START = /^(?=### )/m;
const VERSION_HEADER = /^##\s*\[([^\]]+)\]/;
const PRERELEASE = /^\d+\.\d+\.\d+-/;
const LIST_ITEM = /^[-*+] /;

function sectionVersion(section) {
  return section.match(VERSION_HEADER)?.[1].trim() ?? null;
}

function isUnreleased(version) {
  return version?.toLowerCase() === 'unreleased';
}

function sectionBody(section) {
  const newline = section.indexOf('\n');
  return newline === -1 ? '' : section.slice(newline + 1);
}

/**
 * Concatenate section bodies so each `### ` subsection appears once, in order of
 * first appearance; a list continued from a later section stays one list.
 */
function mergeBodies(bodies) {
  const blocks = [];
  const subsections = new Map();
  for (const body of bodies) {
    for (const part of body.split(SUBSECTION_START)) {
      if (!part.startsWith('### ')) {
        if (part.trim()) blocks.push(part.trim());
        continue;
      }
      const newline = part.indexOf('\n');
      const heading = (newline === -1 ? part : part.slice(0, newline)).trim();
      const content = newline === -1 ? '' : part.slice(newline + 1).trim();
      const merged = subsections.get(heading);
      if (!merged) subsections.set(heading, content);
      else if (content) subsections.set(heading, merged + (LIST_ITEM.test(content) ? '\n' : '\n\n') + content);
    }
  }
  for (const [heading, content] of subsections) {
    blocks.push(content ? `${heading}\n\n${content}` : heading);
  }
  return blocks.map((block) => `${block}\n\n`).join('');
}

/**
 * Return the changelog text bumped to `version`, dated `today` (YYYY-MM-DD),
 * with a one-line summary; unchanged when the top versioned header is already
 * `version`.
 */
export function bumpChangelogText(text, version, today) {
  const sections = text.split(SECTION_START);
  const head = sections[0].startsWith('##') ? '' : sections.shift();

  const top = sections.map(sectionVersion).find((v) => v !== null && !isUnreleased(v));
  if (top === undefined) throw new Error('CHANGELOG has no versioned header to anchor the new entry');
  if (top === version) return { text, summary: `top header already [${version}] (no change)` };

  const header = `## [${version}] - ${today}\n\n`;
  if (PRERELEASE.test(version)) {
    const anchor = sections.findIndex((s) => sectionVersion(s) === top);
    sections.splice(anchor, 0, header);
    return { text: head + sections.join(''), summary: `inserted [${version}] above [${top}]` };
  }

  const isPrereleaseOfVersion = (s) => sectionVersion(s)?.startsWith(`${version}-`) ?? false;
  const folds = (s) => isUnreleased(sectionVersion(s)) || isPrereleaseOfVersion(s);
  const kept = sections.filter((s) => !folds(s));
  const anchor = kept.findIndex((s) => sectionVersion(s) !== null);
  kept.splice(anchor === -1 ? kept.length : anchor, 0,
    '## [Unreleased]\n\n', header + mergeBodies(sections.filter(folds).map(sectionBody)));
  return {
    text: head + kept.join(''),
    summary: `folded [Unreleased] and ${sections.filter(isPrereleaseOfVersion).length} ` +
      `pre-release section(s) into [${version}]`,
  };
}
