#!/usr/bin/env python3
"""Measure what the vector width actually costs in retrieval quality.

`VECTOR_DIM` was 384, folding `qwen3-embedding` (natively 1024) down through
its Matryoshka head. Widening the store is only worth a migration if the wider
vector retrieves better, so this measures it instead of assuming it.

The corpus is small but deliberately *hard*: each query describes a topic using
words the chunks never use, so a bag-of-words match cannot win. Recall@3 and
MRR are reported per width.

    python backend/tools/embedding_width_bench.py
"""

import asyncio
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

OLLAMA = "http://localhost:11434/api/embed"
MODEL = "qwen3-embedding:0.6b"
WIDTHS = (128, 256, 384, 512, 768, 1024)

# (subject, [chunk sentences], [queries describing the same subject in
# different words]). Every query is lexically disjoint from its chunks.
TOPICS = [
    ("pgvector index tuning", [
        "ivfflat builds a coarse quantiser with `lists` centroids",
        "raising lists above rows/1000 improves recall and costs memory",
        "probes controls how many lists a query visits at search time",
        "hnsw grows a navigable small world graph instead of flat centroids",
        "ef_search must be raised above 40 for hnsw to find true neighbours",
    ], [
        "how do I stop the nearest-neighbour index from missing matches?",
        "what knobs make approximate search consider more of the space?",
    ]),
    ("asyncio task cancellation", [
        "cancelling a task raises CancelledError at its next await point",
        "shield lets the inner future finish while the outer is cancelled",
        "gather with return_exceptions keeps siblings running",
        "a task that swallows CancelledError cannot be shut down cleanly",
        "wait_for times out and cancels the awaited future",
    ], [
        "my background worker refuses to stop when I tell it to quit",
        "why does shutdown hang even though I cancelled the task?",
    ]),
    ("soil nitrogen cycling", [
        "nitrifying bacteria oxidise ammonium to nitrite then nitrate",
        "legume rhizobia fix atmospheric nitrogen into plant-available forms",
        "denitrification returns nitrate to dinitrogen gas in wet conditions",
        "compost maturity depends on carbon to nitrogen ratio near 30 to 1",
        "cover crops reduce nitrate leaching into groundwater",
    ], [
        "how does nitrogen get returned to the air from farmland?",
        "what keeps fields from washing fertiliser into rivers?",
    ]),
    ("medieval charter boundaries", [
        "a charter lists manors held in demesne by the abbey",
        "perches and furlongs measure area in customary units",
        "marginalia record later grants squeezed into available space",
        "a cope is a duplicate surviving only because the original was lost",
        "writs in the chancery registers authorised royal itinerations",
    ], [
        "what surviving paperwork tells us about land an abbey owned?",
        "why do some old land grants survive only as later copies?",
    ]),
    ("SQL query planner", [
        "the planner chooses between a sequential scan and an index scan",
        "estimated row counts come from statistics and drive the cost model",
        "a nested loop re-executes the inner side per outer row",
        "work_mem bounds the sort and hash before it spills to disk",
        "a misleading predicate can pin the planner to the wrong plan",
    ], [
        "why does my query ignore the index I built for it?",
        "the database keeps choosing a slow path despite good statistics",
    ]),
    ("bee colony thermoregulation", [
        "workers shiver to raise thoracic temperature during cold snaps",
        "the winter cluster contracts as ambient temperature falls",
        "fanning workers drive evaporative cooling in summer",
        "the brood nest must hold 34 to 35 degrees for development",
        "drone brood is capped to regulate colony composition",
    ], [
        "how do insects hold a steady temperature through the seasons?",
        "what stops the young dying when it gets cold outside?",
    ]),
    ("package manager resolution", [
        "semver ranges resolve to the highest version satisfying every bound",
        "a lockfile pins exact versions plus transitive integrity hashes",
        "peer dependencies express what a library expects from its host",
        "dependency resolution is a search over a version lattice",
        "overrides let a parent override a transitive version constraint",
    ], [
        "the library keeps building against an API it says it needs",
        "why does a fresh checkout install different versions than yesterday?",
    ]),
    ("archival film restoration", [
        "colour grading rebuilds faded dye records from a faded print",
        "wet-gate scanning reduces base scratch density during transport",
        "grain reduction trades real texture for a cleaner image",
        "a workprint may survive when the camera negative did not",
        "rehydration shrinks and gelatin embrittlement with age",
    ], [
        "recovering the true colours of a movie that has gone milky",
        "what can be recovered from a print when the original is gone?",
    ]),
    ("glacier mass balance", [
        "the accumulation area gains snow while the ablation zone loses it",
        "a surge-type glacier advances in short bursts, not smoothly",
        "calving fronts retreat where the ice meets seawater",
        "meltwater beneath the ice lubricates faster basal sliding",
        "geothermal heat can sustain a lake at the ice bed year-round",
    ], [
        "why does a river of ice sometimes lurch forward suddenly?",
        "what makes ice sheets grow in one place and shrink in another?",
    ]),
    ("type inference in Hindley-Milner", [
        "unification assigns a substitution to the type variable",
        "let-polymorphism generalises only bindings not yet used",
        "value restriction blocks generalisation of expansive expressions",
        "occurs check rejects infinite types like t is a list of t",
        "principal types describe every instantiation the term admits",
    ], [
        "the compiler refuses to accept a recursive function that is valid",
        "a generic function keeps needing an explicit type annotation",
    ]),
    ("fermentation and pH", [
        "lactic acid bacteria drop pH and outcompete spoilage flora",
        "salt concentration selects for halophilic organisms",
        "a starter culture supplies the microbial population deliberately",
        "acidity in the final product preserves it against pathogens",
        "wild fermentation depends entirely on local microbiome",
    ], [
        "keeping food from going bad without a refrigerator",
        "adding salt or vinegar to make something last longer",
    ]),
    ("tide gauge datum", [
        "chart datum is a low water level chosen for safety margins",
        "a tide staff reads against a fixed benchmark of known elevation",
        "barometric pressure inversely adjusts observed water level",
        "storm surge is the anomaly above the predicted astronomical tide",
        "vertical datum changes shift the entire record and its products",
    ], [
        "why do two tide tables for the same harbour disagree?",
        "how do we know the sea level reading is not just weather pressure?",
    ]),
    ("lock-free data structures", [
        "compare-and-swap is the only primitive needed for atomics",
        "a hazard pointer protects nodes another thread may free",
        "ABA is resolved with a tagged pointer carrying a version",
        "epoch reclamation batches frees until readers pass a grace period",
        "linearisability is the correctness condition clients can observe",
    ], [
        "thread-safe code that never blocks and never deadlocks",
        "a queue where producers and consumers never wait on each other",
    ]),
    ("coastal wetland carbon", [
        "mangroves accrete sediment and raise the substrate",
        "blue carbon in saltmarsh soils accumulates over centuries",
        "restoration fails where sediment supply was cut off upstream",
        "seagrass meadows buffer wave energy before it reaches the shore",
        "drainage for agriculture releases stored soil carbon as CO2",
    ], [
        "wetlands that hold far more carbon than the forests around them",
        "protecting land that would otherwise release greenhouse gases",
    ]),
]

# The first corpus saturates: every topic sits far from every other, so
# recall@3 hits 1.000 from 384 upward and the width stops mattering. These
# pairs are deliberately near-neighbours — same domain, different mechanism —
# which is the regime where a narrower vector should actually lose signal.
# (subject, chunks, queries) grouped as confusable pairs.
HARD_PAIRS = [
    (
        ("btree index", [
            "a btree keeps keys in sorted order in contiguous pages",
            "branching factor is set by the page size",
            "sequential scans lose when the selectivity is low",
            "composite keys order by the leading column first",
            "a bloated btree splits pages and wastes space",
        ], [
            "which index structure stores entries in sorted order?",
            "why has my ordered index grown so large after many updates?",
        ]),
        ("skip scan", [
            "a skip scan advances a cursor to the next distinct leading value",
            "it helps when the leading column has very few distinct values",
            "the planner may choose it instead of a full index scan",
            "each distinct value still needs its own index descent",
            "it is most effective when later columns are selective",
        ], [
            "reading an index that skips past repeated first-column values",
            "which technique jumps between groups of equal leading keys?",
        ]),
    ),
    (
        ("SQL join: nested loop", [
            "the outer table drives one inner probe per row",
            "cost is the product of outer rows and inner cost per probe",
            "wins when the outer side is tiny",
            "an index on the inner side turns it into an index scan per row",
        ], [
            "which join runs the inner query once for every outer row?",
            "how do I make a join cheap when one table has only a few rows?",
        ]),
        ("SQL join: hash join", [
            "the build side is hashed into a bucket table in memory",
            "the probe side is scanned once and looked up per row",
            "cost grows with the build side but not with row multiplicity",
            "it spills to disk when work_mem is exceeded",
            "good when many outer rows share the same join key",
        ], [
            "which join builds a lookup table then scans the other side once?",
            "what should I raise when a join spills to disk?",
        ]),
    ),
    (
        ("forward error correction", [
            "a parity bit detects but cannot correct a single flip",
            "hamming code corrects one error and detects two",
            "reed-solomon corrects bursts up to t symbols",
            "interleaving scatters a burst into independent symbols",
        ], [
            "which code fixes a single flipped bit using extra check bits?",
            "what corrects several consecutive corrupted symbols?",
        ]),
        ("checksums and CRCs", [
            "a CRC detects errors but carries no correction capability",
            "the remainder of division by a generator polynomial is stored",
            "burst errors up to the polynomial degree go undetected",
            "a checksum is for accidental corruption, not tampering",
        ], [
            "what detects accidental corruption but cannot repair it?",
            "which code was designed to catch bursts from a noisy line?",
        ]),
    ),
    (
        ("HTTPS certificate pinning", [
            "pinning restricts trust to a known key or SPKI hash",
            "backup pins allow rotation without a hard outage",
            "pinning is set in native builds, not in a web page",
            "a compromised CA cannot mint an accepted certificate",
        ], [
            "limiting which keys a client will trust even if a CA is broken",
            "how does a mobile app avoid accepting any certificate?",
        ]),
        ("OAuth device code flow", [
            "the device shows a short code the user types elsewhere",
            "polling exchanges the code for a token once approved",
            "the flow suits input-constrained devices with no browser",
            "denial shows a code the user can cancel",
        ], [
            "signing in on a screen with no keyboard or browser",
            "authorising a token by typing a code on another device",
        ]),
    ),
]


async def embed(texts: list[str], dims: int | None) -> list[list[float]]:
    body = {"model": MODEL, "input": texts, "truncate": False}
    if dims:
        body["dimensions"] = dims
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(OLLAMA, json=body)
        r.raise_for_status()
        return r.json()["embeddings"]


def cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def build_corpora() -> dict[str, tuple[list[str], list[int], list[tuple[str, int]]]]:
    """-> {name: (chunks, owners, queries)}"""
    out = {}

    chunks, owners, queries = [], [], []
    for ti, (_s, chunk_list, query_list) in enumerate(TOPICS):
        for c in chunk_list:
            chunks.append(c)
            owners.append(ti)
        for q in query_list:
            queries.append((q, ti))
    out["broad"] = (chunks, owners, queries)

    chunks, owners, queries = [], [], []
    ti = 0
    for pair in HARD_PAIRS:
        for _subject, chunk_list, query_list in pair:
            for c in chunk_list:
                chunks.append(c)
                owners.append(ti)
            for q in query_list:
                queries.append((q, ti))
            ti += 1
    out["confusable"] = (chunks, owners, queries)
    return out


async def evaluate(chunks, owners, queries) -> dict[int, dict]:
    embeddings: dict[int, list[list[float]]] = {}
    for w in WIDTHS:
        embeddings[w] = await embed(chunks, w if w != 1024 else None)
    qvecs: dict[int, list[list[float]]] = {}
    for w in WIDTHS:
        qvecs[w] = await embed([q for q, _ in queries], w if w != 1024 else None)

    def rank_of(width: int, qi: int) -> list[int]:
        qv = qvecs[width][qi]
        scored = [(cos(qv, cv), ci) for ci, cv in enumerate(embeddings[width])]
        return [ci for _, ci in sorted(scored, key=lambda t: -t[0])]

    reference = [rank_of(1024, qi) for qi in range(len(queries))]

    print(f"{'width':>6} {'recall@3':>10} {'recall@1':>10} {'MRR':>8} {'top5 agree w/ 1024':>22}")
    print("-" * 62)

    results = {}
    for w in WIDTHS:
        r3 = r1 = 0
        mrr = 0.0
        agreements = []
        for qi, (_q, owner) in enumerate(queries):
            order = rank_of(w, qi)
            pos = next((r for r, ci in enumerate(order) if owners[ci] == owner), None)
            if pos is not None:
                mrr += 1.0 / (pos + 1)
                if pos < 1:
                    r1 += 1
                if pos < 3:
                    r3 += 1
            agreements.append(len(set(order[:5]) & set(reference[qi][:5])) / 5)

        n = len(queries)
        results[w] = {
            "recall@3": r3 / n,
            "recall@1": r1 / n,
            "mrr": mrr / n,
            "top5_agreement": statistics.fmean(agreements),
        }
        print(f"{w:>6} {r3/n:>10.3f} {r1/n:>10.3f} {mrr/n:>8.3f} "
              f"{statistics.fmean(agreements):>22.3f}")
    return results


async def main() -> int:
    all_results = {}
    for name, (chunks, owners, queries) in build_corpora().items():
        topics = len(set(owners))
        print(f"\n=== {name}: {len(chunks)} chunks, {topics} topics, "
              f"{len(queries)} queries ===")
        all_results[name] = await evaluate(chunks, owners, queries)

    path = Path(__file__).with_name("embedding_width_bench.json")
    path.write_text(json.dumps(all_results, indent=2))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))