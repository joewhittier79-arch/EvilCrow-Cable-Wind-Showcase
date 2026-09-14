#!/bin/bash

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$BASE/../.." && pwd)"
LOGS="$PROJECT_ROOT/sessions"
ARCHIVES="$PROJECT_ROOT/archives"
SNAPSHOT="$PROJECT_ROOT/.payloadlog-before-session"
EVILCROW_MAC="${EVILCROW_MAC:-}"
SETTINGS="$PROJECT_ROOT/config/settings.json"
PAYLOAD_DELTA="$PROJECT_ROOT/.payloadlog-delta"
ARCHIVE_PAYLOAD="$PROJECT_ROOT/.payloadlog-delta.complete-records"

mkdir -p "$LOGS" "$ARCHIVES"
cd "$BASE" || exit 1

SETTINGS_OUTPUT=$(python3 - "$SETTINGS" <<'PYSETTINGS'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as settings_file:
        monitor = json.load(settings_file)["completion_monitor"]
    values = (
        monitor["poll_interval_seconds"],
        monitor["partial_inactivity_grace_seconds"],
        monitor["initial_activity_timeout_seconds"],
        monitor["complete_grace_seconds"],
    )
    if any(type(value) is not int for value in values):
        raise ValueError("all completion-monitor values must be integers")
    if values[0] <= 0 or values[1] <= 0 or values[2] <= 0 or values[3] < 0:
        raise ValueError(
            "poll interval, inactivity grace, and initial timeout must be "
            "positive; completion grace may be zero"
        )
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
    print(f"ERROR: Invalid completion monitor settings in {path}: {error}", file=sys.stderr)
    raise SystemExit(1)

print(*values)
PYSETTINGS
) || exit 1

read -r POLL_INTERVAL PARTIAL_INACTIVITY_GRACE INITIAL_ACTIVITY_TIMEOUT COMPLETE_GRACE <<< "$SETTINGS_OUTPUT"
# Reuse the configured initial observation window as a bounded second window
# when the deadline-crossing device-log observation is unavailable.
FINAL_OBSERVATION_GRACE=$INITIAL_ACTIVITY_TIMEOUT

# A retained snapshot means the preceding run did not finish its archive path.
# A raw delta with a partial final record also contains evidence that is absent
# from the archive-safe complete-record capture. Preserve either case before a
# new run reuses the working filenames. Successful complete-record runs create
# no permanent recovery copy.
RECOVERY_OUTPUT=$(python3 - "$ARCHIVES" "$SNAPSHOT" "$PAYLOAD_DELTA" "$ARCHIVE_PAYLOAD" <<'PYRECOVERY'
from datetime import datetime
from pathlib import Path
import shutil
import sys

archives = Path(sys.argv[1])
snapshot = Path(sys.argv[2])
raw_capture = Path(sys.argv[3])
archive_capture = Path(sys.argv[4])

raw_data = raw_capture.read_bytes() if raw_capture.exists() else b""
has_partial_record = bool(raw_data) and not raw_data.endswith(b"\n")
prior_archive_unfinished = snapshot.exists()

if prior_archive_unfinished or has_partial_record:
    reason = "failed-run" if prior_archive_unfinished else "partial-evidence"
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f%z")
    recovery_dir = archives / f"recovery-{reason}-{stamp}"
    try:
        recovery_dir.mkdir()
        if snapshot.exists():
            shutil.copy2(snapshot, recovery_dir / "payloadlog-before-session.raw")
        if raw_capture.exists():
            shutil.copy2(raw_capture, recovery_dir / "payloadlog-delta.raw")
        if archive_capture.exists():
            shutil.copy2(
                archive_capture,
                recovery_dir / "payloadlog-delta.complete-records",
            )
    except OSError as error:
        print(f"ERROR: Could not preserve prior recovery evidence: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(recovery_dir)
PYRECOVERY
) || exit 1

if [ -n "$RECOVERY_OUTPUT" ]; then
    echo "Preserved prior recovery evidence: $RECOVERY_OUTPUT"
fi

# Keep a run-specific capture available even if no test start is observed.
: > "$PAYLOAD_DELTA"
rm -f "$ARCHIVE_PAYLOAD"

# Automatically detect Kali's IPv4 address on wlan0.
SERVER_IP=$(ip -4 addr show wlan0 | awk '/inet / {sub(/\/.*$/, "", $2); print $2; exit}')
if [ -z "$SERVER_IP" ]; then
    echo "ERROR: Could not detect an IPv4 address on wlan0."
    exit 1
fi
echo "Detected server IP: $SERVER_IP"

# Automatically discover the Evil Crow without requiring sudo.
DEVICE_IP=$(ip neigh show dev wlan0 | awk -v mac="${EVILCROW_MAC,,}" 'tolower($5) == mac {print $1; exit}')

if [ -z "$DEVICE_IP" ] && [ -f "$PROJECT_ROOT/.run-context" ]; then
    DEVICE_IP=$(awk -F= '$1 == "DEVICE_IP" {print $2; exit}' "$PROJECT_ROOT/.run-context")
fi

if [ -z "$DEVICE_IP" ]; then
    if [ -n "$EVILCROW_IP" ]; then
        DEVICE_IP="$EVILCROW_IP"
        echo "Using EVILCROW_IP: $DEVICE_IP"
    else
        echo "ERROR: Could not determine the Evil Crow IP."
        exit 1
    fi
else
    echo "Detected Evil Crow IP: $DEVICE_IP"
fi

# Preserve the actual endpoints and run parameters for the final report.
RUN_CONTEXT="$PROJECT_ROOT/.run-context"
cat > "$RUN_CONTEXT" <<EOF
SERVER_IP=$SERVER_IP
SERVER_PORT=4444
DEVICE_IP=$DEVICE_IP
DEVICE_MAC=${EVILCROW_MAC:-Not recorded}
RECIPIENT_OS=ios
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
# An HTTP-successful empty response is a valid baseline after the device log has
# been cleared. Keep request failure distinct from a successful empty response.
SNAPSHOT_OK=false
SNAPSHOT_TMP="$SNAPSHOT.tmp"
for attempt in 1 2 3 4 5; do
    if curl -fsS --connect-timeout 5 --max-time 5 \
        "http://$DEVICE_IP/payloadlog" \
        > "$SNAPSHOT_TMP" 2>/dev/null; then

        if [ ! -s "$SNAPSHOT_TMP" ]; then
            mv "$SNAPSHOT_TMP" "$SNAPSHOT"
            SNAPSHOT_OK=true
            echo "Device payload history snapshot succeeded with an empty baseline."
            break
        fi

        # /payloadlog can be read while the device is still writing its final
        # line. Never allow an incomplete trailing line to become the session
        # isolation anchor.
        python3 - "$SNAPSHOT_TMP" <<'PYTRIM'
from pathlib import Path
import sys

p = Path(sys.argv[1])
data = p.read_bytes()

if data and not data.endswith(b"\n"):
    last_newline = data.rfind(b"\n")
    if last_newline >= 0:
        p.write_bytes(data[:last_newline + 1])
    else:
        p.write_bytes(b"")
PYTRIM

        if [ -s "$SNAPSHOT_TMP" ]; then
            mv "$SNAPSHOT_TMP" "$SNAPSHOT"
            SNAPSHOT_OK=true
            break
        fi
        echo "Payload history snapshot attempt $attempt returned data but no complete record; retrying..."
    else
        echo "Payload history snapshot request attempt $attempt failed; retrying..."
    fi
    rm -f "$SNAPSHOT_TMP"
    sleep 1
done

if [ "$SNAPSHOT_OK" != true ]; then
    echo "ERROR: Could not obtain a valid /payloadlog snapshot."
    rm -f "$BEFORE_SESSIONS" "$SNAPSHOT" "$SNAPSHOT_TMP"
    exit 1
fi

# Run the server as a child process.
python3 "$BASE/evilcrow-server.py" --port 4444 --target linux &
SERVER_PID=$!

SESSION_DIRS=()
RUN_STATE="waiting_for_start"
MONITOR_STARTED=$SECONDS
LAST_PROGRESS=$SECONDS
PROGRESS_TOKEN=""
FINALIZING=false
FINAL_OBSERVATION_ACTIVE=false
FINAL_OBSERVATION_STARTED=0

# All launcher stop conditions converge here so each run is archived once.
cleanup() {
    # Ignore further termination signals until the one finalization path ends.
    trap '' INT TERM

    local reason="${1:-unspecified stop condition}"
    local requested_status="${2:-0}"

    if [ "$FINALIZING" = true ]; then
        return
    fi
    FINALIZING=true

    echo
    echo "Finalizing run: $reason"
    echo "Stopping server..."
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -INT "$SERVER_PID" 2>/dev/null
    fi
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

    if ARCHIVE_CAPTURE_KIND=$(python3 - "$PAYLOAD_DELTA" "$ARCHIVE_PAYLOAD" <<'PYARCHIVEINPUT'
from pathlib import Path
import os
import sys

raw_path = Path(sys.argv[1])
archive_path = Path(sys.argv[2])
candidate_path = archive_path.with_name(archive_path.name + ".tmp")

raw_data = raw_path.read_bytes()
last_newline = raw_data.rfind(b"\n")
complete_data = raw_data[:last_newline + 1] if last_newline >= 0 else b""

candidate_path.write_bytes(complete_data)
os.replace(candidate_path, archive_path)
print("partial" if len(complete_data) != len(raw_data) else "complete")
PYARCHIVEINPUT
    ); then
        if [ "$ARCHIVE_CAPTURE_KIND" = "partial" ]; then
            echo "Raw capture retains a trailing partial record: $PAYLOAD_DELTA"
            echo "Archive classification input contains complete records only: $ARCHIVE_PAYLOAD"
        fi
        python3 "$BASE/archive_session.py" "${SESSION_DIRS[@]}" "$ARCHIVE_PAYLOAD"
        ARCHIVE_STATUS=$?
    else
        echo "ERROR: Could not prepare the complete-record archive input."
        ARCHIVE_STATUS=1
    fi

    rm -f "$BEFORE_SESSIONS"

    echo
    if [ "$SERVER_STATUS" -eq 0 ]; then
        echo "Server exited cleanly."
    else
        echo "WARNING: Server exited with status $SERVER_STATUS."
    fi

    if [ "$ARCHIVE_STATUS" -eq 0 ]; then
        echo "Archive complete."
        rm -f "$SNAPSHOT" "$ARCHIVE_PAYLOAD"
    else
        echo "ERROR: Archive generation failed with status $ARCHIVE_STATUS."
        if [ -f "$ARCHIVE_PAYLOAD" ]; then
            echo "Raw capture, complete-record archive input, and pre-session snapshot were retained for recovery."
        else
            echo "Raw capture and pre-session snapshot were retained for recovery."
        fi
    fi
    echo "Launcher closing automatically."

    if [ "$ARCHIVE_STATUS" -ne 0 ]; then
        exit "$ARCHIVE_STATUS"
    fi
    if [ "$SERVER_STATUS" -ne 0 ]; then
        exit "$SERVER_STATUS"
    fi
    exit "$requested_status"
}

trap 'trap "" INT TERM; cleanup "termination signal received" 130' INT TERM

# Monitor immediately; a device action need not create a recipient TCP session.
# The retained delta may include trailing partial bytes, but state decisions use
# complete newline-terminated event records only.
while kill -0 "$SERVER_PID" 2>/dev/null; do
    CURRENT_PAYLOAD="$PROJECT_ROOT/.payloadlog-current"
    CURRENT_PAYLOAD_TMP="$PROJECT_ROOT/.payloadlog-current.tmp"
    CANDIDATE_DELTA="$PROJECT_ROOT/.payloadlog-delta.candidate"
    POLL_OBSERVATION="failed"
    POLL_FAILURE_DETAIL=""

    if curl -fsS --connect-timeout 5 --max-time 5 \
        "http://$DEVICE_IP/payloadlog" \
        > "$CURRENT_PAYLOAD_TMP" 2>/dev/null; then
        if [ -s "$CURRENT_PAYLOAD_TMP" ]; then
            mv "$CURRENT_PAYLOAD_TMP" "$CURRENT_PAYLOAD"
            # The device may correct/mutate older payload-log lines after the
            # pre-session snapshot. Use an exact contiguous tail anchor instead
            # of requiring the entire historical snapshot to remain unchanged.
            # With a valid empty baseline, the full current log belongs to this
            # launcher run. For a nonempty baseline, a missing anchor still
            # fails closed: never guess which events belong to this run.
            ANCHOR_FILE="$PROJECT_ROOT/.payloadlog-anchor"

            # Ignore an incomplete trailing line in the pre-session snapshot.
            # /payloadlog can be read while the device is still appending to it,
            # so only complete newline-terminated records are safe anchor data.
            python3 - "$SNAPSHOT" "$ANCHOR_FILE" <<'PYANCHOR'
from pathlib import Path
import sys

snapshot = Path(sys.argv[1]).read_bytes()

if snapshot and not snapshot.endswith(b"\n"):
    snapshot = snapshot.rsplit(b"\n", 1)[0] + b"\n"

lines = snapshot.splitlines(keepends=True)
Path(sys.argv[2]).write_bytes(b"".join(lines[-20:]))
PYANCHOR
            ANCHOR_STATUS=$?

            if [ "$ANCHOR_STATUS" -eq 0 ]; then
                python3 - "$ANCHOR_FILE" "$CURRENT_PAYLOAD" "$PAYLOAD_DELTA" "$CANDIDATE_DELTA" <<'PYDELTA'
from pathlib import Path
import os
import sys

anchor = Path(sys.argv[1]).read_bytes()
current = Path(sys.argv[2]).read_bytes()
retained_path = Path(sys.argv[3])
candidate_path = Path(sys.argv[4])

if anchor:
    if anchor not in current:
        raise SystemExit(1)
    _, delta = current.rsplit(anchor, 1)
else:
    delta = current

retained = retained_path.read_bytes()
if len(delta) < len(retained) or not delta.startswith(retained):
    raise SystemExit(1)

if len(delta) > len(retained):
    candidate_path.write_bytes(delta)
    os.replace(candidate_path, retained_path)

raise SystemExit(0 if not delta or delta.endswith(b"\n") else 2)
PYDELTA
                ISOLATION_STATUS=$?
                if [ "$ISOLATION_STATUS" -eq 0 ]; then
                    POLL_OBSERVATION="successful"
                elif [ "$ISOLATION_STATUS" -eq 2 ]; then
                    POLL_FAILURE_DETAIL="HTTP request succeeded, but the isolated device log ended with a partial record"
                else
                    POLL_FAILURE_DETAIL="HTTP request succeeded, but device-log isolation did not safely confirm the retained capture"
                fi
            else
                POLL_FAILURE_DETAIL="HTTP request succeeded, but the device-log anchor could not be prepared"
            fi
        elif [ ! -s "$SNAPSHOT" ] && [ ! -s "$PAYLOAD_DELTA" ]; then
            # With an empty baseline and no retained events, an HTTP-successful
            # empty response is a valid observation that no event is yet visible.
            POLL_OBSERVATION="successful"
        else
            POLL_FAILURE_DETAIL="HTTP request succeeded with empty data that could not safely extend the retained capture"
        fi
    else
        CURL_STATUS=$?
        POLL_FAILURE_DETAIL="device-log request failed with curl status $CURL_STATUS"
    fi

    if [ "$POLL_OBSERVATION" = "failed" ]; then
        echo "Device-log observation unavailable: $POLL_FAILURE_DETAIL."
    fi

    STATE_OUTPUT=$(python3 - "$PAYLOAD_DELTA" <<'PYSTATE'
from pathlib import Path
import sys

data = Path(sys.argv[1]).read_bytes()
last_newline = data.rfind(b"\n")
complete_data = data[:last_newline + 1] if last_newline >= 0 else b""
lines = complete_data.decode("utf-8", errors="replace").splitlines()

events = []
for index, line in enumerate(lines):
    fields = [field.strip() for field in line.split("|")]
    if len(fields) >= 3:
        events.append((index, fields[2]))

starts = [index for index, event in events if event == "PAYLOAD_START"]
if not starts:
    print("waiting_for_start -1 0")
else:
    selected_start = starts[-1]
    execution_events = [
        event for index, event in events if index >= selected_start
    ]
    matching_completion = "PAYLOAD_COMPLETE" in execution_events[1:]
    state = "matching_completion" if matching_completion else "test_active"
    print(state, selected_start, len(execution_events))
PYSTATE
)
    if [ "$?" -ne 0 ]; then
        cleanup "device evidence state parsing failed" 1
    fi

    read -r OBSERVED_STATE SELECTED_START EVENT_COUNT <<< "$STATE_OUTPUT"
    CURRENT_PROGRESS_TOKEN="$SELECTED_START:$EVENT_COUNT"

    if [ "$OBSERVED_STATE" = "test_active" ]; then
        if [ "$RUN_STATE" != "test_active" ] || [ "$CURRENT_PROGRESS_TOKEN" != "$PROGRESS_TOKEN" ]; then
            if [ "$RUN_STATE" != "test_active" ]; then
                echo "Device PAYLOAD_START recorded; monitoring selected test execution."
            fi
            RUN_STATE="test_active"
            PROGRESS_TOKEN="$CURRENT_PROGRESS_TOKEN"
            LAST_PROGRESS=$SECONDS
        fi
    elif [ "$OBSERVED_STATE" = "matching_completion" ]; then
        RUN_STATE="matching_completion"
        echo
        echo "Payload completion detected."
        echo "Matching device PAYLOAD_COMPLETE recorded after the selected test start."
        if [ "$COMPLETE_GRACE" -gt 0 ]; then
            echo "Allowing $COMPLETE_GRACE seconds for final recipient evidence..."
            sleep "$COMPLETE_GRACE"
        fi
        echo "Stopping recipient server and creating archive..."
        cleanup "matching test completion observed" 0
    fi

    if [ "$RUN_STATE" = "waiting_for_start" ] && \
        [ $((SECONDS - MONITOR_STARTED)) -ge "$INITIAL_ACTIVITY_TIMEOUT" ]; then
        if [ "$POLL_OBSERVATION" = "successful" ]; then
            cleanup "no test start observed within ${INITIAL_ACTIVITY_TIMEOUT} seconds; confirmed by a successful device-log observation; result is indeterminate" 0
        elif [ "$FINAL_OBSERVATION_ACTIVE" != true ]; then
            FINAL_OBSERVATION_ACTIVE=true
            FINAL_OBSERVATION_STARTED=$SECONDS
            echo "Initial activity timeout reached without a successful confirming observation."
            echo "Continuing bounded device-log observation for up to ${FINAL_OBSERVATION_GRACE} additional seconds."
        elif [ $((SECONDS - FINAL_OBSERVATION_STARTED)) -ge "$FINAL_OBSERVATION_GRACE" ]; then
            cleanup "device-log observation unavailable throughout the bounded ${FINAL_OBSERVATION_GRACE}-second final-observation grace; result is indeterminate" 0
        fi
    fi

    if [ "$RUN_STATE" = "test_active" ] && \
        [ $((SECONDS - LAST_PROGRESS)) -ge "$PARTIAL_INACTIVITY_GRACE" ]; then
        RUN_STATE="incomplete_at_capture_cutoff"
        cleanup "incomplete at capture cutoff; no new observed device events within the configured ${PARTIAL_INACTIVITY_GRACE}-second inactivity grace" 0
    fi

    sleep "$POLL_INTERVAL"
done

cleanup "server process exited before a launcher completion condition" 0
