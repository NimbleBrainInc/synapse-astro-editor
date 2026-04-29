import { useEffect, useRef, useState } from "react";
import {
  SynapseProvider,
  useCallTool,
  useDataSync,
  useTheme,
  useVisibleState,
} from "@nimblebrain/synapse/react";

type SiteProfile = {
  has_astro_config: boolean;
  has_content_config: boolean;
  base: string;
  root_path: string;
  pages: string[];
  components: string[];
  layouts: string[];
  content_collections: string[];
  notes: string[];
};

type BootPhase =
  | "idle"
  | "cloning"
  | "installing"
  | "scanning"
  | "starting"
  | "rendering"
  | "ready"
  | "failed";

type WorkspaceStatus = {
  configured: boolean;
  error?: string;
  repo_url?: string;
  owner?: string;
  repo?: string;
  draft_branch?: string;
  base_branch?: string;
  cloned?: boolean;
  current_branch?: string | null;
  runtime: "running" | "stopped";
  init_error: string | null;
  boot_phase: BootPhase;
  boot_started_at: number;
  boot_finished_at: number;
  profile: SiteProfile | null;
  /** Same-origin URL the UI should iframe, or null if proxy not wired. */
  preview_url: string | null;
  /** Result of the most recent post-edit rebuild. `null` until the first
   *  edit; "ok" / "failed" thereafter. The preview iframe still serves
   *  the LAST successful build, so when this is "failed" the user sees
   *  a stale page — the banner is what tells them why. */
  last_build_status: "ok" | "failed" | null;
  last_build_error: string | null;
  last_build_at: number;
};

const PHASE_LABEL: Record<BootPhase, string> = {
  idle: "Initializing…",
  cloning: "Cloning your repo…",
  installing: "Installing dependencies…",
  scanning: "Reading your site…",
  starting: "Starting Astro…",
  rendering: "Rendering preview…",
  ready: "Ready",
  failed: "Setup failed",
};

type PendingChange = {
  sha: string;
  short_sha: string;
  message: string;
  when: string;
};

type PendingResult = {
  draft_branch: string;
  base_branch: string;
  count: number;
  commits: PendingChange[];
};

type ChangedFile = {
  path: string;
  status: "added" | "modified" | "deleted" | "renamed" | "type_changed" | "other";
};

type ChangedFilesResult = {
  draft_branch: string;
  base_branch: string;
  count: number;
  files: ChangedFile[];
};

type PublishResult =
  | { published: false; reason: string }
  | {
      published: true;
      mode: "ship";
      branch: string;
      sha: string;
      message: string;
      commits: number;
    }
  | {
      published: true;
      mode: "pr";
      pr: { number?: number; url?: string; error?: string };
    };

function AstroEditor() {
  const theme = useTheme();

  const bootTool = useCallTool<{ phase: BootPhase }>("boot");
  const statusTool = useCallTool<WorkspaceStatus>("get_workspace_status");
  const pendingTool = useCallTool<PendingResult>("list_pending_changes");
  const changedFilesTool = useCallTool<ChangedFilesResult>("list_changed_files");
  const undoTool = useCallTool<{ reverted: string | null }>("undo_last_change");
  const revertFileTool = useCallTool<{ path: string; action: string }>(
    "revert_file_to_base",
  );
  const publishTool = useCallTool<PublishResult>("publish");

  const [status, setStatus] = useState<WorkspaceStatus | null>(null);
  const [iframeStamp, setIframeStamp] = useState(0);
  const [pending, setPending] = useState<PendingResult | null>(null);
  const [changedFiles, setChangedFiles] = useState<ChangedFilesResult | null>(null);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [siteInfoOpen, setSiteInfoOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  // What the user is currently looking at inside the preview iframe. Pushed
  // to the host as visible state so the agent gets "user is viewing /about"
  // in its per-turn context — no asking, no guessing.
  const [currentPage, setCurrentPage] = useState<{ path: string; title: string } | null>(
    null,
  );

  const bootKickedRef = useRef(false);

  async function refreshStatus() {
    try {
      const r = await statusTool.call({});
      setStatus(r.data);
      return r.data;
    } catch (e) {
      setError(e instanceof Error ? e.message : "status failed");
      return null;
    }
  }

  function refreshPreview() {
    // Bust iframe cache — same-origin proxy handles the actual fetch.
    setIframeStamp(Date.now());
  }

  async function refreshPending() {
    try {
      const r = await pendingTool.call({});
      setPending(r.data);
    } catch {
      // non-critical
    }
  }

  async function refreshChangedFiles() {
    try {
      const r = await changedFilesTool.call({});
      setChangedFiles(r.data);
    } catch {
      // non-critical
    }
  }

  async function kickBoot() {
    if (bootAlreadyStarted(status) || bootKickedRef.current) return;
    bootKickedRef.current = true;
    try {
      await bootTool.call({});
      refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "boot failed");
      bootKickedRef.current = false;
    }
  }

  async function handleUndo() {
    setError(null);
    try {
      const r = await undoTool.call({});
      if (r.data.reverted) setToast("Undid the last change");
      else setToast("Nothing to undo");
      setTimeout(() => setToast(null), 3000);
      refreshStatus();
      refreshPreview();
      refreshPending();
      refreshChangedFiles();
    } catch (e) {
      setError(e instanceof Error ? e.message : "undo failed");
    }
  }

  async function handleRevertFile(path: string) {
    setError(null);
    try {
      await revertFileTool.call({ path });
      setToast(`Reverted ${path}`);
      setTimeout(() => setToast(null), 3000);
      refreshStatus();
      refreshPreview();
      refreshPending();
      refreshChangedFiles();
    } catch (e) {
      setError(e instanceof Error ? e.message : "revert failed");
    }
  }

  async function handlePublish() {
    setError(null);
    try {
      const r = await publishTool.call({});
      const data = r.data;
      if (!data.published) {
        setToast(`Nothing to publish — ${"reason" in data ? data.reason : ""}`);
      } else if ("mode" in data && data.mode === "ship") {
        setToast(`Published ${data.commits} change${data.commits === 1 ? "" : "s"}`);
      } else if ("pr" in data && data.pr.url) {
        setToast(`PR opened: #${data.pr.number}`);
      } else {
        setToast("Published");
      }
      setTimeout(() => setToast(null), 4000);
      refreshPending();
    } catch (e) {
      setError(e instanceof Error ? e.message : "publish failed");
    }
  }

  // First mount — get current state, kick boot if needed.
  useEffect(() => {
    // Make the iframe slot's html/body fill the viewport so our root's
    // `height: 100%` resolves to the slot height instead of collapsing to
    // content height. Without this the editor renders at ~200px tall and
    // the preview iframe only shows the website's nav.
    document.documentElement.style.height = "100%";
    document.body.style.height = "100%";
    document.body.style.margin = "0";
    document.body.style.overflow = "hidden";
    // React mounts into #root, which also needs to fill the body or our flex
    // root collapses to its content height (header only).
    const rootEl = document.getElementById("root");
    if (rootEl) rootEl.style.height = "100%";
    (async () => {
      const s = await refreshStatus();
      if (s && !bootAlreadyStarted(s)) kickBoot();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll status while boot is in progress.
  useEffect(() => {
    if (!status) return;
    if (status.boot_phase === "ready" || status.boot_phase === "failed") return;
    const t = setInterval(refreshStatus, 1500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.boot_phase]);

  // Once boot reaches ready, load pending changes. Preview iframe loads itself
  // via same-origin src.
  useEffect(() => {
    if (status?.boot_phase !== "ready") return;
    refreshPending();
    refreshChangedFiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.boot_phase]);

  // Re-render preview + refresh pending + refresh status whenever the agent
  // calls a tool. Status refresh is what flips the build-error banner on/off
  // when an edit succeeds or fails.
  useDataSync(() => {
    if (status?.boot_phase === "ready") {
      refreshStatus();
      refreshPreview();
      refreshPending();
      refreshChangedFiles();
    }
  });

  // Push the page the user is currently viewing into the agent's per-turn
  // context. The host wraps this in containment so the agent reads it as
  // "the user is currently viewing X" without us having to add a tool call.
  // Updates whenever the inner iframe navigates; the declarative form
  // auto-debounces (250ms in the Synapse SDK).
  useVisibleState(
    () => ({
      state: currentPage
        ? {
            currentPath: currentPage.path,
            currentTitle: currentPage.title,
          }
        : {},
      summary: currentPage
        ? `Currently viewing ${currentPage.path}${
            currentPage.title ? ` (${currentPage.title})` : ""
          }`
        : undefined,
    }),
    [currentPage?.path, currentPage?.title],
  );

  const fg = theme.tokens["--color-text-primary"] || "#111827";
  const bg = theme.tokens["--color-background-primary"] || "#fff";
  const bgSubtle = theme.tokens["--color-background-secondary"] || "#f9fafb";
  const border = theme.tokens["--color-border-primary"] || "#e5e7eb";
  const accent = theme.tokens["--color-text-accent"] || "#2563eb";
  const muted = theme.tokens["--color-text-secondary"] || "#6b7280";

  const profile = status?.profile ?? null;
  const runtime = status?.runtime ?? "stopped";
  const phase = status?.boot_phase ?? "idle";
  const isReady = phase === "ready";
  const isBooting = !!status && phase !== "ready" && phase !== "failed";

  return (
    <div
      style={{
        // Fill the platform slot exactly. `100vh` would be the browser viewport
        // (taller than the slot the platform gives us), which causes the
        // editor to overflow and show an extra outer scrollbar alongside the
        // iframe's own.
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
        fontFamily:
          "var(--font-sans, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif)",
        color: fg,
        background: bg,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: ".75rem",
          padding: ".55rem 1rem",
          borderBottom: `1px solid ${border}`,
          background: bgSubtle,
          flexShrink: 0,
        }}
      >
        <span style={{ fontWeight: 600 }}>🚀 Astro Editor</span>
        <StatusPill status={status} runtime={runtime} muted={muted} border={border} />
        <PendingPill
          changedFiles={changedFiles}
          pending={pending}
          open={pendingOpen}
          onToggle={() => setPendingOpen((v) => !v)}
          muted={muted}
          accent={accent}
        />
        <div style={{ flex: 1 }} />
        <SiteInfoButton
          profile={profile}
          status={status}
          open={siteInfoOpen}
          onToggle={() => setSiteInfoOpen((v) => !v)}
          muted={muted}
          border={border}
          fg={fg}
          bg={bg}
          bgSubtle={bgSubtle}
        />
        <button
          onClick={refreshPreview}
          disabled={runtime !== "running"}
          style={ghostBtn(border, fg, runtime !== "running")}
          title="Reload preview"
        >
          ↻
        </button>
        <button
          onClick={handleUndo}
          disabled={undoTool.isPending || !pending || pending.count === 0}
          style={ghostBtn(border, fg, undoTool.isPending || !pending || pending.count === 0)}
        >
          Undo
        </button>
        <button
          onClick={handlePublish}
          disabled={publishTool.isPending || !pending || pending.count === 0}
          style={primaryBtn(accent, publishTool.isPending || !pending || pending.count === 0)}
        >
          {publishTool.isPending ? "Publishing…" : "Publish"}
        </button>
      </header>

      {(error || status?.init_error || toast) && (
        <div
          style={{
            padding: ".4rem 1rem",
            background: error || status?.init_error ? "#fef2f2" : "#ecfdf5",
            color: error || status?.init_error ? "#991b1b" : "#065f46",
            fontSize: ".82rem",
            borderBottom: `1px solid ${border}`,
          }}
        >
          {error ?? status?.init_error ?? toast}
        </div>
      )}

      {pendingOpen && changedFiles && changedFiles.files.length > 0 && (
        <ChangedFilesList
          changedFiles={changedFiles}
          onRevertFile={handleRevertFile}
          isReverting={revertFileTool.isPending}
          border={border}
          muted={muted}
          fg={fg}
        />
      )}

      <div style={{ display: "flex", flex: 1, minWidth: 0, minHeight: 0 }}>
        <main
          style={{
            // `flex: 1` paired with `minWidth: 0` is the canonical "fill the
            // remaining flex track without growing past it." Default flex
            // items have `min-width: auto` (= min-content), which expands the
            // item to fit its intrinsic child width. The preview iframe is
            // hardcoded to PREVIEW_DESIGN_WIDTH (1280) — without minWidth: 0
            // here, `<main>` swells to 1280 even when the editor's host slot
            // is narrower (e.g., chat panel open), and the iframe's
            // ResizeObserver never sees the true visible width to scale to.
            // overflow: hidden clips the scaled iframe to our pane.
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            overflow: "hidden",
            position: "relative",
            background: bg,
          }}
        >
          {phase === "failed" ? (
            <CenterMessage muted={muted}>
              Setup failed.
              <div style={{ marginTop: ".5rem", color: "#991b1b", fontSize: ".82rem" }}>
                {status?.init_error ?? "unknown error"}
              </div>
              <button
                onClick={() => {
                  bootKickedRef.current = false;
                  kickBoot();
                }}
                style={{
                  marginTop: ".75rem",
                  padding: ".4rem .9rem",
                  borderRadius: 6,
                  border: `1px solid ${border}`,
                  background: bg,
                  color: fg,
                  cursor: "pointer",
                  fontSize: ".8rem",
                }}
              >
                Retry
              </button>
            </CenterMessage>
          ) : isBooting || !status ? (
            <CenterMessage muted={muted}>
              <div style={{ fontWeight: 500 }}>{PHASE_LABEL[phase]}</div>
              <div style={{ fontSize: ".75rem", marginTop: ".4rem", opacity: 0.7 }}>
                First run can take a minute (clone + npm install).
              </div>
            </CenterMessage>
          ) : !isReady || runtime !== "running" ? (
            <CenterMessage muted={muted}>
              Astro dev server is not running. Check status pill.
            </CenterMessage>
          ) : status?.preview_url ? (
            <>
              <ScaledPreview
                src={`${status.preview_url}?_=${iframeStamp}`}
                iframeKey={iframeStamp}
                bg={bg}
                proxyPrefix={status.preview_url}
                onNavigate={setCurrentPage}
              />
              {status.last_build_status === "failed" && status.last_build_error && (
                <BuildErrorBanner
                  error={status.last_build_error}
                  onRevert={handleUndo}
                  isReverting={undoTool.isPending}
                />
              )}
            </>
          ) : (
            <CenterMessage muted={muted}>
              No preview URL — check that the http-proxy declaration is wired.
            </CenterMessage>
          )}
        </main>
      </div>
    </div>
  );
}

function StatusPill({
  status,
  runtime,
  muted,
  border,
}: {
  status: WorkspaceStatus | null;
  runtime: "running" | "stopped";
  muted: string;
  border: string;
}) {
  if (!status) return null;
  const label = !status.configured
    ? "unconfigured"
    : status.boot_phase === "failed"
      ? "failed"
      : status.boot_phase !== "ready"
        ? PHASE_LABEL[status.boot_phase]
        : runtime === "running"
          ? `${status.current_branch} · live`
          : `${status.current_branch} · idle`;
  return (
    <span
      style={{
        fontSize: ".75rem",
        color: muted,
        border: `1px solid ${border}`,
        borderRadius: 999,
        padding: ".1rem .55rem",
      }}
    >
      {label}
    </span>
  );
}

function bootAlreadyStarted(s: WorkspaceStatus | null): boolean {
  if (!s) return false;
  return s.boot_phase !== "idle";
}

function PendingPill({
  changedFiles,
  pending,
  open,
  onToggle,
  muted,
  accent,
}: {
  changedFiles: ChangedFilesResult | null;
  pending: PendingResult | null;
  open: boolean;
  onToggle: () => void;
  muted: string;
  accent: string;
}) {
  // Prefer the file-level diff (the user thinks in "what's about to ship,"
  // not "how many commits did the agent make"). Fall back to commit count
  // while the changed-files tool result is in flight, so the pill never
  // disappears mid-refresh.
  const source = changedFiles ?? pending;
  if (!source) return null;
  const n = source.count;
  const noun = changedFiles ? "file" : "commit";
  const color = n > 0 ? accent : muted;
  return (
    <button
      onClick={onToggle}
      style={{
        fontSize: ".75rem",
        color,
        border: `1px solid ${color}`,
        borderRadius: 999,
        padding: ".1rem .55rem",
        background: "transparent",
        cursor: "pointer",
      }}
      title={n > 0 ? "Show pending changes" : "No pending changes"}
    >
      {n === 0
        ? "up to date"
        : `${n} ${noun}${n === 1 ? "" : "s"} changed ${open ? "▴" : "▾"}`}
    </button>
  );
}

function ChangedFilesList({
  changedFiles,
  onRevertFile,
  isReverting,
  border,
  muted,
  fg,
}: {
  changedFiles: ChangedFilesResult;
  onRevertFile: (path: string) => void;
  isReverting: boolean;
  border: string;
  muted: string;
  fg: string;
}) {
  return (
    <div
      style={{
        maxHeight: 220,
        overflow: "auto",
        borderBottom: `1px solid ${border}`,
        background: "rgba(0,0,0,.02)",
        fontSize: ".82rem",
      }}
    >
      {changedFiles.files.map((f) => (
        <div
          key={f.path}
          style={{
            display: "flex",
            alignItems: "center",
            gap: ".75rem",
            padding: ".4rem 1rem",
            borderBottom: `1px solid ${border}`,
          }}
        >
          <StatusBadge status={f.status} muted={muted} />
          <code
            style={{
              flex: 1,
              color: fg,
              wordBreak: "break-all",
              fontSize: ".78rem",
              fontFamily:
                "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
            }}
          >
            {f.path}
          </code>
          <button
            onClick={() => onRevertFile(f.path)}
            disabled={isReverting}
            style={ghostBtn(border, fg, isReverting)}
            title={`Revert ${f.path} to ${changedFiles.base_branch}`}
          >
            Revert
          </button>
        </div>
      ))}
    </div>
  );
}

function StatusBadge({
  status,
  muted,
}: {
  status: ChangedFile["status"];
  muted: string;
}) {
  // Single-letter badge that mirrors `git status --short` so it's familiar
  // to anyone who's used git: A added, M modified, D deleted, R renamed,
  // T type changed.
  const map: Record<ChangedFile["status"], { letter: string; color: string }> = {
    added: { letter: "A", color: "#16a34a" },
    modified: { letter: "M", color: "#2563eb" },
    deleted: { letter: "D", color: "#dc2626" },
    renamed: { letter: "R", color: "#9333ea" },
    type_changed: { letter: "T", color: "#ca8a04" },
    other: { letter: "?", color: muted },
  };
  const { letter, color } = map[status];
  return (
    <span
      style={{
        display: "inline-block",
        width: "1.4rem",
        textAlign: "center",
        fontFamily:
          "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
        fontSize: ".75rem",
        fontWeight: 600,
        color,
      }}
      title={status}
    >
      {letter}
    </span>
  );
}

/**
 * Render the preview iframe at a fixed "design width" and CSS-scale it to fit
 * the actual pane width. This is the standard responsive-preview pattern
 * (CodeSandbox / Storybook / Vercel preview):
 *
 *   - Sites usually have a designed-for desktop width (most marketing sites:
 *     1200–1440px). At narrower widths their layouts often have small overflow
 *     bugs that produce horizontal scrollbars in the preview pane.
 *   - Rendering at the design width and scaling-to-fit gives the editor user
 *     the design as intended, just smaller. No horizontal scrollbar from
 *     site CSS quirks.
 *   - Layout-sensitive previews (a 320px mobile mockup, etc.) belong as a
 *     follow-up "viewport size" picker; the desktop default is the right
 *     out-of-the-box choice.
 */
const PREVIEW_DESIGN_WIDTH = 1280;

function ScaledPreview({
  src,
  iframeKey,
  bg,
  proxyPrefix,
  onNavigate,
}: {
  src: string;
  iframeKey: number;
  bg: string;
  /** Public path prefix the proxy serves under, e.g.
   *  `/v1/ws/<wsId>/apps/<bundle>/preview`. Used to strip the prefix from
   *  the iframe's pathname so `onNavigate` reports the user-facing route
   *  (`/about`) rather than the proxied one. */
  proxyPrefix?: string | null;
  onNavigate?: (info: { path: string; title: string }) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const w = el.clientWidth;
      // Don't scale UP — if the pane is wider than the design width, render
      // at native size and center horizontally.
      setScale(Math.min(1, w / PREVIEW_DESIGN_WIDTH));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Read the inner page's location + title on every iframe load. The proxy
  // serves on the platform origin (same as us), so cross-frame DOM access
  // is allowed. If the platform ever flips on cross-origin isolation per
  // bundle, contentWindow access throws — the try/catch lets us fail
  // closed (no page context pushed) without breaking the preview.
  function handleLoad() {
    if (!onNavigate) return;
    const iframe = iframeRef.current;
    if (!iframe) return;
    try {
      const win = iframe.contentWindow;
      const doc = iframe.contentDocument;
      if (!win || !doc) return;
      let pathname = win.location.pathname;
      // Strip the proxy prefix so we report the user-facing route.
      if (proxyPrefix && pathname.startsWith(proxyPrefix)) {
        pathname = pathname.slice(proxyPrefix.length) || "/";
      }
      onNavigate({ path: pathname, title: doc.title ?? "" });
    } catch {
      // Cross-origin or detached document — fail silently.
    }
  }

  // Iframe at native pixels: width = design, height = container/scale so
  // after scaling it fills the container vertically.
  const inverseScalePct = scale > 0 ? 100 / scale : 100;

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: "100%",
        overflow: "hidden",
        background: bg,
        // Center the scaled iframe horizontally when the pane is wider than
        // the scaled design width (e.g., on very wide editor panes).
        display: "flex",
        justifyContent: "center",
      }}
    >
      <iframe
        ref={iframeRef}
        key={iframeKey}
        title="Astro preview"
        src={src}
        onLoad={handleLoad}
        style={{
          display: "block",
          width: `${PREVIEW_DESIGN_WIDTH}px`,
          height: `${inverseScalePct}%`,
          border: "none",
          background: bg,
          transformOrigin: "top center",
          transform: `scale(${scale})`,
          flexShrink: 0,
        }}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      />
    </div>
  );
}

function SiteInfoButton({
  profile,
  status,
  open,
  onToggle,
  muted,
  border,
  fg,
  bg,
  bgSubtle,
}: {
  profile: SiteProfile | null;
  status: WorkspaceStatus | null;
  open: boolean;
  onToggle: () => void;
  muted: string;
  border: string;
  fg: string;
  bg: string;
  bgSubtle: string;
}) {
  if (!profile) return null;
  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={onToggle}
        style={{
          fontSize: ".75rem",
          color: muted,
          border: `1px solid ${border}`,
          borderRadius: 999,
          padding: ".1rem .55rem",
          background: "transparent",
          cursor: "pointer",
        }}
        title="Site info"
      >
        Site {open ? "▴" : "▾"}
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + .35rem)",
            right: 0,
            zIndex: 10,
            minWidth: 280,
            background: bg,
            color: fg,
            border: `1px solid ${border}`,
            borderRadius: 8,
            boxShadow: "0 6px 20px rgba(0,0,0,.08)",
            padding: ".75rem .9rem",
            fontSize: ".8rem",
          }}
        >
          <ProfilePanel profile={profile} status={status} muted={muted} fg={fg} />
        </div>
      )}
      {/* unused, kept to silence lint for unused params on some bundlers */}
      <span style={{ display: "none" }}>{bgSubtle}</span>
    </div>
  );
}

function ProfilePanel({
  profile,
  status,
  muted,
  fg,
}: {
  profile: SiteProfile;
  status: WorkspaceStatus | null;
  muted: string;
  fg: string;
}) {
  return (
    <div style={{ padding: ".75rem .9rem", fontSize: ".8rem", overflow: "auto", flex: 1 }}>
      <div style={{ fontWeight: 600, marginBottom: ".5rem", color: fg }}>Site</div>
      {status?.owner && (
        <Row label="Repo" value={`${status.owner}/${status.repo}`} muted={muted} />
      )}
      {status?.current_branch && (
        <Row label="Branch" value={status.current_branch} muted={muted} />
      )}
      <Row
        label="astro.config"
        value={profile.has_astro_config ? "✓" : "missing"}
        muted={muted}
      />
      <Row
        label="content/config"
        value={profile.has_content_config ? "✓" : "missing"}
        muted={muted}
      />
      <Row label="pages" value={String(profile.pages.length)} muted={muted} />
      <Row
        label="components"
        value={String(profile.components.length)}
        muted={muted}
      />
      <Row label="layouts" value={String(profile.layouts.length)} muted={muted} />
      <Row
        label="collections"
        value={
          profile.content_collections.length
            ? profile.content_collections.join(", ")
            : "none"
        }
        muted={muted}
      />
      {profile.notes.length > 0 && (
        <div style={{ marginTop: ".5rem", color: "#b45309", fontSize: ".75rem" }}>
          {profile.notes.join(" · ")}
        </div>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  muted,
}: {
  label: string;
  value: string;
  muted: string;
}) {
  return (
    <div style={{ display: "flex", gap: ".5rem", padding: "1px 0" }}>
      <div style={{ width: 92, color: muted }}>{label}</div>
      <div style={{ flex: 1, wordBreak: "break-all" }}>{value}</div>
    </div>
  );
}

function CenterMessage({
  children,
  muted,
}: {
  children: React.ReactNode;
  muted: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        color: muted,
        fontSize: ".9rem",
        textAlign: "center",
        padding: "2rem",
      }}
    >
      {children}
    </div>
  );
}

function BuildErrorBanner({
  error,
  onRevert,
  isReverting,
}: {
  error: string;
  onRevert: () => void;
  isReverting: boolean;
}) {
  // Astro build errors are usually multi-line, with the last few lines being
  // the actionable bit (file + line + reason). Show them all but cap height.
  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        background: "#fef2f2",
        borderBottom: "1px solid #fecaca",
        color: "#991b1b",
        padding: ".55rem .85rem",
        fontSize: ".78rem",
        display: "flex",
        gap: ".75rem",
        alignItems: "flex-start",
        boxShadow: "0 2px 6px rgba(153, 27, 27, .08)",
        zIndex: 10,
      }}
    >
      <div style={{ flexShrink: 0, fontWeight: 600 }}>Build failed</div>
      <pre
        style={{
          flex: 1,
          minWidth: 0,
          margin: 0,
          maxHeight: "6.5rem",
          overflow: "auto",
          fontFamily:
            "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
          fontSize: ".72rem",
          lineHeight: 1.4,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {error}
      </pre>
      <button
        onClick={onRevert}
        disabled={isReverting}
        style={{
          flexShrink: 0,
          padding: ".3rem .65rem",
          borderRadius: 4,
          border: "1px solid #fecaca",
          background: "#fff",
          color: "#991b1b",
          fontSize: ".75rem",
          cursor: isReverting ? "wait" : "pointer",
          opacity: isReverting ? 0.6 : 1,
        }}
        title="Revert the last commit on the draft branch and rebuild"
      >
        {isReverting ? "Reverting…" : "Revert last edit"}
      </button>
    </div>
  );
}

function primaryBtn(accent: string, disabled: boolean): React.CSSProperties {
  return {
    padding: ".35rem .85rem",
    borderRadius: 6,
    border: "none",
    background: accent,
    color: "#fff",
    fontSize: ".8rem",
    fontWeight: 500,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    whiteSpace: "nowrap",
  };
}

function ghostBtn(
  border: string,
  fg: string,
  disabled: boolean
): React.CSSProperties {
  return {
    padding: ".3rem .7rem",
    borderRadius: 6,
    border: `1px solid ${border}`,
    background: "transparent",
    color: fg,
    fontSize: ".78rem",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    whiteSpace: "nowrap",
  };
}

export function App() {
  return (
    <SynapseProvider name="astro-editor" version="0.1.0">
      <AstroEditor />
    </SynapseProvider>
  );
}
