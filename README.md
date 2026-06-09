# Enlightenment Documentary Pipeline

AI-powered historical documentary video generator. Input bilingual (EN/ZH) episode scripts, automatically generate images, narration, parallax effects, bilingual subtitles, and compose the final video — all through one script.

## Pipeline Overview

```
Episode Script (EN+ZH)
       │
       ▼
  Sentence Alignment ──► ComfyUI Image Gen (Flux, copperplate style)
       │
       ▼
  For each sentence (in parallel):
    ├── Edge-TTS Narration (.wav)
    ├── DepthFlow Parallax Video (horizontal/zoom/orbital)
    └── PIL Bilingual Subtitle (green screen)
       │
       ▼
  Background: xfade chain transitions (0.8s fade)
  Subtitle:   chromakey overlay, timed to audio
  Audio:      concat with 0.5s silence gaps
       │
       ▼
  FFmpeg merge → output/demo_v7/demo_final_ep{N}.mp4
```

## Prerequisites

| Dependency | Version / Notes |
|------------|----------------|
| Python     | >= 3.11 |
| FFmpeg     | Must be in `PATH` (test with `ffmpeg -version`) |
| ComfyUI    | Running at `http://127.0.0.1:8188` with Flux model |
| DepthFlow  | `pip install depthflow` (optional, falls back to static image) |
| Fonts      | Noto Sans CJK (Chinese) + FreeSans Bold (English) |

### Font Installation (Linux)

```bash
# Noto Sans CJK (Chinese)
sudo apt install fonts-noto-cjk

# FreeSans (English)
sudo apt install fonts-freefont-ttf
```

On macOS, adjust font paths in `demo_parallax_subtitle.py`:
- `EN_FONT`: path to a bold English font
- `CN_FONT`: path to a Chinese font (e.g., `/System/Library/Fonts/PingFang.ttc`)

## Quick Start

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Copy and edit config (for LLM-based image prompt generation)
cp config.example.yaml config.yaml
# Fill in your LLM API key/base_url/model
# (Optional - without LLM, falls back to template prompts)

# 3. Prepare episode scripts
# Create two files in the project root:
#   enlightenment_22_episodes.txt    (English)
#   enlightenment_22_episodes_zh.txt (Chinese)
# Format: "Episode N: Title\n\nBody paragraphs..."
# (See example section below for format)

# 4. Test with a single episode (first 3 sentences)
python3 demo_parallax_subtitle.py --episode 1

# 5. Full episode
python3 demo_parallax_subtitle.py --episode 1 --all

# 6. Batch all episodes
PYTHON=python3 bash batch_generate.sh
```

## Usage

```bash
python3 demo_parallax_subtitle.py                    # Test mode (default 3 sentences)
python3 demo_parallax_subtitle.py --episode 1        # Episode 1, first 3 sentences
python3 demo_parallax_subtitle.py --episode 1 --all  # Episode 1, all sentences
```

### Standalone Image Generation

```bash
python3 generate_episode_images.py                   # All 22 episodes
python3 generate_episode_images.py --ep 1            # Only episode 1
python3 generate_episode_images.py --ep 8 12         # Episodes 8-12
python3 generate_episode_images.py --limit 5         # Max 5 sentences per ep
```

## Episode Script Format

### English (`enlightenment_22_episodes.txt`)

```
Episode 1: The Dawn of Enlightenment

In the late 17th and early 18th centuries, Europe was undergoing a revolution unlike any before. Not a revolution of guns and cannons, but a revolution of ideas.

In the elegant salons of Paris and the bustling coffeehouses of London, a small group of bold thinkers gathered to question everything.

Episode 2: The Rise of the Philosophes

...
```

### Chinese (`enlightenment_22_episodes_zh.txt`)

```
第一集：启蒙运动的曙光

17世纪末18世纪初，欧洲正经历着一场前所未有的革命。这不是枪炮的革命，而是思想的革命。

在巴黎的优雅沙龙和伦敦的喧嚣咖啡馆里，一小群勇敢的思想家聚集起来，质疑一切。

第二集：哲学家的崛起

...
```

## Output Structure

```
output/
├── episode_images/          ← ComfyUI generated images
│   ├── ep01_00_image.png
│   ├── ep01_01_image.png
│   └── ...
└── demo_v7/
    ├── demo_final_ep1.mp4   ← Final video episodes
    ├── demo_final_ep2.mp4
    └── ...
```

## Configuration (`config.example.yaml`)

### LLM (for image prompt generation)

| Provider | Base URL | Model |
|----------|----------|-------|
| Qwen    | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max` |
| OpenAI  | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| Ollama   | `http://localhost:11434/v1` | `llama3.2` |

Without LLM config, the script falls back to template-based prompts.

## Video Parameters

| Parameter | File | Line | Default |
|-----------|------|------|---------|
| Video size | `demo_parallax_subtitle.py` | 51 | 1024×1792 (portrait) |
| Crossfade duration | `demo_parallax_subtitle.py` | 54 | 0.8s |
| Audio gap between sentences | `demo_parallax_subtitle.py` | 55 | 0.8s |
| TTS voice | `pixelle_video/utils/tts_ssml.py` | 13 | zh-CN-YunyangNeural |
| Image style prompt | `demo_parallax_subtitle.py` | 44-48 | 18th c. copperplate engraving |

## Files

```
enlightenment-pipeline/
├── demo_parallax_subtitle.py      # Main pipeline script
├── batch_generate.sh              # Batch 22-episode generation
├── test_pipeline.sh               # Pipeline test script
├── generate_episode_images.py     # Standalone image generation
├── config.example.yaml            # Configuration template
├── requirements.txt               # Python dependencies
├── pixelle_video/utils/
│   ├── __init__.py
│   ├── tts_ssml.py               # Edge-TTS narration generation
│   ├── image_subtitle.py         # PIL-based bilingual subtitle rendering
│   └── srt_util.py              # Sentence splitting utilities
├── workflows/selfhost/
│   └── image_flux.json           # ComfyUI Flux workflow
├── .gitignore
└── README.md
```

## License

Apache 2.0
