# Backlog

Audited 2026-10-18 against `forum-style` @ `8bffa98`. Every item below was
verified against the running code or by loading the page in Electron — none is
recalled from memory.

Status: `[ ]` open · `[x]` done · `[~]` partially addressed

---

## 🔴 Bugs

### Correctness — silent wrong answers or data loss

- [x] **B1. `pgvector` is not ensured on a new database.**
  `docker/postgres-init/01-vector.sql` runs *only* on first container init, so
  any database created afterwards (`CREATE DATABASE`, CI, a colleague's clone)
  has no `vector` type. `init_db()` then fails and every endpoint 500s.
  **Seen in practice** — 12 errors before the extension was added by hand.
  → **Fixed.** `init_db()` now runs `CREATE EXTENSION IF NOT EXISTS vector`
  first (guarded, best-effort, logged) and reports it.
  *Verified: a database with no pgvector now bootstraps itself — 13 tables.*

- [x] **B2. A failed `init_db()` is swallowed.** `main.py:83-84` logs
  `database_init_failed` and starts anyway, so the app *looks* healthy and
  every forum request fails later.
  → **Fixed.** The error is carried on `app.state.db_error` and surfaced on
  `/api/health` as `startup_error`. Health also now checks the **schema**, not
  just `SELECT 1` — a reachable database with no tables previously passed.
  *Verified: against a reachable-but-unusable DB, health reports
  `status: degraded`, `postgres_ok: true`, `schema_ok: false`.*

- [x] **B3. Topics strand in `status="debating"` forever.** Set at
  `api/forum.py:350,474`; nothing clears it if a run raises. No reaper, no
  retry, no recovery path. The thread is unrecoverable from the UI.
  → **Fixed.** New `fail_topic()` + `topics.error` column. Both run paths
  (`_continue_thread_locked`, `run_topic_streaming`) now mark a dead run
  `failed` with the reason, in their own session. `failed` is accepted by
  PATCH, and a retry clears the stale reason. Clients get a `run_failed` event.
  *Verified end-to-end: pinned ORACLE to a credential-less provider, ran a
  topic → `status=failed`, `error="ProviderError: no API key"`, readable
  through `GET /api/forum/topics/{id}`.*

- [x] **B4. API intake can lose a question permanently.**
  `claim_intake` marks the row `opened` and creates the topic *before* the
  background task is scheduled. A scheduling failure loses it with no trace.
  → **Fixed.** New `release_intake()` puts a claimed item back in the queue.
  Both intake paths now share `_open_intake_topic`, which releases the claim if
  topic creation fails, and release again if scheduling fails — in which case
  the orphaned topic is also marked `failed` so it does not look runnable.
  `drain_intake` now returns **503** when it has no task runner rather than
  draining the queue into topics that never run.
  *Verified: released item is queued, its stale `topic_id` link is cleared, and
  it is claimable again.*

- [x] **B5. `db/chroma.py:32` stacks two silent failures.** A genuine
  "collection not found" and a broken/unreachable Chroma both fell through to
  `create_collection`, which also failed and was caught by the outer handler —
  neither was ever logged.
  → **Fixed.** `_is_missing_collection()` separates the two (typed
  `NotFoundError`, plus a message fallback). A connection error no longer
  triggers `create_collection` and is logged with what actually went wrong.

- [x] **B6. `topic.cast` is read after its session closed.**
  Safe only because `expire_on_commit=False`.
  → **Fixed.** Captured inside the session block.

### Correctness — fixed 2026-10-17/18
Orphan topic on every streamed run · silent cast writing empty posts ·
missing per-topic locks (×3) · dropped `"rag_chunks"` knowledge source · empty
free-model catalogue under the default provider · the only bare `except:` ·
unguarded `json.loads` in the legacy WS loop · malformed UUID → 400 not 500 ·
`utcnow()` ×7 · unbounded + racy `_gap_tracker` · unbounded `TopicStream._locks` ·
blocking Neo4j call on the event loop · **32%/92% silent embedding truncation** ·
79 corrupted `openai/*` model ids · per-role provider selection dead in 3 callers ·
`okf/bundle.py` bypassing the selected embedding backend.

---

## 🟡 Quality

- [ ] **Q1. No re-embed command.** Changing the embedding model invalidates
  every stored vector and there is no way to rebuild them.
  **Do this before `rag_chunks` has data** — afterwards it is a second pass.

- [ ] **Q2. `api/forum.py`, `api/forum_ws.py` and 10 other routers have zero
  unit coverage.** This is why four defects shipped together.
  *(forum.py and forum_ws.py now have 21 tests.)*

- [x] **Q7. The suite only passed from `backend/`, and only with a real
  `.env`.** Found by checking the commit out into a clean worktree and running
  it there — not by reading the tests.

  Twenty-eight tests read source files as `Path("api/forum.py")`, so they
  resolved only when the working directory happened to be `backend/`. They now
  use `BACKEND = Path(__file__).resolve().parents[1]`, the convention the rest
  of the suite already followed.

  Five more (`test_provider_registry`, `test_model_selection`) called
  `build_kwargs(provider=providers.get("opencode"))`, which requires an API key.
  Their subject is kwarg construction, not authentication, so a fresh clone with
  no `.env` failed them for an unrelated reason. An autouse fixture supplies a
  dummy key; the tests that *are* about a missing credential set it back to
  empty explicitly.

  Verified: 614 pass from the repo root, from `backend/`, and with `.env`
  removed.

- [ ] **Q3. Ten routers still untested:** `websocket`, `deliberations`,
  `documents`, `verdicts`, `divination`, `wiki`, `okf`, `sessions`, `agents`,
  `rag`, `llm`.

- [ ] **Q4. The "cast differs by *how it thinks*" claim is untested.**
  `registry.py` documents the knowledge sources as "deliberately disjoint";
  nothing pins that, so it can rot silently.

- [ ] **Q5. No reranker stage.** Every source consulted agreed a cross-encoder
  reranker lifts retrieval more than swapping embedders does.

- [ ] **Q6. Free-tier guidance rots.** *Catalogue re-verified; detection still
  missing.*

  Re-checked against the live catalogue on 2026-10-22 and the rot was worse
  than recorded: `longcat-2.5-preview-free` (the **Oracle** default),
  `space-bunny-free`, both `muse-spark-*-contributor-free` entries and three of
  the eight paid suggestions no longer existed. `PROVIDERS.md` had drifted two
  generations behind, still listing `ling-3.1-flash-free` and
  `step-5-preview-free` for Analyst and Historian.

  Oracle now defaults to `minimax-m3.1-flash-free`, verified by exercising
  every free model through `opencode-cli`. Two measured findings: `jev-1.13-free`
  is listed as free but is not a chat model and fails, and `flint-40b-free`
  answers correctly but took **82s**, so neither is a default.

  Still open: nothing *detects* this at runtime. Health verifies an Ollama model
  is installed and that the CLI binary exists, but for Zen it only checks that
  an API key is set. A role pointed at a delisted model reports healthy and then
  produces nothing. The discovery layer already lists live models, so the check
  is reachable — compare each role's model against it and report
  `model_present: false` rather than `ok: true`.

- Free tier is **CLI-only**: `POST /v1/chat/completions` for a `-free` model
  returns `403 FreeTierError — "can only be used from within OpenCode"`, and a
  paid model returns `402 Insufficient account funds` on a zero balance.
  Verified 2026-10-22.

- [x] **Q8. Default to the Go subscription over HTTP, not the CLI.**

  The CLI was the default only because it was the only route to free models.
  On a subscription that constraint disappears, and the CLI costs a process
  spawn per call: `big-pickle` took **26.9s** for a four-word reply, and a
  one-round topic spent **426s** in the moderator's synthesis alone.

  New provider `opencode-go` at
  `https://opencode.ai/inference/go/openai/v1`, same key as Zen, with
  `deepseek-v4.1-flash` for every role. Measured through the app's own
  `llm_complete`: **2.9–6.2s per call**, against 1.0–27s for the CLI.

  The endpoint is not quite plain OpenAI-compatible: it answers
  `400 "Request is missing x-opencode-session and cannot be routed"` unless the
  request carries a session header. `Provider.extra_headers` now exists for
  exactly this, and `build_kwargs` attaches it — Zen and Ollama requests are
  unchanged, which a test pins.

  Free-tier access still requires the CLI, so `opencode-cli` stays available
  and is no longer the default. Ollama is documented as the secondary local
  fallback.

  Verified: unit tests for provider shape, headers, routing, and discovery; and
  three **live** tests against the real endpoint, including one that asserts
  the header requirement still exists so the header can be deleted rather than
  carried as folklore if it ever stops being true.

---

## 🟠 GUI

*Verified by loading all 13 pages in Electron and reading console errors +
backend status codes. 11 of 13 clean; 74/74 API calls returned 200.*

- [x] **U1. `models.html` renders 3,649 `<option>` elements.**
  6 role dropdowns × 612 live models; 116,897 chars of body text against
  ~600 for every other page. A 600-item native `<select>` cannot be searched.
  *Regression introduced when model selection was added.*
  → **Fixed.** Replaced with a searchable combobox: a filter input plus a
  listbox that renders at most 60 matches, with the catalogue held in JS.
  Each row also states its current model in text, since the selection is no
  longer visible in a control.

  | | before | after |
  |---|---|---|
  | `<option>` elements | 3,649 | **25** |
  | DOM nodes | 3,854 | **278** |
  | body text (chars) | 116,897 | **2,468** |

  *Verified in Electron: typing `free` filters 605 → 29, typing `qwen` → 50,
  clicking a row sets the selection, unfiltered shows "60 of 605".*

  A subtlety worth keeping: a stored bare tag (`longcat-2.5-preview-free`)
  exists in the catalogue as **both** `opencode/…` and `opencode-go/…`. The page
  now marks the one the backend would actually route to
  (`llm.cli_provider.to_cli_model` → `opencode/`), so opening the page does
  not appear to re-point the character at a different route.

- [x] **U2. Every page loaded fonts from `fonts.googleapis.com` at runtime**;
  `topic.html` also pulled `cdn.jsdelivr.net`. An offline desktop app rendered
  completely unstyled, and a live failure was observed during the audit
  (`Refused to apply style … MIME type 'text/html'`).
  → Fixed with U3. See "Offline assets" below.

- [x] **U3. Tailwind via `cdn.tailwindcss.com`** — shipped a production warning
  on every page, recompiled CSS at runtime, broke offline.

### Offline assets (U2 + U3 + O2)

The pages are loaded over `file://`, so "works offline" is the normal case, not
an edge case. Three separate network dependencies are now gone.

**One stylesheet per page, not one shared config.** The thirteen pages do not
share a Tailwind theme. `agent_profiles.html` and `history.html` set
`pixel-body: 16px` where the others use 18px, and `council_chamber.html` omits
letter-spacing entirely. Those differences read as deliberate — the denser
pages use smaller type — so a single config would have silently restyled them.
`com_website/tailwind.config.js` holds all thirteen and `build-css.js` compiles
each page from its own entry. `test_offline_assets.py` pins the difference.

**Configs moved out of the HTML.** They were inline
`<script>tailwind.config = {…}</script>`, which only worked because the CDN
script defined the `tailwind` global. Removing the CDN made every page throw
`ReferenceError: tailwind is not defined` — invisible in the UI, since the
config had already been compiled in, but a hard error in the console
thirteen times over.

**Fonts are vendored, subset.** Material Symbols is fetched with `icon_names=`
covering only the 69 glyphs in use (234 kB total for all three families).
The glyph list is harvested from the **rendered DOM**, not by regex: icons
reach the DOM three ways, and a regex-only pass missed the NAV arrays
(`['models.html', 'tune', 'Models']`) and shipped a font without `tune`, so the
sidebar rendered the literal word "TUNE".

**Verified, not assumed.** `offline-check.js` runs Chromium with
`MAP * ~NOTFOUND`, making every DNS lookup fail, and then checks the page is
actually styled rather than merely that no request was attempted: theme colours
resolve (`text-runed-gold` → `rgb(255, 215, 0)`), VT323 and Material Symbols
report `status === 'loaded'`, and zero network requests are attempted. All
thirteen pages also carry a screenshot diff against the CDN baseline; four are
pixel-identical and the rest differ only where the database moved between
captures.

Build: `npm run build:web` in `desktop/`. Generated assets are committed —
the app has no build step on install, and `electron-builder` already packages
`../com_website/**/*`.

- [x] **S1. Markdown fallback failed open.** `topic.html` and
  `council_chamber.html` both did:

  ```js
  const html = window.marked ? marked.parse(src) : escapeHtml(src);
  return window.DOMPurify ? DOMPurify.sanitize(html) : html;
  ```

  If `marked` loaded and DOMPurify did not — precisely what a partial CDN
  outage produces, and what happened here — the second line returned marked's
  raw output with nothing sanitising it, and model output and user posts are
  rendered through this path. Both libraries are now vendored, and both
  functions bail to escaped text if *either* is missing.
  `test_markdown_sanitisation.py` extracts the real functions and exercises all
  four availability combinations against a stub DOM. Found while writing
  `test_offline_assets.py`, not by looking for it.

### Verified *not* problems
- `topic.html` `ERR_ABORTED` — by design; no `?id=` redirects to forum.
- `/api/llm/discovery` shells out to `opencode models` but answers in **0.17s**.

---

## ⚡ Optimization

- [x] **O1. Searchable model picker** — removes 3,649 DOM nodes and makes 605
  models usable. Fixes U1.
- [x] **O2. Vendor Tailwind + fonts into the Electron bundle.** Turns
  "works offline" from false to true. Done with U2/U3 above.
- [x] **O3. Widen `Vector(384)` → `Vector(1024)`.** — **Measured, and rejected.**
  `qwen3-embedding:0.6b` is natively 1024-dim and the store folds it to 384
  through the Matryoshka head, so widening is the obvious "use the model's full
  capacity" move. `backend/tools/embedding_width_bench.py` measures whether it
  buys anything, on two corpora: a broad one, and a deliberately confusable one
  where near-neighbour topics compete for the same query.

  | width | broad MRR | broad R@3 | confusable MRR | confusable R@3 |
  |-------|-----------|-----------|----------------|---------------|
  | 128     | 0.936 | 0.964 | 0.804 | 0.938 |
  | 256     | 0.973 | 0.964 | 0.771 | 0.938 |
  | **384** | **0.958** | **1.000** | **0.810** | **0.938** |
  | 512     | 0.964 | 1.000 | 0.811 | 0.938 |
  | 768     | 0.976 | 1.000 | 0.810 | 0.938 |
  | 1024    | 0.958 | 1.000 | 0.826 | 0.875 |

  384 already saturates: recall@3 is 1.000 on the broad corpus, and everything
  from 384 to 1024 sits inside one query's worth of difference (n=16 on the
  confusable set, so one query is 6 points — recall@3 actually *drops* at
  1024). Only 128 and 256 are measurably worse. Widening would cost a schema
  migration, a re-embed of every stored chunk, 2.7x the vector storage and
  slower index builds, for no measured retrieval gain.

  What the run *does* establish: 384 was a good choice rather than an arbitrary
  one, and there is real headroom below it. Re-run the benchmark before ever
  lowering it.

  Caveat: this measures embedding quality under exact search, not approximate
  index behaviour at scale. Higher dimensions do help ANN recall on very large
  corpora, and this project is nowhere near that — its own OKF bundle is ~31
  chunks. If the corpus reaches the 10^5 range, revisit with a scale test.

  The window where changing this would be cheap — `rag_chunks` and `documents`
  are both empty — is noted, not acted on.
  is lossy — currently paying for a 1024-dim model and storing 384.
  **Only worth it before `rag_chunks` has data**, or you re-embed twice.
- [ ] **O4. Cut free-tier cost of a topic run.** 6 characters × 2 rounds ×
  1.4–3.2s ≈ 40–60s. Tiered rounds (one round, then only unresolved
  challengers) is the biggest latency win available.
- [ ] **O5. Cache `_bundle_fingerprint()`.** `okf/bundle.py` does
  `rglob("*.md")` + a `stat()` per file on *every* retrieval.
- [x] **O6. Lock the OKF index build.** `to_thread`'d but mutated shared state
  without a lock.

  Two failure modes, both real. The check-then-act guard had no lock, so every
  concurrent caller saw a cold cache as stale and built its own index — at the
  opening of a debate five characters retrieve at once, so that was five
  embedding passes over the whole bundle. Worse, the cache was a module-level
  dict updated key-by-key, so a reader could see `ids` from one build beside
  `embeddings` from another, pairing the wrong rows with the wrong vectors and
  returning confidently wrong results instead of failing.

  Now a frozen `_ConceptIndex` rebound under a `threading.Lock`, with the
  fingerprint recomputed inside the lock and the build done before publication.
  Readers take the returned snapshot rather than the module global, so a
  concurrent republish cannot tear the read.

  Verified two ways. `test_okf_index_race.py` fires 8 threads at a cold cache:
  the old code reports **`assert 8 == 1`** — eight builds — and the new code
  builds once. Against the real 30-concept bundle, 10 concurrent retrievals
  produce one `count=30` embed followed by ten `count=1` query embeds, in 2.82s
  with no errors.
- [ ] **O7. Split the three oversized functions.** `moderator_synthesis`
  (154 lines) does prompt construction, the LLM call, fallback synthesis,
  signal enforcement and two DB writes.

---

## Environment notes

- **`init_db()` installs pgvector now** (B1), so a fresh database bootstraps
  on its own. The conftest guard still refuses a DB whose name lacks
  `test`/`tmp`/`scratch` unless `--allow-real-db` is passed.
- **Live tests that touch `db.postgres.engine` must share one event loop.**
  The engine's pool is bound to the loop that first used it, so `asyncio.run()`
  inside a sync test poisons every later live test with "attached to a
  different loop". Use `@pytest.mark.asyncio(loop_scope="session")`, as
  `test_forum_integration.py` and `test_schema_evolution.py` do.
- **The live suite rewrites `backend/okf/bundles/oracle/index.md`.**
  `git checkout` it before committing.
- Test DBs are disposable: `POSTGRES_URL=…/council_test pytest -m live --run-live`.