import { useState, useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import { secondsToMinutesSeconds } from "../utils/timeUtils";
import { SubtitleOverlay } from "./SubtitleOverlay";

/**
 * Phase 4 — Video player component with subtitle synchronization.
 *
 * Displays the uploaded video and synchronizes subtitle display with playback time.
 */
export const VideoPlayer = forwardRef(function VideoPlayer(
  {
    videoUrl,
    subtitles = [],
    subtitleMode = "romanized",
    onSubtitleClick = () => {},
    onTimeUpdate = () => {},
  },
  ref
) {
  const videoRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [activeSubtitleId, setActiveSubtitleId] = useState(null);

  // Expose seek function to parent component
  useImperativeHandle(ref, () => ({
    seek: (time) => {
      if (videoRef.current) {
        videoRef.current.currentTime = time;
        setCurrentTime(time);
      }
    },
  }));

  // Find the current active subtitle based on playback time
  useEffect(() => {
    if (!subtitles || subtitles.length === 0) {
      setActiveSubtitleId(null);
      return;
    }

    const active = subtitles.find(
      (sub) => currentTime >= sub.start && currentTime <= sub.end
    );
    setActiveSubtitleId(active ? active.id : null);
    onTimeUpdate(currentTime, active);
  }, [currentTime, subtitles, onTimeUpdate]);

  const handlePlayPause = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
    } else {
      videoRef.current.play();
    }
    setIsPlaying(!isPlaying);
  };

  const handleLoadedMetadata = () => {
    if (!videoRef.current) return;
    setDuration(videoRef.current.duration);
  };

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    setCurrentTime(videoRef.current.currentTime);
  };

  const handleEnded = () => {
    setIsPlaying(false);
  };

  const handleSeek = (e) => {
    if (!videoRef.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const percent = (e.clientX - rect.left) / rect.width;
    const newTime = percent * duration;
    videoRef.current.currentTime = newTime;
    setCurrentTime(newTime);
  };

  const handleSliderChange = (e) => {
    const newTime = parseFloat(e.target.value);
    if (!videoRef.current) return;
    videoRef.current.currentTime = newTime;
    setCurrentTime(newTime);
  };

  // Get current active subtitle
  const currentSubtitle = subtitles.find((sub) => sub.id === activeSubtitleId);

  return (
    <div className="w-full space-y-4">
      {/* Video Player Container */}
      <div className="relative w-full bg-black rounded-lg overflow-hidden shadow-lg">
        {/* Video Element */}
        <video
          ref={videoRef}
          src={videoUrl}
          className="w-full aspect-video"
          onLoadedMetadata={handleLoadedMetadata}
          onTimeUpdate={handleTimeUpdate}
          onEnded={handleEnded}
        />

        {/* Subtitle Overlay */}
        <SubtitleOverlay subtitle={currentSubtitle} mode={subtitleMode} />

        {/* Play/Pause Button Overlay */}
        <button
          onClick={handlePlayPause}
          className="absolute inset-0 flex items-center justify-center opacity-0 hover:opacity-100 transition-opacity bg-black/20 group"
        >
          <div className="w-16 h-16 bg-white/80 rounded-full flex items-center justify-center group-hover:bg-white transition-colors">
            {isPlaying ? (
              <svg
                className="w-8 h-8 text-black ml-1"
                fill="currentColor"
                viewBox="0 0 24 24"
              >
                <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
              </svg>
            ) : (
              <svg
                className="w-8 h-8 text-black ml-1"
                fill="currentColor"
                viewBox="0 0 24 24"
              >
                <path d="M8 5v14l11-7z" />
              </svg>
            )}
          </div>
        </button>
      </div>

      {/* Controls */}
      <div className="space-y-3">
        {/* Timeline Scrubber */}
        <div className="space-y-1">
          <input
            type="range"
            min="0"
            max={duration || 0}
            step="0.1"
            value={currentTime}
            onChange={handleSliderChange}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-600"
          />
          {/* Time Display */}
          <div className="flex justify-between text-xs text-gray-400 px-1">
            <span>{secondsToMinutesSeconds(currentTime)}</span>
            <span>{secondsToMinutesSeconds(duration)}</span>
          </div>
        </div>

        {/* Play/Pause Button */}
        <button
          onClick={handlePlayPause}
          className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition"
        >
          {isPlaying ? "⏸ Pause" : "▶ Play"}
        </button>
      </div>
    </div>
  );
});
