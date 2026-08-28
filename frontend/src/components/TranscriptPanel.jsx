function formatTimestamp(seconds) {
  const value = Number(seconds) || 0;
  const mins = Math.floor(value / 60);
  const secs = value - mins * 60;
  return `${String(mins).padStart(2, "0")}:${secs.toFixed(2).padStart(5, "0")}`;
}

export function TranscriptPanel({ jobId, segments }) {
  if (!segments) return null;

  return (
    <div className="w-full max-w-2xl bg-gray-800 rounded-lg border border-gray-700">
      <div className="px-4 py-3 border-b border-gray-700 flex items-center justify-between">
        <h2 className="text-white font-medium">Transcript</h2>
        <span className="text-xs text-gray-500">{segments.length} segments</span>
      </div>

      {segments.length === 0 ? (
        <div className="p-4">
          <p className="text-gray-400 text-sm">
            No speech was recognized in this video.
          </p>
        </div>
      ) : (
        <ul className="divide-y divide-gray-700 max-h-96 overflow-y-auto">
          {segments.map((segment) => (
            <li key={segment.id} className="p-4">
              <p className="text-xs text-blue-400 font-mono mb-1">
                {formatTimestamp(segment.start)} — {formatTimestamp(segment.end)}
              </p>
              <p className="text-gray-100 leading-relaxed">{segment.text}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
