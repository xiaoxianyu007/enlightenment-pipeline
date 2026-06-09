"""
SRT Subtitle Utility - Generate SRT subtitle files from narration text.
"""

import os
import re


def _split_en_sentences(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    raw = re.split(r'(?<=[.!?])\s+|(?<=[.!?])$', text)
    result = [s.strip() for s in raw if s.strip()]
    return result if result else [text]


def _split_zh_sentences(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    raw = re.split(r'(?<=[。！？])', text)
    result = [s.strip() for s in raw if s.strip()]
    return result if result else [text]


def _align_sentences(en_sentences: list, zh_sentences: list) -> list:
    if not en_sentences or not zh_sentences:
        return [(" ".join(en_sentences), "".join(zh_sentences))]
    if len(en_sentences) == len(zh_sentences):
        return list(zip(en_sentences, zh_sentences))
    if len(en_sentences) > len(zh_sentences):
        merged_en = _merge_to_target(en_sentences, len(zh_sentences))
        return list(zip(merged_en, zh_sentences))
    else:
        merged_zh = _merge_to_target(zh_sentences, len(en_sentences))
        return list(zip(en_sentences, merged_zh))


def _merge_to_target(items: list, target_n: int) -> list:
    if target_n <= 0 or target_n >= len(items):
        return items
    total_chars = sum(len(s) for s in items)
    chars_per_group = total_chars / target_n
    groups = []
    current = ""
    current_chars = 0
    for item in items:
        if not current:
            current = item
            current_chars = len(item)
        elif current_chars + len(item) < chars_per_group * 1.5:
            current += item
            current_chars += len(item)
        else:
            groups.append(current)
            current = item
            current_chars = len(item)
    if current:
        groups.append(current)
    while len(groups) < target_n:
        groups.append("")
    if len(groups) > target_n:
        groups = groups[:target_n - 1] + ["".join(groups[target_n - 1:])]
    return groups


def split_text_into_chunks(text: str, max_chars: int = 35) -> list:
    words = text.strip().split()
    if not words:
        return []
    chunks = []
    current = words[0]
    for i, word in enumerate(words[1:], start=1):
        test = f"{current} {word}"
        if len(test) <= max_chars:
            current = test
        else:
            remaining_words = words[i:]
            remaining_total = sum(len(w) + 1 for w in remaining_words) - 1
            if remaining_total < 5:
                current = test
            else:
                chunks.append(current)
                current = word
    if current:
        chunks.append(current)
    return chunks


def generate_bilingual_srt(en_text: str, zh_text: str, audio_duration: float, max_chars: int = 35) -> str:
    en_sentences = _split_en_sentences(en_text)
    zh_sentences = _split_zh_sentences(zh_text)
    if not en_sentences:
        return ""
    aligned_pairs = _align_sentences(en_sentences, zh_sentences)
    entries = []
    for en_part, zh_part in aligned_pairs:
        if len(en_part) > max_chars * 1.5:
            en_sub = split_text_into_chunks(en_part, max_chars)
            zh_sub = _merge_to_target(
                _split_zh_sentences(zh_part) if (zh_part and len(zh_part) > 5) else [zh_part],
                len(en_sub)
            ) if zh_part else [""] * len(en_sub)
            while len(zh_sub) < len(en_sub):
                zh_sub.append("")
            zh_sub = zh_sub[:len(en_sub)]
            for e, z in zip(en_sub, zh_sub):
                entries.append((e, z))
        else:
            entries.append((en_part, zh_part or ""))
    if not entries:
        entries = [(en_text, zh_text)]
    total_chars = sum(len(en) + len(zh) for en, zh in entries)
    if total_chars == 0:
        return ""
    time_per_char = max(audio_duration / total_chars, 0.01)
    gap = 0.15
    srt_lines = []
    current_time = 0.0
    for i, (en_chunk, zh_chunk) in enumerate(entries):
        chunk_duration = (len(en_chunk) + len(zh_chunk)) * time_per_char
        chunk_duration = max(chunk_duration, 1.0)
        start_time = current_time
        end_time = min(current_time + chunk_duration, audio_duration)
        start_str = _format_srt_time(start_time)
        end_str = _format_srt_time(end_time)
        srt_lines.append(f"{i + 1}")
        srt_lines.append(f"{start_str} --> {end_str}")
        srt_lines.append(en_chunk)
        srt_lines.append(zh_chunk)
        srt_lines.append("")
        current_time = end_time + gap
    return "\n".join(srt_lines)


def generate_srt(text: str, audio_duration: float, max_chars: int = 35) -> str:
    chunks = split_text_into_chunks(text, max_chars)
    if not chunks:
        return ""
    total_chars = sum(len(chunk) for chunk in chunks)
    if total_chars == 0:
        return ""
    time_per_char = audio_duration / total_chars
    gap = 0.15
    srt_lines = []
    current_time = 0.0
    for i, chunk in enumerate(chunks):
        chunk_duration = len(chunk) * time_per_char
        chunk_duration = max(chunk_duration, 1.0)
        end_time = min(current_time + chunk_duration, audio_duration)
        srt_lines.append(f"{i + 1}")
        srt_lines.append(f"{_format_srt_time(current_time)} --> {_format_srt_time(end_time)}")
        srt_lines.append(chunk)
        srt_lines.append("")
        current_time = end_time + gap
    return "\n".join(srt_lines)


def _format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt_file(text: str, audio_duration: float, output_path: str, max_chars: int = 35) -> str:
    srt_content = generate_srt(text, audio_duration, max_chars)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return output_path
