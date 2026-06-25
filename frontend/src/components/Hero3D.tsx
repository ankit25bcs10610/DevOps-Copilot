import {
  Component,
  type ReactNode,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  Environment,
  Float,
  Grid,
  Lightformer,
  MeshTransmissionMaterial,
  Stars,
} from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import * as THREE from "three";

// Honor reduced-motion: freeze every animation and render a single static frame.
const REDUCED =
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// Deep cinematic "void" behind the glass UI — kept dark on every theme so bloom
// and the starfield always read. Only the accent ELEMENTS recolor per theme.
const VOID = "#050816";

// ---- Theme-reactive palette -------------------------------------------------
// The 3D scene pulls its accent colors straight from the same CSS variables the
// rest of the app themes with (--accent / --accent-2 / --ok), so switching the
// theme in the UI recolors the whole scene in lockstep.
interface Palette {
  a: string; // primary accent
  b: string; // secondary accent
  c: string; // positive / mint accent
}

function readPalette(): Palette {
  if (typeof window === "undefined") return { a: "#6491ff", b: "#7c5cff", c: "#2dd4a7" };
  const cs = getComputedStyle(document.documentElement);
  const get = (v: string, fb: string) => cs.getPropertyValue(v).trim() || fb;
  return {
    a: get("--accent", "#6491ff"),
    b: get("--accent-2", "#7c5cff"),
    c: get("--ok", "#2dd4a7"),
  };
}

function useThemePalette(): Palette {
  const [pal, setPal] = useState<Palette>(readPalette);
  useEffect(() => {
    // Re-read once after mount (CSS vars are resolved) and on every theme switch.
    setPal(readPalette());
    const obs = new MutationObserver(() => setPal(readPalette()));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);
  return pal;
}

/** Transparent holographic crystal cube — refraction + real environment reflections. */
function CrystalCube({ pal }: { pal: Palette }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((s) => {
    if (!ref.current || REDUCED) return;
    const t = s.clock.elapsedTime;
    ref.current.rotation.y = t * 0.25;
    ref.current.rotation.x = Math.sin(t * 0.3) * 0.15;
  });
  return (
    <mesh ref={ref}>
      <boxGeometry args={[1.9, 1.9, 1.9]} />
      <MeshTransmissionMaterial
        transmission={1}
        thickness={1.1}
        roughness={0.04}
        ior={1.4}
        chromaticAberration={0.6}
        anisotropy={0.3}
        distortion={0.3}
        distortionScale={0.4}
        temporalDistortion={0.1}
        color="#acd0ff"
        attenuationColor={pal.b}
        attenuationDistance={2.2}
      />
    </mesh>
  );
}

/** AI neural core glowing from inside the cube. */
function NeuralCore({ pal }: { pal: Palette }) {
  const solid = useRef<THREE.Mesh>(null);
  const wire = useRef<THREE.Mesh>(null);
  useFrame((s) => {
    if (REDUCED) return;
    const t = s.clock.elapsedTime;
    const pulse = 1 + Math.sin(t * 2.2) * 0.07;
    if (solid.current) {
      solid.current.scale.setScalar(pulse);
      solid.current.rotation.y = t * 0.5;
      solid.current.rotation.x = t * 0.25;
    }
    if (wire.current) {
      wire.current.rotation.y = -t * 0.4;
      wire.current.rotation.z = t * 0.2;
    }
  });
  return (
    <group>
      <mesh ref={solid}>
        <icosahedronGeometry args={[0.5, 1]} />
        <meshStandardMaterial
          color={pal.b}
          emissive={pal.b}
          emissiveIntensity={3.2}
          toneMapped={false}
          roughness={0.2}
        />
      </mesh>
      <mesh ref={wire} scale={0.78}>
        <icosahedronGeometry args={[0.95, 1]} />
        <meshBasicMaterial color={pal.a} wireframe toneMapped={false} />
      </mesh>
    </group>
  );
}

/** Rotating hexagonal energy ring. */
function HexRing({
  radius,
  color,
  speed,
  tilt,
}: {
  radius: number;
  color: string;
  speed: number;
  tilt: [number, number, number];
}) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((s) => {
    if (ref.current && !REDUCED) ref.current.rotation.z = s.clock.elapsedTime * speed;
  });
  return (
    <mesh ref={ref} rotation={tilt}>
      <torusGeometry args={[radius, 0.022, 8, 6]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={2.6}
        toneMapped={false}
        metalness={0.6}
        roughness={0.3}
      />
    </mesh>
  );
}

/** Neural constellation: a sphere of nodes with lines drawn between near neighbours. */
function Constellation({ pal, count = 220 }: { pal: Palette; count?: number }) {
  const group = useRef<THREE.Group>(null);
  const { nodes, lines } = useMemo(() => {
    const nodes = new Float32Array(count * 3);
    const vecs: THREE.Vector3[] = [];
    for (let i = 0; i < count; i++) {
      const r = 3.0 + Math.random() * 3.6;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      const x = r * Math.sin(ph) * Math.cos(th);
      const y = (Math.random() - 0.5) * 5;
      const z = r * Math.sin(ph) * Math.sin(th);
      nodes[i * 3] = x;
      nodes[i * 3 + 1] = y;
      nodes[i * 3 + 2] = z;
      vecs.push(new THREE.Vector3(x, y, z));
    }
    // Connect neighbours within a distance threshold (capped for performance).
    const TH = 1.2;
    const MAX = 900;
    const seg: number[] = [];
    for (let i = 0; i < count && seg.length / 6 < MAX; i++) {
      for (let j = i + 1; j < count && seg.length / 6 < MAX; j++) {
        if (vecs[i].distanceTo(vecs[j]) < TH) {
          seg.push(vecs[i].x, vecs[i].y, vecs[i].z, vecs[j].x, vecs[j].y, vecs[j].z);
        }
      }
    }
    return { nodes, lines: new Float32Array(seg) };
  }, [count]);

  useFrame((s) => {
    if (group.current && !REDUCED) group.current.rotation.y = s.clock.elapsedTime * 0.04;
  });

  return (
    <group ref={group}>
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[nodes, 3]} />
        </bufferGeometry>
        <pointsMaterial
          size={0.035}
          color={pal.a}
          transparent
          opacity={0.9}
          sizeAttenuation
          toneMapped={false}
        />
      </points>
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[lines, 3]} />
        </bufferGeometry>
        <lineBasicMaterial color={pal.c} transparent opacity={0.14} toneMapped={false} />
      </lineSegments>
    </group>
  );
}

/** Vertical "data streams" of light rising from the floor — telemetry flowing in. */
function DataStreams({ pal, count = 40 }: { pal: Palette; count?: number }) {
  const ref = useRef<THREE.Points>(null);
  const { positions, speeds } = useMemo(() => {
    const positions = new Float32Array(count * 3);
    const speeds = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const r = 1.6 + Math.random() * 6;
      const a = Math.random() * Math.PI * 2;
      positions[i * 3] = Math.cos(a) * r;
      positions[i * 3 + 1] = -1.8 + Math.random() * 5.2;
      positions[i * 3 + 2] = Math.sin(a) * r;
      speeds[i] = 0.6 + Math.random() * 1.4;
    }
    return { positions, speeds };
  }, [count]);

  useFrame((_, dt) => {
    if (!ref.current || REDUCED) return;
    const arr = ref.current.geometry.attributes.position.array as Float32Array;
    const step = Math.min(dt, 0.05); // clamp so a stutter doesn't teleport streams
    for (let i = 0; i < count; i++) {
      arr[i * 3 + 1] += speeds[i] * step;
      if (arr[i * 3 + 1] > 3.4) arr[i * 3 + 1] = -1.8;
    }
    ref.current.geometry.attributes.position.needsUpdate = true;
  });

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={0.05}
        color={pal.c}
        transparent
        opacity={0.7}
        sizeAttenuation
        toneMapped={false}
      />
    </points>
  );
}

/** Glowing circular platform the command center floats above. */
function Platform({ pal }: { pal: Palette }) {
  return (
    <group position={[0, -1.7, 0]}>
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[2.3, 2.3, 0.06, 72]} />
        <meshStandardMaterial color="#081226" metalness={0.85} roughness={0.35} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
        <ringGeometry args={[2.05, 2.28, 72]} />
        <meshStandardMaterial
          color={pal.a}
          emissive={pal.a}
          emissiveIntensity={3}
          toneMapped={false}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
        <ringGeometry args={[1.4, 1.46, 72]} />
        <meshStandardMaterial
          color={pal.c}
          emissive={pal.c}
          emissiveIntensity={2}
          toneMapped={false}
          side={THREE.DoubleSide}
        />
      </mesh>
    </group>
  );
}

function CommandCenter({ pal }: { pal: Palette }) {
  const grp = useRef<THREE.Group>(null);
  useFrame((s) => {
    if (grp.current && !REDUCED) grp.current.rotation.y = s.clock.elapsedTime * 0.12;
  });
  return (
    <group ref={grp}>
      <Float speed={1.1} rotationIntensity={0.25} floatIntensity={0.6}>
        <group>
          <NeuralCore pal={pal} />
          <CrystalCube pal={pal} />
          <HexRing radius={1.7} color={pal.a} speed={0.5} tilt={[Math.PI / 2.2, 0, 0]} />
          <HexRing radius={2.0} color={pal.b} speed={-0.35} tilt={[Math.PI / 1.7, 0.4, 0]} />
          <HexRing radius={2.35} color={pal.c} speed={0.22} tilt={[Math.PI / 2.5, -0.5, 0.3]} />
        </group>
      </Float>
    </group>
  );
}

/** Cinematic mouse parallax: the camera eases toward the pointer and keeps the
 *  command center framed. Disabled under reduced-motion. */
function ParallaxRig() {
  useFrame((s) => {
    if (REDUCED) return;
    const cam = s.camera;
    cam.position.x += (s.pointer.x * 0.9 - cam.position.x) * 0.045;
    cam.position.y += (1.2 + s.pointer.y * 0.5 - cam.position.y) * 0.045;
    cam.lookAt(0, 0.3, 0);
  });
  return null;
}

function Scene({ pal }: { pal: Palette }) {
  return (
    <>
      <color attach="background" args={[VOID]} />
      <fog attach="fog" args={[VOID, 9, 26]} />
      <ambientLight intensity={0.25} />
      <pointLight position={[5, 4, 5]} intensity={120} color={pal.b} distance={22} decay={2} />
      <pointLight position={[-6, 2, -2]} intensity={90} color={pal.a} distance={22} decay={2} />
      <pointLight position={[0, -2, 5]} intensity={60} color={pal.c} distance={20} decay={2} />

      <Suspense fallback={null}>
        {/* Baked studio lighting → real reflections on the glass crystal. Re-bakes
            on theme change (keyed by accent) but never hits the network. */}
        <Environment key={pal.a} resolution={256} frames={1}>
          <Lightformer form="rect" intensity={2} color={pal.a} position={[0, 5, -4]} scale={[10, 5, 1]} />
          <Lightformer
            form="rect"
            intensity={1.6}
            color={pal.b}
            position={[-5, 1, 2]}
            scale={[3, 7, 1]}
            rotation={[0, Math.PI / 2, 0]}
          />
          <Lightformer form="circle" intensity={2.5} color={pal.c} position={[4, 2, 4]} scale={3} />
          <Lightformer form="ring" intensity={1.4} color="#ffffff" position={[0, -1, 6]} scale={2.5} />
        </Environment>

        <CommandCenter pal={pal} />
        <Constellation pal={pal} />
        <DataStreams pal={pal} />
        <Platform pal={pal} />
        <Grid
          position={[0, -1.85, 0]}
          args={[40, 40]}
          cellSize={0.6}
          cellThickness={0.6}
          sectionSize={3}
          sectionThickness={1}
          cellColor="#16284a"
          sectionColor={pal.b}
          fadeDistance={28}
          fadeStrength={1.6}
          infiniteGrid
        />
        <Stars radius={60} depth={32} count={1200} factor={4} saturation={0} fade speed={0.4} />
      </Suspense>

      <EffectComposer>
        <Bloom intensity={1.25} luminanceThreshold={0.18} luminanceSmoothing={0.6} mipmapBlur />
        <Vignette eskil={false} offset={0.28} darkness={0.82} />
      </EffectComposer>

      <ParallaxRig />
    </>
  );
}

/** Static CSS fallback if WebGL is unavailable or the canvas errors. */
class CanvasBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) return <div className="hero3d__fallback" />;
    return this.props.children;
  }
}

export function Hero3D({ active = true }: { active?: boolean }) {
  const pal = useThemePalette();
  // Pause the render loop when the backdrop isn't the focus (console view): the
  // last frame stays on screen but no frames are drawn — no idle GPU/CPU spin.
  // Reduced-motion renders a single static frame on demand.
  const frameloop = REDUCED ? "demand" : active ? "always" : "never";
  return (
    <div className="hero3d">
      <CanvasBoundary>
        <Canvas
          dpr={[1, 1.75]}
          frameloop={frameloop}
          camera={{ position: [0, 1.2, 6.5], fov: 42 }}
          gl={{ antialias: true, powerPreference: "high-performance" }}
        >
          <Scene pal={pal} />
        </Canvas>
      </CanvasBoundary>
    </div>
  );
}
