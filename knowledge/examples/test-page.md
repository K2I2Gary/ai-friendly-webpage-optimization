---
check_ids: ["agents-md.exists", "html.canonical-link", "html.glossary-link", "html.json-ld", "html.meta-description", "html.og-description", "html.og-title", "html.text-ratio", "http.content-type-html", "llms-txt.exists", "markdown.alternate-link", "markdown.content-negotiation", "markdown.mirror-suffix", "robots-txt.exists", "sitemap-md.exists", "sitemap-xml.exists"]
score_before: 30
score_after: 67
verdict: PASS
final_score: 76.57
---

1. Objective
   - Target URL: http://127.0.0.1:8765/test.html
   - Current score: 30 / 100 (Grade F; pass=7, fail=16, na=15)
   - Target score: ≥ 65 (Grade C+/B-) by flipping all P0 + P1 checks below.

2. Optimization checklist

P0 — Do first (unblocks multiple checks)

- [Discoverability] Publish `/llms.txt` at site root, served as `text/plain; charset=utf-8`, non-empty, links ending in `.md`:
  ```
  # Test Site
  > Entry index provided by this site for AI agents.

  - [Test page](http://127.0.0.1:8765/test.md): overview and content of the test page
  ```
  Flips: `llms-txt.exists`, `llms-txt.content-type`, `llms-txt.non-empty`, `llms-txt.md-extensions`, `discovery.indexed`.

- [Discoverability] Publish `/robots.txt`:
  ```
  User-agent: *
  Allow: /

  Sitemap: http://127.0.0.1:8765/sitemap.xml
  ```
  Flips: `robots-txt.exists`.

- [Discoverability] Publish `/sitemap.xml`:
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>http://127.0.0.1:8765/test.html</loc><lastmod>2026-04-06</lastmod></url>
    <url><loc>http://127.0.0.1:8765/test.md</loc><lastmod>2026-04-06</lastmod></url>
  </urlset>
  ```
  Flips: `sitemap-xml.exists`, `sitemap-xml.valid`, `sitemap-xml.has-lastmod`.

- [Discoverability] Publish `/sitemap.md`:
  ```markdown
  # Sitemap

  - [Test page](http://127.0.0.1:8765/test.html)
  - [Test page (Markdown)](http://127.0.0.1:8765/test.md)
  ```
  Flips: `sitemap-md.exists`, `sitemap-md.has-structure`.

- [Structured data] Inject JSON-LD into `<head>` of `test.html`:
  ```html
  <script type="application/ld+json">
  {"@context":"https://schema.org","@type":"WebPage",
   "name":"Test Page","description":"Test page served by the local a14y fixture server for AI-readability checks.",
   "url":"http://127.0.0.1:8765/test.html",
   "dateModified":"2026-04-06",
   "breadcrumb":{"@type":"BreadcrumbList","itemListElement":[
     {"@type":"ListItem","position":1,"name":"Home","item":"http://127.0.0.1:8765/"},
     {"@type":"ListItem","position":2,"name":"Test Page","item":"http://127.0.0.1:8765/test.html"}]}}
  </script>
  ```
  Flips: `html.json-ld`, `html.json-ld.date-modified`, `html.json-ld.breadcrumb`.

P1 — High ROI, low effort

- [HTML metadata] Add to `<head>` of `test.html`:
  ```html
  <link rel="canonical" href="http://127.0.0.1:8765/test.html">
  <meta name="description" content="Test page served by the local a14y fixture server, used to validate AI-readability checks and metadata.">
  <meta property="og:title" content="Test Page">
  <meta property="og:description" content="Test page served by the local a14y fixture server, used to validate AI-readability checks and metadata.">
  <link rel="alternate" type="text/markdown" href="/test.md">
  ```
  Flips: `html.canonical-link`, `html.meta-description`, `html.og-title`, `html.og-description`, `markdown.alternate-link`.

- [Markdown mirror] Publish `/test.md`:
  ```markdown
  ---
  title: Test Page
  description: Test page served by the local a14y fixture server, used to validate AI-readability checks and metadata.
  dateModified: 2026-04-06
  canonical: http://127.0.0.1:8765/test.html
  ---

  # Test Page

  Markdown mirror of the test page content.

  ## Sitemap

  - [Test page](http://127.0.0.1:8765/test.html)
  ```
  Flips: `markdown.mirror-suffix`, `markdown.frontmatter`, `markdown.canonical-header`, `markdown.sitemap-section`.

- [HTTP] Set server to return `Content-Type: text/html; charset=utf-8` for `.html` responses (server config, not HTML edit).

- [Markdown mirror] Enable content negotiation: when request sends `Accept: text/markdown`, return `/test.md` body with:
  ```
  Content-Type: text/markdown; charset=utf-8
  Link: <http://127.0.0.1:8765/test.html>; rel="canonical"
  ```
  Flips: `markdown.content-negotiation`, `http.content-type-html`.

- [Discoverability] Publish `/AGENTS.md` covering ≥ 2 of install/config/usage:
  ```markdown
  # AGENTS.md

  ## Install
  Serve this directory with any static HTTP server on port 8765.

  ## Usage
  Fetch `/test.html` for the HTML page, `/test.md` for the Markdown mirror, `/llms.txt` for the entry index.
  ```
  Flips: `agents-md.exists`, `agents-md.has-min-sections`.

P2 — Content quality (only if time permits)

- [Content structure] Raise text-to-HTML ratio above 15%: move inline `<style>`/`<script>` in `test.html` to external `test.css` / `test.js`, and add ≥ 300 words of real prose to the body. Flips: `html.text-ratio`.

- [Content structure] Add a glossary link in the body and create `/glossary.html` with a `<dl>` of terms:
  ```html
  <a href="/glossary.html">Glossary</a>
  ```
  Flips: `html.glossary-link`.

3. Acceptance criteria
   - `llms-txt.exists`, `llms-txt.content-type`, `llms-txt.non-empty`, `llms-txt.md-extensions`, `discovery.indexed` → pass
   - `robots-txt.exists` → pass
   - `sitemap-xml.exists`, `sitemap-xml.valid`, `sitemap-xml.has-lastmod` → pass
   - `sitemap-md.exists`, `sitemap-md.has-structure` → pass
   - `agents-md.exists`, `agents-md.has-min-sections` → pass
   - `http.content-type-html` → pass
   - `html.canonical-link`, `html.meta-description`, `html.og-title`, `html.og-description` → pass
   - `html.json-ld`, `html.json-ld.date-modified`, `html.json-ld.breadcrumb` → pass
   - `markdown.mirror-suffix`, `markdown.alternate-link`, `markdown.content-negotiation`, `markdown.frontmatter`, `markdown.canonical-header`, `markdown.sitemap-section` → pass
   - `html.text-ratio`, `html.glossary-link` → pass (P2)
   - Target total score: ≥ 65 / 100 (P0+P1 complete); ≥ 80 / 100 if P2 also completed.
