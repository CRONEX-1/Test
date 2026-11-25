#!/bin/bash
# Builds Sigscan.app (universal: Apple Silicon + Intel) and zips it.
# Needs a Mac with Xcode or the Xcode Command Line Tools. GitHub Actions runs this
# for you on every push (see .github/workflows/macos-app.yml).
set -euo pipefail
cd "$(dirname "$0")"

APP=build/Sigscan.app
SDK="$(xcrun --sdk macosx --show-sdk-path)"

rm -rf build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

for arch in arm64 x86_64; do
  echo "==> Compiling for $arch"
  xcrun swiftc -O -swift-version 5 -parse-as-library \
    -target "$arch-apple-macos13.0" -sdk "$SDK" \
    Sources/*.swift -o "build/Sigscan-$arch"
done
lipo -create build/Sigscan-arm64 build/Sigscan-x86_64 -output "$APP/Contents/MacOS/Sigscan"

cp Info.plist "$APP/Contents/Info.plist"

echo "==> Building icon"
ICONSET=build/AppIcon.iconset
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" icon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

echo "==> Signing (ad-hoc)"
codesign --force --sign - "$APP"
codesign --verify --verbose "$APP"

echo "==> Zipping"
(cd build && ditto -c -k --keepParent Sigscan.app Sigscan.zip)
ls -lh build/Sigscan.zip
