#!/usr/bin/env python3
"""
UnMP3 GUI — Hybrid Lossless Audio Codec
========================================
A Windows-friendly Tkinter interface for unmp3.py

Requirements: Python 3.8+, numpy, ffmpeg in PATH
Run: python unmp3_gui.py
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import sys
import os
import subprocess
from pathlib import Path

# ── Try to import the codec (same directory or on PYTHONPATH) ──────────────
try:
    from unmp3 import UnMP3Codec, generate_test_audio, run_experiment
    CODEC_AVAILABLE = True
except ImportError:
    CODEC_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# PALETTE  (dark phosphor / DAW aesthetic)
# ══════════════════════════════════════════════════════════════════════════════
C = {
    "bg":        "#0e0f0e",   # near-black chassis
    "panel":     "#161a16",   # slightly lifted panels
    "border":    "#253025",   # subtle green-tinted border
    "accent":    "#3adf6a",   # phosphor green
    "accent2":   "#1fa84a",   # deeper green for secondary elements
    "warn":      "#e0a020",   # amber warning
    "err":       "#d94040",   # red error
    "text":      "#c8d8c8",   # soft green-white text
    "muted":     "#4a5e4a",   # muted label text
    "entry_bg":  "#111811",   # input field bg
    "entry_fg":  "#9ecf9e",   # input field text
    "btn":       "#1e2e1e",   # button bg
    "btn_hover": "#253525",   # button hover
    "btn_act":   "#3adf6a",   # button active/accent
    "meter_off": "#162416",   # meter inactive segment
}

FONT_MONO  = ("Consolas", 10)
FONT_MONO_SM = ("Consolas", 9)
FONT_LABEL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI", 11, "bold")
FONT_HEAD  = ("Consolas", 13, "bold")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def check_ffmpeg():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def check_numpy():
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# STYLED WIDGETS
# ══════════════════════════════════════════════════════════════════════════════

class PhosphorButton(tk.Canvas):
    """A flat button with phosphor-green hover state."""

    def __init__(self, parent, text, command=None, width=140, accent=False, **kw):
        bg = C["btn"]
        super().__init__(parent, width=width, height=32,
                         bg=C["panel"], highlightthickness=0, **kw)
        self._cmd = command
        self._accent = accent
        self._text = text
        self._draw(hover=False)
        self.bind("<Enter>",        self._on_enter)
        self.bind("<Leave>",        self._on_leave)
        self.bind("<ButtonPress-1>",  self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self, hover=False, pressed=False):
        self.delete("all")
        if pressed:
            border = C["accent"]; fg = C["bg"]; bg = C["accent"]
        elif hover:
            border = C["accent"]; fg = C["accent"]; bg = C["btn_hover"]
        elif self._accent:
            border = C["accent2"]; fg = C["accent"]; bg = C["btn"]
        else:
            border = C["border"]; fg = C["text"]; bg = C["btn"]

        w = int(self["width"]); h = int(self["height"])
        self.create_rectangle(0, 0, w-1, h-1, fill=bg, outline=border, width=1)
        self.create_text(w//2, h//2, text=self._text, fill=fg,
                         font=FONT_MONO if not pressed else ("Consolas", 10, "bold"))

    def _on_enter(self, _):  self._draw(hover=True)
    def _on_leave(self, _):  self._draw(hover=False)
    def _on_press(self, _):  self._draw(pressed=True)
    def _on_release(self, e):
        self._draw(hover=True)
        if self._cmd:
            self._cmd()

    def configure_state(self, enabled=True):
        """Grey out button when disabled."""
        self._enabled = enabled
        if not enabled:
            self.delete("all")
            w = int(self["width"]); h = int(self["height"])
            self.create_rectangle(0, 0, w-1, h-1,
                                  fill=C["btn"], outline=C["border"], width=1)
            self.create_text(w//2, h//2, text=self._text,
                             fill=C["muted"], font=FONT_MONO)
            self.unbind("<Enter>"); self.unbind("<Leave>")
            self.unbind("<ButtonPress-1>"); self.unbind("<ButtonRelease-1>")
        else:
            self._draw()
            self.bind("<Enter>",        self._on_enter)
            self.bind("<Leave>",        self._on_leave)
            self.bind("<ButtonPress-1>",  self._on_press)
            self.bind("<ButtonRelease-1>", self._on_release)


class FileRow(tk.Frame):
    """Label + entry + browse button row."""

    def __init__(self, parent, label, filetypes, save=False, **kw):
        super().__init__(parent, bg=C["panel"], **kw)
        self._save = save
        self._filetypes = filetypes

        tk.Label(self, text=label, width=16, anchor="w",
                 bg=C["panel"], fg=C["muted"], font=FONT_LABEL).pack(side="left")

        self.var = tk.StringVar()
        entry = tk.Entry(self, textvariable=self.var, width=46,
                         bg=C["entry_bg"], fg=C["entry_fg"],
                         insertbackground=C["accent"],
                         relief="flat", font=FONT_MONO_SM,
                         highlightthickness=1,
                         highlightcolor=C["accent2"],
                         highlightbackground=C["border"])
        entry.pack(side="left", padx=(0, 6))

        PhosphorButton(self, "Browse…", command=self._browse,
                       width=90).pack(side="left")

    def _browse(self):
        if self._save:
            path = filedialog.asksaveasfilename(filetypes=self._filetypes,
                                                defaultextension=self._filetypes[0][1])
        else:
            path = filedialog.askopenfilename(filetypes=self._filetypes)
        if path:
            self.var.set(path)

    @property
    def path(self):
        return self.var.get().strip()


class LogPane(tk.Frame):
    """Scrollable monospaced console output."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C["panel"], **kw)
        self.text = tk.Text(self, bg=C["entry_bg"], fg=C["text"],
                            font=FONT_MONO_SM, relief="flat",
                            state="disabled", wrap="word",
                            insertbackground=C["accent"],
                            selectbackground=C["accent2"],
                            highlightthickness=1,
                            highlightbackground=C["border"])
        sb = tk.Scrollbar(self, command=self.text.yview,
                          bg=C["panel"], troughcolor=C["bg"],
                          activebackground=C["accent2"])
        self.text.configure(yscrollcommand=sb.set)

        self.text.tag_configure("ok",   foreground=C["accent"])
        self.text.tag_configure("warn", foreground=C["warn"])
        self.text.tag_configure("err",  foreground=C["err"])
        self.text.tag_configure("head", foreground=C["accent"],
                                font=("Consolas", 10, "bold"))
        self.text.tag_configure("muted", foreground=C["muted"])

        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

    def append(self, msg, tag=None):
        self.text.configure(state="normal")
        if tag:
            self.text.insert("end", msg + "\n", tag)
        else:
            self.text.insert("end", msg + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class SegBar(tk.Canvas):
    """Horizontal segmented progress bar."""

    def __init__(self, parent, segments=30, **kw):
        super().__init__(parent, height=12, bg=C["panel"],
                         highlightthickness=0, **kw)
        self._segs = segments
        self._active = 0
        self.bind("<Configure>", lambda _: self._redraw())

    def set(self, fraction):
        self._active = max(0.0, min(1.0, fraction))
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or 400
        seg_w = (w - self._segs) / self._segs
        active_n = int(self._active * self._segs)
        for i in range(self._segs):
            x0 = i * (seg_w + 1)
            x1 = x0 + seg_w
            color = C["accent"] if i < active_n else C["meter_off"]
            self.create_rectangle(x0, 1, x1, 11, fill=color, outline="")


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════

class EncodeTab(tk.Frame):
    def __init__(self, parent, log, q):
        super().__init__(parent, bg=C["panel"])
        self._log = log
        self._q   = q
        self._build()

    def _build(self):
        pad = dict(padx=12, pady=5)

        tk.Label(self, text="SOURCE", bg=C["panel"],
                 fg=C["muted"], font=FONT_LABEL).pack(anchor="w", **pad)

        self.wav_row = FileRow(self, "Input WAV",
                               [("WAV Audio", "*.wav"), ("All Files", "*.*")])
        self.wav_row.pack(fill="x", **pad)

        tk.Label(self, text="OUTPUTS", bg=C["panel"],
                 fg=C["muted"], font=FONT_LABEL).pack(anchor="w", padx=12, pady=(10,2))

        self.mp3_row = FileRow(self, "Output MP3",
                               [("MP3 Audio", "*.mp3")], save=True)
        self.mp3_row.pack(fill="x", **pad)

        self.unmp3_row = FileRow(self, "Output UNMP3",
                                 [("UNMP3 Residual", "*.unmp3")], save=True)
        self.unmp3_row.pack(fill="x", **pad)

        # Auto-fill outputs when WAV chosen
        self.wav_row.var.trace_add("write", self._autofill)

        # Bitrate
        br_frame = tk.Frame(self, bg=C["panel"])
        br_frame.pack(fill="x", **pad)
        tk.Label(br_frame, text="MP3 Bitrate", width=16, anchor="w",
                 bg=C["panel"], fg=C["muted"], font=FONT_LABEL).pack(side="left")
        self.bitrate = tk.StringVar(value="320k")
        for br in ["128k", "192k", "256k", "320k"]:
            tk.Radiobutton(br_frame, text=br, variable=self.bitrate, value=br,
                           bg=C["panel"], fg=C["text"], selectcolor=C["bg"],
                           activebackground=C["panel"], activeforeground=C["accent"],
                           font=FONT_MONO_SM).pack(side="left", padx=8)

        # Action buttons
        btn_frame = tk.Frame(self, bg=C["panel"])
        btn_frame.pack(fill="x", padx=12, pady=10)
        PhosphorButton(btn_frame, "▶  Encode", command=self._run,
                       width=130, accent=True).pack(side="left", padx=(0, 8))
        PhosphorButton(btn_frame, "Clear", command=self._clear,
                       width=80).pack(side="left")

    def _autofill(self, *_):
        wav = self.wav_row.path
        if not wav:
            return
        stem = Path(wav).with_suffix("")
        if not self.mp3_row.path:
            self.mp3_row.var.set(str(stem) + ".mp3")
        if not self.unmp3_row.path:
            self.unmp3_row.var.set(str(stem) + ".unmp3")

    def _clear(self):
        self.wav_row.var.set("")
        self.mp3_row.var.set("")
        self.unmp3_row.var.set("")

    def _run(self):
        wav   = self.wav_row.path
        mp3   = self.mp3_row.path
        unmp3 = self.unmp3_row.path
        br    = self.bitrate.get()

        if not wav:
            messagebox.showwarning("Missing Input", "Please select an input WAV file.")
            return
        if not mp3:
            messagebox.showwarning("Missing Output", "Please specify an output MP3 path.")
            return
        if not unmp3:
            messagebox.showwarning("Missing Output", "Please specify an output UNMP3 path.")
            return
        if not Path(wav).exists():
            messagebox.showerror("File Not Found", f"WAV not found:\n{wav}")
            return

        self._log.clear()
        self._log.append(f"ENCODE  {Path(wav).name}  →  {Path(mp3).name} + {Path(unmp3).name}", "head")
        self._log.append(f"Bitrate: {br}\n", "muted")

        def worker():
            try:
                codec = UnMP3Codec(mp3_bitrate=br)
                # Redirect stdout to queue
                import io, contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    result = codec.encode(wav, mp3, unmp3)
                for line in buf.getvalue().splitlines():
                    tag = "ok" if "✅" in line else ("err" if "❌" in line else None)
                    self._q.put(("log", line, tag))
                self._q.put(("log", "", None))
                self._q.put(("log", "Encode complete.", "ok"))
                self._q.put(("done", None, None))
            except Exception as e:
                self._q.put(("log", f"ERROR: {e}", "err"))
                self._q.put(("done", None, None))

        threading.Thread(target=worker, daemon=True).start()


class DecodeTab(tk.Frame):
    def __init__(self, parent, log, q):
        super().__init__(parent, bg=C["panel"])
        self._log = log
        self._q   = q
        self._build()

    def _build(self):
        pad = dict(padx=12, pady=5)

        tk.Label(self, text="INPUTS", bg=C["panel"],
                 fg=C["muted"], font=FONT_LABEL).pack(anchor="w", **pad)

        self.mp3_row = FileRow(self, "Input MP3",
                               [("MP3 Audio", "*.mp3"), ("All Files", "*.*")])
        self.mp3_row.pack(fill="x", **pad)

        self.unmp3_row = FileRow(self, "Input UNMP3",
                                 [("UNMP3 Residual", "*.unmp3"), ("All Files", "*.*")])
        self.unmp3_row.pack(fill="x", **pad)

        # Auto-fill UNMP3 when MP3 chosen
        self.mp3_row.var.trace_add("write", self._autofill)

        tk.Label(self, text="OUTPUT", bg=C["panel"],
                 fg=C["muted"], font=FONT_LABEL).pack(anchor="w", padx=12, pady=(10,2))

        self.wav_row = FileRow(self, "Output WAV",
                               [("WAV Audio", "*.wav")], save=True)
        self.wav_row.pack(fill="x", **pad)

        # Verify checkbox
        self.do_verify = tk.BooleanVar(value=False)
        ck_frame = tk.Frame(self, bg=C["panel"])
        ck_frame.pack(fill="x", padx=12, pady=(4, 0))
        tk.Checkbutton(ck_frame, text="Verify reconstruction after decode",
                       variable=self.do_verify,
                       bg=C["panel"], fg=C["text"], selectcolor=C["bg"],
                       activebackground=C["panel"], activeforeground=C["accent"],
                       font=FONT_LABEL).pack(side="left")

        self.orig_row = FileRow(self, "Original WAV (verify)",
                                [("WAV Audio", "*.wav"), ("All Files", "*.*")])
        self.orig_row.pack(fill="x", padx=12, pady=3)

        btn_frame = tk.Frame(self, bg=C["panel"])
        btn_frame.pack(fill="x", padx=12, pady=10)
        PhosphorButton(btn_frame, "▶  Decode", command=self._run,
                       width=130, accent=True).pack(side="left", padx=(0, 8))
        PhosphorButton(btn_frame, "Clear", command=self._clear,
                       width=80).pack(side="left")

    def _autofill(self, *_):
        mp3 = self.mp3_row.path
        if not mp3:
            return
        stem = Path(mp3).with_suffix("")
        if not self.unmp3_row.path:
            candidate = str(stem) + ".unmp3"
            if Path(candidate).exists():
                self.unmp3_row.var.set(candidate)
        if not self.wav_row.path:
            self.wav_row.var.set(str(stem) + "_restored.wav")

    def _clear(self):
        self.mp3_row.var.set("")
        self.unmp3_row.var.set("")
        self.wav_row.var.set("")
        self.orig_row.var.set("")

    def _run(self):
        mp3   = self.mp3_row.path
        unmp3 = self.unmp3_row.path
        wav   = self.wav_row.path
        verify = self.do_verify.get()
        orig  = self.orig_row.path

        if not mp3 or not unmp3:
            messagebox.showwarning("Missing Input", "Please select both an MP3 and UNMP3 file.")
            return
        if not wav:
            messagebox.showwarning("Missing Output", "Please specify an output WAV path.")
            return
        for p, label in [(mp3, "MP3"), (unmp3, "UNMP3")]:
            if not Path(p).exists():
                messagebox.showerror("File Not Found", f"{label} not found:\n{p}")
                return
        if verify and not orig:
            messagebox.showwarning("Verify", "Enable verification requires the original WAV path.")
            return

        self._log.clear()
        self._log.append(f"DECODE  {Path(mp3).name} + {Path(unmp3).name}  →  {Path(wav).name}", "head")

        def worker():
            try:
                import io, contextlib
                codec = UnMP3Codec()
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    codec.decode(mp3, unmp3, wav)
                for line in buf.getvalue().splitlines():
                    self._q.put(("log", line, None))

                if verify and orig:
                    self._q.put(("log", "", None))
                    buf2 = io.StringIO()
                    with contextlib.redirect_stdout(buf2):
                        perfect = codec.verify(orig, wav)
                    for line in buf2.getvalue().splitlines():
                        tag = "ok" if "✅" in line else ("warn" if "⚠" in line else ("err" if "❌" in line else None))
                        self._q.put(("log", line, tag))

                self._q.put(("log", "", None))
                self._q.put(("log", "Decode complete.", "ok"))
                self._q.put(("done", None, None))
            except Exception as e:
                self._q.put(("log", f"ERROR: {e}", "err"))
                self._q.put(("done", None, None))

        threading.Thread(target=worker, daemon=True).start()


class TestTab(tk.Frame):
    def __init__(self, parent, log, q):
        super().__init__(parent, bg=C["panel"])
        self._log = log
        self._q   = q
        self._build()

    def _build(self):
        pad = dict(padx=12, pady=5)

        info = (
            "Generates a 10-second harmonic test tone and runs\n"
            "the full encode → decode → verify cycle at four\n"
            "bitrates: 128k / 192k / 256k / 320k.\n\n"
            "A summary table is printed to the console below."
        )
        tk.Label(self, text=info, bg=C["panel"], fg=C["text"],
                 font=FONT_LABEL, justify="left").pack(anchor="w", **pad)

        dir_frame = tk.Frame(self, bg=C["panel"])
        dir_frame.pack(fill="x", **pad)
        tk.Label(dir_frame, text="Output folder", width=16, anchor="w",
                 bg=C["panel"], fg=C["muted"], font=FONT_LABEL).pack(side="left")
        self.out_dir = tk.StringVar(value="./unmp3_test")
        tk.Entry(dir_frame, textvariable=self.out_dir, width=40,
                 bg=C["entry_bg"], fg=C["entry_fg"],
                 insertbackground=C["accent"], relief="flat",
                 font=FONT_MONO_SM,
                 highlightthickness=1, highlightcolor=C["accent2"],
                 highlightbackground=C["border"]).pack(side="left", padx=(0,6))
        PhosphorButton(dir_frame, "Browse…",
                       command=self._browse_dir, width=90).pack(side="left")

        btn_frame = tk.Frame(self, bg=C["panel"])
        btn_frame.pack(fill="x", padx=12, pady=12)
        PhosphorButton(btn_frame, "▶  Run Test Suite", command=self._run,
                       width=160, accent=True).pack(side="left")

    def _browse_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.out_dir.set(d)

    def _run(self):
        out = self.out_dir.get().strip()
        self._log.clear()
        self._log.append("TEST SUITE — UnMP3 Codec", "head")
        self._log.append(f"Output: {out}\n", "muted")

        def worker():
            try:
                import io, contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    run_experiment(out)
                for line in buf.getvalue().splitlines():
                    tag = "ok" if "✅" in line else ("err" if "❌" in line else None)
                    self._q.put(("log", line, tag))
                self._q.put(("log", "", None))
                self._q.put(("log", "Test suite complete.", "ok"))
                self._q.put(("done", None, None))
            except Exception as e:
                self._q.put(("log", f"ERROR: {e}", "err"))
                self._q.put(("done", None, None))

        threading.Thread(target=worker, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class UnMP3App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("UnMP3  ·  Hybrid Lossless Codec")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(660, 560)

        self._q = queue.Queue()
        self._busy = False

        self._build_header()
        self._build_deps_banner()
        self._build_tabs()
        self._build_console()
        self._build_status()

        self.after(100, self._poll_queue)

        # Center on screen
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 720, 640
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self, bg=C["bg"], pady=10)
        hdr.pack(fill="x", padx=16)

        tk.Label(hdr, text="UN", bg=C["bg"],
                 fg=C["muted"], font=("Consolas", 20, "bold")).pack(side="left")
        tk.Label(hdr, text="MP3", bg=C["bg"],
                 fg=C["accent"], font=("Consolas", 20, "bold")).pack(side="left")
        tk.Label(hdr, text="  Hybrid Lossless Codec", bg=C["bg"],
                 fg=C["text"], font=("Segoe UI", 11)).pack(side="left", padx=(4,0))

        tk.Label(hdr, text="AlphaAudio", bg=C["bg"],
                 fg=C["muted"], font=FONT_MONO_SM).pack(side="right")

        # Divider
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")

    # ── Dependency banner ─────────────────────────────────────────────────────

    def _build_deps_banner(self):
        has_ffmpeg  = check_ffmpeg()
        has_numpy   = check_numpy()
        has_codec   = CODEC_AVAILABLE

        issues = []
        if not has_ffmpeg:  issues.append("ffmpeg not found in PATH")
        if not has_numpy:   issues.append("numpy not installed  →  pip install numpy")
        if not has_codec:   issues.append("unmp3.py not found alongside this script")

        if issues:
            banner = tk.Frame(self, bg="#2a1010", pady=4)
            banner.pack(fill="x", padx=0)
            for msg in issues:
                tk.Label(banner, text=f"⚠  {msg}", bg="#2a1010",
                         fg=C["warn"], font=FONT_MONO_SM).pack(anchor="w", padx=14)

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _build_tabs(self):
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Dark.TNotebook",
                        background=C["bg"], borderwidth=0, tabmargins=0)
        style.configure("Dark.TNotebook.Tab",
                        background=C["btn"], foreground=C["muted"],
                        padding=[14, 6], font=FONT_MONO,
                        borderwidth=0, focuscolor=C["bg"])
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", C["panel"])],
                  foreground=[("selected", C["accent"])],
                  expand=[("selected", [0, 0, 0, 0])])

        nb = ttk.Notebook(self, style="Dark.TNotebook")
        nb.pack(fill="x", padx=0, pady=0)

        self._log = LogPane(self)  # shared log, built before tabs pass it

        self.enc_tab  = EncodeTab(nb, self._log, self._q)
        self.dec_tab  = DecodeTab(nb, self._log, self._q)
        self.test_tab = TestTab(nb,  self._log, self._q)

        nb.add(self.enc_tab,  text="  Encode  ")
        nb.add(self.dec_tab,  text="  Decode  ")
        nb.add(self.test_tab, text="  Test Suite  ")

    # ── Console ───────────────────────────────────────────────────────────────

    def _build_console(self):
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(self, text="OUTPUT", bg=C["bg"],
                 fg=C["muted"], font=FONT_LABEL,
                 anchor="w").pack(fill="x", padx=14, pady=(6, 2))

        self._log.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self._log.append("Ready — select a tab above to get started.", "muted")

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_status(self):
        bar = tk.Frame(self, bg=C["bg"], pady=4)
        bar.pack(fill="x", side="bottom")
        tk.Frame(bar, bg=C["border"], height=1).pack(fill="x")

        inner = tk.Frame(bar, bg=C["bg"])
        inner.pack(fill="x", padx=12, pady=(4, 2))

        self._status_var = tk.StringVar(value="Idle")
        tk.Label(inner, textvariable=self._status_var,
                 bg=C["bg"], fg=C["muted"], font=FONT_MONO_SM,
                 anchor="w").pack(side="left", fill="x", expand=True)

        self._seg = SegBar(inner, segments=24, width=200)
        self._seg.pack(side="right")

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                kind, msg, tag = self._q.get_nowait()
                if kind == "log":
                    self._log.append(msg, tag)
                    self._status_var.set(msg[:80] if msg else "Running…")
                elif kind == "done":
                    self._busy = False
                    self._status_var.set("Done.")
                    self._seg.set(0)
                elif kind == "progress":
                    self._seg.set(msg)  # msg is float 0-1
        except queue.Empty:
            pass

        if self._busy:
            # Animate the seg bar in indeterminate mode
            cur = getattr(self, "_seg_pos", 0)
            cur = (cur + 0.03) % 1.2
            self._seg_pos = cur
            self._seg.set(min(cur, 1.0))

        self.after(80, self._poll_queue)

    def _start_busy(self):
        self._busy = True
        self._seg_pos = 0
        self._status_var.set("Running…")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if not CODEC_AVAILABLE:
        # Still launch GUI but show big warning
        pass
    app = UnMP3App()
    app.mainloop()


if __name__ == "__main__":
    main()
