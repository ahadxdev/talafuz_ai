import { useEffect, useState } from "react";
import {
  ANIMATIONS,
  CAPTION_PRESETS,
  FONT_FAMILIES,
  FONT_WEIGHTS,
  POSITION_PRESETS,
} from "../../utils/captionStyles";
import { getSubtitleText } from "../../utils/subtitleUtils";

/**
 * Phase 4 — Right panel: caption properties.
 *
 * TEXT     — selected subtitle text (editable, except the read-only original
 *            ASR text) + typography.
 * STYLE    — colors, outline, shadow, word highlighting and presets.
 * POSITION — X/Y placement with quick presets and the safe-zone indicator.
 * ANIMATION— simple caption entrance animations.
 *
 * Slider changes stream in live (`onStyleChange`); history is committed once
 * per interaction via `onStyleCommit`.
 */

const TABS = [
  { id: "text", label: "Text" },
  { id: "style", label: "Style" },
  { id: "position", label: "Position" },
  { id: "animation", label: "Animation" },
];

const ALIGNMENTS = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];

function Section({ title, children }) {
  return (
    <div className="space-y-2.5">
      <h4 className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
        {title}
      </h4>
      {children}
    </div>
  );
}

function SliderRow({ label, value, min, max, step = 1, format, onChange, onCommit }) {
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between items-center text-xs">
        <span className="text-gray-400">{label}</span>
        <span className="text-gray-300 font-mono">
          {format ? format(value) : value}
        </span>
      </div>
      <input
        type="range"
        className="tf-range w-full"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        onPointerUp={onCommit}
        onKeyUp={onCommit}
      />
    </div>
  );
}

function ColorRow({ label, value, onChange, onCommit }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-gray-400">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-gray-500 font-mono uppercase">
          {value}
        </span>
        <input
          type="color"
          className="tf-color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onCommit}
        />
      </div>
    </div>
  );
}

function ToggleRow({ label, checked, onChange, onCommit }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-gray-400">{label}</span>
      <button
        type="button"
        onClick={() => {
          onChange(!checked);
          onCommit?.();
        }}
        className={`relative w-9 h-5 rounded-full transition ${
          checked ? "bg-emerald-500" : "bg-gray-700"
        }`}
        role="switch"
        aria-checked={checked}
      >
        <span
          className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${
            checked ? "left-4.5 translate-x-0" : "left-0.5"
          }`}
          style={{ left: checked ? "1.125rem" : "0.125rem" }}
        />
      </button>
    </div>
  );
}

const selectClass =
  "w-full bg-gray-950 border border-gray-700 rounded-md px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-emerald-500";

export function StylePanel({
  className = "",
  style,
  onStyleChange,
  onStyleCommit,
  selectedSubtitle,
  language,
  onEditText,
  onEditCommit,
  showSafeZone,
  onToggleSafeZone,
}) {
  const [tab, setTab] = useState("text");
  const [textDraft, setTextDraft] = useState("");
  const selectedId = selectedSubtitle?.id ?? null;

  // Sync the text draft when the selection or language changes.
  useEffect(() => {
    setTextDraft(getSubtitleText(selectedSubtitle, language));
  }, [selectedId, language]);

  // Original and Urdu are read-only display views (Urdu is derived from the
  // original ASR text on the fly), so neither offers inline text editing.
  const isOriginal = language === "original" || language === "urdu";

  return (
    <aside
      className={`${className} flex flex-col min-h-0 bg-gray-900 border border-gray-800 rounded-xl`}
    >
      {/* Tabs */}
      <div className="shrink-0 flex border-b border-gray-800">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`flex-1 px-2 py-2.5 text-xs font-medium transition ${
              tab === t.id
                ? "text-emerald-400 border-b-2 border-emerald-400 bg-gray-900"
                : "text-gray-500 hover:text-gray-300 border-b-2 border-transparent"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="tf-scroll flex-1 min-h-0 overflow-y-auto p-3 space-y-5">
        {/* ------------------------------ TEXT ------------------------------ */}
        {tab === "text" && (
          <>
            <Section title={`Subtitle text · ${language}`}>
              {!selectedSubtitle ? (
                <p className="text-xs text-gray-500 py-2">
                  Select a caption in the list to edit its text.
                </p>
              ) : isOriginal ? (
                <>
                  <textarea
                    rows={4}
                    readOnly
                    value={textDraft}
                    className="w-full bg-gray-950/50 border border-gray-800 rounded-md px-2 py-1.5 text-xs text-gray-400 resize-none cursor-default"
                  />
                  <p className="text-[10px] text-gray-600">
                    {language === "urdu"
                      ? "Urdu is a read-only script view of the original ASR text."
                      : "Original ASR text is read-only."}
                  </p>
                </>
              ) : (
                <>
                  <textarea
                    rows={4}
                    value={textDraft}
                    onChange={(e) => {
                      setTextDraft(e.target.value);
                      onEditText(selectedSubtitle.id, e.target.value);
                    }}
                    onBlur={onEditCommit}
                    className="w-full bg-gray-950 border border-gray-700 rounded-md px-2 py-1.5 text-xs text-gray-100 resize-none focus:outline-none focus:border-emerald-500"
                  />
                  {language === "english" &&
                    !selectedSubtitle.english_text && (
                      <p className="text-[10px] text-gray-600">
                        No English translation was generated for this caption —
                        typing here adds one.
                      </p>
                    )}
                </>
              )}
            </Section>

            <Section title="Typography">
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  Font family
                </label>
                <select
                  className={selectClass}
                  value={style.fontFamily}
                  onChange={(e) => {
                    onStyleChange({ fontFamily: e.target.value });
                    onStyleCommit();
                  }}
                >
                  {FONT_FAMILIES.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </div>

              <SliderRow
                label="Font size"
                value={style.fontSize}
                min={16}
                max={96}
                format={(v) => `${v} px`}
                onChange={(v) => onStyleChange({ fontSize: v })}
                onCommit={onStyleCommit}
              />

              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  Font weight
                </label>
                <select
                  className={selectClass}
                  value={style.fontWeight}
                  onChange={(e) => {
                    onStyleChange({ fontWeight: parseInt(e.target.value, 10) });
                    onStyleCommit();
                  }}
                >
                  {FONT_WEIGHTS.map((w) => (
                    <option key={w} value={w}>
                      {w}
                      {w === 400 ? " (Regular)" : w === 700 ? " (Bold)" : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  Text alignment
                </label>
                <div className="flex rounded-md overflow-hidden border border-gray-700">
                  {ALIGNMENTS.map((a) => (
                    <button
                      key={a.value}
                      type="button"
                      onClick={() => {
                        onStyleChange({ alignment: a.value });
                        onStyleCommit();
                      }}
                      className={`flex-1 py-1.5 text-[11px] transition ${
                        style.alignment === a.value
                          ? "bg-emerald-500 text-gray-950 font-medium"
                          : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                      }`}
                    >
                      {a.label}
                    </button>
                  ))}
                </div>
              </div>

              <SliderRow
                label="Letter spacing"
                value={style.letterSpacing}
                min={0}
                max={10}
                step={0.5}
                format={(v) => `${v} px`}
                onChange={(v) => onStyleChange({ letterSpacing: v })}
                onCommit={onStyleCommit}
              />

              <SliderRow
                label="Line height"
                value={style.lineHeight}
                min={0.9}
                max={2}
                step={0.05}
                format={(v) => v.toFixed(2)}
                onChange={(v) => onStyleChange({ lineHeight: v })}
                onCommit={onStyleCommit}
              />

              <ToggleRow
                label="Uppercase"
                checked={style.uppercase}
                onChange={(v) => onStyleChange({ uppercase: v })}
                onCommit={onStyleCommit}
              />
            </Section>
          </>
        )}

        {/* ----------------------------- STYLE ------------------------------ */}
        {tab === "style" && (
          <>
            <Section title="Presets">
              <div className="grid grid-cols-3 gap-1.5">
                {CAPTION_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => {
                      onStyleChange(preset.style);
                      onStyleCommit();
                    }}
                    className="flex flex-col items-center gap-1.5 py-2.5 rounded-lg bg-gray-950/70 border border-gray-800 hover:border-emerald-500/60 transition"
                    title={preset.name}
                  >
                    <span
                      className="tf-text-outline leading-none"
                      style={{
                        fontFamily: `"${preset.style.fontFamily}", sans-serif`,
                        fontWeight: preset.style.fontWeight,
                        fontSize: 15,
                        color: preset.style.textColor,
                        WebkitTextStroke:
                          preset.style.outlineWidth > 0
                            ? `1px ${preset.style.outlineColor}`
                            : undefined,
                      }}
                    >
                      Aa
                    </span>
                    <span className="text-[10px] text-gray-400">
                      {preset.name}
                    </span>
                  </button>
                ))}
              </div>
            </Section>

            <Section title="Colors">
              <ColorRow
                label="Text color"
                value={style.textColor}
                onChange={(v) => onStyleChange({ textColor: v })}
                onCommit={onStyleCommit}
              />
              <ColorRow
                label="Background color"
                value={style.backgroundColor}
                onChange={(v) => onStyleChange({ backgroundColor: v })}
                onCommit={onStyleCommit}
              />
              <SliderRow
                label="Background opacity"
                value={Math.round(style.backgroundOpacity * 100)}
                min={0}
                max={100}
                format={(v) => `${v}%`}
                onChange={(v) => onStyleChange({ backgroundOpacity: v / 100 })}
                onCommit={onStyleCommit}
              />
            </Section>

            <Section title="Caption box">
              <SliderRow
                label="Box width"
                value={Math.round(style.boxWidth ?? 95)}
                min={30}
                max={100}
                step={1}
                format={(v) => `${v}%`}
                onChange={(v) => onStyleChange({ boxWidth: v })}
                onCommit={onStyleCommit}
              />
              <p className="text-[10px] text-gray-600 leading-relaxed">
                Maximum width the caption text can span across the video.
                Text wraps inside this box; it also applies to exported
                videos.
              </p>
            </Section>

            <Section title="Outline">
              <ColorRow
                label="Outline color"
                value={style.outlineColor}
                onChange={(v) => onStyleChange({ outlineColor: v })}
                onCommit={onStyleCommit}
              />
              <SliderRow
                label="Outline width"
                value={style.outlineWidth}
                min={0}
                max={8}
                step={0.5}
                format={(v) => `${v} px`}
                onChange={(v) => onStyleChange({ outlineWidth: v })}
                onCommit={onStyleCommit}
              />
            </Section>

            <Section title="Shadow">
              <ToggleRow
                label="Text shadow"
                checked={style.shadow}
                onChange={(v) => onStyleChange({ shadow: v })}
                onCommit={onStyleCommit}
              />
              {style.shadow && (
                <SliderRow
                  label="Shadow opacity"
                  value={Math.round(style.shadowOpacity * 100)}
                  min={0}
                  max={100}
                  format={(v) => `${v}%`}
                  onChange={(v) => onStyleChange({ shadowOpacity: v / 100 })}
                  onCommit={onStyleCommit}
                />
              )}
            </Section>

            <Section title="Word highlighting">
              <ToggleRow
                label="Highlight active word"
                checked={style.wordHighlight}
                onChange={(v) => onStyleChange({ wordHighlight: v })}
                onCommit={onStyleCommit}
              />
              {style.wordHighlight && (
                <ColorRow
                  label="Highlight color"
                  value={style.highlightColor}
                  onChange={(v) => onStyleChange({ highlightColor: v })}
                  onCommit={onStyleCommit}
                />
              )}
              <p className="text-[10px] text-gray-600 leading-relaxed">
                Words are highlighted in proportion to their estimated
                speaking time — longer words and punctuation pauses hold
                the highlight longer, matching the exported video.
              </p>
            </Section>
          </>
        )}

        {/* ---------------------------- POSITION ---------------------------- */}
        {tab === "position" && (
          <>
            <Section title="Quick presets">
              <div className="flex rounded-md overflow-hidden border border-gray-700">
                {POSITION_PRESETS.map((p) => (
                  <button
                    key={p.value}
                    type="button"
                    onClick={() => {
                      onStyleChange({ position: p.value, posY: p.posY });
                      onStyleCommit();
                    }}
                    className={`flex-1 py-1.5 text-[11px] transition ${
                      style.position === p.value
                        ? "bg-emerald-500 text-gray-950 font-medium"
                        : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </Section>

            <Section title="Custom position">
              <SliderRow
                label="Horizontal (X)"
                value={Number(style.posX.toFixed(1))}
                min={0}
                max={100}
                step={0.5}
                format={(v) => `${v.toFixed(1)}%`}
                onChange={(v) =>
                  onStyleChange({ posX: v, position: "custom" })
                }
                onCommit={onStyleCommit}
              />
              <SliderRow
                label="Vertical (Y)"
                value={Number(style.posY.toFixed(1))}
                min={0}
                max={100}
                step={0.5}
                format={(v) => `${v.toFixed(1)}%`}
                onChange={(v) =>
                  onStyleChange({ posY: v, position: "custom" })
                }
                onCommit={onStyleCommit}
              />
            </Section>

            <Section title="Preview guides">
              <ToggleRow
                label="Safe zone indicator"
                checked={showSafeZone}
                onChange={onToggleSafeZone}
              />
            </Section>
          </>
        )}

        {/* ---------------------------- ANIMATION --------------------------- */}
        {tab === "animation" && (
          <Section title="Entrance animation">
            <div className="grid grid-cols-2 gap-1.5">
              {ANIMATIONS.map((a) => (
                <button
                  key={a.value}
                  type="button"
                  onClick={() => {
                    onStyleChange({ animation: a.value });
                    onStyleCommit();
                  }}
                  className={`py-2 text-xs rounded-md border transition ${
                    style.animation === a.value
                      ? "bg-emerald-500/15 border-emerald-500 text-emerald-400 font-medium"
                      : "bg-gray-950/70 border-gray-800 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  {a.label}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-gray-600 leading-relaxed">
              Animations replay each time a caption appears. Select a caption
              and scrub the timeline to preview.
            </p>
          </Section>
        )}
      </div>
    </aside>
  );
}
