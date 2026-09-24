# Has .md or .mdx mirror

> A markdown mirror at <page>.md is the cleanest format an agent can ingest, no HTML, no scripts, no styling, just the content. Mirrors are a small amount of work to generate but dramatically reduce the cost of ingesting a doc site.

`page` | `Markdown mirror` | `impl 1.0.0` | `markdown.mirror-suffix`

## How the check decides
The check derives a markdown URL by stripping any trailing slash and .html from the page path and appending .md , then .mdx . It sends a HEAD request and asserts a 2xx response. Fails if neither variant returns 2xx.

## How to implement it
Add a build step that emits a markdown version of every page alongside the HTML. Most modern SSGs already have an “export markdown” plugin or expose the source markdown directly via a route. The mirror should match the HTML page’s URL with a .md extension.

### Pass

```
GET /docs/install.md → 200 OK
```

### Fail

```
GET /docs/install.md → 404 Not Found
GET /docs/install.mdx → 404 Not Found
```
