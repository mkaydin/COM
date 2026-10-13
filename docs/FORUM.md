# The Forum

A topic is a persistent thread. Characters post in order, each one reading
what the others have actually said.

## Model

### `topics`

| column | meaning |
|--------|---------|
| `id` | uuid |
| `title` | the Moderator's title |
| `question` | the raw seed question, verbatim |
| `framing` | the Moderator's framing — posted as the opening |
| `origin` | `user` · `api` · `moderator` |
| `status` | `open` · `debating` · `concluded` · `archived` |
| `mode` | requested outcome: `auto` · `consensus` · `diverse` |
| `outcome` | what actually happened: `consensus` · `plurality` · null |
| `cast` | ordered character keys |
| `tags` | free-form |
| `source_meta` | API provenance (source system, request id, payload) |
| `synthesis` | the Moderator's closing answer or positions |
| `signals` | the deterministic convergence measurement |
| `max_rounds` / `round_count` | the round budget and its use |
| `session_id` | optional link into the Archive |

### `posts`

| column | meaning |
|--------|---------|
| `seq` | per-topic monotonic, **UNIQUE** |
| `round` | 0 = opening, 1..n = exchange rounds |
| `author_kind` | `user` · `moderator` · `character` |
| `author` | character key, or `moderator` / `user` |
| `stance` | `open` `propose` `support` `challenge` `question` `concede` `synthesize` |
| `reply_to` | the post being answered |
| `claims` | structured claims — **this is what convergence measures** |
| `citations`, `extra` | sources, reasoning, retrieval stats |

`extra.reply_to_seq` mirrors `reply_to` so the signal layer can resolve
"which post was this challenging" without a second query.

> **`seq` allocation.** `append_post` takes a row lock on the topic
> (`SELECT … FOR UPDATE`) before reading `MAX(seq) + 1`. The `exchange` node
> writes sequentially anyway, but the lock is the backstop that makes the
> UNIQUE constraint unreachable rather than merely unlikely.

### `topic_intake`

The API-fed question queue: `queued` → `opened` | `rejected`. `claim_intake` is
a conditional UPDATE under `FOR UPDATE`, so two workers cannot both open the
same question.

---

## State machine

```
                 POST /api/forum/topics
                          │
                          ▼
   ┌──────────────────────────────────────────┐
   │  open_topic    Moderator frames, picks    │   status: open
   │                the cast and the mode      │
   └────────────────────┬─────────────────────┘
                        ▼
   ┌──────────────────────────────────────────┐
   │  statements     everyone states a position │   parallel — nobody is
   │                (fan-out is correct here)  │   responding yet
   └────────────────────┬─────────────────────┘
                        ▼
   ┌──────────────────────────────────────────┐
   │  exchange       ONE round, cast in        │   sequential, so each
   │                canonical speaking order   │   reads this round's
   └────────────────────┬─────────────────────┘   replies
                        │
         ┌──────────────┴──────────────┐
         │ converged, or round spent   │
         └──────────────┬──────────────┘
                        ▼
   ┌──────────────────────────────────────────┐
   │  synthesis      consensus or plurality   │   status: concluded
   └──────────────────────────────────────────┘
```

`max_rounds` is checked in exactly one place (`decide_continue`). A cast that
disagrees forever still terminates. Early convergence skips the remaining
rounds rather than burning free-tier calls.

---

## Convergence

`agents/forum_signals.py`. Pure functions over plain dicts.

| signal | rule |
|--------|------|
| agreement | Jaccard overlap of content words between two characters' **final claims** ≥ `0.35` |
| clustering | union-find over agreeing pairs → agreement clusters |
| coverage | largest cluster ÷ cast ≥ `2/3` |
| spread | max − min confidence ≤ `0.25` |
| challenges | every `challenge`/`concede` needs a later `concede` from the challenged author |
| minimum cast | **≥ 2 characters** — one voice agreeing with itself is not consensus |

A thread is converged only if all of those hold.

### The veto

`forum_signals.enforce(signals, claimed_mode)` — if the Moderator claims
`consensus` on a thread that has not converged, it is downgraded to
`plurality` and the reason is recorded in `synthesis.signals_note`.

This is deliberate. The failure a forum cannot afford is a confident-sounding
verdict over an actual disagreement, and a language model asked "did the
Council agree?" will sometimes say yes.

---

## HTTP API

### Topics

```
POST   /api/forum/topics                      open and run
GET    /api/forum/topics                      list (status, outcome, tag, cast, session)
GET    /api/forum/topics/{id}                 topic + posts   (?since_seq=N)
PATCH  /api/forum/topics/{id}                 rename / retag / archive
DELETE /api/forum/topics/{id}                 delete (posts cascade)
GET    /api/forum/topics/{id}/posts           paginated posts (?since_seq=N)
POST   /api/forum/topics/{id}/posts           user reply; optionally triggers a round
POST   /api/forum/topics/{id}/close           force a closing synthesis now
POST   /api/forum/topics/{id}/reopen          one more round
```

`POST /api/forum/topics` takes:

```jsonc
{
  "question": "…",          // required
  "mode": "auto",           // auto | consensus | diverse
  "cast": ["oracle","sage"],// empty = let the Moderator choose
  "tags": ["architecture"],
  "stream": true,           // true: 202 + stream over WS; false: block for outcome
  "max_rounds": 2
}
```

### Intake (the API path)

```
POST   /api/forum/intake                     submit a question
GET    /api/forum/intake?status=queued       inspect the queue
POST   /api/forum/intake/drain?limit=5       open queued questions as topics
DELETE /api/forum/intake/{id}?reason=…       reject one
```

```jsonc
{
  "question": "…",
  "source": "jira",
  "payload": {"ticket": "PROJ-42"},
  "options": {"cast": ["oracle"], "mode": "consensus", "max_rounds": 2},
  "open_now": true
}
```

**Authentication.** `FORUM_INTAKE_KEY` gates intake via `X-COM-Key`. Empty means
local-only, which is the default — a forum that accepts questions from the open
internet by default is not a reasonable thing to ship.

### Discovery

```
GET /api/forum/cast        the cast, each with its live model binding
GET /api/forum/status      enabled, cast, max_rounds, whether intake is open
GET /api/forum/stats       topic / post / outcome counts
```

---

## WebSocket

```
WS /ws/forum/{topic_id}[?since_seq=N]
```

On connect the server replays the thread so far, then streams live.

**Server → client**

| message | when |
|---------|------|
| `topic_opened` | the topic and its opening post |
| `post_created` | one per post, as it is written |
| `round_started` | a new exchange round begins |
| `round_completed` | the round's posts are all in |
| `signals_updated` | deterministic convergence after the round |
| `synthesis` | the outcome |
| `topic_concluded` | `{topic_id, outcome}` |
| `error` | `{message}` |

**Client → server**

```jsonc
{"type": "reply", "body": "…", "trigger_round": true}
{"type": "close"}          // force a closing synthesis
{"type": "ping"}
```

One run, many watchers: `forum_ws.TopicStream` fans a run's events out to every
socket on that topic, so a second tab does not trigger a second run.

---

## Failure behaviour

A provider outage must not produce a thread that reads like the Council
considered the question and declined to answer.

- A character that fails produces **no post**. Writing one apology per
  character per round buries the thread in identical noise.
- The moderator is told which characters did not post, and instructed to report
  the outage rather than synthesise around it.
- If *nobody* posted, `_fallback_synthesis` says exactly that.
- `llm_call_with_retry` raises auth errors on the first attempt — a missing API
  key used to cost 1s+2s+4s per call and turn a misconfiguration into a ~50s
  thread. A full two-round topic with no key now completes in ~4s.

---

## Testing

`tests/test_forum_signals.py` — the convergence maths, including the veto.
`tests/test_forum_graph.py` — wiring and the round bound.
`tests/test_forum_integration.py` — live, real Postgres, stubbed LLM. Runs
complete topics through both outcome shapes and checks the schema, `seq`
uniqueness, `since_seq`, intake, cascade delete.

```bash
cd backend
pytest -q                                              # unit
pytest -q -m live --run-live --allow-real-db          # + integration
```