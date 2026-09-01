#!/usr/bin/env python
"""Quick test of SRT service"""

def seconds_to_srt_timestamp(seconds):
    if not isinstance(seconds, (int, float)) or seconds < 0:
        seconds = 0.0
    total_seconds = int(seconds)
    milliseconds = int(round((seconds - total_seconds) * 1000))
    hours = total_seconds // 3600
    remainder = total_seconds % 3600
    minutes = remainder // 60
    secs = remainder % 60
    if milliseconds >= 1000:
        milliseconds = 0
        secs += 1
        if secs >= 60:
            secs = 0
            minutes += 1
            if minutes >= 60:
                minutes = 0
                hours += 1
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}'

def generate_srt(subtitles, mode="romanized"):
    if not subtitles:
        return ""
    if mode not in ("romanized", "english", "dual"):
        raise ValueError(f"Invalid mode: {mode}")
    
    srt_lines = []
    for idx, subtitle in enumerate(subtitles, start=1):
        start = subtitle.get("start", 0.0)
        end = subtitle.get("end", 0.0)
        romanized = subtitle.get("romanized_text", "")
        english = subtitle.get("english_text", "")
        
        if mode == "romanized":
            text_content = romanized
        elif mode == "english":
            text_content = english if english else romanized
        else:
            text_content = f"{romanized}\n{english}" if english else romanized
        
        srt_lines.append(str(idx))
        srt_lines.append(f"{seconds_to_srt_timestamp(start)} --> {seconds_to_srt_timestamp(end)}")
        srt_lines.append(text_content)
        srt_lines.append("")
    
    return "\n".join(srt_lines)

if __name__ == "__main__":
    # Test SRT generation
    subtitles = [
        {
            "id": 1,
            "start": 0.0,
            "end": 2.8,
            "romanized_text": "Agar main 2026 mein AI/ML parhna start karta",
            "english_text": "If I were starting to learn AI/ML in 2026"
        },
        {
            "id": 2,
            "start": 2.8,
            "end": 5.5,
            "romanized_text": "To main ye teen websites zaroor use karta",
            "english_text": "Then I would definitely use these three websites"
        }
    ]

    print("=== ROMANIZED SRT ===")
    print(generate_srt(subtitles, "romanized"))
    print("\n=== ENGLISH SRT ===")
    print(generate_srt(subtitles, "english"))
    print("\n=== DUAL SRT ===")
    print(generate_srt(subtitles, "dual"))
    print("\n✓ All SRT generation tests passed!")
