"use client";

import { Canvas, ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import gsap from "gsap";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

const LAYERS = [
  "AndroidManifest.xml",
  "classes.dex",
  "resources.arsc",
  "res/",
  "assets/",
  "META-INF",
];

function roundedRect(width: number, height: number, radius: number) {
  const x = -width / 2;
  const y = -height / 2;
  const shape = new THREE.Shape();
  shape.moveTo(x + radius, y);
  shape.lineTo(x + width - radius, y);
  shape.quadraticCurveTo(x + width, y, x + width, y + radius);
  shape.lineTo(x + width, y + height - radius);
  shape.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  shape.lineTo(x + radius, y + height);
  shape.quadraticCurveTo(x, y + height, x, y + height - radius);
  shape.lineTo(x, y + radius);
  shape.quadraticCurveTo(x, y, x + radius, y);
  return shape;
}

type PlateProps = {
  index: number;
  exploded: boolean;
  register: (index: number, group: THREE.Group | null) => void;
};

function DataPlate({ index, exploded, register }: PlateProps) {
  const shape = useMemo(() => roundedRect(3.05 - index * 0.055, 3.78 - index * 0.055, 0.24), [index]);
  const geometry = useMemo(() => new THREE.ExtrudeGeometry(shape, {
    depth: 0.13,
    bevelEnabled: true,
    bevelSegments: 2,
    steps: 1,
    bevelSize: 0.045,
    bevelThickness: 0.045,
  }), [shape]);
  const assembledDepth = (index - 2.5) * -0.16;
  const edge = index === 0 || index === 3;

  return (
    <group ref={(node) => register(index, node)} position={[0, 0, assembledDepth]}>
      <mesh geometry={geometry}>
        <meshStandardMaterial
          color={index % 2 ? "#111b0c" : "#0b1508"}
          emissive={edge ? "#526f0f" : "#233309"}
          emissiveIntensity={exploded ? (edge ? 0.2 : 0.1) : 0.065}
          metalness={0.93}
          roughness={0.18 + index * 0.035}
        />
      </mesh>

      <mesh position={[0, 0, 0.2]}>
        <shapeGeometry args={[shape]} />
        <meshBasicMaterial color="#1c2119" transparent opacity={0.16 + index * 0.015} />
      </mesh>

      {index === 0 && <FrontFace exploded={exploded} />}
      {index === 1 && <DexPattern />}
      {index === 2 && <ResourcePattern />}
      {index === 3 && <FolderPattern />}
      {index === 4 && <AssetPattern />}
      {index === 5 && <SignaturePattern />}
    </group>
  );
}

function Trace({ position, scale = [1, 1, 1] }: { position: [number, number, number]; scale?: [number, number, number] }) {
  return (
    <mesh position={position} scale={scale}>
      <boxGeometry args={[1, 0.025, 0.02]} />
      <meshBasicMaterial color="#c8ff1a" toneMapped={false} />
    </mesh>
  );
}

function FrontFace({ exploded }: { exploded: boolean }) {
  const core = useRef<THREE.Group>(null);

  useEffect(() => {
    if (!core.current) return;
    const tween = gsap.to(core.current.scale, {
      x: exploded ? 0 : 1,
      y: exploded ? 0 : 1,
      z: exploded ? 0 : 1,
      duration: exploded ? 0.38 : 0.5,
      ease: exploded ? "power2.in" : "power3.out",
      overwrite: true,
    });
    return () => tween.kill();
  }, [exploded]);

  return (
    <group position={[0, 0, 0.25]}>
      <group ref={core}>
        <mesh>
          <torusGeometry args={[0.64, 0.065, 12, 64]} />
          <meshStandardMaterial color="#c8ff1a" emissive="#8fbd13" emissiveIntensity={1.1} toneMapped={false} />
        </mesh>
        <mesh>
          <circleGeometry args={[0.43, 48]} />
          <meshStandardMaterial color="#060706" metalness={1} roughness={0.08} />
        </mesh>
        <mesh position={[0, 0, 0.025]}>
          <ringGeometry args={[0.11, 0.16, 28]} />
          <meshBasicMaterial color="#c8ff1a" toneMapped={false} />
        </mesh>
      </group>
      <Trace position={[-0.93, 1.28, 0]} scale={[0.58, 1, 1]} />
      <Trace position={[0.93, -1.28, 0]} scale={[0.58, 1, 1]} />
      {[-1, 1].map((x) => [-1.45, 1.45].map((y) => (
        <mesh key={`${x}-${y}`} position={[x * 1.2, y, 0]}>
          <circleGeometry args={[0.045, 14]} />
          <meshBasicMaterial color="#798167" />
        </mesh>
      )))}
    </group>
  );
}

function DexPattern() {
  return <group position={[0, 0, 0.24]}>{[-1.15, -.72, -.29, .14, .57, 1].map((y, index) => <Trace key={y} position={[-0.2 + (index % 2) * .25, y, 0]} scale={[1.45 - (index % 3) * .22, 1, 1]} />)}</group>;
}

function ResourcePattern() {
  return (
    <group position={[0, 0, 0.24]}>
      {[-.82, 0, .82].flatMap((x) => [-1.05, -.35, .35, 1.05].map((y) => (
        <mesh key={`${x}-${y}`} position={[x, y, 0]}>
          <boxGeometry args={[0.42, 0.42, 0.025]} />
          <meshBasicMaterial color={(Math.abs(x + y) * 10) % 2 > 1 ? "#c8ff1a" : "#41483a"} />
        </mesh>
      )))}
    </group>
  );
}

function FolderPattern() {
  return (
    <group position={[0, 0, 0.24]}>
      {[-.82, 0, .82].map((x, index) => (
        <group key={x} position={[x, index === 1 ? .25 : -.2, 0]}>
          <mesh><boxGeometry args={[0.58, 1.25, 0.025]} /><meshBasicMaterial color="#30372a" /></mesh>
          <Trace position={[0, .28, .03]} scale={[.36, 1, 1]} />
        </group>
      ))}
    </group>
  );
}

function AssetPattern() {
  return <group position={[0, 0, 0.24]}>{[-1.1, -.55, 0, .55, 1.1].map((x, index) => <Trace key={x} position={[x, 0, 0]} scale={[.025, 58 + index * 7, 1]} />)}</group>;
}

function SignaturePattern() {
  return (
    <group position={[0, 0, 0.24]}>
      <mesh><torusGeometry args={[.82, .035, 8, 6]} /><meshBasicMaterial color="#59624c" /></mesh>
      <mesh rotation={[0, 0, Math.PI / 6]}><torusGeometry args={[.48, .04, 8, 6]} /><meshBasicMaterial color="#c8ff1a" /></mesh>
      <mesh><circleGeometry args={[.09, 20]} /><meshBasicMaterial color="#c8ff1a" toneMapped={false} /></mesh>
    </group>
  );
}

function ApkCore({ exploded, setExploded }: { exploded: boolean; setExploded: (value: boolean) => void }) {
  const assembly = useRef<THREE.Group>(null);
  const layerGroups = useRef<Array<THREE.Group | null>>([]);
  const expansion = useRef({ value: 0 });
  const { pointer, viewport } = useThree();
  const targets = useMemo(
    () => LAYERS.map((_, index) => viewport.width < 8
      ? new THREE.Vector3(index % 2 ? 1.45 : -1.45, 1.75 - Math.floor(index / 2) * 1.75, (2.5 - index) * 0.12)
      : new THREE.Vector3((index - 2.5) * 1.9, 1.18 - index * 0.47, (2.5 - index) * 0.14)),
    [viewport.width],
  );
  const assembled = useMemo(
    () => LAYERS.map((_, index) => new THREE.Vector3(0, 0, (index - 2.5) * -0.16)),
    [],
  );

  useEffect(() => {
    const tween = gsap.to(expansion.current, {
      value: exploded ? 1 : 0,
      duration: exploded ? 0.82 : 0.68,
      ease: exploded ? "power3.out" : "power3.inOut",
      overwrite: true,
    });
    return () => tween.kill();
  }, [exploded]);

  useFrame((state, delta) => {
    const damp = 1 - Math.exp(-Math.min(delta, 1 / 30) * 8);
    const progress = expansion.current.value;
    if (assembly.current) {
      assembly.current.rotation.y = THREE.MathUtils.lerp(assembly.current.rotation.y, -0.18 + pointer.x * 0.13, damp);
      assembly.current.rotation.x = THREE.MathUtils.lerp(assembly.current.rotation.x, 0.04 - pointer.y * 0.08, damp);
      assembly.current.position.y = Math.sin(state.clock.elapsedTime * 0.72) * 0.09;
      assembly.current.rotation.z = Math.sin(state.clock.elapsedTime * 0.38) * 0.012;
    }
    layerGroups.current.forEach((group, index) => {
      if (!group) return;
      group.position.lerpVectors(assembled[index], targets[index], progress);
      group.rotation.y = (index - 2.5) * -0.075 * progress;
      group.rotation.z = (index - 2.5) * 0.012 * progress;
      const openScale = viewport.width < 8 ? 0.5 : 0.58;
      group.scale.setScalar(THREE.MathUtils.lerp(1, openScale, progress));
    });
  });

  const inspect = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    setExploded(true);
  };

  return (
    <group ref={assembly} onPointerEnter={inspect}>
      <mesh visible={!exploded} position={[0, 0, -0.72]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[2.55, 0.012, 6, 96]} />
        <meshBasicMaterial color="#56613e" transparent opacity={0.38} />
      </mesh>
      {LAYERS.map((_, index) => (
        <DataPlate key={index} index={index} exploded={exploded} register={(layerIndex, group) => { layerGroups.current[layerIndex] = group; }} />
      ))}
    </group>
  );
}

export function NoirApkCoreHero() {
  const [exploded, setExploded] = useState(false);
  const [introComplete, setIntroComplete] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setIntroComplete(true), 4000);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div
      className={`android-stage ${exploded ? "is-exploded" : ""} ${introComplete ? "intro-complete" : "intro-active"}`}
      onPointerLeave={() => setExploded(false)}
      role="img"
      aria-label={introComplete ? "Interactive exploded view of a precision APK security module" : "NOIR"}
    >
      <div className="noir-intro" aria-hidden="true"><strong>NOIR</strong></div>
      <Canvas className="android-canvas" camera={{ position: [0, 0.2, 8.1], fov: 38 }} dpr={[1, 1.25]} gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}>
        <ambientLight intensity={0.42} />
        <directionalLight position={[4, 6, 6]} intensity={2.4} color="#f4f5f1" />
        <pointLight position={[-4, 2, 5]} intensity={24} distance={10} color="#c8ff1a" />
        <pointLight position={[3, -3, 4]} intensity={8} distance={8} color="#53622c" />
        <ApkCore exploded={exploded} setExploded={setExploded} />
      </Canvas>

      <div className="android-reticle" aria-hidden="true"><span /><span /></div>
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
