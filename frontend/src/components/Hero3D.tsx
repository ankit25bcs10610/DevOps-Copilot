import { Component, type ReactNode, Suspense, useMemo, useRef } from "react";

import {
  Float,
  Grid,
  MeshTransmissionMaterial,
  OrbitControls,
  Stars,
} from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import * as THREE from "three";

const PURPLE = "#7C5CFF";
const CYAN = "#00D4FF";
const MINT = "#00FFB3";

// Honor reduced-motion: freeze the scene's animations and auto-rotation.
const REDUCED =
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Transparent holographic crystal cube (glassmorphism, refraction). */
function CrystalCube() {
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
        attenuationColor={PURPLE}
        attenuationDistance={2.2}
      />
    </mesh>
  );
}

/** AI neural core glowing from inside the cube. */
function NeuralCore() {
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
          color={PURPLE}
          emissive={PURPLE}
          emissiveIntensity={3.2}
          toneMapped={false}
          roughness={0.2}
        />
      </mesh>
      <mesh ref={wire} scale={0.78}>
        <icosahedronGeometry args={[0.95, 1]} />
        <meshBasicMaterial color={CYAN} wireframe toneMapped={false} />
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

/** Orbiting particle field / digital nodes. */
function Particles({ count = 450 }: { count?: number }) {
  const ref = useRef<THREE.Points>(null);
  const positions = useMemo(() => {
    const a = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const r = 2.6 + Math.random() * 3.4;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      a[i * 3] = r * Math.sin(ph) * Math.cos(th);
      a[i * 3 + 1] = (Math.random() - 0.5) * 4.5;
      a[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
    }
    return a;
  }, [count]);
  useFrame((s) => {
    if (ref.current && !REDUCED) ref.current.rotation.y = s.clock.elapsedTime * 0.045;
  });
  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial size={0.03} color={CYAN} transparent opacity={0.85} toneMapped={false} />
    </points>
  );
}

/** Glowing circular platform the command center floats above. */
function Platform() {
  return (
    <group position={[0, -1.7, 0]}>
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[2.3, 2.3, 0.06, 72]} />
        <meshStandardMaterial color="#081226" metalness={0.85} roughness={0.35} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
        <ringGeometry args={[2.05, 2.28, 72]} />
        <meshStandardMaterial
          color={CYAN}
          emissive={CYAN}
          emissiveIntensity={3}
          toneMapped={false}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
        <ringGeometry args={[1.4, 1.46, 72]} />
        <meshStandardMaterial
          color={MINT}
          emissive={MINT}
          emissiveIntensity={2}
          toneMapped={false}
          side={THREE.DoubleSide}
        />
      </mesh>
    </group>
  );
}

function CommandCenter() {
  return (
    <Float speed={1.1} rotationIntensity={0.25} floatIntensity={0.6}>
      <group>
        <NeuralCore />
        <CrystalCube />
        <HexRing radius={1.7} color={CYAN} speed={0.5} tilt={[Math.PI / 2.2, 0, 0]} />
        <HexRing radius={2.0} color={PURPLE} speed={-0.35} tilt={[Math.PI / 1.7, 0.4, 0]} />
        <HexRing radius={2.35} color={MINT} speed={0.22} tilt={[Math.PI / 2.5, -0.5, 0.3]} />
      </group>
    </Float>
  );
}

function Scene() {
  return (
    <>
      <color attach="background" args={["#050816"]} />
      <fog attach="fog" args={["#050816", 9, 24]} />
      <ambientLight intensity={0.25} />
      <pointLight position={[5, 4, 5]} intensity={120} color={PURPLE} distance={22} decay={2} />
      <pointLight position={[-6, 2, -2]} intensity={90} color={CYAN} distance={22} decay={2} />
      <pointLight position={[0, -2, 5]} intensity={60} color={MINT} distance={20} decay={2} />

      <Suspense fallback={null}>
        <CommandCenter />
        <Particles />
        <Platform />
        <Grid
          position={[0, -1.85, 0]}
          args={[40, 40]}
          cellSize={0.6}
          cellThickness={0.6}
          sectionSize={3}
          sectionThickness={1}
          cellColor="#16284a"
          sectionColor={PURPLE}
          fadeDistance={28}
          fadeStrength={1.6}
          infiniteGrid
        />
        <Stars radius={60} depth={32} count={1400} factor={4} saturation={0} fade speed={0.4} />
      </Suspense>

      <EffectComposer>
        <Bloom intensity={1.25} luminanceThreshold={0.18} luminanceSmoothing={0.6} mipmapBlur />
      </EffectComposer>

      {/* Non-interactive: it's a global background, so it never captures input. */}
      <OrbitControls
        enableZoom={false}
        enablePan={false}
        enableRotate={false}
        autoRotate={!REDUCED}
        autoRotateSpeed={0.5}
      />
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

export function Hero3D() {
  return (
    <div className="hero3d">
      <CanvasBoundary>
        <Canvas dpr={[1, 2]} camera={{ position: [0, 1.2, 6.5], fov: 42 }} gl={{ antialias: true }}>
          <Scene />
        </Canvas>
      </CanvasBoundary>
    </div>
  );
}
