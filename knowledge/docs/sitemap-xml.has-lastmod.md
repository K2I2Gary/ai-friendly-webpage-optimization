# sitemap entries include <lastmod>

> <lastmod> lets agents do incremental re-ingestion, they can skip pages whose lastmod hasn't changed since their last crawl. Without it, agents either re-fetch everything every time (wasteful) or never update their snapshot (stale).

`site` | `Discoverability` | `impl 1.0.0` | `sitemap-xml.has-lastmod`

## How the check decides
After parsing the sitemap, the check inspects every <url> entry, counts how many are missing a <lastmod> child, and counts how many have a <lastmod> whose value does not parse as a W3C Datetime (the format sitemaps.org requires). Passes only if every entry has a <lastmod> and every value is a valid date. Fails with a breakdown of missing vs. invalid counts otherwise. Warns if the sitemap has zero entries. Returns N/A if the sitemap couldn’t be parsed at all. Calendar-impossible values like 2026-02-30 are rejected. The W3C Datetime profile permits coarser granularities like 2026 or 2026-04 , but this check requires at least a calendar day — coarser values aren’t actionable for incremental re-ingestion. Surrounding whitespace from pretty-printed sitemaps is tolerated.

## How to implement it
Most sitemap generators emit <lastmod> automatically from the source file mtime or the content’s last_updated frontmatter. If yours doesn’t, populate it from the most recent commit touching each page.

### Pass

```
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/docs/install</loc>
    <lastmod>2026-04-01</lastmod>
  </url>
</urlset>
```

### Fail

```
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/docs/install</loc>
  </url>
</urlset>
```

```
<!-- lastmod present but not a valid W3C Datetime -->
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/docs/install</loc>
    <lastmod>recently</lastmod>
  </url>
</urlset>
```
