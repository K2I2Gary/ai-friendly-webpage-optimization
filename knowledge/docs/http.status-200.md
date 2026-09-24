# Page returns HTTP 200

> A page that doesn't return 200 isn't a page, it's a 404, a 5xx, or an unintended redirect target. Agents that follow links to non-200 pages waste context on error bodies they can't ingest.

`page` | `HTTP` | `impl 1.0.0` | `http.status-200`

## How the check decides
After following any redirects, the check inspects the final response status. Passes if it is exactly 200. Fails with the actual status otherwise.

## How to implement it
Make sure every URL listed in your sitemap or linked from your pages resolves to a 200. Audit your site for broken internal links periodically, a14y check --mode site will surface every non-200 page in one pass.

### Pass

```
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
```

### Fail

```
HTTP/1.1 404 Not Found
```
