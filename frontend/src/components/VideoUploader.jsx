import { useRef, useState } from "react";
import { api } from "../services/api";

export function VideoUploader({ onVideoSelect, onUploadStart, onUploadComplete, onError }) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const fileInputRef = useRef(null);

  // Open the native file picker explicitly. (A <button> nested inside a
  // <label> swallows the click instead of activating the hidden input, so
  // the trigger is driven from a ref.)
  const openFilePicker = () => fileInputRef.current?.click();

  const handleFileSelect = (file) => {
    if (file) {
      setSelectedFile(file);
      onVideoSelect?.(file);
      setUploadProgress(0);
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const file = e.dataTransfer.files[0];
    handleFileSelect(file);
  };

  const handleFileInputChange = (e) => {
    const file = e.target.files?.[0];
    handleFileSelect(file);
    // Reset so choosing the same file again (e.g. after removing it)
    // still fires onChange.
    e.target.value = "";
  };

  const handleRemoveFile = () => {
    setSelectedFile(null);
    setUploadProgress(0);
  };

  const handleUpload = async () => {
    if (!selectedFile) {
      onError?.("No file selected");
      return;
    }

    setIsUploading(true);
    onUploadStart?.();

    try {
      // Simulate progress
      setUploadProgress(30);
      await new Promise((r) => setTimeout(r, 200));

      const response = await api.uploadVideo(selectedFile);

      setUploadProgress(100);
      await new Promise((r) => setTimeout(r, 500));

      onUploadComplete?.(response);
      setSelectedFile(null);
      setUploadProgress(0);
    } catch (error) {
      onError?.(error.message || "Upload failed");
      setUploadProgress(0);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="w-full max-w-2xl">
      {/* Hidden file input, driven via ref from the drop zone below */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".mp4,.mov,.webm,video/mp4,video/quicktime,video/webm"
        onChange={handleFileInputChange}
        className="hidden"
      />
      {!selectedFile ? (
        <div
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          onClick={openFilePicker}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              openFilePicker();
            }
          }}
          role="button"
          tabIndex={0}
          className="border-2 border-dashed border-gray-600 rounded-lg p-12 text-center hover:border-gray-500 transition cursor-pointer"
        >
          <div className="text-gray-400 mb-4">
            <svg
              className="w-16 h-16 mx-auto mb-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8m0 8l-6-4m6 4l6-4"
              />
            </svg>
          </div>
          <p className="text-gray-300 text-lg mb-2">Drag & drop your video here</p>
          <p className="text-gray-500 text-sm">or</p>
          <button
            type="button"
            onClick={openFilePicker}
            className="mt-3 px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition"
          >
            Choose Video
          </button>
          <p className="text-gray-500 text-xs mt-3">
            Supported: MP4, MOV, WEBM
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Selected file info */}
          <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm text-gray-400">Selected file</p>
                <p className="text-white font-medium break-all">{selectedFile.name}</p>
                <p className="text-sm text-gray-500 mt-1">
                  {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB
                </p>
              </div>
              <button
                onClick={handleRemoveFile}
                disabled={isUploading}
                className="text-gray-400 hover:text-gray-200 disabled:opacity-50"
              >
                ✕
              </button>
            </div>
          </div>

          {/* Upload progress */}
          {isUploading && (
            <div>
              <div className="w-full bg-gray-700 rounded-full h-2">
                <div
                  className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
              <p className="text-sm text-gray-400 mt-2">Uploading... {uploadProgress}%</p>
            </div>
          )}

          {/* Upload button */}
          <button
            onClick={handleUpload}
            disabled={isUploading}
            className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 text-white font-medium rounded-lg transition"
          >
            {isUploading ? "Uploading..." : "Upload Video"}
          </button>
        </div>
      )}
    </div>
  );
}
