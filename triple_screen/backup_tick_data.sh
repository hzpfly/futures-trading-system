#!/bin/bash
# 每日 tick + kline 数据备份 → GitHub Releases
# 用法:
#   ./backup_tick_data.sh                    # 备份今天（含 kline 重采样），上传 GitHub
#   ./backup_tick_data.sh 2026-06-23         # 备份指定日期
#   ./backup_tick_data.sh --local            # 只打包不上传
#   ./backup_tick_data.sh --no-kline         # 跳过 kline 重采样（只备份 tick）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$REPO_DIR/data/ticks"
KLINE_DIR="$REPO_DIR/data/klines"
BACKUP_DIR="$REPO_DIR/backups"

DATE="${1:-}"
UPLOAD=true
DO_KLINE=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) UPLOAD=false; shift ;;
        --no-kline) DO_KLINE=false; shift ;;
        *)
            if [[ "$1" != --* ]]; then
                DATE="$1"
            fi
            shift
            ;;
    esac
done

if [ -z "$DATE" ]; then
    DATE=$(date +%Y-%m-%d)
fi

ARCHIVE_NAME="tick_${DATE}.tar.gz"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"

# Python path
PYTHON="/Users/michaelhe/.workbuddy/binaries/python/envs/default/bin/python3"

echo "========================================"
echo "  Tick + K线 数据备份"
echo "  日期: $DATE"
echo "========================================"

# 0. K线重采样
if [ "$DO_KLINE" = true ]; then
    echo ""
    echo "[0/3] Tick → K线 重采样..."
    $PYTHON "$SCRIPT_DIR/resample_klines.py" "$DATE" 2>&1 | grep -E "K线生成汇总|1min|5min|15min|60min|day|week|输出目录"
fi

# 1. 创建备份目录
mkdir -p "$BACKUP_DIR"

# 2. 查找该日期所有 parquet 文件（tick + kline）
echo ""
echo "[1/3] 扫描文件..."
TICK_COUNT=$(find "$REPO_DIR/data/ticks" -name "${DATE}_*.parquet" 2>/dev/null | wc -l | tr -d ' ')

if [ "$TICK_COUNT" -eq 0 ]; then
    echo "  ❌ 未找到 ${DATE} 的 tick 数据文件"
    exit 1
fi

# 统计 K 线
KL_COUNT=$(find "$KLINE_DIR" -name "*.parquet" 2>/dev/null | wc -l | tr -d ' ')
KL_SIZE=$(du -sh "$KLINE_DIR" 2>/dev/null | awk '{print $1}')

TICK_SIZE=$(find "$REPO_DIR/data/ticks" -name "${DATE}_*.parquet" 2>/dev/null | xargs du -ch 2>/dev/null | tail -1 | awk '{print $1}')
echo "  Tick: ${TICK_COUNT} 文件, ${TICK_SIZE}"
echo "  K线:  ${KL_COUNT} 文件, ${KL_SIZE}"

# 3. 压缩：tick + kline 一起打包
echo ""
echo "[2/3] 压缩中..."

# 创建临时文件列表
FILELIST=$(mktemp)
cd "$REPO_DIR"
find data/ticks -name "${DATE}_*.parquet" 2>/dev/null > "$FILELIST"
find data/klines -name "*.parquet" 2>/dev/null >> "$FILELIST"

TOTAL_FILES=$(wc -l < "$FILELIST" | tr -d ' ')
tar -czf "$ARCHIVE_PATH" -T "$FILELIST" 2>/dev/null
rm -f "$FILELIST"

ARCHIVE_SIZE=$(ls -lh "$ARCHIVE_PATH" | awk '{print $5}')
echo "  ✅ $ARCHIVE_NAME ($ARCHIVE_SIZE) 共 ${TOTAL_FILES} 文件"

# 4. 上传到 GitHub Release
if [ "$UPLOAD" = true ]; then
    echo ""
    echo "[3/3] 上传到 GitHub..."

    TAG="tick-data-${DATE}"
    RELEASE_TITLE="Tick + K线数据 - ${DATE}"

    if gh release view "$TAG" --repo hzpfly/futures-trading-system &>/dev/null 2>&1; then
        echo "  ⚠️  Release $TAG 已存在，跳过创建"
    else
        gh release create "$TAG" \
            --repo hzpfly/futures-trading-system \
            --title "$RELEASE_TITLE" \
            --notes "$(cat <<EOF
## 每日 Tick + K线数据备份

- 日期: ${DATE}
- Tick 文件数: ${TICK_COUNT}
- K线文件数: ${KL_COUNT}
- Tick 原始: ${TICK_SIZE}
- K线大小: ${KL_SIZE}
- 压缩后: ${ARCHIVE_SIZE}
- 总文件: ${TOTAL_FILES}
- 内容: tick + 1min/5min/15min/60min/day/week K线
- 来源: TqSdk 实时行情 (61品种)

恢复: \`./triple_screen/restore_tick_data.sh ${DATE}\`
EOF
)" \
            "$ARCHIVE_PATH" 2>&1
        echo "  ✅ 已上传到 GitHub Releases: $TAG"
    fi
else
    echo ""
    echo "[3/3] 跳过上传 (--local 模式)"
    echo "  文件已保存到: $ARCHIVE_PATH"
fi

echo ""
echo "========================================"
echo "  备份完成 ✅"
echo "  本地: $ARCHIVE_PATH ($ARCHIVE_SIZE)"
echo "========================================"
