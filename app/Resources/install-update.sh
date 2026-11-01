#!/bin/sh
# Replaces the app with a downloaded update, then opens it again.
# Started by the app (Updater.swift) right before it quits:
#
#   install-update.sh PID NEW_APP DEST_APP FAILURE_MARKER
#
# Waits for the app (PID) to quit, moves the old app aside, copies the new
# one into place and removes the old one. If anything fails, the old app is
# put back and FAILURE_MARKER gets the reason, so the app can explain it
# when it opens again.
PID="$1"
NEW="$2"
DEST="$3"
MARKER="$4"

fail() {
  echo "$1" > "$MARKER"
  /usr/bin/open "$DEST"
  exit 1
}

i=0
while kill -0 "$PID" 2>/dev/null; do
  sleep 0.2
  i=$((i + 1))
  if [ "$i" -gt 600 ]; then
    echo "The app didn't quit, so the update wasn't installed." > "$MARKER"
    exit 1
  fi
done

[ -d "$NEW/Contents/MacOS" ] || fail "The downloaded update is incomplete."

OLD="$DEST.old-$$"
mv "$DEST" "$OLD" 2>/dev/null || fail "macOS didn't allow replacing the app. Allow Mesh Mix-S2C under System Settings → Privacy & Security → App Management, or drag the new version into Applications yourself."
if /usr/bin/ditto "$NEW" "$DEST"; then
  rm -rf "$OLD"
else
  rm -rf "$DEST"
  mv "$OLD" "$DEST"
  fail "The new version couldn't be copied into place (is the disk full?). The old version is still installed."
fi
/usr/bin/xattr -dr com.apple.quarantine "$DEST" 2>/dev/null
rm -rf "$(dirname "$NEW")"
/usr/bin/open "$DEST"
exit 0
