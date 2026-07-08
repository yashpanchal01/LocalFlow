"""
LocalFlow — a local Wispr Flow clone.

Press Ctrl+Alt+/ to start recording, press it again to stop.
Audio is transcribed with faster-whisper (live, while you speak), cleaned
up with a local Ollama model, and pasted into whatever app has focus.

Everything runs on-device. No cloud, no subscription.
"""

import ctypes
import io
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
WHISPER_COMPUTE = "int8_float16"    # quantized on GPU = fits next to the LLM
OLLAMA_MODEL = "qwen2.5:3b"     # small enough to always fit next to whisper
OLLAMA_URL = "http://localhost:11434"
OLLAMA_KEEP_ALIVE = "2h"        # keep model warm between dictations
OLLAMA_NUM_CTX = 2048           # small context = far less VRAM than default
OLLAMA_TIMEOUT = 30             # give up and paste raw transcript after this
MAX_RECORD_SECONDS = 120        # safety cutoff
LIVE_PREVIEW_EVERY = 2.0        # seconds between live-transcription passes

# Words/names whisper should recognize. Add your own jargon here.
DICTIONARY = ["Claude Code", "Claude", "Ollama", "Whisper", "LocalFlow",
              "GitHub", "Python", "VS Code", "API", "Anthropic"]

# Common mishearings, fixed after transcription (case-insensitive regex).
CORRECTIONS = {
    r"\b(?:blood|cloud|clod|clot|clawed|clogged|clout) ?"
    r"(?:cold|code|coat|called|cord)\b": "Claude Code",
    r"\bo+ ?lama\b": "Ollama",
    r"\bget ?hub\b": "GitHub",
}

SAMPLE_RATE = 16000             # whisper's native rate
SINGLE_INSTANCE_PORT = 52739    # refuses to start twice
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(APP_DIR, "localflow.log")

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


# ----------------------------------------------------------------------------
# Overlay (Qt): frameless translucent pill, bottom-center, always on top.
# Voice bars react to the real microphone level; smooth fades; live
# transcript preview. Other threads set .state / .preview / .level.
# ----------------------------------------------------------------------------
class Overlay(QtWidgets.QWidget):
    W = 330
    BASE_H = 40
    N_BARS = 11
    BG = QtGui.QColor(14, 14, 22, 238)
    BORDER = QtGui.QColor(255, 255, 255, 26)
    TEXT = QtGui.QColor(242, 242, 248)
    DIM = QtGui.QColor(150, 150, 172)
    ACCENT = {"recording": (QtGui.QColor("#ff5c6a"), QtGui.QColor("#ff8f5c")),
              "processing": (QtGui.QColor("#ffb02e"), QtGui.QColor("#ffd36e"))}
    TITLE = {"recording": "Listening", "processing": "Polishing…"}

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
        self._opacity = 0.0
        self._lvl = 0.0
        self._tick = 0
        self._heights = [3.0] * self.N_BARS
        self._phase = [random.uniform(0, 6.28) for _ in range(self.N_BARS)]
        self._speed = [random.uniform(0.35, 0.75) for _ in range(self.N_BARS)]
        self._lines = []

        self.f_title = QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold)
        self.f_hint = QtGui.QFont("Segoe UI", 7)
        self.f_prev = QtGui.QFont("Segoe UI", 9)

        self.setWindowOpacity(0.0)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(33)

    # -- animation loop --------------------------------------------------------
    def _animate(self):
        self._tick += 1
        state = self.state
        active = state in ("recording", "processing")

        if active and self._shown_state != state:
            self._shown_state = state
        target_op = 1.0 if active else 0.0
        self._opacity += (target_op - self._opacity) * 0.28
        if not active and self._opacity < 0.04:
            if self.isVisible():
                self.hide()
                self.preview = ""
                self._lines = []
            return
        if active and not self.isVisible():
            self._place()
            self.show()
        self.setWindowOpacity(self._opacity)

        # smooth mic level
        self._lvl += (min(1.0, self.level) - self._lvl) * 0.35

        # bar targets
        for i in range(self.N_BARS):
            if self._shown_state == "recording":
                wobble = 0.4 + 0.6 * abs(math.sin(self._tick * self._speed[i]
                                                  + self._phase[i]))
                target = 2.5 + 11 * wobble * (0.18 + 1.6 * self._lvl)
            else:  # processing: gentle travelling wave
                target = 3 + 6 * abs(math.sin(self._tick * 0.22 - i * 0.45))
            target = min(target, 12.5)
            self._heights[i] += (target - self._heights[i]) * 0.45

        # preview lines (wrap to 2 lines, keep the tail)
        fm = QtGui.QFontMetrics(self.f_prev)
        avail = self.W - 48
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

        new_h = self.BASE_H + (len(self._lines) * 16 + 6 if self._lines else 0)
        if new_h != self.height() or self.width() != self.W:
            self._place(new_h)
        self.update()

    def _place(self, h=None):
        h = h or self.BASE_H
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - self.W) // 2
        y = screen.y() + screen.height() - h - 56
        self.setGeometry(x, y, self.W, h)

    # -- painting ---------------------------------------------------------------
    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        state = self._shown_state

        # pill
        r = 19 if self._lines else h / 2
        path = QtGui.QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, r, r)
        p.fillPath(path, self.BG)
        pen = QtGui.QPen(self.BORDER, 1)
        p.setPen(pen)
        p.drawPath(path)

        c1, c2 = self.ACCENT.get(state, (self.DIM, self.DIM))
        grad = QtGui.QLinearGradient(20, 0, 20 + self.N_BARS * 6, 0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)

        # voice bars, with a soft glow behind them
        cy = self.BASE_H / 2
        glow = QtGui.QColor(c1)
        glow.setAlpha(34)
        for i in range(self.N_BARS):
            bh = self._heights[i]
            bx = 20 + i * 6
            halo = QtGui.QPainterPath()
            halo.addRoundedRect(bx - 1.6, cy - bh - 2.2, 6.0,
                                (bh + 2.2) * 2, 3.0, 3.0)
            p.fillPath(halo, glow)
            bar = QtGui.QPainterPath()
            bar.addRoundedRect(bx, cy - bh, 2.8, bh * 2, 1.4, 1.4)
            p.fillPath(bar, QtGui.QBrush(grad))

        # title
        p.setFont(self.f_title)
        p.setPen(self.TEXT)
        tx = 20 + self.N_BARS * 6 + 12
        p.drawText(QtCore.QRectF(tx, 0, 160, self.BASE_H),
                   QtCore.Qt.AlignVCenter, self.TITLE.get(state, ""))

        # hotkey hint, right-aligned
        p.setFont(self.f_hint)
        p.setPen(self.DIM)
        p.drawText(QtCore.QRectF(0, 0, w - 18, self.BASE_H),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                   "Ctrl · Alt · /")

        # preview
        if self._lines:
            p.setFont(self.f_prev)
            p.setPen(self.DIM)
            for i, ln in enumerate(self._lines):
                p.drawText(QtCore.QPointF(20, self.BASE_H + 6 + (i + 0.7) * 16),
                           ln)
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
        self.hotwords = " ".join(DICTIONARY)

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
            r = OLLAMA_SESSION.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": CLEANUP_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "stream": False,
                    "keep_alive": OLLAMA_KEEP_ALIVE,
                    "options": {"temperature": 0.1,
                                "num_ctx": OLLAMA_NUM_CTX},
                },
                timeout=OLLAMA_TIMEOUT,
            )
            r.raise_for_status()
            cleaned = r.json()["message"]["content"].strip()
            # guard against a model that ignored instructions and went rogue
            if cleaned and len(cleaned) < len(text) * 3 + 80:
                return cleaned
            return quick_clean(text)
        except (requests.RequestException, KeyError) as e:
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
                        hotwords=self.hotwords)
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
                    audio, beam_size=1, vad_filter=True,
                    hotwords=self.hotwords)
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
        old_clip = None
        try:
            old_clip = pyperclip.paste()
        except pyperclip.PyperclipException:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)
        kb = keyboard.Controller()
        with kb.pressed(keyboard.Key.ctrl):
            kb.press("v")
            kb.release("v")
        if old_clip is not None:
            threading.Timer(1.0, lambda: pyperclip.copy(old_clip)).start()

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

    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
