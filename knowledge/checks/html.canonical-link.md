---
id: html.canonical-link
name: Has <link rel="canonical">
group: HTML metadata
---

## What it checks
The page `<head>` has a `<link rel="canonical">` pointing to the canonical URL.

## How to fix
Add a canonical link in `<head>`.

```html
<link rel="canonical" href="https://<your-domain>/">
```

## Gotchas
- Use the absolute URL to avoid duplicate-content dilution across paths.

## Acceptance
- `html.canonical-link` flips fail → pass.
