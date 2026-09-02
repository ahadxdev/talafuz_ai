import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatRulerTime } from "../../utils/subtitleUtils";

/**
 * Phase 4 — Bottom panel: timeline with time ruler, playhead, video track
 * and caption track.
 *
 * - Click/drag the ruler or empty track space to scrub the video.
 * - Click a caption block to select it; double-click to seek to its start.
 * - Drag the left/right edge of a block to adjust its start/end time
 *   (clamped between neighbouring cues).
 */

const RULER_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];

export function Timeline({
  subtitles,
  duration,
  currentTime,
  selectedId,
  onSeek,
  onSelect,
  onTrim,
}) {
  const trackRef = useRef(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const safeDuration = useMemo(() => {
    if (Number.isFinite(duration) && duration > 0) return duration;
    return (
      (subtitles || []).reduce((max, s) => Math.max(max, s.end), 0) || 1
    );
  }, [duration, subtitles]);

  const pps = width / safeDuration; // pixels per second

  const rulerStep = useMemo(
    () => RULER_STEPS.find((s) => safeDuration / s <= 12) ?? 300,
    [safeDuration]
  );

  const ticks = useMemo(() => {
    const arr = [];
    for (let t = 0; t <= safeDuration + 0.001; t += rulerStep) {
      arr.push(Math.round(t * 1000) / 1000);
    }
    return arr;
  }, [safeDuration, rulerStep]);

  const timeAt = useCallback(
    (clientX) => {
      const el = trackRef.current;
      if (!el) return 0;
      const rect = el.getBoundingClientRect();
      const ratio = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
      return ratio * safeDuration;
    },
    [safeDuration]
  );

  // ---- Scrubbing (ruler + track background) ----
  const scrubbingRef = useRef(false);

  const handleScrubDown = (e) => {
    scrubbingRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    onSeek(timeAt(e.clientX));
  };

  const handleScrubMove = (e) => {
    if (scrubbingRef.current) onSeek(timeAt(e.clientX));
  };

  const handleScrubUp = (e) => {
    scrubbingRef.current = false;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
  };

  // ---- Caption block edge trimming ----
  const trimRef = useRef(null); // { id, edge }

  const handleEdgeDown = (e, sub, edge) => {
    e.stopPropagation();
    e.preventDefault();
    trimRef.current = { id: sub.id, edge };
    e.currentTarget.setPointerCapture(e.pointerId);
    onTrim(sub.id, edge, timeAt(e.clientX), "start");
  };

  const handleEdgeMove = (e) => {
    const trim = trimRef.current;
    if (trim) onTrim(trim.id, trim.edge, timeAt(e.clientX), "move");
  };

  const handleEdgeUp = (e) => {
    const trim = trimRef.current;
    if (trim) {
      onTrim(trim.id, trim.edge, timeAt(e.clientX), "end");
      trimRef.current = null;
    }
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
  };

  return (
    <section className="shrink-0 bg-gray-900 border border-gray-800 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
          Timeline
        </h3>
        <span className="text-[10px] text-gray-500">
          Drag block edges to adjust timing · click to select
        </span>
      </div>

      <div className="select-none">
        {/* Time ruler */}
        <div
          className="relative h-5 cursor-pointer touch-none"
          onPointerDown={handleScrubDown}
          onPointerMove={handleScrubMove}
          onPointerUp={handleScrubUp}
        >
          {ticks.map((t) => (
            <div
              key={t}
              className="absolute bottom-0"
              style={{ left: `${(t / safeDuration) * 100}%` }}
            >
              <div className="w-px h-1.5 bg-gray-600" />
              <span className="absolute bottom-full left-0 -translate-x-1/2 text-[9px] text-gray-500 font-mono whitespace-nowrap">
                {formatRulerTime(t)}
              </span>
            </div>
          ))}
        </div>

        {/* Video track */}
        <div className="relative h-5 mb-1.5 rounded bg-purple-500/25 border border-purple-400/40 flex items-center px-2">
          <span className="text-[10px] text-purple-200 font-medium">
            Video
          </span>
        </div>

        {/* Caption track */}
        <div
          ref={trackRef}
          className="relative h-11 rounded bg-gray-950/80 border border-gray-800 cursor-crosshair overflow-hidden touch-none"
          onPointerDown={handleScrubDown}
          onPointerMove={handleScrubMove}
          onPointerUp={handleScrubUp}
        >
          {(subtitles || []).map((sub) => {
            const left = sub.start * pps;
            const blockWidth = Math.max((sub.end - sub.start) * pps, 3);
            const isSelected = sub.id === selectedId;
            return (
              <div
                key={sub.id}
                className={`absolute top-1 bottom-1 rounded-md flex items-center px-1.5 overflow-hidden ${
                  isSelected
                    ? "bg-blue-500/90 ring-2 ring-blue-300 z-10"
                    : "bg-amber-400/80 hover:bg-amber-300"
                }`}
                style={{ left, width: blockWidth }}
                title={`#${sub.id} · ${sub.romanized_text || "(empty)"}`}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  onSelect(sub);
                }}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  onSeek(sub.start);
                }}
              >
                <span className="text-[10px] font-medium text-gray-950 truncate pointer-events-none">
                  {sub.romanized_text || "…"}
                </span>

                {/* Left edge (start time) */}
                <div
                  className="absolute left-0 top-0 bottom-0 w-2 cursor-ew-resize group"
                  title="Drag to change start time"
                  onPointerDown={(e) => handleEdgeDown(e, sub, "start")}
                  onPointerMove={handleEdgeMove}
                  onPointerUp={handleEdgeUp}
                >
                  <div className="w-1 h-full bg-black/0 group-hover:bg-white/70 transition-colors" />
                </div>

                {/* Right edge (end time) */}
                <div
                  className="absolute right-0 top-0 bottom-0 w-2 cursor-ew-resize group"
                  title="Drag to change end time"
                  onPointerDown={(e) => handleEdgeDown(e, sub, "end")}
                  onPointerMove={handleEdgeMove}
                  onPointerUp={handleEdgeUp}
                >
                  <div className="w-1 h-full ml-auto bg-black/0 group-hover:bg-white/70 transition-colors" />
                </div>
              </div>
            );
          })}

          {/* Playhead */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-white pointer-events-none z-20"
            style={{ left: currentTime * pps }}
          >
            <div className="absolute -top-0.5 left-1/2 -translate-x-1/2 w-2.5 h-2.5 rotate-45 bg-white rounded-sm" />
          </div>
        </div>
      </div>
    </section>
  );
}
