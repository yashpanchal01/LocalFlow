"""
LocalFlow — a local Wispr Flow clone.

Press Ctrl+Alt+/ to start recording, press it again to stop.
Audio is transcribed with faster-whisper (live, while you speak), cleaned
up with a local Ollama model, and pasted into whatever app has focus.

Everything runs on-device. No cloud, no subscription.
"""

import ctypes
import io
import json
import math
import os
import random
import re
import socket
import sys
import time
import threading
import wave
import winsound
from ctypes import wintypes

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
# Ctrl+Alt+/ via Win32 RegisterHotKey (reliable, unlike keyboard hooks)
HOTKEY_MODS = 0x0002 | 0x0001 | 0x4000    # MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
HOTKEY_VK = 0xBF                          # VK_OEM_2, the / ? key
HOTKEY_NAME = "Ctrl+Alt+/"

WHISPER_MODEL = "distil-large-v3"   # accuracy; small.en if VRAM gets tight
# TODO: upgrade to "distil-whisper/distil-large-v3.5-ct2" (lower WER, same VRAM)
# once the Windows HF-cache symlink issue is resolved: first download hits
# WinError 1314 (needs Developer Mode / admin, or HF_HUB_DISABLE_SYMLINKS).
# See docs/research/improving-localflow.md §2a.
WHISPER_COMPUTE = "int8_float16"    # quantized on GPU = fits next to the LLM
OLLAMA_MODEL = "qwen2.5:3b"     # small enough to always fit next to whisper
OLLAMA_URL = "http://localhost:11434"
OLLAMA_KEEP_ALIVE = "2h"        # keep model warm between dictations
OLLAMA_NUM_CTX = 2048           # small context = far less VRAM than default
OLLAMA_TIMEOUT = 30             # give up and paste raw transcript after this
MAX_RECORD_SECONDS = 120        # safety cutoff
LIVE_PREVIEW_EVERY = 2.0        # seconds between live-transcription passes

# Seed word list. On first run this is written to dictionary.txt, which you
# then edit; the file wins after that. Whisper is biased toward these words.
DICTIONARY = ["Claude Code", "Claude", "Ollama", "Whisper", "LocalFlow",
              "GitHub", "Python", "VS Code", "API", "Anthropic"]

# Common mishearings, fixed after transcription (case-insensitive regex).
# Deliberately narrow: each entry targets an *observed* mishear with an
# unambiguous shape, so it can't clobber a legitimate word. When in doubt,
# leave it out — a wrong "correction" is worse than an uncorrected transcript.
CORRECTIONS = {
    # "Claude Code" — two-word mishears: a Claude-ish first token followed by
    # a code-ish second token. "claw" (as in the observed "claw code") joins
    # the existing "clawed"/"cloud"/… set.
    r"\b(?:blood|cloud|clod|clot|claw|clawed|clogged|clout) ?"
    r"(?:cold|code|coat|called|cord)\b": "Claude Code",
    # "Claude Code" — single-token smears Whisper emits as one word
    # ("broadcourt", "Claudecote", "Claudecode"). These strings have no
    # legitimate English meaning, so a bare match is safe.
    r"\b(?:broadcourt|claudecote|claudecode)\b": "Claude Code",
    # "Ollama" — the leading vowel Whisper usually hears ("ollama", "o lama",
    # "oh lama", "ooo lama").
    r"\bo+ ?lama\b": "Ollama",
    # "Ollama" — bare capitalized "Lama" (a proper-noun-shaped mishear).
    # (?-i:Lama) turns OFF the global re.I for just this token, so it matches
    # only the capitalized form and never clobbers "lama" (Tibetan teacher)
    # or "llama" (the animal), both of which stay lowercase in normal prose.
    r"\b(?-i:Lama)\b": "Ollama",
    r"\bget ?hub\b": "GitHub",
}

SAMPLE_RATE = 16000             # whisper's native rate
SINGLE_INSTANCE_PORT = 52739    # refuses to start twice
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(APP_DIR, "localflow.log")
DICT_FILE = os.path.join(APP_DIR, "dictionary.txt")  # user-editable word list

CLEANUP_PROMPT = """You clean up raw speech-to-text transcripts for dictation.

Rules:
- Fix punctuation, capitalization, and obvious transcription errors.
- Remove filler words (um, uh, you know, like) and false starts.
- Apply the speaker's self-corrections: "send it Monday, no wait, Tuesday" \
becomes "send it Tuesday".
- Keep the speaker's words and meaning. Do NOT summarize, expand, answer \
questions, or add anything.
- Keep everything in ONE paragraph with the same sentence structure. Do not \
reformat into lists or separate lines. Every sentence ends with punctuation.
- Output ONLY the cleaned text. No quotes, no preamble, no explanations."""

# ----------------------------------------------------------------------------
# Make pip-installed NVIDIA DLLs visible to ctranslate2 (GPU support without
# a system CUDA install). Harmless if the packages are missing.
# ----------------------------------------------------------------------------
def _register_nvidia_dlls():
    try:
        import nvidia
        for pkg_dir in nvidia.__path__:
            for root, _dirs, files in os.walk(pkg_dir):
                if any(f.lower().endswith(".dll") for f in files):
                    os.add_dll_directory(root)
                    os.environ["PATH"] = root + os.pathsep + os.environ["PATH"]
    except ImportError:
        pass

_register_nvidia_dlls()

import numpy as np
import requests

# one reused connection, no proxy lookup — Windows proxy auto-detection was
# adding ~2s of latency to every localhost request
OLLAMA_SESSION = requests.Session()
OLLAMA_SESSION.trust_env = False

import sounddevice as sd
import pyperclip
from pynput import keyboard
from faster_whisper import WhisperModel
from PySide6 import QtCore, QtGui, QtWidgets


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Sounds: soft synthesized chimes (sine + gentle envelope), played async.
# Drop your own start.wav / stop.wav / done.wav / ready.wav / error.wav into
# a `sounds` folder next to this script to override any of them.
# ----------------------------------------------------------------------------
def _chime(freqs, dur=0.10, gap=0.015, vol=0.35, sr=22050):
    frames = bytearray()
    for f in freqs:
        n = int(sr * dur)
        for i in range(n):
            env = min(1.0, i / (sr * 0.008), (n - i) / (sr * 0.05))
            v = env * (math.sin(2 * math.pi * f * i / sr)
                       + 0.25 * math.sin(4 * math.pi * f * i / sr))
            s = int(max(-1.0, min(1.0, vol * v)) * 32767)
            frames += s.to_bytes(2, "little", signed=True)
        frames += b"\x00\x00" * int(sr * gap)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return buf.getvalue()

SOUNDS = {
    "ready": _chime([523, 659, 784], dur=0.09),        # C-E-G, "hello"
    "start": _chime([587, 880], dur=0.08),             # quick rising blip
    "stop": _chime([880, 587], dur=0.08),              # quick falling blip
    "done": _chime([1047], dur=0.06, vol=0.22),        # tiny tick
    "error": _chime([233, 220], dur=0.16, vol=0.3),    # low buzz
}

def play(name):
    custom = os.path.join(APP_DIR, "sounds", f"{name}.wav")
    try:
        if os.path.exists(custom):
            winsound.PlaySound(custom,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif name in SOUNDS:
            winsound.PlaySound(SOUNDS[name],
                               winsound.SND_MEMORY | winsound.SND_ASYNC)
    except RuntimeError:
        pass


def fix_terms(text):
    """Apply the CORRECTIONS map for commonly misheard words."""
    for pat, rep in CORRECTIONS.items():
        text = re.sub(pat, rep, text, flags=re.I)
    return text


def quick_clean(text):
    """Regex fallback when the LLM is unavailable: strip common fillers."""
    text = re.sub(r"\b(um+|uh+|erm+)\b[,.]?\s*", "", text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def load_dictionary():
    """Return the dictionary word list, read fresh from dictionary.txt so edits
    apply on the next dictation. Seeds the file from the built-in DICTIONARY on
    first run; falls back to the built-in list if the file can't be read."""
    try:
        if not os.path.exists(DICT_FILE):
            with open(DICT_FILE, "w", encoding="utf-8") as f:
                f.write("# LocalFlow dictionary — one word or name per line.\n")
                f.write("# Whisper is biased toward these. Lines starting with "
                        "'#' are ignored.\n")
                f.write("# Edits take effect on your next dictation.\n\n")
                for w in DICTIONARY:
                    f.write(w + "\n")
        terms = []
        with open(DICT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    terms.append(line)
        return terms or DICTIONARY
    except OSError as e:
        log(f"Could not read dictionary.txt ({e}); using built-in list.")
        return DICTIONARY


# ----------------------------------------------------------------------------
# Easing for the overlay's motion. The pill grows and the transcript fades on
# time-based tweens (not per-frame snap) — chosen "hard-S" via prototype_anim.py
# on 2026-07-09: a cubic-bezier(.85,0,.15,1) that eases in and out steeply so
# the height barely moves at the ends and rushes through the middle.
# ----------------------------------------------------------------------------
def _cubic_bezier(x1, y1, x2, y2):
    """CSS-style cubic-bezier(x1,y1,x2,y2) timing function -> p(0..1)->value."""
    def bez(a1, a2, t):
        return (((1 - 3 * a2 + 3 * a1) * t + (3 * a2 - 6 * a1)) * t + 3 * a1) * t

    def solve(p):
        p = 0.0 if p < 0.0 else 1.0 if p > 1.0 else p
        lo, hi, t = 0.0, 1.0, p
        for _ in range(28):
            x = bez(x1, x2, t)
            if abs(x - p) < 1e-6:
                break
            if x < p:
                lo = t
            else:
                hi = t
            t = 0.5 * (lo + hi)
        return bez(y1, y2, t)
    return solve


EASE_HARD_S = _cubic_bezier(0.85, 0.0, 0.15, 1.0)   # window fade + pill expand
_EASE_CUBIC = QtCore.QEasingCurve(QtCore.QEasingCurve.InOutCubic)
def EASE_TEXT(p):                                    # transcript fade-in
    return _EASE_CUBIC.valueForProgress(0.0 if p < 0.0 else 1.0 if p > 1.0 else p)


# ----------------------------------------------------------------------------
# Overlay (Qt): frameless translucent pill, bottom-center, always on top.
# Voice bars react to the real microphone level; smooth fades; live
# transcript preview. Other threads set .state / .preview / .level.
# ----------------------------------------------------------------------------
class Overlay(QtWidgets.QWidget):
    # "Aurora · Mono" theme (chosen 2026-07-09 from prototype_overlay.py):
    # near-black glass pill with a painted soft shadow + rim light, greyscale
    # voice bars (no colour), and a small blinking state dot carrying the mode.
    PILL_W = 344
    BASE_H = 46
    PAD = 26                  # transparent margin around the pill for the shadow
    N_BARS = 12
    BG_TOP = QtGui.QColor(17, 17, 20, 247)
    BG_BOT = QtGui.QColor(8, 8, 10, 247)
    RIM_A = 42                # rim-light alpha along the top edge
    TEXT = QtGui.QColor(244, 244, 250)
    DIM = QtGui.QColor(140, 140, 162)
    PREV = QtGui.QColor(224, 224, 234)   # newest preview line
    PREV_OLD = QtGui.QColor(150, 150, 160)  # dimmed older line
    BARS = {"recording": (QtGui.QColor("#ececf2"), QtGui.QColor("#8d8d9c")),
            "processing": (QtGui.QColor("#c9c9d4"), QtGui.QColor("#77778a"))}
    DOT = {"recording": QtGui.QColor("#ff5c6a"),
           "processing": QtGui.QColor("#ffb02e")}
    TITLE = {"recording": "Listening", "processing": "Polishing…"}
    FADE_MS = 200                 # window opacity fade (hard-S)
    EXPAND_MS = 320               # pill height grow/shrink (hard-S)
    TEXT_MS = 220                 # newest transcript line fade-in (InOutCubic)

    def __init__(self):
        super().__init__(None,
                         QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.WindowStaysOnTopHint
                         | QtCore.Qt.Tool
                         | QtCore.Qt.WindowTransparentForInput
                         | QtCore.Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)

        self.state = "idle"      # set from other threads
        self.preview = ""
        self.level = 0.0         # mic RMS 0..1, set from audio callback

        self._shown_state = "idle"
        self._lvl = 0.0
        self._tick = 0
        self._heights = [3.0] * self.N_BARS
        self._phase = [random.uniform(0, 6.28) for _ in range(self.N_BARS)]
        self._speed = [random.uniform(0.35, 0.75) for _ in range(self.N_BARS)]
        self._lines = []
        self._prev_nlines = 0

        # ---- time-based tweens (hard-S expand + smooth fades) ----
        self._opacity = 0.0            # window opacity, tweened 0<->1
        self._op_frm = self._op_to = 0.0
        self._op_t0 = 0.0
        self._h = float(self.BASE_H)    # pill height, tweened toward _pill_h()
        self._h_frm = self._h_to = self._h
        self._h_t0 = 0.0
        self._bf_t0 = -10.0             # newest transcript line's fade start
        self._paint_ph = float(self.BASE_H)     # height paintEvent renders at
        self._paint_bottom_alpha = 1.0          # newest line's opacity

        self.f_title = QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold)
        self.f_hint = QtGui.QFont("Segoe UI", 7)
        self.f_prev = QtGui.QFont("Segoe UI", 9)

        self.setWindowOpacity(0.0)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(16)            # ~60fps so the eased motion stays smooth

    # -- animation loop --------------------------------------------------------
    def _animate(self):
        now = time.perf_counter()
        self._tick += 1
        state = self.state
        active = state in ("recording", "processing")

        if active and self._shown_state != state:
            self._shown_state = state

        # window opacity: hard-S fade toward 1 (active) or 0 (idle)
        target_op = 1.0 if active else 0.0
        if target_op != self._op_to:
            self._op_frm, self._op_to, self._op_t0 = self._opacity, target_op, now
        op_p = min(1.0, (now - self._op_t0) * 1000.0 / self.FADE_MS)
        self._opacity = self._op_frm + (self._op_to - self._op_frm) * EASE_HARD_S(op_p)

        if not active and self._opacity < 0.04:
            if self.isVisible():
                self.hide()
                self.preview = ""
                self._lines = []
                self._prev_nlines = 0
                self._h = self._h_frm = self._h_to = float(self.BASE_H)
            return
        if active and not self.isVisible():
            self._h = self._h_frm = self._h_to = float(self.BASE_H)
            self._place(self.BASE_H)
            self.show()
        self.setWindowOpacity(self._opacity)

        # smooth mic level
        self._lvl += (min(1.0, self.level) - self._lvl) * 0.35

        # bar targets
        for i in range(self.N_BARS):
            if self._shown_state == "recording":
                wobble = 0.4 + 0.6 * abs(math.sin(self._tick * self._speed[i]
                                                  + self._phase[i]))
                target = 2.5 + 11.5 * wobble * (0.18 + 1.6 * self._lvl)
            else:  # processing: gentle travelling wave
                target = 3 + 6.5 * abs(math.sin(self._tick * 0.22 - i * 0.45))
            target = min(target, 13.0)
            self._heights[i] += (target - self._heights[i]) * 0.45

        # preview lines (wrap to 2 lines, keep the tail)
        fm = QtGui.QFontMetrics(self.f_prev)
        avail = self.PILL_W - 48
        lines, line = [], ""
        for wd in self.preview.split():
            trial = (line + " " + wd).strip()
            if fm.horizontalAdvance(trial) > avail:
                lines.append(line)
                line = wd
            else:
                line = trial
        if line:
            lines.append(line)
        self._lines = lines[-2:]

        # a newly-appeared transcript line fades in (smooth opacity, InOutCubic)
        if len(self._lines) > self._prev_nlines:
            self._bf_t0 = now
        self._prev_nlines = len(self._lines)
        txt_p = min(1.0, (now - self._bf_t0) * 1000.0 / self.TEXT_MS)
        self._paint_bottom_alpha = EASE_TEXT(txt_p)

        # pill height: hard-S tween toward the target (no more instant snap)
        target_h = self._pill_h()
        if target_h != self._h_to:
            self._h_frm, self._h_to, self._h_t0 = self._h, float(target_h), now
        h_p = min(1.0, (now - self._h_t0) * 1000.0 / self.EXPAND_MS)
        self._h = self._h_frm + (self._h_to - self._h_frm) * EASE_HARD_S(h_p)
        self._paint_ph = self._h

        win_w = self.PILL_W + self.PAD * 2
        win_h = int(round(self._h)) + self.PAD * 2
        if win_h != self.height() or win_w != self.width():
            self._place(self._h)
        self.update()

    def _pill_h(self):
        return self.BASE_H + (len(self._lines) * 17 + 8 if self._lines else 0)

    def _place(self, ph=None):
        # the window carries a PAD-wide transparent margin so the painted
        # shadow has room; geometry is pill size + margin on every side.
        ph = self.BASE_H if ph is None else int(round(ph))
        win_w = self.PILL_W + self.PAD * 2
        win_h = ph + self.PAD * 2
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - win_w) // 2
        y = screen.y() + screen.height() - win_h - 30
        self.setGeometry(x, y, win_w, win_h)

    # -- painting ---------------------------------------------------------------
    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        ph = self._paint_ph
        px, py = self.PAD, self.PAD
        state = self._shown_state
        r = 20 if self._lines else ph / 2

        # soft painted shadow: three widening, fading layers under the pill
        for grow, alpha in ((4, 30), (10, 15), (18, 6)):
            sp = QtGui.QPainterPath()
            sp.addRoundedRect(px - grow / 2, py - grow / 2 + 4,
                              self.PILL_W + grow, ph + grow,
                              r + grow / 2, r + grow / 2)
            p.fillPath(sp, QtGui.QColor(0, 0, 0, alpha))

        # near-black glass body: vertical gradient
        body = QtGui.QPainterPath()
        body.addRoundedRect(px, py, self.PILL_W, ph, r, r)
        g = QtGui.QLinearGradient(0, py, 0, py + ph)
        g.setColorAt(0, self.BG_TOP)
        g.setColorAt(1, self.BG_BOT)
        p.fillPath(body, g)

        # rim light: catches the top edge, fades toward the bottom
        rim = QtGui.QLinearGradient(0, py, 0, py + ph)
        rim.setColorAt(0, QtGui.QColor(255, 255, 255, self.RIM_A))
        rim.setColorAt(0.35, QtGui.QColor(255, 255, 255, 12))
        rim.setColorAt(1, QtGui.QColor(255, 255, 255, 7))
        p.setPen(QtGui.QPen(QtGui.QBrush(rim), 1.2))
        p.drawPath(body)

        # greyscale voice bars (no glow — the quiet 'Mono' look)
        c1, c2 = self.BARS.get(state, (self.DIM, self.DIM))
        x0 = px + 22
        grad = QtGui.QLinearGradient(x0, 0, x0 + self.N_BARS * 6, 0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        cy = py + self.BASE_H / 2
        for i in range(self.N_BARS):
            bh = self._heights[i]
            bx = x0 + i * 6
            bar = QtGui.QPainterPath()
            bar.addRoundedRect(bx, cy - bh, 3.0, bh * 2, 1.5, 1.5)
            p.fillPath(bar, QtGui.QBrush(grad))

        # state dot (red = recording, amber = polishing), gently blinking
        tx = x0 + self.N_BARS * 6 + 12
        dot = self.DOT.get(state)
        if dot is not None:
            dc = QtGui.QColor(dot)
            if state == "recording" and (self._tick // 16) % 2:
                dc.setAlpha(90)
            p.setBrush(dc)
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QPointF(tx + 3, cy), 3.4, 3.4)
            tx += 14

        # title
        p.setFont(self.f_title)
        p.setPen(self.TEXT)
        p.drawText(QtCore.QRectF(tx, py, 170, self.BASE_H),
                   QtCore.Qt.AlignVCenter, self.TITLE.get(state, ""))

        # hotkey hint, right-aligned inside the pill
        p.setFont(self.f_hint)
        p.setPen(self.DIM)
        p.drawText(QtCore.QRectF(px, py, self.PILL_W - 18, self.BASE_H),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                   "Ctrl · Alt · /")

        # live transcript preview (older line dimmed, newest bright and fading
        # in via _paint_bottom_alpha so a fresh line doesn't pop)
        if self._lines:
            p.setFont(self.f_prev)
            for i, ln in enumerate(self._lines):
                older = i == 0 and len(self._lines) > 1
                col = QtGui.QColor(self.PREV_OLD if older else self.PREV)
                if i == len(self._lines) - 1:
                    col.setAlphaF(max(0.0, min(1.0, self._paint_bottom_alpha)))
                p.setPen(col)
                p.drawText(QtCore.QPointF(x0,
                           py + self.BASE_H + 4 + (i + 0.75) * 17), ln)
        p.end()


def _tray_icon(status="idle"):
    accent = {"recording": QtGui.QColor("#ff5c6a"),
              "processing": QtGui.QColor("#ffb02e")}.get(
        status, QtGui.QColor(126, 132, 170))
    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor(20, 20, 32))
    p.setPen(QtGui.QPen(QtGui.QColor(48, 48, 74), 2))
    p.drawRoundedRect(5, 5, 54, 54, 16, 16)
    p.setBrush(accent)
    p.setPen(QtCore.Qt.NoPen)
    p.drawRoundedRect(26, 13, 12, 24, 6, 6)
    pen = QtGui.QPen(accent, 3)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(20, 22, 24, 22, 180 * 16, 180 * 16)
    p.drawLine(32, 44, 32, 50)
    p.drawLine(25, 51, 39, 51)
    p.end()
    return QtGui.QIcon(pm)


# ----------------------------------------------------------------------------
# Win32 paste helpers: wait for the user to release the hotkey modifiers before
# synthesizing Ctrl+V, and detect when the target window is higher integrity
# than us (UIPI silently blocks injected input into elevated windows). Both are
# defensive — any failure degrades to "proceed with a normal paste".
# ----------------------------------------------------------------------------
_MOD_VKS = (0x11, 0x12, 0x10, 0x5B, 0x5C)   # CTRL, ALT, SHIFT, LWIN, RWIN

def wait_modifiers_released(timeout=1.0):
    """Block until the hotkey's Ctrl/Alt (etc.) are physically up, so a still-
    held modifier can't corrupt the synthesized Ctrl+V."""
    user32 = ctypes.windll.user32
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MOD_VKS):
            return
        time.sleep(0.01)


def _process_integrity(pid):
    """Integrity-level RID of a process (pid=None means the current process).
    Returns an int RID (medium=0x2000, high=0x3000, …) or None if it can't be
    determined."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    a32 = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    a32.OpenProcessToken.restype = wintypes.BOOL
    a32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.HANDLE)]
    a32.GetTokenInformation.restype = wintypes.BOOL
    a32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    a32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    a32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    a32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    a32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenIntegrityLevel = 25

    if pid is None:
        hproc, close = k32.GetCurrentProcess(), False
    else:
        hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        close = True
        if not hproc:
            return None
    try:
        tok = wintypes.HANDLE()
        if not a32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            size = wintypes.DWORD()
            a32.GetTokenInformation(tok, TokenIntegrityLevel, None, 0,
                                    ctypes.byref(size))
            if not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if not a32.GetTokenInformation(tok, TokenIntegrityLevel, buf, size,
                                           ctypes.byref(size)):
                return None
            sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            count = a32.GetSidSubAuthorityCount(sid)[0]
            return int(a32.GetSidSubAuthority(sid, count - 1)[0])
        finally:
            k32.CloseHandle(tok)
    finally:
        if close:
            k32.CloseHandle(hproc)


def foreground_is_elevated():
    """True only when we can positively confirm the foreground window runs at a
    higher integrity level than us — so a false reading never blocks a normal
    paste. Any error is swallowed as 'not elevated'."""
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        target = _process_integrity(pid.value)
        mine = _process_integrity(None)
        if target is None or mine is None:
            return False          # can't tell → behave as before, try to paste
        return target > mine
    except Exception as e:
        log(f"Elevation check failed ({type(e).__name__}); assuming normal.")
        return False


class Notifier(QtCore.QObject):
    """Marshals cross-thread user notifications onto the GUI thread."""
    message = QtCore.Signal(str, str)   # title, body


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
class App:
    def __init__(self, overlay=None):
        self.recording = False
        self.busy = False
        self.frames = []
        self.stream = None
        self.lock = threading.Lock()
        self.whisper_lock = threading.Lock()  # one transcription at a time
        self.tray = None
        self.overlay = overlay
        self.notifier = Notifier()
        self.hotwords = ""
        self._reload_hotwords()

        log(f"Loading whisper model '{WHISPER_MODEL}'...")
        try:
            self.whisper = WhisperModel(WHISPER_MODEL, device="cuda",
                                        compute_type=WHISPER_COMPUTE)
            # force CUDA init now so failures surface here, not mid-dictation
            self.whisper.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
            log(f"Whisper ready (GPU, {WHISPER_COMPUTE}).")
        except Exception as e:
            log(f"GPU unavailable ({type(e).__name__}), using CPU int8.")
            self.whisper = WhisperModel("small.en", device="cpu",
                                        compute_type="int8")
            log("Whisper ready (CPU, small.en).")

        threading.Thread(target=self._warm_ollama, daemon=True).start()

    def _reload_hotwords(self):
        """Re-read the user dictionary so edits apply without a restart."""
        self.hotwords = " ".join(load_dictionary())

    # -- Ollama ---------------------------------------------------------------
    def _warm_ollama(self):
        try:
            # unload any other resident models first — this machine is
            # memory-tight and a stale model with a big context starves us
            r = OLLAMA_SESSION.get(f"{OLLAMA_URL}/api/ps", timeout=10)
            for m in r.json().get("models", []):
                if m.get("name") != OLLAMA_MODEL:
                    log(f"Unloading other Ollama model: {m['name']}")
                    OLLAMA_SESSION.post(f"{OLLAMA_URL}/api/generate",
                                        json={"model": m["name"],
                                              "keep_alive": 0},
                                        timeout=30)
            OLLAMA_SESSION.post(f"{OLLAMA_URL}/api/generate",
                                json={"model": OLLAMA_MODEL, "prompt": "",
                                      "keep_alive": OLLAMA_KEEP_ALIVE,
                                      "options": {"num_ctx": OLLAMA_NUM_CTX}},
                                timeout=180)
            log(f"Ollama model '{OLLAMA_MODEL}' warm.")
        except requests.RequestException as e:
            log(f"Warning: could not reach Ollama ({e}). "
                f"Raw transcripts will be pasted uncleaned.")

    def cleanup_text(self, text):
        if len(text.split()) <= 3:      # too short to bother the LLM
            return quick_clean(text)
        try:
            parts = []
            with OLLAMA_SESSION.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": CLEANUP_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "stream": True,
                    "keep_alive": OLLAMA_KEEP_ALIVE,
                    "options": {"temperature": 0.1,
                                "num_ctx": OLLAMA_NUM_CTX},
                },
                timeout=OLLAMA_TIMEOUT,
                stream=True,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        parts.append(piece)
                        # show the text polishing live in the overlay preview
                        if self.overlay is not None:
                            self.overlay.preview = fix_terms("".join(parts))
                    if chunk.get("done"):
                        break
            cleaned = "".join(parts).strip()
            # guard against a model that ignored instructions and went rogue
            if cleaned and len(cleaned) < len(text) * 3 + 80:
                return cleaned
            return quick_clean(text)
        except (requests.RequestException, ValueError, KeyError) as e:
            log(f"Ollama cleanup failed ({e}); pasting raw transcript.")
            return quick_clean(text)

    # -- Recording ------------------------------------------------------------
    def toggle(self):
        with self.lock:
            if self.busy:
                log("Hotkey pressed, but still processing — ignored.")
                return
            if not self.recording:
                self._start_recording()
            else:
                self.busy = True
                self._stop_recording()
                threading.Thread(target=self._process, daemon=True).start()

    def _audio_cb(self, data, *_):
        self.frames.append(data.copy())
        if self.overlay is not None:
            rms = float(np.sqrt((data ** 2).mean()))
            self.overlay.level = min(1.0, rms * 14)

    def _start_recording(self):
        self._reload_hotwords()   # pick up dictionary.txt edits per dictation
        try:
            self.frames = []
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                callback=self._audio_cb,
            )
            self.stream.start()
        except Exception as e:
            log(f"Microphone error: {e}")
            self._set_status("idle")
            play("error")
            return
        self.recording = True
        self._set_status("recording")
        play("start")
        log("Recording... (Ctrl+Alt+/ to stop)")
        threading.Timer(MAX_RECORD_SECONDS, self._safety_stop).start()
        threading.Thread(target=self._live_preview, daemon=True).start()

    def _live_preview(self):
        """Transcribe accumulated audio while recording so text appears
        on screen as you speak."""
        while self.recording:
            time.sleep(LIVE_PREVIEW_EVERY)
            if not self.recording or not self.frames:
                continue
            try:
                audio = np.concatenate(self.frames).flatten()
                if len(audio) < SAMPLE_RATE:  # wait for 1s of audio
                    continue
                audio = audio[-SAMPLE_RATE * 30:]  # last 30s is plenty
                with self.whisper_lock:
                    if not self.recording:
                        break
                    segments, _ = self.whisper.transcribe(
                        audio, beam_size=1, vad_filter=True,
                        condition_on_previous_text=False,
                        hotwords=self.hotwords,
                        initial_prompt=self.hotwords)
                    text = " ".join(s.text.strip() for s in segments).strip()
                if text and self.overlay and self.recording:
                    self.overlay.preview = fix_terms(text)
            except Exception as e:
                log(f"Live preview error: {type(e).__name__}: {e}")
                break

    def _safety_stop(self):
        with self.lock:
            if self.recording and not self.busy:
                self.busy = True
                self._stop_recording()
                threading.Thread(target=self._process, daemon=True).start()

    def _stop_recording(self):
        self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception as e:
            log(f"Error closing mic stream: {e}")
        self.stream = None
        play("stop")

    # -- Pipeline -------------------------------------------------------------
    def _process(self):
        try:
            self._set_status("processing")
            if not self.frames:
                return
            audio = np.concatenate(self.frames).flatten()
            if len(audio) < SAMPLE_RATE // 4:  # < 0.25s, ignore
                log("Recording too short; ignored.")
                return
            log(f"Processing {len(audio) / SAMPLE_RATE:.1f}s of audio...")

            t0 = time.perf_counter()
            with self.whisper_lock:
                segments, _info = self.whisper.transcribe(
                    audio, beam_size=5, vad_filter=True,
                    hotwords=self.hotwords,
                    initial_prompt=self.hotwords)
                raw = " ".join(s.text.strip() for s in segments).strip()
            t1 = time.perf_counter()
            if not raw:
                log("Heard nothing.")
                return
            raw = fix_terms(raw)
            log(f"Transcript ({t1 - t0:.2f}s): {raw}")

            cleaned = fix_terms(self.cleanup_text(raw))
            t2 = time.perf_counter()
            log(f"Cleaned    ({t2 - t1:.2f}s): {cleaned}")

            self._paste(cleaned)
            play("done")
            log(f"Done in {time.perf_counter() - t0:.2f}s total.")
        except Exception as e:
            log(f"Pipeline error: {type(e).__name__}: {e}")
        finally:
            self.busy = False
            self._set_status("idle")

    def _paste(self, text):
        if foreground_is_elevated():
            # UIPI silently swallows an injected Ctrl+V into an elevated window.
            # Leave the text on the clipboard and tell the user, rather than
            # failing invisibly.
            try:
                pyperclip.copy(text)
            except pyperclip.PyperclipException:
                pass
            self.notifier.message.emit(
                "LocalFlow — can't paste here",
                "This window is running as administrator. Your text is on the "
                "clipboard (press Ctrl+V), or run LocalFlow as admin.")
            play("error")
            log("Foreground window is elevated; left text on clipboard.")
            return

        old_clip = None
        try:
            old_clip = pyperclip.paste()
        except pyperclip.PyperclipException:
            pass
        pyperclip.copy(text)
        wait_modifiers_released()   # a still-held Ctrl/Alt would corrupt Ctrl+V
        time.sleep(0.05)
        kb = keyboard.Controller()
        with kb.pressed(keyboard.Key.ctrl):
            kb.press("v")
            kb.release("v")
        if old_clip is not None:
            threading.Timer(
                1.0, lambda: self._restore_clip(text, old_clip)).start()

    def _restore_clip(self, ours, old_clip):
        """Restore the previous clipboard only if our dictated text is still
        there — if the user copied something new meanwhile, leave it alone."""
        try:
            if pyperclip.paste() == ours:
                pyperclip.copy(old_clip)
        except pyperclip.PyperclipException:
            pass

    # -- Status (tray + overlay) ----------------------------------------------
    def _set_status(self, status):
        if self.overlay:
            self.overlay.state = status
        if self.tray:
            self.tray.setIcon(_tray_icon(status))
            self.tray.setToolTip(f"LocalFlow — {status}")

    def quit(self):
        log("Quitting.")
        QtWidgets.QApplication.quit()
        os._exit(0)


def hotkey_thread(app):
    """Win32 RegisterHotKey + message loop. Must run in one thread."""
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, HOTKEY_MODS, HOTKEY_VK):
        err = ctypes.get_last_error()
        log(f"ERROR: could not register Ctrl+Alt+/ (code {err}). "
            f"Another app may already use this hotkey.")
        play("error")
        return
    log("Hotkey Ctrl+Alt+/ registered (Win32).")
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            log("Hotkey pressed.")
            app.toggle()


def main():
    # single-instance guard: two copies would fight over the hotkey
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        print("LocalFlow is already running. Quit it from the tray icon "
              "before starting another.")
        play("error")
        time.sleep(1)
        sys.exit(1)

    qapp = QtWidgets.QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)

    overlay = Overlay()
    app = App(overlay)

    threading.Thread(target=hotkey_thread, args=(app,), daemon=True).start()
    log("LocalFlow ready. Press Ctrl+Alt+/ to dictate.")
    play("ready")

    tray = QtWidgets.QSystemTrayIcon(_tray_icon("idle"))
    menu = QtWidgets.QMenu()
    menu.addAction("Quit LocalFlow", app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip("LocalFlow — idle")
    tray.show()
    app.tray = tray
    app.notifier.message.connect(
        lambda title, body: tray.showMessage(
            title, body, QtWidgets.QSystemTrayIcon.MessageIcon.Warning, 6000))

    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
