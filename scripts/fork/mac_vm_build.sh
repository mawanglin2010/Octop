#!/bin/bash
# fork 自用：在 NAS 上的 Intel macOS 虚拟机（mac-build）里交叉打 darwin-arm64 桌面包（GitHub 的 macOS Apple 芯片机器排不上时用）。
# 用法（在虚拟机里）：build_mac.sh <源码包.tar.gz> <完整版本> <基础版本> [平台]，例如 build_mac.sh ~/post1.tar.gz 1.0.3b5.post1 1.0.3b5
# 平台默认 darwin-arm64；传 darwin-amd64 则在这台 Intel 虚拟机上原生打 Intel 包。
# 源码包 = `git archive --prefix=octop/ <标签>` 再追加本机构建好的 src/octop/dashboard（前端产物不在 git 里）。
# 产物在 ~/out_<完整版本>/（dmg、zip、SHA256SUMS）；状态写 ~/status_<完整版本>（RUNNING / DONE / FAILED ...）。
# 要点：用国内镜像（虚拟机直连 GitHub/npm 只有约 0.3 MB/s）；交叉组装只允许二进制轮子，所以把只有源码包的纯 Python 包
# 先在本机构建成通用轮子（crcmod 强制走纯 Python 回退）；package.sh 的“修剪”一步会运行目标平台的 python，
# 在 Intel 上跑不了，只在临时副本里改用本机 python。没有任何密码写在这里。
# Build the darwin-arm64 desktop package on the Intel macOS VM (cross-assemble), mirroring fork-desktop.yml.
# usage: build_mac.sh <tarball> <full-version> <base-version> [darwin-arm64|darwin-amd64]
set -u
TARBALL="$1"; VER="$2"; BASE="$3"; PLAT="${4:-darwin-arm64}"
case "$PLAT" in darwin-arm64) EB_ARCH=--arm64 ;; darwin-amd64) EB_ARCH=--x64 ;; *) echo "unsupported platform $PLAT" >&2; exit 2 ;; esac
WORK="$HOME/work_${VER}"; OUT="$HOME/out_${VER}"
export PATH="$HOME/.local/bin:$HOME/tools/node/bin:$PATH"
# China mirrors (direct downloads from this VM are ~0.3 MB/s). Same files, same hashes.
export npm_config_registry=https://registry.npmmirror.com COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
export ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
export UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
export UV_PYTHON_INSTALL_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone/
log() { echo "[$(date +%H:%M:%S)] $*"; }
retry() { local n="$1"; shift; local i; for i in $(seq 1 "$n"); do "$@" && return 0; log "retry $i/$n: $*"; sleep 15; done; return 1; }
fail() { log "FAILED: $*"; echo "FAILED $*" > "$HOME/status_${VER}"; exit 1; }
echo "RUNNING" > "$HOME/status_${VER}"
log "host: $(uname -ms) $(sw_vers -productVersion)"

if ! command -v uv >/dev/null 2>&1; then
  log "install uv"; retry 5 bash -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh' || fail "uv install"
fi
uv --version
retry 5 uv python install 3.12 || fail "python 3.12"
PY="$(uv python find 3.12)"; log "python: $PY"

if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" != "24" ]; then
  log "install node 24"
  mkdir -p "$HOME/tools"
  file="$(curl -fsSL --retry 5 https://npmmirror.com/mirrors/node/latest-v24.x/SHASUMS256.txt | awk '/darwin-x64\.tar\.gz$/ {print $2; exit}')"
  [ -n "$file" ] || fail "node tarball name"
  rm -rf "$HOME/tools/node"; mkdir -p "$HOME/tools/node"
  retry 5 bash -c "curl -fsSL --retry 5 https://npmmirror.com/mirrors/node/latest-v24.x/$file | tar -xz -C $HOME/tools/node --strip-components=1" || fail "node download"
fi
node --version
corepack enable 2>/dev/null || true
retry 5 corepack prepare pnpm@12.15.0 --activate || fail "pnpm"
pnpm --version

log "extract source"
rm -rf "$WORK"; mkdir -p "$WORK"; tar -xzf "$TARBALL" -C "$WORK" || fail "extract"
cd "$WORK/octop" || fail "cd"
test -f src/octop/dashboard/index.html || fail "dashboard missing in tarball"
grep -m1 '^version' pyproject.toml

# Cross-assembling on an Intel host: the pruning step would run the arm64 runtime's python (Bad CPU type). The
# pruning script only deletes files, so run it with the host python. Only this throw-away copy is edited.
"$PY" - <<'PYEOF'
import pathlib
p = pathlib.Path("desktop/portable/package.sh")
t = p.read_text()
old = 'prune_py="$(runtime_python "${staging}/runtime")"'
assert old in t, "prune line not found"
p.write_text(t.replace(old, 'prune_py="$(uv python find 3.12)"'))
print("patched prune_py to use the host python")
PYEOF

export GREEN_HOST_PLAT=darwin-amd64
export PBS_BASE_URL=https://registry.npmmirror.com/-/binary/python-build-standalone/20251209 PBS_TAG=20251209 PBS_PY=3.12.12
export OCTOP_VERSION="$VER"

log "bootstrap portable CPython ($PLAT)"
retry 6 bash desktop/portable/bootstrap-runtime.sh "$PLAT" || fail "bootstrap-runtime"
log "assemble portable tree"
# Cross-assembling arm64 on an Intel host allows binary wheels only. Pure-Python packages that publish only an
# sdist (e.g. aliyun-python-sdk-core) are built here into universal (py3-none-any) wheels and fed to uv.
export UV_FIND_LINKS="$HOME/wheelhouse" PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
mkdir -p "$HOME/wheelhouse"
assemble_loop() {
  local i out rc pkg bad
  for i in $(seq 1 30); do
    out="$(bash desktop/portable/package.sh "$PLAT" 2>&1)"; rc=$?
    echo "$out" | tail -25
    [ "$rc" -eq 0 ] && return 0
    pkg="$(echo "$out" | sed -n 's/.*Because \([A-Za-z0-9_.-]*\)==\([^ ]*\) has no usable wheels.*/\1==\2/p' | head -1)"
    if [ -z "$pkg" ]; then log "package.sh failed for a reason other than a missing wheel"; return 1; fi
    log "no wheel for $pkg: building one from the sdist on this host"
    uvx --from pip pip wheel --no-deps --no-cache-dir -w "$HOME/wheelhouse" "$pkg" || return 1
    bad="$(ls "$HOME/wheelhouse" | grep -v -e '-none-any\.whl$' || true)"
    if [ -n "$bad" ]; then
      # C-extension package with a pure-Python fallback (crcmod): force the extension build to fail so setup falls back.
      log "platform-specific wheel $bad: rebuilding $pkg as pure Python (no C extension)"
      for w in $bad; do rm -f "$HOME/wheelhouse/$w"; done
      CC=/usr/bin/false CXX=/usr/bin/false uvx --from pip pip wheel --no-deps --no-cache-dir -w "$HOME/wheelhouse" "$pkg" || return 1
      bad="$(ls "$HOME/wheelhouse" | grep -v -e '-none-any\.whl$' || true)"
      if [ -n "$bad" ]; then log "still platform specific, cannot reuse for arm64: $bad"; return 1; fi
    fi
  done
  return 1
}
assemble_loop || fail "package.sh"
ls -1 "$HOME/wheelhouse"

log "stage electron runtime"
inner="desktop/portable/release/Octop-${PLAT}"
zip="desktop/portable/release/Octop-portable-${PLAT}-${VER}.zip"
test -f "$inner/packages/octop/dashboard/index.html" || fail "portable tree has no dashboard"
test -f "$inner/launch.py" || fail "portable tree has no launch.py"
rm -rf desktop/src/resources/portable; mkdir -p desktop/src/resources/portable
cp -a "$inner"/. desktop/src/resources/portable/
"$PY" - "$zip" "$VER" <<'PYEOF'
import hashlib, json, pathlib, sys, zipfile
path = pathlib.Path(sys.argv[1])
if not zipfile.is_zipfile(path):
    raise SystemExit("not a portable zip: %s" % path)
manifest = {"version": sys.argv[2], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
pathlib.Path("desktop/src/resources/portable/portable-version.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
print("Bundled", path.name, manifest["sha256"])
PYEOF
test -f desktop/src/resources/portable/packages/octop/dashboard/index.html || fail "staged tree has no dashboard"

log "pnpm install + checks"
cd desktop/src || fail "cd desktop/src"
retry 4 pnpm install --frozen-lockfile || fail "pnpm install"
pnpm run typecheck || fail "typecheck"
pnpm run test || fail "shell tests"

log "electron-builder ($PLAT)"
export OCTOP_PLATFORM="$PLAT" OCTOP_VERSION="$BASE"
export CSC_LINK="" CSC_KEY_PASSWORD="" WIN_CSC_LINK="" WIN_CSC_KEY_PASSWORD="" APPLE_ID="" APPLE_APP_SPECIFIC_PASSWORD="" APPLE_TEAM_ID=""
pnpm run build || fail "pnpm build"
retry 3 pnpm exec electron-builder --config electron-builder.config.ts --mac "$EB_ARCH" --publish never || fail "electron-builder"

log "collect"
rm -rf "$OUT"; mkdir -p "$OUT"
n=0
for f in release/Octop-desktop-${PLAT}-${BASE}.*; do
  case "$f" in *.dmg|*.zip) ;; *) continue ;; esac
  ext="${f##*.}"
  cp "$f" "$OUT/Octop-desktop-${PLAT}-${VER}.${ext}"; n=$((n+1))
done
ls -lh "$OUT"
[ "$n" -ge 2 ] || fail "expected dmg and zip, found $n"
( cd "$OUT" && shasum -a 256 * > SHA256SUMS )
echo "DONE" > "$HOME/status_${VER}"
log "DONE"
