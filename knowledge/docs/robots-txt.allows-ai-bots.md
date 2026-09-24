# robots.txt allows AI bots

> GPTBot, ClaudeBot, CCBot, and Google-Extended are the named user-agents that today's largest AI ingesters use. Disallowing them in robots.txt is the explicit "do not include this site in any LLM" signal, and it's almost always set inadvertently when authors copy-paste a generic robots.txt template.

`site` | `Discoverability` | `impl 1.0.0` | `robots-txt.allows-ai-bots`

## How the check decides
The check parses your robots.txt with robots-parser and asks each of GPTBot , ClaudeBot , CCBot , and Google-Extended whether the site root ( / ) is allowed. Passes if all four are allowed. Fails (with a list of blocked bots) if any are disallowed. If no robots.txt exists at all, the check passes, no robots.txt implies allow-all.

## How to implement it
Either omit named AI bot user-agents entirely (the global User-agent: * rule applies) or add explicit allow rules for them. Don’t add User-agent: GPTBot\nDisallow: / unless you’ve decided you actively don’t want to be in a corpus.

### Pass

```
User-agent: *
Allow: /

Sitemap: https://example.com/sitemap.xml
```

### Fail

```
User-agent: *
Allow: /

User-agent: GPTBot
Disallow: /

User-agent: ClaudeBot
Disallow: /
```
