#!/usr/bin/env bash
# Writes Config/Local.xcconfig (signing team + the address of this machine's Jarvis,
# token included) and generates the Xcode project. Run on the Mac that runs Jarvis:
#   ./configure.sh <TEAM_ID> [bundle id]
set -euo pipefail
cd "$(dirname "$0")"
team="${1:?usage: ./configure.sh <TEAM_ID> [bundle id]}"
bundle="${2:-local.jarvis.desk.$(echo "$team" | tr "[:upper:]" "[:lower:]")}"
root="$(cd ../.. && pwd)"
port="$(cat "$root/data/server_port" 2>/dev/null || echo 5100)"
token="${JARVIS_TOKEN:-$(cat "$root/data/access_token" 2>/dev/null || true)}"
[ -n "$token" ] || { echo "no access token: start Jarvis once first (it writes data/access_token)" >&2; exit 1; }
host="${JARVIS_DESK_HOST:-$(scutil --get LocalHostName 2>/dev/null || hostname -s).local}"
umask 077
cat > Config/Local.xcconfig <<CONF
DEVELOPMENT_TEAM = $team
PRODUCT_BUNDLE_IDENTIFIER = $bundle
// "//" starts a comment in xcconfig files, hence /\$()/
JARVIS_DESK_URL = http:/\$()/$host:$port/desk?token=$token
CONF
echo "Jarvis Desk will open http://$host:$port/desk"
xcodegen generate --quiet
