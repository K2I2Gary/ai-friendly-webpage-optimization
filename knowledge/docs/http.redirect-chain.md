# Redirect chain is at most 1 hop

> Each redirect hop is a round trip an agent has to make before it can read content. Long redirect chains add latency, increase the chance of bot blockers tripping, and occasionally lose request headers between hops.

`page` | `HTTP` | `impl 1.0.0` | `http.redirect-chain`

## How the check decides
The HTTP client follows redirects manually and records the chain of intermediate URLs. Passes if the chain length is 0 or 1. Fails (with the hop count) otherwise.

## How to implement it
Audit your redirect rules and collapse chains. The most common offender is “old URL → canonical URL → URL with trailing slash”, collapse the middle hop so the original URL goes straight to its final destination.

### Pass

```
GET /old → 301 /new
GET /new → 200 OK
```
(1 hop.)

### Fail

```
GET /old → 301 /redirect
GET /redirect → 301 /new
GET /new → 301 /new/
GET /new/ → 200 OK
```
(3 hops.)
