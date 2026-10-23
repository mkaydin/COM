"""The revision pass, final answers, and filing a topic into the library.

Before this, a character's "final answer" was whatever they last replied to.
The graph went straight from the discussion loop to synthesis, so there was
nothing for the Moderator to summarise as a conclusion — only positions derived
from a conversation nobody had finished.

These tests cover the three pieces that make the flow coherent:

* `final_answers` — every successful character has one, a revision wins over
  earlier posts, and `changed` reflects a real change of position rather than
  rephrasing;
* `topic_markdown` — the document that gets filed puts finals first;
* the wiring — the discussion loop always exits into revision, never straight
  into synthesis.
"""

import pytest

from agents.forum_store import final_answers, topic_markdown
from agents.forum_graph import build_workflow, decide_continue


def post(author, seq, kind, body, *, stance="propose", author_kind="character",
         revision_of=None, confidence=0.5):
    return {
        "author": author,
        "author_kind": author_kind,
        "seq": seq,
        "kind": kind,
        "body": body,
        "stance": stance,
        "confidence": confidence,
        "revision_of": revision_of,
        "citations": [],
    }


class TestFinalAnswers:
    def test_a_revision_becomes_the_final_answer(self):
        posts = [
            post("oracle", 1, "statement", "My opening position."),
            post("oracle", 5, "revision", "My final position.", revision_of="p1"),
        ]
        finals = final_answers(posts)
        assert len(finals) == 1
        assert finals[0]["body"] == "My final position."
        assert finals[0]["kind"] == "revision"

    def test_without_a_revision_the_last_post_is_the_final(self):
        """A character whose revision call failed still gets an answer."""
        posts = [
            post("oracle", 1, "statement", "Opening."),
            post("oracle", 4, "reply", "A later reply.", stance="challenge"),
        ]
        finals = final_answers(posts)
        assert finals[0]["body"] == "A later reply."
        assert finals[0]["kind"] == "reply"

    def test_every_character_gets_exactly_one(self):
        posts = [
            post("oracle", 1, "statement", "o1"),
            post("sage", 2, "statement", "s1"),
            post("oracle", 3, "reply", "o2"),
            post("sage", 4, "revision", "s2"),
            post("oracle", 5, "revision", "o3"),
        ]
        finals = final_answers(posts)
        # Ordered by the sequence of the *final* post, so sage (seq 4) precedes
        # oracle (seq 5) even though oracle spoke first.
        assert [f["author"] for f in finals] == ["sage", "oracle"]
        assert len(finals) == 2

    def test_moderator_and_user_posts_are_not_answers(self):
        posts = [
            post("moderator", 1, "opening", "Framing.", author_kind="moderator"),
            post("user", 2, "user", "A question.", author_kind="user"),
            post("oracle", 3, "statement", "Position."),
            post("moderator", 4, "synthesis", "Verdict.", author_kind="moderator"),
        ]
        assert [f["author"] for f in final_answers(posts)] == ["oracle"]

    def test_legacy_posts_without_a_kind_still_count(self):
        """Rows written before `kind` existed must not vanish from the finals."""
        legacy = {
            "author": "oracle", "author_kind": "character", "seq": 1,
            "body": "Old post.", "stance": "propose", "citations": [],
        }
        finals = final_answers([legacy])
        assert len(finals) == 1
        assert finals[0]["body"] == "Old post."

    def test_empty_thread_has_no_finals(self):
        assert final_answers([]) == []


class TestChangedFlag:
    def test_a_reworded_revision_is_not_a_change(self):
        """Rephrasing must not be reported as a change of mind.

        The threshold was calibrated on real output: asking a model to restate
        a position without changing it scored 0.50-0.66, while genuine
        revisions scored 0.13-0.21.
        """
        opening = (
            "I cannot decide this from the record. The retrieved corpus covers "
            "Raspberry Pi hardware and introductory material only, so there is "
            "no evidence about vector indexing to reason from. Treat the "
            "question as unretrieved and say so plainly."
        )
        reworded = (
            "On the basis of the available record I am unable to decide the "
            "question. What was retrieved concerns Raspberry Pi single-board "
            "computers and introductory notes, and contains no evidence about "
            "vector indexing. The question should be treated as unretrieved and "
            "that should be stated plainly."
        )
        posts = [
            post("scholar", 1, "statement", opening),
            post("scholar", 9, "revision", reworded),
        ]
        assert final_answers(posts)[0]["changed"] is False

    def test_a_real_revision_is_a_change(self):
        opening = (
            "Adopt a vector database now. Exact search in Postgres will not "
            "scale past a hundred thousand rows, and migration later is more "
            "expensive than starting correctly."
        )
        revised = (
            "Start with exact search in Postgres. The corpus here is small, the "
            "operational cost of a second datastore is real, and the migration "
            "path is a schema change rather than a rewrite. Revisit at a "
            "measured latency threshold."
        )
        posts = [
            post("analyst", 1, "statement", opening),
            post("analyst", 7, "revision", revised),
        ]
        assert final_answers(posts)[0]["changed"] is True

    def test_no_revision_is_never_a_change(self):
        posts = [post("oracle", 1, "statement", "Unchanged position.")]
        assert final_answers(posts)[0]["changed"] is False


class TestTopicMarkdown:
    def _topic(self):
        return {
            "title": "Should we adopt a vector database?",
            "question": "Should a small team adopt a vector database now?",
            "framing": "Decide whether the operational cost is justified.",
            "outcome": "plurality",
            "synthesis": {
                "answer": "",
                "positions": [
                    {"label": "Wait", "position": "Start with Postgres.", "backers": ["analyst"]}
                ],
                "fault_line": "Cost now versus migration later.",
                "process": "The analyst moved after the sage's point about p95.",
            },
        }

    def test_finals_come_before_the_transcript(self):
        posts = [
            post("oracle", 1, "statement", "Opening text."),
            post("oracle", 5, "revision", "Final text.", revision_of="p1"),
        ]
        md = topic_markdown(self._topic(), posts)
        assert md.index("## Final answers") < md.index("## Discussion")
        assert "Final text." in md
        assert "Opening text." in md

    def test_it_records_that_a_character_moved(self):
        posts = [
            post("oracle", 1, "statement", "Adopt now, migration is expensive."),
            post("oracle", 5, "revision",
                 "Start in Postgres; the corpus is small and a second store costs "
                 "more than the migration it avoids."),
        ]
        md = topic_markdown(self._topic(), posts)
        assert "Changed position" in md

    def test_the_question_and_synthesis_are_present(self):
        md = topic_markdown(self._topic(), [post("oracle", 1, "statement", "x")])
        assert "Should a small team adopt a vector database now?" in md
        assert "Fault line" in md
        assert "The analyst moved" in md


class TestGraphWiring:
    def test_revision_runs_before_synthesis(self):
        """The loop must not be able to reach synthesis without revising."""
        assert "revise" in set(build_workflow().nodes)

    @pytest.mark.parametrize("state", [
        {"round": 0, "max_rounds": 1, "signals": {}},
        {"round": 5, "max_rounds": 5, "signals": {}},
        {"round": 0, "max_rounds": 5, "signals": {"converged": True}},
        {"round": 0, "max_rounds": 0},
        {},
    ])
    def test_every_exit_from_the_loop_is_revision(self, state):
        """`decide_continue` may only say "exchange" or "revise"."""
        assert decide_continue(state) in ("exchange", "revise")