"""
Phase 3 — Romanization service (South Asian script → Latin script).

Talafuz converts Urdu/Hindi/Devanagari-script ASR output into readable,
natural Roman Urdu / Roman Hindi while preserving the original language,
meaning, tone, slang and code-switching. This is TRANSLITERATION, not
translation. An optional, separately generated English translation can be
attached.

Implementation notes:
- Uses a Qwen TEXT model (default qwen-plus) through the official Alibaba
  Cloud Model Studio OpenAI-compatible endpoint (compatible-mode/v1).
  Qwen3-ASR is NOT involved — ASR already happened in Phase 2.
- Reuses the same credentials/region configuration as the ASR integration
  (DASHSCOPE_API_KEY, ALIBABA_ASR_REGION, ALIBABA_WORKSPACE_ID).
- Transcript segments are sent in batches; the model must return a JSON
  array aligned by index. Responses are strictly validated — results are
  never fabricated.
- Long segments are split into readable subtitle chunks (≤2 lines of
  ~SUBTITLE_MAX_CHARS_PER_LINE characters) at word/punctuation boundaries;
  timestamps are proportionally distributed inside the original ASR timing.
"""
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .. import config

logger = logging.getLogger(__name__)

ROMANIZED_SUBTITLES_FILENAME = "romanized_subtitles.json"

# Official DashScope domains per region (same mapping as the ASR service).
# Tuple order: (workspace-specific domain suffix, legacy standard domain)
_REGION_DOMAINS = {
    "singapore": ("ap-southeast-1.maas.aliyuncs.com", "dashscope-intl.aliyuncs.com"),
    "beijing": ("cn-beijing.maas.aliyuncs.com", "dashscope.aliyuncs.com"),
}

# Scripts that must NOT appear in romanized output.
_NON_LATIN_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0900-\u097F\u0750-\u077F]")

_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{6,}")


def _sanitize_api_text(text: str) -> str:
    """Redact key-like tokens and truncate provider error text for logging."""
    if not text:
        return ""
    return _SECRET_PATTERN.sub("[REDACTED]", text)[:500]


class RomanizationError(RuntimeError):
    """Base error for romanization/translation failures."""


class RomanizationNotConfiguredError(RomanizationError):
    """Raised when credentials or model configuration are missing."""


class RomanizationNetworkError(RomanizationError):
    """Network-level failure while contacting the model service."""


class RomanizationAPIError(RomanizationError):
    """The model service rejected the request."""


class RomanizationResponseError(RomanizationError):
    """The model returned an empty or malformed result."""


class RomanizationTimeoutError(RomanizationError):
    """The model service did not respond in time."""


@dataclass
class Subtitle:
    """One timestamped subtitle cue (seconds)."""

    id: int
    start: float
    end: float
    original_text: str
    romanized_text: str
    english_text: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "original_text": self.original_text,
            "romanized_text": self.romanized_text,
            "english_text": self.english_text,
        }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_ROMANIZE_SYSTEM_PROMPT = """\
You are an expert romanizer of South Asian languages (Urdu, Hindi, Roman
Urdu, Hinglish and code-switched speech).

TASK: rewrite each input text using the Latin script only (romanization /
transliteration).

STRICT RULES:
1. This is NOT translation. NEVER convert the meaning into English. The
   words must stay in the original South Asian language; only the writing
   system changes from Urdu/Hindi script to Latin script.
2. Preserve meaning, conversational tone, slang, informal expressions and
   Pakistani/Indian cultural expressions exactly.
3. Words already written in English or Latin script (technical terms,
   brand names, code-switched words) must be kept exactly as they are.
4. Keep numbers, years, dates, URLs, acronyms and punctuation as they are.
5. Use natural Pakistani/Indian Roman Urdu conventions that a native
   speaker would write (e.g. "kya", "hai", "mein", "karna", "zaroor",
   "nahi"). Prioritize READABILITY and NATURALNESS over any rigid academic
   transliteration system.
6. Text already written in Roman Urdu/Hinglish/English must be returned
   essentially unchanged (do not rewrite or "correct" it).
7. Output Latin script ONLY — no Urdu, Arabic, Hindi or Devanagari
   characters may appear in the output.
"""

_ROMANIZE_USER_TEMPLATE = """\
Romanize each numbered text. Respond with ONLY a JSON array (no markdown,
no explanations), with exactly {n} items in the same order, each item:
{{"index": <int>, "romanized": "<latin script text>"}}

Texts:
{items}
"""

_TRANSLATE_SYSTEM_PROMPT = """\
You are a professional translator for South Asian content (Urdu, Hindi,
Roman Urdu, Hinglish and code-switched speech).

TASK: translate each input text into natural, fluent English.

RULES:
1. Translate the MEANING into English — this is a real translation, not
   romanization.
2. Preserve tone, register and intent (informal stays informal).
3. Keep technical terms, brand names and numbers natural in English.
"""

_TRANSLATE_USER_TEMPLATE = """\
Translate each numbered text into English. Respond with ONLY a JSON array
(no markdown, no explanations), with exactly {n} items in the same order,
each item: {{"index": <int>, "english": "<english text>"}}

Texts:
{items}
"""


# ---------------------------------------------------------------------------
# Subtitle segmentation (pure logic, no LLM)
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.?!،।؟…]\s*$")


def split_text_into_chunks(text: str, max_chars: int) -> List[str]:
    """
    Split text into readable subtitle chunks at word boundaries.

    Prefers breaking after sentence-ending punctuation once a chunk is
    reasonably filled; never breaks inside a word, number, name or term.
    """
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            # Prefer a natural break right after sentence punctuation once
            # the chunk is reasonably filled.
            if len(current) >= max_chars * 0.6 and _SENTENCE_END_RE.search(current):
                chunks.append(current)
                current = ""
        else:
            if current:
                chunks.append(current)
            # Extremely long token: hard-split so max_chars is respected.
            while len(word) > max_chars:
                chunks.append(word[:max_chars])
                word = word[max_chars:]
            current = word
    if current:
        chunks.append(current)

    # Avoid a tiny trailing chunk when it fits the previous one.
    if len(chunks) > 1 and len(chunks[-1]) <= max_chars * 0.25:
        merged = f"{chunks[-2]} {chunks[-1]}"
        if len(merged) <= max_chars:
            chunks[-2] = merged
            chunks.pop()
    return chunks


def distribute_timestamps(
    chunks: List[str], start: float, end: float, min_duration: float
) -> List[Tuple[float, float]]:
    """
    Proportionally distribute [start, end] across chunks (by character
    weight), enforcing a minimum cue duration where the total time allows.
    Timestamps always stay within the original ASR segment timing.
    """
    n = len(chunks)
    if n == 0:
        return []
    if n == 1 or end <= start:
        return [(start, end)] * n

    duration = end - start
    weights = [max(len(c), 1) for c in chunks]
    total = sum(weights)
    shares = [w / total for w in weights]

    # Enforce a readable minimum duration per cue when possible.
    if duration / n < min_duration:
        shares = [1.0 / n] * n
    else:
        floor_share = min_duration / duration
        shares = [max(s, floor_share) for s in shares]
        norm = sum(shares)
        shares = [s / norm for s in shares]

    times: List[Tuple[float, float]] = []
    cursor = start
    for i, share in enumerate(shares):
        chunk_end = end if i == n - 1 else cursor + duration * share
        chunk_end = max(chunk_end, cursor + 0.05)
        times.append((round(cursor, 3), round(min(chunk_end, end), 3)))
        cursor = chunk_end
    return times


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class QwenRomanizationService:
    """
    Romanization + optional English translation via an official Qwen text
    model on Alibaba Cloud Model Studio (OpenAI-compatible endpoint).
    """

    def __init__(self):
        if not config.DASHSCOPE_API_KEY:
            raise RomanizationNotConfiguredError(
                "Romanization requires DASHSCOPE_API_KEY. Add a Model Studio "
                "API key to the project root .env and restart the backend."
            )
        if not config.ROMANIZATION_MODEL:
            raise RomanizationNotConfiguredError(
                "ROMANIZATION_MODEL is not set. Set it in the project root "
                ".env (e.g. ROMANIZATION_MODEL=qwen-plus)."
            )
        self._api_key = config.DASHSCOPE_API_KEY
        self._model = config.ROMANIZATION_MODEL

        region = config.ALIBABA_ASR_REGION
        if region not in _REGION_DOMAINS:
            raise RomanizationNotConfiguredError(
                f"Unsupported ALIBABA_ASR_REGION '{region}'. Supported "
                f"regions: {', '.join(sorted(_REGION_DOMAINS))}."
            )
        workspace_id = config.ALIBABA_WORKSPACE_ID
        ws_domain, legacy_domain = _REGION_DOMAINS[region]
        host = f"{workspace_id}.{ws_domain}" if workspace_id else legacy_domain
        self._base = f"https://{host}/compatible-mode/v1"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def romanize_segments(
        self, segments: List[Dict], include_english: bool = False
    ) -> List[Subtitle]:
        """
        Romanize ASR transcript segments and produce segmented subtitles.

        `segments` are transcript.json entries: {id, start, end, text}.
        Returns Subtitle cues; original ASR text is preserved untouched.
        """
        self._validate_segments(segments)

        texts = [s["text"] for s in segments]
        romanized = self._transform_batches(
            texts,
            system_prompt=_ROMANIZE_SYSTEM_PROMPT,
            user_template=_ROMANIZE_USER_TEMPLATE,
            output_key="romanized",
            purpose="romanizing",
        )
        self._verify_latin_script(texts, romanized)

        english: Dict[int, str] = {}
        if include_english:
            english_list = self._transform_batches(
                texts,
                system_prompt=_TRANSLATE_SYSTEM_PROMPT,
                user_template=_TRANSLATE_USER_TEMPLATE,
                output_key="english",
                purpose="translating",
            )
            english = {i: v for i, v in enumerate(english_list)}

        return self._build_subtitles(segments, romanized, english)

    # ------------------------------------------------------------------
    # Validation / assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_segments(segments: List[Dict]) -> None:
        if not segments:
            raise RomanizationError(
                "The transcript is empty — there is nothing to romanize."
            )
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                raise RomanizationError(f"Invalid transcript: segment {i} is malformed.")
            text = seg.get("text")
            if not isinstance(text, str) or not text.strip():
                raise RomanizationError(
                    f"Invalid transcript: segment {i} has no text."
                )
            start, end = seg.get("start"), seg.get("end")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                raise RomanizationError(
                    f"Invalid transcript: segment {i} has invalid timestamps."
                )

    @staticmethod
    def _verify_latin_script(texts: List[str], romanized: List[str]) -> None:
        """Romanized output must be Latin script (inputs that were already
        Latin are naturally returned unchanged by the model)."""
        for i, rom in enumerate(romanized):
            if _NON_LATIN_SCRIPT_RE.search(rom):
                raise RomanizationResponseError(
                    f"Romanization failed for segment {i}: the model returned "
                    "non-Latin script. Try again or check ROMANIZATION_MODEL."
                )

    def _build_subtitles(
        self,
        segments: List[Dict],
        romanized: List[str],
        english: Dict[int, str],
    ) -> List[Subtitle]:
        max_chars = config.SUBTITLE_MAX_CHARS_PER_LINE * config.SUBTITLE_MAX_LINES
        subtitles: List[Subtitle] = []
        for i, seg in enumerate(segments):
            rom_text = (romanized[i] or "").strip()
            if not rom_text:
                raise RomanizationResponseError(
                    f"The model returned empty romanized text for segment {i}."
                )
            chunks = split_text_into_chunks(rom_text, max_chars)
            times = distribute_timestamps(
                chunks, float(seg["start"]), float(seg["end"]),
                config.SUBTITLE_MIN_DURATION,
            )
            for chunk, (cue_start, cue_end) in zip(chunks, times):
                subtitles.append(Subtitle(
                    id=len(subtitles) + 1,
                    start=cue_start,
                    end=cue_end,
                    original_text=seg["text"],
                    romanized_text=chunk,
                    english_text=english.get(i),
                ))
        if not subtitles:
            raise RomanizationResponseError("No subtitles were produced.")
        return subtitles

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    def _transform_batches(
        self,
        texts: List[str],
        system_prompt: str,
        user_template: str,
        output_key: str,
        purpose: str,
    ) -> List[str]:
        """Run the model over batches; return outputs aligned to `texts`."""
        batch_size = max(1, config.ROMANIZATION_BATCH_SIZE)
        results: List[str] = []
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset: offset + batch_size]
            items = "\n".join(
                f"{offset + j}. {text}" for j, text in enumerate(batch)
            )
            user_prompt = user_template.format(n=len(batch), items=items)
            outputs = self._call_batch(
                system_prompt, user_prompt, output_key, len(batch), purpose
            )
            results.extend(outputs)
        return results

    def _call_batch(
        self,
        system_prompt: str,
        user_prompt: str,
        output_key: str,
        expected_count: int,
        purpose: str,
    ) -> List[str]:
        """One model call with a single retry on malformed responses."""
        last_error = ""
        for attempt in (1, 2):
            content = self._chat(system_prompt, user_prompt, purpose)
            try:
                return self._parse_aligned_array(
                    content, output_key, expected_count
                )
            except RomanizationResponseError as e:
                last_error = str(e)
                logger.warning(
                    "Malformed model response while %s (attempt %d): %s",
                    purpose, attempt, last_error,
                )
                if attempt == 1:
                    user_prompt = (
                        user_prompt
                        + "\nIMPORTANT: your previous reply was not valid "
                        "JSON. Reply ONLY with the JSON array."
                    )
        raise RomanizationResponseError(
            f"The model did not return a valid result while {purpose}. "
            f"Last error: {last_error}"
        )

    def _chat(self, system_prompt: str, user_prompt: str, purpose: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": config.ROMANIZATION_MAX_TOKENS,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                f"{self._base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=config.ROMANIZATION_HTTP_TIMEOUT,
            )
        except requests.Timeout:
            raise RomanizationTimeoutError(
                f"Alibaba Cloud did not respond within "
                f"{config.ROMANIZATION_HTTP_TIMEOUT}s while {purpose}."
            )
        except requests.RequestException as e:
            raise RomanizationNetworkError(
                f"Network failure while {purpose}: {e}"
            )

        if resp.status_code in (401, 403):
            raise RomanizationAPIError(
                f"Alibaba Cloud rejected the API key while {purpose} "
                f"(HTTP {resp.status_code}). Check that DASHSCOPE_API_KEY is "
                "valid and matches the configured region."
            )
        if resp.status_code != 200:
            raise RomanizationAPIError(
                f"Alibaba Cloud API error while {purpose} (HTTP "
                f"{resp.status_code}): {_sanitize_api_text(resp.text)}"
            )
        try:
            body = resp.json()
        except ValueError:
            raise RomanizationResponseError(
                f"Alibaba Cloud returned a non-JSON response while {purpose}."
            )
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RomanizationResponseError(
                f"Unexpected model response structure while {purpose}."
            )
        if not isinstance(content, str) or not content.strip():
            raise RomanizationResponseError(
                f"The model returned empty content while {purpose}."
            )
        return content

    @staticmethod
    def _parse_aligned_array(
        content: str, output_key: str, expected_count: int
    ) -> List[str]:
        """Parse a JSON array of {index, <output_key>} items aligned by index."""
        text = content.strip()
        # Tolerate markdown code fences.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        # Tolerate leading/trailing prose around the array.
        first, last = text.find("["), text.rfind("]")
        if first == -1 or last == -1 or last <= first:
            raise RomanizationResponseError(
                "Model response did not contain a JSON array."
            )
        try:
            data = json.loads(text[first: last + 1])
        except json.JSONDecodeError:
            raise RomanizationResponseError("Model response was not valid JSON.")

        if not isinstance(data, list) or len(data) != expected_count:
            raise RomanizationResponseError(
                f"Model returned {len(data) if isinstance(data, list) else 0} "
                f"items but {expected_count} were expected."
            )
        outputs: List[str] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise RomanizationResponseError(
                    f"Model response item {i} is not an object."
                )
            value = item.get(output_key)
            if not isinstance(value, str) or not value.strip():
                raise RomanizationResponseError(
                    f"Model response item {i} is missing '{output_key}' text."
                )
            outputs.append(value.strip())
        return outputs


# ---------------------------------------------------------------------------
# Storage (job directory, no database)
# ---------------------------------------------------------------------------

def save_romanized_subtitles(
    job_dir: Path,
    job_id: str,
    subtitles: List[Subtitle],
    model: str,
    include_english: bool,
) -> Path:
    """Persist romanized subtitles as romanized_subtitles.json in the job dir."""
    job_dir = Path(job_dir)
    payload = {
        "job_id": job_id,
        "model": model,
        "include_english": include_english,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "subtitles": [s.to_dict() for s in subtitles],
    }
    path = job_dir / ROMANIZED_SUBTITLES_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_romanized_subtitles(job_dir: Path) -> Optional[Dict]:
    """Load romanized_subtitles.json from a job directory; None if absent/invalid."""
    path = Path(job_dir) / ROMANIZED_SUBTITLES_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("subtitles"), list):
        return None
    return data
