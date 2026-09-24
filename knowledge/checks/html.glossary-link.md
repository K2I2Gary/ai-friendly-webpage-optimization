---
id: html.glossary-link
name: Links to a glossary or terminology page
group: Content structure
---

## What it checks
The page body links to a glossary or terminology page.

## How to fix
Add a glossary link in the body.

```html
<a href="/glossary">Glossary</a>
```

## Gotchas
- Create `/glossary` (or `/glossary.html`) with a definition list so the link is not dead.

## Acceptance
- `html.glossary-link` flips fail → pass.
