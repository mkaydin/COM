"""The models page must not render the whole catalogue into the DOM.

A native `<select>` per character listed every model the provider serves — 612
for the CLI catalogue — which came to **3,649 `<option>` elements** and ~117,000
characters of body text, against ~600 for every other page in the app. It was
also unusable: a 600-item dropdown cannot be searched.

These pin the shape of the fix rather than its styling. The rendering itself is
verified in Electron (see the measurements in the commit message); what is
testable here without a browser is that the page does not go back to
materialising one option per model.
"""

import re
from pathlib import Path

# Resolved from this file, not the working directory — see test_offline_assets.
PAGE = Path(__file__).resolve().parents[2] / "com_website" / "models.html"


def _src() -> str:
    return PAGE.read_text()


def _code() -> str:
    """The page with HTML comments and JS comments removed.

    These assertions are about what the page *does*, and several of the strings
    they look for appear in the comments explaining the bug they prevent. The
    first version of this file matched its own comment block and failed.
    """
    text = _src()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return text


class TestNoPerModelOptions:
    def test_no_native_model_select(self):
        """The character rows must not be a <select> of models."""
        src = _code()
        assert 'data-field="model"' not in src, (
            "a <select data-field=\"model\"> renders one <option> per model — "
            "that was the 3,649-option regression"
        )

    def test_the_chat_picker_never_builds_options(self):
        """Only the small, bounded pickers may use <option>.

        The provider list has 3 entries and the embedding backend 4, and the
        embedding *datalist* is limited to embedding models (8 for the CLI
        catalogue) — all fine. The chat catalogue is 605 models across six
        characters, and that is what must not be materialised.
        """
        src = _code()

        def body(start, end=None):
            i = src.index(start)
            j = src.index(end, i) if end else len(src)
            return src[i:j]

        assert "<option" not in body("function renderModelList", "function pickModel")
        assert "<option" not in body("box.innerHTML = res.roles.map", "providerOptions")

    def test_embedding_datalist_stays_bounded(self):
        """The datelist is allowed options, but only embedding models."""
        src = _code()
        assert "embedModels(backend)" in src
        assert ".filter((m) => m.embedding)" in src or "embedModels" in src

    def test_the_picker_renders_only_a_window(self):
        src = _code()
        assert "MATCH_LIMIT" in src, (
            "the listbox must cap how many models it renders, or it just "
            "moves the bloat from <option> to <li>"
        )
        assert "slice(0, MATCH_LIMIT)" in src


class TestPickerBehaviour:
    def test_searching_filters(self):
        src = _code()
        assert "renderModelList" in src
        assert "toLowerCase().includes(needle)" in src

    def test_matching_prefers_the_routed_id(self):
        """A bare tag must not highlight the wrong provider route.

        `longcat-2.5-preview-free` is in the catalogue twice, as
        `opencode/…` and `opencode-go/…`. The backend routes a bare tag to
        `opencode/` (see `llm.cli_provider.to_cli_model`), so that is the one
        the page has to mark as current — otherwise opening the page looks like
        it re-points the character.
        """
        src = _code()
        assert "currentModelId" in src
        assert "`opencode/${bare}`" in src, (
            "the fallback must mirror to_cli_model's opencode/ prefix"
        )

    def test_current_model_is_always_shown(self):
        """A pinned model the provider does not list must stay selectable."""
        src = _code()
        assert "current (not listed)" in src

    def test_embedders_stay_out_of_the_chat_picker(self):
        """The worst state this page can land in."""
        src = _code()
        assert "chatModels" in src
        assert "!m.embedding" in src

    def test_each_row_still_reports_its_current_model(self):
        """The chosen model is now behind a filter, so it is stated in text."""
        src = _code()
        assert 'on <b class="text-parchment">' in src