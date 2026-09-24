---
id: html.meta-description
name: Has meta description (>= 50 chars)
group: HTML metadata
---

## What it checks
The page `<head>` has a `<meta name="description">` at least 50 characters long.

## How to fix
Add a meta description (≥ 50 characters) in `<head>`.

```html
<meta name="description" content="A concise summary of this page, at least fifty characters long.">
```

## Gotchas
- The 50-character minimum is enforced; a short description still fails.
- Reuse it for `og:description`.

## Acceptance
- `html.meta-description` flips fail → pass.
