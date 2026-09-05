#!/bin/bash
#
# sync-po.sh — 同步 po 文件到最新 pot 模板，产出零 fuzzy、稳定排序的干净 diff
#
# 解决的问题：
#   - msgmerge 默认的 fuzzy matching 会把不相关的旧翻译配到新 msgid 上
#   - msgmerge 的折行和条目重排导致 git diff 充满噪音
#   - obsolete 条目堆积
#
# 用法：
#   ./po/sync-po.sh              # 同步全部 po 文件
#   ./po/sync-po.sh es           # 只同步 es.po
#   ./po/sync-po.sh --check      # 只校验不修改（CI 友好）
#   ./po/sync-po.sh --check es   # 只校验 es.po
#
# 前置条件：
#   po/setzer.pot 已是最新（通过 ninja -C builddir setzer-pot + xgettext 生成）。
#   本脚本只负责 po 与 pot 的同步，不重新生成 pot。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
POT="$SCRIPT_DIR/setzer.pot"

CHECK_ONLY=0
LANGS=()

for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        *)       LANGS+=("$arg") ;;
    esac
done

# 默认处理全部 po 文件
if [ ${#LANGS[@]} -eq 0 ]; then
    while IFS= read -r line; do
        LANGS+=("$line")
    done < "$SCRIPT_DIR/LINGUAS"
fi

if [ ! -f "$POT" ]; then
    echo "错误: $POT 不存在。请先运行 ninja -C builddir setzer-pot 生成模板。" >&2
    exit 1
fi

check_po() {
    local lang="$1" po="$2"
    local errors=0

    # 1. 翻译完整性
    local fuzzy untrans obsolete
    fuzzy=$(grep -c '^#,.*fuzzy' "$po" 2>/dev/null || true)
    untrans=$(awk '/^msgstr ""$/{found=0} /^msgid /{found=1} found && /^msgstr ""$/{c++} END{print c+0}' "$po")
    obsolete=$(awk '/^#~/{c++} END{print c+0}' "$po")

    if [ "$fuzzy" -gt 0 ]; then
        echo "  ✗ $lang: $fuzzy 条 fuzzy 标记"
        errors=$((errors + 1))
    fi
    if [ "$obsolete" -gt 0 ]; then
        echo "  ✗ $lang: $obsolete 条 obsolete"
        errors=$((errors + 1))
    fi

    # 2. msgid/msgstr 结尾换行一致性（msgfmt fatal 根因）
    # 用 python3 + polib 做严格检查
    if command -v python3 &>/dev/null && python3 -c "import polib" 2>/dev/null; then
        local nl_issues
        nl_issues=$(python3 -c "
import polib, sys
po = polib.pofile('$po')
n = 0
for e in po:
    if e.msgid and e.msgstr and e.msgid.endswith('\n') != e.msgstr.endswith('\n'):
        n += 1
        print(f'  line {e.linenum}: msgid/str 换行不一致', file=sys.stderr)
print(n)
" 2>/dev/null || echo 0)
        if [ "$nl_issues" -gt 0 ]; then
            echo "  ✗ $lang: $nl_issues 条 msgid/msgstr 换行结尾不一致"
            errors=$((errors + 1))
        fi
    fi

    if [ "$errors" -eq 0 ]; then
        echo "  ✓ $lang: 校验通过"
    fi
    return $errors
}

sync_po() {
    local lang="$1" po="$SCRIPT_DIR/$lang.po"

    if [ ! -f "$po" ]; then
        echo "  跳过: $po 不存在"
        return 1
    fi

    echo "同步 $lang.po ..."

    # 核心：--no-fuzzy-matching 杜绝错配，--previous 保留旧 msgid，--no-wrap 减少 diff 噪音
    msgmerge --no-fuzzy-matching --previous --no-wrap "$po" "$POT" \
        | msgcat --sort-by-file --no-wrap - -o "$po.tmp"

    # 清理 obsolete 条目
    msgattrib --no-obsolete --no-wrap "$po.tmp" -o "$po"
    rm -f "$po.tmp"

    echo "  完成: $lang.po"
}

echo "NeoSetzer po 同步工具"
echo "pot: $POT"
echo ""

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "模式: 仅校验（不修改文件）"
    echo ""
    exit_code=0
    for lang in "${LANGS[@]}"; do
        po="$SCRIPT_DIR/$lang.po"
        if [ -f "$po" ]; then
            check_po "$lang" "$po" || exit_code=1
        else
            echo "  跳过: $po 不存在"
        fi
    done
    if [ "$exit_code" -ne 0 ]; then
        echo ""
        echo "校验未通过，请修复上述问题后再提交。"
    fi
    exit $exit_code
else
    echo "模式: 同步 + 校验"
    echo ""
    for lang in "${LANGS[@]}"; do
        sync_po "$lang" || true
    done

    echo ""
    echo "同步完成，开始校验 ..."
    echo ""
    exit_code=0
    for lang in "${LANGS[@]}"; do
        po="$SCRIPT_DIR/$lang.po"
        if [ -f "$po" ]; then
            check_po "$lang" "$po" || exit_code=1
        fi
    done

    echo ""
    if [ "$exit_code" -eq 0 ]; then
        echo "全部校验通过。"
    else
        echo "校验未通过，请处理上述问题。"
    fi
    exit $exit_code
fi
