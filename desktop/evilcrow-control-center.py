#!/usr/bin/env python3

import os
import subprocess
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ARCHIVES = BASE / "archives"
SESSIONS = BASE / "sessions"
LAUNCHER = BASE / "src" / "backend" / "evilcrow-master.sh"
ICON = BASE / "assets" / "evil-crow-icon.png"


def open_path(path):
    path = Path(path)
    if not path.exists():
        messagebox.showerror("Evil Crow", f"Not found:\n{path}")
        return

    if path.is_file() and path.suffix.lower() in (".txt", ".log"):
        subprocess.Popen(["mousepad", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def start_listener():
    if not LAUNCHER.exists():
        messagebox.showerror("Evil Crow", f"Listener launcher not found:\n{LAUNCHER}")
        return

    subprocess.Popen(
        ["x-terminal-emulator", "-e", str(LAUNCHER)],
        cwd=str(BASE),
        start_new_session=True
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
        if item.name == "REPORT.txt":
            archive_name = item.parent.name
            stamp = archive_name.removeprefix("archive-")
            if "_" in stamp:
                date_part, time_part = stamp.split("_", 1)
                display_name = f"Report — {date_part} {time_part.replace('-', ':')}"
            else:
                display_name = f"Report — {stamp}"
        elif item.name in ("session.log", "received.log"):
            display_name = item.parent.name
        else:
            display_name = item.name

        listbox.insert("end", display_name)

    def open_selected(event=None):
        selection = listbox.curselection()
        if not selection:
            return
        selected = files[selection[0]]
        open_path(selected)

    def delete_selected():
        selection = listbox.curselection()
        if not selection:
            messagebox.showinfo("Evil Crow", "Select an item to delete.")
            return

        selected = files[selection[0]]

        if selected.name == "REPORT.txt":
            target = selected.parent
            description = f"archive folder:\n{target}"
        else:
            target = selected
            description = f"file:\n{target}"

        confirmed = messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete this {description}?\n\n"
            "This action cannot be undone."
        )
        if not confirmed:
            return

        try:
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()

            files.pop(selection[0])
            listbox.delete(selection[0])

        except Exception as e:
            messagebox.showerror(
                "Evil Crow",
                f"Could not delete:\n{target}\n\n{e}"
            )

    listbox.bind("<Double-Button-1>", open_selected)

    button_frame = tk.Frame(window, bg=BG)
    button_frame.pack(pady=12)

    buttons = [
        ("Open Selected", open_selected, 18, PANEL),
        ("Open Folder", lambda: open_path(directory), 18, PANEL),
        ("Delete Selected", delete_selected, 18, RED),
        ("Close", window.destroy, 12, PANEL),
    ]

    for button_text, command, width, button_bg in buttons:
        tk.Button(
            button_frame,
            text=button_text,
            width=width,
            command=command,
            bg=button_bg,
            fg=TEXT,
            activebackground=RED_DARK,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            cursor="hand2"
        ).pack(side="left", padx=5)


root = tk.Tk()
root.title("Evil Crow Cable Wind")
root.geometry("620x690")
root.resizable(False, False)

BG = "#171717"
PANEL = "#222222"
RED = "#8B0000"
RED_DARK = "#650000"
TEXT = "#F0F0F0"
MUTED = "#B8B8B8"
ENTRY_BG = "#2B2B2B"

root.configure(bg=BG)

if ICON.exists():
    crow_icon = tk.PhotoImage(file=str(ICON))
    root.iconphoto(True, crow_icon)
    tk.Label(
        root,
        image=crow_icon,
        bg=BG
    ).pack(pady=(10, 2))

tk.Label(
    root,
    text="EVIL CROW",
    font=("Sans", 23, "bold"),
    fg=TEXT,
    bg=BG
).pack(pady=(10, 0))

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
).pack(pady=(0, 12))

tk.Frame(
    root,
    height=3,
    bg=RED
).pack(fill="x", padx=70, pady=(0, 10))

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
        SESSIONS,
        "**/session.log"
    )),
    ("View Received Data", lambda: show_files(
        "Received Data",
        SESSIONS,
        "**/received.log"
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
    ).pack(fill="x", pady=4)

tk.Frame(
    root,
    height=2,
    bg=RED_DARK
).pack(fill="x", padx=70, pady=(10, 6))



root.mainloop()
