#!/usr/bin/env python3

import os
import subprocess
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "server"
ARCHIVES = BASE / "archives"
LOGS = BASE / "logs"
LAUNCHER = BASE / "evilcrow-master.sh"


def open_path(path):
    path = Path(path)
    if not path.exists():
        messagebox.showerror("Evil Crow", f"Not found:\n{path}")
        return
    subprocess.Popen(["xdg-open", str(path)])


def start_listener():
    if not LAUNCHER.exists():
        messagebox.showerror("Evil Crow", f"Listener launcher not found:\n{LAUNCHER}")
        return

    subprocess.Popen(
        ["x-terminal-emulator", "-e", str(LAUNCHER)],
        cwd=str(BASE)
    )


def show_files(title, directory, pattern):
    window = tk.Toplevel(root)
    window.title(title)
    window.geometry("760x500")
    window.configure(bg=BG)

    tk.Label(
        window,
        text=title,
        font=("Sans", 16, "bold"),
        fg=TEXT,
        bg=BG
    ).pack(pady=(12, 4))

    tk.Label(
        window,
        text=str(directory),
        font=("Sans", 9),
        fg=MUTED,
        bg=BG
    ).pack(pady=(0, 10))

    frame = tk.Frame(window, bg=BG)
    frame.pack(fill="both", expand=True, padx=15, pady=5)

    scrollbar = tk.Scrollbar(frame)
    scrollbar.pack(side="right", fill="y")

    listbox = tk.Listbox(
        frame,
        font=("Monospace", 11),
        bg=ENTRY_BG,
        fg=TEXT,
        selectbackground=RED,
        selectforeground=TEXT,
        yscrollcommand=scrollbar.set,
        relief="flat",
        bd=0
    )
    listbox.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=listbox.yview)

    if directory.exists():
        files = sorted(
            directory.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
    else:
        files = []

    for item in files:
        listbox.insert("end", item.name)

    def open_selected(event=None):
        selection = listbox.curselection()
        if not selection:
            return
        selected = files[selection[0]]
        open_path(selected)

    listbox.bind("<Double-Button-1>", open_selected)

    button_frame = tk.Frame(window, bg=BG)
    button_frame.pack(pady=12)

    for button_text, command, width in [
        ("Open Selected", open_selected, 18),
        ("Open Folder", lambda: open_path(directory), 18),
        ("Close", window.destroy, 12),
    ]:
        tk.Button(
            button_frame,
            text=button_text,
            width=width,
            command=command,
            bg=PANEL,
            fg=TEXT,
            activebackground=RED_DARK,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            cursor="hand2"
        ).pack(side="left", padx=5)


root = tk.Tk()
root.title("Evil Crow Cable Wind")
root.geometry("620x560")
root.resizable(False, False)

BG = "#171717"
PANEL = "#222222"
RED = "#8B0000"
RED_DARK = "#650000"
TEXT = "#F0F0F0"
MUTED = "#B8B8B8"
ENTRY_BG = "#2B2B2B"

root.configure(bg=BG)

tk.Label(
    root,
    text="EVIL CROW",
    font=("Sans", 23, "bold"),
    fg=TEXT,
    bg=BG
).pack(pady=(22, 0))

tk.Label(
    root,
    text="CABLE WIND",
    font=("Sans", 13, "bold"),
    fg=RED,
    bg=BG
).pack(pady=(0, 3))

tk.Label(
    root,
    text="CONTROL CENTER",
    font=("Sans", 11),
    fg=MUTED,
    bg=BG
).pack(pady=(0, 22))

tk.Frame(
    root,
    height=3,
    bg=RED
).pack(fill="x", padx=70, pady=(0, 15))

button_frame = tk.Frame(root, bg=BG)
button_frame.pack(fill="x", padx=70)

buttons = [
    ("Start Listener", start_listener),
    ("View Reports", lambda: show_files(
        "Evil Crow Reports",
        ARCHIVES,
        "**/REPORT.txt"
    )),
    ("View Logs", lambda: show_files(
        "Evil Crow Logs",
        LOGS,
        "*.log"
    )),
    ("View Received Data", lambda: show_files(
        "Received Data",
        LOGS,
        "received-*.log"
    )),
    ("Open Archives Folder", lambda: open_path(ARCHIVES)),
    ("Open Evil Crow Folder", lambda: open_path(BASE)),
]

for text, command in buttons:
    is_listener = text == "Start Listener"
    tk.Button(
        button_frame,
        text=text,
        command=command,
        height=2,
        font=("Sans", 11, "bold" if is_listener else "normal"),
        bg=RED if is_listener else PANEL,
        fg=TEXT,
        activebackground=RED_DARK,
        activeforeground=TEXT,
        relief="flat",
        bd=0,
        cursor="hand2"
    ).pack(fill="x", pady=5)

tk.Frame(
    root,
    height=2,
    bg=RED_DARK
).pack(fill="x", padx=70, pady=(15, 10))

tk.Button(
    root,
    text="Close",
    command=root.destroy,
    width=18,
    height=2,
    font=("Sans", 10),
    bg=PANEL,
    fg=MUTED,
    activebackground=RED_DARK,
    activeforeground=TEXT,
    relief="flat",
    bd=0,
    cursor="hand2"
).pack(pady=10)

root.mainloop()
