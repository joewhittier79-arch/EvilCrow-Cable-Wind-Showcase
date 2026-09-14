#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime
import urllib.request
import subprocess
import os
import shutil

BASE = Path(__file__).resolve().parents[2]
LOGS = BASE / "sessions"
ARCHIVE = BASE / "archives"
RUN_CONTEXT = BASE / ".run-context"

def format_device_elapsed(milliseconds):
    """Format a device millisecond counter as human-readable elapsed time."""
    try:
        total_seconds = int(milliseconds) / 1000
    except (TypeError, ValueError):
        return "Unknown elapsed time"

    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60

    if hours:
        return f"{hours}h {minutes:02d}m {seconds:06.3f}s"
    if minutes:
        return f"{minutes}m {seconds:06.3f}s"
    return f"{seconds:.3f}s"


def load_run_context():
    context = {}
    if RUN_CONTEXT.exists():
        for line in RUN_CONTEXT.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                context[key.strip()] = value.strip()
    return context

def normalize_recipient_os(value):
    """Return a consistent display value for meaningful OS evidence."""
    text = str(value or "").strip()
    if text.casefold() in {"", "unknown", "not recorded"}:
        return None

    display_names = {
        "ios": "iOS",
        "linux": "Linux",
        "macos": "macOS",
        "windows": "Windows",
    }
    return display_names.get(text.casefold(), text)

def unique_recipient_os_values(values):
    """Normalize and de-duplicate OS evidence while preserving input order."""
    unique = []
    seen = set()
    for value in values:
        normalized = normalize_recipient_os(value)
        if normalized is None:
            continue
        identity = normalized.casefold()
        if identity not in seen:
            seen.add(identity)
            unique.append(normalized)
    return unique

def main():
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    # Accept one or more exact session directories.
    # No session fallback: an archive may contain only sessions explicitly supplied by the launcher.
    args = __import__("sys").argv[1:]

    # The launcher may append an explicit payload-capture file.
    # It is not a session directory and must not be treated as one.
    if args:
        last_arg = Path(args[-1])
        if last_arg.is_file() and last_arg.name != "session.log":
            args = args[:-1]

    session_dirs = [
        Path(arg).resolve()
        for arg in args
    ]

    sessions = []
    for session_dir in session_dirs:
        session = session_dir / "session.log"
        if not session.exists():
            raise SystemExit(f"Session log not found: {session}")
        sessions.append(session)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = ARCHIVE / f"archive-{stamp}"
    dest.mkdir()

    server_parts = []
    received_parts = []
    preserved_session_artifacts = []
    recipient_sessions_root = dest / "recipient-sessions"
    if sessions:
        recipient_sessions_root.mkdir()

    for session in sessions:
        preserved_session_dir = recipient_sessions_root / session.parent.name
        preserved_session_dir.mkdir()

        session_artifacts = (
            (session, "session.log", "byte-exact recipient/server session log"),
            (
                session.parent / "received.log",
                "received.log",
                "byte-exact raw received data",
            ),
            (
                session.parent / "SESSION_SUMMARY.txt",
                "SESSION_SUMMARY.txt",
                "byte-exact session summary",
            ),
            (
                session.parent / "REPORT.txt",
                "SESSION_REPORT.txt",
                "byte-exact per-session report copied from REPORT.txt",
            ),
        )
        for source, archive_name, description in session_artifacts:
            if source.exists():
                archive_path = preserved_session_dir / archive_name
                shutil.copy2(source, archive_path)
                preserved_session_artifacts.append(
                    (archive_path.relative_to(dest).as_posix(), description)
                )

        server_text = session.read_text(
            encoding="utf-8", errors="replace"
        )
        server_parts.append(
            f"===== {session.parent.name} =====\n{server_text.rstrip()}"
        )

        received = session.parent / "received.log"
        if received.exists():
            received_text = received.read_text(
                encoding="utf-8", errors="replace"
            )
            received_parts.append(
                f"===== {session.parent.name} =====\n{received_text.rstrip()}"
            )

    server_text = "\n\n".join(server_parts)
    received_text = "\n\n".join(received_parts)

    session_recipient_os_values = []
    for session in sessions:
        summary = session.parent / "SESSION_SUMMARY.txt"
        if summary.exists():
            for line in summary.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.startswith("Recipient OS:"):
                    value = line.split(":", 1)[1].strip()
                    session_recipient_os_values.append(value)
                    break

    session_recipient_os_values = unique_recipient_os_values(
        session_recipient_os_values
    )

    if server_text:
        (dest / "server-session.log").write_text(
            server_text + "\n",
            encoding="utf-8"
        )

    if received_text:
        (dest / "received.log").write_text(
            received_text + "\n",
            encoding="utf-8"
        )

    # The launcher may provide a run-specific payload capture as the
    # final argument. Never consume the global .payloadlog-delta during
    # manual archive regeneration because it may belong to a different run.
    import sys

    payload_source = (
        Path(sys.argv[-1]).resolve()
        if len(sys.argv) > 1
        and Path(sys.argv[-1]).is_file()
        and Path(sys.argv[-1]).name != "session.log"
        else None
    )

    if payload_source is not None:
        payload_text = payload_source.read_text(
            encoding="utf-8", errors="replace"
        )
    else:
        payload_text = None

    snapshot = BASE / ".payloadlog-before-session"

    # Prefer the launcher-recorded device IP. Fall back to the normal
    # neighbor table without requiring sudo.
    context_for_discovery = load_run_context()
    device_ip = context_for_discovery.get("DEVICE_IP")

    if not device_ip:
        result = subprocess.run(
            ["ip", "neigh", "show", "dev", "wlan0"],
            capture_output=True,
            text=True,
            check=False
        )

        expected_mac = os.environ.get("EVILCROW_MAC", "").strip().lower()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 5 and expected_mac and fields[4].lower() == expected_mac:
                device_ip = fields[0]
                break

    if not device_ip:
        current_payload_text = (
            "Unable to automatically discover the Evil Crow "
            "by MAC address.\n"
        )
    else:
        try:
            with urllib.request.urlopen(
                f"http://{device_ip}/payloadlog", timeout=5
            ) as response:
                current_payload_text = response.read().decode(
                    "utf-8", errors="replace"
                )
        except Exception as e:
            current_payload_text = (
                f"Unable to retrieve device payload log: {e}\n"
            )

    if payload_text is not None:
        pass
    elif snapshot.exists():
        before_payload_text = snapshot.read_text(
            encoding="utf-8", errors="replace"
        )

        # Only isolate new events when the current device log contains the
        # exact pre-session snapshot as its prefix. Never use line counts:
        # device reboots, truncation, or log changes can otherwise cause
        # historical events to be misidentified as current-session events.
        if current_payload_text.startswith(before_payload_text):
            payload_text = current_payload_text[len(before_payload_text):]
        else:
            payload_text = (
                "Device payload log could not be safely isolated from the "
                "pre-session snapshot.\n"
                "The current log does not contain the exact pre-session "
                "snapshot as its prefix.\n"
                "No historical device events are included in this report.\n"
            )
    else:
        payload_text = (
            "No safely associated device payload capture was available "
            "for this archive.\n"
            "No unrelated global payload capture was included.\n"
        )

    (dest / "device-payload-execution.log").write_text(
        payload_text, encoding="utf-8"
    )

    recipient_events = []
    for session in sessions:
        session_text = session.read_text(encoding="utf-8", errors="replace")
        session_events = [
            line for line in session_text.splitlines()
            if (
                "] EVENT: CONNECTED " in line
                or "] EVENT: DISCONNECTED " in line
                or "] EVENT: SESSION_SUMMARY " in line
                or "] RECIPIENT:" in line
                or "] DEVICE -> SERVER:" in line
            )
        ]
        if session_events:
            recipient_events.append(f"===== {session.parent.name} =====")
            recipient_events.extend(session_events)

    server_to_device_events = []
    for session in sessions:
        session_text = session.read_text(encoding="utf-8", errors="replace")
        server_to_device_events.extend(
            line for line in session_text.splitlines()
            if "] SERVER -> DEVICE:" in line
        )

    report = []
    report.append("EVIL CROW CABLE WIND — SESSION REPORT")
    report.append("=" * 50)
    report.append(
        f"Report created (Kali local time): "
        f"{datetime.now().strftime("%B %-d, %Y at %-I:%M:%S %p")}"
    )
    report.append("Server timestamps: Kali local time")
    report.append("Device time: relative device counter; not wall-clock time")
    report.append("")

    payload_lines = payload_text.rstrip().splitlines() if payload_text.rstrip() else []

    # Keep the complete received history for raw evidence, but scope the
    # execution report to the latest complete payload. This prevents older
    # payloads from inflating command counts or producing negative elapsed
    # times when the device log contains many historical executions.
    payload_starts = [line for line in payload_lines if "PAYLOAD_START" in line]
    payload_completes = [line for line in payload_lines if "PAYLOAD_COMPLETE" in line]

    execution_lines = []
    if payload_starts:
        latest_start_index = max(
            i for i, line in enumerate(payload_lines)
            if "PAYLOAD_START" in line
        )
        latest_complete_index = next(
            (
                i for i in range(latest_start_index, len(payload_lines))
                if "PAYLOAD_COMPLETE" in payload_lines[i]
            ),
            None,
        )
        if latest_complete_index is not None:
            execution_lines = payload_lines[
                latest_start_index:latest_complete_index + 1
            ]
        else:
            execution_lines = payload_lines[latest_start_index:]

    execution_payload_starts = [
        line for line in execution_lines if "PAYLOAD_START" in line
    ]
    execution_payload_completes = [
        line for line in execution_lines if "PAYLOAD_COMPLETE" in line
    ]
    command_starts = [
        line for line in execution_lines if "COMMAND_START" in line
    ]
    command_completes = [
        line for line in execution_lines if "COMMAND_COMPLETE" in line
    ]
    metadata_recipient_os_values = unique_recipient_os_values(
        line.split("|", 3)[-1].strip().split(":", 1)[1]
        for line in command_starts
        if line.split("|", 3)[-1].strip().casefold().startswith("# os:")
    )

    def device_elapsed_from_line(line):
        """Return the device millisecond counter from a payload event line."""
        try:
            parts = [part.strip() for part in line.split("|")]
            if len(parts) >= 2:
                return int(parts[1])
        except (TypeError, ValueError):
            pass
        return None

    payload_start_ms = (
        device_elapsed_from_line(execution_payload_starts[-1])
        if execution_payload_starts
        else None
    )

    def format_device_event(line):
        """Show raw device evidence plus payload-relative elapsed time."""
        milliseconds = device_elapsed_from_line(line)
        if milliseconds is None:
            return line

        if payload_start_ms is not None:
            relative_ms = milliseconds - payload_start_ms
            display_line = line.replace("TIME_UNSYNCED", "DEVICE_TIME", 1)
            return (
                f"{display_line} "
                f"[payload elapsed: +{format_device_elapsed(relative_ms)} "
                f"| device counter: {milliseconds} ms]"
            )

        return (
            f"{line} "
            f"[device counter: {milliseconds} ms]"
        )

    def format_device_timeline_event(line):
        """Translate a device event into a concise human-readable timeline entry."""
        milliseconds = device_elapsed_from_line(line)
        if milliseconds is None:
            return line

        relative_ms = (
            milliseconds - payload_start_ms
            if payload_start_ms is not None
            else None
        )

        prefix = (
            f"+{format_device_elapsed(relative_ms)}  "
            if relative_ms is not None
            else ""
        )

        parts = [part.strip() for part in line.split("|")]
        event = parts[2] if len(parts) >= 3 else ""
        detail = parts[3] if len(parts) >= 4 else ""

        if event == "PAYLOAD_START":
            text = "Payload started"
        elif event == "PAYLOAD_COMPLETE":
            text = "Payload completed"
        elif event == "COMMAND_START" and detail.startswith("# Name:"):
            text = f"Name: {detail.split(':', 1)[1].strip()}"
        elif event == "COMMAND_START" and detail.startswith("# Description:"):
            text = f"Description: {detail.split(':', 1)[1].strip()}"
        elif event == "COMMAND_START" and detail.startswith("# OS:"):
            text = f"Target OS: {detail.split(':', 1)[1].strip()}"
        elif event == "COMMAND_START" and detail.startswith("ServerConnect "):
            text = f"Connection requested: {detail.split(None, 1)[1]}"
        elif event == "COMMAND_START" and detail.startswith("RunNix "):
            text = f"Linux command started: {detail.split(None, 1)[1]}"
        elif event == "COMMAND_COMPLETE" and detail.startswith("RunNix "):
            text = "Linux command completed"
        elif event == "SERVER_CONNECT_RESULT":
            value = detail.split("=", 1)[-1].strip().lower()
            text = f"Connection result: {'successful' if value == 'true' else 'failed'}"
        elif event == "SERVER_CONNECT_STATE":
            value = detail.split("=", 1)[-1].strip().lower()
            text = f"Connection state: {'connected' if value == 'true' else 'not connected'}"
        elif event == "SERVER_TEST_MARKER_SENT":
            text = f"Test marker sent: {detail}"
        elif event == "COMMAND_COMPLETE" and detail.startswith("ServerConnect "):
            text = "Connection command completed"
        elif event == "COMMAND_COMPLETE" and detail.startswith("# "):
            return None
        else:
            text = f"{event}: {detail}" if detail else event

        return prefix + text

    def device_duration(start_line, complete_line):
        """Return elapsed duration between two device events."""
        start_ms = device_elapsed_from_line(start_line)
        complete_ms = device_elapsed_from_line(complete_line)
        if start_ms is None or complete_ms is None:
            return None
        return complete_ms - start_ms
    connect_results = [line for line in execution_lines if "SERVER_CONNECT_RESULT" in line]
    connect_states = [line for line in execution_lines if "SERVER_CONNECT_STATE" in line]
    server_connect_commands = [
        line.split("|", 3)[-1].strip()
        for line in command_starts
        if "ServerConnect " in line
    ]

    peer_endpoints = []
    peer_ips = []
    for session in sessions:
        summary = session.parent / "SESSION_SUMMARY.txt"
        if summary.exists():
            for line in summary.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.startswith("Remote endpoint:"):
                    endpoint = line.split(":", 1)[1].strip()
                    if endpoint and endpoint != "UNKNOWN":
                        peer_endpoints.append(endpoint)
                        peer_ips.append(endpoint.rsplit(":", 1)[0])
                    break

    context = load_run_context()

    session_ids = ", ".join(
        session.parent.name for session in sessions
    ) or "No recipient/server session created"

    # The launcher records the actual run endpoints. Use those values
    # for endpoint identification rather than reconstructing them from
    # an unrelated or absent recipient session.
    device_ip = context.get("DEVICE_IP", "Not recorded")
    device_mac = context.get("DEVICE_MAC", "Not recorded")
    server_ip = context.get("SERVER_IP", "Not recorded")
    server_port = context.get("SERVER_PORT", "4444")
    recipient_os_from_context = normalize_recipient_os(
        context.get("RECIPIENT_OS")
    )
    if session_recipient_os_values:
        recipient_os_for_report = ", ".join(session_recipient_os_values)
    elif metadata_recipient_os_values:
        recipient_os_for_report = ", ".join(metadata_recipient_os_values)
    elif recipient_os_from_context is not None:
        recipient_os_for_report = recipient_os_from_context
    else:
        recipient_os_for_report = "Not recorded"
    payload_transport = context.get(
        "PAYLOAD_TRANSPORT", "Not recorded"
    )
    report_transport = (
        "Device Wi-Fi / TCP"
        if server_connect_commands
        else payload_transport
    )

    # Preserve the actual observed TCP peer endpoint when a recipient/server
    # session exists. Otherwise explicitly report that no TCP peer endpoint
    # was observed instead of using historical session data.
    peer_endpoint = ", ".join(
        dict.fromkeys(peer_endpoints)
    ) if peer_endpoints else "No TCP peer endpoint observed"

    # If the payload itself requested ServerConnect, retain that target
    # as additional device-side evidence.
    server_target = "Not requested"
    if server_connect_commands:
        server_target = server_connect_commands[0].split()[-1]
        if ":" not in server_target:
            server_target = server_target + ":4444"

    if execution_payload_starts and execution_payload_completes:
        result_status = "Completed"
        payload_result_text = "PAYLOAD_COMPLETE recorded"
    elif execution_payload_starts and not execution_payload_completes:
        result_status = "Incomplete"
        payload_result_text = "PAYLOAD_START recorded; PAYLOAD_COMPLETE not recorded"
    else:
        result_status = "Indeterminate"
        payload_result_text = "PAYLOAD_START not recorded; completion cannot be determined"

    command_count = "Not recorded"
    if execution_payload_completes and "commands=" in execution_payload_completes[-1]:
        command_count = execution_payload_completes[-1].split("commands=", 1)[1].strip()

    connection_result = (
        connect_results[-1].split("|", 3)[-1].strip()
        if connect_results else "Not recorded"
    )
    connection_state = (
        connect_states[-1].split("|", 3)[-1].strip()
        if connect_states else "Not recorded"
    )

    report.append("SESSION OVERVIEW")
    report.append("-" * 50)
    report.append(f"Session ID: {session_ids}")
    report.append(f"Result: {result_status}")
    report.append("Payload result: " + payload_result_text)
    payload_step_count = len(command_starts)
    metadata_count = sum(
        1 for line in command_starts
        if line.split("|", 3)[-1].strip().startswith("# ")
    )
    timing_control_count = sum(
        1 for line in command_starts
        if line.split("|", 3)[-1].strip().startswith(("Delay ", "Release"))
    )
    executable_action_count = payload_step_count - metadata_count - timing_control_count

    report.append(f"Payload entries recorded: {payload_step_count}")
    report.append(f"Metadata entries: {metadata_count}")
    report.append(f"Executable actions: {executable_action_count}")
    report.append(f"Timing/control steps: {timing_control_count}")
    report.append("")

    report.append("ENDPOINT & CONNECTION INFORMATION")
    report.append("-" * 50)
    report.append(f"Evil Crow IP: {device_ip}")
    report.append(f"Evil Crow MAC: {device_mac}")
    report.append(f"Recipient / Server IP: {server_ip}")
    report.append(f"Recipient OS: {recipient_os_for_report}")
    report.append(f"Recipient port: {server_port}")
    report.append(f"Payload transport: {report_transport}")
    report.append(f"Requested server target: {server_target}")
    report.append(f"Observed TCP peer endpoint: {peer_endpoint}")
    if server_connect_commands:
        report.append(f"Connection result: {connection_result}")
        report.append(f"Connection state: {connection_state}")
    else:
        report.append("Connection result: Not applicable — no TCP connection requested")
        report.append("Connection state: Not applicable — no TCP connection requested")
    report.append("")

    report.append("PAYLOAD DEPLOYMENT & EXECUTION MECHANISM")
    report.append("-" * 50)
    report.append(f"Deployment transport: {report_transport}")
    if server_connect_commands:
        report.append(
            "Recipient interaction mechanism: device network action using "
            "a TCP connection to the requested listener address and port."
        )
        report.append(
            "Device-side processing: the Evil Crow processes the ServerConnect "
            "action and records its command lifecycle, connection result/state, "
            "and payload completion."
        )
        report.append(
            "Recipient-side confirmation: listener session evidence is used "
            "to confirm that the connection was accepted and data was received."
        )
    else:
        report.append(
            "Recipient interaction mechanism: USB HID keyboard emulation is "
            "used for payload actions that type commands or invoke recipient-side interfaces."
        )
        report.append(
            "Device-side processing: the Evil Crow processes the payload and "
            "records PAYLOAD_START, COMMAND_START/COMMAND_COMPLETE, and PAYLOAD_COMPLETE events."
        )
        report.append(
            "Recipient-side confirmation: device completion confirms completion "
            "of the device-side routine; recipient-side success is reported "
            "separately only when supported by recipient evidence."
        )
    report.append("")

    report.append("PAYLOAD EXECUTION SUMMARY")
    report.append("-" * 50)
    report.append(
        f"Payload start: "
        f"{format_device_event(execution_payload_starts[-1]) if execution_payload_starts else 'Not recorded'}"
    )
    report.append(f"Payload entries started: {len(command_starts)}")
    report.append(f"Payload entries completed: {len(command_completes)}")
    report.append(f"Metadata entries: {metadata_count}")
    report.append(f"Executable actions: {executable_action_count}")
    report.append(
        f"Payload complete: "
        f"{format_device_event(execution_payload_completes[-1]) if execution_payload_completes else 'Not recorded'}"
    )

    payload_duration = None
    if execution_payload_starts and execution_payload_completes:
        payload_duration = device_duration(
            execution_payload_starts[-1],
            execution_payload_completes[-1]
        )

    report.append(
        "Total device payload duration: "
        + (
            f"{format_device_elapsed(payload_duration)} "
            f"({payload_duration} ms)"
            if payload_duration is not None
            else "Not calculable from recorded device timestamps."
        )
    )
    report.append("")

    report.append("PAYLOAD COMMAND DETAILS")
    report.append("-" * 50)

    if command_starts:
        executable_command_number = 0

        for index, start_line in enumerate(command_starts, 1):
            command_text = start_line.split("|", 3)[-1].strip()
            is_metadata = command_text.startswith("# ")

            if is_metadata:
                mechanism = (
                    "Payload metadata: descriptive information supplied "
                    "with the payload, not an executable recipient-side command."
                )
                evidence = (
                    "Device COMMAND_START recorded for this payload metadata entry."
                )
            else:
                executable_command_number += 1
                mechanism = "Device command mechanism not specifically identified."
                evidence = "Device COMMAND_START recorded."

            if not is_metadata:
                if command_text.startswith("RunNix "):
                    mechanism = (
                        "HID action: device opens a terminal with Ctrl+Alt+T, "
                        "waits approximately 2 seconds, then types the supplied "
                        "Linux command and returns from the HID routine."
                    )
                    evidence = (
                        "Device COMMAND_START and COMMAND_COMPLETE recorded, "
                        "confirming that the Evil Crow completed the RunNix HID "
                        "routine. The routine opens the recipient terminal, "
                        "waits approximately 2 seconds, and types the exact "
                        "Linux command shown above. Any recipient-side "
                        "application launch or command success is reported "
                        "separately only when supported by recipient evidence."
                    )
                elif command_text.startswith("ServerConnect "):
                    mechanism = (
                        "Network action: device parses the requested server "
                        "address/port and attempts a TCP connection."
                    )
                    evidence = (
                        "Device COMMAND_START plus SERVER_CONNECT_RESULT/"
                        "SERVER_CONNECT_STATE events are used when present; "
                        "recipient session evidence is used to confirm actual "
                        "TCP receipt."
                    )
                elif command_text.startswith("RunLauncher "):
                    mechanism = (
                        "HID action: device opens the desktop application "
                        "launcher with Alt+F2 and types the supplied command."
                    )
                    evidence = (
                        "Device command lifecycle confirms the HID routine "
                        "completed; recipient application launch is assessed "
                        "separately from recipient evidence."
                    )
                elif command_text.startswith("RunMac "):
                    mechanism = (
                        "HID action: device opens the macOS search interface "
                        "with Command+Space and types the supplied command."
                    )
                    evidence = (
                        "Device command lifecycle confirms the HID routine "
                        "completed; recipient-side execution is assessed "
                        "separately from recipient evidence."
                    )
                elif command_text.startswith("CtrlAltT"):
                    mechanism = (
                        "HID action: device sends the Ctrl+Alt+T keyboard "
                        "shortcut to the recipient."
                    )
                    evidence = (
                        "Device command lifecycle confirms the HID action "
                        "completed; terminal creation is assessed separately "
                        "from recipient evidence."
                    )
                elif command_text.startswith("RunWin "):
                    mechanism = (
                        "HID action: device types the supplied Windows command "
                        "through its Windows execution path."
                    )
                    evidence = (
                        "Device command lifecycle confirms the HID routine "
                        "completed; Windows-side execution is assessed "
                        "separately from recipient evidence."
                    )

            matching_complete = None
            if index <= len(command_completes):
                matching_complete = command_completes[index - 1]

            command_elapsed = (
                device_duration(start_line, matching_complete)
                if matching_complete
                else None
            )

            if is_metadata:
                report.append(f"Payload metadata: {command_text[2:]}")
                report.append("")
                continue
            else:
                report.append(
                    f"Command {executable_command_number}: {command_text}"
                )
                if command_text.startswith("RunNix "):
                    report.append(
                        f"  Executed Linux command: {command_text.split(None, 1)[1]}"
                    )
            report.append(f"  Mechanism: {mechanism}")
            report.append(f"  Start evidence: {format_device_event(start_line)}")
            report.append(
                f"  Completion evidence: "
                f"{format_device_event(matching_complete) if matching_complete else 'No matching COMMAND_COMPLETE recorded.'}"
            )
            report.append(
                "  Device command duration: "
                + (
                    f"{format_device_elapsed(command_elapsed)} "
                    f"({command_elapsed} ms)"
                    if command_elapsed is not None
                    else "Not calculable from recorded device timestamps."
                )
            )
            report.append(f"  Interpretation: {evidence}")
            report.append("")
    else:
        report.append("(no command-start events recorded)")
        report.append("")

    report.append("EXECUTION TIMELINE")
    report.append("-" * 50)
    report.append(
        "Device time is relative; server time is local wall-clock time. "
        ""
    )
    report.append("")
    report.append("Recipient/server timeline:")
    recipient_timeline = []
    for line in recipient_events:
        if line.startswith("====="):
            continue
        timestamp = line[1:20] if line.startswith("[") else ""
        prefix = f"{timestamp}  " if timestamp else ""
        if "] EVENT: CONNECTED " in line:
            recipient_timeline.append(prefix + "Connection accepted from " + line.split("CONNECTED ", 1)[1])
        elif "] DEVICE -> SERVER:" in line:
            data = line.split("DEVICE -> SERVER:", 1)[1].strip().replace("\\n", "")
            recipient_timeline.append(prefix + "Data received: " + data)
            recipient_timeline.append(prefix + "Data stored in received.log")
        elif "] RECIPIENT: DECODE_COMPLETE" in line:
            recipient_timeline.append(prefix + "Data decoded as UTF-8")
        elif "] RECIPIENT: CLIENT_DISCONNECTED" in line:
            recipient_timeline.append(prefix + "Device disconnected")
        elif "] EVENT: SESSION_SUMMARY " in line:
            recipient_timeline.append(prefix + "Session finalized")
    report.extend(recipient_timeline or ["(no recipient-side events recorded)"])
    report.append("")
    report.append("Device timeline:")
    timeline_entries = [
        format_device_timeline_event(line)
        for line in execution_lines
    ]
    report.extend(
        [line for line in timeline_entries if line]
        if execution_lines
        else ["(no test execution data)"]
    )
    report.append("")

    report.append("RECIPIENT PROCESSING & ACTION ASSESSMENT")
    report.append("-" * 50)

    raw_received_bytes = b""
    for session in sessions:
        received_path = session.parent / "received.log"
        if received_path.exists():
            raw_received_bytes += received_path.read_bytes()

    decoded_received = raw_received_bytes.decode("utf-8", errors="replace")
    literal_text_terminator = ""
    decoded_display = decoded_received

    if decoded_display.endswith("\\r\\n"):
        literal_text_terminator = r"\r\n"
        decoded_display = decoded_display[:-4]
    elif decoded_display.endswith("\\n"):
        literal_text_terminator = r"\n"
        decoded_display = decoded_display[:-2]
    elif decoded_display.endswith("\\r"):
        literal_text_terminator = r"\r"
        decoded_display = decoded_display[:-2]

    decoded_display = decoded_display.strip()

    assessment_connected = sum(
        "] EVENT: CONNECTED " in line for line in recipient_events
    )
    assessment_data_received = sum(
        "] RECIPIENT: DATA_RECEIVED" in line for line in recipient_events
    )
    assessment_raw_written = sum(
        "] RECIPIENT: RAW_WRITE_COMPLETE" in line for line in recipient_events
    )
    assessment_decoded = sum(
        "] RECIPIENT: DECODE_COMPLETE" in line for line in recipient_events
    )
    assessment_disconnected = sum(
        "] EVENT: DISCONNECTED " in line
        or "] RECIPIENT: CLIENT_DISCONNECTED" in line
        for line in recipient_events
    )
    assessment_errors = [
        line for line in recipient_events
        if any(
            marker in line.upper()
            for marker in ("ERROR", "FAILED", "FAILURE", "WARNING")
        )
    ]

    recipient_action_confirmed = bool(
        assessment_data_received
        and assessment_raw_written
        and assessment_decoded
    )
    recipient_response_texts = [
        line.split("SERVER -> DEVICE:", 1)[1].strip().replace("\\n", "")
        for line in server_to_device_events
        if "] SERVER -> DEVICE:" in line
    ]

    if recipient_events or raw_received_bytes:
        report.append(f"TCP peer endpoint: {peer_endpoint}")
        report.append("Connection established: " + ("yes" if assessment_connected else "no"))
        report.append(
            "Decoded content: "
            + (decoded_display if decoded_display else "(no decoded content)")
        )
        if literal_text_terminator:
            report.append(f"Raw text terminator: literal {literal_text_terminator}")
        report.append("Encoding: " + ("UTF-8" if raw_received_bytes else "not established"))
        report.append("Data stored: " + ("yes" if assessment_raw_written else "no"))
        report.append("Connection closed: " + ("yes" if assessment_disconnected else "no"))
        report.append(
            "Errors / warnings: "
            + (str(len(assessment_errors)) + " recorded" if assessment_errors else "none")
        )
        report.append(
            "Recipient action confirmed: "
            + (
                "yes — received data was stored and decoded."
                if recipient_action_confirmed
                else "no — complete receive/store/decode confirmation was not recorded."
            )
        )
        report.append(
            "Recipient response text: "
            + (
                " | ".join(recipient_response_texts)
                if recipient_response_texts
                else "(none recorded)"
            )
        )
    else:
        report.append("No recipient-side connection or received data was recorded.")

    report.append("")

    execution_text = "\n".join(execution_lines)
    has_server_connect = "ServerConnect" in execution_text
    has_shell_nix = "ShellNix " in execution_text
    has_run_nix = "RunNix " in execution_text

    if result_status == "Indeterminate":
        report.append(
            "Device result: indeterminate — PAYLOAD_START was not recorded, "
            "so completion cannot be determined from associated device evidence."
        )
        report.append(
            "Listener result: "
            + (
                "recipient/server evidence exists, but it does not determine "
                "device test completion."
                if assessment_connected or assessment_data_received
                else
                "no listener-side data recorded."
            )
        )
    elif result_status == "Incomplete":
        report.append(
            "Device result: incomplete — PAYLOAD_START was recorded but "
            "PAYLOAD_COMPLETE was not recorded."
        )
        report.append(
            "Listener result: "
            + (
                "connection/data evidence recorded before the device test ended."
                if assessment_connected or assessment_data_received
                else
                "no listener-side data recorded."
            )
        )
    elif has_shell_nix:
        report.append("Device result: ShellNix action and payload completed.")
        report.append(
            "Listener result: "
            + (
                "connection accepted and returned data received."
                if raw_received_bytes and assessment_connected and assessment_data_received
                else
                "no returned data recorded."
            )
        )
    elif has_run_nix and not has_server_connect:
        report.append("Device result: RunNix action and payload completed.")
        report.append("Listener result: no listener-side data recorded.")
    elif has_server_connect:
        report.append("Device result: ServerConnect action and payload completed.")
        report.append(
            "Listener result: "
            + (
                "connection accepted and data received."
                if assessment_connected and assessment_data_received
                else
                "no connection/data confirmation recorded."
            )
        )
    else:
        report.append("Device result: payload completion recorded.")
        report.append("Listener result: no listener-side data recorded.")

    report.append(
        "Recipient session: "
        + (
            "closed and finalized."
            if assessment_disconnected
            else
            "finalization not recorded."
            if recipient_events
            else
            "no recipient session recorded."
        )
    )

    report.append(
        "Listener-to-device data: "
        + (
            f"{len(server_to_device_events)} transmission(s) recorded."
            if server_to_device_events
            else
            "none recorded."
        )
    )
    report.append("")

    report.append("EVIDENCE / ARCHIVE CONTENTS")
    report.append("-" * 50)
    report.append("- REPORT.txt — combined readable report")
    report.append("- README.txt — archive index")
    if server_text:
        report.append("- server-session.log — recipient/server session evidence")
    report.append("- device-payload-execution.log — isolated device payload history")
    if received_text:
        report.append(
            "- received.log — combined readable compatibility view; "
            "authoritative byte-exact data is preserved under recipient-sessions/"
        )
    if preserved_session_artifacts:
        report.append(
            "- recipient-sessions/ — authoritative byte-exact per-session evidence"
        )
        for relative_path, description in preserved_session_artifacts:
            report.append(f"  - {relative_path} — {description}")

    (dest / "REPORT.txt").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8"
    )

    files = sorted(
        {
            path.relative_to(dest).as_posix()
            for path in dest.rglob("*")
            if path.is_file()
        }
        | {"README.txt"}
    )

    readme = [
        "EVIL CROW CABLE WIND SESSION ARCHIVE",
        "=" * 40,
        f"Created: {datetime.now().strftime("%B %-d, %Y at %-I:%M:%S %p")}",
        (
            f"Source sessions: {', '.join(session.parent.name for session in sessions)}"
            if sessions
            else "Source sessions: none — no recipient/server session created"
        ),
        "",
        "Open REPORT.txt for the combined readable record.",
        "",
        "Archive files:",
    ]

    readme.extend(f"- {name}" for name in files)

    (dest / "README.txt").write_text(
        "\n".join(readme) + "\n",
        encoding="utf-8"
    )

    print(f"Archive created: {dest}")
    print(f"Combined report: {dest / 'REPORT.txt'}")

if __name__ == "__main__":
    main()
