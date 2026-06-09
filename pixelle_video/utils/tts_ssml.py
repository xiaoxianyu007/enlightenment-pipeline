"""
Edge-TTS 中文旁白工具（Yunyang · 标点增强 · 逐句情感）

基于深度研究结论：
- 微软封杀自定义 SSML，只接受 <voice><prosody>文本</prosody></voice>
- 通过 rate/pitch 参数 + 文本标点预处理提升自然度
- 方案A：标点增强（逗号插入 = 自然停顿）
"""

import asyncio, os, subprocess, re, shutil
import tempfile

VOICE = "zh-CN-YunyangNeural"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

BASE_RATE = "-20%"
BASE_PITCH = "-6Hz"

EMO_MAP = {
    "calm":       ("-28%", "-10Hz"),
    "normal":     (BASE_RATE, BASE_PITCH),
    "passionate": ("-12%", "+2Hz"),
    "dramatic":   ("-6%",  "+8Hz"),
}

_COMMA_BEFORE_WORDS = [
    "然而", "但是", "因此", "于是", "接着", "最后", "最终",
    "不过", "可是", "所以", "此外", "同时", "随后", "此后",
]

_COMMA_BEFORE_PATTERNS = [
    (r'(?<=。)(在\d{4}年)', r'，\1'),
    (r'(?<=。)(到了\d{4}年)', r'，\1'),
    (r'(?<=。)(在.{2,8}，)', r'，\1'),
]


def _enhance_text(text: str) -> str:
    for ch in ['\u200b', '\u200c', '\u200d', '\ufeff', '\u00ad', '\ufffd']:
        text = text.replace(ch, '')
    for i in range(0x20):
        text = text.replace(chr(i), '')
    for word in _COMMA_BEFORE_WORDS:
        text = re.sub(rf'(?<![，。！？、：；\s]){word}', rf'，{word}', text)
    for pat, repl in _COMMA_BEFORE_PATTERNS:
        text = re.sub(pat, repl, text)

    def _break_long(text):
        parts = re.split(r'([，。！？、：；])', text)
        result = []
        i = 0
        while i < len(parts):
            chunk = parts[i]
            if len(chunk) > 50 and i % 2 == 0:
                mid = len(chunk) // 2
                for m in re.finditer(r'(?<=[了的是在和与把被将以从])', chunk):
                    if 15 < m.start() < len(chunk) - 10:
                        mid = m.start()
                        break
                result.append(chunk[:mid] + '，')
                result.append(chunk[mid:])
            else:
                result.append(chunk)
            i += 1
        return ''.join(result)

    text = _break_long(text)
    return text.strip()


def _split_sentences(text: str) -> list:
    parts = re.split(r'(?<=[。！？.!?])', text)
    return [p.strip() for p in parts if p.strip()] or [text]


def _classify_sentence(s: str) -> str:
    s = s.strip()
    if not s:
        return "normal"
    if s.endswith("！") or s.endswith("!"):
        return "dramatic" if (s.count("！") + s.count("!")) > 1 else "passionate"
    if s.endswith("？") or s.endswith("?"):
        return "passionate"
    if len(s) > 60:
        return "calm"
    return "normal"


async def _gen_one(text: str, rate: str, pitch: str, out: str):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch).save(out)


def _probe_dur(path: str) -> float:
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip()) if r.stdout.strip() else 0.0
    except Exception:
        return 0.0


def _concat(seg_files: list, out: str):
    txt = os.path.join(tempfile.gettempdir(), "_tts_c.txt")
    with open(txt, "w") as f:
        for sp in seg_files:
            f.write(f"file '{sp}'\n")
    subprocess.run([FFMPEG, "-f", "concat", "-safe", "0", "-i", txt, "-c", "copy", "-y", out],
                   capture_output=True, timeout=120)
    try:
        os.remove(txt)
    except OSError:
        pass


def generate(text: str, output_path: str, cinematic: bool = False) -> float:
    text = _enhance_text(text)
    sentences = _split_sentences(text)
    if len(sentences) == 1:
        emo = _classify_sentence(sentences[0])
        rate, pitch = EMO_MAP[emo]
        tmp = output_path.replace(".mp3", "_r.mp3")
        asyncio.run(_gen_one(sentences[0], rate, pitch, tmp))
        os.replace(tmp, output_path)
        return _probe_dur(output_path)
    segs = []
    for i, s in enumerate(sentences):
        emo = _classify_sentence(s)
        rate, pitch = EMO_MAP[emo]
        sp = os.path.join(tempfile.gettempdir(), f"_t_{i}.mp3")
        asyncio.run(_gen_one(s, rate, pitch, sp))
        segs.append(sp)
    tmp = output_path.replace(".mp3", "_r.mp3")
    _concat(segs, tmp)
    os.replace(tmp, output_path)
    for sp in segs:
        try:
            os.remove(sp)
        except OSError:
            pass
    return _probe_dur(output_path)
