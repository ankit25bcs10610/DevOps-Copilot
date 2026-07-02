/** Content-shaped loading placeholders (shimmer) — reserve space for async content
 *  so there's no layout jump, and give a clear "loading" affordance. */
export function Skeleton({
  width = "100%",
  height = 16,
  radius = 6,
  className = "",
}: {
  width?: number | string;
  height?: number | string;
  radius?: number;
  className?: string;
}) {
  return (
    <span
      className={`skeleton ${className}`}
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

/** A stack of skeleton lines for a loading block. */
export function SkeletonBlock({ lines = 3, label = "Loading…" }: { lines?: number; label?: string }) {
  return (
    <div className="skeleton-block" role="status" aria-live="polite" aria-label={label}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? "60%" : "100%"} height={14} />
      ))}
    </div>
  );
}
