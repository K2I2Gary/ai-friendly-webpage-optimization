---
id: robots-txt.allows-llms-txt
name: robots.txt does not disallow llms.txt
group: Discoverability
---

## What it checks
`/robots.txt` must not block `/llms.txt` or `/.well-known/llms.txt`.

## How to fix
Remove any `Disallow` rules that mention `/llms.txt` or `/.well-known/llms.txt`.

```
# Remove lines like these two:
# Disallow: /llms.txt
# Disallow: /.well-known/llms.txt
```

## Gotchas
- A `Disallow: /` catches `/llms.txt` too — scope the rule or remove it.
- If you block `/.well-known/`, remember `/llms.txt` may be served from there.

## Acceptance
- `robots-txt.allows-llms-txt` flips fail → pass.
