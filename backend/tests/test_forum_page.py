"""Tests for the forum page's markup.

These assert structure that the Python tests cannot reach and that a browser
check is easy to skip: a control that exists but is unreachable, an element a
script writes to that is not on the page, a row that cannot be focused.

Each one here corresponds to something that was actually broken.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "com_website"
FORUM = WEB / "forum.html"


def _src() -> str:
    return FORUM.read_text()


class TestEveryControlIsReachable:
    """The sidebar is `hidden md:flex`.

    Everything that lived inside it — including the only New Topic button and
    the only navigation — disappeared below the md breakpoint. A half-width
    desktop app window therefore had no way to open a topic at all.
    """

    def test_a_mobile_new_topic_button_exists(self):
        assert 'id="new-topic-btn-mobile"' in _src(), (
            "below md the sidebar is display:none, so its New Topic button is "
            "unreachable — a narrow window cannot open a topic"
        )

    def test_it_is_actually_wired(self):
        assert "getElementById('new-topic-btn-mobile').onclick = openModal" in _src()

    def test_a_mobile_nav_exists(self):
        assert 'id="nav-links-mobile"' in _src(), (
            "below md there is otherwise no navigation at all"
        )

    def test_both_navs_are_rendered_not_one_hidden(self):
        src = _src()
        assert "nav-links-mobile" in src and "nav-links'" in src
        # renderNav must write to both; media queries do not render anything.
        render = src[src.index("function renderNav"):src.index("// ---------------- state")]
        assert "nav-links-mobile" in render, "the mobile nav is never populated"

    def test_the_mobile_bar_is_not_inside_the_flex_row(self):
        """As a sibling of <nav> and <main> it becomes a third flex item and
        squeezes the content to zero width."""
        src = _src()
        flex_row = src.index('<div class="flex">')
        mobile = src.index('id="new-topic-btn-mobile"')
        assert mobile < flex_row, (
            "the mobile bar must sit outside the flex row, or it collapses main"
        )


class TestRowsAreAccessible:
    """Rows were <div onclick>, which is a link that cannot be opened, focused
    or middle-clicked."""

    def test_rows_are_anchors_with_a_real_href(self):
        src = _src()
        row = src[src.index("function renderTopicRow"):]
        assert "<a href=\"topic.html?id=" in row, "rows must be real links"
        assert "onclick=\"openTopic(" not in row, "a div+onclick row is not a link"

    def test_the_id_is_encoded(self):
        src = _src()
        row = src[src.index("function renderTopicRow"):]
        assert "encodeURIComponent(t.id)" in row, (
            "an unencoded id breaks the link on any id with a reserved character"
        )

    def test_rows_have_a_visible_focus_ring(self):
        assert "focus-visible:ring" in _src(), "keyboard focus must be visible"

    def test_the_search_box_is_labelled(self):
        assert 'aria-label="Search topics"' in _src()

    def test_the_modal_error_is_announced(self):
        assert 'role="alert"' in _src(), "a failed topic submission is silent otherwise"


class TestReadingComfort:
    """Everything here came out of looking at a screenshot of the real page."""

    def test_timestamps_are_relative(self):
        assert "timeAgo" in _src(), (
            "toLocaleString() gave every row '10/13/2026, 7:57:43 PM' — twenty "
            "characters that all say the same thing"
        )

    def test_the_absolute_date_is_kept_in_the_tooltip(self):
        """Relative time alone loses the exact time; the tooltip keeps both."""
        assert "toLocaleString()" in _src()

    def test_avatars_use_two_letters(self):
        """The cast is Scholar / Sage / Analyst. One-letter initials gave
        'S A S' and 'O S S', indistinguishable at 22px."""
        avatar = _src()[_src().index("function avatar"):]
        avatar = avatar[: avatar.index("\n}\n")]
        assert "slice(0, 2)" in avatar, "avatars must use two-letter initials"

    def test_a_redundant_preview_is_omitted(self):
        """When there is no framing and the question repeats the title, the
        preview line is pure repetition."""
        src = _src()
        assert "${preview ?" in src, "the preview must be conditional"

    def test_zero_stats_are_dimmed(self):
        """Five competing cards gave 'API QUEUED 0' the same weight as the
        topic count."""
        assert "text-neutral-gray/45" in _src()

    def test_loading_uses_a_skeleton_not_text(self):
        assert "function skeleton()" in _src()


class TestSearch:
    def test_every_word_must_match(self):
        assert "every(w => hay.includes(w))" in _src(), (
            "a substring match would make 'rust go caching' match a topic it "
            "has nothing to do with"
        )

    def test_it_is_debounced(self):
        assert "searchTimer" in _src()

    def test_the_result_count_is_shown(self):
        assert 'id="topic-count"' in _src()


class TestAutoRefresh:
    """The page never refreshed, so a thread that opened and deliberated for
    three minutes looked identical to a finished one."""

    def test_it_polls_while_something_is_in_flight(self):
        assert "scheduleAutoRefresh" in _src()
        assert "debating" in _src()

    def test_it_stops_when_the_board_is_quiet(self):
        src = _src()
        fn = src[src.index("function scheduleAutoRefresh"):]
        fn = fn[: fn.index("\n}\n")]
        assert "if (!busy) return" in fn, "polling forever is its own kind of wrong"


class TestScriptWiring:
    @pytest.mark.parametrize(
        "element_id",
        [
            "new-topic-btn",
            "new-topic-btn-mobile",
            "submit-topic",
            "refresh-btn",
            "filter-status",
            "filter-outcome",
            "search",
            "topic-question",
            "topic-modal",
            "cast-picker",
            "cast-grid",
            "stats-bar",
            "topic-list",
            "nav-links",
            "nav-links-mobile",
            "topic-count",
            "cast-hint",
            "provider-chip",
            "forum-chip",
        ],
    )
    def test_every_referenced_element_exists_in_the_markup(self, element_id):
        """getElementById returns null for a missing element and the failure
        surfaces later as a confusing TypeError, not a missing element."""
        assert f'id="{element_id}"' in _src(), f"#{element_id} is referenced but absent"

    def test_no_leftover_click_handler_for_rows(self):
        assert "function openTopic(" not in _src(), (
            "openTopic is dead now that rows are anchors"
        )