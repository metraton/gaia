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

## Existing Articles (for style reference)

| Date | Slug | Theme |
|------|------|-------|
| 2025-09-29 | context-design-agentic-deployment | Context engineering and agentic deployment on GCP |
| 2025-11-01 | beyond-delegation-agentic-systems | Moving past simple delegation in agentic workflows |
| 2026-04-02 | faster-development-orchestration | How orchestration improves faster development cycles |
| 2026-04-10 | writing-skills-that-actually-work | Skill design for LLM agents -- judgment over compliance |

## Local Preview

```bash
cd /home/jorge/ws/me/metraton.github.io
bundle exec jekyll serve
# Preview at http://localhost:4000
# Note: WSL2 may require port forwarding for browser access
```

## Publication

```bash
cd /home/jorge/ws/me/metraton.github.io
git add _posts/YYYY-MM-DD-slug.html
git commit -m "Add: article title"
git push origin main
# GitHub Pages deploys automatically
```

## Pending Article Ideas

- **"How to Build an Agent Identity"** -- about agent identities, what they contain, how to structure them, the relationship between identity and skills. More reflective than technical.
