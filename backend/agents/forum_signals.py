"""Deterministic convergence detection.

The Moderator decides what a thread *means*, but it must not be the thing that
decides whether the Council *agreed*. Agreement is measurable: two characters
whose claims use substantially the same content words, with no unresolved
challenge between them, are agreeing — regardless of how the model describes
it afterwards.

So this module computes the signals, the graph reads them, and the Moderator is
handed the result as a constraint. The Moderator may still override, but it
cannot silently pretend five positions collapsed into one when the text says
otherwise — `test_forum_signals.py` pins that.

All functions are pure and take plain dicts, so they are trivially testable.
"""

from typing import Iterable, Optional

# --- thresholds (tuned against short character posts, not essays) --------------------

#: Jaccard overlap of content words between two characters' claim sets at or
#: above which they are treated as agreeing.
AGREEMENT_THRESHOLD = 0.35

#: Fraction of the cast that must sit in one agreement cluster before the
#: thread counts as converged.
CONVERGENCE_COVERAGE = 2 / 3

#: Consensus needs at least this many characters agreeing. One voice agreeing
#: with itself is 100% coverage and means nothing — without this floor a lone
#: successful statement would publish itself as a unanimous verdict.
MIN_CONVERGENCE_CAST = 2

#: Mean confidence spread (max - min) across the cast above which the thread is
#: treated as unsettled even if the wording agrees.
CONVERGENCE_SPREAD = 0.25

#: Content words shorter than this are dropped — they are noise, not signal.
MIN_TOKEN_LEN = 4

#: Stances that put a claim in dispute.
#:
#: A concession is the opposite — it resolves one — and a question is a
#: challenge even when politely phrased.
#:
#: This was ("challenge", "concede"), which was wrong in three ways at once: a
#: concession marked the post it *agreed* with as unresolved, a question did
#: not count at all, and a challenge that was conceded to still reported
#: unresolved because the concession itself was read as a new challenge. The
#: effect was that threads looked more divided than they were, which feeds the
#: Moderator a false picture.
CHALLENGE_STANCES = ("challenge", "question")


def tokens(text: str) -> set[str]:
    """Lowercase content-word set for overlap comparison.

    Mirrors `agents/moderator._tokens` so the two agreement computations stay
    consistent; kept separate because this one must run without the moderator
    module's settings import.
    """
    cleaned = "".join(ch if (ch.isalnum() or ch in "-'") else " " for ch in (text or "").lower())
    return {t for t in cleaned.split() if len(t) > MIN_TOKEN_LEN}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b) or 1
    return len(a & b) / union


def claim_texts(post: dict) -> str:
    """The text a post actually asserts — claims if it made any, else the body.

    Using claims rather than the whole body matters: a post that names its
    claims is far more comparable than one that argues for six paragraphs.
    """
    claims = post.get("claims") or []
    texts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in claims]
    texts = [t for t in texts if t and t.strip()]
    if texts:
        return " ".join(texts)
    return (post.get("body") or post.get("verdict") or "").strip()


def _norm_conf(v) -> Optional[float]:
    """Normalize confidence to 0..1 — some schemas emit 0..100."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f > 1.5:
        f /= 100.0
    return max(0.0, min(1.0, f))


def _latest_by_author(posts: Iterable[dict]) -> dict[str, dict]:
    """Last post per author — later rounds revise earlier positions."""
    latest: dict[str, dict] = {}
    for p in posts:
        author = p.get("author")
        if not author:
            continue
        if author not in latest or (p.get("seq") or 0) >= (latest[author].get("seq") or 0):
            latest[author] = p
    return latest


def reply_seq(post: dict) -> Optional[int]:
    """Which post this one answers.

    Read from both places it can appear: `extra["reply_to_seq"]` is where
    `forum_store.append_post` persists it, and a bare top-level key is what
    ad-hoc dicts (and tests) tend to carry.
    """
    extra = post.get("extra") or {}
    return post.get("reply_to_seq", extra.get("reply_to_seq"))


def unresolved_challenges(posts: Iterable[dict]) -> list[dict]:
    """Challenge posts that were never answered with a concession.

    A challenge nobody responded to is a live disagreement, whatever the
    wording of the two positions happens to share.
    """
    posts = list(posts)
    challenged = set()
    for p in posts:
        if p.get("stance") in CHALLENGE_STANCES:
            target = reply_seq(p)
            if target is not None:
                challenged.add(target)

    out = []
    for p in posts:
        if p.get("seq") in challenged:
            # Look for a later concession from the challenged author.
            author = p.get("author")
            conceded = any(
                q.get("author") == author
                and q.get("stance") == "concede"
                and (q.get("seq") or 0) > (p.get("seq") or 0)
                for q in posts
            )
            if not conceded:
                out.append(p)
    return out


def next_round_speakers(
    posts: Iterable[dict], cast: Iterable[str], *, minimum: int = 1
) -> list[str]:
    """Who still has a reason to speak in another round?

    Everyone speaks in the first round. After that, only the characters whose
    claim is still in dispute — the ones who were challenged or questioned and
    have not conceded. Everyone else has already said their piece, and inviting
    them again costs a full model call to produce a restatement.

    Returns [] when nobody qualifies: the round would be pure cost, and the
    caller should move on to the revision pass. `minimum` exists because a
    round with a single speaker is still worth running — that one character owes
    an answer — but a caller can raise it to demand an actual exchange.
    """
    invited = [c for c in (cast or []) if c]
    if not invited:
        return []

    owed = {
        p.get("author")
        for p in unresolved_challenges(posts)
        if p.get("author") in invited
    }
    # Preserve the cast's speaking order rather than the order in which the
    # challenges happened to appear.
    chosen = [c for c in invited if c in owed]
    return chosen if len(chosen) >= minimum else []


def agreement_matrix(posts: Iterable[dict]) -> dict[tuple[str, str], float]:
    """Pairwise Jaccard over each author's final claim text."""
    latest = _latest_by_author(posts)
    texts = {a: tokens(claim_texts(p)) for a, p in latest.items()}
    matrix: dict[tuple[str, str], float] = {}
    authors = sorted(texts)
    for i, a in enumerate(authors):
        for b in authors[i + 1:]:
            matrix[(a, b)] = round(jaccard(texts[a], texts[b]), 3)
    return matrix


def clusters(posts: Iterable[dict], threshold: float = AGREEMENT_THRESHOLD) -> list[list[str]]:
    """Group characters into agreement clusters.

    Union-find over pairs whose claims overlap at or above `threshold`. A
    character nobody agrees with forms its own singleton cluster — which is
    exactly what a thread with a genuine dissenter should look like.
    """
    latest = _latest_by_author(posts)
    authors = sorted(latest)
    parent = {a: a for a in authors}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    texts = {a: tokens(claim_texts(p)) for a, p in latest.items()}
    for i, a in enumerate(authors):
        for b in authors[i + 1:]:
            if jaccard(texts[a], texts[b]) >= threshold:
                union(a, b)

    groups: dict[str, list[str]] = {}
    for a in authors:
        groups.setdefault(find(a), []).append(a)
    return [sorted(g) for g in groups.values()]


def compute(posts: Iterable[dict]) -> dict:
    """The full signal block.

    Returned verbatim to the Moderator and to the UI, so a disagreement that
    the model decided to call "consensus" is still visible to the user.
    """
    posts = list(posts)
    character_posts = [p for p in posts if p.get("author_kind") == "character"]
    if not character_posts:
        return {
            "cast": [],
            "clusters": [],
            "largest_cluster": [],
            "coverage": 0.0,
            "confidence_spread": 0.0,
            "unresolved_challenges": 0,
            "converged": False,
            "pairwise": {},
            "reason": "no character posts yet",
        }

    latest = _latest_by_author(character_posts)
    cluster_list = clusters(character_posts)
    largest = max(cluster_list, key=len) if cluster_list else []
    matrix = agreement_matrix(character_posts)

    confs = [_norm_conf(p.get("confidence")) for p in latest.values()]
    confs = [c for c in confs if c is not None]
    spread = (max(confs) - min(confs)) if len(confs) >= 2 else 0.0

    challenges = unresolved_challenges(character_posts)
    coverage = len(largest) / len(latest) if latest else 0.0

    reasons = []
    if len(latest) < MIN_CONVERGENCE_CAST:
        reasons.append(
            f"only {len(latest)} character spoke; consensus needs at least "
            f"{MIN_CONVERGENCE_CAST}"
        )
    if coverage < CONVERGENCE_COVERAGE:
        reasons.append(
            f"largest agreement cluster covers {len(largest)}/{len(latest)} "
            f"characters, below the {CONVERGENCE_COVERAGE:.0%} threshold"
        )
    if spread > CONVERGENCE_SPREAD:
        reasons.append(f"confidence spread {spread:.2f} exceeds {CONVERGENCE_SPREAD}")
    if challenges:
        reasons.append(f"{len(challenges)} challenge(s) never answered with a concession")

    return {
        "cast": sorted(latest),
        "clusters": cluster_list,
        "largest_cluster": largest,
        "coverage": round(coverage, 3),
        "confidence_spread": round(spread, 3),
        "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        "unresolved_challenges": len(challenges),
        "pairwise": {f"{a}|{b}": v for (a, b), v in matrix.items()},
        "converged": not reasons,
        "reason": "; ".join(reasons) or "characters converged on substantively the same claim",
        "thresholds": {
            "agreement": AGREEMENT_THRESHOLD,
            "coverage": CONVERGENCE_COVERAGE,
            "spread": CONVERGENCE_SPREAD,
            "min_cast": MIN_CONVERGENCE_CAST,
        },
    }


def enforce(signals: dict, claimed_mode: str) -> tuple[str, str]:
    """Reconcile the Moderator's chosen mode with the evidence.

    Returns `(mode, note)`. A thread that provably has not converged cannot be
    published as consensus — that is the one override the model does not get,
    because it is the failure mode a forum cannot afford (a confident-sounding
    synthesis over an actual disagreement).
    """
    mode = (claimed_mode or "plurality").strip().lower()
    if mode not in ("consensus", "plurality"):
        mode = "plurality"

    if signals.get("converged"):
        return mode, ""

    if mode == "consensus":
        return "plurality", (
            "overridden: the Moderator claimed consensus but the thread has not "
            f"converged ({signals.get('reason')})"
        )
    return mode, ""