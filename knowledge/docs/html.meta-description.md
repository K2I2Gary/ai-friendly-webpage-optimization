# Has meta description (>= 50 chars)

> The meta description is what agents use as the page summary when they need a quick representation that's smaller than the full body. A short or missing description means agents either fall back to scraping the body or skip the page entirely in summary lists.

`page` | `HTML metadata` | `impl 1.0.0` | `html.meta-description`

## How the check decides
The check reads the content attribute of <meta name="description"> , trims whitespace, and asserts the length is at least 50 characters. Fails if the tag is missing or its trimmed content is shorter.

## How to implement it
Author a one- or two-sentence description for every page. 50 characters is a low floor; 120–160 is closer to the real sweet spot for both agents and search engines.

### Pass

```
<meta name="description"
      content="Install a14y in under a minute and audit your first documentation site against scorecard v0.2.0.">
```

### Fail

```
<meta name="description" content="Install page">
```
