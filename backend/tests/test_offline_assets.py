"""No page may load a stylesheet or font from the network.

This is a desktop app: the backend, the database and the models are all local,
so "works offline" is the normal case rather than an edge case. Loading
Tailwind from `cdn.tailwindcss.com` and the fonts from `fonts.googleapis.com`
meant a machine with no network rendered every page completely unstyled — and
that is not hypothetical, it was observed during the audit as
`Refused to apply style … MIME type 'text/html'`.

The verification that matters is not "the tag is absent" but "the stylesheet the
tag pointed at now exists and is complete", so both are checked here.
"""

import re
from pathlib import Path

# Resolved from this file, not the working directory: the suite is run both
# from `backend/` and from the repo root, and a relative path silently points
# at nothing in the second case.
WEB = Path(__file__).resolve().parents[2] / "com_website"

CDN_HOSTS = ("cdn.tailwindcss.com", "fonts.googleapis.com", "cdn.jsdelivr.net",
             "unpkg.com", "fonts.gstatic.com")


def _pages():
    return sorted(p for p in WEB.glob("*.html"))


class TestNoNetworkAssets:
    def test_no_page_references_a_cdn(self):
        offenders = []
        for page in _pages():
            text = page.read_text()
            for host in CDN_HOSTS:
                # Only markup that actually fetches counts; a comment mentioning
                # a CDN is not a request.
                for line in text.splitlines():
                    if host in line and ("<link" in line or "<script" in line):
                        offenders.append(f"{page.name}: {line.strip()[:90]}")
        assert not offenders, "pages load assets from the network:\n" + "\n".join(offenders)

    def test_third_party_libraries_are_vendored(self):
        """`marked` and DOMPurify came from jsDelivr.

        DOMPurify is not cosmetic — it is the sanitiser for rendered model
        output and user posts.
        """
        for page in _pages():
            for line in page.read_text().splitlines():
                if "marked.min.js" in line or "purify.min.js" in line:
                    assert 'src="vendor/' in line, (
                        f"{page.name} still loads a library from the network: "
                        f"{line.strip()[:90]}"
                    )
        for lib in ("marked.min.js", "purify.min.js"):
            assert (WEB / "vendor" / lib).exists(), (
                f"com_website/vendor/{lib} is missing — run "
                "`node desktop/vendor-libs.js`"
            )

    def test_markdown_fallback_fails_closed(self):
        """A missing sanitiser must yield escaped text, never raw HTML.

        The old code was `window.DOMPurify ? DOMPurify.sanitize(html) : html`,
        which returned `marked`'s raw output whenever DOMPurify failed to load
        — precisely what a partial CDN outage produces.
        """
        for name in ("council_chamber.html", "topic.html"):
            src = (WEB / name).read_text()
            assert ": html;" not in src, (
                f"{name} returns unsanitised HTML when DOMPurify is missing"
            )
            assert "if (!window.marked || !window.DOMPurify)" in src, (
                f"{name} must bail to escaped text when either library is absent"
            )

    def test_no_inline_tailwind_config_left(self):
        """It would throw `ReferenceError: tailwind is not defined`.

        The configs moved to `com_website/tailwind.config.js`, which only the
        build reads. Leaving the inline script in place throws on every page.
        """
        offenders = [p.name for p in _pages() if "tailwind.config" in p.read_text()]
        assert not offenders, (
            f"inline tailwind.config still in {offenders} — it references the "
            "`tailwind` global the CDN used to define, and now throws on load"
        )

    def test_generated_stylesheet_is_always_valid_js(self):
        """The config file is CommonJS and must not be loaded by a page."""
        cfg = WEB / "tailwind.config.js"
        assert cfg.exists(), "run: node desktop/extract-configs.js"
        text = cfg.read_text()
        assert "module.exports" in text
        pages = [p.name for p in _pages() if "tailwind.config.js" in p.read_text()]
        assert not pages, f"a browser is loading the build config: {pages}"


class TestGeneratedAssetsExist:
    def test_every_page_has_its_stylesheet(self):
        missing = [
            p.name for p in _pages()
            if not (WEB / f"tailwind.{p.stem}.css").exists()
        ]
        assert not missing, (
            f"no built stylesheet for {missing} — run `npm run build:web` in desktop/"
        )

    def test_stylesheets_are_linked(self):
        for page in _pages():
            text = page.read_text()
            expected = f'tailwind.{page.stem}.css'
            assert expected in text, f"{page.name} does not link {expected}"
            assert 'href="fonts.css"' in text, f"{page.name} does not link fonts.css"

    def test_stylesheets_are_not_empty(self):
        """A zero-byte stylesheet renders unstyled and still looks linked."""
        for css in WEB.glob("tailwind.*.css"):
            assert css.stat().st_size > 5000, f"{css.name} is suspiciously small"

    def test_fonts_are_local_and_present(self):
        fonts_css = WEB / "fonts.css"
        assert fonts_css.exists(), "run: node desktop/fetch-fonts.js"
        text = fonts_css.read_text()

        remote = [u for u in re.findall(r"url\((https?://[^)]+)\)", text)]
        assert not remote, f"fonts.css still points at the network: {remote[:3]}"

        font_dir = WEB / "fonts"
        assert font_dir.is_dir(), "com_website/fonts/ is missing"
        referenced = set(re.findall(r"url\(fonts/([^)]+)\)", text))
        on_disk = {p.name for p in font_dir.glob("*")}
        missing = referenced - on_disk
        assert not missing, f"fonts.css references files that do not exist: {sorted(missing)}"

    def test_all_three_families_are_present(self):
        text = (WEB / "fonts.css").read_text()
        for family in ("VT323", "Be Vietnam Pro", "Material Symbols"):
            assert family in text, f"{family} is not declared in fonts.css"


class TestPerPageConfigsArePreserved:
    """One stylesheet per page, because the pages do not share a theme."""

    def test_every_page_has_a_config_entry(self):
        cfg = (WEB / "tailwind.config.js").read_text()
        for page in _pages():
            assert f"\n  {page.stem}:" in cfg, (
                f"{page.name} has no entry in tailwind.config.js; it would be "
                "skipped by the build and render unstyled"
            )

    def test_the_typography_differences_are_still_there(self):
        """The build preserves each page's own sizes; this pins that.

        `agent_profiles` and `history` deliberately use a smaller `pixel-body`
        than the rest. A single shared config would silently restyle them, so
        this asserts the difference still exists in the generated file.
        """
        small = (WEB / "tailwind.history.css").read_text()
        normal = (WEB / "tailwind.forum.css").read_text()
        assert small != normal, (
            "history.html and forum.html now compile to the same CSS — the "
            "per-page font sizes were lost"
        )