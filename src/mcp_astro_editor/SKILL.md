# Astro Editor

How to edit an Astro website on behalf of a non-technical user.

## Critical: Edit minimally

The user usually wants the smallest change that satisfies their request. They expect existing styling, animations, and structure to survive.

- "Change the header to X" → change ONLY the visible text. Preserve `className`, `<motion.span>` wrappers, orange highlights, `<br>` break points, and surrounding JSX.
- Reach for `edit_file` with the smallest unique `old_string` that covers what changes. If you find yourself replacing a 20-line block to swap five words, the edit is too big — narrow it.
- Don't reformat surrounding code, change unrelated whitespace, or "improve" code that wasn't part of the request. Edits should read as one focused diff in the pending dropdown.

If the user explicitly asks for a structural change ("simplify the hero", "remove the animation"), then go bigger. The default is minimal.

## Use the page the user is looking at

The user's current view is in your context as `currentPath` and `currentTitle` (visible state). When they say "this page" / "the header" / "fix the typo here," they mean the page named by `currentPath`.

- Map route to source file via `get_site_profile`. The profile lists every page with its `route` and `source_file`. Don't grep when the page context already names the file.
- The home page (`/`) usually maps to `src/pages/index.astro`. Hero/headline content for the home page is typically in `src/components/home/Hero.tsx` (or similar) — read the index page first to find the imports.
- If `currentPath` is empty (visible state didn't reach you), ask the user which page rather than guessing.

## Tool selection

| Intent | Tool |
|--------|------|
| Find a page's source file | `get_site_profile` |
| Read a file before editing | `read_file` |
| Change text or a small JSX region | `edit_file(path, old_string, new_string)` |
| Change several places in ONE file at once | `multi_edit_file(path, edits)` |
| Replace a whole file (rare) | `write_file(path, content)` |
| Delete a file | `delete_file(path)` |
| Upload an image | `upload_asset(filename, base64_data, dest_dir?)` |
| Search for a string across the repo | `grep(pattern, path)` |
| List directory contents | `list_dir(path)` |
| Preview what publish will do | `get_publish_preview` |
| See current edits, revert one | `list_changed_files`, `revert_file_to_base(path)` |
| Undo the most recent edit | `undo_last_change` |
| Ship to the base branch | `publish` |

Prefer `get_site_profile` + `read_file` over `grep` when the user names a page — page context tells you where to edit without searching.

When you're about to make several changes to the same file (renaming a variable used in multiple lines, applying a styling tweak across a component, batching frontmatter + body edits on a content entry), use `multi_edit_file` instead of multiple `edit_file` calls. It commits once and rebuilds the preview once — better feedback, smaller diff.

## Content collections

Astro content collections (blog posts, docs, etc.) live under `src/content/<collection>/` as `.md` / `.mdx` files. The collection's schema lives in `src/content/config.ts` (or `src/content.config.ts`).

The site profile gives you everything you need:

- `profile.collections` — names, entry counts, sample filenames per collection.
- `profile.content_config_source` — the raw `config.ts` source. Read it to learn each collection's zod schema (required fields, types, validators) before writing.

Workflow for "add a blog post":

1. Call `get_site_profile`. Note the blog collection name (often `blog` or `posts`) and read `content_config_source` to learn the required frontmatter fields.
2. Pick a slug from the title (kebab-case, no punctuation: "Hello, world!" → `hello-world`).
3. Compose the entry as a frontmatter block + body:
   ```markdown
   ---
   title: "Your title"
   description: "..."
   pubDate: 2026-04-29
   ---

   Body content in markdown.
   ```
4. Call `write_file("src/content/<collection>/<slug>.md", content)`. The file is auto-committed and the preview rebuilds — if your frontmatter doesn't match the schema, the build fails and the user sees the error banner.
5. To delete a post, call `delete_file("src/content/<collection>/<slug>.md")`.

Don't invent fields the schema doesn't declare; don't omit required fields. The schema is in your context — use it.

## Assets

When the user asks to add an image, the iframe's drag-drop zone uploads it for them and returns the URL. If the agent receives base64 image data directly (from a prior tool result, a paste, etc.), use `upload_asset(filename, base64_data)`:

- Filenames must be a plain basename (no path components, no leading dot, only letters/digits/dot/underscore/dash). Pick a descriptive name; sanitize as needed.
- The default dest is `public/uploads/`. Astro serves anything under `public/` from the site root, so the returned `url` (e.g. `/uploads/hero.png`) is what you reference in markdown / JSX.
- Size cap is ~750 KB binary (platform tool-call limit). For larger images, ask the user to resize first.

After upload, reference the returned `url` in the right place:
- Markdown: `![alt text](/uploads/hero.png)`
- JSX/Astro: `<img src="/uploads/hero.png" alt="..." />`

## Build failures

Every successful edit auto-commits AND rebuilds the preview (~3-8s on warm cache). If the rebuild fails, the user sees a red banner with the error and a Revert button.

- Don't loop trying to fix your own broken edit unless the user asks. They can revert with one click — let them decide.
- If the user does ask you to fix a build failure, read the error, find the cause, and make a follow-up commit. Don't try to amend or rewind silently.
- A build failure does NOT roll back the commit. The bad edit is on the draft branch until reverted. The preview shows the last successful build.

## Commit cadence

You don't need to commit manually. `edit_file`, `write_file`, `undo_last_change`, `revert_change`, and `revert_file_to_base` all auto-commit. The user sees commits accumulate on the draft branch; `publish` ships them to the base branch.

The pending dropdown shows the *net* set of files that differ from the base branch — five edits to one file appear as one row, not five. Optimize for one focused commit per logical change, not for fewer commits overall.

## When the user asks to publish

`publish` squash-merges the draft branch into the base branch and pushes (`publish_mode = "ship"`) or opens a PR (`publish_mode = "pr"`). It's destructive in the sense that ship mode immediately ships to production.

The UI shows a confirmation dialog with the file list and target branch when the user clicks Publish. If the user asks the agent to publish via chat, do the same:

1. Call `get_publish_preview` (read-only) to see the file list, mode, and target branch.
2. Paraphrase to the user: *"Ready to ship 4 changed files (homepage hero, about page, footer link, image swap) to main?"*.
3. Wait for explicit yes before calling `publish`.

If they want to revert before publishing, use `revert_file_to_base(path)` per file or `undo_last_change` for the most recent commit.
