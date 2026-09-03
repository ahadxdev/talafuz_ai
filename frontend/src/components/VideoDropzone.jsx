import { useRef, useState } from "react";
import { IconUpload } from "./editor/icons";

const ACCEPTED_EXTENSIONS = [".mp4", ".mov", ".webm"];
const MAX_SIZE_MB = 500;

/**
 * Phase 5 — Video drop zone for the redesigned home page.
 *
 * Purely presentational: validates the picked file client-side and hands it
 * to `onFile` — the upload itself (and the automatic pipeline that follows)
 * is orchestrated by the Home page.
 */
export function VideoDropzone({ onFile }) {
  const inputRef = useRef(null);
  const [dragActive, setDragActive] = useState(false);
  const [localError, setLocalError] = useState("");

  const validate = (file) => {
    const name = (file.name || "").toLowerCase();
    if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
      return `Unsupported format. Supported: ${ACCEPTED_EXTENSIONS.join(", ")}`;
    }
    if (file.size === 0) return "File is empty.";
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      return `File is too large. Maximum size is ${MAX_SIZE_MB}MB.`;
    }
    return "";
  };

  const handleFile = (file) => {
    if (!file) return;
    const problem = validate(file);
    if (problem) {
      setLocalError(problem);
      return;
    }
    setLocalError("");
    onFile(file);
  };

  const openPicker = () => inputRef.current?.click();

  return (
    <div className="w-full max-w-2xl">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_EXTENSIONS.join(",") + ",video/mp4,video/quicktime,video/webm"}
        onChange={(e) => {
          handleFile(e.target.files?.[0]);
          e.target.value = "";
        }}
        className="hidden"
      />
      <div
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setDragActive(false);
          handleFile(e.dataTransfer.files?.[0]);
        }}
        onClick={openPicker}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPicker();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Upload a video"
        className={`border-2 border-dashed rounded-xl p-10 sm:p-14 text-center cursor-pointer transition ${
          dragActive
            ? "border-emerald-500 bg-emerald-500/5"
            : "border-gray-700 bg-gray-900/50 hover:border-gray-500"
        }`}
      >
        <div className="w-14 h-14 mx-auto rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 mb-4">
          <IconUpload size={22} />
        </div>
        <p className="text-base sm:text-lg font-semibold text-gray-100">
          Drop your video here
        </p>
        <p className="text-xs text-gray-500 mt-1.5">
          or click to browse — MP4, MOV, WEBM up to {MAX_SIZE_MB}MB
        </p>
        <p className="text-[11px] text-gray-600 mt-4">
          Transcription, subtitles and the editor run automatically after
          upload.
        </p>
      </div>
      {localError && (
        <p className="mt-3 text-center text-xs text-red-400">{localError}</p>
      )}
    </div>
  );
}
