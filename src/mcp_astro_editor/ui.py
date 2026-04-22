"""UI resource loaders.

Two ui:// resources:
  - ui://astro-editor/main    → the editor shell (chat + preview iframe)
  - ui://astro-editor/preview → the flattened Astro page (or placeholder)

The shell is a Vite-built single-file React bundle. The preview is generated on
demand by the flattener.
"""

from __future__ import annotations

from pathlib import Path

from . import flatten
from .state import SESSION

_UI_DIR = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"


def load_main_ui() -> str:
    built = _UI_DIR / "index.html"
    if built.exists():
        return built.read_text()
    return _MAIN_FALLBACK


def load_preview() -> str:
    """Return the most recently rendered & flattened page, or a placeholder."""
    last = SESSION.last_preview_path
    cached = SESSION.rendered_pages.get(last)
    return cached or flatten.PLACEHOLDER_HTML


# Minimal fallback for the editor shell — used if the UI bundle hasn't been
# built. Renders a friendly "run npm run build" message.
_MAIN_FALLBACK = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Astro Editor</title>
<style>
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--color-background-primary, #fff);
    color: var(--color-text-primary, #111827);
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 2rem;
  }
  .card { max-width: 32rem; }
  h1 { margin: 0 0 .75rem; font-size: 1.25rem; }
  p { margin: 0 0 .5rem; line-height: 1.5; }
  code { background: rgba(0,0,0,.06); padding: .1rem .35rem; border-radius: 4px; font-size: .85rem; }
</style>
</head>
<body>
<div class="card">
  <h1>Astro Editor — UI not built</h1>
  <p>Run <code>cd ui &amp;&amp; npm install &amp;&amp; npm run build</code> to build the editor shell.</p>
  <p>For development with HMR, run <code>cd ui &amp;&amp; npm run dev</code> and open <code>/__preview</code>.</p>
</div>
</body>
</html>
"""
