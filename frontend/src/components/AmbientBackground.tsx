/** App-wide animated ambient backdrop: drifting neon aurora blobs + a perspective
 *  grid, painted behind every view so the whole product shares the hero's glow.
 *  Pure CSS (theme-var driven) — no GPU/WebGL cost while you work in the console. */
export function AmbientBackground() {
  return (
    <div className="ambient" aria-hidden="true">
      <div className="ambient__grid" />
      <div className="ambient__blob ambient__blob--1" />
      <div className="ambient__blob ambient__blob--2" />
      <div className="ambient__blob ambient__blob--3" />
    </div>
  );
}
