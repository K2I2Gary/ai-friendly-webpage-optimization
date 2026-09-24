---
id: html.lang-attribute
name: Root <html> has lang attribute
group: HTML metadata
---

## What it checks
The root `<html>` tag declares a `lang` attribute.

## How to fix
Add a `lang` attribute to the root `<html>` tag.

```html
<html lang="en">
```

## Gotchas
- Use the page's actual language (e.g. `zh-CN` for Chinese, `en` for English).

## Acceptance
- `html.lang-attribute` flips fail → pass.
