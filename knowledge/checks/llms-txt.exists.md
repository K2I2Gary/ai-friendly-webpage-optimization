---
id: llms-txt.exists
name: llms.txt is published
group: Discoverability
---

## What it checks
A non-empty `/llms.txt` (or `/llms-full.txt`) is served at the site root, `/.well-known/`, or `/docs/`.

## How to fix
Publish `/llms.txt` at the site root, served as `text/plain; charset=utf-8`.

```
# llms.txt
> Entry index provided by this site for AI agents.

- [Home](https://<your-domain>/): site homepage
- [About](https://<your-domain>/about.md): about us
```

## Gotchas
- Links inside `llms.txt` should end in `.md` / `.mdx` to satisfy `llms-txt.md-extensions`.
- A non-empty file also flips `llms-txt.content-type` (if served as text/plain) and `llms-txt.non-empty`.
- This check is the single biggest discoverability unlock.

## Acceptance
- `llms-txt.exists` flips fail → pass (and unblocks `llms-txt.content-type`, `llms-txt.non-empty`, `llms-txt.md-extensions`, `discovery.indexed`).
