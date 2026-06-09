#!/usr/bin/env python3
"""
Demo v7-SSML：分层架构 + Edge-TTS 中文SSML朗读 + 双语字幕

用法:
    python demo_parallax_subtitle.py                     # 使用默认3句（第1集前3句）
    python demo_parallax_subtitle.py --episode 1         # 完整处理第1集（取前3段）
    python demo_parallax_subtitle.py --episode 1 --all   # 完整处理第1集所有段

说明：
    - TTS: Edge-TTS YunyangNeural，SSML 优化，中文朗读
    - 字幕: PIL 绘制的双语字幕（中英文在同一张图上）
    - 视差: DepthFlow 视差动效
    - 合成: 分层架构（背景 xfade → 字幕 overlay）
"""

# ── 清理 socks 代理 ──
import os as _os
for _k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
           "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
    _os.environ.pop(_k, None)

import os, sys, shutil, tempfile, subprocess, re, argparse, json, time, requests
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output", "demo_v7")
EP_IMG_DIR = os.path.join(PROJECT_ROOT, "output", "episode_images")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(EP_IMG_DIR, exist_ok=True)

EPISODES_FILE_EN = os.path.join(PROJECT_ROOT, "enlightenment_22_episodes.txt")
EPISODES_FILE_ZH = os.path.join(PROJECT_ROOT, "enlightenment_22_episodes_zh.txt")

# ── 路径（自动查找 ffmpeg，无需硬编码 conda 路径）──
_ffmpeg_bin = shutil.which("ffmpeg")
if _ffmpeg_bin:
    CONDA_BIN = os.path.dirname(_ffmpeg_bin)
    FFMPEG = _ffmpeg_bin
else:
    CONDA_BIN = ""
    FFMPEG = "ffmpeg"
os.environ["PATH"] = f"{CONDA_BIN}:{os.environ.get('PATH', '')}".strip(":")
EN_FONT = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
CN_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
COMFY = "http://127.0.0.1:8188"
COMFY_WF = os.path.join(PROJECT_ROOT, "workflows/selfhost/image_flux.json")

COPPERPLATE = (
    "Monochrome European copperplate engraving, 18th century printmaking aesthetic, "
    "fine cross-hatching, high contrast black and white, antique paper texture, "
    "dramatic chiaroscuro lighting, European historical scene"
)

# ── 样式 ──
VIDEO_W, VIDEO_H = 1024, 1792
FONT_EN = 40
FONT_CN = 36
T_DUR = 0.8
AUDIO_GAP = 0.8
TTS_BASE_DUR = 8.0

# ── 兜底：从 output 目录找旧图片（非 episode 模式用） ──

def _find_latest_images(count=3):
    candidates = []
    for root, dirs, files in os.walk(os.path.join(PROJECT_ROOT, "output")):
        for f in sorted(files):
            if f.endswith("_image.png"):
                candidates.append(os.path.join(root, f))
    if len(candidates) >= count:
        return sorted(candidates)[-count:]
    return [os.path.join(PROJECT_ROOT, "output", "default.png")] * count


# ═══════════════ ComfyUI 自动生图 ═══════════════

def _comfy_queue(prompt_text, seed):
    with open(COMFY_WF) as f: wf = json.load(f)
    for nid, node in wf.items():
        if node.get("class_type") == "CLIPTextEncode":
            wf[nid]["inputs"]["text"] = prompt_text
        if node.get("class_type") == "KSampler":
            wf[nid]["inputs"]["seed"] = seed
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def _comfy_wait(prompt_id, timeout=600):
    for _ in range(timeout):
        time.sleep(2)
        r = requests.get(f"{COMFY}/history/{prompt_id}", timeout=10)
        if r.status_code != 200 or prompt_id not in r.json():
            continue
        for no in r.json()[prompt_id].get("outputs", {}).values():
            for img in no.get("images", []):
                return requests.get(f"{COMFY}/view", params={
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output")}, timeout=60).content
    return None


def _gen_image_prompts(title_en, en_sentences):
    """用 LLM 生成逐句提示词，失败则用模板"""
    try:
        import yaml
        cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            llm = cfg.get("llm", {})
            if llm.get("api_key") and llm.get("base_url"):
                from openai import OpenAI
                client = OpenAI(api_key=llm["api_key"], base_url=llm["base_url"])
                items = "\n".join(f"{i+1}. {s}" for i, s in enumerate(en_sentences))
                resp = client.chat.completions.create(
                    model=llm.get("model", "gpt-4o"),
                    messages=[{"role": "user", "content": (
                        f"Episode: {title_en}\nStyle: {COPPERPLATE}\n"
                        f"Output as JSON. Generate one unique image prompt per sentence (50-80 words each). "
                        f"18th century European, no Asian, no modern, no text.\n"
                        f"{items}"
                    )}],
                    temperature=0.7, max_tokens=4096, response_format={"type": "json_object"})
                data = json.loads(resp.choices[0].message.content)
                prompts = data if isinstance(data, list) else data.get("image_prompts", [])
                # 归一化：确保每个元素是字符串
                prompts = [p["prompt"] if isinstance(p, dict) and "prompt" in p else (p if isinstance(p, str) else str(p)) for p in prompts]
                if len(prompts) == len(en_sentences):
                    return prompts
    except Exception as e:
        log(f"  LLM失败: {e}")
    return [f"{COPPERPLATE}: {s[:200]}, no Chinese people, no Asian features, no modern elements, no text"
            for s in en_sentences]


def _auto_gen_images(ep, title_en, en_sentences, limit=0):
    """自动生成逐句配图（缺图才生成）"""
    n = min(len(en_sentences), limit) if limit > 0 else len(en_sentences)
    images = []
    for i in range(n):
        out = os.path.join(EP_IMG_DIR, f"ep{ep:02d}_{i:02d}_image.png")
        images.append(out)
    missing = [i for i, p in enumerate(images) if not os.path.exists(p)]
    if not missing:
        log(f"  ✓ {n} 张配图已就绪")
        return images

    log(f"  ComfyUI 生图: {len(missing)}/{n} 张新图 ...")
    try:
        requests.get(f"{COMFY}/queue", timeout=3)
    except:
        log("  ⚠ ComfyUI 未启动，用已有/占位图")
        return images

    prompts = _gen_image_prompts(title_en, [en_sentences[i] for i in missing])
    for j, (idx, prompt) in enumerate(zip(missing, prompts)):
        out = images[idx]
        log(f"  [{j+1}/{len(missing)}] {prompt[:60]}...", end=" ")
        try:
            pid = _comfy_queue(prompt, seed=42 + ep * 100 + idx)
            data = _comfy_wait(pid)
            if data:
                with open(out, "wb") as f: f.write(data)
                log(f"✓ {len(data)//1024}KB")
            else:
                log("✗ 超时")
        except Exception as e:
            log(f"✗ {e}")
    return images


# ── 工具：句子拆分与匹配 ──

def _split_sents(text, lang="zh"):
    """按句末标点拆分，过滤空句"""
    if lang == "zh":
        parts = [s.strip() for s in re.split(r'(?<=[。！？])', text) if s.strip()]
    else:
        parts = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    return parts if parts else [text]


def _match_sentences(en_text, zh_text):
    """
    中英文句子一一配对。两边分别按标点拆分后，1:1 匹配。
    如果句子数不一致，取最小值并截断多余的。
    """
    en_sents = _split_sents(en_text, "en")
    zh_sents = _split_sents(zh_text, "zh")
    n = min(len(en_sents), len(zh_sents))
    if n < 3:
        # 太少了不拆分，整段当一句
        return [(en_text, zh_text)]
    return list(zip(en_sents[:n], zh_sents[:n]))


# ── 读取中英文分集文案 ──
def _read_episode(ep_num: int):
    """
    从中英文文案文件中读取指定集，配对段落。

    Returns:
        (title_en, title_zh, en_paragraphs, zh_paragraphs)
    """
    def _read_file(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    en_content = _read_file(EPISODES_FILE_EN)
    zh_content = _read_file(EPISODES_FILE_ZH)

    # 中文数字映射（中文文件使用"第一集"而非"第1集"）
    CN_NUM_MAP = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                  "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八",
                  "十九", "二十", "二十一", "二十二"]

    def _cn_ep(n: int) -> str:
        if 1 <= n <= 22:
            return CN_NUM_MAP[n]
        return str(n)

    def _extract(content, is_zh=False):
        if is_zh:
            cn_cur = _cn_ep(ep_num)
            cn_nxt = _cn_ep(ep_num + 1)
            pattern = rf"第{cn_cur}集[：:](.+?)\n\n(.*?)(?=\n\n第{cn_nxt}集[：:]|\Z)"
        else:
            pattern = rf"Episode {ep_num}:(.+?)\n\n(.*?)(?=\n\nEpisode {ep_num+1}:|\Z)"
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            raise ValueError(f"未找到 Episode/第{ep_num}集")
        title = match.group(1).strip()
        body = match.group(2).strip()
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
        return title, paragraphs

    title_en, paras_en = _extract(en_content, is_zh=False)
    title_zh, paras_zh = _extract(zh_content, is_zh=True)

    # 配对段落：如果段落数不一致，取最小值
    n = min(len(paras_en), len(paras_zh))
    if n < max(len(paras_en), len(paras_zh)):
        log(f"  ⚠ 中英文段落数不一致 (EN={len(paras_en)}, ZH={len(paras_zh)})，取前{n}段")

    return title_en, title_zh, paras_en[:n], paras_zh[:n]


def log(m, end="\n"):
    print(m, end=end, flush=True)


def probe_dur(path):
    try:
        r = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
                           "-show_entries", "format=duration",
                           "-of", "default=noprint_wrappers=1:nokey=1", path],
                          capture_output=True, text=True, timeout=10)
        if r.stdout.strip():
            return float(r.stdout.strip())
    except:
        pass
    return 0.0


def gen_audio_ssml(zh_text, out_path):
    """使用 Edge-TTS + SSML 生成中文旁白"""
    print("  TTS(SSML)...", end=" ", flush=True)
    from pixelle_video.utils.tts_ssml import generate
    dur = generate(zh_text, out_path, cinematic=False)
    log(f"✓ ({dur:.1f}s)"); return dur


def gen_audio_fallback(out_path, dur=TTS_BASE_DUR):
    """静音回退"""
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                   f"anullsrc=r=24000:cl=mono", "-t", f"{dur}", out_path],
                  capture_output=True)
    return dur


def render_green(w, h, en, cn):
    """渲染双语字幕为绿色底图（用于 chromakey overlay）"""
    ef = ImageFont.truetype(EN_FONT, FONT_EN)
    cf = ImageFont.truetype(CN_FONT, FONT_CN)
    ol = Image.new("RGB", (w, h), (0, 255, 0))
    draw = ImageDraw.Draw(ol)
    mt = w - 60

    import importlib.util as u
    p = os.path.join(PROJECT_ROOT, "pixelle_video/utils/image_subtitle.py")
    s = u.spec_from_file_location("x", p)
    m = u.module_from_spec(s)
    try:
        s.loader.exec_module(m)
    except:
        log(f"  ✗ 导入失败"); raise

    el = m.word_wrap_lines(en, ef, mt, draw, 3)
    cl = m.char_wrap_lines(cn, cf, mt, draw, 3)

    ehs = [draw.textbbox((0, 0), l, font=ef)[3] - draw.textbbox((0, 0), l, font=ef)[1] for l in el]
    chs = [draw.textbbox((0, 0), l, font=cf)[3] - draw.textbbox((0, 0), l, font=cf)[1] for l in cl]
    ebh = sum(ehs) + max(0, len(el) - 1) * 4
    cbh = sum(chs) + max(0, len(cl) - 1) * 2
    th = ebh + cbh + 8

    mw = 0
    for l in el:
        mw = max(mw, draw.textbbox((0, 0), l, font=ef)[2] - draw.textbbox((0, 0), l, font=ef)[0])
    for l in cl:
        mw = max(mw, draw.textbbox((0, 0), l, font=cf)[2] - draw.textbbox((0, 0), l, font=cf)[0])
    bg_w = mw + 48
    bg_h = th + 28
    bg_x = (w - bg_w) // 2
    bg_y = max(10, (h // 4) - (bg_h // 2))
    draw.rounded_rectangle((bg_x, bg_y, bg_x + bg_w, bg_y + bg_h), radius=10, fill=(0, 0, 0))

    tx, ty = bg_x + 24, bg_y + 14
    for i, l in enumerate(el):
        lw = draw.textbbox((0, 0), l, font=ef)[2] - draw.textbbox((0, 0), l, font=ef)[0]
        lx = tx + (mw - lw) // 2
        for ox, oy in [(2, 2), (2, 0), (0, 0)]:
            draw.text((lx + ox, ty + oy), l, font=ef, fill=(0, 0, 0) if ox or oy else (255, 255, 255))
        ty += ehs[i] + 4
    ty += 4
    for i, l in enumerate(cl):
        lw = draw.textbbox((0, 0), l, font=cf)[2] - draw.textbbox((0, 0), l, font=cf)[0]
        lx = tx + (mw - lw) // 2
        for ox, oy in [(2, 2), (2, 0), (0, 0)]:
            draw.text((lx + ox, ty + oy), l, font=cf, fill=(0, 0, 0) if ox or oy else (255, 255, 255))
        ty += chs[i] + 2
    return ol


def main():
    parser = argparse.ArgumentParser(description="SSML 中文旁白 + 双语字幕 Demo")
    parser.add_argument("--episode", type=int, default=0, help="集号 (1-22)")
    parser.add_argument("--all", action="store_true", help="处理该集所有段落（默认只取前3段）")
    args = parser.parse_args()

    log("╔" + "═" * 58 + "╗")
    log("║  v7-SSML — Edge-TTS 中文SSML + 双语字幕        ║")
    log("╚" + "═" * 58 + "╝")

    # ── 读取文案 ──
    if args.episode > 0:
        title_en, title_zh, paras_en, paras_zh = _read_episode(args.episode)
        log(f"  第{args.episode}集: {title_en} / {title_zh}")

        # 段拆句 → 逐句配图
        all_pairs = []
        for en_para, zh_para in zip(paras_en, paras_zh):
            all_pairs.extend(_match_sentences(en_para, zh_para))
        total_sents = len(all_pairs)

        if not args.all:
            all_pairs = all_pairs[:3]
            log(f"  → 取前 3 句测试 (共 {total_sents} 句)")
        else:
            log(f"  → 全部 {total_sents} 句")

        # 自动生成逐句配图（缺图才调 ComfyUI）
        en_sentences = [p[0] for p in all_pairs]
        IMAGES = _auto_gen_images(args.episode, title_en, en_sentences)

        motions = ["horizontal", "zoom", "orbital", "horizontal", "zoom", "orbital"]
        SENTENCES = []
        for i, (en_s, zh_s) in enumerate(all_pairs):
            motion = motions[i % len(motions)]
            SENTENCES.append((motion, en_s, zh_s))
    else:
        # 默认 3 句测试
        IMAGES = _find_latest_images(3)
        SENTENCES = [
            ("horizontal",
             "In the late 17th and early 18th centuries, Europe was undergoing a revolution unlike any before.",
             "17世纪末18世纪初，欧洲正经历着一场前所未有的革命。"),
            ("zoom",
             "Not a revolution of guns and cannons, but a revolution of ideas.",
             "这不是枪炮的革命，而是思想的革命。"),
            ("orbital",
             "In the elegant salons of Paris and the bustling coffeehouses of London, a small group of bold thinkers gathered to question everything.",
             "在巴黎的优雅沙龙和伦敦的喧嚣咖啡馆里，一小群勇敢的思想家聚集起来，质疑一切。"),
        ]

    log(f"  共 {len(SENTENCES)} 句 | 图片: {len(IMAGES)} 张")

    if not shutil.which("depthflow"):
        log("✗ 需要 depthflow: pip install depthflow"); sys.exit(1)

    td = tempfile.mkdtemp(prefix="v7_", dir=OUTPUT_DIR)
    bg_clips = []
    sub_overlays = []
    segment_durs = []
    audio_files = []
    audio_durs = []

    # ── 阶段1：生成各素材 ──
    for i, (motion, en, cn) in enumerate(SENTENCES):
        log(f"\n── 第{i+1}句 [{motion}] ──")
        img = IMAGES[i] if i < len(IMAGES) else IMAGES[-1]
        log(f"  图片: {os.path.basename(img)}")
        log(f"  中文: {cn[:50]}...")

        # 音频（Edge-TTS SSML 中文旁白）
        ap = os.path.join(td, f"a{i}.wav")
        dur = gen_audio_ssml(cn, ap)

        # DepthFlow 纯背景
        bg = os.path.join(td, f"bg{i}.mp4")
        print("  DepthFlow...", end=" ", flush=True)
        r = subprocess.run(["depthflow", "input", "-i", img, "da2", motion, "main",
                           "--speed", "0.5",
                           "-o", bg, "--time", f"{dur:.1f}",
                           "-w", str(VIDEO_W), "-h", str(VIDEO_H)],
                          capture_output=True, text=True, timeout=300,
                          env={**os.environ, "PATH": f"{CONDA_BIN}:{os.environ.get('PATH', '')}"})
        if r.returncode != 0:
            log("✗ DepthFlow 失败，回退静态图")
            subprocess.run([FFMPEG, "-y", "-loop", "1", "-framerate", "30",
                           "-t", f"{dur:.1f}", "-i", img,
                           "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-preset", "fast", "-crf", "20", bg],
                          check=True, capture_output=True)
        else:
            log("✓")

        # 字幕绿色叠加图
        grn = os.path.join(td, f"g{i}.png")
        render_green(VIDEO_W, VIDEO_H, en, cn).save(grn)

        bg_clips.append(bg)
        sub_overlays.append((grn, dur))
        segment_durs.append(dur)
        audio_files.append(ap)
        audio_durs.append(dur)

    # ── 阶段1.5：预填充 + 补空隙 ──
    # 1. 非首句 clip 开头预填充 T_DUR 秒（被 xfade 消耗，保护音频时长不被缩短）
    n = len(bg_clips)
    for i in range(1, n):
        pre = os.path.join(td, f"bg_pre{i}.mp4")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", bg_clips[i],
                       "-vf", f"tpad=start_mode=clone:start_duration={T_DUR}",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-preset", "fast", "-crf", "20", pre],
                      check=True, capture_output=True)
        bg_clips[i] = pre
    # 2. 非末尾 clip 末尾冻结 AUDIO_GAP 秒（填补句间停顿）
    for i in range(n - 1):
        ext = os.path.join(td, f"bg_ext{i}.mp4")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", bg_clips[i],
                       "-vf", f"tpad=stop_mode=clone:stop_duration={AUDIO_GAP}",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-preset", "fast", "-crf", "20", ext],
                      check=True, capture_output=True)
        bg_clips[i] = ext
    # segment_durs 保存原始时长，扩展后用于 xfade 计算
    seg_original = list(segment_durs)
    for i in range(n - 1):
        segment_durs[i] += AUDIO_GAP
    for i in range(1, n):
        segment_durs[i] += T_DUR

    # ── 阶段2：计算时间轴 ──
    # 字幕时间 = 音频时间（含句间停顿）
    sub_timeline = []
    cum_a = 0.0
    for i, d in enumerate(seg_original):
        sub_timeline.append((cum_a, cum_a + d))
        cum_a += d + AUDIO_GAP
    final_audio_dur = cum_a - AUDIO_GAP  # 最后一段后无停顿

    # ── 阶段3：背景 xfade ──
    log(f"\n{'='*60}\n合成背景视频 (xfade)...")
    bg_concat = os.path.join(td, "bg_concat.mp4")
    if n == 1:
        # 单片段：直接拷贝，无需 xfade
        subprocess.run([FFMPEG, "-y", "-i", bg_clips[0],
                       "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-preset", "medium", "-crf", "23", bg_concat],
                      check=True, capture_output=True)
    else:
        cmd = [FFMPEG, "-y", "-loglevel", "error"]
        for c in bg_clips:
            cmd.extend(["-i", c])
        fps = []
        pv = "0:v"
        cc = segment_durs[0]
        for i in range(1, n):
            cv = f"{i}:v"
            ol = f"xf{i}"
            off = cc - T_DUR  # 转场在前一段的定格帧期间发生
            fps.append(f"[{pv}][{cv}]xfade=transition=fade:duration={T_DUR:.1f}:offset={off:.3f},format=yuv420p[{ol}]")
            pv = ol
            cc = cc - T_DUR + segment_durs[i]
        cmd.extend(["-filter_complex", ";".join(fps), "-map", f"[{pv}]"])
        cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "23", bg_concat])
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
    log(f"  ✓ 背景视频")

    # ── 阶段4：叠加字幕 ──
    log("叠加字幕 overlay...")
    ep_tag = f"_ep{args.episode}" if args.episode > 0 else ""
    final_out = os.path.join(OUTPUT_DIR, f"demo_final{ep_tag}.mp4")
    cmd2 = [FFMPEG, "-y", "-loglevel", "error", "-i", bg_concat]
    for grn, _ in sub_overlays:
        cmd2.extend(["-i", grn])
    fps2 = []
    pv2 = "0:v"
    for i, ((s, e), (grn, _)) in enumerate(zip(sub_timeline, sub_overlays)):
        sl = f"{i+1}:v"
        ck = f"ck{i}"
        ol = f"ol{i}"
        fps2.append(f"[{sl}]chromakey=0x00FF00:0.15:0.05[{ck}]")
        fps2.append(f"[{pv2}][{ck}]overlay=0:0:format=auto:enable='between(t,{s:.1f},{e:.1f})'[{ol}]")
        pv2 = ol
    cmd2.extend(["-filter_complex", ";".join(fps2), "-map", f"[{pv2}]"])
    cmd2.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "23",
                  os.path.join(td, "_subbed.mp4")])
    subprocess.run(cmd2, check=True, capture_output=True, text=True, timeout=120)

    # ── 阶段5：音频拼接 ──
    log("合成音频...")
    aud_concat = os.path.join(td, "audio.mp3")
    aud_segments = []
    for i, ap in enumerate(audio_files):
        seg = os.path.join(td, f"aseg{i}.mp3")
        subprocess.run([FFMPEG, "-y", "-i", ap, "-vn", "-acodec", "libmp3lame", "-q:a", "2", seg],
                       check=True, capture_output=True)
        aud_segments.append(seg)
        if i < len(audio_files) - 1 and AUDIO_GAP > 0:
            gap = os.path.join(td, f"gap{i}.mp3")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                           f"anullsrc=r=44100:cl=mono", "-t", str(AUDIO_GAP), gap],
                           check=True, capture_output=True)
            aud_segments.append(gap)
    alist = os.path.join(td, "alist.txt")
    with open(alist, "w") as f:
        for s in aud_segments:
            f.write(f"file '{s}'\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", alist,
                    "-c", "copy", aud_concat],
                   check=True, capture_output=True, timeout=30)
    aud_dur = probe_dur(aud_concat)
    log(f"  ✓ 音频 {aud_dur:.1f}s")

    # ── 阶段6：音视频合成 ──
    log("音视频合并...")
    subprocess.run([FFMPEG, "-y", "-i", os.path.join(td, "_subbed.mp4"), "-i", aud_concat,
                    "-c:v", "copy", "-c:a", "aac", "-shortest", final_out],
                   check=True, capture_output=True, timeout=60)

    sz = os.path.getsize(final_out) / 1024 / 1024
    log(f"\n  ✓ {final_out} ({sz:.1f}MB)")
    log(f"  旁白: Edge-TTS YunyangNeural SSML 中文 | 字幕: 中英双语")
    log(f"  转场 {T_DUR}s | 句间停顿 {AUDIO_GAP}s")

    shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    # 动态导入 glob 用于 _find_latest_images
    import glob as glob_mod
    main()
