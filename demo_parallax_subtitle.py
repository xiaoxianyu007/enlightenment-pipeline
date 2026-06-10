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
    "qin_empire": {
        "name": ("Qin Empire", "大秦帝国"),
        "file_en": "qin_empire_en.txt",
        "file_zh": "qin_empire_zh.txt",
        "bilingual_src": "qin_empire_script.txt",
        "style_prompt": (
            "Han dynasty stone relief style, bold silhouette lines, deep black ink on aged rice paper, "
            "traditional Chinese ink painting aesthetic, strong contrast black and white, "
            "ancient Chinese historical scene, solemn composition"
        ),
    },
}