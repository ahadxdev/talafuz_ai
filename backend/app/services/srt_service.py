"""
Phase 4 — SRT export service.

Converts subtitle data to valid SubRip (.srt) format with proper timestamp
formatting and support for multiple modes (romanized, english, dual).

SRT format specification:
- Sequence number (1-indexed)
- Start timestamp --> End timestamp (HH:MM:SS,mmm)
- Subtitle text (one or more lines)
- Blank line separator
"""
from typing import List, Dict, Any


def seconds_to_srt_timestamp(seconds: float) -> str:
    """
    Convert seconds (float) to SRT timestamp format.

    Example:
        0.0 → "00:00:00,000"
        3.5 → "00:00:03,500"
        125.75 → "00:02:05,750"
    """
    # Handle edge cases
    if not isinstance(seconds, (int, float)) or seconds < 0:
        seconds = 0.0

    # Calculate components
    total_seconds = int(seconds)
    milliseconds = int(round((seconds - total_seconds) * 1000))

    hours = total_seconds // 3600
    remainder = total_seconds % 3600
    minutes = remainder // 60
    secs = remainder % 60

    # Handle millisecond rounding edge case (e.g., 999.9999 → 1000 ms)
    if milliseconds >= 1000:
        milliseconds = 0
        secs += 1
        if secs >= 60:
            secs = 0
            minutes += 1
            if minutes >= 60:
                minutes = 0
                hours += 1

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def generate_srt(
    subtitles: List[Dict[str, Any]],
    mode: str = "romanized"
) -> str:
    """
    Generate valid SRT format from subtitle data.

    Args:
        subtitles: List of subtitle dictionaries with keys:
            - id (int): subtitle index
            - start (float): start time in seconds
            - end (float): end time in seconds
            - romanized_text (str): romanized text
            - english_text (str, optional): English translation
        mode (str): Display mode:
            - "romanized": show romanized_text only
            - "english": show english_text only
            - "dual": show romanized_text, then english_text on next line

    Returns:
        Valid SRT format as a string.

    Raises:
        ValueError: If subtitle data is invalid or incomplete for the mode.
    """
    if not subtitles:
        return ""

    if mode not in ("romanized", "english", "dual"):
        raise ValueError(f"Invalid mode: {mode}. Must be 'romanized', 'english', or 'dual'.")

    srt_lines = []

    for idx, subtitle in enumerate(subtitles, start=1):
        # Extract fields
        start = subtitle.get("start", 0.0)
        end = subtitle.get("end", 0.0)
        romanized = subtitle.get("romanized_text", "")
        english = subtitle.get("english_text", "")

        # Validate
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise ValueError(f"Subtitle {idx}: invalid timestamp format")
        if start < 0 or end < 0:
            raise ValueError(f"Subtitle {idx}: timestamps cannot be negative")
        if start >= end:
            raise ValueError(f"Subtitle {idx}: start must be before end")

        # Build subtitle text based on mode
        if mode == "romanized":
            if not romanized:
                raise ValueError(f"Subtitle {idx}: romanized_text is missing or empty")
            text_content = romanized
        elif mode == "english":
            if not english:
                raise ValueError(f"Subtitle {idx}: english_text is missing or empty for mode 'english'")
            text_content = english
        else:  # dual
            if not romanized:
                raise ValueError(f"Subtitle {idx}: romanized_text is missing or empty")
            # If English is missing, fall back to romanized only
            if english:
                text_content = f"{romanized}\n{english}"
            else:
                text_content = romanized

        # Add to SRT (sequence number, timestamps, text, blank line)
        srt_lines.append(str(idx))
        srt_lines.append(f"{seconds_to_srt_timestamp(start)} --> {seconds_to_srt_timestamp(end)}")
        srt_lines.append(text_content)
        srt_lines.append("")  # Blank line separator

    return "\n".join(srt_lines)
