---
id: html.og-title
name: Has og:title
group: HTML metadata
---

## What it checks
The page `<head>` declares an Open Graph `og:title`.

## How to fix
Add `og:title` in `<head>`.

```html
<meta property="og:title" content="<page title>">
```

## Gotchas
- Keep it identical to (or a short version of) the `<title>`.

## Acceptance
- `html.og-title` flips fail → pass.
