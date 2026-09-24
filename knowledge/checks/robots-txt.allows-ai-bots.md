---
id: robots-txt.allows-ai-bots
name: robots.txt allows AI bots
group: Discoverability
---

## What it checks
The site's `/robots.txt` must not `Disallow` AI crawlers such as GPTBot, ClaudeBot, CCBot, or Google-Extended.

## How to fix
Publish a `/robots.txt` at the site root that explicitly `Allow`s the AI bots.

```
User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: CCBot
Allow: /

User-agent: Google-Extended
Allow: /

Sitemap: https://<your-domain>/sitemap.xml
```

## Gotchas
- A blanket `User-agent: *` + `Disallow: /` blocks every bot and fails this check.
- `Allow: /` after each `User-agent` line is the safe, explicit form.

## Acceptance
- `robots-txt.allows-ai-bots` flips fail → pass.
