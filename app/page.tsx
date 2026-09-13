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
  ArrowDownRight,
  Check,
  CheckCircle2,
  ChevronRight,
  FileArchive,
  LockKeyhole,
  Radio,
  RotateCcw,
  ShieldCheck,
  Terminal,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";

type Phase = "empty" | "ready" | "previewing" | "review" | "building" | "complete";
type Log = { time: string; tag: string; message: string };

const openingLogs: Log[] = [
  { time: "14:08:02", tag: "SAFE", message: "Private workspace ready" },
  { time: "14:08:04", tag: "WAIT", message: "Drop an authorized APK" },
];

const previewSequence = [
  [16, "UPLOAD", "4 resumable ranges acknowledged"],
  [30, "DECODE", "Apktool workspace created"],
  [45, "SCAN", "Manifest, resources, and Smali indexed"],
  [62, "AI", "Grounded plan generated from real paths"],
  [78, "PATCH", "Deterministic operations validated"],
  [100, "REVIEW", "Exact diff ready for approval"],
] as const;

const buildSequence = [
  [18, "APPLY", "Approved preimage hashes matched"],
  [36, "VALID", "Workspace validation passed"],
  [58, "BUILD", "APK rebuilt from revision 01"],
  [74, "ALIGN", "Archive alignment verified"],
  [88, "SIGN", "Personal signing profile applied"],
  [100, "VERIFY", "Signature and SHA-256 verified"],
] as const;

const wait = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const stamp = () =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());

export default function Home() {
  const root = useRef<HTMLElement>(null);
  const feed = useRef<HTMLDivElement>(null);
  const [phase, setPhase] = useState<Phase>("empty");
  const [fileName, setFileName] = useState("");
  const [fileMeta, setFileMeta] = useState("");
  const [request, setRequest] = useState(
    "Rename the app to NOIR Notes and preserve all permissions.",
  );
  const [progress, setProgress] = useState(8);
  const [logs, setLogs] = useState<Log[]>(openingLogs);

  const addLog = useCallback((tag: string, message: string) => {
    setLogs((current) => [...current, { time: stamp(), tag, message }]);
  }, []);

  const loadFile = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith(".apk")) {
        addLog("REFUSE", "Only .apk files are accepted in this workspace");
        return;
      }
      setFileName(file.name);
      setFileMeta(`${(file.size / 1024 / 1024).toFixed(1)} MB · local selection`);
      setPhase("ready");
      setProgress(8);
      setLogs([
        ...openingLogs,
        { time: stamp(), tag: "INPUT", message: `${file.name} selected locally` },
      ]);
    },
    [addLog],
  );

  const loadSample = useCallback(() => {
    setFileName("sample-authorized.apk");
    setFileMeta("18.4 MB · demonstration file");
    setPhase("ready");
    setProgress(8);
    setLogs([
      ...openingLogs,
      { time: stamp(), tag: "DEMO", message: "Safe sample workspace loaded" },
    ]);
  }, []);

  const runSequence = useCallback(
    async (sequence: typeof previewSequence | typeof buildSequence) => {
      for (const [value, tag, message] of sequence) {
        await wait(430);
        setProgress(value);
        addLog(tag, message);
      }
    },
    [addLog],
  );

  const startPreview = useCallback(async () => {
    if (!fileName || phase === "previewing" || phase === "building") return false;
    setPhase("previewing");
    setProgress(10);
    addLog("START", "Preview requested; workspace remains unchanged");
    await runSequence(previewSequence);
    setPhase("review");
    return true;
  }, [addLog, fileName, phase, runSequence]);

  const approveBuild = useCallback(async () => {
    if (phase !== "review") return false;
    setPhase("building");
    setProgress(5);
    addLog("APPROVE", "Plan + patch hashes approved for revision 00");
    await runSequence(buildSequence);
    setPhase("complete");
    return true;
  }, [addLog, phase, runSequence]);

  const reset = useCallback(() => {
    setPhase("empty");
    setFileName("");
    setFileMeta("");
    setProgress(8);
    setLogs(openingLogs);
  }, []);

  useLayoutEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.registerPlugin(ScrollTrigger);
    const context = gsap.context(() => {
      gsap.from("[data-reveal]", {
        y: 24,
        opacity: 0,
        duration: 0.9,
        stagger: 0.08,
        ease: "power3.out",
      });
      gsap.utils.toArray<HTMLElement>("[data-scroll-reveal]").forEach((element) => {
        gsap.from(element, {
          scrollTrigger: { trigger: element, start: "top 82%", once: true },
          y: 40,
          opacity: 0,
          duration: 0.85,
          ease: "power3.out",
        });
      });
    }, root);
    return () => context.revert();
  }, []);

  useEffect(() => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [logs]);

  useEffect(() => {
    const modelContext = (
      document as Document & {
        modelContext?: {
          registerTool: (tool: Record<string, unknown>, options?: { signal?: AbortSignal }) => void | Promise<void>;
        };
      }
    ).modelContext;
    if (!modelContext?.registerTool) return;
    const lifecycle = new AbortController();
    const register = async () => {
      await modelContext.registerTool(
        {
          name: "load_noir_sample",
          title: "Load NOIR sample",
          description: "Load the safe demonstration APK into the visible NOIR workspace.",
          inputSchema: { type: "object", properties: {}, additionalProperties: false },
          annotations: { readOnlyHint: false, untrustedContentHint: false },
          execute: async (input: unknown) => {
            if (!input || typeof input !== "object" || Object.keys(input).length > 0) {
              throw new Error("load_noir_sample does not accept input fields");
            }
            loadSample();
            return { loaded: true, file: "sample-authorized.apk" };
          },
        },
        { signal: lifecycle.signal },
      );
      await modelContext.registerTool(
        {
          name: "read_noir_workspace",
          title: "Read NOIR workspace",
          description: "Read the currently visible workspace phase, file name, progress, and latest log.",
          inputSchema: { type: "object", properties: {}, additionalProperties: false },
          annotations: { readOnlyHint: true, untrustedContentHint: false },
          execute: async (input: unknown) => {
            if (!input || typeof input !== "object" || Object.keys(input).length > 0) {
              throw new Error("read_noir_workspace does not accept input fields");
            }
            return {
              phase,
              file: fileName || null,
              progress,
              latestLog: logs.at(-1)?.message ?? null,
            };
          },
        },
        { signal: lifecycle.signal },
      );
    };
    void register().catch(() => undefined);
    return () => lifecycle.abort();
  }, [fileName, loadSample, logs, phase, progress]);

  const stage = phase === "complete" ? 3 : phase === "building" ? 2 : phase === "review" ? 1 : 0;
  const working = phase === "previewing" || phase === "building";
  const buttonText = phase === "previewing"
    ? "Preparing exact preview…"
    : phase === "review"
      ? "Approve exact patch & build"
      : phase === "building"
        ? "Building verified APK…"
        : phase === "complete"
          ? "Build verified"
          : "Preview exact changes";

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
        <a className="wordmark" href="#top" aria-label="NOIR home">
          <span className="mark" aria-hidden="true">N</span>
          <span>NOIR</span>
        </a>
        <a className="nav-link" href="#workspace">Workspace</a>
        <div className="nav-status"><span className="pulse-dot" /> Online</div>
      </header>

      <section id="top" className="landing-hero">
        <div className="hero-copy">
          <p className="eyebrow" data-reveal><ShieldCheck size={14} /> Authorized APK editor</p>
          <h1 data-reveal>Edit apps.<span>In plain English.</span></h1>
          <p className="hero-intro" data-reveal>Describe the change. Review the diff. Build with proof.</p>
          <div className="hero-actions" data-reveal>
            <Button className="primary-cta" asChild>
              <a href="#workspace">Enter workspace <ArrowDownRight /></a>
            </Button>
            <span>Nothing runs without approval.</span>
          </div>
        </div>
      </section>

      <section className="workspace-section">
        <div className="workspace-intro" data-scroll-reveal>
          <p>NOIR / WORKSPACE</p>
          <h2>Make the change.</h2>
        </div>
        <section id="workspace" className="workspace" aria-labelledby="workspace-title" data-scroll-reveal>
          <div className="workspace-head">
            <div><p>INTERACTIVE PREVIEW</p><h2 id="workspace-title">APK workspace</h2></div>
            <span className="secure-chip"><LockKeyhole size={13} /> Private</span>
          </div>
          <div className="workspace-body">
            <div className="controls-column">
              <label className={`upload-zone ${fileName ? "has-file" : ""}`} htmlFor="apk-input" onDrop={handleDrop} onDragOver={(event) => event.preventDefault()}>
                <input id="apk-input" type="file" accept=".apk,application/vnd.android.package-archive" onChange={handleFile} />
                <span className="upload-icon">{fileName ? <Check size={21} /> : <Upload size={21} />}</span>
                <span><strong>{fileName || "Drop APK"}</strong><small>{fileMeta || "or browse files"}</small></span>
                <FileArchive size={19} />
              </label>
              {!fileName && <button className="sample-link" onClick={loadSample}>Try a safe sample <ChevronRight size={13} /></button>}
              <div className="request-block">
                <div className="field-label"><label htmlFor="change-request">Change request</label></div>
                <Textarea id="change-request" className="request-input" value={request} onChange={(event) => setRequest(event.target.value)} disabled={working || phase === "complete"} />
              </div>
              <div className="consent-row"><span className="check-box"><Check size={12} /></span><span>Authorized APK</span></div>
              {phase === "review" && (
                <div className="review-card">
                  <span>PLAN 7F2A · REV 00</span>
                  <strong>2 exact operations</strong>
                  <small>AndroidManifest.xml · res/values/strings.xml</small>
                </div>
              )}
              {phase === "complete" && (
                <div className="success-card"><CheckCircle2 size={18} /><span><strong>Verified build ready</strong><small>SHA-256 · 7b2a…9e14</small></span></div>
              )}
              <Button
                className={`run-button ${fileName ? "enabled" : ""}`}
                disabled={!fileName || working || phase === "complete"}
                onClick={phase === "review" ? approveBuild : startPreview}
              >
                {working && <span className="button-spinner" />}{buttonText}
              </Button>
              {phase === "complete" && <button className="reset-button" onClick={reset}><RotateCcw size={13} /> Start another</button>}
            </div>

            <div className="console-column">
              <div className="console-head"><span><Terminal size={14} /> Activity</span><span className="live-state"><Radio size={12} /> Live</span></div>
              <div className="stage-strip" aria-label="Workflow stages">
                {['Input', 'Review', 'Build', 'Verify'].map((label, index) => (
                  <span key={label} className={index <= stage ? "active" : ""}>{label}{index < 3 && <i />}</span>
                ))}
              </div>
              <Progress value={progress} className="process-progress" />
              <div className="terminal-feed" ref={feed} aria-live="polite">
                {logs.map((log, index) => (
                  <p key={`${log.time}-${log.tag}-${index}`}>
                    <time>{log.time}</time><b>[{log.tag}]</b><span>{log.message}</span>
                  </p>
                ))}
                <span className="cursor-line"><i /> {working ? "processing" : phase === "complete" ? "complete" : "ready"}</span>
              </div>
            </div>
          </div>
        </section>
      </section>

      <footer><a className="wordmark" href="#top"><span className="mark">N</span><span>NOIR</span></a><p>Draft. Decide. Build.</p><span>© 2026</span></footer>
    </main>
  );
}
