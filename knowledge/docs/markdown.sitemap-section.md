# Markdown mirror includes a Sitemap section

> Agents reading a markdown mirror benefit from a "where am I in this site" pointer embedded right in the document. A `## Sitemap` section linking to /sitemap.md gives them one click to the full hierarchy without leaving the markdown world.

`page` | `Markdown mirror` | `impl 1.0.0` | `markdown.sitemap-section`

## How the check decides
The check loads the markdown mirror’s body and asserts it contains a ## Sitemap heading (matched line-by-line, case-sensitive). Fails if the heading is absent. Returns N/A if no mirror exists at all.

## How to implement it
When generating the markdown mirror, append a ## Sitemap section near the bottom (or in a footer template) with a link to the site-wide sitemap.md.

### Pass

```
# Install a14y

...content...

## Sitemap
[Full site index](/sitemap.md)
```

### Fail

```
# Install a14y

...content...
```
