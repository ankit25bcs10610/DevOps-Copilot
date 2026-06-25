import { lazy, Suspense, useState } from "react";

import { Console } from "./components/Console";
import { Landing } from "./components/Landing";

// Code-split the 3D backdrop (R3F + three + postprocessing ≈ the bulk of the
// bundle) so it never blocks first paint — the rest of the app loads without it.
const Hero3D = lazy(() => import("./components/Hero3D").then((m) => ({ default: m.Hero3D })));

export default function App() {
  const [view, setView] = useState<"landing" | "console">("landing");
  return (
    <>
      {/* One persistent 3D command-center backdrop behind every view. Its render
          loop pauses in the console (active=false) — no GPU spin behind the UI. */}
      <Suspense fallback={<div className="hero3d" aria-hidden="true" />}>
        <Hero3D active={view === "landing"} />
      </Suspense>
      {/* Dimming veil so the bright 3D never washes out content on any section. */}
      <div className="scene-veil" aria-hidden="true" />
      {view === "landing" ? (
        <Landing onLaunch={() => setView("console")} />
      ) : (
        <Console onHome={() => setView("landing")} />
      )}
    </>
  );
}
