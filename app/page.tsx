"use client";

import {
  ChangeEvent,
  DragEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  Download,
  FileArchive,
  RefreshCw,
  Terminal,
  X,
} from "lucide-react";

type Phase =
  | "empty"
  | "ready"
  | "uploading"
  | "importing"
  | "previewing"
  | "review"
  | "building"
  | "complete"
  | "error";

type Log = { time: string; tag: string; message: string };
type Job = {
  job_id: string;
  project_id: string;
  stage: string;
  state: string;
  error_message?: string | null;
  result_data?: Record<string, unknown>;
};
type Project = { id: string; workspace_revision: number; original_filename: string };
type Review = {
  planId: string;
  patchId: string;
  planHash: string;
  patchHash: string;
  revision: number;
  paths: string[];
  operationCount: number;
};
type S3UploadSession = {
  upload_id: string;
  project_id: string;
  upload_mode: "s3";
  part_size: number;
  total_parts: number;
  completed_parts?: CompletedPart[];
};
type PendingPart = { part_number: number; bytes: ArrayBuffer; checksum_sha256: string };
type CompletedPart = { part_number: number; etag: string; checksum_sha256: string; size: number };
type PresignedPart = { part_number: number; url: string; headers: Record<string, string> };
type ModelId =
  | "gemini-3.6-flash"
  | "gemini-3.5-flash"
  | "gemini-3.5-flash-lite"
  | "gemini-flash-latest"
  | "gemini-3.7-flash"
  | "gemini-3.8-flash"
  | "gemini-3.1-pro-preview"
  | "gemini-3.1-flash-lite"
  | "openrouter:nvidia/nemotron-3.5-lightning:free";

const models: Array<{ id: ModelId; label: string; note: string }> = [
  { id: "gemini-3.6-flash",              label: "3.6 Flash",       note: "Balanced" },
  { id: "gemini-3.5-flash",              label: "3.5 Flash",       note: "Fallback" },
  { id: "gemini-3.8-flash",              label: "3.8 Flash",       note: "Preview" },
  { id: "gemini-3.1-pro-preview",        label: "3.1 Pro",         note: "Deep plan" },
  { id: "gemini-3.5-flash-lite",         label: "3.5 Flash Lite",  note: "Fast" },
  { id: "openrouter:nvidia/nemotron-3.5-lightning:free", label: "Nemotron", note: "OpenRouter" },
];

const quickChips = [
  {
    label: "Rename App",
    prompt: "Rename the app to NOIR Notes and preserve all permissions.",
  },
  {
    label: "Add Toast",
    prompt: "Display a toast message saying 'Welcome to NOIR' whenever MainActivity starts.",
  },
  {
    label: "Internet Perm",
    prompt: "Add android.permission.INTERNET permission to AndroidManifest.xml.",
  },
  {
    label: "Network Ping",
    prompt: "Ping https://example.com/heartbeat on application launch.",
  },
];

const openingLogs: Log[] = [
  { time: "--:--:--", tag: "NOIR", message: "Connecting to NOIR backend" },
];

const wait = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const stamp = () =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());

const quietStatus = new Set(["queued", "running", "succeeded"]);

const logTag = (value: string) => value
  .replace(/^validating_input$/i, "VALIDATE")
  .replace(/^generating_patch$/i, "PATCH")
  .replace(/_/g, " ")
  .slice(0, 12)
  .toUpperCase();

const hexDigest = (bytes: ArrayBuffer) =>
  Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, "0")).join("");

const base64Digest = (bytes: ArrayBuffer) => {
  let binary = "";
  for (const value of new Uint8Array(bytes)) binary += String.fromCharCode(value);
  return window.btoa(binary);
};

const retry = async <T,>(operation: () => Promise<T>) => {
  let lastError: unknown;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      if (attempt === 3) break;
      await wait(500 * (2 ** attempt));
    }
  }
  throw lastError;
};

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/noir${path}`, {
    ...init,
    headers: { "Cache-Control": "no-store", ...init?.headers },
  });
  const data = await response.json().catch(() => null) as Record<string, unknown> | null;
  if (!response.ok) {
    throw new Error(String(data?.detail || data?.error || `Request failed (${response.status})`));
  }
  return data as T;
}

const jsonRequest = (method: string, body: unknown, idempotencyKey?: string): RequestInit => ({
  method,
  headers: {
    "Content-Type": "application/json",
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  },
  body: JSON.stringify(body),
});

export default function Home() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const eventCursor = useRef<string | null>(null);
  const lastJobState = useRef("");

  const [phase, setPhase] = useState<Phase>("empty");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [request, setRequest] = useState(
    "Rename the app to NOIR Notes and preserve all permissions.",
  );
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("");
  const [logs, setLogs] = useState<Log[]>(openingLogs);
  const [project, setProject] = useState<Project | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [buildId, setBuildId] = useState("");
  const [error, setError] = useState("");
  const [backendOnline, setBackendOnline] = useState(false);
  const [selectedModel, setSelectedModel] = useState<ModelId>("gemini-3.6-flash");
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [logsDrawerOpen, setLogsDrawerOpen] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);

  const working = ["uploading", "importing", "previewing", "building"].includes(phase);

  const addLog = useCallback((tag: string, message: string) => {
    setLogs((current) => [
      ...current,
      { time: stamp(), tag: logTag(tag), message: message.trim() },
    ].slice(-160));
  }, []);

  const checkBackend = useCallback(async () => {
    try {
      const [health, account] = await Promise.all([
        apiJson<{ status: string; capabilities?: Record<string, boolean> }>("/v1/health"),
        apiJson<{ user_id: string }>("/v1/auth/me"),
      ]);
      const ready = health.status === "ok" && health.capabilities?.import && health.capabilities?.build && Boolean(account.user_id);
      setBackendOnline(Boolean(ready));
      setLogs([{ time: stamp(), tag: ready ? "LIVE" : "WAIT", message: ready ? "Backend & build tools online" : "Backend is not build-ready" }]);
    } catch {
      setBackendOnline(false);
      setLogs([{ time: stamp(), tag: "OFF", message: "Backend currently unreachable" }]);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void checkBackend(), 0);
    return () => window.clearTimeout(timer);
  }, [checkBackend]);

  const loadFile = useCallback((file: File) => {
    if (!file.name.toLowerCase().endsWith(".apk")) {
      setError("Please choose a valid .apk file.");
      addLog("REFUSE", "Only APK files are accepted");
      return;
    }
    if (file.size === 0) {
      setError("The selected APK is empty.");
      return;
    }
    setSelectedFile(file);
    setPhase("ready");
    setProgress(0);
    setProject(null);
    setReview(null);
    setBuildId("");
    setError("");
    eventCursor.current = null;
    lastJobState.current = "";
    addLog("INPUT", `${file.name} ready (${(file.size / 1024 / 1024).toFixed(1)} MB)`);
  }, [addLog]);

  const handleFileInput = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) loadFile(file);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) loadFile(file);
  };

  const syncEvents = useCallback(async (projectId: string) => {
    const query = eventCursor.current ? `?after=${encodeURIComponent(eventCursor.current)}&limit=100` : "?limit=100";
    const data = await apiJson<{ events?: Array<{ event_id: string; stage?: string; severity?: string; message: string }> }>(
      `/v1/projects/${projectId}/events${query}`,
    );
    const events = data.events ?? [];
    if (!events.length) return;
    eventCursor.current = events.at(-1)?.event_id ?? eventCursor.current;
    const usefulEvents = events.filter((event) => {
      const message = event.message.trim();
      return !quietStatus.has(message.toLowerCase()) && !message.startsWith("Workflow: ");
    });
    setLogs((current) => [
      ...current,
      ...usefulEvents.map((event) => ({
        time: stamp(),
        tag: logTag(event.stage || event.severity || "AWS"),
        message: event.message,
      })),
    ].slice(-160));
  }, []);

  const pollJob = useCallback(async (initial: Job, from: number, to: number, labelPrefix: string) => {
    let job = initial;
    for (let attempt = 0; attempt < 900; attempt += 1) {
      if (job.project_id) await syncEvents(job.project_id).catch(() => undefined);
      const stateKey = `${job.stage}:${job.state}`;
      if (stateKey !== lastJobState.current) {
        lastJobState.current = stateKey;
        addLog(job.stage.toUpperCase(), job.state);
        setStatusMessage(`${labelPrefix}: ${job.state}...`);
      }
      if (["succeeded", "failed", "cancelled"].includes(job.state)) break;
      setProgress(Math.min(to - 2, from + Math.round((to - from) * Math.min(attempt / 24, .9))));
      await wait(2000);
      job = await apiJson<Job>(`/v1/jobs/${job.job_id}`);
    }
    if (job.state !== "succeeded") {
      throw new Error(job.error_message || `Job ${job.state}`);
    }
    setProgress(to);
    return job;
  }, [addLog, syncEvents]);

  const uploadAndImport = useCallback(async (file: File) => {
    setPhase("uploading");
    setStatusMessage("Verifying APK locally...");
    setProgress(2);
    addLog("HASH", "Verifying APK locally");
    const key = crypto.randomUUID();
    const fileBytes = await file.arrayBuffer();
    const sha256 = hexDigest(await crypto.subtle.digest("SHA-256", fileBytes));
    setProgress(5);
    setStatusMessage("Connecting to S3 upload...");
    addLog("UPLOAD", "Opening direct S3 upload");
    const session = await apiJson<S3UploadSession>(
      "/v1/uploads",
      jsonRequest("POST", { filename: file.name, size: file.size, sha256, upload_mode: "s3" }, key),
    );
    if (session.upload_mode !== "s3" || !session.upload_id || !Number.isInteger(session.part_size) || session.part_size < 5 * 1024 * 1024) {
      throw new Error("Direct S3 upload is unavailable.");
    }

    const completedParts = new Map<number, CompletedPart>(
      (session.completed_parts ?? []).map((part) => [part.part_number, part]),
    );
    let uploadedBytes = [...completedParts.values()].reduce((sum, part) => sum + part.size, 0);
    const pendingNumbers = Array.from({ length: session.total_parts }, (_, index) => index + 1)
      .filter((partNumber) => !completedParts.has(partNumber));

    for (let start = 0; start < pendingNumbers.length; start += 4) {
      const numbers = pendingNumbers.slice(start, start + 4);
      const pending = await Promise.all(numbers.map(async (partNumber): Promise<PendingPart> => {
        const offset = (partNumber - 1) * session.part_size;
        const bytes = fileBytes.slice(offset, Math.min(offset + session.part_size, file.size));
        return {
          part_number: partNumber,
          bytes,
          checksum_sha256: base64Digest(await crypto.subtle.digest("SHA-256", bytes)),
        };
      }));
      const authorization = await retry(() => apiJson<{ parts: PresignedPart[] }>(
        `/v1/uploads/${session.upload_id}/parts/presign`,
        jsonRequest("POST", { parts: pending.map(({ part_number, checksum_sha256 }) => ({ part_number, checksum_sha256 })) }),
      ));
      const byNumber = new Map(authorization.parts.map((part) => [part.part_number, part]));
      const uploaded = await Promise.all(pending.map(async (part): Promise<CompletedPart> => {
        const signed = byNumber.get(part.part_number);
        if (!signed) throw new Error(`Upload authorization missing for part ${part.part_number}.`);
        const checksumHeader = Object.entries(signed.headers).find(([name]) => name.toLowerCase() === "x-amz-checksum-sha256");
        if (Object.keys(signed.headers).length !== 1 || checksumHeader?.[1] !== part.checksum_sha256) {
          throw new Error("The backend returned unsafe upload headers.");
        }
        const response = await retry(async () => {
          const result = await fetch(signed.url, {
            method: "PUT",
            headers: { "x-amz-checksum-sha256": part.checksum_sha256 },
            body: part.bytes,
          });
          if (!result.ok) throw new Error(`S3 rejected part ${part.part_number} (${result.status}).`);
          return result;
        });
        const etag = response.headers.get("etag")?.trim() ?? "";
        if (!/^"?[a-f\d]{32}(?:-\d+)?"?$/i.test(etag)) throw new Error("S3 did not acknowledge the uploaded part.");
        const result = { part_number: part.part_number, etag, checksum_sha256: part.checksum_sha256, size: part.bytes.byteLength };
        completedParts.set(part.part_number, result);
        uploadedBytes += result.size;
        const pct = 5 + Math.round((uploadedBytes / file.size) * 25);
        setProgress(pct);
        setStatusMessage(`Uploading APK (${Math.round((uploadedBytes / file.size) * 100)}%)...`);
        return result;
      }));
      await retry(() => apiJson<S3UploadSession>(
        `/v1/uploads/${session.upload_id}/parts`,
        jsonRequest("PUT", { parts: uploaded }),
      ));
    }
    if (uploadedBytes !== file.size || completedParts.size !== session.total_parts) throw new Error("S3 upload is incomplete.");
    addLog("AWS", "Direct upload complete; import queued");
    setPhase("importing");
    setStatusMessage("Decoding APK with Apktool...");
    const job = await apiJson<Job>(
      `/v1/uploads/${session.upload_id}/complete?authorized=true`,
      jsonRequest("POST", { parts: [...completedParts.values()].sort((a, b) => a.part_number - b.part_number) }, key),
    );
    const completed = await pollJob(job, 30, 48, "Decoding APK");
    const imported = await apiJson<Project>(`/v1/projects/${completed.project_id}`);
    setProject(imported);
    addLog("IMPORT", "APK decoded and indexed");
    return imported;
  }, [addLog, pollJob]);

  const startPreview = useCallback(async () => {
    if (!selectedFile) {
      fileInputRef.current?.click();
      setError("Please attach an APK first.");
      return;
    }
    if (working) return;
    setError("");
    setReview(null);
    try {
      const activeProject = project ?? await uploadAndImport(selectedFile);
      setPhase("previewing");
      setProgress(50);
      setStatusMessage("AI generating plan & patch preview...");
      addLog("AI", "Preparing grounded change plan");
      const job = await apiJson<Job>(
        `/v1/projects/${activeProject.id}/workflow/prepare`,
        jsonRequest("POST", {
          user_request: request.trim(),
          allow_ai_upload: true,
          revision: activeProject.workspace_revision,
          model: selectedModel,
        }, crypto.randomUUID()),
      );
      const completed = await pollJob(job, 50, 88, "Generating Patch");
      const result = (completed.result_data?.result || {}) as Record<string, unknown>;
      if (result.plan_id && result.unsupported) {
        const unsupportedPlan = await apiJson<Record<string, unknown>>(
          `/v1/projects/${activeProject.id}/plans/${String(result.plan_id)}`,
        );
        const reasons = Array.isArray(unsupportedPlan.unsupported_aspects)
          ? unsupportedPlan.unsupported_aspects.map(String).filter(Boolean)
          : [];
        throw new Error(reasons.join(" ") || "NOIR could not find enough evidence to make this change safely.");
      }
      if (!result.plan_id || !result.patch_id) throw new Error("The backend did not return a reviewable patch.");
      const [plan, patch, diff] = await Promise.all([
        apiJson<Record<string, unknown>>(`/v1/projects/${activeProject.id}/plans/${String(result.plan_id)}`),
        apiJson<Record<string, unknown>>(`/v1/projects/${activeProject.id}/patches/${String(result.patch_id)}`),
        apiJson<{ diff?: Array<Record<string, unknown>> }>(`/v1/projects/${activeProject.id}/patches/${String(result.patch_id)}/diff`),
      ]);
      const operations = Array.isArray(patch.operations) ? patch.operations as Array<Record<string, unknown>> : [];
      const paths = [...new Set(operations.map((operation) => String(operation.relative_path || "")).filter(Boolean))];
      setReview({
        planId: String(plan.plan_id),
        patchId: String(patch.patch_id),
        planHash: String(plan.plan_hash),
        patchHash: String(patch.patch_hash),
        revision: Number(plan.workspace_revision),
        paths,
        operationCount: operations.length || diff.diff?.length || 0,
      });
      setProgress(100);
      setStatusMessage("Patch ready for approval");
      setPhase("review");
      addLog("REVIEW", "Exact patch ready for approval");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Preview failed.";
      setError(message);
      setPhase("error");
      addLog("ERROR", message);
    }
  }, [addLog, project, request, selectedFile, selectedModel, uploadAndImport, working, pollJob]);

  const approveBuild = useCallback(async () => {
    if (!project || !review || phase !== "review") return;
    setPhase("building");
    setProgress(5);
    setStatusMessage("Rebuilding APK with Apktool & signing...");
    setError("");
    addLog("APPROVE", "Exact plan and patch approved");
    try {
      const job = await apiJson<Job>(
        `/v1/projects/${project.id}/workflow/finish`,
        jsonRequest("POST", {
          plan_id: review.planId,
          patch_id: review.patchId,
          plan_hash: review.planHash,
          patch_hash: review.patchHash,
          revision: review.revision,
          confirm: true,
        }, crypto.randomUUID()),
      );
      const completed = await pollJob(job, 8, 94, "Rebuilding & Signing");
      const result = (completed.result_data?.result || {}) as Record<string, string>;
      if (!result.build_id) throw new Error("The backend did not return a verified build.");
      setBuildId(result.build_id);
      setProgress(100);
      setStatusMessage("Build complete & signed!");
      setPhase("complete");
      addLog("VERIFY", "Signed APK verified and stored in S3");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Build failed.";
      setError(message);
      setPhase("error");
      addLog("ERROR", message);
    }
  }, [addLog, phase, pollJob, project, review]);

  const reset = useCallback(() => {
    setPhase("empty");
    setSelectedFile(null);
    setProgress(0);
    setStatusMessage("");
    setProject(null);
    setReview(null);
    setBuildId("");
    setError("");
    eventCursor.current = null;
    lastJobState.current = "";
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  const selectedModelObj = models.find((m) => m.id === selectedModel) || models[0];

  return (
    <div className="stage">
      {/* Background Video (No audio, autoplay, loop, object-fit: cover) */}
      <video
        className="stage-video"
        autoPlay
        muted
        loop
        playsInline
        src="/k_no_logo_final.mp4"
      />
      <div className="stage-overlay" />

      {/* Main Responsive Frame */}
      <div className="frame">
        {/* Navigation Bar */}
        <header className="nav">
          <a href="#" className="brand" aria-label="NOIR home" onClick={(e) => { e.preventDefault(); reset(); }}>
            <svg className="brand-mark" viewBox="0 0 34 34" aria-hidden="true">
              <circle cx="17" cy="17" r="17" fill="#9C86CE" />
              <circle cx="17" cy="17" r="8.6" fill="#FFFFFF" />
              <circle cx="17" cy="17" r="3.7" fill="#151519" />
            </svg>
            <span className="brand-name">NOIR</span>
            <span
              className={`brand-status-dot ${backendOnline ? "" : "offline"}`}
              title={backendOnline ? "Backend Online" : "Backend Offline"}
            />
          </a>

          <nav className="links" aria-label="Main Navigation">
            <a href="#features">Features</a>
            <a href="#docs">Docs</a>
            <a href="#status" onClick={(e) => { e.preventDefault(); setLogsDrawerOpen(true); }}>
              Status {backendOnline ? "· Online" : "· Offline"}
            </a>
          </nav>

          <button
            type="button"
            className="cta-button"
            onClick={reset}
            aria-label="Start New Modification"
          >
            <span>{selectedFile ? "Start Over" : "Get Started"}</span>
          </button>
        </header>

        {/* Hero Section */}
        <main className="hero">
          <h1 className="h1">Describe an APK change. We&apos;ll build it.</h1>

          {/* Composer Card */}
          <div
            className={`composer-card ${isDragOver ? "drag-active" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
            onDragLeave={() => setIsDragOver(false)}
            onDrop={handleDrop}
          >
            {/* Hidden File Input */}
            <input
              type="file"
              ref={fileInputRef}
              accept=".apk,application/vnd.android.package-archive"
              onChange={handleFileInput}
              style={{ display: "none" }}
            />

            {/* Attached File Badge */}
            {selectedFile && (
              <div className="attached-badge">
                <FileArchive size={13} className="text-orange-400" />
                <span className="badge-name">{selectedFile.name}</span>
                <span className="badge-size">({(selectedFile.size / 1024 / 1024).toFixed(1)} MB)</span>
                {!working && (
                  <button
                    type="button"
                    className="remove-btn"
                    onClick={(e) => { e.stopPropagation(); reset(); }}
                    aria-label="Remove attached file"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>
            )}

            {/* Textarea Input */}
            <textarea
              className="prompt-ta"
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              placeholder="Describe what you want to change in the APK (e.g. rename app, add toast on launch, ping server)..."
              disabled={working || phase === "complete"}
              rows={2}
            />

            {/* Status / Progress Indicator */}
            {working && (
              <div className="status-bar">
                <div className="status-label">
                  <span>{statusMessage}</span>
                  <span>{progress}%</span>
                </div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${progress}%` }} />
                </div>
              </div>
            )}

            {/* Exact Patch Ready Review Badge */}
            {phase === "review" && review && (
              <div className="patch-review-pane">
                <div className="patch-info">
                  <span>Exact Patch Ready</span>
                  <strong>{review.operationCount} operation{review.operationCount === 1 ? "" : "s"}</strong>
                  <small>{review.paths.join(" · ") || "Grounded smali modifications"}</small>
                </div>
                <button
                  type="button"
                  className="approve-btn"
                  onClick={approveBuild}
                >
                  Approve &amp; Rebuild
                </button>
              </div>
            )}

            {/* Success Pane */}
            {phase === "complete" && project && buildId && (
              <div className="complete-pane">
                <div className="flex items-center gap-2 text-emerald-400">
                  <CheckCircle2 size={18} />
                  <div>
                    <strong className="block text-white text-sm">Verified APK Ready</strong>
                    <span className="text-xs text-emerald-300/80">Signed, aligned, and ready to install</span>
                  </div>
                </div>
                <a
                  className="download-link"
                  href={`/api/noir/v1/projects/${project.id}/builds/${buildId}/download?artifact=signed`}
                  download
                >
                  <Download size={14} /> Download APK
                </a>
              </div>
            )}

            {/* Error Notification */}
            {error && (
              <div className="error-pane">
                <span>{error}</span>
                <button
                  type="button"
                  onClick={() => setError("")}
                  className="text-red-300 hover:text-white"
                >
                  <X size={12} />
                </button>
              </div>
            )}

            {/* Toolbar Row */}
            <div className="tools-row">
              {/* Quick Suggestion Chips */}
              <div className="chips-group">
                {quickChips.map((chip) => (
                  <button
                    key={chip.label}
                    type="button"
                    className="chip-btn"
                    onClick={() => setRequest(chip.prompt)}
                    disabled={working || phase === "complete"}
                  >
                    <span>{chip.label}</span>
                  </button>
                ))}
              </div>

              {/* Right Action Cluster */}
              <div className="desktop-right-cluster">
                {/* Model Selector Popover */}
                <div className="relative">
                  <button
                    type="button"
                    className="model-trigger"
                    onClick={() => setModelDropdownOpen((v) => !v)}
                    disabled={working || phase === "complete"}
                    aria-label="Select AI Model"
                  >
                    <span>{selectedModelObj.label}</span>
                    <ChevronDown size={11} className="opacity-80" />
                  </button>

                  {modelDropdownOpen && (
                    <div className="model-dropdown">
                      {models.map((m) => (
                        <div
                          key={m.id}
                          className={`model-item ${selectedModel === m.id ? "active" : ""}`}
                          onClick={() => {
                            setSelectedModel(m.id);
                            setModelDropdownOpen(false);
                          }}
                        >
                          <span className="font-medium">{m.label}</span>
                          <span className="model-tag">{m.note}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Paperclip Attach Button */}
                <button
                  type="button"
                  className={`attach-btn ${selectedFile ? "has-file" : ""}`}
                  onClick={() => fileInputRef.current?.click()}
                  disabled={working || phase === "complete"}
                  title={selectedFile ? `Selected: ${selectedFile.name}` : "Attach APK file"}
                  aria-label="Attach APK file"
                >
                  <svg className="w-5 h-5" viewBox="0 0 20 20" fill="none" stroke="currentColor">
                    <path
                      d="M14.5 6.5L7.91421 13.0858C6.74264 14.2574 4.84315 14.2574 3.67157 13.0858C2.5 11.9142 2.5 10.0147 3.67157 8.84315L10.2574 2.25736C11.0384 1.47631 12.3047 1.47631 13.0858 2.25736C13.8668 3.03841 13.8668 4.30474 13.0858 5.08579L6.5 11.6716C6.10948 12.0621 5.47631 12.0621 5.08579 11.6716C4.69526 11.281 4.69526 10.6479 5.08579 10.2574L11 4.34315"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>

                {/* Send Button (Orange Circle) */}
                <button
                  type="button"
                  className="send-btn"
                  onClick={phase === "review" ? approveBuild : startPreview}
                  disabled={working || (!selectedFile && !request.trim())}
                  aria-label="Submit APK Change"
                >
                  {working ? (
                    <RefreshCw className="animate-spin text-white w-3.5 h-3.5" />
                  ) : (
                    <ArrowUp className="text-white w-3.5 h-3.5" />
                  )}
                </button>
              </div>
            </div>
          </div>
        </main>

        {/* Footer Partners / Proof */}
        <footer className="proof">
          <p className="by">Built with NOIR Dalvik/Smali compiler</p>
          <div className="logos">
            <span className="logo logo-google" aria-label="Google">
              <svg viewBox="0 0 272 92" aria-hidden="true" fill="currentColor">
                <path d="M115.75 47.18c0 12.77-9.99 22.18-22.25 22.18s-22.25-9.41-22.25-22.18C71.25 34.32 81.24 25 93.5 25s22.25 9.32 22.25 22.18zm-9.74 0c0-7.98-5.79-13.44-12.51-13.44S80.99 39.2 80.99 47.18c0 7.9 5.79 13.44 12.51 13.44s12.51-5.55 12.51-13.44z"/>
                <path d="M163.75 47.18c0 12.77-9.99 22.18-22.25 22.18s-22.25-9.41-22.25-22.18c0-12.85 9.99-22.18 22.25-22.18s22.25 9.32 22.25 22.18zm-9.74 0c0-7.98-5.79-13.44-12.51-13.44s-12.51 5.46-12.51 13.44c0 7.9 5.79 13.44 12.51 13.44s12.51-5.55 12.51-13.44z"/>
                <path d="M209.75 26.34v39.82c0 16.38-9.66 23.07-21.08 23.07-10.75 0-17.22-7.19-19.66-13.07l8.48-3.53c1.51 3.61 5.21 7.87 11.17 7.87 7.31 0 11.84-4.51 11.84-13v-3.19h-.34c-2.18 2.69-6.38 5.04-11.68 5.04-11.09 0-21.25-9.66-21.25-22.09 0-12.52 10.16-22.26 21.25-22.26 5.29 0 9.49 2.35 11.68 4.96h.34v-3.61h9.25zm-8.56 20.92c0-7.81-5.21-13.52-11.84-13.52-6.72 0-12.35 5.71-12.35 13.52 0 7.73 5.63 13.36 12.35 13.36 6.63 0 11.84-5.63 11.84-13.36z"/>
                <path d="M225 3v65h-9.5V3h9.5z"/>
                <path d="M262.02 54.48l7.56 5.04c-2.44 3.61-8.32 9.83-18.48 9.83-12.6 0-22.01-9.74-22.01-22.18 0-13.19 9.49-22.18 20.92-22.18 11.51 0 17.14 9.16 18.98 14.11l1.01 2.52-29.65 12.28c2.27 4.45 5.8 6.72 10.75 6.72 4.96 0 8.4-2.44 10.92-6.14zm-23.27-7.98l19.82-8.23c-1.09-2.77-4.37-4.7-8.23-4.7-4.95 0-11.84 4.37-11.59 12.93z"/>
                <path d="M35.29 41.41V32H67c.31 1.64.47 3.58.47 5.68 0 7.06-1.93 15.79-8.15 22.01-6.05 6.3-13.78 9.66-24.02 9.66C16.32 69.35.36 53.89.36 34.91.36 15.93 16.32.47 35.3.47c10.5 0 17.98 4.12 23.6 9.49l-6.64 6.64c-4.03-3.78-9.49-6.72-16.97-6.72-13.86 0-24.7 11.17-24.7 25.03 0 13.86 10.84 25.03 24.7 25.03 8.99 0 14.11-3.61 17.39-6.89 2.66-2.66 4.41-6.46 5.1-11.65l-22.49.01z"/>
              </svg>
            </span>

            <span className="logo logo-cisco" aria-label="Cisco">
              <svg viewBox="0 0 216 114" aria-hidden="true" fill="currentColor">
                <g fill="currentColor">
                  <rect x="0" y="30.7" width="9.4" height="29" rx="4.7"/>
                  <rect x="25.8" y="17.8" width="9.4" height="41.9" rx="4.7"/>
                  <rect x="51.6" y="0.1" width="9.4" height="59.6" rx="4.7"/>
                  <rect x="77.5" y="17.8" width="9.4" height="41.9" rx="4.7"/>
                  <rect x="103.3" y="30.7" width="9.4" height="29" rx="4.7"/>
                  <rect x="129.1" y="17.8" width="9.4" height="41.9" rx="4.7"/>
                  <rect x="155" y="0.1" width="9.4" height="59.6" rx="4.7"/>
                  <rect x="180.8" y="17.8" width="9.4" height="41.9" rx="4.7"/>
                  <rect x="206.6" y="30.7" width="9.4" height="29" rx="4.7"/>
                </g>
                <path d="M48.07 76.4c-.89-.26-4.18-1.35-8.64-1.35-11.52 0-19.98 8.22-19.98 19.43 0 12.09 9.34 19.44 19.98 19.44 4.23 0 7.46-1 8.64-1.34v-10.08c-.41.23-3.5 2-7.96 2-6.31 0-10.38-4.45-10.38-10.02 0-5.75 4.25-10.02 10.38-10.02 4.53 0 7.58 1.81 7.96 2.01V76.4zm22.49 36.85h-9.47V75.72h9.47v37.53zm35.92-37.01c-.28-.08-4.62-1.2-9.23-1.2-8.73 0-13.99 4.71-13.99 11.73 0 6.22 4.4 9.32 9.68 10.98.58.2 1.44.47 2.02.66 2.35.74 4.22 1.83 4.22 3.74 0 2.12-2.17 3.5-6.88 3.5-4.14 0-8.11-1.18-8.94-1.4v8.64c.46.1 5.18 1.02 10.22 1.02 7.25 0 15.54-3.16 15.54-12.59 0-4.57-2.8-8.78-8.95-10.74l-4.61-1.49c-1.56-.49-4.34-1.29-4.34-3.57 0-1.81 2.06-3.08 5.86-3.08 3.27 0 7.26 1.1 7.4 1.15v-8.85zm40.45.16c-.89-.26-4.18-1.35-8.64-1.35-11.53 0-19.99 8.22-19.99 19.43 0 12.09 9.34 19.44 19.99 19.44 4.23 0 7.46-1 8.64-1.34v-10.08c-.41.23-3.5 2-7.96 2-6.31 0-10.38-4.45-10.38-10.02 0-5.75 4.25-10.02 10.38-10.02 4.53 0 7.58 1.81 7.96 2.01V76.4zm29.83 18.08c0 5.46-4.18 9.88-9.8 9.88-5.61 0-9.79-4.42-9.79-9.88 0-5.45 4.17-9.87 9.79-9.87 5.62 0 9.8 4.42 9.8 9.87zm-9.8-19.43c-11.54 0-19.82 8.71-19.82 19.43 0 10.74 8.28 19.44 19.82 19.44 11.55 0 19.84-8.7 19.84-19.44 0-10.72-8.29-19.43-19.84-19.43z"/>
              </svg>
            </span>

            <span className="logo logo-adobe" aria-label="Adobe">
              <svg viewBox="0 0 66.25 16.75" aria-hidden="true" fill="currentColor">
                <path d="m9.057 5.43-2.988 7.423h3.593l1.258 3.593H.025L6.794.272h4.711l6.747 16.174h-4.794Zm49.586 3.694.021-.082c.041-.161.091-.306.146-.431.2-.445.462-.78.781-.995.321-.216.679-.326 1.063-.326.336 0 .66.086.964.253.304.168.563.431.771.782.119.203.203.446.248.722l.014.077zm7.11-1.542c-.157-.355-.342-.685-.553-.986-.211-.302-.45-.576-.713-.821-.527-.488-1.131-.864-1.796-1.116-.664-.252-1.365-.38-2.084-.38-1.055 0-2.039.252-2.926.749-.886.496-1.606 1.209-2.141 2.119-.535.909-.807 1.999-.807 3.239 0 .98.179 1.868.53 2.639.351.772.843 1.43 1.462 1.957.619.528 1.345.936 2.156 1.211.812.276 1.706.416 2.658.416.689 0 1.374-.077 2.035-.231.646-.15 1.271-.377 1.859-.674V12.54c-.603.274-1.185.492-1.733.647-.578.164-1.179.248-1.789.248-.658 0-1.243-.112-1.739-.332-.498-.222-.901-.563-1.194-1.015-.089-.137-.167-.294-.233-.467l-.034-.091h7.425c.014-.205.032-.43.054-.684.023-.26.035-.526.035-.787 0-.933-.159-1.766-.472-2.477m-16.17 4.399c-.123.206-.266.388-.428.547-.162.157-.343.29-.541.398-.397.213-.823.322-1.267.322-.258 0-.522-.034-.785-.101l-.05-.013v-5.34l.051-.012c.282-.064.584-.096.9-.096.444 0 .858.096 1.231.287.375.191.681.491.913.889.229.397.346.91.346 1.524 0 .646-.125 1.183-.37 1.595m3.819-4.329c-.329-.756-.782-1.391-1.347-1.887s-1.196-.868-1.876-1.105c-.681-.238-1.383-.358-2.086-.358-.537 0-1.041.056-1.499.165l-.082.02V.094h-4.037v15.584c.725.329 1.484.576 2.253.734.785.162 1.65.244 2.573.244.826 0 1.646-.136 2.436-.404.788-.268 1.505-.671 2.133-1.199.626-.527 1.127-1.19 1.485-1.969.359-.78.542-1.703.542-2.745 0-1.026-.167-1.931-.495-2.687M37.37 11.999c-.1.206-.215.388-.346.543-.131.154-.276.281-.436.382-.32.199-.677.301-1.061.301-.383 0-.74-.102-1.06-.301-.321-.201-.583-.511-.782-.925-.198-.41-.297-.937-.297-1.567 0-.644.099-1.175.297-1.578.199-.406.461-.713.782-.913.32-.199.677-.301 1.06-.301.384 0 .74.102 1.061.301.32.2.583.507.782.913.197.404.298.935.298 1.578 0 .629-.101 1.156-.298 1.567m3.412-4.957c-.55-.917-1.293-1.621-2.21-2.095-.46-.237-.945-.415-1.453-.535-.509-.119-1.04-.18-1.592-.18-1.101 0-2.126.241-3.044.715-.917.474-1.664 1.178-2.222 2.096-.558.916-.84 2.056-.84 3.389 0 1.333.282 2.474.84 3.39.558.917 1.306 1.626 2.222 2.108.918.482 1.942.726 3.044.726 1.087 0 2.107-.244 3.033-.726.924-.482 1.672-1.191 2.222-2.107.55-.917.829-2.058.829-3.391 0-1.333-.279-2.473-.829-3.39m-16.239 6.071-.051.013c-.284.066-.578.099-.878.099-.428 0-.838-.101-1.218-.299-.383-.199-.694-.506-.925-.911-.229-.405-.346-.929-.346-1.559 0-.645.112-1.181.334-1.593.223-.415.535-.722.927-.913.387-.191.824-.287 1.299-.287.275 0 .547.032.807.095l.051.013zm0-13.019v4.31l-.079-.015c-.38-.073-.776-.11-1.175-.11-.765 0-1.527.131-2.261.392-.734.26-1.396.659-1.97 1.186-.572.528-1.042 1.183-1.393 1.948-.352.763-.53 1.663-.53 2.674 0 1.072.193 2.01.575 2.789.382.78.89 1.427 1.508 1.924.62.497 1.325.869 2.098 1.106.775.237 1.564.358 2.345.358.953 0 1.834-.086 2.619-.255.769-.166 1.535-.417 2.276-.746V.094Z"/>
              </svg>
            </span>
          </div>
        </footer>
      </div>

      {/* Floating Bottom Drawer for Live Activity Logs */}
      <button
        type="button"
        className="logs-toggle-btn"
        onClick={() => setLogsDrawerOpen((v) => !v)}
      >
        <Terminal size={12} />
        <span>Activity ({logs.length})</span>
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
      </button>

      {logsDrawerOpen && (
        <div className="logs-drawer">
          <div className="logs-header">
            <span className="flex items-center gap-2">
              <Terminal size={13} /> Real-time Audit &amp; Build Activity
            </span>
            <button
              type="button"
              onClick={() => setLogsDrawerOpen(false)}
              className="text-slate-400 hover:text-white"
            >
              <X size={13} />
            </button>
          </div>
          <div className="logs-content">
            {logs.map((log, idx) => (
              <p key={`${log.time}-${log.tag}-${idx}`}>
                <time>{log.time}</time>
                <b>[{log.tag}]</b>
                <span>{log.message}</span>
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
