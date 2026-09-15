"use client";

import dynamic from "next/dynamic";
import { PointerEvent, useCallback, useEffect, useRef, useState } from "react";

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
  const [sceneReady, setSceneReady] = useState(false);
  const [inView, setInView] = useState(true);
  const stage = useRef<HTMLDivElement>(null);
  const brand = useRef<HTMLDivElement>(null);
  const brandFrame = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const warmScene = () => {
      void import("@/components/noir-apk-canvas").then(() => {
        if (!cancelled) setSceneReady(true);
      });
    };
    const idleWindow = window as unknown as {
      requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
      cancelIdleCallback?: (handle: number) => void;
    };
    const idleTimer = idleWindow.requestIdleCallback
      ? idleWindow.requestIdleCallback(warmScene, { timeout: 1200 })
      : window.setTimeout(warmScene, 250);
    const timer = window.setTimeout(() => setIntroComplete(true), 4000);
    return () => {
      cancelled = true;
      if (idleWindow.cancelIdleCallback) idleWindow.cancelIdleCallback(idleTimer);
      else window.clearTimeout(idleTimer);
      window.clearTimeout(timer);
    };
  }, []);

  const resetBrand = useCallback(() => {
    if (brandFrame.current !== null) window.cancelAnimationFrame(brandFrame.current);
    brandFrame.current = null;
    brand.current?.classList.remove("is-startled");
    brand.current?.querySelectorAll<HTMLElement>("[data-brand-letter]").forEach((letter) => {
      letter.style.setProperty("--flee-x", "0px");
      letter.style.setProperty("--flee-y", "0px");
    });
  }, []);

  const moveBrandLetters = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (!brand.current || exploded) return;
    const pointerX = event.clientX;
    const pointerY = event.clientY;
    if (brandFrame.current !== null) window.cancelAnimationFrame(brandFrame.current);
    brandFrame.current = window.requestAnimationFrame(() => {
      const letters = brand.current?.querySelectorAll<HTMLElement>("[data-brand-letter]");
      if (!letters || !brand.current) return;
      let startled = false;
      letters.forEach((letter, index) => {
        const bounds = letter.getBoundingClientRect();
        const dx = bounds.left + bounds.width / 2 - pointerX;
        const dy = bounds.top + bounds.height / 2 - pointerY;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const force = Math.max(0, 1 - distance / 190);
        startled ||= force > 0;
        letter.style.setProperty("--flee-x", `${(dx / distance) * force * (30 + index * 3)}px`);
        letter.style.setProperty("--flee-y", `${(dy / distance) * force * (22 + index * 2)}px`);
      });
      brand.current.classList.toggle("is-startled", startled);
      brandFrame.current = null;
    });
  }, [exploded]);

  useEffect(() => () => resetBrand(), [resetBrand]);

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
      onPointerMove={moveBrandLetters}
      onPointerLeave={() => {
        setExploded(false);
        resetBrand();
      }}
      role="img"
      aria-label={introComplete ? "Interactive exploded view of a precision APK security module" : "NOIR"}
    >
      <div className="noir-intro" aria-hidden="true"><strong>NOIR</strong></div>
      <div className="hero-brand" aria-hidden="true" ref={brand}>
        <span>AUTHORIZED APK EDITOR</span>
        <strong aria-label="NOIR">
          {["N", "O", "I", "R"].map((letter) => <i data-brand-letter key={letter}>{letter}</i>)}
        </strong>
      </div>
      {sceneReady && <NoirApkCanvas active={inView && introComplete} exploded={exploded} setExploded={setExploded} />}
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
