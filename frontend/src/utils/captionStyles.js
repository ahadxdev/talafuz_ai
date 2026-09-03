/**
 * Caption style model + presets for the Phase 4 editor.
 *
 * The style is editor-level (applied to the whole caption track) and is
 * persisted alongside the subtitles through the save endpoint.
 * fontSize is expressed in px at 1080p video height; the preview scales it
 * to the displayed video size.
 */

export const DEFAULT_CAPTION_STYLE = {
  fontFamily: "Inter",
  fontSize: 45,
  fontWeight: 700,
  uppercase: false,
  textColor: "#FFFFFF",
  backgroundColor: "#000000",
  backgroundOpacity: 0,
  outlineColor: "#000000",
  outlineWidth: 0,
  shadow: true,
  shadowOpacity: 0.6,
  alignment: "center", // left | center | right
  position: "bottom", // top | center | bottom
  posX: 50, // % of video width
  posY: 82.9, // % of video height
  animation: "none", // none | fade | pop | slide-up | slide-down
  letterSpacing: 0,
  lineHeight: 1.2,
  wordHighlight: true,
  highlightColor: "#22C55E",
  boxWidth: 95, // % of video width the caption box may span
};

export const FONT_FAMILIES = [
  "Inter",
  "Poppins",
  "Montserrat",
  "Roboto Mono",
  "Georgia",
  "Courier New",
];

export const FONT_WEIGHTS = [400, 500, 600, 700, 800, 900];

export const ANIMATIONS = [
  { value: "none", label: "None" },
  { value: "fade", label: "Fade" },
  { value: "pop", label: "Pop" },
  { value: "slide-up", label: "Slide Up" },
  { value: "slide-down", label: "Slide Down" },
];

export const POSITION_PRESETS = [
  { value: "top", label: "Top", posY: 10 },
  { value: "center", label: "Center", posY: 50 },
  { value: "bottom", label: "Bottom", posY: 82.9 },
];

export const CAPTION_PRESETS = [
  {
    id: "classic",
    name: "Classic",
    style: {
      fontFamily: "Inter",
      fontSize: 45,
      fontWeight: 600,
      uppercase: false,
      textColor: "#FFFFFF",
      backgroundOpacity: 0,
      outlineWidth: 0,
      shadow: true,
      shadowOpacity: 0.6,
      wordHighlight: false,
    },
  },
  {
    id: "bold",
    name: "Bold",
    style: {
      fontFamily: "Poppins",
      fontSize: 52,
      fontWeight: 800,
      uppercase: true,
      textColor: "#FFFFFF",
      backgroundOpacity: 0,
      outlineColor: "#000000",
      outlineWidth: 3,
      shadow: true,
      shadowOpacity: 0.7,
      wordHighlight: false,
    },
  },
  {
    id: "minimal",
    name: "Minimal",
    style: {
      fontFamily: "Inter",
      fontSize: 36,
      fontWeight: 500,
      uppercase: false,
      textColor: "#FFFFFF",
      backgroundOpacity: 0,
      outlineWidth: 0,
      shadow: false,
      shadowOpacity: 0,
      wordHighlight: false,
    },
  },
  {
    id: "highlight",
    name: "Highlight",
    style: {
      fontFamily: "Inter",
      fontSize: 44,
      fontWeight: 700,
      uppercase: true,
      textColor: "#FFFFFF",
      backgroundOpacity: 0,
      outlineWidth: 0,
      shadow: true,
      shadowOpacity: 0.6,
      wordHighlight: true,
      highlightColor: "#22C55E",
    },
  },
  {
    id: "creator",
    name: "Creator",
    style: {
      fontFamily: "Montserrat",
      fontSize: 46,
      fontWeight: 800,
      uppercase: true,
      textColor: "#FACC15",
      backgroundOpacity: 0,
      outlineColor: "#000000",
      outlineWidth: 4,
      shadow: true,
      shadowOpacity: 0.75,
      wordHighlight: true,
      highlightColor: "#22C55E",
    },
  },
];

/** Merge a persisted/partial style over the defaults. */
export function mergeCaptionStyle(style) {
  return { ...DEFAULT_CAPTION_STYLE, ...(style || {}) };
}
