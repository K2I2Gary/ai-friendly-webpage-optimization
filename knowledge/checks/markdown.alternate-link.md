---
id: markdown.alternate-link
name: HTML declares <link rel="alternate" type="text/markdown">
group: Markdown mirror
---

## What it checks
The HTML `<head>` declares a `<link rel="alternate" type="text/markdown">` pointing at the mirror.

## How to fix
Declare the Markdown mirror in `<head>`.

```html
<link rel="alternate" type="text/markdown" href="/index.md">
```

## Gotchas
- Point at the actual `.md` mirror path.

## Acceptance
- `markdown.alternate-link` flips fail → pass.
