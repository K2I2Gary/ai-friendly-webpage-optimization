# robots.txt is published

> robots.txt is the first file most crawlers (agent or otherwise) request. Even if its rules are permissive, having it tells well-behaved bots they're at the right host and lets you point them at sitemaps.

`site` | `Discoverability` | `impl 1.0.0` | `robots-txt.exists`

## How the check decides
The check sends GET /robots.txt . It passes if the response status is 2xx. It fails if the file is missing.

## How to implement it
Serve a robots.txt at the root of your domain. At minimum it should declare User-agent: * and either explicitly Allow: / or list the parts of your site you don’t want indexed.

### Pass

```
User-agent: *
Allow: /

Sitemap: https://example.com/sitemap.xml
```

### Fail

```
HTTP/1.1 404 Not Found
```
