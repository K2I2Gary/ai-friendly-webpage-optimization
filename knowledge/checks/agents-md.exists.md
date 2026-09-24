---
id: agents-md.exists
name: AGENTS.md (or equivalent) is published
group: Discoverability
---

## What it checks
An agent skill file (`/AGENTS.md`) is published, describing the site's purpose and entry points.

## How to fix
Publish `/AGENTS.md` covering at least 2 of install / config / usage.

```markdown
# AGENTS.md

## Install
(how to deploy / install)

## Usage
(how to use this site)
```

## Gotchas
- Cover ≥2 of install/config/usage to also satisfy `agents-md.has-min-sections`.

## Acceptance
- `agents-md.exists` flips fail → pass (and unblocks `agents-md.has-min-sections`).
