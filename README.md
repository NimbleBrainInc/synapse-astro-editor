# synapse-astro-editor

Natural-language editor for Astro websites. A Synapse app that runs inside NimbleBrain and exposes a chat + live-preview interface for non-technical users to edit their Astro site.

## Architecture

- **MCP server** (Python / FastMCP) — file/git/repo tools, Astro subprocess manager, `ui://` resource handler with asset flattener.
- **Astro runtime** — `astro dev` runs as a child process on `localhost:4321`, never exposed externally. The server fetches rendered pages from it.
- **UI** (React + Vite + Synapse SDK) — chat panel, preview iframe (loads `ui://astro-editor/preview`), publish button. Built as a single-file bundle.
- **Preview delivery** — rendered Astro HTML is flattened (inline CSS, inline JS, data-URL small images) into a single-document `ui://` resource, in-spec with the MCP Apps extension.

```
┌─── Synapse app container ───────────────────────┐
│  FastMCP server                                 │
│    - tools (files/git/preview/publish)          │
│    - ui://astro-editor/main   → UI shell        │
│    - ui://astro-editor/preview → flattened page │
│    - spawns: astro dev (localhost:4321)         │
│                                                 │
│  Workspace: ./workspace/ (cloned repo)          │
└─────────────────────────────────────────────────┘
```

## Status

Prototype. The scaffold runs and serves a placeholder preview. Real flattening, subprocess management, and repo cloning are stubbed — see TODOs in the source.

## Run locally

```bash
# Install Python deps (in this dir)
uv sync

# Install and start UI dev server
cd ui
npm install
npm run dev
```

Open the Vite preview URL (printed by `npm run dev`, usually `http://localhost:5173/__preview`). The Synapse preview host handshakes with the MCP server automatically via the `synapseVite()` plugin.

## Next up

1. Real repo clone + workspace lifecycle on `configure_repo`
2. `astro dev` subprocess spawn + health check
3. Site profile scanner (read astro.config.mjs, content/config.ts)
4. Asset flattener (HTML walker that inlines `<link>`, `<script>`, small `<img>`)
5. Navigation interceptor in the flattened output → postMessage → re-render
