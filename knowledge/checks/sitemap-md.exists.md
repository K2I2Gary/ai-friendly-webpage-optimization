---
id: sitemap-md.exists
name: sitemap.md is published
group: Discoverability
---

## What it checks
`/sitemap.md` is published at the site root, listing key page links.

## How to fix
Publish `/sitemap.md` with headings + links.

```markdown
# Sitemap

- [Home](https://<your-domain>/)
- [About](https://<your-domain>/about)
```

## Gotchas
- Use markdown headings + a link list to also satisfy `sitemap-md.has-structure`.

## Acceptance
- `sitemap-md.exists` flips fail → pass (and unblocks `sitemap-md.has-structure`).
