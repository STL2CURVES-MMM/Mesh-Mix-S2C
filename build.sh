#!/bin/zsh
# Build "Manifold Mesh Mix.app".
#
#   ./build.sh            build app + bundled engine
#   ./build.sh --app      rebuild only the Swift app (reuse the engine build)
#   ./build.sh --install  also copy the app to /Applications
set -euo pipefail
ROOT="${0:A:h}"
cd "$ROOT"
APP_NAME="Manifold Mesh Mix"
DIST="$ROOT/dist"
APP="$DIST/$APP_NAME.app"
PY="$ROOT/.venv/bin/python"
ONLY_APP=0
INSTALL=0
for a in "$@"; do
  case "$a" in
    --app) ONLY_APP=1 ;;
    --install) INSTALL=1 ;;
  esac
done

mkdir -p "$DIST" app/build

echo "==> Compiling Swift app"
swiftc -parse-as-library -O -swift-version 5 -target arm64-apple-macos14.0 \
  app/Sources/*.swift -o app/build/ManifoldMeshMix

if [[ $ONLY_APP -eq 0 || ! -d "$ROOT/engine/build/dist/mmm-engine" ]]; then
  echo "==> Freezing conversion engine (PyInstaller)"
  "$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
    --distpath "$ROOT/engine/build/dist" --workpath "$ROOT/engine/build/work" \
    "$ROOT/engine/mmm-engine.spec"
fi

echo "==> Assembling bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp app/Info.plist "$APP/Contents/Info.plist"
cp app/build/ManifoldMeshMix "$APP/Contents/MacOS/ManifoldMeshMix"
cp app/Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/"
[[ -f LICENSE ]] && cp LICENSE "$APP/Contents/Resources/"
ditto "$ROOT/engine/build/dist/mmm-engine" "$APP/Contents/Resources/engine"

echo "==> Signing (ad-hoc)"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || codesign --force --deep --sign - "$APP"

echo "==> Smoke test"
"$APP/Contents/Resources/engine/mmm-engine" version

if [[ $INSTALL -eq 1 ]]; then
  echo "==> Installing to /Applications"
  rm -rf "/Applications/$APP_NAME.app"
  ditto "$APP" "/Applications/$APP_NAME.app"
fi
du -sh "$APP"
echo "Built $APP"
