---
id: markdown.mirror-suffix
name: Has .md or .mdx mirror
group: Markdown mirror
---

## What it checks
A `.md`/`.mdx` mirror of the page exists (e.g. `/index.md`).

## How to fix
Publish a Markdown mirror of the page (e.g. `/index.md`).

```markdown
# Page title

Markdown version of the page content.
```

## Gotchas
- Add frontmatter (`title`/`description`/`dateModified`/`canonical`) to unlock `markdown.frontmatter`.
- Add a `## Sitemap` section to unlock `markdown.sitemap-section`.

## Acceptance
- `markdown.mirror-suffix` flips fail → pass (and unblocks `markdown.frontmatter`, `markdown.canonical-header`, `markdown.sitemap-section`).
