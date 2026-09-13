"use client";

import {
  ChangeEvent,
  DragEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  Check,
  CheckCircle2,
  Download,
  FileArchive,
  LockKeyhole,
  Radio,
  RotateCcw,
  Terminal,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { NoirApkCoreHero } from "@/components/noir-android-hero";

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

const openingLogs: Log[] = [
  { time: "--:--:--", tag: "AWS", message: "Connecting to NOIR" },
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
  const root = useRef<HTMLElement>(null);
  const feed = useRef<HTMLDivElement>(null);
  const eventCursor = useRef<string | null>(null);
  const lastJobState = useRef("");
  const [phase, setPhase] = useState<Phase>("empty");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [request, setRequest] = useState(
    "Rename the app to NOIR Notes and preserve all permissions.",
  );
  const [progress, setProgress] = useState(0);
  const [logs, setLogs] = useState<Log[]>(openingLogs);
  const [project, setProject] = useState<Project | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [buildId, setBuildId] = useState("");
  const [error, setError] = useState("");
  const [backendOnline, setBackendOnline] = useState(false);
  const [authorized, setAuthorized] = useState(false);

  const addLog = useCallback((tag: string, message: string) => {
    setLogs((current) => [...current, { time: stamp(), tag, message }]);
  }, []);

  const checkBackend = useCallback(async () => {
    try {
      const [health, account] = await Promise.all([
        apiJson<{ status: string; capabilities?: Record<string, boolean> }>("/v1/health"),
        apiJson<{ user_id: string }>("/v1/auth/me"),
      ]);
      const ready = health.status === "ok" && health.capabilities?.import && health.capabilities?.build && Boolean(account.user_id);
      setBackendOnline(Boolean(ready));
      setLogs([{ time: stamp(), tag: ready ? "AWS" : "WAIT", message: ready ? "Backend and build tools online" : "Backend is not build-ready" }]);
    } catch {
      setBackendOnline(false);
      setLogs([{ time: stamp(), tag: "OFF", message: "Backend unavailable" }]);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void checkBackend(), 0);
    return () => window.clearTimeout(timer);
  }, [checkBackend]);

  const loadFile = useCallback((file: File) => {
    if (!file.name.toLowerCase().endsWith(".apk")) {
      setError("Choose an .apk file.");
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
    setAuthorized(false);
    eventCursor.current = null;
    lastJobState.current = "";
    setLogs([{ time: stamp(), tag: "INPUT", message: `${file.name} ready` }]);
  }, [addLog]);

  const syncEvents = useCallback(async (projectId: string) => {
    const query = eventCursor.current ? `?after=${encodeURIComponent(eventCursor.current)}&limit=100` : "?limit=100";
    const data = await apiJson<{ events?: Array<{ event_id: string; stage?: string; severity?: string; message: string }> }>(
      `/v1/projects/${projectId}/events${query}`,
    );
    const events = data.events ?? [];
    if (!events.length) return;
    eventCursor.current = events.at(-1)?.event_id ?? eventCursor.current;
    setLogs((current) => [
      ...current,
      ...events.map((event) => ({
        time: stamp(),
        tag: (event.stage || event.severity || "AWS").slice(0, 8).toUpperCase(),
        message: event.message,
      })),
    ]);
  }, []);

  const pollJob = useCallback(async (initial: Job, from: number, to: number) => {
    let job = initial;
    for (let attempt = 0; attempt < 900; attempt += 1) {
      if (job.project_id) await syncEvents(job.project_id).catch(() => undefined);
      const stateKey = `${job.stage}:${job.state}`;
      if (stateKey !== lastJobState.current) {
        lastJobState.current = stateKey;
        addLog(job.stage.toUpperCase(), job.state);
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
    setProgress(2);
    addLog("HASH", "Verifying APK locally");
    const key = crypto.randomUUID();
    const fileBytes = await file.arrayBuffer();
    const sha256 = hexDigest(await crypto.subtle.digest("SHA-256", fileBytes));
    setProgress(5);
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
        setProgress(5 + Math.round((uploadedBytes / file.size) * 25));
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
    const job = await apiJson<Job>(
      `/v1/uploads/${session.upload_id}/complete?authorized=true`,
      jsonRequest("POST", { parts: [...completedParts.values()].sort((a, b) => a.part_number - b.part_number) }, key),
    );
    const completed = await pollJob(job, 30, 48);
    const imported = await apiJson<Project>(`/v1/projects/${completed.project_id}`);
    setProject(imported);
    addLog("IMPORT", "APK decoded and indexed");
    return imported;
  }, [addLog, pollJob]);

  const startPreview = useCallback(async () => {
    if (!selectedFile || !authorized || ["uploading", "importing", "previewing", "building"].includes(phase)) return;
    setError("");
    setReview(null);
    try {
      const activeProject = project ?? await uploadAndImport(selectedFile);
      setPhase("previewing");
      setProgress(50);
      addLog("AI", "Preparing grounded change plan");
      const job = await apiJson<Job>(
        `/v1/projects/${activeProject.id}/workflow/prepare`,
        jsonRequest("POST", {
          user_request: request.trim(),
          allow_ai_upload: true,
          revision: activeProject.workspace_revision,
        }, crypto.randomUUID()),
      );
      const completed = await pollJob(job, 50, 88);
      const result = (completed.result_data?.result || {}) as Record<string, string>;
      if (!result.plan_id || !result.patch_id) throw new Error("The backend did not return a reviewable patch.");
      const [plan, patch, diff] = await Promise.all([
        apiJson<Record<string, unknown>>(`/v1/projects/${activeProject.id}/plans/${result.plan_id}`),
        apiJson<Record<string, unknown>>(`/v1/projects/${activeProject.id}/patches/${result.patch_id}`),
        apiJson<{ diff?: Array<Record<string, unknown>> }>(`/v1/projects/${activeProject.id}/patches/${result.patch_id}/diff`),
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
      setPhase("review");
      addLog("REVIEW", "Exact patch ready for approval");
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Preview failed.";
      setError(message);
      setPhase("error");
      addLog("ERROR", message);
    }
  }, [addLog, authorized, phase, pollJob, project, request, selectedFile, uploadAndImport]);

  const approveBuild = useCallback(async () => {
    if (!project || !review || phase !== "review") return;
    setPhase("building");
    setProgress(5);
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
      const completed = await pollJob(job, 8, 94);
      const result = (completed.result_data?.result || {}) as Record<string, string>;
      if (!result.build_id) throw new Error("The backend did not return a verified build.");
      setBuildId(result.build_id);
      setProgress(100);
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
    setProject(null);
    setReview(null);
    setBuildId("");
    setProgress(0);
    setError("");
    setAuthorized(false);
    eventCursor.current = null;
    lastJobState.current = "";
    void checkBackend();
  }, [checkBackend]);

  useLayoutEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.registerPlugin(ScrollTrigger);
    const context = gsap.context(() => {
      gsap.from("[data-reveal]", { y: 24, opacity: 0, duration: .9, stagger: .08, ease: "power3.out" });
      gsap.utils.toArray<HTMLElement>("[data-scroll-reveal]").forEach((element) => {
        gsap.from(element, { scrollTrigger: { trigger: element, start: "top 82%", once: true }, y: 36, opacity: 0, duration: .8, ease: "power3.out" });
      });
    }, root);
    return () => context.revert();
  }, []);

  useEffect(() => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [logs]);

  useEffect(() => {
    const modelContext = (document as Document & { modelContext?: { registerTool: (tool: Record<string, unknown>, options?: { signal?: AbortSignal }) => void | Promise<void> } }).modelContext;
    if (!modelContext?.registerTool) return;
    const lifecycle = new AbortController();
    void Promise.resolve(modelContext.registerTool({
      name: "read_noir_workspace",
      title: "Read NOIR workspace",
      description: "Read the connected NOIR workspace state without making changes.",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      execute: async (input: unknown) => {
        if (!input || typeof input !== "object" || Object.keys(input).length > 0) throw new Error("read_noir_workspace does not accept input fields");
        return { phase, backendOnline, file: selectedFile?.name || null, progress, latestLog: logs.at(-1)?.message || null };
      },
    }, { signal: lifecycle.signal })).catch(() => undefined);
    return () => lifecycle.abort();
  }, [backendOnline, logs, phase, progress, selectedFile]);

  const working = ["uploading", "importing", "previewing", "building"].includes(phase);
  const stage = phase === "complete" ? 3 : phase === "building" ? 2 : phase === "review" ? 1 : 0;
  const buttonText = phase === "uploading" ? "Uploading APK…"
    : phase === "importing" ? "Decoding APK…"
      : phase === "previewing" ? "Preparing preview…"
        : phase === "review" ? "Approve patch & build"
          : phase === "building" ? "Building verified APK…"
            : phase === "complete" ? "Build verified"
              : project ? "Retry preview" : "Preview exact changes";

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) loadFile(file);
  };

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (file) loadFile(file);
  };

  return (
    <main ref={root} className="noir-shell">
      <div className="scanline" aria-hidden="true" />
      <header className="site-nav" data-reveal>
        <a className="wordmark" href="#top" aria-label="NOIR home"><span>NOIR</span></a>
        <a className="nav-link" href="#workspace">Workspace</a>
        <div className="nav-status"><span className={`pulse-dot ${backendOnline ? "" : "offline"}`} /> {backendOnline ? "AWS online" : "Connecting"}</div>
      </header>

      <section
        id="top"
        className="landing-hero"
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          event.currentTarget.style.setProperty("--grid-x", `${event.clientX - bounds.left}px`);
          event.currentTarget.style.setProperty("--grid-y", `${event.clientY - bounds.top}px`);
        }}
      >
        <div className="hero-backdrop" aria-hidden="true">
          <svg className="circuit-map" viewBox="0 0 1600 820" preserveAspectRatio="none">
            <path d="M0 168H188L238 218H422L468 172H620" />
            <path d="M1600 142H1390L1338 194H1190L1142 242H1012" />
            <path d="M0 628H205L260 574H424L482 632H650" />
            <path d="M1600 660H1434L1384 610H1220L1168 558H1010" />
            <path d="M800 0V94L748 146V238" />
            <path d="M800 820V742L854 688V604" />
            <circle cx="238" cy="218" r="4" />
            <circle cx="1338" cy="194" r="4" />
            <circle cx="260" cy="574" r="4" />
            <circle cx="1384" cy="610" r="4" />
          </svg>
          <div className="calibration-rings"><i /><i /><i /></div>
          <span className="telemetry telemetry-a">APK / CONTROL PLANE<br />X 04.219 · Y 08.404</span>
          <span className="telemetry telemetry-b">SHA-256<br />INTEGRITY CHANNEL</span>
          <span className="telemetry telemetry-c">REVISION 01<br />BOUNDED CHANGESET</span>
          <span className="telemetry telemetry-d">SIGN / VERIFY<br />OUTPUT SEALED</span>
          <div className="side-scale side-scale-left">{["00", "16", "32", "48", "64"].map((tick) => <span key={tick}>{tick}</span>)}</div>
          <div className="side-scale side-scale-right">{["A", "B", "C", "D", "E"].map((tick) => <span key={tick}>{tick}</span>)}</div>
        </div>
        <div className="hero-layout">
          <div className="hero-object" data-reveal><NoirApkCoreHero /></div>
        </div>

      </section>

      <section className="workspace-section">
        <div className="workspace-intro" data-scroll-reveal><div><p>NOIR / WORKSPACE</p><h2>Make the change.</h2></div></div>
        <section id="workspace" className="workspace" aria-labelledby="workspace-title" data-scroll-reveal>
          <div className="workspace-head">
            <div><p>LIVE AWS WORKSPACE</p><h2 id="workspace-title">APK workspace</h2></div>
            <span className="secure-chip"><LockKeyhole size={13} /> Private</span>
          </div>
          <div className="workspace-body">
            <div className="controls-column">
              <label className={`upload-zone ${selectedFile ? "has-file" : ""}`} htmlFor="apk-input" onDrop={handleDrop} onDragOver={(event) => event.preventDefault()}>
                <input id="apk-input" type="file" accept=".apk,application/vnd.android.package-archive" onChange={handleFile} disabled={working} />
                <span className="upload-icon">{selectedFile ? <Check size={21} /> : <Upload size={21} />}</span>
                <span><strong>{selectedFile?.name || "Drop APK"}</strong><small>{selectedFile ? `${(selectedFile.size / 1024 / 1024).toFixed(1)} MB · local file` : "or browse files"}</small></span>
                <FileArchive size={19} />
              </label>
              <div className="request-block">
                <div className="field-label"><label htmlFor="change-request">Change request</label></div>
                <Textarea id="change-request" className="request-input" value={request} onChange={(event) => setRequest(event.target.value)} disabled={working || phase === "complete"} />
              </div>
              <label className="consent-row" htmlFor="authorization">
                <Checkbox id="authorization" className="consent-check" checked={authorized} onCheckedChange={(value) => setAuthorized(value === true)} disabled={working} />
                <span>I own or may modify this APK and allow bounded AI context.</span>
              </label>
              {review && phase === "review" && (
                <div className="review-card"><span>EXACT PATCH READY</span><strong>{review.operationCount} operation{review.operationCount === 1 ? "" : "s"}</strong><small>{review.paths.join(" · ") || "Grounded file changes"}</small></div>
              )}
              {phase === "complete" && project && buildId && (
                <div className="success-card"><CheckCircle2 size={18} /><span><strong>Verified APK ready</strong><small>Stored privately in S3</small></span></div>
              )}
              {error && <div className="error-card">{error}</div>}
              <Button className={`run-button ${selectedFile && authorized && backendOnline ? "enabled" : ""}`} disabled={!selectedFile || !authorized || !request.trim() || !backendOnline || working || phase === "complete"} onClick={phase === "review" ? approveBuild : startPreview}>
                {working && <span className="button-spinner" />}{buttonText}
              </Button>
              {phase === "complete" && project && buildId && (
                <Button className="download-button" asChild><a href={`/api/noir/v1/projects/${project.id}/builds/${buildId}/download?artifact=signed`}><Download size={14} /> Download APK</a></Button>
              )}
              {(phase === "complete" || phase === "error") && <button className="reset-button" onClick={reset}><RotateCcw size={13} /> Start another</button>}
            </div>

            <div className="console-column">
              <div className="console-head"><span><Terminal size={14} /> Activity</span><span className="live-state"><Radio size={12} /> Live</span></div>
              <div className="stage-strip" aria-label="Workflow stages">
                {["Input", "Review", "Build", "Verify"].map((label, index) => <span key={label} className={index <= stage ? "active" : ""}>{label}{index < 3 && <i />}</span>)}
              </div>
              <Progress value={progress} className="process-progress" />
              <div className="terminal-feed" ref={feed} aria-live="polite">
                {logs.map((log, index) => <p key={`${log.time}-${log.tag}-${index}`}><time>{log.time}</time><b>[{log.tag}]</b><span>{log.message}</span></p>)}
                <span className="cursor-line"><i /> {working ? "processing" : phase === "complete" ? "complete" : phase === "error" ? "attention" : "ready"}</span>
              </div>
            </div>
          </div>
        </section>
      </section>

      <footer><a className="wordmark" href="#top"><span className="mark">N</span><span>NOIR</span></a><p>Draft. Decide. Build.</p><span>© 2026</span></footer>
    </main>
  );
}
