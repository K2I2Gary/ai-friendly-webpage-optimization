---
id: sitemap-xml.exists
name: sitemap.xml is published
group: Discoverability
---

## What it checks
`/sitemap.xml` is reachable at the site root.

## How to fix
Publish `/sitemap.xml` and declare it in `/robots.txt` with a `Sitemap:` line.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://<your-domain>/</loc><lastmod>2026-09-15</lastmod></url>
</urlset>
```

## Gotchas
- Include `<lastmod>` per `<url>` to also satisfy `sitemap-xml.has-lastmod`.
- Must parse as `<urlset>` (or `<sitemapindex>`) to satisfy `sitemap-xml.valid`.

## Acceptance
- `sitemap-xml.exists` flips fail → pass (and unblocks `sitemap-xml.valid`, `sitemap-xml.has-lastmod`).
