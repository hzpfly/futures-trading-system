#!/bin/bash
# Tick 数据维护：合并 → 清理
# 用法: ./maintain_and_clean.sh [--age N] [--product 品种名]
# 默认: 合并并清理 30 天前的数据

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGE="${1:-30}"  # 默认30天
PRODUCT="${2}"

echo "=========================================="
echo "  Tick 数据维护 — merge + clean"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  归档天数: ${AGE}"
[ -n "$PRODUCT" ] && echo "  品种: ${PRODUCT}"
echo "=========================================="

PYTHON="/Users/michaelhe/.workbuddy/binaries/python/envs/default/bin/python"
MAINTAIN="$SCRIPT_DIR/maintain_data.py"

MERGE_ARGS="--age $AGE"
CLEAN_ARGS="--age $AGE"
[ -n "$PRODUCT" ] && MERGE_ARGS="$MERGE_ARGS --product $PRODUCT"
[ -n "$PRODUCT" ] && CLEAN_ARGS="$CLEAN_ARGS --product $PRODUCT"

# Step 1: Merge
echo ""
echo ">>> Step 1/2: 合并每日文件 → 月度归档"
$PYTHON "$MAINTAIN" merge $MERGE_ARGS

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] merge 失败，终止"
    exit 1
fi

# Step 2: Clean
echo ""
echo ">>> Step 2/2: 清理已归档的原始每日文件"
$PYTHON "$MAINTAIN" clean $CLEAN_ARGS

echo ""
echo "=========================================="
echo "  维护完成: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
