import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { CaptionOverlay } from "./CaptionOverlay";
import {
  IconFullscreen,
  IconMuted,
  IconPause,
  IconPlay,
  IconVolume,
} from "./icons";
import { secondsToMinutesSeconds } from "../../utils/timeUtils";

/**
 * Phase 4 — Center panel video preview.
 *
 * Plays the job's uploaded video and renders the styled caption overlay aligned
 * to the displayed video rect (letterboxing-aware). Playback time reaches the
 * parent two ways: a coarse ~15 fps report for the caption list / timeline /
 * style panel, and an un-throttled live playhead (getPlayheadTime) the caption
 * overlay reads every frame for smooth word highlighting — so the highlight
 * tracks speech within a frame without re-rendering the editor at full rate.
 */

const TIME_REPORT_INTERVAL = 1 / 15; // seconds between time updates

export const VideoStage = forwardRef(function VideoStage(
  {
    videoUrl,
    activeSubtitle,
    language,
    style,
    currentTime,
    onCurrentTime,
    onDuration,
    showSafeZone = false,
  },
  ref
) {
  const containerRef = useRef(null);
  const videoRef = useRef(null);
  const rafRef = useRef(0);
  const lastSentRef = useRef(0);
  const onCurrentTimeRef = useRef(onCurrentTime);
  // Live playhead, refreshed EVERY frame (un-throttled) for the word highlight.
  const playheadRef = useRef(0);

  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [highlightEnabled, setHighlightEnabled] = useState(true);
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 });
  const [naturalSize, setNaturalSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    onCurrentTimeRef.current = onCurrentTime;
  }, [onCurrentTime]);

  // Report playback time via rAF while playing. Two paths share one loop:
  //  • playheadRef — refreshed EVERY frame (un-throttled) so the caption overlay
  //    can read the live playhead directly (getPlayheadTime) instead of waiting
  //    on the ~15 Hz React state round-trip.
  //  • onCurrentTime — the coarse ~15 Hz update EditorPage's state still uses to
  //    drive SubtitleList / Timeline / StylePanel (TIME_REPORT_INTERVAL unchanged).
  useEffect(() => {
    const tick = () => {
      const video = videoRef.current;
      if (video) {
        playheadRef.current = video.currentTime;
        if (!video.paused && !video.ended) {
          const t = video.currentTime;
          if (Math.abs(t - lastSentRef.current) >= TIME_REPORT_INTERVAL) {
            lastSentRef.current = t;
            onCurrentTimeRef.current(t);
          }
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  // Track container size for the letterboxing-aware overlay rect.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setContainerSize({ w: rect.width, h: rect.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const seekTo = useCallback((time) => {
    const video = videoRef.current;
    if (!video) return;
    const max = Number.isFinite(video.duration) ? video.duration : time;
    const clamped = Math.max(0, Math.min(time, max));
    video.currentTime = clamped;
    lastSentRef.current = clamped;
    onCurrentTimeRef.current(clamped);
  }, []);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      video.play().catch(() => {});
    } else {
      video.pause();
    }
  }, []);

  // Stable imperative getter the caption overlay calls every frame to read the
  // LIVE playhead directly — bypasses the throttled React state round-trip so
  // the word highlight never triggers (or waits on) an EditorPage re-render.
  const getPlayheadTime = useCallback(
    () => (videoRef.current ? videoRef.current.currentTime : playheadRef.current),
    []
  );

  useImperativeHandle(ref, () => ({
    seek: seekTo,
    togglePlay,
  }));

  const handleLoadedMetadata = () => {
    const video = videoRef.current;
    if (!video) return;
    setDuration(video.duration || 0);
    setNaturalSize({
      w: video.videoWidth || 0,
      h: video.videoHeight || 0,
    });
    onDuration?.(video.duration || 0);
  };

  const handleSeekChange = (e) => {
    seekTo(parseFloat(e.target.value));
  };

  const handleVolumeChange = (e) => {
    const v = parseFloat(e.target.value);
    setVolume(v);
    setMuted(v === 0);
    if (videoRef.current) {
      videoRef.current.volume = v;
      videoRef.current.muted = v === 0;
    }
  };

  const toggleMute = () => {
    const video = videoRef.current;
    if (!video) return;
    const next = !video.muted;
    video.muted = next;
    setMuted(next);
  };

  const toggleFullscreen = () => {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      containerRef.current?.requestFullscreen?.().catch(() => {});
    }
  };

  // Rect of the video as displayed with object-contain inside the container.
  const videoRect = useMemo(() => {
    if (!containerSize.w || !containerSize.h || !naturalSize.w || !naturalSize.h) {
      return null;
    }
    const scale = Math.min(
      containerSize.w / naturalSize.w,
      containerSize.h / naturalSize.h
    );
    const w = naturalSize.w * scale;
    const h = naturalSize.h * scale;
    return {
      left: (containerSize.w - w) / 2,
      top: (containerSize.h - h) / 2,
      width: w,
      height: h,
    };
  }, [containerSize, naturalSize]);

  // Scale the caption font with the displayed video (fontSize is px @1080p).
  const captionFontPx = useMemo(() => {
    const base = videoRect ? videoRect.height : containerSize.h;
    if (!base) return style.fontSize;
    return Math.max(12, Math.round((style.fontSize * base) / 1080));
  }, [videoRect, containerSize.h, style.fontSize]);

  const overlayWrapStyle = videoRect
    ? {
        left: videoRect.left,
        top: videoRect.top,
        width: videoRect.width,
        height: videoRect.height,
        "--caption-font-size": `${captionFontPx}px`,
      }
    : {
        inset: 0,
        "--caption-font-size": `${style.fontSize}px`,
      };

  return (
    <div className="flex-1 min-h-[260px] lg:min-h-0 flex flex-col gap-2">
      <div
        ref={containerRef}
        className="relative flex-1 min-h-0 bg-black rounded-xl overflow-hidden border border-gray-800"
      >
        <video
          ref={videoRef}
          src={videoUrl}
          className="w-full h-full object-contain"
          preload="metadata"
          playsInline
          onLoadedMetadata={handleLoadedMetadata}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onEnded={() => setIsPlaying(false)}
          onSeeked={(e) => {
            const t = e.currentTarget.currentTime;
            lastSentRef.current = t;
            onCurrentTimeRef.current(t);
          }}
        />

        {/* Caption layer aligned to the displayed video rect */}
        <div className="absolute overflow-hidden" style={overlayWrapStyle}>
          <CaptionOverlay
            subtitle={activeSubtitle}
            language={language}
            style={style}
            getPlayheadTime={getPlayheadTime}
            showSafeZone={showSafeZone}
            highlightEnabled={highlightEnabled}
          />
        </div>

        {/* Click to play/pause */}
        <button
          type="button"
          onClick={togglePlay}
          className="absolute inset-0"
          aria-label={isPlaying ? "Pause" : "Play"}
        />
      </div>

      {/* Controls */}
      <div className="shrink-0 flex items-center gap-3 bg-gray-900 border border-gray-800 rounded-lg px-3 py-2">
        <button
          type="button"
          onClick={togglePlay}
          className="w-8 h-8 flex items-center justify-center rounded-full bg-emerald-500 text-gray-950 hover:bg-emerald-400 transition"
          title={isPlaying ? "Pause (Space)" : "Play (Space)"}
        >
          {isPlaying ? <IconPause size={16} /> : <IconPlay size={16} />}
        </button>

        <span className="text-xs font-mono text-gray-400 tabular-nums whitespace-nowrap">
          {secondsToMinutesSeconds(currentTime)} /{" "}
          {secondsToMinutesSeconds(duration)}
        </span>

        <input
          type="range"
          className="tf-range flex-1 min-w-0"
          min={0}
          max={duration || 0}
          step={0.01}
          value={Math.min(currentTime, duration || 0)}
          onChange={handleSeekChange}
          aria-label="Seek"
        />

        <button
          type="button"
          onClick={toggleMute}
          className="text-gray-400 hover:text-white transition"
          title={muted ? "Unmute" : "Mute"}
        >
          {muted || volume === 0 ? <IconMuted size={16} /> : <IconVolume size={16} />}
        </button>
        <input
          type="range"
          className="tf-range w-20 hidden sm:block"
          min={0}
          max={1}
          step={0.05}
          value={muted ? 0 : volume}
          onChange={handleVolumeChange}
          aria-label="Volume"
        />

        <button
          type="button"
          onClick={() => setHighlightEnabled((v) => !v)}
          className={`transition ${
            highlightEnabled ? "text-emerald-400" : "text-gray-600 hover:text-gray-400"
          }`}
          title={highlightEnabled ? "Hide word highlight" : "Show word highlight"}
        >
          <span className="relative inline-block">
            <span className="text-xs font-black tracking-tighter">Aa</span>
            {highlightEnabled && (
              <span className="absolute -bottom-0.5 left-1/2 -translate-x-1/2 w-4 h-0.5 rounded-full bg-emerald-400" />
            )}
          </span>
        </button>

        <button
          type="button"
          onClick={toggleFullscreen}
          className={`transition ${isFullscreen ? "text-emerald-400" : "text-gray-400 hover:text-white"}`}
          title="Fullscreen"
        >
          <IconFullscreen size={16} />
        </button>
      </div>
    </div>
  );
});
