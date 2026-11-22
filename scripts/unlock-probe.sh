#!/bin/bash
# Can this Mac be unlocked by typing the login password into the lock screen?
# A one-off check before Jarvis's desk display gets an "unlock" step.
#
#   1. store the password once (you type it; it goes to your login keychain):
#        security add-generic-password -U -s jarvis-unlock -a "$USER" -w
#   2. give the terminal you run this from Accessibility access
#      (System Settings → Privacy & Security → Accessibility)
#   3. run it, then lock the screen (⌃⌘Q) within 15 seconds and wait
#
# The result goes to /tmp/jarvis-unlock-probe.log (the screen is locked meanwhile).
set -u
log=/tmp/jarvis-unlock-probe.log
locked() {
  /usr/bin/python3 - <<'PY'
import ctypes
cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFDictionaryGetValue.restype = ctypes.c_void_p
cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
d = cg.CGSessionCopyCurrentDictionary()
v = cf.CFDictionaryGetValue(d, cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)) if d else None
print("locked" if v and cf.CFBooleanGetValue(v) else "unlocked")
PY
}

security find-generic-password -s jarvis-unlock -a "$USER" >/dev/null 2>&1 \
  || { echo "No password stored yet: run step 1 first."; exit 1; }
echo "Lock the screen now (⌃⌘Q). In 15 seconds this types the password; then come back and read $log"
sleep 15
{
  echo "$(date '+%T') before: $(locked)"
  caffeinate -u -t 3 &
  sleep 2
  # the password goes to osascript through the environment, never on a command line
  JARVIS_PW="$(security find-generic-password -s jarvis-unlock -a "$USER" -w)" \
    osascript -e 'tell application "System Events"' \
              -e 'keystroke (system attribute "JARVIS_PW")' \
              -e 'delay 0.3' -e 'key code 36' -e 'end tell' 2>&1
  echo "$(date '+%T') osascript exit: $?"
  sleep 4
  echo "$(date '+%T') after: $(locked)"
} > "$log" 2>&1
cat "$log"
