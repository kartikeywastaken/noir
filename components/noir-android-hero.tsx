"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";

const LAYERS = [
  "AndroidManifest.xml",
  "classes.dex",
  "resources.arsc",
  "res/",
  "assets/",
  "META-INF",
];

const NoirApkCanvas = dynamic(
  () => import("@/components/noir-apk-canvas").then((module) => module.NoirApkCanvas),
  { ssr: false },
);

export function NoirApkCoreHero() {
  const [exploded, setExploded] = useState(false);
  const [introComplete, setIntroComplete] = useState(false);
  const [inView, setInView] = useState(true);
  const stage = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const preloadTimer = window.setTimeout(() => {
      void import("@/components/noir-apk-canvas");
    }, 1000);
    const timer = window.setTimeout(() => setIntroComplete(true), 4000);
    return () => {
      window.clearTimeout(preloadTimer);
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!stage.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => setInView(entry.isIntersecting),
      { rootMargin: "120px" },
    );
    observer.observe(stage.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={stage}
      className={`android-stage ${exploded ? "is-exploded" : ""} ${introComplete ? "intro-complete" : "intro-active"}`}
      onPointerLeave={() => setExploded(false)}
      role="img"
      aria-label={introComplete ? "Interactive exploded view of a precision APK security module" : "NOIR"}
    >
      <div className="noir-intro" aria-hidden="true"><strong>NOIR</strong></div>
      {introComplete && <NoirApkCanvas active={inView} exploded={exploded} setExploded={setExploded} />}
      <div className="android-caption"><span>NOIR / APK CORE</span><b>{exploded ? "06 LAYERS EXPOSED" : "HOVER TO INSPECT"}</b></div>
      <div className="apk-labels" aria-hidden="true">
        {LAYERS.map((label, index) => (
          <div className={`apk-label layer-${index + 1}`} key={label}>
            <span>L{String(index + 1).padStart(2, "0")}</span><b>{label}</b><i />
          </div>
        ))}
      </div>
    </div>
  );
}
