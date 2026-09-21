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
  History,
  RefreshCw,
  ShieldCheck,
  Terminal,
  Trash2,
  X,
} from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

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
type HistoryRecord = {
  id: string;
  projectId: string;
  jobId?: string;
  buildId?: string;
  filename: string;
  prompt: string;
  operations: string[];
  status: string;
  createdAt: string;
  updatedAt: string;
};
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
const HISTORY_KEY = "noir.browser.history.v1";

const operationLabels = (prompt: string) => {
  const labels: string[] = [];
  if (/\b(rename|change\s+(?:the\s+)?app\s+name|name\s+the\s+app)\b/i.test(prompt)) labels.push("Rename");
  if (/https?:\/\//i.test(prompt) && /\b(launch|startup|open|redirect|website|site)\b/i.test(prompt)) labels.push("Launch redirect");
  if (/\b(toast|flash|pop-?up|message)\b/i.test(prompt)) {
    labels.push(/\b(tap|touch|click|interact|interaction)\b/i.test(prompt) ? "Interaction toast" : "Startup message");
  }
  return labels;
};

const isDeterministicPrompt = (prompt: string) => {
  const operations = operationLabels(prompt);
  const unsupported = /\b(?:add|grant|remove|revoke)\s+(?:the\s+)?(?:[\w.]+\s+){0,3}permission|\b(?:layout|button\s+colou?r|native\s+code|unity|il2cpp|service|receiver)\b/i.test(prompt);
  return operations.length > 0 && !unsupported;
};

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
  const [history, setHistory] = useState<HistoryRecord[]>([]);

  const working = ["uploading", "importing", "previewing", "building"].includes(phase);
  const deterministic = isDeterministicPrompt(request);

  const saveHistory = useCallback((records: HistoryRecord[]) => {
    const limited = records.slice(0, 30);
    setHistory(limited);
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(limited));
  }, []);

  const upsertHistory = useCallback((record: HistoryRecord) => {
    setHistory((current) => {
      const next = [record, ...current.filter((item) => item.id !== record.id)].slice(0, 30);
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

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

  useEffect(() => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(HISTORY_KEY) || "[]") as HistoryRecord[];
      const records = Array.isArray(parsed) ? parsed.slice(0, 30) : [];
      setHistory(records);
      void Promise.all(records.map(async (record) => {
        if (!record.jobId || ["complete", "failed", "cancelled"].includes(record.status)) return record;
        try {
          const job = await apiJson<Job>(`/v1/jobs/${record.jobId}`);
          const result = (job.result_data?.result || {}) as Record<string, unknown>;
          return {
            ...record,
            status: job.state === "succeeded" && result.build_id ? "complete" : job.state,
            buildId: result.build_id ? String(result.build_id) : record.buildId,
            updatedAt: new Date().toISOString(),
          };
        } catch {
          return record;
        }
      })).then((reconciled) => saveHistory(reconciled));
    } catch {
      window.localStorage.removeItem(HISTORY_KEY);
    }
  }, [saveHistory]);

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
        setStatusMessage(`${labelPrefix}: ${job.stage.replace(/_/g, " ")} (${job.state})...`);
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

  const uploadAndImport = useCallback(async (file: File, prompt: string) => {
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
    const historyId = session.project_id;
    upsertHistory({
      id: historyId,
      projectId: session.project_id,
      filename: file.name,
      prompt,
      operations: operationLabels(prompt),
      status: "uploading",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });

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
      jsonRequest("POST", {
        parts: [...completedParts.values()].sort((a, b) => a.part_number - b.part_number),
        user_request: prompt,
      }, key),
    );
    upsertHistory({
      id: historyId,
      projectId: session.project_id,
      jobId: job.job_id,
      filename: file.name,
      prompt,
      operations: operationLabels(prompt),
      status: job.state,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
    const completed = await pollJob(job, 30, 48, "Decoding APK");
    const imported = await apiJson<Project>(`/v1/projects/${completed.project_id}`);
    setProject(imported);
    addLog("IMPORT", "APK decoded and indexed");
    return imported;
  }, [addLog, pollJob, upsertHistory]);

  const startPreview = useCallback(async () => {
    if (!selectedFile && !project) {
      fileInputRef.current?.click();
      setError("Please attach an APK first.");
      return;
    }
    if (working) return;
    setError("");
    setReview(null);
    try {
      const activeProject = project ?? await uploadAndImport(selectedFile as File, request.trim());
      setPhase("previewing");
      setProgress(50);
      setStatusMessage(deterministic ? "Generating deterministic patch preview..." : "AI generating plan & patch preview...");
      addLog(deterministic ? "LOCAL" : "AI", deterministic ? "Preparing deterministic operation spec" : "Preparing grounded change plan");
      const job = await apiJson<Job>(
        `/v1/projects/${activeProject.id}/workflow/prepare`,
        jsonRequest("POST", {
          user_request: request.trim(),
          allow_ai_upload: !deterministic,
          revision: activeProject.workspace_revision,
          model: selectedModel,
        }, crypto.randomUUID()),
      );
      const completed = await pollJob(job, 50, 88, "Generating Patch");
      upsertHistory({
        id: activeProject.id,
        projectId: activeProject.id,
        jobId: job.job_id,
        filename: activeProject.original_filename || selectedFile?.name || "APK",
        prompt: request.trim(),
        operations: operationLabels(request),
        status: completed.state,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      });
      const result = (completed.result_data?.result || {}) as Record<string, unknown>;
      if (result.build_id) {
        setBuildId(String(result.build_id));
        setProgress(100);
        setStatusMessage("Build complete & signed!");
        setPhase("complete");
        addLog("VERIFY", "Signed APK verified and ready");
        return;
      }
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
      const reviewData = {
        planId: String(plan.plan_id),
        patchId: String(patch.patch_id),
        planHash: String(plan.plan_hash),
        patchHash: String(patch.patch_hash),
        revision: Number(plan.workspace_revision),
        paths,
        operationCount: operations.length || diff.diff?.length || 0,
      };
      setReview(reviewData);
      addLog("REVIEW", "Exact patch generated; automatically proceeding to rebuild & sign");

      // Automated End-to-End Progression: Patch generation -> Rebuild & Sign (R5)
      setPhase("building");
      setProgress(88);
      setStatusMessage("Rebuilding APK with Apktool & signing...");
      addLog("APPROVE", "Patch accepted; executing automated rebuild & signing");

      const finishJob = await apiJson<Job>(
        `/v1/projects/${activeProject.id}/workflow/finish`,
        jsonRequest("POST", {
          plan_id: reviewData.planId,
          patch_id: reviewData.patchId,
          plan_hash: reviewData.planHash,
          patch_hash: reviewData.patchHash,
          revision: reviewData.revision,
          confirm: true,
        }, crypto.randomUUID()),
      );
      const finishCompleted = await pollJob(finishJob, 90, 99, "Rebuilding & Signing");
      const resultData = (finishCompleted.result_data?.result || {}) as Record<string, string>;
      if (!resultData.build_id) throw new Error("The backend did not return a verified build.");
      setBuildId(resultData.build_id);
      setProgress(100);
      setStatusMessage("Build complete & signed!");
      setPhase("complete");
      addLog("VERIFY", "Signed APK verified and stored in S3");
      upsertHistory({
        id: activeProject.id,
        projectId: activeProject.id,
        jobId: finishJob.job_id,
        buildId: resultData.build_id,
        filename: activeProject.original_filename || selectedFile?.name || "APK",
        prompt: request.trim(),
        operations: operationLabels(request),
        status: "complete",
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      });
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Workflow failed.";
      setError(message);
      setPhase("error");
      addLog("ERROR", message);
    }
  }, [addLog, deterministic, project, request, selectedFile, selectedModel, uploadAndImport, upsertHistory, working, pollJob]);

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

  const resumeHistory = useCallback(async (record: HistoryRecord) => {
    setError("");
    setRequest(record.prompt);
    try {
      const savedProject = await apiJson<Project>(`/v1/projects/${record.projectId}`);
      setProject(savedProject);
      if (record.buildId) {
        setBuildId(record.buildId);
        setPhase("complete");
        return;
      }
      if (record.jobId && !["failed", "cancelled"].includes(record.status)) {
        setPhase("previewing");
        const job = await apiJson<Job>(`/v1/jobs/${record.jobId}`);
        const completed = ["succeeded", "failed", "cancelled"].includes(job.state)
          ? job
          : await pollJob(job, 20, 88, "Resuming");
        const result = (completed.result_data?.result || {}) as Record<string, unknown>;
        if (result.build_id) {
          setBuildId(String(result.build_id));
          setPhase("complete");
          upsertHistory({ ...record, buildId: String(result.build_id), status: "complete", updatedAt: new Date().toISOString() });
          return;
        }
      }
      setPhase("ready");
      setStatusMessage("Workspace restored. Submit to retry from the clean revision.");
    } catch (caught) {
      setPhase("error");
      setError(caught instanceof Error ? caught.message : "Unable to resume this build.");
    }
  }, [pollJob, upsertHistory]);

  const removeHistory = useCallback((id: string) => {
    saveHistory(history.filter((record) => record.id !== id));
  }, [history, saveHistory]);

  const selectedModelObj = models.find((m) => m.id === selectedModel) || models[0];

  return (
    <div className="stage">
      {/* Background Video: something_bright_no_logo_no_audio.mp4 (No audio, autoplay, loop, cover) */}
      <video
        className="stage-video"
        autoPlay
        muted
        loop
        playsInline
        src="/something_bright_no_logo_no_audio.mp4"
      />
      <div className="stage-overlay" />

      {/* Main Responsive Frame */}
      <div className="frame">
        {/* Navigation Bar: Actual NOIR Brand Only */}
        <header className="nav">
          <a
            href="#"
            className="brand"
            aria-label="NOIR home"
            onClick={(e) => { e.preventDefault(); reset(); }}
          >
            <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
              <rect width="32" height="32" rx="7" fill="#080909" stroke="rgba(217, 255, 67, 0.45)" strokeWidth="1.2" />
              <path d="M8 23V9l16 14V9" fill="none" stroke="#d9ff43" strokeWidth="3" strokeLinejoin="bevel" />
            </svg>
            <span className="brand-name">NOIR</span>
          </a>
          <Sheet>
            <SheetTrigger asChild>
              <button type="button" className="history-trigger">
                <History size={15} /> History
              </button>
            </SheetTrigger>
            <SheetContent className="history-sheet">
              <SheetHeader className="history-head">
                <SheetTitle>History</SheetTitle>
                <SheetDescription>Builds saved in this browser.</SheetDescription>
              </SheetHeader>
              <div className="history-list">
                {history.length === 0 ? (
                  <p className="history-empty">Your recent APK builds will appear here.</p>
                ) : history.map((record) => (
                  <article className="history-card" key={record.id}>
                    <div className="history-card-top">
                      <div>
                        <strong>{record.filename}</strong>
                        <span>{new Date(record.updatedAt).toLocaleString()}</span>
                      </div>
                      <span className={`history-status status-${record.status}`}>{record.status}</span>
                    </div>
                    <p>{record.prompt}</p>
                    <div className="history-ops">
                      {record.operations.map((operation) => <span key={operation}>{operation}</span>)}
                    </div>
                    <div className="history-actions">
                      {record.buildId ? (
                        <a href={`/api/noir/v1/projects/${record.projectId}/builds/${record.buildId}/download?artifact=signed`} download>
                          <Download size={13} /> Download
                        </a>
                      ) : (
                        <button type="button" onClick={() => void resumeHistory(record)}>
                          <RefreshCw size={13} /> {record.status === "failed" ? "Retry" : "Resume"}
                        </button>
                      )}
                      <button type="button" className="history-remove" onClick={() => removeHistory(record.id)} aria-label={`Remove ${record.filename} from this browser`}>
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            </SheetContent>
          </Sheet>
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

            <div className={`execution-indicator ${deterministic ? "is-local" : "is-ai"}`}>
              <ShieldCheck size={13} />
              <span>{deterministic ? "Deterministic · no AI" : "Advanced request · AI assisted"}</span>
            </div>

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
                <div className="flex items-center gap-2 text-emerald-600">
                  <CheckCircle2 size={18} />
                  <div>
                    <strong className="block text-slate-900 text-sm font-semibold">Verified APK Ready</strong>
                    <span className="text-xs text-emerald-700">Signed, aligned, and ready to install</span>
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
                  className="text-red-500 hover:text-red-700"
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
                {!deterministic && <div className="relative">
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
                </div>}

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
      </div>

      {/* Floating Bottom Drawer for Live Activity Logs */}
      <button
        type="button"
        className="logs-toggle-btn"
        onClick={() => setLogsDrawerOpen((v) => !v)}
      >
        <Terminal size={12} />
        <span>Activity ({logs.length})</span>
        <span className={`w-1.5 h-1.5 rounded-full ${backendOnline ? "bg-emerald-400" : "bg-amber-400"} animate-pulse`} />
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
