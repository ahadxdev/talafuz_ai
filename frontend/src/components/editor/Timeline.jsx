import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatRulerTime } from "../../utils/subtitleUtils";

/**
 * Timeline with zoom, horizontal scroll, vertical resize, time ruler,
 * video track and caption track.
 *
 * Toolbar features:
 *   - Split at playhead (cut selected caption)
 *   - Zoom to fit (show entire duration)
 *   - Zoom in / out (1×–8×) with Ctrl+scroll
 *   - Current time / duration display
 *   - Snap-to-edges toggle
 *
 * Interaction:
 *   - Drag ruler or empty track space to scrub
 *   - Click a caption block to select; double-click to seek
 *   - Drag left/right block edges to trim (clamped to neighbours)
 *   - Drag the top edge of the panel to resize vertically
 */

const RULER_STEPS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
const ZOOM_MIN = 1;
const ZOOM_MAX = 8;
const ZOOM_STEP = 0.5;
const DEFAULT_HEIGHT = 180;
const MIN_HEIGHT = 100;
const MAX_HEIGHT = 500;
const SNAP_THRESHOLD_PX = 8; // snap when within 8px of an edge

// ── helpers ──────────────────────────────────────────────────────────

function formatTimeClock(seconds) {
  const s = Math.max(0, seconds || 0);
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

// ── component ────────────────────────────────────────────────────────

export function Timeline({
  subtitles,
  duration,
  currentTime,
  selectedId,
  onSeek,
  onSelect,
  onTrim,
  onSplitAtPlayhead,
}) {
  const trackRef = useRef(null);
  const scrollRef = useRef(null);
  const sectionRef = useRef(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [height, setHeight] = useState(() => {
    try {
      const v = localStorage.getItem("talafuz_timeline_height");
      if (v) return Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, +v));
    } catch { /* ignore */ }
    return DEFAULT_HEIGHT;
  });
  const [snap, setSnap] = useState(true);

  // Observe the visible scroll container width
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver((entries) => {
      setContainerWidth(entries[0].contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Persist height
  useEffect(() => {
    try { localStorage.setItem("talafuz_timeline_height", String(height)); } catch { /* */ }
  }, [height]);

  const safeDuration = useMemo(() => {
    if (Number.isFinite(duration) && duration > 0) return duration;
    return (
      (subtitles || []).reduce((max, s) => Math.max(max, s.end), 0) || 1
    );
  }, [duration, subtitles]);

  // Total scrollable width scales with zoom
  const totalWidth = Math.max(containerWidth * zoom, containerWidth);
  const pps = totalWidth / safeDuration;

  // Ruler ticks adapt to zoom
  const rulerStep = useMemo(
    () => RULER_STEPS.find((s) => safeDuration / (s * zoom) <= 15) ?? 300,
    [safeDuration, zoom]
  );

  const ticks = useMemo(() => {
    const arr = [];
    for (let t = 0; t <= safeDuration + 0.001; t += rulerStep) {
      arr.push(Math.round(t * 1000) / 1000);
    }
    return arr;
  }, [safeDuration, rulerStep]);

  // All snap-able edges (caption start/end times)
  const snapEdges = useMemo(() => {
    if (!snap) return [];
    const edges = [];
    (subtitles || []).forEach((s) => {
      edges.push(s.start, s.end);
    });
    return edges;
  }, [subtitles, snap]);

  const applySnap = useCallback(
    (time) => {
      if (!snap || snapEdges.length === 0) return time;
      const threshold = SNAP_THRESHOLD_PX / pps; // convert px threshold to seconds
      let best = time;
      let bestDist = threshold;
      for (const edge of snapEdges) {
        const d = Math.abs(time - edge);
        if (d < bestDist) {
          bestDist = d;
          best = edge;
        }
      }
      return best;
    },
    [snap, snapEdges, pps]
  );

  const timeAt = useCallback(
    (clientX) => {
      const el = scrollRef.current;
      if (!el) return 0;
      const rect = el.getBoundingClientRect();
      const x = clientX - rect.left + el.scrollLeft;
      const raw = Math.min(Math.max(x / pps, 0), safeDuration);
      return applySnap(raw);
    },
    [pps, safeDuration, applySnap]
  );

  // Auto-scroll so the playhead stays in view
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const playheadX = currentTime * pps;
    const { scrollLeft, clientWidth } = el;
    if (playheadX < scrollLeft + 40) {
      el.scrollLeft = Math.max(0, playheadX - 40);
    } else if (playheadX > scrollLeft + clientWidth - 40) {
      el.scrollLeft = playheadX - clientWidth + 40;
    }
  }, [currentTime, pps]);

  // Scroll to selected caption
  useEffect(() => {
    if (selectedId == null) return;
    const sub = (subtitles || []).find((s) => s.id === selectedId);
    if (!sub) return;
    const el = scrollRef.current;
    if (!el) return;
    const cx = ((sub.start + sub.end) / 2) * pps;
    const { scrollLeft, clientWidth } = el;
    if (cx < scrollLeft + 60 || cx > scrollLeft + clientWidth - 60) {
      el.scrollLeft = cx - clientWidth / 2;
    }
  }, [selectedId, subtitles, pps]);

  // ── Zoom controls ──
  const zoomIn = () =>
    setZoom((z) => Math.min(ZOOM_MAX, +(z + ZOOM_STEP).toFixed(1)));
  const zoomOut = () =>
    setZoom((z) => Math.max(ZOOM_MIN, +(z - ZOOM_STEP).toFixed(1)));
  const zoomReset = () => setZoom(1);

  const handleZoomChange = (newZoom) => {
    const el = scrollRef.current;
    if (el) {
      const ratio = (el.scrollLeft + el.clientWidth / 2) / totalWidth;
      requestAnimationFrame(() => {
        const newTotal = Math.max(containerWidth * newZoom, containerWidth);
        el.scrollLeft = ratio * newTotal - el.clientWidth / 2;
      });
    }
    setZoom(newZoom);
  };

  const zoomToFit = () => {
    setZoom(1);
    const el = scrollRef.current;
    if (el) el.scrollLeft = 0;
  };

  // Ctrl+scroll = zoom
  const handleWheel = (e) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.25 : 0.25;
      handleZoomChange(
        Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, +(zoom + delta).toFixed(1)))
      );
    }
  };

  // ── Vertical resize (drag top edge) ──
  const vertResizeRef = useRef(null);

  const handleVertResizeStart = (e) => {
    e.preventDefault();
    vertResizeRef.current = { startY: e.clientY, startHeight: height };
    const onMove = (ev) => {
      const r = vertResizeRef.current;
      if (!r) return;
      // Dragging UP increases height (negative deltaY)
      const newH = r.startHeight - (ev.clientY - r.startY);
      setHeight(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, newH)));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      vertResizeRef.current = null;
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
  };

  // ── Scrubbing ──
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
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* */ }
  };

  // ── Caption block edge trimming ──
  const trimRef = useRef(null);

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
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* */ }
  };

  // ── Selected caption for split button ──
  const selectedSub = useMemo(
    () => (subtitles || []).find((s) => s.id === selectedId) || null,
    [subtitles, selectedId]
  );
  const canSplitAtPlayhead =
    !!selectedSub &&
    currentTime > selectedSub.start + 0.05 &&
    currentTime < selectedSub.end - 0.05;

  // ── Render ──
  return (
    <section
      ref={sectionRef}
      className="shrink-0 bg-gray-900 border border-gray-800 rounded-xl flex flex-col relative"
      style={{ height }}
    >
      {/* Vertical resize handle (top edge) */}
      <div
        className="absolute -top-1 left-4 right-4 h-2 cursor-row-resize z-30 group"
        onMouseDown={handleVertResizeStart}
        title="Drag to resize timeline height"
      >
        <div className="mx-auto w-12 h-1 rounded-full bg-gray-700 group-hover:bg-emerald-500/60 transition-colors mt-0.5" />
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 pt-3 pb-1.5 gap-2 flex-wrap">
        {/* Left — title + actions */}
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
            Timeline
          </h3>

          {/* Split at playhead */}
          <button
            type="button"
            onClick={() => onSplitAtPlayhead?.()}
            disabled={!canSplitAtPlayhead}
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-gray-800 text-gray-300 hover:bg-gray-700 transition disabled:opacity-30 disabled:cursor-not-allowed"
            title="Split selected caption at the playhead position"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-3 h-3">
              <path fillRule="evenodd" d="M10 3a.75.75 0 0 1 .75.75v3.5h3.5a.75.75 0 0 1 0 1.5h-3.5v3.5a.75.75 0 0 1-1.5 0v-3.5h-3.5a.75.75 0 0 1 0-1.5h3.5v-3.5A.75.75 0 0 1 10 3Z" clipRule="evenodd" />
            </svg>
            Split
          </button>

          {/* Snap toggle */}
          <button
            type="button"
            onClick={() => setSnap((v) => !v)}
            className={`px-2 py-1 rounded text-[10px] font-medium transition ${
              snap
                ? "bg-emerald-500/20 text-emerald-400"
                : "bg-gray-800 text-gray-500 hover:bg-gray-700"
            }`}
            title={snap ? "Snap to edges: ON" : "Snap to edges: OFF"}
          >
            Snap
          </button>
        </div>

        {/* Right — time + zoom */}
        <div className="flex items-center gap-2">
          {/* Time display */}
          <span className="text-[10px] font-mono text-gray-500 tabular-nums">
            <span className="text-gray-300">{formatTimeClock(currentTime)}</span>
            {" / "}
            {formatTimeClock(safeDuration)}
          </span>

          {/* Zoom to fit */}
          <button
            type="button"
            onClick={zoomToFit}
            className="px-2 py-1 rounded text-[10px] font-medium bg-gray-800 text-gray-400 hover:bg-gray-700 transition"
            title="Zoom to fit — show entire timeline"
          >
            Fit
          </button>

          {/* Zoom controls */}
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={zoomOut}
              disabled={zoom <= ZOOM_MIN}
              className="w-6 h-6 flex items-center justify-center rounded bg-gray-800 text-gray-300 hover:bg-gray-700 transition text-sm font-bold disabled:opacity-30 disabled:cursor-not-allowed"
              title="Zoom out"
            >
              −
            </button>
            <button
              type="button"
              onClick={zoomReset}
              className="px-2 h-6 flex items-center justify-center rounded bg-gray-800 text-[10px] text-gray-400 hover:bg-gray-700 transition font-mono min-w-[40px]"
              title="Reset zoom"
            >
              {zoom.toFixed(1)}×
            </button>
            <button
              type="button"
              onClick={zoomIn}
              disabled={zoom >= ZOOM_MAX}
              className="w-6 h-6 flex items-center justify-center rounded bg-gray-800 text-gray-300 hover:bg-gray-700 transition text-sm font-bold disabled:opacity-30 disabled:cursor-not-allowed"
              title="Zoom in"
            >
              +
            </button>
          </div>
        </div>
      </div>

      {/* Scrollable track area */}
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden tf-scroll select-none px-3 pb-3"
        style={{ minWidth: 0 }}
        onWheel={handleWheel}
      >
        <div style={{ width: totalWidth, minWidth: "100%" }}>
          {/* Time ruler */}
          <div
            className="relative h-6 cursor-pointer touch-none border-b border-gray-800"
            onPointerDown={handleScrubDown}
            onPointerMove={handleScrubMove}
            onPointerUp={handleScrubUp}
          >
            {ticks.map((t) => {
              const x = t * pps;
              const isMajor = t % (rulerStep * 5) < 0.001 || t === 0;
              return (
                <div key={t} className="absolute bottom-0" style={{ left: x }}>
                  <div
                    className={`w-px ${isMajor ? "h-3 bg-gray-500" : "h-1.5 bg-gray-700"}`}
                  />
                  <span className="absolute bottom-full left-1 -translate-x-0 text-[9px] text-gray-500 font-mono whitespace-nowrap">
                    {formatRulerTime(t)}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Video track */}
          <div className="relative h-5 my-1 rounded bg-purple-500/20 border border-purple-500/30 flex items-center px-2">
            <span className="text-[10px] text-purple-300/70 font-medium">
              Video
            </span>
          </div>

          {/* Caption track */}
          <div
            ref={trackRef}
            className="relative rounded bg-gray-950/80 border border-gray-800 cursor-crosshair overflow-hidden touch-none"
            style={{ height: Math.max(48, height - 110) }}
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
                  className={`absolute top-1 bottom-1 rounded flex items-center px-1.5 overflow-hidden transition-shadow ${
                    isSelected
                      ? "bg-blue-500/90 ring-2 ring-blue-400 z-10 shadow-lg shadow-blue-500/20"
                      : "bg-amber-400/80 hover:bg-amber-300/90"
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
                  <span className="text-[10px] font-medium text-gray-950 truncate pointer-events-none leading-tight">
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

            {/* Snap indicator lines at edges (subtle) */}
            {snap &&
              snapEdges.map((edge, i) => (
                <div
                  key={i}
                  className="absolute top-0 bottom-0 w-px bg-emerald-500/10 pointer-events-none"
                  style={{ left: edge * pps }}
                />
              ))}
          </div>
        </div>
      </div>
    </section>
  );
}
