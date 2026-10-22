#!/usr/bin/env python3
"""Does the Moderator keep leaving the Oracle out of the cast?

Spotted while validating the Go provider: a retrieval question came back with a
four-character cast and no Oracle — the character whose entire job is grounding
answers in the OKF knowledge bundle. One run proves nothing, so this repeats the
Moderator's *opening* decision across varied questions and counts.

It calls the same prompt `forum_graph.open_topic` uses, with the same
temperature, but stops before the DB write — so it measures the cast decision
and nothing else.

    python backend/tools/cast_frequency.py [--runs 2]
"""

import argparse
import asyncio
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import registry  # noqa: E402
from llm import llm_complete  # noqa: E402
from agents.json_utils import extract_json  # noqa: E402

MODERATOR_ROLE = "MODERATOR"

# Deliberately mixed: some are knowledge questions the Oracle is the obvious
# fit for, some are judgement calls where any cast is defensible. If the Oracle
# is dropped across both kinds, it is a bias rather than a matching decision.
QUESTIONS = [
    ("retrieval", "Should a retrieval system chunk documents by structure or by fixed token windows?"),
    ("retrieval", "How should we tune an ivfflat index for a corpus of a few thousand vectors?"),
    ("retrieval", "When is a reranker worth the latency over plain vector search?"),
    ("knowledge", "What does the filed knowledge base say about pgvector index parameters?"),
    ("knowledge", "Summarise what we already know about embedding model selection."),
    ("knowledge", "What concepts in the bundle relate to chunking strategy?"),
    ("judgement", "Is it ethical to publish model outputs without labelling them as synthetic?"),
    ("judgement", "Should a council of models ever overrule a human operator?"),
    ("engineering", "How do we stop a background worker that refuses to shut down?"),
    ("engineering", "What is the cost of holding a database transaction open across an LLM call?"),
]


async def one(question: str) -> dict:
    messages = [
        {"role": "system", "content": registry.MODERATOR_PROMPT},
        {
            "role": "user",
            "content": (
                "OPEN A NEW TOPIC.\n\n"
                f"RAW QUESTION: {question}\n\n"
                "Choose the cast yourself.\n"
            ),
        },
    ]
    resp = await llm_complete(
        messages, role=MODERATOR_ROLE, temperature=0.3, max_tokens=1024
    )
    data = extract_json(resp.content) or {}
    try:
        cast = registry.validate_cast(data.get("cast") or registry.default_cast())
    except (KeyError, ValueError):
        cast = registry.default_cast()
    return {"cast": cast, "mode": data.get("mode"), "reasoning": data.get("reasoning")}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="repeats per question")
    args = ap.parse_args()

    total = collections.Counter()
    by_kind = collections.defaultdict(lambda: [0, 0])
    rows = []

    for run in range(args.runs):
        for kind, question in QUESTIONS:
            try:
                out = await one(question)
            except Exception as e:  # noqa: BLE001
                rows.append((kind, question, f"FAILED {type(e).__name__}", []))
                continue
            cast = out["cast"]
            has_oracle = "oracle" in cast
            total["runs"] += 1
            total["oracle"] += has_oracle
            by_kind[kind][0] += has_oracle
            by_kind[kind][1] += 1
            rows.append((kind, question, ",".join(cast), out["reasoning"] or ""))

    print(f"\n{'kind':12} {'cast':44} question")
    print("-" * 110)
    for kind, question, cast, _reason in rows:
        flag = "" if "oracle" in cast else "   <-- no oracle"
        print(f"{kind:12} {cast:44} {question[:44]}{flag}")

    print(f"\nOracle included in {total['oracle']}/{total['runs']} casts")
    for kind, (hit, n) in sorted(by_kind.items()):
        print(f"  {kind:12} {hit}/{n}")

    missing = [r for r in rows if "oracle" not in r[2]]
    if missing:
        print(f"\nreasoning for the {len(missing)} without the Oracle:")
        for kind, question, cast, reason in missing[:4]:
            print(f"  [{kind}] {reason[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))