#!/bin/bash
# ═══════════════════════════════════════════════════
# 测试脚本：单集 3 句，验证完整管线
# 用法: bash test_pipeline.sh
# ═══════════════════════════════════════════════════

set -e
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy NO_PROXY no_proxy

cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
info() { echo -e "${CYAN}[INFO]${NC} $*"; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Documentary Pipeline Test              ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Step 1: ComfyUI
info "Step 1/2: Checking ComfyUI ..."
curl -sf --connect-timeout 3 http://127.0.0.1:8188/prompt > /dev/null && pass "ComfyUI online" || fail "ComfyUI not running at http://127.0.0.1:8188"

# Step 2: Run full pipeline (episode 8, first 3 sentences)
info "Step 2/2: Episode 8 test (3 sentences) ..."
echo ""
$PYTHON demo_parallax_subtitle.py --episode 8

OUT="output/demo_v7/demo_final_ep8.mp4"
if [ -f "$OUT" ]; then
    SZ=$(du -h "$OUT" | cut -f1)
    echo ""
    pass "Video: ${OUT} (${SZ})"
else
    fail "Output not found: ${OUT}"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ✓ Test passed!                         ║"
echo "║  Batch: PYTHON=$PYTHON bash batch_generate.sh  ║"
echo "╚══════════════════════════════════════════╝"
echo ""
