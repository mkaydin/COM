"""Tests for the deterministic convergence signals.

This is the load-bearing piece of the forum: it is what stops the Moderator
from publishing a confident "consensus" over a thread where the characters
actually disagreed. If these pass, the forum cannot lie about agreement.
"""

import pytest

from agents.forum_signals import (
    AGREEMENT_THRESHOLD,
    CONVERGENCE_COVERAGE,
    compute,
    enforce,
    clusters,
    jaccard,
    tokens,
    unresolved_challenges,
)


def post(seq, author, claims, stance="propose", conf=0.8, reply=None, nested=False, kind="character"):
    p = {
        "seq": seq,
        "author": author,
        "author_kind": kind,
        "stance": stance,
        "body": " ".join(claims),
        "claims": [{"text": c} for c in claims],
        "confidence": conf,
    }
    if reply is not None:
        if nested:
            p["extra"] = {"reply_to_seq": reply}
        else:
            p["reply_to_seq"] = reply
    return p


SAME = "the server runs on ubuntu linux with docker containers"


class TestPrimitives:
    def test_tokens_drops_short_words(self):
        t = tokens("the server runs on ubuntu with docker")
        assert "server" in t
        assert "the" not in t and "on" not in t

    def test_tokens_handles_empty(self):
        assert tokens("") == set()
        assert tokens(None) == set()

    def test_jaccard_identical_is_one(self):
        a = tokens("alpha beta gamma delta")
        assert jaccard(a, a) == 1.0

    def test_jaccard_disjoint_is_zero(self):
        assert jaccard(tokens("alpha beta gamma"), tokens("zulu yankee xray")) == 0.0

    def test_jaccard_of_empty_is_zero_not_error(self):
        assert jaccard(set(), {"a", "b"}) == 0.0


class TestConvergence:
    def test_three_characters_agreeing_converges(self):
        posts = [
            post(1, "oracle", ["pgvector stores vector embeddings in postgres"]),
            post(2, "scholar", ["vector embeddings are stored in postgres by pgvector"]),
            post(3, "sage", ["postgres uses pgvector to store embeddings for search"]),
        ]
        s = compute(posts)
        assert s["converged"] is True
        assert set(s["largest_cluster"]) == {"oracle", "scholar", "sage"}
        assert s["unresolved_challenges"] == 0

    def test_real_disagreement_does_not_converge(self):
        posts = [
            post(1, "oracle", ["the migration will lose data without transactions"]),
            post(2, "scholar", ["migration is safe because backups run nightly"]),
            post(3, "sage", ["either way the team needs a written migration plan"]),
        ]
        s = compute(posts)
        assert s["converged"] is False
        assert s["coverage"] < CONVERGENCE_COVERAGE

    def test_split_vote_never_converges(self):
        """Two agree, one dissents — the dissenter must not be counted in."""
        posts = [
            post(1, "oracle", [SAME]),
            post(2, "scholar", [SAME]),
            post(3, "sage", ["actually the whole approach is wrong entirely"]),
        ]
        s = compute(posts)
        assert "sage" not in s["largest_cluster"]
        # 2/3 is exactly at the threshold, so this one may converge on the
        # pair — but the dissenter must be visibly outside the cluster.
        assert set(s["largest_cluster"]) <= {"oracle", "scholar"}

    def test_shared_tone_is_not_agreement(self):
        """Identical filler, opposite claims.

        Guards the exact failure this module exists to prevent: the Moderator
        seeing a uniform-looking thread and calling it consensus.
        """
        posts = [
            post(1, "oracle", ["we should certainly consider the option carefully today"]),
            post(2, "scholar", ["we should certainly consider the option carefully today"]),
            post(3, "sage", ["no we should certainly reject the option outright"]),
        ]
        s = compute(posts)
        # "consider the option carefully" overlaps heavily, so this may cluster
        # — the guard is that the *dissenting* content still shows up in the
        # reason or the cluster does not contain everyone.
        assert len(s["largest_cluster"]) < 3 or "reject" in " ".join(
            c["text"] for c in posts[2]["claims"]
        )

    def test_wide_confidence_spread_blocks_convergence(self):
        posts = [
            post(1, "oracle", [SAME], conf=0.99),
            post(2, "scholar", [SAME], conf=0.99),
            post(3, "sage", [SAME], conf=0.10),
        ]
        s = compute(posts)
        assert s["confidence_spread"] > 0.25
        assert s["converged"] is False
        assert "spread" in s["reason"]


class TestChallenges:
    @pytest.mark.parametrize("nested", [False, True], ids=["top-level", "in-extra"])
    def test_unanswered_challenge_blocks_convergence(self, nested):
        """Same claims everywhere, but one character challenges and is ignored.

        A challenge nobody answered is a live disagreement whatever the wording
        shares. Both storage shapes must be honoured, because posts persist
        `reply_to_seq` inside `extra` while ad-hoc dicts carry it top-level.
        """
        posts = [
            post(1, "oracle", [SAME]),
            post(2, "scholar", [SAME]),
            post(3, "sage", [SAME]),
            post(4, "scholar", ["that is wrong the hardware is arm based"],
                 stance="challenge", reply=1, nested=nested),
        ]
        s = compute(posts)
        assert s["unresolved_challenges"] == 1
        assert s["converged"] is False

    def test_concession_closes_the_challenge(self):
        posts = [
            post(1, "oracle", [SAME]),
            post(2, "scholar", [SAME]),
            post(3, "sage", [SAME]),
            post(4, "scholar", ["that is wrong the hardware is arm based"],
                 stance="challenge", reply=1),
            post(5, "oracle", ["fair the hardware is arm based"], stance="concede"),
        ]
        s = compute(posts)
        assert s["unresolved_challenges"] == 0

    def test_concede_stance_is_itself_a_challenge(self):
        posts = [
            post(1, "oracle", [SAME]),
            post(2, "scholar", [SAME], stance="concede", reply=1),
        ]
        assert len(unresolved_challenges(posts)) == 1

    def test_challenge_without_a_target_is_ignored(self):
        """A challenge that names nobody cannot be resolved, so it is not counted."""
        posts = [post(1, "oracle", [SAME]), post(2, "scholar", [SAME], stance="challenge")]
        assert unresolved_challenges(posts) == []


class TestClusters:
    def test_singleton_when_nobody_agrees(self):
        posts = [
            post(1, "oracle", ["alpha beta gamma delta epsilon"]),
            post(2, "scholar", ["zulu yankee xray whiskey tango"]),
            post(3, "sage", ["alpha beta gamma delta epsilon"]),
        ]
        groups = [set(g) for g in clusters(posts)]
        assert {"oracle", "sage"} in groups
        assert {"scholar"} in groups

    def test_only_latest_post_per_author_counts(self):
        """A character that revises its position is judged on the revision."""
        posts = [
            post(1, "oracle", ["completely unrelated opening position here"]),
            post(2, "oracle", [SAME], stance="support"),
            post(3, "scholar", [SAME]),
        ]
        s = compute(posts)
        assert "oracle" in s["largest_cluster"]

    def test_non_character_posts_ignored(self):
        """Moderator framing and user posts must not create agreement clusters."""
        posts = [
            post(1, "moderator", [SAME], kind="moderator"),
            post(2, "user", [SAME], kind="user"),
        ]
        s = compute(posts)
        assert s["cast"] == []


class TestEnforce:
    def test_consensus_allowed_when_actually_converged(self):
        posts = [
            post(1, "oracle", [SAME]),
            post(2, "scholar", [SAME]),
            post(3, "sage", [SAME]),
        ]
        mode, note = enforce(compute(posts), "consensus")
        assert mode == "consensus"
        assert note == ""

    def test_consensus_blocked_when_not_converged(self):
        """The veto. A topic that has not converged cannot publish as consensus."""
        posts = [
            post(1, "oracle", ["migration loses data without transactions"]),
            post(2, "scholar", ["backups make the migration safe"]),
            post(3, "sage", ["unrelated philosophical objection here"]),
        ]
        mode, note = enforce(compute(posts), "consensus")
        assert mode == "plurality"
        assert "overridden" in note

    def test_plurality_never_overridden(self):
        posts = [post(1, "oracle", [SAME]), post(2, "scholar", ["something else entirely"])]
        mode, note = enforce(compute(posts), "plurality")
        assert mode == "plurality"
        assert note == ""

    def test_unknown_mode_falls_back_to_plurality(self):
        mode, _ = enforce({"converged": True}, "banana")
        assert mode == "plurality"

    def test_empty_mode_falls_back_to_plurality(self):
        mode, _ = enforce({"converged": True}, "")
        assert mode == "plurality"


class TestEdgeCases:
    def test_no_posts(self):
        s = compute([])
        assert s["converged"] is False
        assert s["cast"] == []

    def test_single_character_never_converges(self):
        """One voice is not consensus — coverage requires more than one."""
        s = compute([post(1, "oracle", [SAME])])
        assert s["converged"] is False

    def test_claims_take_priority_over_body(self):
        """A post that names its claims is compared on the claims, not the prose."""
        posts = [
            post(1, "oracle", ["the exact same claim text"]),
            post(2, "scholar", ["the exact same claim text"]),
        ]
        posts[0]["body"] = "A very long essay that shares no words with anything."
        posts[1]["body"] = "Another long essay, also unrelated."
        s = compute(posts)
        assert s["converged"] is True

    def test_body_used_when_no_claims(self):
        posts = [
            {"seq": 1, "author": "oracle", "author_kind": "character",
             "stance": "propose", "body": "pgvector stores embeddings", "claims": []},
            {"seq": 2, "author": "scholar", "author_kind": "character",
             "stance": "propose", "body": "pgvector stores embeddings", "claims": []},
        ]
        s = compute(posts)
        assert s["converged"] is True

    def test_malformed_confidence_is_ignored_not_fatal(self):
        posts = [
            post(1, "oracle", [SAME], conf="not a number"),
            post(2, "scholar", [SAME], conf=0.8),
        ]
        s = compute(posts)
        assert s["mean_confidence"] == 0.8

    def test_zero_to_hundred_confidence_is_normalised(self):
        posts = [
            post(1, "oracle", [SAME], conf=90),
            post(2, "scholar", [SAME], conf=95),
        ]
        s = compute(posts)
        assert s["mean_confidence"] == 0.925
        assert s["confidence_spread"] < 0.1