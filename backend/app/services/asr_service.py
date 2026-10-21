
"""
Phase 2 — ASR (speech recognition) service abstraction.

Supported Alibaba Model Studio models:
- qwen3-asr-flash-filetrans: asynchronous transcription with timestamps.
- qwen3-asr-flash / qwen3-asr-flash-2026-02-10: synchronous SDK API.
- qwen-audio-3.0-asr-flash: multimodal-generation HTTP API, without
  sentence-level timestamps.

Original ASR text is preserved without translation or normalization.
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
import dashscope

from .. import config

logger = logging.getLogger(__name__)

TRANSCRIPT_FILENAME = "transcript.json"
ASR_RAW_FILENAME = "asr_raw.json"
FILETRANS_MODEL = "qwen3-asr-flash-filetrans"
SYNC_MODEL = "qwen3-asr-flash"
SNAPSHOT_SYNC_MODEL = "qwen3-asr-flash-2026-02-10"
AUDIO30_SYNC_MODEL = "qwen-audio-3.0-asr-flash"

_REGION_DOMAINS = {
    "singapore": (
        "ap-southeast-1.maas.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
    ),
    "beijing": (
        "cn-beijing.maas.aliyuncs.com",
        "dashscope.aliyuncs.com",
    ),
}

_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{6,}")


def _sanitize_api_text(text: str) -> str:
    """Redact key-like tokens and truncate provider error text."""
    if not text:
        return ""
    return _SECRET_PATTERN.sub("[REDACTED]", text)[:500]


class ASRNotConfiguredError(RuntimeError):
    """Raised when ASR is not configured or credentials are missing."""


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
    """One piece of recognized speech; timestamps are in seconds."""
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
        """Return {'segments': list[TranscriptSegment], 'raw': provider data}."""
        raise NotImplementedError


class AlibabaCloudASRProvider(ASRProvider):
    """Alibaba Cloud Model Studio ASR provider."""

    name = "alibaba"

    def __init__(self):
        api_key = config.DASHSCOPE_API_KEY
        if not api_key:
            raise ASRNotConfiguredError(
                "Alibaba Cloud ASR is selected but DASHSCOPE_API_KEY is not set. "
                "Add a Model Studio API key to the project .env and restart."
            )
        self._api_key = api_key

        model = config.ALIBABA_ASR_MODEL
        supported_models = (
            FILETRANS_MODEL,
            SYNC_MODEL,
            SNAPSHOT_SYNC_MODEL,
            AUDIO30_SYNC_MODEL,
        )
        if model not in supported_models:
            raise ASRNotConfiguredError(
                f"Unsupported ALIBABA_ASR_MODEL '{model}'. Supported models: "
                f"{FILETRANS_MODEL}, {SYNC_MODEL}, {SNAPSHOT_SYNC_MODEL}, "
                f"{AUDIO30_SYNC_MODEL}."
            )
        self._model = model

        region = config.ALIBABA_ASR_REGION
        if region not in _REGION_DOMAINS:
            raise ASRNotConfiguredError(
                f"Unsupported ALIBABA_ASR_REGION '{region}'. Supported regions: "
                f"{', '.join(sorted(_REGION_DOMAINS))}."
            )
        self._region = region

        workspace_id = config.ALIBABA_WORKSPACE_ID
        ws_domain, legacy_domain = _REGION_DOMAINS[region]
        host = f"{workspace_id}.{ws_domain}" if workspace_id else legacy_domain
        self._base = f"https://{host}/api/v1"

        raw_hints = getattr(config, "ALIBABA_ASR_LANGUAGE_HINTS", "") or ""
        hints = [h.strip() for h in raw_hints.split(",") if h.strip()]
        if len(hints) > 1:
            logger.warning(
                "ALIBABA_ASR_LANGUAGE_HINTS contains multiple values; using '%s'.",
                hints[0],
            )
        self._language = hints[0] if hints else None

    def transcribe(self, audio_path: Path) -> Dict[str, Any]:
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise ASRError(f"Audio file not found: {audio_path}")
        if audio_path.stat().st_size == 0:
            raise ASRError(f"Audio file is empty (0 bytes): {audio_path}")

        if self._model == AUDIO30_SYNC_MODEL:
            logger.warning(
                "%s returns text without sentence timestamps; creating one "
                "segment covering the full audio.",
                self._model,
            )
            return self._transcribe_audio30_sync(audio_path)

        if self._model in (SYNC_MODEL, SNAPSHOT_SYNC_MODEL):
            logger.warning(
                "%s returns text without sentence timestamps; creating one "
                "segment covering the full audio.",
                self._model,
            )
            return self._transcribe_sync(audio_path)

        return self._transcribe_filetrans(audio_path)

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_status(resp: requests.Response, action: str) -> None:
        raise ASRAPIError(
            f"Alibaba Cloud API error while {action} (HTTP {resp.status_code}): "
            f"{_sanitize_api_text(resp.text)}"
        )

    @staticmethod
    def _request_json(resp: requests.Response, action: str) -> Dict[str, Any]:
        try:
            body = resp.json()
        except ValueError as exc:
            raise ASRResponseError(
                f"Alibaba Cloud returned non-JSON data while {action}: "
                f"{_sanitize_api_text(resp.text)}"
            ) from exc
        if not isinstance(body, dict):
            raise ASRResponseError(
                f"Alibaba Cloud returned an unexpected JSON value while {action}."
            )
        return body

    # Timestamped filetrans model: upload -> submit -> poll -> download

    def _transcribe_filetrans(self, audio_path: Path) -> Dict[str, Any]:
        file_url = self._upload_for_transcription(audio_path)
        logger.info("Audio uploaded; submitting %s transcription task", self._model)
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
        """Request temporary OSS credentials and upload the local audio file."""
        try:
            policy_resp = requests.get(
                f"{self._base}/uploads",
                headers=self._auth_headers(),
                params={"action": "getPolicy", "model": self._model},
                timeout=config.ASR_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ASRNetworkError(
                f"Network failure while requesting upload credentials: {exc}"
            ) from exc

        if policy_resp.status_code != 200:
            self._raise_for_status(policy_resp, "requesting upload credentials")

        policy = self._request_json(policy_resp, "requesting upload credentials")
        data = policy.get("data") or {}
        required = (
            "policy", "signature", "upload_dir", "upload_host",
            "oss_access_key_id", "x_oss_object_acl", "x_oss_forbid_overwrite",
        )
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ASRResponseError(
                "Upload credential response is missing fields: " + ", ".join(missing)
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
                    data["upload_host"],
                    files=files,
                    timeout=config.ASR_HTTP_TIMEOUT,
                )
        except requests.RequestException as exc:
            raise ASRNetworkError(
                f"Network failure while uploading audio: {exc}"
            ) from exc

        if upload_resp.status_code != 200:
            raise ASRAPIError(
                f"Temporary audio upload failed (HTTP {upload_resp.status_code}): "
                f"{_sanitize_api_text(upload_resp.text)}"
            )
        return f"oss://{key}"

    def _submit_transcription_task(self, file_url: str) -> str:
        parameters: Dict[str, Any] = {
            "channel_id": [0],
            "enable_itn": False,
            "enable_words": getattr(config, "ALIBABA_ASR_ENABLE_WORDS", False),
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
        headers["X-DashScope-OssResourceResolve"] = "enable"

        try:
            resp = requests.post(
                f"{self._base}/services/audio/asr/transcription",
                headers=headers,
                json=payload,
                timeout=config.ASR_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ASRNetworkError(
                f"Network failure while submitting transcription task: {exc}"
            ) from exc

        if resp.status_code != 200:
            self._raise_for_status(resp, "submitting transcription task")
        body = self._request_json(resp, "submitting transcription task")
        task_id = (body.get("output") or {}).get("task_id")
        if not task_id:
            raise ASRResponseError(
                "Task submission response did not contain task_id: "
                f"{_sanitize_api_text(resp.text)}"
            )
        return str(task_id)

    def _wait_for_task(self, task_id: str) -> Dict[str, Any]:
        deadline = time.monotonic() + config.ASR_TASK_TIMEOUT
        terminal = {"SUCCEEDED", "FAILED", "UNKNOWN"}

        while True:
            if time.monotonic() > deadline:
                raise ASRTimeoutError(
                    f"Transcription task {task_id} did not finish within "
                    f"{config.ASR_TASK_TIMEOUT}s."
                )
            time.sleep(config.ASR_TASK_POLL_INTERVAL)
            try:
                resp = requests.get(
                    f"{self._base}/tasks/{task_id}",
                    headers=self._auth_headers(),
                    timeout=config.ASR_HTTP_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise ASRNetworkError(
                    f"Network failure while polling transcription task: {exc}"
                ) from exc

            if resp.status_code != 200:
                self._raise_for_status(resp, "polling transcription task")
            body = self._request_json(resp, "polling transcription task")
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
                    f"Transcription task failed (status={status}): "
                    f"{_sanitize_api_text(str(detail))}"
                )
            return body

    def _download_transcription_result(
        self, query_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        output = query_data.get("output") or {}
        result = output.get("result") or {}
        transcription_url = result.get("transcription_url")
        if not transcription_url:
            results = output.get("results") or []
            if results and isinstance(results[0], dict):
                transcription_url = results[0].get("transcription_url")
        if not transcription_url:
            raise ASRResponseError(
                "Task succeeded but no transcription_url was returned."
            )

        try:
            resp = requests.get(
                transcription_url, timeout=config.ASR_HTTP_TIMEOUT
            )
        except requests.RequestException as exc:
            raise ASRNetworkError(
                f"Network failure while downloading transcription result: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise ASRAPIError(
                f"Failed to download transcription result (HTTP {resp.status_code})."
            )
        return self._request_json(resp, "downloading transcription result")

    def _map_sentences(self, result_doc: Dict[str, Any]) -> List[TranscriptSegment]:
        transcripts = result_doc.get("transcripts")
        if not isinstance(transcripts, list) or not transcripts:
            raise ASRResponseError(
                "ASR result contains no transcripts or no recognizable speech."
            )
        sentences = transcripts[0].get("sentences")
        if not isinstance(sentences, list) or not sentences:
            raise ASRResponseError("ASR result contains no timestamped sentences.")

        segments: List[TranscriptSegment] = []
        for sentence in sentences:
            if not isinstance(sentence, dict):
                continue
            text = (sentence.get("text") or "").strip()
            if not text:
                continue
            begin = sentence.get("begin_time")
            end = sentence.get("end_time")
            if begin is None or end is None:
                raise ASRResponseError(
                    "Recognized sentence is missing begin_time/end_time timestamps."
                )
            segments.append(TranscriptSegment(
                id=len(segments) + 1,
                start=float(begin) / 1000.0,
                end=float(end) / 1000.0,
                text=text,
            ))
        if not segments:
            raise ASRResponseError("ASR result contains no recognized speech text.")
        return segments

    # Qwen-Audio-3.0-ASR-Flash: HTTP API, without timestamps

    def _transcribe_audio30_sync(self, audio_path: Path) -> Dict[str, Any]:
        """Recognize WAV audio with qwen-audio-3.0-asr-flash."""
        try:
            with wave.open(str(audio_path), "rb") as wav:
                sample_rate = wav.getframerate()
            if sample_rate <= 0:
                raise ASRError("The WAV file has an invalid sample rate.")
        except (wave.Error, OSError, EOFError) as exc:
            raise ASRError(
                f"Qwen-Audio ASR expects a valid WAV file: {exc}"
            ) from exc

        size = audio_path.stat().st_size
        max_bytes = getattr(config, "ASR_SYNC_MAX_AUDIO_BYTES", 10 * 1024 * 1024)
        if size > max_bytes:
            raise ASRError(
                f"Audio is too large ({size} bytes) for the configured request "
                f"limit ({max_bytes} bytes). Shorten/compress it or use "
                f"ALIBABA_ASR_MODEL={FILETRANS_MODEL}."
            )

        data_uri = "data:audio/wav;base64," + base64.b64encode(
            audio_path.read_bytes()
        ).decode("ascii")
        payload = {
            "model": AUDIO30_SYNC_MODEL,
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "input_audio",
                        "input_audio": {"data": data_uri},
                    }],
                }],
            },
            "parameters": {
                "format": "wav",
                "sample_rate": str(sample_rate),
            },
        }

        try:
            resp = requests.post(
                f"{self._base}/services/aigc/multimodal-generation/generation",
                headers=self._auth_headers(),
                json=payload,
                timeout=config.ASR_HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ASRNetworkError(
                f"Network failure while calling Qwen-Audio ASR: {exc}"
            ) from exc

        if resp.status_code != 200:
            self._raise_for_status(resp, "transcribing audio with Qwen-Audio")

        body = self._request_json(resp, "transcribing audio with Qwen-Audio")
        output = body.get("output") or {}
        nested_output = output.get("output") or {}
        sentence = nested_output.get("sentence") or {}
        text = sentence.get("text") or output.get("text") or ""
        if not isinstance(text, str):
            text = ""
        text = text.strip()

        if not text:
            if body.get("code"):
                raise ASRAPIError(
                    "Qwen-Audio ASR reported an error: "
                    f"{_sanitize_api_text(json.dumps(body, ensure_ascii=False))}"
                )
            raise ASRResponseError(
                "Qwen-Audio ASR returned no recognized text. Check the audio, "
                "model availability, region, and workspace configuration."
            )

        duration = self._wav_duration(audio_path)
        return {
            "segments": [
                TranscriptSegment(id=1, start=0.0, end=duration, text=text)
            ],
            "raw": body,
        }

    # Qwen3-ASR synchronous SDK API, without timestamps

    def _transcribe_sync(self, audio_path: Path) -> Dict[str, Any]:
        size = audio_path.stat().st_size
        max_bytes = getattr(config, "ASR_SYNC_MAX_AUDIO_BYTES", 10 * 1024 * 1024)
        if size > max_bytes:
            raise ASRError(
                f"Audio is too large ({size} bytes) for synchronous {self._model}. "
                f"Use ALIBABA_ASR_MODEL={FILETRANS_MODEL} for larger files."
            )

        data_uri = "data:audio/wav;base64," + base64.b64encode(
            audio_path.read_bytes()
        ).decode("ascii")
        messages = [
            {"role": "system", "content": [{"text": ""}]},
            {"role": "user", "content": [{"audio": data_uri}]},
        ]

        try:
            dashscope.base_http_api_url = self._base
            response = dashscope.MultiModalConversation.call(
                api_key=self._api_key,
                model=self._model,
                messages=messages,
                result_format="message",
                asr_options={
                    "enable_lid": True,
                    "enable_itn": False,
                    "enable_words": True,
                },
            )
        except Exception as exc:
            raise ASRNetworkError(
                f"Failure while calling Alibaba Cloud ASR: "
                f"{_sanitize_api_text(str(exc))}"
            ) from exc

        if isinstance(response, dict):
            body = response
        elif hasattr(response, "to_dict"):
            body = response.to_dict()
        else:
            raise ASRResponseError("Unexpected DashScope response object type.")

        if body.get("status_code") != 200:
            raise ASRAPIError(
                f"Alibaba Cloud ASR request failed "
                f"(HTTP {body.get('status_code')}): "
                f"{_sanitize_api_text(str(body))}"
            )

        text = self._extract_sync_text(body)
        if not text:
            raise ASRResponseError("The ASR service returned no recognized text.")

        return {
            "segments": [
                TranscriptSegment(
                    id=1, start=0.0, end=self._wav_duration(audio_path), text=text
                )
            ],
            "raw": body,
        }

    @staticmethod
    def _extract_sync_text(body: Dict[str, Any]) -> str:
        try:
            content = body["output"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ASRResponseError(
                "Unexpected ASR response structure: "
                "output.choices[0].message.content is missing."
            )
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(
                item["text"] for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ).strip()
        raise ASRResponseError("Unexpected ASR response content type.")

    @staticmethod
    def _wav_duration(audio_path: Path) -> float:
        """Return WAV duration in seconds; 0.0 if unreadable."""
        try:
            with wave.open(str(audio_path), "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                return frames / rate if rate else 0.0
        except (wave.Error, OSError, EOFError):
            return 0.0


def get_asr_provider() -> ASRProvider:
    """Return the ASR provider configured by ASR_PROVIDER."""
    provider_name = config.ASR_PROVIDER
    if provider_name == "alibaba":
        return AlibabaCloudASRProvider()
    raise ASRNotConfiguredError(
        "Speech recognition (ASR) has not been configured yet. Set "
        "ASR_PROVIDER=alibaba in the project root .env and provide credentials."
    )


def save_transcript(
    job_dir: Path,
    job_id: str,
    segments: List[TranscriptSegment],
    provider_name: str,
    raw: Optional[Any] = None,
) -> Path:
    """Save standardized transcript JSON and optional raw provider response."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = job_dir / TRANSCRIPT_FILENAME
    payload = {
        "job_id": job_id,
        "provider": provider_name,
        "segments": [segment.to_dict() for segment in segments],
    }
    transcript_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if raw is not None:
        try:
            (job_dir / ASR_RAW_FILENAME).write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (TypeError, ValueError, OSError):
            logger.warning(
                "Could not serialize raw ASR response; transcript was saved."
            )

    return transcript_path


def load_transcript(job_dir: Path) -> Optional[Dict[str, Any]]:
    """Load transcript.json; return None if absent, unreadable, or invalid."""
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
