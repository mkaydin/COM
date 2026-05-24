# Sage Wiki Schema
#
# This file defines conventions and workflows for the Sage agent
# as a disciplined wiki maintainer. The Sage reads this on every session.
# Co-evolve this file with the Sage over time.

## Directory Structure

```
sage_wiki/
├── SCHEMA.md             ← This file
├── raw/                  ← Immutable source documents (never modified by Sage)
│   ├── papers/           ← Academic papers, research
│   ├── articles/         ← Web articles, blog posts
│   └── assets/           ← Downloaded images, data files
├── wiki/                 ← Sage-maintained markdown knowledge base
│   ├── index.md          ← Catalog of all pages (auto-updated on every ingest)
│   ├── log.md            ← Append-only chronological log
│   ├── entities/         ← Pages for specific entities (people, orgs, objects)
│   ├── concepts/         ← Pages for abstract concepts, theories, patterns
│   ├── sources/          ← One summary page per ingested source document
│   ├── comparisons/      ← User-requested comparison tables
│   ├── syntheses/        ← Evolving synthesis on big questions
│   └── queries/          ← Good answers filed back as persistent knowledge
└── .git/                 ← Version history of the entire wiki
```

## Page Conventions

### Every wiki page MUST have YAML frontmatter:
```yaml
---
title: "Page Title"
type: entity | concept | source | comparison | synthesis | query
tags: [tag1, tag2]
created: 2026-05-21
updated: 2026-05-22
sources: [raw/papers/paper1.pdf, raw/articles/article2.md]
confidence: high | medium | low | speculative
status: draft | reviewed | superseded
---
```

### Cross-referencing
- Link to other wiki pages using `[[wiki/entities/some-entity]]`
- Link to raw sources using `(raw/papers/some-paper.pdf)`
- Flag contradictions explicitly with `> ⚠️ CONTRADICTION:` blockquote

## Operations

### Ingest Workflow
When the user drops a source into `raw/`:
1. Read the full source document
2. Discuss key takeaways with the user — what to emphasize, what to skip
3. Write `wiki/sources/<source-title>.md` — a summary page
4. For each key entity found, create or update `wiki/entities/<name>.md`
5. For each key concept found, create or update `wiki/concepts/<topic>.md`
6. Check related wiki pages for contradictions — flag them explicitly
7. Update `wiki/index.md` — add entry with link + one-line summary + tags
8. Append to `wiki/log.md`:
   ```
   ## [YYYY-MM-DD] ingest | Source Title
   - Created: [[wiki/sources/source-title]]
   - Updated: [[wiki/entities/...]], [[wiki/concepts/...]]
   - Key insight: one-line summary
   ```

### Query Workflow
When the user asks a question:
1. Read `wiki/index.md` first to find relevant pages
2. Search by keyword matches in page titles and tags
3. Load the 3-5 most relevant wiki pages (not all)
4. Synthesize an answer with citations using `[[wiki/...]]` links
5. If the answer is valuable, FILE IT BACK as `wiki/queries/<query-slug>.md`

### Lint Workflow
Periodic health check (run manually or on schedule):
1. Scan all wiki pages for contradictions (claims that disagree)
2. Find orphan pages (no inbound links from any other page)
3. Identify stale claims (superseded by newer sources in the log)
4. Find missing cross-references (entities mentioned but lacking their own page)
5. Flag data gaps (questions the wiki can't yet answer)
6. Suggest new sources to find (web search or user upload)
7. Report findings to user with actionable suggestions

## Style Guide
- Write in clear, concise markdown
- Prefer bullet points over long paragraphs for entity/concept pages
- Use tables for comparisons
- Use blockquotes for direct claims from sources
- Always distinguish wiki-documented fact from speculative interpretation
- When uncertain, say so explicitly: "⚠️ This is speculative. Evidence is mixed."
