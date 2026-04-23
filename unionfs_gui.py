#!/usr/bin/env python3
"""
Mini-UnionFS GUI
================
A terminal-aesthetic GUI for managing and visualising a Mini-UnionFS mount.

Layout
------
Left panel : tri-pane file browser (Lower | Union | Upper)
Right panel : log console + action buttons
Bottom bar  : status / mount info
"""

import os
import sys
import shutil
import subprocess
import threading
import time
import argparse
import platform
import faulthandler
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from datetime import datetime

# Enable traceback dumps on segfaults / fatal errors (best-effort).
faulthandler.enable(all_threads=True)

# ─────────────────────────── Config ──────────────────────────────

IS_LINUX = platform.system() == "Linux"

# Fonts: use Tk named fonts on Linux for stability.
if IS_LINUX:
    FONT_MONO  = ("TkFixedFont", 10)
    FONT_TITLE = ("TkFixedFont", 12, "bold")
    FONT_HEAD  = ("TkFixedFont", 11, "bold")
    FONT_SMALL = ("TkFixedFont", 9)
else:
    FONT_MONO  = ("Courier New", 10)
    FONT_TITLE = ("Courier New", 12, "bold")
    FONT_HEAD  = ("Courier New", 11, "bold")
    FONT_SMALL = ("Courier New", 9)

BG         = "#0d1117"
BG2        = "#161b22"
BG3        = "#21262d"
ACCENT     = "#58a6ff"
ACCENT2    = "#3fb950"
WARN       = "#f0883e"
ERR        = "#f85149"
MUTED      = "#6e7681"
FG         = "#e6edf3"
FG2        = "#c9d1d9"
SEL_BG     = "#1f6feb"

WH_BG      = "#2d1b1b"   # tint for whiteout rows
WH_FG      = "#f85149"
UP_BG      = "#1b2d1b"   # tint for upper-layer rows
UP_FG      = "#3fb950"
LO_FG      = "#58a6ff"

BINARY     = "./mini_unionfs"

# Runtime toggles (set via CLI args in main())
# Default: disable emoji on Linux because some Tk builds segfault
# when rendering emoji/variation-selector glyphs in widgets.
USE_EMOJI  = not IS_LINUX
USE_THEME  = True

# ─────────────────────────── Helpers ─────────────────────────────

def ts():
    return datetime.now().strftime("%H:%M:%S")

def is_wh(name):
    return name.startswith(".wh.")

def wh_target(name):
    """'.wh.foo.txt' → 'foo.txt'"""
    return name[4:] if is_wh(name) else name

def human_size(path):
    try:
        s = os.path.getsize(path)
        top.minsize(420, 260)
        for unit in ["B", "KB", "MB", "GB"]:
            if s < 1024:
                return f"{s:.0f} {unit}"
            s /= 1024
        return f"{s:.1f} TB"
    except Exception:
        return "?"

def file_icon(path, name):
    if not USE_EMOJI:
        if is_wh(name):
            return "[X]"
        if os.path.isdir(path):
            return "[D]"
        return "[F]"
    if is_wh(name):
        return "🚫"
    if os.path.isdir(path):
        return "📁"
    ext = os.path.splitext(name)[1].lower()
    icons = {".py": "🐍", ".sh": "📜", ".c": "⚙️", ".h": "⚙️",
             ".txt": "📄", ".md": "📝", ".json": "📋", ".log": "📋"}
    return icons.get(ext, "📄")


# ──────────────────────── Main Application ───────────────────────

class UnionFSGUI:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Mini-UnionFS Explorer")
        self.root.configure(bg=BG)
        self.root.geometry("1280x800")
        self.root.minsize(960, 640)

        # Single ttk Style instance (avoids repeated theme init)
        self._style = ttk.Style()
        if USE_THEME:
            try:
                self._style.theme_use("clam")
            except Exception:
                # Theme setup should never hard-fail; some minimal Tk builds
                # behave oddly here.
                pass

        # State
        self.lower_dir  = tk.StringVar(value="")
        self.upper_dir  = tk.StringVar(value="")
        self.mount_dir  = tk.StringVar(value="")
        self.cur_path   = "/"          # current relative path in union view
        self.mount_proc = None
        self.mounted    = False
        self.auto_refresh = tk.BooleanVar(value=True)
        self._refresh_job = None

        self._build_ui()
        self._log(f"Mini-UnionFS GUI ready — {ts()}", "info")
        self._log("Set up directories and click  [Mount]  to begin.", "muted")

    # ─────────────────────── UI Construction ──────────────────────

    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

    def _build_menu(self):
        mb = tk.Menu(self.root, bg=BG2, fg=FG, activebackground=SEL_BG,
                     activeforeground=FG, tearoff=0)
        self.root.config(menu=mb)

        fm = tk.Menu(mb, bg=BG2, fg=FG, activebackground=SEL_BG,
                     activeforeground=FG, tearoff=0)
        mb.add_cascade(label="File", menu=fm)
        fm.add_command(label="Quick Setup (auto-create dirs)",
                       command=self._quick_setup)
        fm.add_separator()
        fm.add_command(label="Exit", command=self._on_exit)

        vm = tk.Menu(mb, bg=BG2, fg=FG, activebackground=SEL_BG,
                     activeforeground=FG, tearoff=0)
        mb.add_cascade(label="View", menu=vm)
        vm.add_checkbutton(label="Auto-refresh (2s)",
                           variable=self.auto_refresh,
                           command=self._toggle_auto_refresh)
        vm.add_command(label="Refresh Now", command=self._refresh_all)

        hm = tk.Menu(mb, bg=BG2, fg=FG, activebackground=SEL_BG,
                     activeforeground=FG, tearoff=0)
        mb.add_cascade(label="Help", menu=hm)
        hm.add_command(label="About", command=self._about)

    def _build_toolbar(self):
        tb = tk.Frame(self.root, bg=BG2, pady=6, padx=10)
        tb.pack(fill="x", side="top")

        tk.Label(tb, text="◈ MINI-UNIONFS", bg=BG2, fg=ACCENT,
                 font=FONT_TITLE).pack(side="left", padx=(0, 20))

        for label, var, browse_fn in [
            ("Lower (RO):", self.lower_dir, self._browse_lower),
            ("Upper (RW):", self.upper_dir, self._browse_upper),
            ("Mount:",      self.mount_dir, self._browse_mount),
        ]:
            tk.Label(tb, text=label, bg=BG2, fg=MUTED,
                     font=FONT_SMALL).pack(side="left", padx=(8,2))
            e = tk.Entry(tb, textvariable=var, width=20, bg=BG3, fg=FG,
                         insertbackground=FG, relief="flat",
                         font=FONT_SMALL, bd=4)
            e.pack(side="left")
            tk.Button(tb, text="…", command=browse_fn,
                      bg=BG3, fg=MUTED, relief="flat",
                      font=FONT_SMALL, cursor="hand2",
                      activebackground=BG, activeforeground=FG,
                      padx=4).pack(side="left", padx=(0,4))

        # Mount / Unmount button
        mount_label = "⬡  Mount" if USE_EMOJI else "Mount"
        self.mount_btn = tk.Button(tb, text=mount_label,
                                   command=self._toggle_mount,
                                   bg=ACCENT2, fg=BG, relief="flat",
                                   font=FONT_HEAD, cursor="hand2",
                                   padx=12, pady=2,
                                   activebackground="#2ea043",
                                   activeforeground=BG)
        self.mount_btn.pack(side="left", padx=10)

        refresh_label = "⟳ Refresh" if USE_EMOJI else "Refresh"
        tk.Button(tb, text=refresh_label, command=self._refresh_all,
                  bg=BG3, fg=FG2, relief="flat", font=FONT_SMALL,
                  cursor="hand2", padx=8,
                  activebackground=BG, activeforeground=FG
                  ).pack(side="left")

    def _build_body(self):
        paned = tk.PanedWindow(self.root, orient="horizontal",
                               bg=BG, sashwidth=4, sashrelief="flat")
        paned.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Left: three-pane FS viewer ──
        left = tk.Frame(paned, bg=BG)
        paned.add(left, minsize=600)
        self._build_fs_panes(left)

        # ── Right: action panel + console ──
        right = tk.Frame(paned, bg=BG2, padx=0)
        paned.add(right, minsize=320)
        self._build_right_panel(right)

    def _build_fs_panes(self, parent):
        header = tk.Frame(parent, bg=BG2, pady=4)
        header.pack(fill="x")

        self.path_var = tk.StringVar(value="/")
        tk.Label(header, text="Current path:", bg=BG2, fg=MUTED,
                 font=FONT_SMALL).pack(side="left", padx=8)
        tk.Label(header, textvariable=self.path_var, bg=BG2, fg=ACCENT,
                 font=FONT_MONO).pack(side="left")
        up_label = "⬆ Up" if USE_EMOJI else "Up"
        tk.Button(header, text=up_label, command=self._go_up,
                  bg=BG3, fg=FG2, relief="flat", font=FONT_SMALL,
                  cursor="hand2", padx=6,
                  activebackground=BG, activeforeground=FG
                  ).pack(side="right", padx=8)

        cols_frame = tk.Frame(parent, bg=BG)
        cols_frame.pack(fill="both", expand=True)
        cols_frame.columnconfigure(0, weight=1)
        cols_frame.columnconfigure(1, weight=1)
        cols_frame.columnconfigure(2, weight=1)
        cols_frame.rowconfigure(0, weight=1)

        self.lower_tree = self._make_tree(cols_frame, "LOWER  (read-only)", 0,
                                          LO_FG)
        self.union_tree = self._make_tree(cols_frame, "UNION  (merged view)", 1,
                                          ACCENT)
        self.upper_tree = self._make_tree(cols_frame, "UPPER  (read-write)", 2,
                                          ACCENT2)

        # Double-click on union → enter dir
        self.union_tree.bind("<Double-1>", self._union_dblclick)
        self.union_tree.bind("<Return>",   self._union_dblclick)

    def _make_tree(self, parent, title, col, color):
        frame = tk.Frame(parent, bg=BG2, padx=1, pady=1)
        frame.grid(row=0, column=col, sticky="nsew", padx=2, pady=4)

        hdr = tk.Frame(frame, bg=BG3, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text=title, bg=BG3, fg=color,
                 font=FONT_HEAD).pack(side="left", padx=8)

        style_name = f"Tree{col}.Treeview"
        style = self._style
        style.configure(style_name,
                        background=BG2, foreground=FG2,
                        fieldbackground=BG2, borderwidth=0,
                        font=FONT_MONO, rowheight=22)
        style.configure(f"{style_name}.Heading",
                        background=BG3, foreground=MUTED,
                        font=FONT_SMALL, relief="flat")
        style.map(style_name,
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", FG)])

        tree = ttk.Treeview(frame, columns=("size", "note"),
                            style=style_name, selectmode="browse")
        tree.heading("#0",    text="Name", anchor="w")
        tree.heading("size",  text="Size",  anchor="e")
        tree.heading("note",  text="Note",  anchor="w")
        tree.column("#0",    width=160, stretch=True)
        tree.column("size",  width=60,  stretch=False, anchor="e")
        tree.column("note",  width=90,  stretch=True)

        # Tags
        tree.tag_configure("wh",    background=WH_BG, foreground=WH_FG)
        tree.tag_configure("upper", background=UP_BG, foreground=UP_FG)
        tree.tag_configure("lower", foreground=LO_FG)
        tree.tag_configure("dir",   foreground=WARN)

        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)

        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        return tree

    def _build_right_panel(self, parent):
        # Action buttons
        act = tk.Frame(parent, bg=BG2, pady=8, padx=8)
        act.pack(fill="x")

        tk.Label(act, text="POSIX OPERATIONS", bg=BG2, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w", pady=(0,4))

        self._btn(act, "📄  Create File"     if USE_EMOJI else "Create File",     self._op_create_file)
        self._btn(act, "📁  Create Dir"      if USE_EMOJI else "Create Dir",      self._op_mkdir)
        self._btn(act, "✏️   Write / Append"  if USE_EMOJI else "Write / Append",  self._op_write)
        self._btn(act, "👁️   Read File"       if USE_EMOJI else "Read File",       self._op_read)
        self._btn(act, "🚮  Delete (unlink)"  if USE_EMOJI else "Delete (unlink)",  self._op_delete,  danger=True)
        self._btn(act, "📋  Copy to Union"    if USE_EMOJI else "Copy to Union",    self._op_copy_in)

        sep = tk.Frame(act, bg=BG3, height=1)
        sep.pack(fill="x", pady=8)

        tk.Label(act, text="LAYER INSPECTION", bg=BG2, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w", pady=(0,4))

        self._btn(act, "🔍  Show Whiteouts"    if USE_EMOJI else "Show Whiteouts",   self._show_whiteouts)
        self._btn(act, "🧅  Layer Stack View"   if USE_EMOJI else "Layer Stack View",  self._show_layer_stack)
        self._btn(act, "🧪  Run Test Suite"     if USE_EMOJI else "Run Test Suite",    self._run_tests)
        self._btn(act, "🗑️   Clear Log"         if USE_EMOJI else "Clear Log",        self._clear_log, muted=True)

        # Console
        console_hdr = tk.Frame(parent, bg=BG3, pady=4)
        console_hdr.pack(fill="x")
        tk.Label(console_hdr, text="▸ LOG", bg=BG3, fg=MUTED,
                 font=FONT_SMALL).pack(side="left", padx=8)

        self.console = tk.Text(parent, bg=BG, fg=FG2, font=FONT_SMALL,
                               state="disabled", relief="flat",
                               wrap="word", bd=8, insertbackground=FG,
                               selectbackground=SEL_BG)
        self.console.pack(fill="both", expand=True, padx=0)

        # Configure log tags
        self.console.tag_configure("info",    foreground=FG2)
        self.console.tag_configure("ok",      foreground=ACCENT2)
        self.console.tag_configure("warn",    foreground=WARN)
        self.console.tag_configure("err",     foreground=ERR)
        self.console.tag_configure("muted",   foreground=MUTED)
        self.console.tag_configure("accent",  foreground=ACCENT)
        self.console.tag_configure("ts",      foreground=MUTED)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=BG3, pady=3)
        bar.pack(fill="x", side="bottom")

        self.status_var = tk.StringVar(value="Not mounted")
        self.status_lbl = tk.Label(bar, textvariable=self.status_var,
                                   bg=BG3, fg=MUTED, font=FONT_SMALL,
                                   anchor="w")
        self.status_lbl.pack(side="left", padx=10)

        self.mount_ind = tk.Label(bar, text="● UNMOUNTED", bg=BG3,
                                  fg=ERR, font=FONT_SMALL)
        self.mount_ind.pack(side="right", padx=10)

        legend = ("  🔵 lower-only   🟢 upper / CoW   🔴 whiteout (deleted)")
        tk.Label(bar, text=legend, bg=BG3, fg=MUTED,
                 font=FONT_SMALL).pack(side="right", padx=10)

    def _btn(self, parent, label, cmd, danger=False, muted=False):
        fg   = ERR    if danger else (MUTED if muted else FG2)
        abg  = "#3d0a0a" if danger else BG
        btn  = tk.Button(parent, text=label, command=cmd,
                         bg=BG3, fg=fg, relief="flat",
                         font=FONT_SMALL, cursor="hand2",
                         anchor="w", padx=10, pady=4,
                         activebackground=abg, activeforeground=fg)
        btn.pack(fill="x", pady=1)
        return btn

    # ─────────────────────── Logging ──────────────────────────────

    def _log(self, msg, kind="info"):
        self.console.configure(state="normal")
        self.console.insert("end", f"[{ts()}] ", "ts")
        self.console.insert("end", msg + "\n", kind)
        self.console.see("end")
        self.console.configure(state="disabled")

    def _clear_log(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    # ─────────────────────── Browsing ──────────────────────────────

    def _browse_lower(self):
        d = filedialog.askdirectory(title="Select Lower (read-only) directory")
        if d: self.lower_dir.set(d)

    def _browse_upper(self):
        d = filedialog.askdirectory(title="Select Upper (read-write) directory")
        if d: self.upper_dir.set(d)

    def _browse_mount(self):
        d = filedialog.askdirectory(title="Select Mount Point")
        if d: self.mount_dir.set(d)

    def _quick_setup(self):
        base = filedialog.askdirectory(title="Pick a base directory for Quick Setup")
        if not base:
            return
        lo = os.path.join(base, "lower")
        up = os.path.join(base, "upper")
        mnt = os.path.join(base, "mnt")
        for d in [lo, up, mnt]:
            os.makedirs(d, exist_ok=True)

        # Seed lower with some demo files
        Path(os.path.join(lo, "readme.txt")).write_text(
            "Hello from the lower (base) layer!\n"
            "This file is read-only in the union view.\n")
        Path(os.path.join(lo, "config.ini")).write_text(
            "[app]\nversion=1.0\ndebug=false\n")
        Path(os.path.join(lo, "shared.log")).write_text(
            "2024-01-01  system boot\n2024-01-02  health check ok\n")
        sub = os.path.join(lo, "data")
        os.makedirs(sub, exist_ok=True)
        Path(os.path.join(sub, "dataset.csv")).write_text(
            "id,value\n1,100\n2,200\n3,300\n")

        self.lower_dir.set(lo)
        self.upper_dir.set(up)
        self.mount_dir.set(mnt)
        self._log("Quick setup done! Directories created and seeded.", "ok")
        self._log(f"  lower → {lo}", "muted")
        self._log(f"  upper → {up}", "muted")
        self._log(f"  mount → {mnt}", "muted")

    # ─────────────────────── Mount / Unmount ──────────────────────

    def _toggle_mount(self):
        if self.mounted:
            self._unmount()
        else:
            self._mount()

    def _mount(self):
        lo  = self.lower_dir.get().strip()
        up  = self.upper_dir.get().strip()
        mnt = self.mount_dir.get().strip()

        for label, path in [("Lower dir", lo), ("Upper dir", up),
                             ("Mount point", mnt)]:
            if not path:
                messagebox.showerror("Error", f"{label} is not set.")
                return
            if not os.path.isdir(path):
                try:
                    os.makedirs(path, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("Error",
                                         f"Cannot create {label}: {e}")
                    return

        if not os.path.isfile(BINARY):
            messagebox.showerror("Error",
                f"Binary '{BINARY}' not found.\n"
                "Run  make  in the project directory first.")
            return

        cmd = [BINARY, lo, up, mnt, "-f"]
        self._log(f"Mounting: {' '.join(cmd)}", "accent")
        try:
            self.mount_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            self._log(f"Failed to start: {e}", "err")
            return

        # Wait briefly for FUSE to initialise
        time.sleep(0.8)
        if self.mount_proc.poll() is not None:
            err = self.mount_proc.stderr.read().decode()
            self._log(f"Process exited immediately: {err}", "err")
            return

        self.mounted = True
        self.cur_path = "/"
        unmount_label = "⬡  Unmount" if USE_EMOJI else "Unmount"
        self.mount_btn.configure(text=unmount_label, bg=ERR,
                                 activebackground="#5a0000")
        self.mount_ind.configure(text="● MOUNTED", fg=ACCENT2)
        self.status_var.set(f"Mounted: {lo}  +  {up}  →  {mnt}")
        self._log("Mount successful!", "ok")
        self._refresh_all()
        if self.auto_refresh.get():
            self._schedule_refresh()

    def _unmount(self):
        mnt = self.mount_dir.get().strip()
        self._log(f"Unmounting {mnt} …", "warn")
        subprocess.run(["fusermount", "-u", mnt],
                       capture_output=True)
        subprocess.run(["umount", mnt], capture_output=True)
        if self.mount_proc:
            self.mount_proc.terminate()
            self.mount_proc = None
        self.mounted = False
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        mount_label = "⬡  Mount" if USE_EMOJI else "Mount"
        self.mount_btn.configure(text=mount_label, bg=ACCENT2,
                                 activebackground="#2ea043")
        self.mount_ind.configure(text="● UNMOUNTED", fg=ERR)
        self.status_var.set("Not mounted")
        self._log("Unmounted.", "ok")
        self._refresh_all()

    # ─────────────────────── Refresh / Trees ──────────────────────

    def _schedule_refresh(self):
        if not self.mounted:
            return
        self._refresh_job = self.root.after(2000, self._auto_refresh_tick)

    def _auto_refresh_tick(self):
        self._refresh_all(silent=True)
        self._schedule_refresh()

    def _toggle_auto_refresh(self):
        if self.auto_refresh.get() and self.mounted:
            self._schedule_refresh()
        elif self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None

    def _refresh_all(self, silent=False):
        self.path_var.set(self.cur_path)
        self._populate_tree(self.lower_tree, self.lower_dir.get(),
                            self.cur_path, "lower")
        self._populate_tree(self.union_tree, self.mount_dir.get(),
                            self.cur_path, "union")
        self._populate_tree(self.upper_tree, self.upper_dir.get(),
                            self.cur_path, "upper")
        if not silent:
            self._log(f"Refreshed view for  {self.cur_path}", "muted")

    def _populate_tree(self, tree, base_dir, rel_path, mode):
        tree.delete(*tree.get_children())
        if not base_dir or not os.path.isdir(base_dir):
            return

        abs_path = os.path.join(base_dir, rel_path.lstrip("/"))
        if not os.path.isdir(abs_path):
            return

        entries = sorted(os.scandir(abs_path),
                         key=lambda e: (not e.is_dir(), e.name.lower()))

        for entry in entries:
            name = entry.name
            full = entry.path
            whiteout = is_wh(name)
            is_dir   = entry.is_dir() and not whiteout

            icon = file_icon(full, name)
            sz   = "" if is_dir else human_size(full)

            if whiteout:
                note = f"hides '{wh_target(name)}'"
                tag  = "wh"
            elif mode == "upper":
                # Check if it also exists in lower (= CoW copy)
                lo_path = os.path.join(self.lower_dir.get(),
                                       rel_path.lstrip("/"), name)
                note = "CoW copy" if os.path.exists(lo_path) else "upper-only"
                tag  = "upper"
            elif mode == "lower":
                note = "base layer"
                tag  = "lower"
            else:  # union
                # Determine origin
                up_path = os.path.join(self.upper_dir.get(),
                                       rel_path.lstrip("/"), name)
                lo_path = os.path.join(self.lower_dir.get(),
                                       rel_path.lstrip("/"), name)
                if os.path.exists(up_path):
                    note = "from upper"
                    tag  = "upper"
                else:
                    note = "from lower"
                    tag  = "lower"

            if is_dir:
                tag = "dir"

            tree.insert("", "end", iid=name,
                        text=f"  {icon}  {name}",
                        values=(sz, note), tags=(tag,))

    def _union_dblclick(self, event=None):
        sel = self.union_tree.selection()
        if not sel:
            return
        name = sel[0]
        abs_path = os.path.join(self.mount_dir.get(),
                                self.cur_path.lstrip("/"), name)
        if os.path.isdir(abs_path):
            if self.cur_path == "/":
                self.cur_path = f"/{name}"
            else:
                self.cur_path = f"{self.cur_path}/{name}"
            self._refresh_all(silent=True)

    def _go_up(self):
        if self.cur_path == "/":
            return
        self.cur_path = str(Path(self.cur_path).parent)
        self._refresh_all(silent=True)

    # ─────────────────────── POSIX Operations ─────────────────────

    def _require_mount(self):
        if not self.mounted:
            messagebox.showwarning("Not mounted",
                                   "Please mount the filesystem first.")
            return False
        return True

    def _union_abs(self, name=""):
        base = os.path.join(self.mount_dir.get(),
                            self.cur_path.lstrip("/"))
        return os.path.join(base, name) if name else base

    def _selected_name(self):
        sel = self.union_tree.selection()
        return sel[0] if sel else None

    def _op_create_file(self):
        if not self._require_mount(): return
        name = simpledialog.askstring("Create File",
                                      "Filename (in current directory):",
                                      parent=self.root)
        if not name: return
        path = self._union_abs(name)
        try:
            with open(path, "x") as f:
                f.write("")
            self._log(f"create: {self.cur_path}/{name}", "ok")
        except FileExistsError:
            self._log(f"File already exists: {name}", "warn")
        except Exception as e:
            self._log(f"create failed: {e}", "err")
        self._refresh_all(silent=True)

    def _op_mkdir(self):
        if not self._require_mount(): return
        name = simpledialog.askstring("Create Directory",
                                      "Directory name:",
                                      parent=self.root)
        if not name: return
        path = self._union_abs(name)
        try:
            os.mkdir(path)
            self._log(f"mkdir: {self.cur_path}/{name}", "ok")
        except FileExistsError:
            self._log(f"Directory already exists: {name}", "warn")
        except Exception as e:
            self._log(f"mkdir failed: {e}", "err")
        self._refresh_all(silent=True)

    def _op_write(self):
        if not self._require_mount(): return
        name = self._selected_name()
        if not name:
            name = simpledialog.askstring("Write to File",
                                          "Filename:", parent=self.root)
        if not name: return
        path = self._union_abs(name)

        # Simple multi-line editor dialog
        top = tk.Toplevel(self.root)
        top.title(f"Write to {name}")
        top.configure(bg=BG)
        top.geometry("500x320")
        top.minsize(420, 260)

        tk.Label(top, text=f"Content to write to  {name}",
                 bg=BG, fg=FG2, font=FONT_SMALL).pack(anchor="w", padx=10, pady=6)
        txt = tk.Text(top, bg=BG3, fg=FG, font=FONT_MONO,
                      insertbackground=FG, relief="flat", bd=8)
        txt.pack(fill="both", expand=True, padx=6)

        # Pre-load existing content
        initial_content = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                initial_content = f.read()
                txt.insert("1.0", initial_content)
        except Exception:
            pass

        mode_var = tk.StringVar(value="overwrite")
        bf = tk.Frame(top, bg=BG, pady=6)

        left = tk.Frame(bf, bg=BG)
        left.pack(side="left", padx=6)
        tk.Radiobutton(left, text="Overwrite", variable=mode_var,
                   value="overwrite", bg=BG, fg=FG2,
                   selectcolor=BG3, activebackground=BG,
                   font=FONT_SMALL).pack(side="left", padx=(2, 10))
        tk.Radiobutton(left, text="Append", variable=mode_var,
                   value="append", bg=BG, fg=FG2,
                   selectcolor=BG3, activebackground=BG,
                   font=FONT_SMALL).pack(side="left")

        def do_write():
            content = txt.get("1.0", "end-1c")
            flags = "w" if mode_var.get() == "overwrite" else "a"
            try:
                bytes_written = 0
                if flags == "a" and initial_content:
                    # Append only the delta to avoid duplicating existing content.
                    to_write = content[len(initial_content):] if content.startswith(initial_content) else content
                else:
                    to_write = content

                with open(path, flags, encoding="utf-8", errors="replace") as f:
                    bytes_written = len(to_write.encode("utf-8", errors="replace"))
                    f.write(to_write)
                    f.flush()
                    os.fsync(f.fileno())

                self._log(
                    f"write ({flags}): {self.cur_path}/{name}  ({bytes_written} bytes)",
                    "ok",
                )

                # Show CoW effect (upper should receive the modified file)
                up_path = os.path.join(self.upper_dir.get(),
                                       self.cur_path.lstrip("/"), name)
                lo_path = os.path.join(self.lower_dir.get(),
                                       self.cur_path.lstrip("/"), name)
                if os.path.exists(lo_path) and not os.path.exists(up_path):
                    # Fallback: if the backend didn't copy-up for some reason,
                    # ensure upper reflects the saved result.
                    try:
                        os.makedirs(os.path.dirname(up_path), exist_ok=True)
                        shutil.copy2(path, up_path)
                        self._log("  ↳ CoW fallback: copied result into upper", "warn")
                    except Exception as e:
                        self._log(f"  ↳ upper update failed: {e}", "err")

                if os.path.exists(up_path):
                    if os.path.exists(lo_path):
                        self._log("  ↳ served from upper layer now", "accent")
                    else:
                        self._log("  ↳ stored in upper layer", "accent")
            except Exception as e:
                self._log(f"write failed: {e}", "err")
            top.destroy()
            self._refresh_all(silent=True)

        # Keyboard shortcut
        try:
            top.bind("<Control-s>", lambda _e: do_write())
            top.bind("<Control-S>", lambda _e: do_write())
        except Exception:
            pass

        right = tk.Frame(bf, bg=BG)
        right.pack(side="right", padx=6)

        save_label = "💾 Save" if USE_EMOJI else "Save"
        tk.Button(right, text=save_label, command=do_write,
                  bg=ACCENT2, fg=BG, relief="flat",
                  font=FONT_SMALL, cursor="hand2", padx=10
              ).pack(side="right", padx=(8, 0))
        tk.Button(right, text="Cancel", command=top.destroy,
                  bg=BG3, fg=MUTED, relief="flat",
                  font=FONT_SMALL, cursor="hand2", padx=10
                  ).pack(side="right")

        # Pack the bottom bar *after* it has children so it gets a stable height.
        bf.pack(fill="x", side="bottom")
        top.update_idletasks()

    def _op_read(self):
        if not self._require_mount(): return
        name = self._selected_name()
        if not name:
            name = simpledialog.askstring("Read File", "Filename:",
                                          parent=self.root)
        if not name: return
        path = self._union_abs(name)

        try:
            with open(path, "r") as f:
                content = f.read()
        except Exception as e:
            self._log(f"read failed: {e}", "err")
            return

        top = tk.Toplevel(self.root)
        top.title(f"Contents of {name}")
        top.configure(bg=BG)
        top.geometry("600x400")

        # Origin info
        up_path = os.path.join(self.upper_dir.get(),
                               self.cur_path.lstrip("/"), name)
        origin = "upper layer" if os.path.exists(up_path) else "lower layer"
        tk.Label(top,
                 text=f"  📄 {self.cur_path}/{name}   [served from {origin}]",
                 bg=BG2, fg=ACCENT, font=FONT_SMALL,
                 anchor="w").pack(fill="x")

        txt = tk.Text(top, bg=BG, fg=FG2, font=FONT_MONO,
                      insertbackground=FG, relief="flat", bd=10)
        txt.insert("1.0", content)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

        self._log(f"read: {self.cur_path}/{name}  ({len(content)} bytes, {origin})",
                  "ok")

    def _op_delete(self):
        if not self._require_mount(): return
        name = self._selected_name()
        if not name:
            name = simpledialog.askstring("Delete", "Filename to delete:",
                                          parent=self.root)
        if not name: return

        lo_path = os.path.join(self.lower_dir.get(),
                               self.cur_path.lstrip("/"), name)
        will_wh = os.path.exists(lo_path)

        msg = f"Delete  '{name}'  ?"
        if will_wh:
            msg += "\n\nThis file exists in the lower layer.\nA whiteout file will be created."
        if not messagebox.askyesno("Confirm Delete", msg): return

        path = self._union_abs(name)
        try:
            if os.path.isdir(path):
                os.rmdir(path)
            else:
                os.unlink(path)
            self._log(f"unlink: {self.cur_path}/{name}", "ok")
            if will_wh:
                wh = f".wh.{name}"
                self._log(f"  ↳ whiteout created in upper:  {wh}", "warn")
        except Exception as e:
            self._log(f"delete failed: {e}", "err")
        self._refresh_all(silent=True)

    def _op_copy_in(self):
        if not self._require_mount(): return
        src = filedialog.askopenfilename(title="Select file to copy into union")
        if not src: return
        dst = self._union_abs(os.path.basename(src))
        try:
            shutil.copy2(src, dst)
            self._log(f"copy-in: {os.path.basename(src)} → union:{self.cur_path}",
                      "ok")
        except Exception as e:
            self._log(f"copy failed: {e}", "err")
        self._refresh_all(silent=True)

    # ─────────────────────── Inspection ───────────────────────────

    def _show_whiteouts(self):
        up = self.upper_dir.get()
        if not up or not os.path.isdir(up):
            messagebox.showinfo("Whiteouts", "Upper dir not set.")
            return

        whs = []
        for dirpath, _, files in os.walk(up):
            for f in files:
                if is_wh(f):
                    rel = os.path.relpath(os.path.join(dirpath, f), up)
                    target = wh_target(f)
                    whs.append((rel, target))

        top = tk.Toplevel(self.root)
        top.title("Whiteout Files")
        top.configure(bg=BG)
        top.geometry("600x380")

        tk.Label(top,
                 text="  🚫 WHITEOUT FILES  (deleted lower-layer entries)",
                 bg=BG2, fg=WH_FG, font=FONT_HEAD,
                 anchor="w").pack(fill="x", pady=4)

        if not whs:
            tk.Label(top, text="\n  No whiteouts found.\n",
                     bg=BG, fg=MUTED, font=FONT_MONO).pack()
        else:
            txt = tk.Text(top, bg=BG, fg=WH_FG, font=FONT_MONO,
                          relief="flat", bd=10)
            txt.tag_configure("dim", foreground=MUTED)
            txt.tag_configure("bold", foreground=FG)
            for wh_rel, target in sorted(whs):
                txt.insert("end", f"  🚫  {wh_rel}\n", "bold")
                txt.insert("end", f"       hides lower file: '{target}'\n\n",
                           "dim")
            txt.configure(state="disabled")
            txt.pack(fill="both", expand=True)

        self._log(f"Found {len(whs)} whiteout(s) in upper layer.", "warn")

    def _show_layer_stack(self):
        lo = self.lower_dir.get()
        up = self.upper_dir.get()
        if not lo or not up:
            messagebox.showinfo("Layer Stack", "Set lower and upper dirs first.")
            return

        top = tk.Toplevel(self.root)
        top.title("Layer Stack View")
        top.configure(bg=BG)
        top.geometry("700x500")

        tk.Label(top, text="  🧅 LAYER STACK  — file resolution",
                 bg=BG2, fg=ACCENT, font=FONT_HEAD,
                 anchor="w").pack(fill="x", pady=4)

        txt = tk.Text(top, bg=BG, fg=FG2, font=FONT_MONO,
                      relief="flat", bd=10)
        txt.tag_configure("upper", foreground=UP_FG)
        txt.tag_configure("lower", foreground=LO_FG)
        txt.tag_configure("wh",    foreground=WH_FG)
        txt.tag_configure("head",  foreground=ACCENT)
        txt.tag_configure("dim",   foreground=MUTED)

        # Collect all files from both layers
        all_files = set()
        for root_dir, dirs, files in os.walk(lo):
            for f in files:
                rel = os.path.relpath(os.path.join(root_dir, f), lo)
                all_files.add(rel)
        for root_dir, dirs, files in os.walk(up):
            for f in files:
                rel = os.path.relpath(os.path.join(root_dir, f), up)
                all_files.add(rel)

        txt.insert("end", f"{'File':<40} {'Served From':<14} Note\n", "head")
        txt.insert("end", "─" * 70 + "\n", "dim")

        for rel in sorted(all_files):
            up_p  = os.path.join(up, rel)
            lo_p  = os.path.join(lo, rel)
            name  = os.path.basename(rel)

            if is_wh(name):
                target = wh_target(name)
                txt.insert("end",
                           f"  {rel:<40} {'[WHITEOUT]':<14} hides '{target}'\n",
                           "wh")
            elif os.path.exists(up_p) and os.path.exists(lo_p):
                txt.insert("end",
                           f"  {rel:<40} {'upper (CoW)':<14} shadows lower\n",
                           "upper")
            elif os.path.exists(up_p):
                txt.insert("end",
                           f"  {rel:<40} {'upper-only':<14}\n",
                           "upper")
            else:
                # Check if whiteout exists
                wh = os.path.join(up, os.path.dirname(rel), ".wh." + name)
                if os.path.exists(wh):
                    txt.insert("end",
                               f"  {rel:<40} {'[HIDDEN]':<14} whiteouted\n",
                               "wh")
                else:
                    txt.insert("end",
                               f"  {rel:<40} {'lower':<14}\n",
                               "lower")

        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

    def _run_tests(self):
        if not os.path.isfile("test_unionfs.sh"):
            messagebox.showwarning("Test Suite",
                                   "test_unionfs.sh not found in current directory.")
            return
        if not os.path.isfile(BINARY):
            messagebox.showerror("Test Suite",
                                 f"Binary '{BINARY}' not found. Run 'make' first.")
            return

        # Unmount existing if mounted
        if self.mounted:
            if not messagebox.askyesno("Run Tests",
                "Running tests will temporarily unmount.\nContinue?"):
                return
            self._unmount()

        self._log("─" * 40, "muted")
        self._log("Running test_unionfs.sh …", "accent")

        def run():
            proc = subprocess.run(["bash", "test_unionfs.sh"],
                                  capture_output=True, text=True)
            out = proc.stdout + proc.stderr
            for line in out.splitlines():
                if "PASSED" in line:
                    self.root.after(0, lambda l=line: self._log(l, "ok"))
                elif "FAILED" in line:
                    self.root.after(0, lambda l=line: self._log(l, "err"))
                else:
                    self.root.after(0, lambda l=line: self._log(l, "muted"))
            self.root.after(0, lambda: self._log("─" * 40, "muted"))

        threading.Thread(target=run, daemon=True).start()

    # ─────────────────────── Misc ──────────────────────────────────

    def _about(self):
        messagebox.showinfo("About",
            "Mini-UnionFS GUI\n\n"
            "A graphical explorer for a FUSE-based Union Filesystem.\n\n"
            "Features:\n"
            "  • Layer stacking (lower RO + upper RW)\n"
            "  • Copy-on-Write (CoW)\n"
            "  • Whiteout file visualisation\n"
            "  • Full POSIX ops: create, read, write, delete, rename\n\n"
            "Build backend:  make\n"
            "Run tests:      bash test_unionfs.sh")

    def _on_exit(self):
        if self.mounted:
            self._unmount()
        self.root.destroy()


# ─────────────────────────── Entry ───────────────────────────────

def main():
    global USE_EMOJI, USE_THEME

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--no-emoji", action="store_true",
                        help="Disable emoji/icons in the UI (helps on some Tk builds)")
    parser.add_argument("--emoji", action="store_true",
                        help="Force-enable emoji/icons in the UI")
    parser.add_argument("--safe", action="store_true",
                        help="Enable safe mode (disables emoji and ttk theme selection)")
    args = parser.parse_args()

    if args.safe:
        USE_EMOJI = False
        USE_THEME = False
    elif args.no_emoji:
        USE_EMOJI = False
    elif args.emoji:
        USE_EMOJI = True

    # Minimal diagnostics for crash reports (printed before Tk init)
    try:
        print(f"[unionfs_gui] python={sys.version.split()[0]} platform={platform.platform()} emoji={USE_EMOJI} theme={USE_THEME}")
        sys.stdout.flush()
    except Exception:
        pass

    root = tk.Tk()
    app = UnionFSGUI(root)
    root.protocol("WM_DELETE_WINDOW", app._on_exit)
    root.mainloop()

if __name__ == "__main__":
    main()
