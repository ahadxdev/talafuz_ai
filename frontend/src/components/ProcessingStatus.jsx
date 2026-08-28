export function ProcessingStatus({ jobId, isSuccess, isError, errorMessage }) {
  if (!jobId && !isError) return null;

  if (isError) {
    return (
      <div className="w-full max-w-2xl bg-red-900 border border-red-700 rounded-lg p-4">
        <p className="text-red-200 font-medium">Upload failed</p>
        <p className="text-red-300 text-sm mt-1">{errorMessage}</p>
      </div>
    );
  }

  if (isSuccess) {
    return (
      <div className="w-full max-w-2xl bg-green-900 border border-green-700 rounded-lg p-4">
        <p className="text-green-200 font-medium">✓ Video uploaded successfully</p>
        <p className="text-green-300 text-sm mt-1">
          Your video is ready for the next step.
        </p>
      </div>
    );
  }

  return null;
}
