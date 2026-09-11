#!/bin/bash

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS="$BASE/logs"
ARCHIVES="$BASE/archives"
SNAPSHOT="$BASE/.payloadlog-before-session"

mkdir -p "$LOGS" "$ARCHIVES"
cd "$BASE" || exit 1
rm -f "$BASE/.payloadlog-delta"

# Automatically detect Kali's IPv4 address on wlan0.
SERVER_IP=$(ip -4 addr show wlan0 | awk '/inet / {sub(/\/.*$/, "", $2); print $2; exit}')
if [ -z "$SERVER_IP" ]; then
    echo "ERROR: Could not detect an IPv4 address on wlan0."
    exit 1
fi
echo "Detected server IP: $SERVER_IP"

# Automatically discover the Evil Crow by its MAC address.
DEVICE_IP=$(sudo arp-scan --localnet 2>/dev/null | awk -v mac="${EVILCROW_MAC,,}" 'tolower($2) == mac {print $1; exit}')
if [ -z "$DEVICE_IP" ]; then
    if [ -n "$EVILCROW_IP" ]; then
        DEVICE_IP="$EVILCROW_IP"
        echo "ARP discovery did not find the Evil Crow; using EVILCROW_IP: $DEVICE_IP"
    else
        echo "ERROR: Could not discover the Evil Crow and EVILCROW_IP is not set."
        exit 1
    fi
else
    echo "Detected Evil Crow IP: $DEVICE_IP"
fi

# Preserve the actual endpoints and run parameters for the final report.
RUN_CONTEXT="$BASE/.run-context"
cat > "$RUN_CONTEXT" <<EOF
SERVER_IP=$SERVER_IP
SERVER_PORT=4444
DEVICE_IP=$DEVICE_IP
DEVICE_MAC=${EVILCROW_MAC:-Not recorded}
RECIPIENT_OS=linux
PAYLOAD_TRANSPORT=USB HID / device Wi-Fi
EOF

echo "========================================"
echo "       EVIL CROW CABLE WIND"
echo "========================================"
echo
echo "Server starting..."
echo "Session recording is active."
echo
echo "Waiting for payload completion..."
echo

# Record existing session directories before starting the server.
BEFORE_SESSIONS=$(mktemp)
find "$LOGS" -maxdepth 1 -type d -name 'session-20??-??-??_??-??-??*'  -print 2>/dev/null | sort > "$BEFORE_SESSIONS"

# Capture the device payload history immediately before the server starts.
# Require a valid non-empty snapshot; retry briefly if the device is not ready yet.
SNAPSHOT_OK=false
for attempt in 1 2 3 4 5; do
    if curl -fsS --connect-timeout 5 \
        "http://$DEVICE_IP/payloadlog" \
        > "$SNAPSHOT" 2>/dev/null && [ -s "$SNAPSHOT" ]; then
        SNAPSHOT_OK=true
        break
    fi
    echo "Payload history snapshot attempt $attempt failed or returned empty data; retrying..."
    sleep 1
done

if [ "$SNAPSHOT_OK" != true ]; then
    echo "ERROR: Could not obtain a valid non-empty /payloadlog snapshot."
    rm -f "$BEFORE_SESSIONS" "$SNAPSHOT"
    exit 1
fi

# Run the server as a child process.
python3 "$BASE/evilcrow-server.py" --port 4444 --target linux &
SERVER_PID=$!

# Start payload monitoring immediately.
# A payload may complete a recipient-side action without creating
# a recipient/server TCP session, so do not wait for SESSION_DIRS here.
SESSION_DIRS=()

# Handle Ctrl+C ourselves so the archive step always runs.
cleanup() {
    echo
    echo "Stopping server..."
    kill -INT "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    SERVER_STATUS=$?

    echo
    echo "Server session ended."
    echo "Collecting all sessions created during this launcher run..."
    echo

    mapfile -t SESSION_DIRS < <(
        find "$LOGS" -maxdepth 1 -type d -name 'session-20??-??-??_??-??-??*'  -print 2>/dev/null |
        sort |
        comm -13 "$BEFORE_SESSIONS" -
    )

    echo "Session directories:"
    printf '  %s\n' "${SESSION_DIRS[@]}"

    echo
    echo "Creating complete session archive..."
    echo

    python3 "$BASE/archive_session.py" "${SESSION_DIRS[@]}" "$BASE/.payloadlog-delta"

    rm -f "$BEFORE_SESSIONS"

    rm -f "$SNAPSHOT"

    echo
    echo "Archive complete."
    echo "Launcher closing automatically."

    exit "$SERVER_STATUS"
}

trap cleanup INT TERM

# Automatically finish when the device has recorded a new PAYLOAD_COMPLETE
# after the pre-session snapshot. Cleanup then stops the recipient/server,
# records its final SESSION_SUMMARY, and builds the complete archive.
while kill -0 "$SERVER_PID" 2>/dev/null; do
    SESSION_COMPLETE=false
    PAYLOAD_COMPLETE=false

    if grep -q "SESSION_SUMMARY" "${SESSION_DIRS[0]}/session.log" 2>/dev/null; then
        SESSION_COMPLETE=true
    fi

    CURRENT_PAYLOAD="$BASE/.payloadlog-current"
    CURRENT_PAYLOAD_TMP="$BASE/.payloadlog-current.tmp"
    if curl -fsS --connect-timeout 5 "http://$DEVICE_IP/payloadlog" > "$CURRENT_PAYLOAD_TMP" 2>/dev/null && [ -s "$CURRENT_PAYLOAD_TMP" ]; then
        mv "$CURRENT_PAYLOAD_TMP" "$CURRENT_PAYLOAD"
        if [ -s "$SNAPSHOT" ] && [ -s "$CURRENT_PAYLOAD" ]; then
            # The device may correct/mutate older payload-log lines after the
            # pre-session snapshot. Use an exact contiguous tail anchor instead
            # of requiring the entire historical snapshot to remain unchanged.
            # If the anchor is missing, fail closed: never guess which events
            # belong to this launcher run.
            ANCHOR_FILE="$BASE/.payloadlog-anchor"
            tail -n 20 "$SNAPSHOT" > "$ANCHOR_FILE"

            python3 - "$ANCHOR_FILE" "$CURRENT_PAYLOAD" "$BASE/.payloadlog-delta" <<'PYDELTA'
from pathlib import Path
import sys

anchor = Path(sys.argv[1]).read_text()
current = Path(sys.argv[2]).read_text()
delta_path = Path(sys.argv[3])

if anchor and anchor in current:
    _, delta = current.split(anchor, 1)
    delta_path.write_text(delta)
    raise SystemExit(0)

delta_path.write_text("")
raise SystemExit(1)
PYDELTA

            if [ "$?" -eq 0 ] && grep -q "PAYLOAD_COMPLETE" "$BASE/.payloadlog-delta"; then
                PAYLOAD_COMPLETE=true
            fi
        fi
    fi

    if [ "$PAYLOAD_COMPLETE" = true ]; then
        echo
        echo "Payload completion detected."
        echo "Device PAYLOAD_COMPLETE recorded."
        echo "Stopping recipient server and creating archive..."
        cleanup
    fi

    sleep 1
done

SERVER_STATUS=$?

# If the server exits normally, archive anyway.
if [ "$SERVER_STATUS" -ne 130 ]; then
    echo
    echo "Server session ended."
    echo "Collecting all sessions created during this launcher run..."
    echo

    mapfile -t SESSION_DIRS < <(
        find "$LOGS" -maxdepth 1 -type d -name 'session-20??-??-??_??-??-??*'  -print 2>/dev/null |
        sort |
        comm -13 "$BEFORE_SESSIONS" -
    )

    echo "Session directories:"
    printf '  %s\n' "${SESSION_DIRS[@]}"

    echo
    echo "Creating complete session archive..."
    echo

    python3 "$BASE/archive_session.py" "${SESSION_DIRS[@]}" "$BASE/.payloadlog-delta"

    rm -f "$BEFORE_SESSIONS"

    rm -f "$SNAPSHOT"

    echo
    echo "Archive complete."
    echo "Press Enter to close this window."
    read
fi

exit "$SERVER_STATUS"
