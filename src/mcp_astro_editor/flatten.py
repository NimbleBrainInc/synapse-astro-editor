"""Flatten a rendered Astro page into a single self-contained HTML document.

Walks the HTML, fetches referenced assets from the running Astro dev server,
and inlines them so the result fits in a single MCP `ui://` resource:
  - <link rel="stylesheet" href="..."> → <style>...</style>
  - <script src="..."> → <script>...</script>
  - <img src="..."> with small payloads → data URLs
  - external (http(s)://) assets → left as-is, must be in resourceDomains

Also injects a tiny navigation script that intercepts internal link clicks and
posts back to the host so the MCP server can re-render the new page.

Prototype scope: a working flattener with conservative defaults. Edge cases
(modulepreload, dynamic imports, srcset, picture, source elements, font
preloads) are TODOs noted inline.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser

# Inline images smaller than this; otherwise leave as a relative URL the
# host can't actually load (TODO: serve big images via declared resourceDomain).
MAX_INLINE_IMAGE_BYTES = 64 * 1024

# Small script injected into every flattened page so internal navigation flows
# back through postMessage to the host (which calls render_preview tool).
NAV_SCRIPT = """
<script>
(function () {
  function isInternal(href) {
    if (!href) return false;
    if (href.startsWith('#')) return false;
    if (href.startsWith('mailto:') || href.startsWith('tel:')) return false;
    try {
      var u = new URL(href, document.baseURI);
      return u.origin === document.location.origin || u.origin === 'null';
    } catch (_) { return href.startsWith('/'); }
  }
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    if (!isInternal(a.getAttribute('href'))) return;
    e.preventDefault();
    var path = new URL(a.href, document.baseURI).pathname;
    window.parent.postMessage({
      jsonrpc: '2.0',
      method: 'ui/navigate',
      params: { path: path }
    }, '*');
  }, true);
})();
</script>
"""


class _LinkExtractor(HTMLParser):
    """Pull stylesheet/script/img references in the order they appear."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, dict[str, str | None]]] = []  # (kind, src, attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "link" and (a.get("rel") or "").lower() == "stylesheet" and a.get("href"):
            self.links.append(("style", a["href"] or "", a))
        elif tag == "script" and a.get("src"):
            self.links.append(("script", a["src"] or "", a))
        elif tag == "img" and a.get("src"):
            self.links.append(("img", a["src"] or "", a))


def _is_relative(url: str) -> bool:
    return not (url.startswith("http://") or url.startswith("https://") or url.startswith("data:"))


async def flatten(
    html: str,
    fetch_asset: Callable[[str], Awaitable[tuple[bytes, str]]],
) -> str:
    """Replace relative <link>, <script>, <img> with inlined equivalents."""
    parser = _LinkExtractor()
    parser.feed(html)

    out = html

    for kind, src, _attrs in parser.links:
        if not _is_relative(src):
            continue  # external — handled via CSP resourceDomains
        try:
            payload, ctype = await fetch_asset(src)
        except Exception as exc:  # pragma: no cover — best-effort flattening
            print(f"[flatten] could not fetch {src}: {exc}")
            continue

        if kind == "style":
            css = payload.decode("utf-8", errors="replace")
            out = _replace_link_stylesheet(out, src, css)
        elif kind == "script":
            js = payload.decode("utf-8", errors="replace")
            out = _replace_script_src(out, src, js)
        elif kind == "img":
            if len(payload) <= MAX_INLINE_IMAGE_BYTES:
                data_url = f"data:{ctype};base64,{base64.b64encode(payload).decode()}"
                out = out.replace(f'src="{src}"', f'src="{data_url}"')
                out = out.replace(f"src='{src}'", f"src='{data_url}'")
            # else TODO: route through declared resourceDomain

    out = _inject_before_body_close(out, NAV_SCRIPT)
    return out


def _replace_link_stylesheet(html: str, href: str, css: str) -> str:
    pattern = re.compile(
        r'<link\b[^>]*\brel=["\']?stylesheet["\']?[^>]*\bhref=["\']'
        + re.escape(href)
        + r'["\'][^>]*>',
        flags=re.IGNORECASE,
    )
    replacement = f"<style>\n{css}\n</style>"
    # Lambda replacement so the engine doesn't interpret \d, \1, \g<...> etc.
    # in the CSS body (Tailwind escapes like .\!w-1\/2 trip plain-string subs).
    new, n = pattern.subn(lambda _m: replacement, html, count=1)
    if n:
        return new
    pattern2 = re.compile(
        r'<link\b[^>]*\bhref=["\']'
        + re.escape(href)
        + r'["\'][^>]*\brel=["\']?stylesheet["\']?[^>]*>',
        flags=re.IGNORECASE,
    )
    return pattern2.sub(lambda _m: replacement, html, count=1)


def _replace_script_src(html: str, src: str, js: str) -> str:
    pattern = re.compile(
        r'<script\b[^>]*\bsrc=["\']' + re.escape(src) + r'["\'][^>]*>\s*</script>',
        flags=re.IGNORECASE,
    )
    replacement = f'<script type="module">\n{js}\n</script>'
    # Same backslash hazard as CSS — JS literals can contain \n, \t, \uXXXX, etc.
    return pattern.sub(lambda _m: replacement, html, count=1)


def _inject_before_body_close(html: str, snippet: str) -> str:
    if "</body>" in html:
        return html.replace("</body>", snippet + "</body>", 1)
    return html + snippet


PLACEHOLDER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Preview not ready</title>
<style>
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--color-background-primary, #fafafa);
    color: var(--color-text-secondary, #6b7280);
    display: flex; align-items: center; justify-content: center;
    height: 100vh; padding: 2rem;
  }
  .card { max-width: 28rem; text-align: center; }
  .card h2 { font-size: 1.1rem; color: var(--color-text-primary, #111827); margin: 0 0 .5rem; }
  .card p { margin: 0; font-size: .9rem; line-height: 1.5; }
  code { background: rgba(0,0,0,.05); padding: 0 .35rem; border-radius: 4px; }
</style>
</head>
<body>
<div class="card">
  <h2>Setting up your site…</h2>
  <p>Cloning the repo and starting Astro. First run can take a minute.</p>
</div>
</body>
</html>
"""
