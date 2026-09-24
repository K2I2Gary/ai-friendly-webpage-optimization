# Content-Type is text/html; charset=utf-8

> Agents sniff content type from the header before deciding how to parse a response. A page served with text/plain or no charset can be misinterpreted, especially when the body contains non-ASCII characters.

`page` | `HTTP` | `impl 1.0.0` | `http.content-type-html`

## How the check decides
The check reads the response Content-Type header (lowercased) and asserts it contains both text/html and utf-8 . Fails with the actual value (or (missing) ) otherwise.

## How to implement it
Configure your web server or CDN to serve .html files with Content-Type: text/html; charset=utf-8 . Most platforms do this by default; explicit declarations matter when you’ve put the page behind a custom rewrite or worker.

### Pass

```
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
```

### Fail

```
HTTP/1.1 200 OK
Content-Type: text/html
```
