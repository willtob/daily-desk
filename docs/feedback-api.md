# Feedback API

The like/dislike contract, served by `esp-serve`. Written for the client
session that builds the macOS gesture — if something here is ambiguous it is a
bug in this file, not a judgement call for the client.

```bash
cd ~/dev/daily-desk && uv run esp-serve --port 8010
```

**Port 8010, not 8000** — Docker holds 8000 on this Mac. Base URL below is
written as `{base}`.

---

## The model in one paragraph

A verdict is `like` or `dislike`, attached to one article, identified by its
URL. There is at most one verdict per article at any time: sending a second one
replaces the first. Clearing removes it. The server resolves everything else
about the article — title, matched area, score, and the exact text that was
embedded when it was ranked — from its own records, so **the client never sends
anything but a URL and a verdict**, and never needs updating when the stored
record grows a field.

URLs are matched after canonicalization (the same normalization the deduper and
the seen store use), so `?utm_source=rss` and other tracking parameters do not
create a second verdict. Send the URL exactly as it appears in `digest.json`;
anything equivalent resolves to the same article.

---

## `POST {base}/feedback`

Record a verdict, change one, or clear one.

**Request**

```json
{ "url": "https://beteve.cat/cultura/...", "verdict": "like" }
```

| field | type | notes |
|---|---|---|
| `url` | string, required | as served in `digest.json` |
| `verdict` | string, required | `"like"`, `"dislike"` or `"clear"` |

**Response — 200**, for `like` and `dislike`:

```json
{
  "url": "https://beteve.cat/cultura/...",
  "verdict": "like",
  "record": {
    "verdict": "like",
    "url": "https://beteve.cat/cultura/...",
    "title": "Una setmana per a les festes de Gràcia",
    "text": "Una setmana per a les festes de Gràcia\n\nEl barri es prepara...",
    "matched_area": "barcelona_dates",
    "score": 0.6722,
    "text_source": "embedded",
    "recorded": "2026-08-09T21:14:07.201Z"
  }
}
```

**Response — 200**, for `clear`:

```json
{ "url": "https://…", "verdict": null, "cleared": true }
```

`cleared` is `false` when there was no verdict to remove. That is a success,
not an error: the end state is the one the caller asked for. A client that
fires undo twice must not see a failure.

**Errors**

| code | when | what the client should do |
|---|---|---|
| 404 | the server has no record of this URL | surface nothing; the digest is older than the server's 45-day index and was never in `latest.json` |
| 422 | `verdict` is not one of the three | fix the client |

`404` cannot happen for an article in the digest the client is currently
displaying. If it does, the client and the server are looking at different
digests.

### Idempotency

Sending `like` twice for the same URL leaves **one** verdict. There is no
"already recorded" response — both calls return 200 with the same current
state, so a retry after a timeout is always safe.

The underlying log keeps every line, so the history of a changed mind survives.
That is invisible through this API and no client should depend on it.

---

## `DELETE {base}/feedback?url=…`

Identical to `POST` with `"verdict": "clear"`, including the response body.
Both exist because both callers are real: the firmware speaks POST and nothing
else, and everything else expects DELETE to be how you undo a thing. Use
whichever suits; they are the same operation.

---

## `GET {base}/feedback`

Every current verdict, newest first. This is what a client calls on load to
render thumb state across a deck.

```json
{
  "count": 2,
  "likes": 1,
  "dislikes": 1,
  "verdicts": [
    {
      "verdict": "dislike",
      "url": "https://www.nbcmiami.com/news/local/…",
      "title": "Deadly shooting in Lauderhill…",
      "matched_area": "florida",
      "score": 0.611,
      "text_source": "embedded",
      "recorded": "2026-08-09T21:14:07.201Z"
    }
  ]
}
```

| query param | default | notes |
|---|---|---|
| `url` | — | return only this article's verdict; see below |
| `include_text` | `false` | include the stored `text` field |

**`text` is omitted by default** and runs to ~1000 characters per record. A
client rendering thumbs never needs it.

### `GET {base}/feedback?url=…`

```json
{ "url": "https://…", "verdict": "like", "record": { … } }
```

An article with no verdict returns **200** with `"verdict": null` and
`"record": null` — *not* a 404. "Not rated" is a state every card is in most of
the time; it is not an error.

---

## Fields the client should understand

| field | meaning |
|---|---|
| `verdict` | `"like"` or `"dislike"`. Never `"clear"` — a cleared verdict is absent, not stored as a value. |
| `matched_area` | the interest area the article was ranked under. The verdict only ever affects **this** area's scoring. |
| `text` | the exact string that was embedded when the article was scored. What makes the record outlive the URL. |
| `text_source` | `"embedded"` when `text` is the string the scorer used; `"display"` when it was reconstructed from what was shown, which happens for a verdict on a digest older than the server's index. Informational — clients can ignore it. |
| `recorded` | ISO 8601, UTC. |

---

## What a verdict actually does

Worth knowing so the UI doesn't promise more than the backend delivers.

- A **like** adds the article to a per-area average, which becomes one extra
  reference vector for that area. It can raise an article's score by at most
  **0.05** before the area weight, and never lowers one.
- A **dislike** becomes a negative vector for its area, subtracting at most the
  same 0.05.
- Both are scoped to `matched_area`, so a verdict recorded against the wrong
  area costs that area and nothing else.
- The **wildcard is not affected at all** — it is drawn from the ranking the
  written profile produces, before any of this.
- Nothing takes effect until the next pipeline run (`POST /refresh`, or the
  08:00 launchd job). A verdict does not reorder the digest already on screen,
  and the client should not imply that it will.

With no verdicts recorded, ranking is byte-identical to a build without the
feature.

---

## Rough edges to expect

- **Verdicts are not versioned against the digest.** If the pipeline rebuilds
  while a client is displaying an older digest, a verdict on an article that
  dropped out still records fine (via the 45-day index) but the client's view of
  which articles exist is stale.
- **No authentication.** Same as every other endpoint here; this is a LAN
  service on a home network.
- **No bulk endpoint.** One verdict per request. At a handful of taps a day
  that is not worth fixing.
