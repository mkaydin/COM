"""Markdown rendering must never emit unsanitised HTML.

`topic.html` and `council_chamber.html` render model output and user posts
through `marked` + DOMPurify. Both used to fall **open**:

    const html = window.marked ? marked.parse(src) : escapeHtml(src);
    return window.DOMPurify ? DOMPurify.sanitize(html) : html;

If `marked` loaded and DOMPurify did not — exactly what a partial CDN outage
produces, and what happened on this machine — the second line returned
`marked`'s raw output with nothing sanitising it.

These tests exercise the page's own functions, extracted verbatim, against a
stub DOM. That covers the control flow deterministically; whether DOMPurify
itself strips a given payload is the library's problem, and is verified
separately in the browser.
"""

import json
import re
import subprocess
import textwrap
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "com_website"
PAYLOAD = '<img src=x onerror="window.__pwned=1"> [click](javascript:window.__pwned=2) **bold**'

# page -> (escape helper name, render function name)
TARGETS = {
    "topic.html": ("esc", "md"),
    "council_chamber.html": ("escapeHtml", "renderMarkdown"),
}


def _extract_function(source: str, name: str) -> str:
    """Pull `function name(...) {…}` out of a page, brace-matched."""
    match = re.search(rf"function {re.escape(name)}\s*\(", source)
    assert match, f"{name}() not found"

    start = source.index("{", match.end())
    depth = 0
    for i in range(start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():i + 1]
    raise AssertionError(f"unbalanced braces in {name}()")


def _harness(page: str) -> str:
    esc_name, render_name = TARGETS[page]
    src = (WEB / page).read_text()

    return textwrap.dedent(f"""
        // A minimal stand-in for a browser: assigning textContent to a detached
        // element and reading innerHTML escapes it. That is the behaviour the
        // pages rely on for their fail-closed path.
        globalThis.document = {{
          createElement() {{
            const el = {{ _t: '' }};
            Object.defineProperty(el, 'textContent', {{
              set(v) {{ this._t = v; }}, get() {{ return this._t; }},
            }});
            Object.defineProperty(el, 'innerHTML', {{
              get() {{
                return String(this._t)
                  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
              }},
            }});
            return el;
          }},
        }};

        {_extract_function(src, esc_name)}

        {_extract_function(src, render_name)}

        const PAYLOAD = {json.dumps(PAYLOAD)};
        const results = {{}};

        function scenario(label, marked, purify) {{
          globalThis.window = {{ marked, DOMPurify: purify }};
          globalThis.marked = marked;
          globalThis.DOMPurify = purify;
          try {{
            results[label] = {{ ok: true, html: {render_name}(PAYLOAD) }};
          }} catch (e) {{
            results[label] = {{ ok: false, error: String(e && e.message) }};
          }}
        }}

        // "Both present" uses recognisable stubs so we can tell that the output
        // really came from the sanitiser rather than from a fallback.
        const STUB_MARKED = {{ parse: (s) => 'MARKED_OUTPUT:' + s }};
        const STUB_PURIFY = {{ sanitize: (h) => 'SANITISED:' + h }};

        scenario('both_present', STUB_MARKED, STUB_PURIFY);
        scenario('purify_missing', STUB_MARKED, undefined);
        scenario('marked_missing', undefined, STUB_PURIFY);
        scenario('both_missing', undefined, undefined);

        console.log(JSON.stringify(results));
    """)


def _run(page: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", _harness(page)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestMarkdownFailsClosed:
    def test_both_libraries_present_calls_the_sanitiser(self):
        """Guards against the render function quietly skipping DOMPurify."""
        for page in TARGETS:
            out = _run(page)["both_present"]
            assert out["ok"], f"{page}: {out.get('error')}"
            assert out["html"].startswith("SANITISED:"), (
                f"{page} did not route through DOMPurify: {out['html'][:80]}"
            )

    def test_missing_sanitiser_yields_escaped_text(self):
        """The DOMPurify-missing case is the one that used to leak.

        `marked` is still present here, so this is exactly the partial-outage
        situation: the output must be escaped plain text.
        """
        for page in TARGETS:
            out = _run(page)["purify_missing"]
            assert out["ok"], f"{page}: {out.get('error')}"
            html = out["html"]
            assert "MARKED_OUTPUT" not in html, (
                f"{page} returned marked's raw output with no sanitiser"
            )
            assert "<" not in html and ">" not in html, (
                f"{page} emitted unescaped tags without DOMPurify: {html[:80]}"
            )

    def test_missing_marked_yields_escaped_text(self):
        for page in TARGETS:
            out = _run(page)["marked_missing"]
            assert out["ok"], f"{page}: {out.get('error')}"
            html = out["html"]
            assert "<" not in html and ">" not in html, (
                f"{page} emitted unescaped tags without marked: {html[:80]}"
            )

    def test_both_missing_still_returns_the_text(self):
        """Blank output would hide content, not just unprotect it."""
        for page in TARGETS:
            out = _run(page)["both_missing"]
            assert out["ok"], f"{page}: {out.get('error')}"
            assert "bold" in out["html"], f"{page} lost the content: {out['html'][:80]}"
            assert "<" not in out["html"]

    def test_no_dangerous_markup_survives_any_fallback(self):
        """Covers the degraded paths.

        `both_present` is excluded on purpose: its stub sanitiser is a
        pass-through, so it cannot strip anything. That DOMPurify really does
        neutralise `<img onerror>` and `javascript:` URLs is the library's
        behaviour, verified in a real renderer.

        The payload's words (`onerror`, `javascript:`) still appear in the
        output — as escaped text, which is what should happen. What must not
        appear is an unescaped `<`, because that is what lets a tag form.
        """
        for page in TARGETS:
            results = _run(page)
            for label, out in results.items():
                if label == "both_present":
                    continue
                html = out.get("html", "")
                assert "<" not in html, (
                    f"{page}/{label} emitted an unescaped tag delimiter: {html[:80]}"
                )
                assert "&lt;" in html, (
                    f"{page}/{label} should have escaped the payload: {html[:80]}"
                )