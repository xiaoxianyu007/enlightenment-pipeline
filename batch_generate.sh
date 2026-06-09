#!/bin/bash
# ═══════════════════════════════════════════════════
# 批量生成全部 22 集纪录片
# 用法: bash batch_generate.sh
# 前置: ComfyUI 运行中 (127.0.0.1:8188)
# ═══════════════════════════════════════════════════

unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy NO_PROXY no_proxy

cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"

echo "╔══════════════════════════════════╗"
echo "║  Documentary 22-Episode Batch    ║"
echo "╚══════════════════════════════════╝"
echo ""

FAILED=""
for ep in $(seq 1 22); do
    echo "==================== Episode ${ep}/22 ===================="
    $PYTHON demo_parallax_subtitle.py --episode $ep --all
    if [ $? -ne 0 ]; then
        echo "✗ Episode ${ep} failed"
        FAILED="$FAILED $ep"
    else
        echo "✓ Episode ${ep} done"
    fi
    echo ""
done

echo "════════════════════════════════════"
if [ -z "$FAILED" ]; then
    echo "✓ All 22 episodes generated!"
else
    echo "△ Failed: episodes ${FAILED}"
fi
echo "Output: output/demo_v7/"
ls -lh output/demo_v7/demo_final_ep*.mp4 2>/dev/null
