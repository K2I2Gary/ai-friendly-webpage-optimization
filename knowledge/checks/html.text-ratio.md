---
id: html.text-ratio
name: Text-to-HTML ratio is above 15%
group: Content structure
---

## What it checks
The visible-text-to-HTML-markup ratio exceeds 15%.

## How to fix
Increase real prose and/or move inline `<style>`/`<script>` to external files.

- Move inline `<style>` and `<script>` blocks to external `.css` / `.js` files.
- Add ≥ 300 words of real prose to the body.

## Gotchas
- Heavy inline markup (styles/scripts) inflates the denominator and drags the ratio down.
- This is often the last item to flip and may be intentionally skipped.

## Acceptance
- `html.text-ratio` flips fail → pass (ratio > 15%).
