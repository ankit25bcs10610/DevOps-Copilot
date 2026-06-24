import { useState } from "react";

import { Console } from "./components/Console";
import { Hero3D } from "./components/Hero3D";
import { Landing } from "./components/Landing";

export default function App() {
  const [view, setView] = useState<"landing" | "console">("landing");
  return (
    <>
      {/* One persistent 3D command-center backdrop behind every view. */}
      <Hero3D />
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
