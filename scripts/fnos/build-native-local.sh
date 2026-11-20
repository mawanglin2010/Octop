#!/usr/bin/env bash
# =============================================================================
# Fork 专用：在本机构建飞牛原生版 FPK（x86_64 或 ARM），不经过 GitHub Actions。
#
# 步骤与 .github/workflows/fork-release.yml 的 fnos-site-packages / fnos-pack
# 两个 job 一致：
#   1. make build：构建前端和 wheel（--skip-build 时复用 dist/ 里当前版本的 wheel）
#   2. 用 Python 3.12 把 wheel 及依赖装进 fnos/native/app/site-packages，
#      固定 mcp / langchain-mcp-adapters 版本、补 vendor 文件、精简、导入自检
#   3. bash scripts/build-fpk.sh native（首次运行会下载 fnpack 到 .verify/）
#   4. 默认删除 site-packages（--keep-site-packages 保留，便于排查）
#
# 用法（仓库根目录）：
#   bash scripts/fnos/build-native-local.sh                       # x86 包
#   bash scripts/fnos/build-native-local.sh --arch arm64          # ARM 包
#   bash scripts/fnos/build-native-local.sh --skip-build [--arch arm64]
# 产物：dist/Octop-fnos-native-amd64-<版本>.fpk 或 dist/Octop-fnos-native-arm64-<版本>.fpk，
# 在飞牛应用中心「手动安装」。需要 x86_64 Linux（fnpack 只有 linux-amd64）、uv、Node.js / npm。
#
# ARM 包是交叉安装（uv --python-platform aarch64-manylinux_2_28），与 GitHub 在
# ARM 机器上构建的包有三点差别：
#   - 本机不能运行 ARM 程序，不做导入自检，也不带 .pyc 缓存（首次启动稍慢）；
#     改为检查所有 .so 都是 aarch64；
#   - 关闭 C 编译器：只有源码包的依赖用纯 Python 版本（crcmod 的 CRC 校验较慢但结果相同）；
#   - 不安装 evdev（pynput 的 uinput 后端，必须本机编译；NAS 上用不到桌面控制）。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SKIP_BUILD=0
KEEP_SP=0
ARCH=amd64
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --keep-site-packages) KEEP_SP=1 ;;
    --arch) ARCH="${2:-}"; shift ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（可选 --arch amd64|arm64、--skip-build、--keep-site-packages）" >&2; exit 1 ;;
  esac
  shift
done
case "$ARCH" in
  amd64|arm64) ;;
  *) echo "--arch 只能是 amd64 或 arm64" >&2; exit 1 ;;
esac

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "只支持在 x86_64 Linux 上构建（fnpack 只有 linux-amd64 版本），当前：$(uname -s) $(uname -m)" >&2
  exit 1
fi
command -v uv >/dev/null || { echo "需要 uv：https://docs.astral.sh/uv/" >&2; exit 1; }

VER="$(sed -nE 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' pyproject.toml | head -1)"
echo "[local-fpk] 版本：$VER，架构：$ARCH"

PY="$(uv python find 3.12 2>/dev/null || true)"
if [ -z "$PY" ]; then
  echo "[local-fpk] 安装 Python 3.12（uv 管理，不影响项目虚拟环境）..."
  # uv may report errors refreshing other versions' links on some filesystems
  # even though 3.12 itself installed; only fail if 3.12 is still missing.
  uv python install 3.12 || true
  PY="$(uv python find 3.12 2>/dev/null || true)"
  [ -n "$PY" ] || { echo "Python 3.12 安装失败" >&2; exit 1; }
fi
echo "[local-fpk] Python：$PY"

# Copying the repo through SMB/Windows can turn git symlinks into plain files,
# which the wheel would then ship as junk; the CI checkout never has this.
BROKEN="$(git ls-files -s | awk '$1 == "120000" {print $4}' | while read -r f; do [ -L "$f" ] || echo "$f"; done)"
if [ -n "$BROKEN" ]; then
  echo "以下符号链接在工作区里变成了普通文件，请先恢复再构建：" >&2
  echo "$BROKEN" | sed 's/^/  git checkout -- /' >&2
  exit 1
fi

WHEEL="dist/octop-${VER}-py3-none-any.whl"
if [ "$SKIP_BUILD" = 0 ]; then
  command -v npm >/dev/null || { echo "需要 Node.js / npm 构建前端" >&2; exit 1; }
  make build
fi
[ -f "$WHEEL" ] || { echo "找不到 $WHEEL（去掉 --skip-build 重新构建）" >&2; exit 1; }

SP="$ROOT/fnos/native/app/site-packages"
OVERRIDES=""
cleanup() {
  if [ "$KEEP_SP" = 0 ]; then rm -rf "$SP"; fi
  if [ -n "$OVERRIDES" ]; then rm -f "$OVERRIDES"; fi
}
trap cleanup EXIT
rm -rf "$SP"
mkdir -p "$SP"

INSTALL=(uv pip install --quiet --python "$PY" --target "$SP" --no-cache)
if [ "$ARCH" = arm64 ]; then
  OVERRIDES="$(mktemp)"
  echo "evdev; sys_platform == 'never'" > "$OVERRIDES"
  INSTALL+=(--python-platform aarch64-manylinux_2_28 --overrides "$OVERRIDES")
  # No cross compiler: sdists with optional C extensions fall back to pure Python,
  # and any sdist that must compile fails here instead of shipping x86 code.
  export CC=/bin/false CXX=/bin/false
fi

echo "[local-fpk] 安装依赖到 site-packages..."
# Constrain to uv.lock so an upstream release (e.g. deepagents 0.7.41) cannot break the bundle.
CONSTRAINTS="$SP.constraints.txt"
uv export --project "$ROOT" --frozen --no-dev --no-emit-project --no-hashes -o "$CONSTRAINTS" >/dev/null
"${INSTALL[@]}" -c "$CONSTRAINTS" "$WHEEL"
rm -f "$CONSTRAINTS"
rm -rf "$SP/mcp" "$SP"/mcp-*.dist-info \
       "$SP/langchain_mcp_adapters" "$SP"/langchain_mcp_adapters-*.dist-info
"${INSTALL[@]}" --no-deps "mcp==1.28.1" "langchain-mcp-adapters==0.3.0"
cp "$WHEEL" "$SP/octop.whl"
mkdir -p "$SP/octop_harness/backends"
cp scripts/fnos/vendor/storage_errors.py "$SP/octop_harness/backends/storage_errors.py"
rm -f "$SP/.lock"
bash scripts/fnos/slim-site-packages.sh "$SP"
if [ "$ARCH" = amd64 ]; then
  # Like the release workflow, the self-check runs after slimming and leaves .pyc
  # caches for the imported modules in the package (faster first start on the NAS).
  PYTHONPATH="$SP" "$PY" -c "import octop, mcp, langchain_mcp_adapters; from octop.launch import run_foreground_blocking; from octop_harness.backends.storage_errors import classify_storage_error; print('[local-fpk] 自检通过 octop', octop.__version__)"
  OUT="dist/Octop-fnos-native-amd64-${VER}.fpk"
else
  # ARM binaries cannot run here: check every native library is aarch64 instead.
  "$PY" - "$SP" "$VER" <<'PYCHECK'
import sys
from pathlib import Path

sp, ver = Path(sys.argv[1]), sys.argv[2]
bad, count = [], 0
for so in sp.rglob("*.so*"):
    if not so.is_file():
        continue
    head = so.read_bytes()[:20]
    if head[:4] != b"\x7fELF":
        continue
    count += 1
    if int.from_bytes(head[18:20], "little") != 0xB7:  # EM_AARCH64
        bad.append(str(so.relative_to(sp)))
if bad:
    sys.exit("[local-fpk] 发现非 ARM 的二进制：" + "、".join(bad[:10]))
if not list(sp.glob(f"octop-{ver}.dist-info")):
    sys.exit(f"[local-fpk] site-packages 里没有 octop {ver}")
print(f"[local-fpk] 检查通过：{count} 个二进制库均为 aarch64，octop {ver}（ARM 不做导入自检）")
PYCHECK
  OUT="dist/Octop-fnos-native-arm64-${VER}.fpk"
fi

FPK_NAME_PREFIX=Octop-fnos FPK_ARCH="$ARCH" bash scripts/build-fpk.sh native

[ -f "$OUT" ] || { echo "没有生成 $OUT" >&2; exit 1; }
echo "[local-fpk] 完成：$OUT（$(( $(stat -c %s "$OUT") / 1048576 )) MB）"
echo "[local-fpk] 在飞牛应用中心「手动安装」选择此文件即可升级。"
