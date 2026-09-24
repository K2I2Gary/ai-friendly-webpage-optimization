---
id: http.content-type-html
name: Content-Type is text/html; charset=utf-8
group: HTTP
---

## What it checks
The page response header is `Content-Type: text/html; charset=utf-8` (charset must be present).

## How to fix
Set the server to return `text/html; charset=utf-8` for `.html` responses.

```
Content-Type: text/html; charset=utf-8
```

## Gotchas
- This is server config, not an HTML edit. For a static server, add the header in the server config.
- The `charset=utf-8` suffix is what this check requires.

## Acceptance
- `http.content-type-html` flips fail → pass.
