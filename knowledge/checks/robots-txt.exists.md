---
id: robots-txt.exists
name: robots.txt is published
group: Discoverability
---

## What it checks
`/robots.txt` is reachable at the site root.

## How to fix
Publish `/robots.txt` (even an empty allow-all is auditable and better than a 404).

```
User-agent: *
Allow: /

Sitemap: https://<your-domain>/sitemap.xml
```

## Gotchas
- Absence defaults to "allow all" for `robots-txt.allows-ai-bots`, but the `exists` check still fails.
- Adding a `Sitemap:` line also helps discoverability.

## Acceptance
- `robots-txt.exists` flips fail → pass.
