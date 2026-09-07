import tkinter as tk
from tkinter import ttk
from pathlib import Path
import urllib.request
import subprocess
import threading
import time

BASE = Path.home() / "EvilCrow-Server"
LOGS = BASE / "logs"
MAC = __import__("os").environ.get("EVILCROW_MAC", "").strip().lower()

root = tk.Tk()
root.title("Evil Crow — Payload Execution")
root.geometry("1200x750")

frame = ttk.Frame(root, padding=10)
frame.pack(fill="both", expand=True)

text = tk.Text(
    frame,
    wrap="none",
    font=("DejaVu Sans Mono", 10),
    state="disabled"
)
text.pack(side="left", fill="both", expand=True)

scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
scrollbar.pack(side="right", fill="y")
text.configure(yscrollcommand=scrollbar.set)

status = ttk.Label(root, text="Waiting for an Evil Crow session...")
status.pack(fill="x", padx=10, pady=(0, 10))

current_log = None
server_position = 0
payload_seen = set()
device_ip = None


def find_device():
    try:
        result = subprocess.run(
            ["sudo", "arp-scan", "--localnet"],
            capture_output=True,
            text=True,
            timeout=10
        )

        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1].lower() == MAC:
                return fields[0]
    except Exception:
        pass

    return None


def find_latest_session():
    sessions = sorted(
        LOGS.glob("session-*/session.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    return sessions[0] if sessions else None


def add_line(line):
    text.configure(state="normal")
    text.insert("end", line.rstrip("\n") + "\n")
    text.see("end")
    text.configure(state="disabled")


def read_server_log():
    global current_log, server_position

    latest = find_latest_session()

    if latest != current_log:
        
        add_line("==========NEW SERVER SESSION=========")
        current_log = latest
        server_position = 0

        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")

        if current_log:
            add_line("========== SERVER SESSION ==========")
            add_line(f"Session: {current_log.parent.name}")

    if current_log and current_log.exists():
        try:
            with open(
                current_log,
                "r",
                encoding="utf-8",
                errors="replace"
            ) as f:
                f.seek(server_position)
                data = f.read()
                server_position = f.tell()

            if data:
                for line in data.splitlines():
                    add_line(line)
        except OSError:
            pass


def read_payload_log():
    global device_ip

    if not device_ip:
        device_ip = find_device()
        if not device_ip:
            return

    try:
        url = f"http://{device_ip}/payloadlog"

        with urllib.request.urlopen(url, timeout=3) as response:
            data = response.read().decode(
                "utf-8",
                errors="replace"
            )

        for line in data.splitlines():
            if line and line not in payload_seen:
                payload_seen.add(line)

                add_line(
                    f"[DEVICE PAYLOAD] {line}"
                )

    except Exception:
        pass


def update():
    read_server_log()
    read_payload_log()

    if current_log:
        status.config(
            text=f"Live session: {current_log.parent.name}"
        )
    elif device_ip:
        status.config(
            text=f"Evil Crow found at {device_ip} — waiting for server session..."
        )
    else:
        status.config(
            text="Searching for Evil Crow..."
        )

    root.after(1000, update)


def close():
    root.destroy()


root.protocol("WM_DELETE_WINDOW", close)

update()
root.mainloop()
