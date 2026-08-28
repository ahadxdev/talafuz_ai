import { useState } from "react";
import { VideoUploader } from "../components/VideoUploader";
import { VideoPreview } from "../components/VideoPreview";
import { ProcessingStatus } from "../components/ProcessingStatus";

export function Home() {
  const [uploadedVideo, setUploadedVideo] = useState(null);
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [isUploading, setIsUploading] = useState(false);

  const handleVideoSelect = (file) => {
    setIsError(false);
    setErrorMessage("");
  };

  const handleUploadStart = () => {
    setIsUploading(true);
    setIsError(false);
  };

  const handleUploadComplete = (response) => {
    setUploadedVideo(response);
    setIsUploading(false);
  };

  const handleError = (message) => {
    setIsError(true);
    setErrorMessage(message);
    setIsUploading(false);
  };

  const handleReset = () => {
    setUploadedVideo(null);
    setIsError(false);
    setErrorMessage("");
    setIsUploading(false);
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="border-b border-gray-800 py-8">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <h1 className="text-4xl font-bold tracking-tight">TALAFUZ AI</h1>
          <p className="text-gray-400 mt-2">
            AI subtitles for South Asian short-form content.
          </p>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="space-y-8">
          {/* Upload section */}
          {!uploadedVideo && (
            <div className="flex justify-center">
              <VideoUploader
                onVideoSelect={handleVideoSelect}
                onUploadStart={handleUploadStart}
                onUploadComplete={handleUploadComplete}
                onError={handleError}
              />
            </div>
          )}

          {/* Status messages */}
          {(isError || uploadedVideo) && (
            <div className="flex justify-center">
              <ProcessingStatus
                jobId={uploadedVideo?.job_id}
                isSuccess={!!uploadedVideo}
                isError={isError}
                errorMessage={errorMessage}
              />
            </div>
          )}

          {/* Video preview */}
          {uploadedVideo && (
            <div className="space-y-6">
              <div className="flex justify-center">
                <VideoPreview
                  jobId={uploadedVideo.job_id}
                  filename={uploadedVideo.filename}
                />
              </div>

              {/* Reset button */}
              <div className="flex justify-center">
                <button
                  onClick={handleReset}
                  className="px-6 py-2 bg-gray-800 hover:bg-gray-700 text-white font-medium rounded-lg transition border border-gray-700"
                >
                  Upload Another Video
                </button>
              </div>
            </div>
          )}

          {/* Error state with retry */}
          {isError && !uploadedVideo && (
            <div className="flex justify-center">
              <button
                onClick={handleReset}
                className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition"
              >
                Try Again
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
