"""
Edge-TTS 中文旁白工具（Yunyang · 标点增强 · 逐句情感）

基于深度研究结论：
- 微软封杀自定义 SSML，只接受 <voice><prosody>文本</prosody></voice>
- 通过 rate/pitch 参数 + 文本标点预处理提升自然度
- 方案A：标点增强（逗号插入 = 自然停顿）
"""

import asyncio, os, subprocess, re
import tempfile

VOICE = "zh-CN-YunyangNeural"
FFMPEG = "/home/shuju46/miniconda3/envs/pixelle_video/bin/ffmpeg"
FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")

# 基准参数 = demo_yunyang_v2
BASE_RATE = "-20%"
BASE_PITCH = "-6Hz"

# 情感微调（全部围绕基准，比之前拉开更大差距）
EMO_MAP = {
    "calm":       ("-28%", "-10Hz"),  # 平静 → 更慢更深
    "normal":     (BASE_RATE, BASE_PITCH),  # 默认 v2
    "passionate": ("-12%", "+2Hz"),   # 激昂 → 略快略高
    "dramatic":   ("-6%",  "+8Hz"),   # 高潮 → 快而高
}


# ═══════════════════════════════════════════════════════
#  方案A核心：标点增强 → 强制 TTS 插入自然停顿
# ═══════════════════════════════════════════════════════

_COMMA_BEFORE_WORDS = [
    "然而", "但是", "因此", "于是", "接着", "最后", "最终",
    "不过", "可是", "所以", "此外", "同时", "随后", "此后",
]

_COMMA_BEFORE_PATTERNS = [
    (r'(?<=。)(在\d{4}年)', r'，\1'),           # 。1789年 → 。，1789年 (句首时间加停顿)
    (r'(?<=。)(到了\d{4}年)', r'，\1'),          # 。到了1789年 → 。，到了1789年
    (r'(?<=。)(在.{2,8}，)', r'，\1'),            # 。在巴黎， → 。，在巴黎，
]


def _enhance_text(text: str) -> str:
    """
    标点增强：在关键位置插入逗号，让 TTS 产生自然停顿。

    TTS 引擎对标点有强烈反应：
    - 逗号 → 约 200-300ms 停顿
    - 句号 → 约 500ms 停顿
    没有标点的长句会被一口气读完，听感机械。
    """
    # 1. 清除异常字符
    for ch in ['\u200b', '\u200c', '\u200d', '\ufeff', '\u00ad', '\ufffd']:
        text = text.replace(ch, '')
    for i in range(0x20):
        text = text.replace(chr(i), '')

    # 2. 过渡词前加逗号（如果前面没有标点）
    for word in _COMMA_BEFORE_WORDS:
        text = re.sub(rf'(?<![，。！？、：；\s]){word}', rf'，{word}', text)

    # 3. 固定模式加逗号
    for pat, repl in _COMMA_BEFORE_PATTERNS:
        text = re.sub(pat, repl, text)

    # 4. 超长逗号间隔自动断句（>70字没有标点 → 在最后的安全位置插入逗号）
    def _break_long(text):
        parts = re.split(r'([，。！？、：；])', text)
        result = []
        i = 0
        while i < len(parts):
            chunk = parts[i]
            # 阈值提高到 70 字，减少对正常句子的干扰
            if len(chunk) > 70 and i % 2 == 0:
                # 找到范围内最后一个安全断点（优先不断在前半段，保护主语和修饰语不被打断）
                best = -1
                for m in re.finditer(r'(?<=[了的是在和与把被将以从后前也而])', chunk):
                    if 25 < m.start() < len(chunk) - 15:
                        best = m.start()  # 取最后一个匹配，尽量保持前面的语意完整
                if best > 0:
                    result.append(chunk[:best] + '，')
                    result.append(chunk[best:])
                else:
                    result.append(chunk)
            else:
                result.append(chunk)
            i += 1
        return ''.join(result)

    text = _break_long(text)

    return text.strip()


# ═══════════════════════════════════════════════════════
#  句子拆分与情感分类
# ═══════════════════════════════════════════════════════

def _split_sentences(text: str) -> list:
    """按句末标点拆分"""
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


# ═══════════════════════════════════════════════════════
#  核心：文本 → 音频
# ═══════════════════════════════════════════════════════

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
    """
    生成纪录片旁白音频（Yunyang · v2参数 · 标点增强）。

    Parameters
    ----------
    text : str
        中文文本
    output_path : str
        输出 .mp3 路径
    cinematic : bool
        FFmpeg 后期（默认关闭）
    """
    text = _enhance_text(text)
    sentences = _split_sentences(text)

    if len(sentences) == 1:
        emo = _classify_sentence(sentences[0])
        rate, pitch = EMO_MAP[emo]
        tmp = output_path.replace(".mp3", "_r.mp3")
        asyncio.run(_gen_one(sentences[0], rate, pitch, tmp))
        os.replace(tmp, output_path)
        return _probe_dur(output_path)

    # 多句：逐句生成 + 拼接
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


# ═══════════════════════════════════════════════════════
#  测试
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    text = ("攻占巴士底狱的消息震撼了整个法国。"
            "在乡村农民奋起反抗领主焚烧庄园和封建记录。"
            "国民议会彻夜工作彻底废除了封建特权。")
    out = "/home/shuju46/Oray/Mi/Pixelle-Video/demo_enhanced.mp3"
    dur = generate(text, out)
    print(f"✓ {out} ({dur:.1f}s)")
    print(f"  预处理后: {_enhance_text(text)}")