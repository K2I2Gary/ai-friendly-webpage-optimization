---
id: html.json-ld
name: Has parseable JSON-LD block
group: Structured data
---

## What it checks
At least one parseable `<script type="application/ld+json">` block in the page.

## How to fix
Inject a JSON-LD block into `<head>` (or `<body>`) declaring the page's `@type` and core fields.

```html
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "WebSite",
 "name": "<site name>", "url": "https://<your-domain>/"}
</script>
```

## Gotchas
- Must be valid JSON — escape quotes/backslashes; one broken block fails the whole check.
- Add `dateModified` to unlock `html.json-ld.date-modified`; add a `breadcrumb` (`BreadcrumbList`) to unlock `html.json-ld.breadcrumb`.

## Acceptance
- `html.json-ld` flips fail → pass (and unblocks `html.json-ld.date-modified`, `html.json-ld.breadcrumb`).
