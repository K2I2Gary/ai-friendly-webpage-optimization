---
id: markdown.content-negotiation
name: Server returns markdown for Accept: text/markdown
group: Markdown mirror
---

## What it checks
When the request sends `Accept: text/markdown`, the server returns the markdown body (`Content-Type: text/markdown`).

## How to fix
Support `Accept: text/markdown` and return `text/markdown` with a canonical `Link` header.

```
Link: <https://<your-domain>/>; rel="canonical"
```

## Gotchas
- This is a server-side rule; it cannot be fixed by editing the HTML alone.
- The canonical `Link` header also satisfies `markdown.canonical-header`.

## Acceptance
- `markdown.content-negotiation` flips fail → pass.
