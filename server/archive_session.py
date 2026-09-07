#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime
import urllib.request
import subprocess
import os

BASE = Path.home() / "EvilCrow-Server"
LOGS = BASE / "logs"
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

    for session in sessions:
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

    recipient_os_values = []
    for session in sessions:
        summary = session.parent / "SESSION_SUMMARY.txt"
        recipient_os = "Not recorded"
        if summary.exists():
            for line in summary.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.startswith("Recipient OS:"):
                    value = line.split(":", 1)[1].strip()
                    recipient_os = value or "Not recorded"
                    break
        recipient_os_values.append(recipient_os)

    unique_recipient_os = list(dict.fromkeys(recipient_os_values))
    recipient_os = ", ".join(unique_recipient_os) if unique_recipient_os else "Not recorded"

    (dest / "server-session.log").write_text(
        server_text + ("\n" if server_text else ""),
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

    # Automatically discover the Evil Crow by its MAC address.
    result = subprocess.run(
        ["sudo", "arp-scan", "--localnet"],
        capture_output=True,
        text=True,
        check=False
    )

    device_ip = None
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].lower() == os.environ.get("EVILCROW_MAC", "").strip().lower():
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
    report.append("Device timestamps: TIME_UNSYNCED (device clock not synchronized)")
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
        device_elapsed_from_line(payload_starts[-1])
        if payload_starts
        else None
    )

    def format_device_event(line):
        """Show raw device evidence plus payload-relative elapsed time."""
        milliseconds = device_elapsed_from_line(line)
        if milliseconds is None:
            return line

        if payload_start_ms is not None:
            relative_ms = milliseconds - payload_start_ms
            return (
                f"{line} "
                f"[payload elapsed: +{format_device_elapsed(relative_ms)} "
                f"| device counter: {milliseconds} ms]"
            )

        return (
            f"{line} "
            f"[device counter: {milliseconds} ms]"
        )

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

    recipient_endpoints = []
    recipient_ips = []
    for session in sessions:
        summary = session.parent / "SESSION_SUMMARY.txt"
        if summary.exists():
            for line in summary.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.startswith("Remote endpoint:"):
                    endpoint = line.split(":", 1)[1].strip()
                    if endpoint and endpoint != "UNKNOWN":
                        recipient_endpoints.append(endpoint)
                        recipient_ips.append(endpoint.rsplit(":", 1)[0])
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
    recipient_os_from_context = context.get("RECIPIENT_OS", "Not recorded")
    payload_transport = context.get(
        "PAYLOAD_TRANSPORT", "Not recorded"
    )

    # Preserve the actual observed TCP endpoint when a recipient session
    # exists. Otherwise explicitly report that no recipient endpoint was
    # observed instead of using historical session data.
    recipient_endpoint = ", ".join(
        dict.fromkeys(recipient_endpoints)
    ) if recipient_endpoints else "No TCP recipient endpoint observed"

    # If the payload itself requested ServerConnect, retain that target
    # as additional device-side evidence.
    server_target = "Not requested"
    if server_connect_commands:
        server_target = server_connect_commands[0].split()[-1]
        if ":" not in server_target:
            server_target = server_target + ":4444"

    result_status = "Completed" if payload_completes else "Not completed"
    command_count = "Not recorded"
    if payload_completes and "commands=" in payload_completes[-1]:
        command_count = payload_completes[-1].split("commands=", 1)[1].strip()

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
    report.append(
        "Payload result: "
        + ("PAYLOAD_COMPLETE recorded" if payload_completes
           else "PAYLOAD_COMPLETE not recorded")
    )
    payload_step_count = len(command_starts)
    timing_control_count = sum(
        1 for line in command_starts
        if line.split("|", 3)[-1].strip().startswith(("Delay ", "Release"))
    )
    action_step_count = payload_step_count - timing_control_count

    report.append(f"Commands processed by device: {command_count}")
    report.append(f"Payload steps recorded: {payload_step_count}")
    report.append(f"Action steps: {action_step_count}")
    report.append(f"Timing/control steps: {timing_control_count}")
    report.append("")

    report.append("ENDPOINT & CONNECTION INFORMATION")
    report.append("-" * 50)
    report.append(f"Evil Crow IP: {device_ip}")
    report.append(f"Evil Crow MAC: {device_mac}")
    report.append(f"Recipient / Server IP: {server_ip}")
    report.append(f"Recipient OS: {recipient_os_from_context}")
    report.append(f"Recipient port: {server_port}")
    report.append(f"Payload transport: {payload_transport}")
    report.append(f"Requested server target: {server_target}")
    report.append(f"Observed recipient endpoint: {recipient_endpoint}")
    report.append(f"Connection result: {connection_result}")
    report.append(f"Connection state: {connection_state}")
    report.append("")

    report.append("PAYLOAD DEPLOYMENT & EXECUTION MECHANISM")
    report.append("-" * 50)
    report.append(f"Deployment transport: {payload_transport}")
    report.append(
        "Recipient interaction mechanism: "
        "USB HID keyboard emulation is used for payload actions that "
        "type commands or invoke recipient-side interfaces."
    )
    report.append(
        "Device-side processing: the Evil Crow processes the payload "
        "and records PAYLOAD_START, COMMAND_START/COMMAND_COMPLETE, "
        "and PAYLOAD_COMPLETE events."
    )
    report.append(
        "Recipient-side confirmation: device completion events establish "
        "that the Evil Crow completed its HID routine, but recipient-side "
        "application or command success is reported separately and only "
        "when supported by recipient/server evidence."
    )
    report.append("")

    report.append("PAYLOAD EXECUTION SUMMARY")
    report.append("-" * 50)
    report.append(
        f"Payload start: "
        f"{format_device_event(payload_starts[-1]) if payload_starts else 'Not recorded'}"
    )
    report.append(f"Command start events: {len(command_starts)}")
    report.append(f"Command completion events: {len(command_completes)}")
    report.append(
        f"Payload complete: "
        f"{format_device_event(payload_completes[-1]) if payload_completes else 'Not recorded'}"
    )

    payload_duration = None
    if payload_starts and payload_completes:
        payload_duration = device_duration(
            payload_starts[-1],
            payload_completes[-1]
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
                        "Linux command shown above. Recipient-side application "
                        "launch is confirmed separately when supported by the "
                        "observed recipient test."
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
            else:
                report.append(
                    f"Command {executable_command_number}: {command_text}"
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
        "Device and recipient/server clocks are not synchronized; "
        "cross-clock ordering is not asserted."
    )
    report.append("")
    report.append("Recipient/server wall-clock events:")
    report.extend(
        recipient_events or ["(no recipient-side events recorded)"]
    )
    report.append("")
    report.append("Device event sequence (TIME_UNSYNCED):")
    report.extend(
        [format_device_event(line) for line in execution_lines]
        if payload_lines
        else ["(no payload execution data)"]
    )
    report.append("")

    report.append("DEVICE PAYLOAD EVENT HISTORY")
    report.append("-" * 50)
    report.append(
        payload_text.rstrip() or "(no payload execution data)"
    )
    report.append("")

    report.append("RECIPIENT-SIDE OBSERVATIONS")
    report.append("-" * 50)

    if recipient_events:
        connected = sum(
            "] EVENT: CONNECTED " in line for line in recipient_events
        )
        receive_started = sum(
            "] RECIPIENT: RECEIVE_LOOP_STARTED" in line
            for line in recipient_events
        )
        data_received = sum(
            "] RECIPIENT: DATA_RECEIVED" in line
            for line in recipient_events
        )
        raw_written = sum(
            "] RECIPIENT: RAW_WRITE_COMPLETE" in line
            for line in recipient_events
        )
        decoded = sum(
            "] RECIPIENT: DECODE_COMPLETE" in line
            for line in recipient_events
        )
        disconnected = sum(
            "] EVENT: DISCONNECTED " in line
            for line in recipient_events
        )
        client_disconnected = sum(
            "] RECIPIENT: CLIENT_DISCONNECTED" in line
            for line in recipient_events
        )
        loop_ended = sum(
            "] RECIPIENT: RECEIVE_LOOP_ENDED" in line
            for line in recipient_events
        )
        summaries = sum(
            "] EVENT: SESSION_SUMMARY " in line
            for line in recipient_events
        )
        error_lines = [
            line for line in recipient_events
            if any(
                marker in line.upper()
                for marker in ("ERROR", "FAILED", "FAILURE", "WARNING")
            )
        ]

        report.append(f"Connections established: {connected}")
        report.append(f"Receive loops started: {receive_started}")
        report.append(f"Data-received events: {data_received}")
        report.append(f"Raw-write-complete events: {raw_written}")
        report.append(f"Decode-complete events: {decoded}")
        report.append(f"Client disconnect events: {client_disconnected}")
        report.append(f"Session disconnect events: {disconnected}")
        report.append(f"Receive loops ended: {loop_ended}")
        report.append(f"Session summaries recorded: {summaries}")
        report.append(
            f"Errors / warnings observed: {len(error_lines)}"
            if error_lines
            else "Errors / warnings observed: none in recorded recipient events."
        )
        report.append(
            "Observation: This section reflects only events actually "
            "recorded by the recipient/server. It does not infer activity "
            "that was not observed or logged."
        )
    else:
        report.append("No recipient-side events were recorded.")

    report.append("")

    report.append("RECIPIENT PROCESSING & ACTION ASSESSMENT")
    report.append("-" * 50)

    raw_received_bytes = b""
    for session in sessions:
        received_path = session.parent / "received.log"
        if received_path.exists():
            raw_received_bytes += received_path.read_bytes()

    decoded_received = raw_received_bytes.decode("utf-8", errors="replace")
    decoded_display = decoded_received.replace("\\r\\n", "\\n").replace("\\r", "\\n").strip()

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

    report.append(
        "Recipient acceptance: "
        + (
            "CONNECTION/DATA ACCEPTED — the recipient accepted the TCP connection "
            "and recorded incoming data. Application-level acceptance of the "
            "payload itself is not established by this evidence."
            if assessment_connected and assessment_data_received
            else
            "PARTIAL — recipient-side evidence does not show both a "
            "successful connection and received data."
            if assessment_connected or assessment_data_received
            else
            "NOT APPLICABLE — this payload did not request a TCP recipient connection; it used the device HID mechanism." if "ServerConnect" not in "\n".join(execution_lines) else "NOT CONFIRMED — no recipient connection/data-receipt evidence recorded."
        )
    )

    if raw_received_bytes:
        report.append(f"Received payload/data size: {len(raw_received_bytes)} bytes")
        report.append(
            f"Recipient data-receipt events: {assessment_data_received}; "
            f"raw-write events: {assessment_raw_written}; "
            f"decode events: {assessment_decoded}."
        )
        report.append(
            "Decode result: UTF-8 decode completed with replacement handling "
            "for any invalid byte sequences."
        )
        report.append("Decoded payload/data:")
        report.append(
            decoded_display if decoded_display else "(decoded data is empty)"
        )

        if "EVILCROW-" in decoded_display.upper():
            report.append(
                "Content classification: Evil Crow test/marker text detected."
            )
        elif any(
            token in decoded_display.lower()
            for token in ("powershell", "cmd.exe", "bash ", "sh ", "python ", "sudo ")
        ):
            report.append(
                "Content classification: command/shell-like text detected."
            )
        elif decoded_display:
            report.append(
                "Content classification: ordinary decoded text; "
                "no specific command format identified."
            )
        else:
            report.append(
                "Content classification: no printable decoded content identified."
            )

        report.append(
            "Recipient processing observed: received bytes were stored in "
            "received.log, decoded by the server, and displayed by the "
            "recipient/server session."
        )
    else:
        report.append("Received payload/data size: 0 bytes")
        report.append("Decode result: no received data was available to decode.")
        report.append("Decoded payload/data: (none)")
        report.append("Content classification: none.")
        report.append(
            "Recipient processing observed: no received payload/data was "
            "available for processing."
        )

    if assessment_errors:
        report.append(
            f"Recipient-side errors/warnings: {len(assessment_errors)} recorded."
        )
        report.extend(
            f"  {line}" for line in assessment_errors
        )
    else:
        report.append(
            "Recipient-side errors/warnings: NONE recorded in the "
            "recipient/server session."
        )

    report.append(
        "Recipient response/result: "
        + (
            "the recipient accepted the connection, received the data, "
            "stored it, decoded it, and displayed the decoded content; "
            "the recorded session then reached its disconnect/finalization events."
            if raw_received_bytes and assessment_connected and assessment_data_received
            else
            "recipient-side response could not be fully established from "
            "the recorded evidence."
        )
    )

    execution_text = "\n".join(execution_lines)
    has_server_connect = "ServerConnect" in execution_text
    has_shell_nix = "ShellNix " in execution_text
    has_run_nix = "RunNix " in execution_text

    if has_shell_nix:
        report.append(
            "Recipient-side execution status: DEVICE-SIDE SHELL BRIDGE "
            "SETUP COMPLETION CONFIRMED. The Evil Crow recorded "
            "COMMAND_COMPLETE for the ShellNix command and subsequently "
            "recorded PAYLOAD_COMPLETE. The ShellNix routine opens a "
            "recipient terminal and establishes a bidirectional shell "
            "bridge through the device USB serial interface and Wi-Fi "
            "TCP connection."
        )
        if raw_received_bytes and assessment_connected and assessment_data_received:
            report.append(
                "Recipient-side execution evidence: recipient/server "
                "evidence shows a TCP connection was accepted and data "
                "was received, stored, decoded, and displayed. This "
                "supports active bidirectional shell communication, but "
                "the recorded server code does not independently identify "
                "which recipient-side shell command produced each received "
                "response."
            )
        else:
            report.append(
                "Recipient-side execution evidence: no recipient/server "
                "data receipt sufficient to confirm a returned shell "
                "response was recorded."
            )
        report.append(
            "Recipient-side resulting actions: the ShellNix command "
            "instructed the device HID mechanism to open a terminal and "
            "type the shell-bridge command on the Kali recipient."
        )
    elif has_run_nix:
        report.append(
            "Recipient-side execution status: DEVICE-SIDE HID COMPLETION "
            "CONFIRMED. The Evil Crow recorded COMMAND_COMPLETE for the "
            "RunNix command and subsequently recorded PAYLOAD_COMPLETE. "
            "This confirms completion of the device-side HID routine; it "
            "does not independently prove that Firefox launched or that "
            "the YouTube page loaded successfully on the recipient."
        )
        report.append(
            "Recipient-side resulting actions: the RunNix command instructed "
            "the device HID mechanism to open a terminal and type the "
            "specified Linux command on the Kali recipient."
        )
        report.append(
            "Direct recipient observation: this field is reserved for "
            "manually documented recipient-side observations. No specific "
            "real-world test result is embedded in the public source."
        )
    elif not has_server_connect:
        report.append(
            "Recipient-side execution status: DEVICE-SIDE COMPLETION "
            "CONFIRMED. The Evil Crow recorded the command lifecycle and "
            "PAYLOAD_COMPLETE. Recipient-side application or command "
            "success is not independently established by the recorded "
            "evidence."
        )
        report.append(
            "Recipient-side resulting actions: the recorded payload "
            "completed its device-side action; no additional recipient-side "
            "result is asserted without supporting evidence."
        )
    else:
        report.append(
            "Recipient-side execution status: NOT OBSERVED. "
            "The recorded recipient/server code receives, stores, decodes, "
            "and displays incoming data; it does not execute received data "
            "as a local command."
        )
        report.append(
            "Recipient-side resulting actions: no additional recipient action "
            "was observed beyond the recorded receive/store/decode/display "
            "processing."
        )

    report.append(
        "Recipient session final state: "
        + (
            "disconnect/finalization events recorded."
            if assessment_disconnected
            else
            "no recipient disconnect/finalization event recorded."
        )
    )

    report.append(
        "Recipient/server -> device commands sent during session: "
        + str(len(server_to_device_events))
    )

    if server_to_device_events:
        report.append("Commands/data sent from recipient/server to device:")
        report.extend(server_to_device_events)
    else:
        report.append(
            "Commands/data sent from recipient/server to device: none recorded."
        )

    report.append(
        "Final assessment: The report distinguishes confirmed recipient "
        "acceptance and processing from unobserved behavior. Receipt of "
        "payload/data alone is not treated as proof of recipient-side "
        "execution. Any recipient error, response, or action is reported "
        "only when supported by recorded session evidence."
    )
    report.append("")

    report.append("-" * 50)

    report.append("RECIPIENT-SIDE PLAY-BY-PLAY")
    report.append("-" * 50)
    report.append(
        "\n".join(recipient_events)
        if recipient_events
        else "(no recipient-side events recorded)"
    )
    report.append("")

    report.append("RAW RECEIVED DATA")
    report.append("-" * 50)
    report.append(
        received_text.rstrip() or "(no raw received data)"
    )
    report.append("")

    report.append("EVIDENCE / ARCHIVE CONTENTS")
    report.append("-" * 50)
    report.append("- REPORT.txt — combined readable report")
    report.append("- README.txt — archive index")
    report.append("- server-session.log — recipient/server session evidence")
    report.append("- device-payload-execution.log — isolated device payload history")
    if received_text:
        report.append("- received.log — raw received server data")

    (dest / "REPORT.txt").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8"
    )

    files = sorted(p.name for p in dest.iterdir())

    readme = [
        "EVIL CROW CABLE WIND SESSION ARCHIVE",
        "=" * 40,
        f"Created: {datetime.now().strftime("%B %-d, %Y at %-I:%M:%S %p")}",
        f"Source sessions: {", ".join(session.parent.name for session in sessions)}",
        "",
        "Open REPORT.txt for the combined readable record.",
        "",
        "Raw files:",
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
