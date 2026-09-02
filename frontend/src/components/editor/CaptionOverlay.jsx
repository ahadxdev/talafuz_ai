import { getActiveWordIndex, getSubtitleText } from "../../utils/subtitleUtils";

/**
 * Phase 4 — Caption overlay rendered over the video preview.
 *
 * Draws the active subtitle with the editor style (font, colors, outline,
 * shadow, background, animation) and optional per-word highlighting.
 * Real word-level timestamps (`subtitle.words`) are used when available;
 * otherwise the active word is estimated proportionally across the cue.
 */

function hexToRgba(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return `rgba(0, 0, 0, ${alpha})`;
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function CaptionOverlay({
  subtitle,
  language,
  style,
  currentTime,
  showSafeZone = false,
}) {
  const text = getSubtitleText(subtitle, language) || "";
  const words = text.split(/\s+/).filter(Boolean);
  const activeWordIndex =
    style.wordHighlight && words.length > 0 && subtitle
      ? getActiveWordIndex(words, subtitle, currentTime)
      : -1;

  const animClass =
    style.animation && style.animation !== "none"
      ? `tf-anim-${style.animation}`
      : "";
  const hasBackground = (style.backgroundOpacity || 0) > 0;

  return (
    <div className="absolute inset-0 pointer-events-none select-none">
      {showSafeZone && (
        <div
          className="absolute border-2 border-dashed border-white/25 rounded-lg"
          style={{ left: "5%", top: "5%", right: "5%", bottom: "5%" }}
        >
          <span className="absolute left-2 -top-0.5 -translate-y-full text-[10px] tracking-widest text-white/40">
            SAFE ZONE
          </span>
        </div>
      )}

      {words.length > 0 && subtitle && (
        <div
          key={subtitle.id}
          className={`absolute ${animClass}`}
          style={{
            left: `${style.posX}%`,
            top: `${style.posY}%`,
            maxWidth: `${style.boxWidth ?? 88}%`,
            transform: "translate(-50%, -50%)",
            textAlign: style.alignment,
            ...(hasBackground
              ? {
                  backgroundColor: hexToRgba(
                    style.backgroundColor,
                    style.backgroundOpacity
                  ),
                  padding: "0.15em 0.5em",
                  borderRadius: "0.25em",
                }
              : {}),
          }}
        >
          <span
            className={`tf-text-outline ${style.uppercase ? "uppercase" : ""}`}
            style={{
              display: "inline-block",
              fontFamily: `"${style.fontFamily}", Inter, sans-serif`,
              fontSize: "var(--caption-font-size, 45px)",
              fontWeight: style.fontWeight,
              color: style.textColor,
              letterSpacing: style.letterSpacing
                ? `${style.letterSpacing}px`
                : "normal",
              lineHeight: style.lineHeight,
              textShadow: style.shadow
                ? `0 2px 6px rgba(0, 0, 0, ${style.shadowOpacity})`
                : "none",
              WebkitTextStroke:
                style.outlineWidth > 0
                  ? `${style.outlineWidth}px ${style.outlineColor}`
                  : undefined,
            }}
          >
            {words.map((word, i) => {
              const isHighlighted = i === activeWordIndex;
              return (
                <span
                  key={`${i}-${word}`}
                  style={
                    isHighlighted
                      ? {
                          color: style.highlightColor,
                          textShadow: `0 0 12px ${hexToRgba(
                            style.highlightColor,
                            0.55
                          )}, 0 2px 6px rgba(0, 0, 0, ${style.shadowOpacity})`,
                        }
                      : undefined
                  }
                >
                  {word}
                  {i < words.length - 1 ? " " : ""}
                </span>
              );
            })}
          </span>
        </div>
      )}
    </div>
  );
}
