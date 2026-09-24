---
id: html.og-description
name: Has og:description
group: HTML metadata
---

## What it checks
The page `<head>` declares an Open Graph `og:description`.

## How to fix
Add `og:description` in `<head>` (recommend ≥ 50 characters).

```html
<meta property="og:description" content="<page description>">
```

## Gotchas
- Match the meta description; keep it ≥ 50 chars for a useful preview.

## Acceptance
- `html.og-description` flips fail → pass.
