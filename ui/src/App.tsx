import { useEffect, useRef, useState } from "react";
import {
  SynapseProvider,
  useCallTool,
  useDataSync,
  useTheme,
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
  const undoTool = useCallTool<{ reverted: string | null }>("undo_last_change");
  const revertTool = useCallTool<{ reverted: string }>("revert_change");
  const publishTool = useCallTool<PublishResult>("publish");

  const [status, setStatus] = useState<WorkspaceStatus | null>(null);
  const [iframeStamp, setIframeStamp] = useState(0);
  const [pending, setPending] = useState<PendingResult | null>(null);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

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
      refreshPreview();
      refreshPending();
    } catch (e) {
      setError(e instanceof Error ? e.message : "undo failed");
    }
  }

  async function handleRevert(sha: string) {
    setError(null);
    try {
      await revertTool.call({ sha });
      setToast("Change reverted");
      setTimeout(() => setToast(null), 3000);
      refreshPreview();
      refreshPending();
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
    document.body.style.margin = "0";
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.boot_phase]);

  // Re-render preview + refresh pending whenever the agent calls a tool.
  useDataSync(() => {
    if (status?.boot_phase === "ready") {
      refreshPreview();
      refreshPending();
    }
  });

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
        display: "flex",
        flexDirection: "column",
        height: "100vh",
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
          pending={pending}
          open={pendingOpen}
          onToggle={() => setPendingOpen((v) => !v)}
          muted={muted}
          border={border}
          accent={accent}
        />
        <div style={{ flex: 1 }} />
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

      {pendingOpen && pending && pending.commits.length > 0 && (
        <PendingList
          pending={pending}
          onRevert={handleRevert}
          revertingSha={revertTool.isPending}
          border={border}
          muted={muted}
          fg={fg}
        />
      )}

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <aside
          style={{
            width: 240,
            borderRight: `1px solid ${border}`,
            background: bgSubtle,
            display: "flex",
            flexDirection: "column",
            flexShrink: 0,
          }}
        >
          {profile ? (
            <ProfilePanel profile={profile} status={status} muted={muted} fg={fg} />
          ) : (
            <div style={{ padding: ".75rem .9rem", color: muted, fontSize: ".82rem" }}>
              Site info appears once the workspace is ready.
            </div>
          )}
          <div
            style={{
              padding: ".55rem .75rem",
              borderTop: `1px solid ${border}`,
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
            <button
              onClick={refreshPreview}
              disabled={runtime !== "running"}
              style={ghostBtn(border, fg, runtime !== "running")}
              title="Reload preview"
            >
              ↻ Reload
            </button>
          </div>
        </aside>

        <main style={{ flex: 1, position: "relative", background: bg }}>
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
            <iframe
              key={iframeStamp}
              title="Astro preview"
              src={`${status.preview_url}?_=${iframeStamp}`}
              style={{
                width: "100%",
                height: "100%",
                border: "none",
                background: bg,
              }}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            />
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
  pending,
  open,
  onToggle,
  muted,
  border,
  accent,
}: {
  pending: PendingResult | null;
  open: boolean;
  onToggle: () => void;
  muted: string;
  border: string;
  accent: string;
}) {
  if (!pending) return null;
  const n = pending.count;
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
      {n === 0 ? "up to date" : `${n} pending ${open ? "▴" : "▾"}`}
    </button>
  );
}

function PendingList({
  pending,
  onRevert,
  revertingSha,
  border,
  muted,
  fg,
}: {
  pending: PendingResult;
  onRevert: (sha: string) => void;
  revertingSha: boolean;
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
      {pending.commits.slice().reverse().map((c) => (
        <div
          key={c.sha}
          style={{
            display: "flex",
            alignItems: "center",
            gap: ".75rem",
            padding: ".4rem 1rem",
            borderBottom: `1px solid ${border}`,
          }}
        >
          <code style={{ color: muted, fontSize: ".75rem" }}>{c.short_sha}</code>
          <span style={{ flex: 1, color: fg, wordBreak: "break-word" }}>
            {c.message}
          </span>
          <button
            onClick={() => onRevert(c.sha)}
            disabled={revertingSha}
            style={ghostBtn(border, fg, revertingSha)}
          >
            Revert
          </button>
        </div>
      ))}
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
