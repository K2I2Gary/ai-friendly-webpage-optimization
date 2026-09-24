# JSON-LD declares a BreadcrumbList

> BreadcrumbList tells agents where the page sits in the site hierarchy without making them parse navigation menus. It's how agents build a tree of your site for context-aware Q&A ("where is this in the docs?") and link generation.

`page` | `Structured data` | `impl 1.0.0` | `html.json-ld.breadcrumb`

## How the check decides
The check parses every JSON-LD block on the page, walks every node (arrays and @graph included), and asserts at least one has @type of BreadcrumbList . Returns N/A if no JSON-LD exists. Fails if JSON-LD exists but no node is a BreadcrumbList.

## How to implement it
Add a BreadcrumbList object to your JSON-LD with one ListItem per level of the hierarchy.

### Pass

```
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {"@type":"ListItem","position":1,"name":"Docs","item":"https://example.com/docs/"},
    {"@type":"ListItem","position":2,"name":"Install","item":"https://example.com/docs/install"}
  ]
}
</script>
```

### Fail

```
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "TechArticle"
}
</script>
```
