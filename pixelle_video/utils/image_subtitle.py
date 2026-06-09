"""
竖屏短视频双语居中字幕合成工具

核心功能：读取分帧图片、英文旁白，自动翻译为中文，
在画面垂直居中位置绘制带阴影、半透明圆角背景的中英双语字幕，
匹配对应帧音频，批量合成高清 MP4 视频。

用法:
    python pixelle_video/utils/image_subtitle.py <video_folder_path>
    python pixelle_video/utils/image_subtitle.py ALL    # 处理 output/ 下所有视频


核心设计原则：中英文绘制在同一张 PIL Image 上（同一画布、同一原点、同一居中算法）
→ 保证两者永远完美对齐，与翻译质量无关。
"""

import json
import os
import sys
import glob as glob_mod
import shutil
from PIL import Image, ImageDraw, ImageFont
import warnings
warnings.filterwarnings("ignore")

# ==================== 全局配置 ====================

# 字体路径
EN_FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
CN_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
CN_FONT_REGULAR_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# 字体大小
EN_FONT_SIZE = 30
CN_FONT_SIZE = 28

# 视频与样式参数
VIDEO_FPS = 30
TEXT_COLOR = (255, 255, 255)
BG_ALPHA = 0.8
CORNER_RADIUS = 10
SIDE_PADDING = 40
LINE_SPACING_EN = 4
LINE_SPACING_CN = 2
GAP_BETWEEN_LANGS = 8
MAX_EN_LINES = 2
MAX_CN_LINES = 2

# 字幕位置: "center" 或 "upper_quarter"
SUBTITLE_VERTICAL_POSITION = "center"

# ==================== 翻译缓存机制 ====================

TRANSLATION_CACHE = {}
TRANSLATION_CACHE_FILE = os.path.join(os.path.dirname(__file__), "_translation_cache.json")
if os.path.exists(TRANSLATION_CACHE_FILE):
    try:
        with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
            TRANSLATION_CACHE = json.load(f)
    except Exception:
        TRANSLATION_CACHE = {}

_translator = None


def get_translator():
    global _translator
    try:
        if _translator is None:
            from deep_translator import GoogleTranslator
            _translator = GoogleTranslator(source="en", target="zh-CN")
    except Exception:
        _translator = None
    return _translator


def translate_text(text):
    if not text.strip():
        return text
    key = text.strip()
    if key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[key]
    tr = get_translator()
    if tr is None:
        return text
    try:
        result = tr.translate(key)
        TRANSLATION_CACHE[key] = result
        return result
    except Exception as e:
        print(f"    [WARN] Translation failed: {e}")
        return text


def save_translation_cache():
    try:
        with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(TRANSLATION_CACHE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==================== 图片路径查找工具 ====================

def get_local_image_path(frames_dir, frame_data):
    remote_path = frame_data.get("image_path", "")
    if remote_path:
        basename = os.path.basename(remote_path)
        local_path = os.path.join(frames_dir, basename)
        if os.path.exists(local_path):
            return local_path
    idx = frame_data["index"]
    for pattern in [f"{idx+1:02d}_image.png", f"sub{idx+1:02d}_image.png",
                    f"{idx+1}_image.png", f"frame_{idx+1}.png",
                    f"{idx+1:04d}.png", f"image_{idx+1}.png"]:
        local_path = os.path.join(frames_dir, pattern)
        if os.path.exists(local_path):
            return local_path
    matches = sorted(glob_mod.glob(os.path.join(frames_dir, "*.png")) +
                     glob_mod.glob(os.path.join(frames_dir, "*.jpg")))
    if idx < len(matches):
        return matches[idx]
    return None


# ==================== 智能自动换行函数 ====================

def word_wrap_lines(text, font, max_width, draw, max_lines):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = test
        else:
            if current:
                if len(lines) < max_lines - 1:
                    lines.append(current)
                elif len(lines) == max_lines - 1:
                    truncated = current
                    while truncated:
                        test_t = truncated[:-1] + "..."
                        tb = draw.textbbox((0, 0), test_t, font=font)
                        if (tb[2] - tb[0]) <= max_width:
                            lines.append(test_t)
                            current = ""
                            break
                        truncated = truncated[:-1]
            current = word
    if current:
        if len(lines) < max_lines:
            lines.append(current)
        else:
            truncated = current
            while truncated:
                test_t = truncated[:-1] + "..."
                tb = draw.textbbox((0, 0), test_t, font=font)
                if (tb[2] - tb[0]) <= max_width:
                    lines[-1] = test_t
                    break
                truncated = truncated[:-1]
    if not lines:
        lines = [text[:30] + ("..." if len(text) > 30 else "")]
    return lines


def char_wrap_lines(text, font, max_width, draw, max_lines):
    if not text:
        return [""]
    lines = []
    current = ""
    for char in text:
        test = current + char
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = test
        else:
            if current:
                if len(lines) < max_lines - 1:
                    lines.append(current)
                    current = char
                else:
                    truncated = current
                    while truncated:
                        test_t = truncated[:-1] + "..."
                        tb = draw.textbbox((0, 0), test_t, font=font)
                        if (tb[2] - tb[0]) <= max_width:
                            lines.append(test_t)
                            current = char
                            break
                        truncated = truncated[:-1]
            else:
                current = char
    if current and len(lines) < max_lines:
        lines.append(current)
    elif current:
        truncated = current
        while truncated:
            test_t = truncated[:-1] + "..."
            tb = draw.textbbox((0, 0), test_t, font=font)
            if (tb[2] - tb[0]) <= max_width:
                lines[-1] = test_t
                break
            truncated = truncated[:-1]
    return lines if lines else [""]


# ==================== 核心字幕绘制函数 ====================

def create_subtitle_image(
    image_path,
    en_text,
    cn_text,
    output_path,
    en_font_path=EN_FONT_PATH,
    cn_font_path=CN_FONT_PATH,
    en_font_size=EN_FONT_SIZE,
    cn_font_size=CN_FONT_SIZE,
    text_color=TEXT_COLOR,
    bg_alpha=BG_ALPHA,
    corner_radius=CORNER_RADIUS,
    side_padding=SIDE_PADDING,
    line_spacing_en=LINE_SPACING_EN,
    line_spacing_cn=LINE_SPACING_CN,
    gap_between_langs=GAP_BETWEEN_LANGS,
    max_en_lines=MAX_EN_LINES,
    max_cn_lines=MAX_CN_LINES,
    vertical_position=SUBTITLE_VERTICAL_POSITION,
    output_size=None,
):
    if not os.path.exists(image_path):
        print(f"    [ERROR] Image not found: {image_path}")
        return None
    img = Image.open(image_path).convert("RGBA")
    img_w, img_h = img.size
    if output_size:
        target_w, target_h = output_size
        if img_w != target_w or img_h != target_h:
            img_ratio = img_w / img_h
            target_ratio = target_w / target_h
            if img_ratio > target_ratio:
                new_h = target_h
                new_w = int(target_h * img_ratio)
            else:
                new_w = target_w
                new_h = int(target_w / img_ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            img = img.crop((left, top, left + target_w, top + target_h))
            img_w, img_h = target_w, target_h
    try:
        en_font = ImageFont.truetype(en_font_path, en_font_size)
    except Exception:
        en_font = ImageFont.load_default()
    try:
        cn_font = ImageFont.truetype(cn_font_path, cn_font_size)
    except Exception:
        cn_font = ImageFont.load_default()
    draw = ImageDraw.Draw(img)
    max_text_w = img_w - 2 * side_padding
    en_lines = word_wrap_lines(en_text, en_font, max_text_w, draw, max_en_lines)
    cn_lines = char_wrap_lines(cn_text, cn_font, max_text_w, draw, max_cn_lines)
    en_line_hs = []
    for line in en_lines:
        bbox = draw.textbbox((0, 0), line, font=en_font)
        en_line_hs.append(bbox[3] - bbox[1])
    cn_line_hs = []
    for line in cn_lines:
        bbox = draw.textbbox((0, 0), line, font=cn_font)
        cn_line_hs.append(bbox[3] - bbox[1])
    en_block_h = sum(en_line_hs) + max(0, len(en_lines) - 1) * line_spacing_en
    cn_block_h = sum(cn_line_hs) + max(0, len(cn_lines) - 1) * line_spacing_cn
    total_text_h = en_block_h + cn_block_h + gap_between_langs
    max_line_w = 0
    for line in en_lines:
        bbox = draw.textbbox((0, 0), line, font=en_font)
        max_line_w = max(max_line_w, bbox[2] - bbox[0])
    for line in cn_lines:
        bbox = draw.textbbox((0, 0), line, font=cn_font)
        max_line_w = max(max_line_w, bbox[2] - bbox[0])
    bg_pad_h = 14
    bg_pad_w = 24
    bg_w = max_line_w + 2 * bg_pad_w
    bg_h = total_text_h + 2 * bg_pad_h
    bg_x = (img_w - bg_w) // 2
    if vertical_position == "upper_quarter":
        bg_y = (img_h // 4) - (bg_h // 2)
        bg_y = max(10, bg_y)
    else:
        bg_y = (img_h - bg_h) // 2
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        (bg_x, bg_y, bg_x + bg_w, bg_y + bg_h),
        radius=corner_radius,
        fill=(0, 0, 0, int(255 * bg_alpha)),
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    shadow_offset = 2
    shadow_color = (0, 0, 0)
    text_x = bg_x + bg_pad_w
    text_y = bg_y + bg_pad_h
    for i, line in enumerate(en_lines):
        bbox = draw.textbbox((0, 0), line, font=en_font)
        lw = bbox[2] - bbox[0]
        lx = text_x + (max_line_w - lw) // 2
        ly = text_y
        draw.text((lx + shadow_offset, ly + shadow_offset), line, font=en_font, fill=shadow_color)
        draw.text((lx + shadow_offset, ly), line, font=en_font, fill=shadow_color)
        draw.text((lx, ly), line, font=en_font, fill=text_color)
        text_y += en_line_hs[i] + line_spacing_en
    text_y += gap_between_langs - line_spacing_en
    for i, line in enumerate(cn_lines):
        bbox = draw.textbbox((0, 0), line, font=cn_font)
        lw = bbox[2] - bbox[0]
        lx = text_x + (max_line_w - lw) // 2
        ly = text_y
        draw.text((lx + shadow_offset, ly + shadow_offset), line, font=cn_font, fill=shadow_color)
        draw.text((lx + shadow_offset, ly), line, font=cn_font, fill=shadow_color)
        draw.text((lx, ly), line, font=cn_font, fill=text_color)
        text_y += cn_line_hs[i] + line_spacing_cn
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[3])
    bg.save(output_path, quality=95)
    return output_path


def render_bilingual_subtitle(image_path, en_text, cn_text, output_path, side_padding=30):
    return create_subtitle_image(image_path, en_text, cn_text, output_path, side_padding=side_padding)
