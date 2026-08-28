"""
Phase 2 — ASR (speech recognition) service abstraction.

Design goals:
- The rest of the application only depends on `get_asr_provider()` and the
  `ASRProvider.transcribe()` contract, so the underlying provider can be
  swapped without touching API routes or the frontend.
- Original ASR text is preserved: no translation, no Roman Urdu
  normalization, no punctuation rewriting (Phase 2 scope).

ASR PROVIDER: Alibaba Cloud Model Studio — Qwen3-ASR.

Official API surface used (Alibaba Cloud Model Studio documentation):
- `qwen3-asr-flash-filetrans` (DashScope async task API): sentence-level
  timestamps in milliseconds. The local audio.wav is first uploaded through
  the official temporary-upload API (getPolicy → OSS multipart) to obtain
  an oss:// URL, then an async transcription task is submitted and polled.
- `qwen3-asr-flash` (DashScope synchronous multimodal-generation API):
  plain-text recognition WITHOUT timestamps. Supported only as a fallback
  mode; it yields a single segment covering the whole audio duration.

Authentication uses a Model Studio API key (DASHSCOPE_API_KEY); the legacy
ALIBABA_CLOUD_ACCESS_KEY_ID / _SECRET variables are not used.
"""
import base64
import json
import logging
import re
import time
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

import requests

from .. import config

logger = logging.getLogger(__name__)

TRANSCRIPT_FILENAME = "transcript.json"
ASR_RAW_FILENAME = "asr_raw.json"

# Supported official model names
FILETRANS_MODEL = "qwen3-asr-flash-filetrans"  # async, timestamped
SYNC_MODEL = "qwen3-asr-flash"                 # sync, text only (no timestamps)

# Official DashScope domains per region. Tuple order:
# (workspace-specific domain suffix, legacy standard domain)
_REGION_DOMAINS = {
    "singapore": ("ap-southeast-1.maas.aliyuncs.com", "dashscope-intl.aliyuncs.com"),
    "beijing": ("cn-beijing.maas.aliyuncs.com", "dashscope.aliyuncs.com"),
}

# Strip anything that could look like an API key before surfacing
# error text coming back from the provider.
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{6,}")


def _sanitize_api_text(text: str) -> str:
    """Redact key-like tokens and truncate provider error text for logging."""
    if not text:
        return ""
    return _SECRET_PATTERN.sub("[REDACTED]", text)[:500]


class ASRNotConfiguredError(RuntimeError):
    """Raised when no ASR provider is configured or credentials are missing."""


class ASRError(RuntimeError):
    """Base error for speech-recognition failures."""


class ASRTimeoutError(ASRError):
    """The ASR task did not finish within the configured timeout."""


class ASRNetworkError(ASRError):
    """Network-level failure while contacting the ASR service."""


class ASRAPIError(ASRError):
    """The ASR service rejected the request or reported a task failure."""


class ASRResponseError(ASRError):
    """The ASR service returned an empty or malformed result."""


@dataclass
class TranscriptSegment:
    """One timestamped piece of recognized speech (seconds)."""
    id: int
    start: float
    end: float
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }


class ASRProvider(ABC):
    """Contract every speech-recognition provider must fulfil."""

    name: str = "abstract"

    @abstractmethod
    def transcribe(self, audio_path: Path) -> Dict[str, Any]:
        """
        Run speech recognition on an audio file.

        Returns a dict:
            segments: list[TranscriptSegment]  (required)
            raw:      provider-native response (optional, kept for debugging)

        Raises ASRNotConfiguredError if the provider cannot run.
        """
        raise NotImplementedError


class AlibabaCloudASRProvider(ASRProvider):
    """
    Alibaba Cloud Model Studio provider for Qwen3-ASR.

    Default mode: `qwen3-asr-flash-filetrans` (async task) — the only
    official Qwen3-ASR variant that returns timestamps:
      1. Upload local audio.wav via the official temporary-upload API
         (getPolicy → OSS multipart) → oss:// URL (valid 48 h).
      2. Submit an async transcription task (X-DashScope-Async: enable).
      3. Poll the task until SUCCEEDED / FAILED / UNKNOWN or timeout.
      4. Download `output.result.transcription_url` (JSON) and map
         `transcripts[].sentences[]` (begin_time/end_time in ms) into
         TranscriptSegment objects (seconds). Original text untouched.

    Fallback mode: `qwen3-asr-flash` (sync) — recognizes base64-encoded
    audio directly but the official API returns plain text WITHOUT
    timestamps, so a single segment spanning the whole audio is emitted.
    """

    name = "alibaba"

    def __init__(self):
        api_key = config.DASHSCOPE_API_KEY
        if not api_key:
            raise ASRNotConfiguredError(
                "Alibaba Cloud ASR is selected but DASHSCOPE_API_KEY is not "
                "set. Add a Model Studio API key to the project root .env "
                "(DASHSCOPE_API_KEY=...) and restart the backend."
            )
        self._api_key = api_key

        model = config.ALIBABA_ASR_MODEL
        if model not in (FILETRANS_MODEL, SYNC_MODEL):
            raise ASRNotConfiguredError(
                f"Unsupported ALIBABA_ASR_MODEL '{model}'. Supported models: "
                f"{FILETRANS_MODEL} (timestamped, recommended) or {SYNC_MODEL} "
                "(text only, no timestamps)."
            )
        self._model = model

        region = config.ALIBABA_ASR_REGION
        if region not in _REGION_DOMAINS:
            raise ASRNotConfiguredError(
                f"Unsupported ALIBABA_ASR_REGION '{region}'. "
                f"Supported regions: {', '.join(sorted(_REGION_DOMAINS))}."
            )
        self._region = region

        workspace_id = config.ALIBABA_WORKSPACE_ID
        ws_domain, legacy_domain = _REGION_DOMAINS[region]
        host = f"{workspace_id}.{ws_domain}" if workspace_id else legacy_domain
        # Never log the full endpoint construction with secrets — host is safe.
        self._base = f"https://{host}/api/v1"

        # The official Qwen3-ASR API accepts a single language code (e.g.
        # "hi" or "en"). Multiple hints are configured comma-separated but
        # only the first one is sent; leaving it empty enables automatic
        # language detection (recommended for code-switched speech).
        hints = [h.strip() for h in config.ALIBABA_ASR_LANGUAGE_HINTS.split(",") if h.strip()]
        if len(hints) > 1:
            logger.warning(
                "ALIBABA_ASR_LANGUAGE_HINTS contains %d values but the "
                "Qwen3-ASR API accepts a single language; using '%s'.",
                len(hints), hints[0],
            )
        self._language = hints[0] if hints else None

    # ------------------------------------------------------------------
    # Public contract
    # ------------------------------------------------------------------

    def transcribe(self, audio_path: Path) -> Dict[str, Any]:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise ASRError(f"Audio file not found: {audio_path}")
        if audio_path.stat().st_size == 0:
            raise ASRError(f"Audio file is empty (0 bytes): {audio_path}")

        if self._model == SYNC_MODEL:
            logger.warning(
                "Using %s (synchronous): the official API returns text "
                "WITHOUT timestamps; the transcript will contain a single "
                "segment covering the whole audio.", self._model,
            )
            return self._transcribe_sync(audio_path)
        return self._transcribe_filetrans(audio_path)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_status(resp: requests.Response, action: str) -> None:
        """Convert non-200 responses into ASRAPIError (secrets redacted)."""
        if resp.status_code in (401, 403):
            raise ASRAPIError(
                f"Alibaba Cloud rejected the API key while {action} "
                f"(HTTP {resp.status_code}). Check that DASHSCOPE_API_KEY is "
                "valid and matches the configured region "
                "(API keys are region-specific)."
            )
        raise ASRAPIError(
            f"Alibaba Cloud API error while {action} (HTTP "
            f"{resp.status_code}): {_sanitize_api_text(resp.text)}"
        )

    @staticmethod
    def _request_json(resp: requests.Response, action: str) -> Dict[str, Any]:
        try:
            return resp.json()
        except ValueError:
            raise ASRResponseError(
                f"Alibaba Cloud returned a non-JSON response while {action}: "
                f"{_sanitize_api_text(resp.text)}"
            )

    # ------------------------------------------------------------------
    # filetrans mode: upload → submit → poll → download → map segments
    # ------------------------------------------------------------------

    def _transcribe_filetrans(self, audio_path: Path) -> Dict[str, Any]:
        file_url = self._upload_for_transcription(audio_path)
        logger.info("Audio uploaded to temporary storage; submitting %s task", self._model)
        task_id = self._submit_transcription_task(file_url)
        logger.info("Transcription task submitted: %s", task_id)
        query_data = self._wait_for_task(task_id)
        result_doc = self._download_transcription_result(query_data)
        segments = self._map_sentences(result_doc)
        raw = {
            "model": self._model,
            "region": self._region,
            "task_id": task_id,
            "query_response": query_data,
            "transcription": result_doc,
        }
        return {"segments": segments, "raw": raw}

    def _upload_for_transcription(self, audio_path: Path) -> str:
        """
        Official temporary-upload flow: getPolicy → OSS multipart upload →
        oss:// URL (valid for 48 hours, development/testing use).
        """
        try:
            policy_resp = requests.get(
                f"{self._base}/uploads",
                headers=self._auth_headers(),
                params={"action": "getPolicy", "model": self._model},
                timeout=config.ASR_HTTP_TIMEOUT,
            )
        except requests.RequestException as e:
            raise ASRNetworkError(
                f"Network failure while requesting an upload credential from "
                f"Alibaba Cloud: {e}"
            )
        if policy_resp.status_code != 200:
            self._raise_for_status(policy_resp, "requesting an upload credential")
        policy = self._request_json(policy_resp, "requesting an upload credential")
        data = policy.get("data") or {}
        required = (
            "policy", "signature", "upload_dir", "upload_host",
            "oss_access_key_id", "x_oss_object_acl", "x_oss_forbid_overwrite",
        )
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise ASRResponseError(
                "Upload credential response is missing required fields: "
                f"{', '.join(missing)}"
            )

        key = f"{data['upload_dir']}/{audio_path.name}"
        try:
            with audio_path.open("rb") as audio_file:
                files = {
                    "OSSAccessKeyId": (None, data["oss_access_key_id"]),
                    "Signature": (None, data["signature"]),
                    "policy": (None, data["policy"]),
                    "x-oss-object-acl": (None, data["x_oss_object_acl"]),
                    "x-oss-forbid-overwrite": (None, data["x_oss_forbid_overwrite"]),
                    "key": (None, key),
                    "success_action_status": (None, "200"),
                    "file": (audio_path.name, audio_file),
                }
                upload_resp = requests.post(
                    data["upload_host"], files=files,
                    timeout=config.ASR_HTTP_TIMEOUT,
                )
        except requests.RequestException as e:
            raise ASRNetworkError(f"Network failure while uploading audio: {e}")
        if upload_resp.status_code != 200:
            raise ASRAPIError(
                "Audio upload to temporary storage failed (HTTP "
                f"{upload_resp.status_code}): "
                f"{_sanitize_api_text(upload_resp.text)}"
            )
        return f"oss://{key}"

    def _submit_transcription_task(self, file_url: str) -> str:
        parameters: Dict[str, Any] = {
            "channel_id": [0],
            "enable_itn": False,
            "enable_words": config.ALIBABA_ASR_ENABLE_WORDS,
        }
        if self._language:
            parameters["language"] = self._language
        payload = {
            "model": self._model,
            "input": {"file_url": file_url},
            "parameters": parameters,
        }
        headers = self._auth_headers()
        headers["X-DashScope-Async"] = "enable"
        # Required when passing an oss:// temporary URL to the model.
        headers["X-DashScope-OssResourceResolve"] = "enable"
        try:
            resp = requests.post(
                f"{self._base}/services/audio/asr/transcription",
                headers=headers,
                json=payload,
                timeout=config.ASR_HTTP_TIMEOUT,
            )
        except requests.RequestException as e:
            raise ASRNetworkError(
                f"Network failure while submitting the transcription task: {e}"
            )
        if resp.status_code != 200:
            self._raise_for_status(resp, "submitting the transcription task")
        body = self._request_json(resp, "submitting the transcription task")
        task_id = (body.get("output") or {}).get("task_id")
        if not task_id:
            raise ASRResponseError(
                "Task submission response did not contain a task_id: "
                f"{_sanitize_api_text(resp.text)}"
            )
        return task_id

    def _wait_for_task(self, task_id: str) -> Dict[str, Any]:
        """Poll the task until a terminal status or ASR_TASK_TIMEOUT."""
        deadline = time.monotonic() + config.ASR_TASK_TIMEOUT
        terminal = {"SUCCEEDED", "FAILED", "UNKNOWN"}
        while True:
            if time.monotonic() > deadline:
                raise ASRTimeoutError(
                    f"Transcription task {task_id} did not finish within "
                    f"{config.ASR_TASK_TIMEOUT}s (ASR_TASK_TIMEOUT)."
                )
            time.sleep(config.ASR_TASK_POLL_INTERVAL)
            try:
                resp = requests.get(
                    f"{self._base}/tasks/{task_id}",
                    headers=self._auth_headers(),
                    timeout=config.ASR_HTTP_TIMEOUT,
                )
            except requests.RequestException as e:
                raise ASRNetworkError(
                    f"Network failure while polling the transcription task: {e}"
                )
            if resp.status_code != 200:
                self._raise_for_status(resp, "polling the transcription task")
            body = self._request_json(resp, "polling the transcription task")
            output = body.get("output") or {}
            status = str(output.get("task_status", "")).upper()
            if not status:
                raise ASRResponseError(
                    "Task status response is missing task_status: "
                    f"{_sanitize_api_text(resp.text)}"
                )
            logger.debug("Transcription task %s status: %s", task_id, status)
            if status not in terminal:
                continue
            if status != "SUCCEEDED":
                detail = output.get("message") or output.get("code") or resp.text
                raise ASRAPIError(
                    f"Alibaba Cloud transcription task failed "
                    f"(status={status}): {_sanitize_api_text(str(detail))}"
                )
            return body

    def _download_transcription_result(self, query_data: Dict[str, Any]) -> Dict[str, Any]:
        output = query_data.get("output") or {}
        result = output.get("result") or {}
        transcription_url = result.get("transcription_url")
        if not transcription_url:
            # Tolerate the multi-file response shape as well.
            results = output.get("results") or []
            if results and isinstance(results[0], dict):
                transcription_url = results[0].get("transcription_url")
        if not transcription_url:
            raise ASRResponseError(
                "Task succeeded but no transcription_url was returned."
            )
        try:
            resp = requests.get(transcription_url, timeout=config.ASR_HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise ASRNetworkError(
                f"Network failure while downloading the transcription result: {e}"
            )
        if resp.status_code != 200:
            raise ASRAPIError(
                "Failed to download the transcription result (HTTP "
                f"{resp.status_code})."
            )
        return self._request_json(resp, "downloading the transcription result")

    def _map_sentences(self, result_doc: Dict[str, Any]) -> List[TranscriptSegment]:
        transcripts = result_doc.get("transcripts")
        if not isinstance(transcripts, list) or not transcripts:
            raise ASRResponseError(
                "The ASR result contains no transcripts — the audio could "
                "not be recognized or contains no speech."
            )
        sentences = transcripts[0].get("sentences")
        if not isinstance(sentences, list) or not sentences:
            raise ASRResponseError(
                "The ASR result contains no sentences — no speech was "
                "recognized in the audio."
            )
        segments: List[TranscriptSegment] = []
        for sentence in sentences:
            text = (sentence.get("text") or "").strip()
            if not text:
                continue
            begin = sentence.get("begin_time")
            end = sentence.get("end_time")
            if begin is None or end is None:
                raise ASRResponseError(
                    "A recognized sentence is missing timestamps "
                    "(begin_time/end_time); cannot build a timestamped "
                    "transcript."
                )
            segments.append(TranscriptSegment(
                id=len(segments) + 1,
                start=float(begin) / 1000.0,
                end=float(end) / 1000.0,
                text=text,
            ))
        if not segments:
            raise ASRResponseError(
                "The ASR result contains no recognizable speech text."
            )
        return segments

    # ------------------------------------------------------------------
    # Sync mode: qwen3-asr-flash (text only, NO timestamps)
    # ------------------------------------------------------------------

    def _transcribe_sync(self, audio_path: Path) -> Dict[str, Any]:
        size = audio_path.stat().st_size
        if size > config.ASR_SYNC_MAX_AUDIO_BYTES:
            raise ASRError(
                f"Audio is too large ({size} bytes) for the synchronous "
                f"{SYNC_MODEL} API (base64 limit). Use "
                f"ALIBABA_ASR_MODEL={FILETRANS_MODEL} for larger files."
            )
        data_uri = "data:audio/wav;base64," + base64.b64encode(
            audio_path.read_bytes()
        ).decode("ascii")

        asr_options: Dict[str, Any] = {"enable_itn": False}
        if self._language:
            asr_options["language"] = self._language
        payload = {
            "model": self._model,
            "input": {
                "messages": [
                    {"role": "user", "content": [{"audio": data_uri}]}
                ]
            },
            "parameters": {"asr_options": asr_options},
        }
        try:
            resp = requests.post(
                f"{self._base}/services/aigc/multimodal-generation/generation",
                headers=self._auth_headers(),
                json=payload,
                timeout=max(config.ASR_HTTP_TIMEOUT, 300),
            )
        except requests.RequestException as e:
            raise ASRNetworkError(
                f"Network failure while calling the ASR service: {e}"
            )
        if resp.status_code != 200:
            self._raise_for_status(resp, "calling the ASR service")
        body = self._request_json(resp, "calling the ASR service")

        text = self._extract_sync_text(body)
        if not text:
            raise ASRResponseError(
                "The ASR service returned no recognized text — the audio "
                "contains no recognizable speech."
            )
        # No timestamps available in sync mode: one segment over full audio.
        duration = self._wav_duration(audio_path)
        segments = [TranscriptSegment(id=1, start=0.0, end=duration, text=text)]
        return {"segments": segments, "raw": body}

    @staticmethod
    def _extract_sync_text(body: Dict[str, Any]) -> str:
        """Read recognized text from the DashScope sync response."""
        try:
            content = body["output"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ASRResponseError(
                "Unexpected ASR response structure: output.choices[0].message"
                ".content is missing."
            )
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts).strip()
        raise ASRResponseError("Unexpected ASR response content type.")

    @staticmethod
    def _wav_duration(audio_path: Path) -> float:
        """Best-effort WAV duration in seconds (0.0 when unreadable)."""
        try:
            with wave.open(str(audio_path), "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate() or 1
                return frames / rate
        except (wave.Error, OSError, EOFError):
            return 0.0


def get_asr_provider() -> ASRProvider:
    """
    Factory: return the configured ASR provider based on ASR_PROVIDER env.

    Raises ASRNotConfiguredError when speech recognition has not been
    configured yet (the default state of this project).
    """
    provider_name = config.ASR_PROVIDER
    if provider_name == "alibaba":
        return AlibabaCloudASRProvider()
    raise ASRNotConfiguredError(
        "Speech recognition (ASR) has not been configured yet. "
        "Set ASR_PROVIDER in the project root .env (e.g. ASR_PROVIDER=alibaba) "
        "and provide the provider credentials to enable transcription."
    )


# ---------------------------------------------------------------------------
# Transcript storage (job directory, no database)
# ---------------------------------------------------------------------------

def save_transcript(
    job_dir: Path,
    job_id: str,
    segments: List[TranscriptSegment],
    provider_name: str,
    raw: Optional[Any] = None,
) -> Path:
    """
    Persist the standardized transcript as transcript.json in the job
    directory. Raw provider output (when available) is kept separately in
    asr_raw.json for debugging.
    """
    job_dir = Path(job_dir)
    transcript_path = job_dir / TRANSCRIPT_FILENAME
    payload = {
        "job_id": job_id,
        "provider": provider_name,
        "segments": [s.to_dict() for s in segments],
    }
    transcript_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if raw is not None:
        raw_path = job_dir / ASR_RAW_FILENAME
        try:
            raw_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (TypeError, ValueError):
            # Non-serializable raw payload: keep transcript, skip raw dump.
            pass

    return transcript_path


def load_transcript(job_dir: Path) -> Optional[Dict[str, Any]]:
    """Load transcript.json from a job directory; None if absent/invalid."""
    transcript_path = Path(job_dir) / TRANSCRIPT_FILENAME
    if not transcript_path.exists():
        return None
    try:
        data = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        return None
    return data
