#!/bin/bash
# 从 GitHub Releases 恢复 tick 数据
# 用法:
#   ./restore_tick_data.sh 2026-06-24              # 恢复单日
#   ./restore_tick_data.sh 2026-06-20 2026-06-24   # 恢复日期范围
#   ./restore_tick_data.sh --list                   # 列出可用的备份
#   ./restore_tick_data.sh --all                    # 恢复全部可用备份

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$REPO_DIR/data/ticks"
BACKUP_DIR="$REPO_DIR/backups"

# --- 列出可用备份 ---
list_backups() {
    echo "可用备份 (GitHub Releases):"
    echo "============================"
    gh release list --repo hzpfly/futures-trading-system \
        | grep "tick-data-" \
        | awk '{printf "  %s  %s  %s\n", $1, $2, $4}'
}

# --- 恢复单日 ---
restore_date() {
    local DATE="$1"
    local TAG="tick-data-${DATE}"
    local ARCHIVE="$BACKUP_DIR/tick_${DATE}.tar.gz"

    mkdir -p "$BACKUP_DIR"

    # 如果本地已有，直接用
    if [ -f "$ARCHIVE" ]; then
        echo "  使用本地缓存: $ARCHIVE"
    else
        echo "  下载 $TAG..."
        cd "$BACKUP_DIR"
        gh release download "$TAG" \
            --repo hzpfly/futures-trading-system \
            --pattern "tick_${DATE}.tar.gz" 2>&1
    fi

    if [ ! -f "$ARCHIVE" ]; then
        echo "  ❌ 未找到 tick_${DATE}.tar.gz"
        return 1
    fi

    # 解压到 data/ticks
    echo "  解压 $DATE → $DATA_DIR ..."
    tar -xzf "$ARCHIVE" -C "$REPO_DIR"

    # 统计
    COUNT=$(find "$DATA_DIR" -name "${DATE}_*.parquet" 2>/dev/null | wc -l | tr -d ' ')
    echo "  ✅ 已恢复 ${COUNT} 个文件"
}

# --- 主逻辑 ---
case "${1:-}" in
    --list|-l)
        list_backups
        exit 0
        ;;
    --all|-a)
        echo "恢复全部备份..."
        gh release list --repo hzpfly/futures-trading-system \
            | grep "tick-data-" \
            | awk '{print $1}' \
            | sed 's/tick-data-//' \
            | while read date; do
                echo ""
                restore_date "$date"
            done
        exit 0
        ;;
    "")
        echo "用法:"
        echo "  $0 2026-06-24              恢复单日"
        echo "  $0 2026-06-20 2026-06-24   恢复日期范围"
        echo "  $0 --list                  列出可用备份"
        echo "  $0 --all                   恢复全部"
        exit 1
        ;;
    *)
        if [ -n "${2:-}" ]; then
            # 日期范围
            START="$1"
            END="$2"
            echo "恢复日期范围: $START ~ $END"
            current="$START"
            while [ "$current" != "$END" ]; do
                echo ""
                echo "=== $current ==="
                restore_date "$current" || echo "  ⚠️  $current 无备份"
                # 日期+1 (macOS兼容)
                current=$(date -j -v+1d -f "%Y-%m-%d" "$current" +%Y-%m-%d)
            done
            echo ""
            echo "=== $END ==="
            restore_date "$END" || echo "  ⚠️  $END 无备份"
        else
            # 单日
            restore_date "$1"
        fi
        ;;
esac
