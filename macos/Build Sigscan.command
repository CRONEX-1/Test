#!/bin/bash
# Double-click this file in Finder to build Sigscan.app on this Mac, put it in
# your Applications folder and open it. Nothing needs to be typed.
cd "$(dirname "$0")" || exit 1

echo "Sigscan builder"
echo

# Apple's free Command Line Tools provide the Swift compiler.
if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "This Mac needs Apple's free Command Line Tools to build the app."
  echo "A window will open: click Install and wait for it to finish (a few minutes),"
  echo "then double-click 'Build Sigscan' again."
  xcode-select --install >/dev/null 2>&1
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

if ! ./build.sh; then
  echo
  echo "The build failed. Please send the messages above to whoever gave you Sigscan."
  read -r -p "Press Return to close this window."
  exit 1
fi

DEST=/Applications
[ -w "$DEST" ] || DEST="$HOME/Applications"
mkdir -p "$DEST"
rm -rf "$DEST/Sigscan.app"
ditto build/Sigscan.app "$DEST/Sigscan.app"

echo
echo "Done! Sigscan is in $DEST. You can open it from Launchpad or Spotlight from now on."
open "$DEST/Sigscan.app"
sleep 2
osascript -e 'tell application "Terminal" to close (every window whose name contains "Build Sigscan")' >/dev/null 2>&1 &
exit 0
