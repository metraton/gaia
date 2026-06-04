# Blog Writing Reference

## Blog Repository

- **Repo path:** `/home/jorge/ws/me/metraton.github.io/`
- **Site:** https://metraton.github.io/
- **Engine:** Jekyll static site, GitHub Pages hosted
- **Posts directory:** `_posts/`
- **Draft workspace:** `/home/jorge/ws/me/<slug>.md`

## Front Matter Template

Every post requires this YAML front matter. All fields are mandatory for the bilingual layout to work correctly.

```yaml
---
layout: bilingual
title: "English Title Here"
title_en: "English Title Here"
title_es: "Spanish Title Here"
post_title_es: "Spanish Title Here"
date: YYYY-MM-DD
terminal_title: "jaguilar@github:~/posts$ cat slug-name"
terminal_title_es: "jaguilar@github:~/posts$ cat slug-name"
excerpt: "English excerpt -- one compelling sentence."
excerpt_es: "Spanish excerpt -- one compelling sentence."
---
```

**Notes:**
- `title` and `title_en` are always identical
- `title_es` and `post_title_es` are always identical
- `terminal_title` and `terminal_title_es` are usually the same (the cat command)
- `date` is the publication date in `YYYY-MM-DD` format
- Excerpts use HTML entities for special characters (e.g., `&eacute;` for accented characters in Spanish)
- Some older posts include `permalink:` -- newer posts rely on the filename for the URL

## HTML Structure

The file is named `YYYY-MM-DD-slug.html` and placed in `_posts/`.

```html
---
(front matter as above)
---

      <div class="bilingual-content" lang="en" data-lang-section="en">
        <!-- English content here -->
        <!-- Use proper HTML entities: &mdash; &rsquo; &ldquo; &rdquo; etc. -->

        <p class="closing-line"><em>Built with [theme]. Jorge Aguilar, 2025&ndash;2026.</em></p>
      </div>
      <div class="bilingual-content" lang="es" data-lang-section="es" hidden>
        <!-- Spanish content here -->
        <!-- Same structure as English, naturally rewritten -->

        <p class="closing-line"><em>Construido con [tema]. Jorge Aguilar, 2025&ndash;2026.</em></p>
      </div>
```

**HTML conventions observed in existing posts:**
- Content is indented with 8 spaces (two levels inside the layout)
- Use `&mdash;` for em dashes, `&rsquo;` for apostrophes, `&ldquo;`/`&rdquo;` for quotes
- Use `&eacute;`, `&aacute;`, `&iacute;`, `&oacute;`, `&uacute;`, `&ntilde;` for Spanish accented characters
- Section headings are `<h2>`, subsection headings are `<h3>`
- Horizontal rules (`<hr />`) separate major sections
- Blockquotes (`<blockquote>`) are used for key insights and external quotes
- Code inline uses `<code>`, code blocks use `<pre><code>`
- The Spanish section has `hidden` attribute (JavaScript toggles it)
- The closing line uses `class="closing-line"` with `<em>` wrapper

## Article Catalog

| Date | Slug | Status |
|------|------|--------|
| 2025-09-29 | context-design-agentic-deployment | PUBLISHED |
| 2025-11-01 | beyond-delegation-agentic-systems | PUBLISHED |
| 2026-04-02 | faster-development-orchestration | PUBLISHED |
| 2026-04-10 | writing-skills-that-actually-work | PUBLISHED |
| TBD | how-to-build-an-agent-identity | PENDING |

## Environment & Tooling

| Resource | Location |
|----------|----------|
| Blog repo | `/home/jorge/ws/me/metraton.github.io/` |
| Jekyll server | `cd /home/jorge/ws/me/metraton.github.io && bash jekyll-loop.sh` (port 4000) |
| Playwright | `~/.cache/ms-playwright/`, requires `NODE_PATH=/home/jorge/ws/aaxis/rnd/node_modules` |
| Git branch | `master` (not main) |
| GitHub Pages | `https://metraton.github.io/` |

Check if Jekyll is running: `ps aux | grep jekyll`

## Visual Components Catalog

All CSS lives inline in `_layouts/default.html` -- no external stylesheets.

| Component | CSS Class | When to Use |
|-----------|-----------|-------------|
| Before/After Grid | `.file-structure-demo` | Comparing old vs new approaches, code transformations |
| Card Grid | `.subagents-grid` / `.subagent-box` | Showing related concepts as cards (agents, types, categories) |
| Skills Grid | `.skills-grid` | 5-column card grid with optional `.lead-agent` header |
| Callout Box | `.callout` | Highlighted info, tips, key takeaways |
| Blockquote | `<blockquote>` | Editorial quotes, citations, reflective text |
| Chapter Layout | `.chapter` / `.chapter-layout` | Text + ASCII diagram side by side |
| Code Block | `<pre><code>` | Actual code, config files, terminal output |
| Table | `<table>` | Data comparison (note: limited CSS, consider cards instead) |

## Publication

```bash
cd /home/jorge/ws/me/metraton.github.io
git add _posts/YYYY-MM-DD-slug.html
git commit -m "Add: article title"
git push origin master
# GitHub Pages deploys automatically
```

## LinkedIn Post Template

```
[1-3 sentence hook connecting to the article's core insight]

https://metraton.github.io/<slug>/

P.D.: Lo escribí en formato bilingüe (ES/EN), para que lo leas de la forma que prefieras.
```

Tone: conversational but technical. No aggressive hashtags. The article preview image does the heavy lifting -- text just hooks.

