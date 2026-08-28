import { api } from "../services/api";

export function VideoPreview({ jobId, filename }) {
  if (!jobId) return null;

  const videoUrl = api.getVideoUrl(jobId);

  return (
    <div className="w-full max-w-2xl space-y-4">
      <div className="bg-gray-800 rounded-lg overflow-hidden border border-gray-700">
        <video
          key={videoUrl}
          controls
          className="w-full aspect-video bg-black"
        >
          <source src={videoUrl} type="video/mp4" />
          Your browser does not support the video tag.
        </video>
      </div>

      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <p className="text-sm text-gray-400 mb-1">Uploaded file</p>
        <p className="text-white font-medium break-all">{filename}</p>
        <p className="text-sm text-gray-500 mt-2">Job ID: {jobId}</p>
      </div>
    </div>
  );
}
