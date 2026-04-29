# synapse-astro-editor

[![mpak](https://img.shields.io/badge/mpak-registry-blue)](https://mpak.dev/packages/@nimblebraininc/synapse-astro-editor?utm_source=github&utm_medium=readme&utm_campaign=synapse-astro-editor)
[![NimbleBrain](https://img.shields.io/badge/NimbleBrain-nimblebrain.ai-purple)](https://nimblebrain.ai?utm_source=github&utm_medium=readme&utm_campaign=synapse-astro-editor)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2)](https://nimblebrain.ai/discord?utm_source=github&utm_medium=readme&utm_campaign=synapse-astro-editor)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Natural-language editor for [Astro](https://astro.build) websites. Point the agent at a GitHub repo; chat drives every edit (text, JSX, blog posts, image uploads). The bundle clones the repo, runs `astro build` + `astro preview`, serves the live site through a same-origin proxy, auto-commits each edit to a draft branch, and publishes by squash-merging into the base branch.

Built for non-technical site owners — marketers, founders, content leads — who want to update a real Astro site without opening an editor or learning git.

**[View on mpak registry](https://mpak.dev/packages/@nimblebraininc/synapse-astro-editor?utm_source=github&utm_medium=readme&utm_campaign=synapse-astro-editor)** | **Built by [NimbleBrain](https://nimblebrain.ai?utm_source=github&utm_medium=readme&utm_campaign=synapse-astro-editor)**

## What it does

- **Live preview, side by side with chat.** The Astro site renders inside the editor pane, scaled to fit. Edits trigger a rebuild (~3-8s on warm cache); the iframe reloads on the same page the user was viewing.
- **Page-aware editing.** The currently-viewed route is pushed to the agent's per-turn context, so "change the headline on this page" works without asking which page.
- **Minimal-edit discipline.** A baked-in skill teaches the agent to change *only* the text, preserving className, animation wrappers, and surrounding JSX — no accidental structural rewrites.
- **Content collections, first-class.** The site profile surfaces every content collection's schema source (`src/content/config.ts`), so the agent writes correct frontmatter on the first try.
- **Drag-drop image upload.** Drop an image on the editor pane; it lands in `public/uploads/` and the agent gets back a `/uploads/<file>` URL ready to drop into markdown or JSX.
- **One-click publish with confirmation.** Click Publish → modal lists the changed files and target branch → confirm to squash-merge to `main` (or open a PR, if configured).
- **Per-file revert + stack-pop undo.** The pending dropdown shows the *net* set of changed files (one row per file, regardless of how many commits hit it); per-file revert uses `git checkout` / `git rm` against the base branch. Undo pops the top commit off the draft so consecutive Undo walks down the stack instead of layering revert commits.
- **Build error visibility.** Failed builds surface a banner with the Astro error and a one-click revert; the iframe stays on the last successful build.

## Architecture

```
┌─────────────────── synapse-astro-editor bundle ──────────────────┐
│                                                                  │
│  FastMCP server (Python)                                         │
│    Tools — files (read/write/edit/multi-edit/delete/grep/list),  │
│            git (commit/revert/publish), site_profile, boot,      │
│            upload_asset, get_publish_preview                     │
│                                                                  │
│    Resources — ui://astro-editor/main      (the editor shell)    │
│                ui://astro-editor/settings  (config UI)           │
│                skill://astro-editor/usage  (editing discipline)  │
│                                                                  │
│    Subprocess — astro build + astro preview on 127.0.0.1:4321    │
│                                                                  │
│  UI (React + Vite + @nimblebrain/synapse SDK)                    │
│    Editor shell with header, pending dropdown, preview pane      │
│    ScaledPreview iframe → platform's same-origin http-proxy      │
│    → bundle's loopback `astro preview` server                    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

The platform exposes `_meta["ai.nimblebrain/http-proxy"]` so the iframe can frame the bundle's loopback server through a same-origin URL — no externally-exposed ports, no CORS gymnastics. See [the http-proxy docs](https://docs.nimblebrain.ai/apps/http-proxy) for the trust model.

## Configuration (`user_config`)

Set when you install the bundle into a workspace:

| Field | Required | Default | Description |
|---|---|---|---|
| `github_repo_url` | Yes | — | HTTPS URL of the Astro repo (`https://github.com/<owner>/<repo>`) |
| `github_token` | Yes | — | Fine-grained PAT with **Contents: Read & Write** (and **Pull requests: Read & Write** if `publish_mode = pr`) |
| `draft_branch` | No | `astro-editor/draft` | Branch the editor commits to |
| `base_branch` | No | `main` | Production branch that drafts target |
| `publish_mode` | No | `ship` | `ship` = squash-merge draft into base and push; `pr` = push draft and open a PR |

The token is marked `sensitive: true` and never appears in logs. The bundle includes a token-scrubber that redacts PAT-shaped strings from any error message before it reaches the user.

## Quick start (mpak)

Install via [mpak](https://mpak.dev) into your NimbleBrain workspace:

```bash
mpak install @nimblebraininc/synapse-astro-editor
```

Configure the workspace:

```bash
mpak config set @nimblebraininc/synapse-astro-editor github_repo_url=https://github.com/you/your-site
mpak config set @nimblebraininc/synapse-astro-editor github_token=<github-pat>
```

Open the **Astro Editor** sidebar entry in your NimbleBrain workspace. The bundle clones the repo, installs `npm` deps, runs `astro build` + `astro preview`, and surfaces the live site in the preview pane.

## Local development

This bundle is one of the more involved ones to develop on because the agent loop, the bundle subprocess, the http-proxy, and the iframe all need to be running at the same time.

```bash
make install       # uv sync + cd ui && npm install
make dev           # Vite dev server (UI hot-reload)
make build-ui      # production single-file UI bundle
make check         # ruff + ty
make bundle        # full .mcpb (includes deps/ + ui/dist/)
```

The bundle's `make dev` only runs the UI. To test against a real platform with the http-proxy primitive wired up, install this bundle into a [NimbleBrain](https://nimblebrain.ai) workspace and `uv pip install --target ./deps .` after every Python change (the platform spawns a long-lived subprocess that imports from `deps/`, not `src/`; restart the platform to pick up new code).

## Per-customer skills

The bundle's bundled `SKILL.md` is intentionally generic — editing discipline, page-context usage, content-collection workflow. Customer-specific guidance (brand voice, blog structure, "where things live") belongs in a **workspace-scoped** skill that the platform's skill loader picks up at runtime.

## Security notes

This bundle clones a GitHub repository, runs `git`, `npm`, and `astro` as subprocesses, and forwards browser requests to a loopback HTTP server through the platform's [http-proxy primitive](https://docs.nimblebrain.ai/apps/http-proxy). Treat the bundle the same way you'd treat installing a CLI: the operator vouches for the code.

The platform's defenses (loopback-only target, credential stripping on forwarded requests, response-side `Set-Cookie` / CSP / X-Frame-Options stripping, per-workspace `allowHttpProxy` kill switch) are documented in the http-proxy trust model. The bundle additionally:

- Stores the GitHub PAT only in process memory; nothing is written to disk.
- Uses `git -c http.extraheader` so the token never appears in process arguments.
- Scrubs PAT-shaped strings from any error message surfaced to the user.

## Releasing

```bash
make bump VERSION=0.2.0
git add -A && git commit -m "Bump version to 0.2.0"
git tag v0.2.0 && git push origin main v0.2.0
gh release create v0.2.0 --title "v0.2.0" --notes "..."
```

The release workflow (`.github/workflows/release.yml`) builds multi-arch `.mcpb` bundles and uploads them to the GitHub release. mpak indexes from there.

## Credits

Built by [NimbleBrain Inc.](https://nimblebrain.ai) on:

- **[Astro](https://astro.build)** — the website framework being edited
- **[FastMCP](https://github.com/jlowin/fastmcp)** — the Python MCP server framework
- **[@nimblebrain/synapse](https://www.npmjs.com/package/@nimblebrain/synapse)** — widget-side SDK with `useTheme` / `useCallTool` / `useDataSync` / `useVisibleState` hooks
- **[mpak](https://mpak.dev?utm_source=github&utm_medium=readme&utm_campaign=synapse-astro-editor)** — MCP bundle registry where releases are published
