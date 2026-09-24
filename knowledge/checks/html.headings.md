---
id: html.headings
name: Has at least 3 section headings
group: Content structure
---

## What it checks
The page has at least 3 section headings (1 `<h1>` + at least 2 `<h2>`).

## How to fix
Add an `<h1>` title plus at least two `<h2>` sections.

```html
<h1>Main title</h1>
<h2>Section one</h2>
<h2>Section two</h2>
```

## Gotchas
- Exactly one `<h1>`; multiple `<h1>` is also flagged by some validators.

## Acceptance
- `html.headings` flips fail → pass.
