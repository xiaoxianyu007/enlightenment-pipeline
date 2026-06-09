#!/usr/bin/env python3
"""
逐句生成拿破仑纪录片配图（ComfyUI）。
用法:
    python generate_episode_images.py              # 全部 22 集逐句
    python generate_episode_images.py --ep 1       # 只第 1 集
    python generate_episode_images.py --ep 8 12    # 第 8-12 集
    python generate_episode_images.py --limit 5    # 每集最多 5 句（测试用）
"""

# ── 清理 socks 代理（httpx 不兼容 socks:// 协议） ──
import os as _os
for _k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
           "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
    _os.environ.pop(_k, None)

import os, re, json, time, requests, sys, argparse
from openai import OpenAI

PROJECT = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(PROJECT, "output", "episode_images")
os.makedirs(OUTPUT, exist_ok=True)

COMFY = "http://127.0.0.1:8188"
WORKFLOW_PATH = os.path.join(PROJECT, "workflows/selfhost/image_flux.json")
SCRIPTS_ZH = os.path.join(PROJECT, "enlightenment_22_episodes_zh.txt")
SCRIPTS_EN = os.path.join(PROJECT, "enlightenment_22_episodes.txt")

COPPERPLATE_STYLE = (
    "Monochrome European copperplate engraving, 18th century printmaking aesthetic, "
    "fine cross-hatching, high contrast black and white, antique paper texture, "
    "dramatic chiaroscuro lighting, European historical scene"
)

CN_NUM = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
          "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
          "二十一", "二十二"]


def _read_episode(ep):
    with open(SCRIPTS_EN) as f: en = f.read()
    with open(SCRIPTS_ZH) as f: zh = f.read()
    m_en = re.search(rf"Episode {ep}:(.+?)\n\n(.*?)(?=\n\nEpisode {ep+1}:|\Z)", en, re.DOTALL)
    m_zh = re.search(rf"第{CN_NUM[ep]}集[：:](.+?)\n\n(.*?)(?=\n\n第.+?集[：:]|\Z)", zh, re.DOTALL)
    if not m_en or not m_zh:
        return None, [], []
    title_en = m_en.group(1).strip()
    title_zh = m_zh.group(1).strip()
    en_body = m_en.group(2).strip()
    zh_body = m_zh.group(2).strip()
    return title_en, _split_sents(en_body), _split_sents(zh_body)


def _split_sents(text):
    parts = [s.strip() for s in re.split(r'(?<=[.!?。！？])\s*', text) if s.strip()]
    return parts if parts else [text]


def _get_llm_client():
    config_path = os.path.join(PROJECT, "config.yaml")
    if not os.path.exists(config_path):
        return None
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    llm_cfg = cfg.get("llm", {})
    if not llm_cfg.get("api_key") or not llm_cfg.get("base_url"):
        return None
    return OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"]), llm_cfg.get("model", "gpt-4o")


def _build_prompts_batch(title_en, en_sentences, limit=None):
    sents = en_sentences[:limit] if limit else en_sentences
    client_tup = _get_llm_client()
    if client_tup:
        client, model = client_tup
        try:
            items = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sents))
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": (
                    f"Episode theme: {title_en}\n\n"
                    f"Generate one English image prompt per sentence below. "
                    f"Style: {COPPERPLATE_STYLE}\n"
                    f"Rules: each prompt must describe a unique visual scene matching its sentence. "
                    f"50-80 words each. 18th century European, no Chinese/Asian, no modern, no text.\n\n"
                    f"Sentences:\n{items}\n\n"
                    f"Output JSON: {{\"image_prompts\": [\"prompt1\", \"prompt2\", ...]}}"
                )}],
                temperature=0.7, max_tokens=4096, response_format={"type": "json_object"}
            )
            data = json.loads(resp.choices[0].message.content)
            prompts = data.get("image_prompts", [])
            if len(prompts) == len(sents):
                return prompts
            print(f"  LLM returned {len(prompts)} prompts, expected {len(sents)}, using fallback")
        except Exception as e:
            print(f"  LLM failed: {e}")
    return [f"{COPPERPLATE_STYLE}: {s[:200]}, no Chinese people, no Asian features, no modern elements, no text"
            for s in sents]


def _load_workflow():
    with open(WORKFLOW_PATH) as f:
        return json.load(f)


def _find_by_class(wf, class_type):
    for nid, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return nid
    return None


def _queue_prompt(prompt_text, seed):
    wf = _load_workflow()
    pn = _find_by_class(wf, "CLIPTextEncode")
    sn = _find_by_class(wf, "KSampler")
    if pn:
        wf[pn]["inputs"]["text"] = prompt_text
    if sn:
        wf[sn]["inputs"]["seed"] = seed
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def _wait_download(prompt_id, output_path):
    for _ in range(600):
        time.sleep(2)
        r = requests.get(f"{COMFY}/history/{prompt_id}", timeout=10)
        if r.status_code == 200 and prompt_id in r.json():
            history = r.json()
            for no in history[prompt_id].get("outputs", {}).values():
                for img in no.get("images", []):
                    dl = requests.get(f"{COMFY}/view", params={
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output")
                    }, timeout=60)
                    with open(output_path, "wb") as f:
                        f.write(dl.content)
                    return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ep", type=int, nargs="+")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.ep:
        episodes = [args.ep[0]] if len(args.ep) == 1 else list(range(args.ep[0], args.ep[-1] + 1))
    else:
        episodes = list(range(1, 23))
    try:
        requests.get(f"{COMFY}/queue", timeout=3)
        print("✓ ComfyUI connected\n")
    except:
        print("✗ ComfyUI not running")
        sys.exit(1)
    for ep in episodes:
        title_en, en_sents, zh_sents = _read_episode(ep)
        if not title_en:
            print(f"[{ep}/22] Skip: no data")
            continue
        n = min(len(en_sents), len(zh_sents))
        limit = args.limit if args.limit > 0 else n
        limit = min(limit, n)
        print(f"\n[{ep}/22] {title_en}")
        prompts = _build_prompts_batch(title_en, en_sents, limit)
        skipped = 0
        for i, prompt in enumerate(prompts):
            out = os.path.join(OUTPUT, f"ep{ep:02d}_{i:02d}_image.png")
            if os.path.exists(out):
                skipped += 1
                continue
            print(f"  [{i+1}/{limit}] {prompt[:80]}...", end=" ", flush=True)
            try:
                pid = _queue_prompt(prompt, seed=42 + ep * 100 + i)
                ok = _wait_download(pid, out)
                if ok:
                    print(f"✓ {os.path.getsize(out)/1024:.0f}KB")
                else:
                    print("✗ timeout")
            except Exception as e:
                print(f"✗ {e}")
        if skipped:
            print(f"  Skipped {skipped} existing")
    print(f"\nImage directory: {OUTPUT}")


if __name__ == "__main__":
    main()
