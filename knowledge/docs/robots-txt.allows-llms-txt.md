# robots.txt does not disallow llms.txt

> Publishing llms.txt and then disallowing it in robots.txt is a contradiction that confuses every well-behaved agent. This check catches the rare but visible case where the two files are out of sync.

`site` | `Discoverability` | `impl 1.0.0` | `robots-txt.allows-llms-txt`

## How the check decides
The check parses robots.txt and asks the universal user-agent * whether /llms.txt and /.well-known/llms.txt are allowed. Passes if both are reachable. Fails with the offending paths if either is disallowed. If no robots.txt exists the check passes (allow-all by default).

## How to implement it
If you’ve added a global Disallow: / for any user-agent, add an explicit Allow: /llms.txt line above it for the same user-agent. Or just don’t disallow llms.txt in the first place.

### Pass

```
User-agent: *
Allow: /
```

### Fail

```
User-agent: *
Disallow: /llms.txt
```
