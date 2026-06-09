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

# ── 系列配置 ──
SERIES_CONFIG = {
    "enlightenment": {
        "name": ("Enlightenment", "启蒙运动"),
        "file_en": "enlightenment_22_episodes.txt",
        "file_zh": "enlightenment_22_episodes_zh.txt",
        "style_prompt": (
            "Monochrome European copperplate engraving, 18th century printmaking aesthetic, "
            "fine cross-hatching, high contrast black and white, antique paper texture, "
            "dramatic chiaroscuro lighting, European historical scene"
        ),
    },
    "american_revolution": {
        "name": ("American Revolution", "美国独立战争"),
        "file_en": "american_revolution_en.txt",
        "file_zh": "american_revolution_zh.txt",
        "bilingual_src": "american_revolution_script.txt",
        "style_prompt": (
            "Monochrome European copperplate engraving, 18th century printmaking aesthetic, "
            "fine cross-hatching, high contrast black and white, antique paper texture, "
            "colonial American historical scene, revolutionary war era"
        ),
    },
    "thirty_years_war": {
        "name": ("Thirty Years' War", "三十年战争"),
        "file_en": "thirty_years_war_en.txt",
        "file_zh": "thirty_years_war_zh.txt",
        "bilingual_src": "thirty_years_war_script.txt",
        "style_prompt": (
            "Monochrome baroque copperplate engraving, 17th century printmaking aesthetic, "
            "dark chiaroscuro, fine cross-hatching, high contrast black and white, "
            "antique paper texture, battlefield scenes, Holy Roman Empire setting"
        ),
    },
    "age_of_exploration": {
        "name": ("Age of Exploration", "大航海时代"),
        "file_en": "age_of_exploration_en.txt",
        "file_zh": "age_of_exploration_zh.txt",
        "bilingual_src": "age_of_exploration_script.txt",
        "style_prompt": (
            "Antique engraving and nautical chart style, 16th century portolan chart aesthetic, "
            "fine cross-hatching, high contrast black and white, aged parchment texture, "
            "European maritime exploration scene, Renaissance cartography"
        ),
    },
}

def _ensure_series_files(series_name):
    cfg = SERIES_CONFIG.get(series_name)
    if not cfg:
        return
    bilingual_src = cfg.get("bilingual_src")
    if not bilingual_src:
        return
    en_path = os.path.join(PROJECT_ROOT, cfg["file_en"])
    zh_path = os.path.join(PROJECT_ROOT, cfg["file_zh"])
    if os.path.exists(en_path) and os.path.exists(zh_path):
        return
    src_path = os.path.join(PROJECT_ROOT, bilingual_src)
    if not os.path.exists(src_path):
        log(f"  File not found: {bilingual_src}")
        return
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    CN = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
          "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八",
          "十九", "二十", "二十一", "二十二"]
    en_blocks, zh_blocks = [], []
    ep = 1
    while True:
        cn_cur = CN[ep] if ep < len(CN) else str(ep)
        cn_nxt = CN[ep + 1] if ep + 1 < len(CN) else str(ep + 1)
        pat_en = rf"Episode {ep}:(.+?)(?=\n\nEpisode {ep+1}:|\Z)"
        pat_zh = rf"第{cn_cur}集[：:](.+?)(?=\n\n第{cn_nxt}集[：:]|\Z)"
        m_en = re.search(pat_en, content, re.DOTALL)
        m_zh = re.search(pat_zh, content, re.DOTALL)
        if not m_en and not m_zh:
            break
        if m_en:
            en_block = m_en.group(0).strip()
            zh_in_en = re.search(rf"\n\n第{cn_cur}集[：:].*", en_block, re.DOTALL)
            if zh_in_en:
                en_block = en_block[:zh_in_en.start()].strip()
            en_blocks.append(en_block)
        if m_zh:
            zh_blocks.append(m_zh.group(0).strip())
        ep += 1
    with open(en_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(en_blocks))
    with open(zh_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(zh_blocks))
    log(f"  Split {bilingual_src} into EN/ZH files")

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

# ── 运行时由 --series 参数设置 ──
STYLE_PROMPT = ""
CURRENT_SERIES = "enlightenment"

# ── 样式 ──
VIDEO_W, VIDEO_H = 1024, 1792
FONT_EN = 40
FONT_CN = 36
T_DUR = 0.8
AUDIO_GAP = 0.8
TTS_BASE_DUR = 8.0


def _find_latest_images(count=3):
    candidates = []
    for root, dirs, files in os.walk(os.path.join(PROJECT_ROOT, "output")):
        for f in sorted(files):
            if f.endswith("_image.png"):
                candidates.append(os.path.join(root, f))
    if len(candidates) >= count:
        return sorted(candidates)[-count:]
    return [os.path.join(PROJECT_ROOT, "output", "default.png")] * count


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
                        f"Episode: {title_en}\nStyle: {STYLE_PROMPT}\n"
                        f"Output as JSON. Generate one unique image prompt per sentence (50-80 words each). "
                        f"18th century European, no Asian, no modern, no text.\n"
                        f"{items}"
                    )}],
                    temperature=0.7, max_tokens=4096, response_format={"type": "json_object"})
                data = json.loads(resp.choices[0].message.content)
                prompts = data if isinstance(data, list) else data.get("image_prompts", [])
                prompts = [p["prompt"] if isinstance(p, dict) and "prompt" in p else (p if isinstance(p, str) else str(p)) for p in prompts]
                if len(prompts) == len(en_sentences):
                    return prompts
    except Exception as e:
        log(f"  LLM failed: {e}")
    return [f"{STYLE_PROMPT}: {s[:200]}, no Chinese people, no Asian features, no modern elements, no text"
            for s in en_sentences]


def _auto_gen_images(ep, title_en, en_sentences, limit=0):
    n = min(len(en_sentences), limit) if limit > 0 else len(en_sentences)
    images = []
    for i in range(n):
        out = os.path.join(EP_IMG_DIR, f"ep{ep:02d}_{i:02d}_image.png")
        images.append(out)
    missing = [i for i, p in enumerate(images) if not os.path.exists(p)]
    if not missing:
        log(f"  {n} images ready")
        return images
    log(f"  ComfyUI: {len(missing)}/{n} new images...")
    try:
        requests.get(f"{COMFY}/queue", timeout=3)
    except:
        log("  ComfyUI not running, using existing/placeholder")
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
                log(f" {len(data)//1024}KB")
            else:
                log(" timeout")
        except Exception as e:
            log(f" {e}")
    return images


def _split_sents(text, lang="zh"):
    if lang == "zh":
        parts = [s.strip() for s in re.split(r'(?<=[。！？])', text) if s.strip()]
    else:
        parts = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    return parts if parts else [text]


def _match_sentences(en_text, zh_text):
    en_sents = _split_sents(en_text, "en")
    zh_sents = _split_sents(zh_text, "zh")
    n = min(len(en_sents), len(zh_sents))
    if n < 3:
        return [(en_text, zh_text)]
    return list(zip(en_sents[:n], zh_sents[:n]))


def _read_episode(ep_num: int, file_en=None, file_zh=None):
    def _read_file(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    en_content = _read_file(file_en or EPISODES_FILE_EN)
    zh_content = _read_file(file_zh or EPISODES_FILE_ZH)
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
            raise ValueError(f"Episode/ep{ep_num} not found")
        title = match.group(1).strip()
        body = match.group(2).strip()
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
        return title, paragraphs
    title_en, paras_en = _extract(en_content, is_zh=False)
    title_zh, paras_zh = _extract(zh_content, is_zh=True)
    n = min(len(paras_en), len(paras_zh))
    if n < max(len(paras_en), len(paras_zh)):
        log(f"  Mismatch (EN={len(paras_en)}, ZH={len(paras_zh)}), taking first {n}")
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
    print("  TTS(SSML)...", end=" ", flush=True)
    from pixelle_video.utils.tts_ssml import generate
    dur = generate(zh_text, out_path, cinematic=False)
    log(f"({dur:.1f}s)"); return dur


def gen_audio_fallback(out_path, dur=TTS_BASE_DUR):
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                   f"anullsrc=r=24000:cl=mono", "-t", f"{dur}", out_path],
                  capture_output=True)
    return dur


def render_green(w, h, en, cn):
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
        log(f" import failed"); raise
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
    parser = argparse.ArgumentParser(description="SSML + Bilingual Subtitles")
    parser.add_argument("--episode", type=int, default=0, help="Episode number")
    parser.add_argument("--all", action="store_true", help="Process all sentences")
    parser.add_argument("--series", type=str, default="enlightenment",
                        choices=list(SERIES_CONFIG.keys()),
                        help="Series name")
    args = parser.parse_args()

    global STYLE_PROMPT, CURRENT_SERIES, EPISODES_FILE_EN, EPISODES_FILE_ZH
    cfg = SERIES_CONFIG.get(args.series, SERIES_CONFIG["enlightenment"])
    STYLE_PROMPT = cfg["style_prompt"]
    CURRENT_SERIES = args.series
    file_en = os.path.join(PROJECT_ROOT, cfg["file_en"])
    file_zh = os.path.join(PROJECT_ROOT, cfg["file_zh"])
    EPISODES_FILE_EN = file_en
    EPISODES_FILE_ZH = file_zh
    _ensure_series_files(args.series)
    series_name = cfg["name"]

    log("=" * 60)
    log(f"  {series_name[0]} — Edge-TTS + Subtitles")
    log("=" * 60)

    if args.episode > 0:
        title_en, title_zh, paras_en, paras_zh = _read_episode(args.episode, file_en, file_zh)
        log(f"  {series_name[1]}: {title_en} / {title_zh}")
        all_pairs = []
        for en_para, zh_para in zip(paras_en, paras_zh):
            all_pairs.extend(_match_sentences(en_para, zh_para))
        total_sents = len(all_pairs)
        if not args.all:
            all_pairs = all_pairs[:3]
            log(f"  First 3 sentences (total {total_sents})")
        else:
            log(f"  All {total_sents} sentences")
        en_sentences = [p[0] for p in all_pairs]
        IMAGES = _auto_gen_images(args.episode, title_en, en_sentences)
        motions = ["horizontal", "zoom", "orbital", "horizontal", "zoom", "orbital"]
        SENTENCES = []
        for i, (en_s, zh_s) in enumerate(all_pairs):
            motion = motions[i % len(motions)]
            SENTENCES.append((motion, en_s, zh_s))
    else:
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

    log(f"  {len(SENTENCES)} sentences | {len(IMAGES)} images")

    if not shutil.which("depthflow"):
        log("need depthflow: pip install depthflow"); sys.exit(1)

    td = tempfile.mkdtemp(prefix="v7_", dir=OUTPUT_DIR)
    bg_clips = []
    sub_overlays = []
    segment_durs = []
    audio_files = []
    audio_durs = []

    for i, (motion, en, cn) in enumerate(SENTENCES):
        log(f"\n[{motion}] sentence {i+1}")
        img = IMAGES[i] if i < len(IMAGES) else IMAGES[-1]
        log(f"  image: {os.path.basename(img)}")
        log(f"  cn: {cn[:50]}...")
        ap = os.path.join(td, f"a{i}.wav")
        dur = gen_audio_ssml(cn, ap)
        bg = os.path.join(td, f"bg{i}.mp4")
        print("  DepthFlow...", end=" ", flush=True)
        r = subprocess.run(["depthflow", "input", "-i", img, "da2", motion, "main",
                           "--speed", "0.5",
                           "-o", bg, "--time", f"{dur:.1f}",
                           "-w", str(VIDEO_W), "-h", str(VIDEO_H)],
                          capture_output=True, text=True, timeout=300,
                          env={**os.environ, "PATH": f"{CONDA_BIN}:{os.environ.get('PATH', '')}"})
        if r.returncode != 0:
            log("DepthFlow failed, fallback to static")
            subprocess.run([FFMPEG, "-y", "-loop", "1", "-framerate", "30",
                           "-t", f"{dur:.1f}", "-i", img,
                           "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-preset", "fast", "-crf", "20", bg],
                          check=True, capture_output=True)
        else:
            log("ok")
        grn = os.path.join(td, f"g{i}.png")
        render_green(VIDEO_W, VIDEO_H, en, cn).save(grn)
        bg_clips.append(bg)
        sub_overlays.append((grn, dur))
        segment_durs.append(dur)
        audio_files.append(ap)
        audio_durs.append(dur)

    n = len(bg_clips)
    for i in range(1, n):
        pre = os.path.join(td, f"bg_pre{i}.mp4")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", bg_clips[i],
                       "-vf", f"tpad=start_mode=clone:start_duration={T_DUR}",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-preset", "fast", "-crf", "20", pre],
                      check=True, capture_output=True)
        bg_clips[i] = pre
    for i in range(n - 1):
        ext = os.path.join(td, f"bg_ext{i}.mp4")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", bg_clips[i],
                       "-vf", f"tpad=stop_mode=clone:stop_duration={AUDIO_GAP}",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-preset", "fast", "-crf", "20", ext],
                      check=True, capture_output=True)
        bg_clips[i] = ext
    seg_original = list(segment_durs)
    for i in range(n - 1):
        segment_durs[i] += AUDIO_GAP
    for i in range(1, n):
        segment_durs[i] += T_DUR

    sub_timeline = []
    cum_a = 0.0
    for i, d in enumerate(seg_original):
        sub_timeline.append((cum_a, cum_a + d))
        cum_a += d + AUDIO_GAP
    final_audio_dur = cum_a - AUDIO_GAP

    log(f"\nSynthesizing background (xfade)...")
    bg_concat = os.path.join(td, "bg_concat.mp4")
    if n == 1:
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
            off = cc - T_DUR
            fps.append(f"[{pv}][{cv}]xfade=transition=fade:duration={T_DUR:.1f}:offset={off:.3f},format=yuv420p[{ol}]")
            pv = ol
            cc = cc - T_DUR + segment_durs[i]
        cmd.extend(["-filter_complex", ";".join(fps), "-map", f"[{pv}]"])