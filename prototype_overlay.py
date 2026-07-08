# =============================================================================
# PROTOTYPE — THROWAWAY. Delete once a winner is folded into localflow.py.
#
# Question: what should the LocalFlow overlay look like?
# Four structurally different variants, driven by a fake dictation loop
# (no mic, no models, no hotkey — safe to run beside the real app):
#   A — Aurora Glass   : current pill layout, properly polished (dark glass)
#   B — Ribbon HUD     : edge-to-edge bottom strip w/ scrolling waveform (cyan)
#   C — Orb Island     : light morphing capsule -> expands into a card
#   D — Terminal Ticker: monospace corner chip, green-on-black, rec timer
#
# Run live :  py -3.13 prototype_overlay.py
#             ←/→ switch variant · Space next phase · Esc quit
#             (click the top bar first if keys stop responding)
# Shots    :  py -3.13 prototype_overlay.py --shoot   (PNGs, no interaction)
# =============================================================================
import math
import os
import random
import sys
import time
from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets

RAW = ("so i was thinking we could test claude code with ollama today um and "
       "maybe see how the new overlay looks")
CLEAN = ("So I was thinking we could test Claude Code with Ollama today, and "
         "maybe see how the new overlay looks.")

SHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratchpad", "proto_shots")


def mono_font(pt, weight=QtGui.QFont.Normal):
    for fam in ("Cascadia Mono", "Consolas", "Courier New"):
        f = QtGui.QFont(fam, pt, weight)
        if QtGui.QFontInfo(f).family() == fam:
            return f
    return QtGui.QFont("Courier New", pt, weight)


def wrap_tail(text, font, avail, max_lines):
    """Word-wrap `text`, return the last `max_lines` lines (same idea as the
    real overlay: keep the tail while dictating)."""
    fm = QtGui.QFontMetrics(font)
    lines, line = [], ""
    for wd in text.split():
        trial = (line + " " + wd).strip()
        if fm.horizontalAdvance(trial) > avail and line:
            lines.append(line)
            line = wd
        else:
            line = trial
    if line:
        lines.append(line)
    return lines[-max_lines:]


def elide_front(text, font, avail):
    fm = QtGui.QFontMetrics(font)
    if fm.horizontalAdvance(text) <= avail:
        return text
    words = text.split()
    while words and fm.horizontalAdvance("…" + " ".join(words)) > avail:
        words.pop(0)
    return "…" + " ".join(words)


def fake_level(t):
    """Speech-ish mic level: syllable wobble + pauses."""
    syll = abs(math.sin(t * 6.1)) * abs(math.sin(t * 1.9 + 1.2))
    pause = 0.15 if math.sin(t * 0.8) < -0.55 else 1.0
    return min(1.0, (0.10 + 0.75 * syll) * pause
               + random.uniform(0.0, 0.06))


def draw_keycap_hint(p, rect, f_kbd, keys, text_color, dim_color):
    """
    Draw keybind hint with keyboard button-style representation.
    """
    fm = QtGui.QFontMetrics(f_kbd)
    sep_char = "+"
    sep_w = fm.horizontalAdvance(sep_char)
    gap = 4
    
    key_widths = []
    for key in keys:
        kw = max(fm.horizontalAdvance(key) + 12, 18)
        key_widths.append(kw)
        
    total_w = sum(key_widths) + (len(keys) - 1) * (sep_w + gap * 2)
    
    # Calculate starting point for right alignment
    x0 = rect.right() - total_w
    key_h = 16
    y0 = rect.top() + (rect.height() - key_h) / 2.0
    
    p.save()
    p.setFont(f_kbd)
    
    cx = x0
    cy = y0
    
    # Check if light or dark theme based on text color lightness
    is_dark_bg = text_color.lightnessF() > 0.5
    
    if is_dark_bg:
        # Dark theme key styling
        bg_grad_top = QtGui.QColor(48, 48, 56)
        bg_grad_bot = QtGui.QColor(32, 32, 38)
        depth_color = QtGui.QColor(14, 14, 18)
        border_color = QtGui.QColor(76, 76, 92, 160)
        text_pen = text_color
    else:
        # Light theme key styling
        bg_grad_top = QtGui.QColor(252, 252, 254)
        bg_grad_bot = QtGui.QColor(226, 226, 232)
        depth_color = QtGui.QColor(172, 172, 180)
        border_color = QtGui.QColor(188, 188, 198)
        text_pen = text_color
        
    for i, key in enumerate(keys):
        kw = key_widths[i]
        
        # 1. 3D shadow/depth
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(depth_color)
        p.drawRoundedRect(QtCore.QRectF(cx, cy + 1, kw, key_h), 3.0, 3.0)
        
        # 2. Keycap body
        body_grad = QtGui.QLinearGradient(cx, cy, cx, cy + key_h - 1)
        body_grad.setColorAt(0, bg_grad_top)
        body_grad.setColorAt(1, bg_grad_bot)
        p.setBrush(body_grad)
        p.drawRoundedRect(QtCore.QRectF(cx, cy, kw, key_h - 1), 3.0, 3.0)
        
        # 3. Border
        p.setPen(QtGui.QPen(border_color, 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(QtCore.QRectF(cx + 0.5, cy + 0.5, kw - 1, key_h - 2), 2.5, 2.5)
        
        # 4. Text
        p.setPen(text_pen)
        # Shift text up slightly so it centers visually with the bottom shadow lip
        p.drawText(QtCore.QRectF(cx, cy - 0.5, kw, key_h - 1), QtCore.Qt.AlignCenter, key)
        
        cx += kw
        
        if i < len(keys) - 1:
            cx += gap
            p.setPen(dim_color)
            p.drawText(QtCore.QRectF(cx, cy - 0.5, sep_w, key_h - 1), QtCore.Qt.AlignCenter, sep_char)
            cx += sep_w + gap
            
    p.restore()


# =============================================================================
# Base: window plumbing + fade in/out, shared by all variants.
# Each variant owns its own geometry, theme and painting entirely.
# =============================================================================
class BaseVariant(QtWidgets.QWidget):
    NAME = "?"

    def __init__(self):
        super().__init__(None,
                         QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.WindowStaysOnTopHint
                         | QtCore.Qt.Tool
                         | QtCore.Qt.WindowTransparentForInput
                         | QtCore.Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)

        self.state = "idle"          # set from outside: idle/recording/processing
        self.preview = ""
        self.level = 0.0

        self._shown_state = "idle"
        self._opacity = 0.0
        self._lvl = 0.0
        self._tick = 0
        self._state_since = time.monotonic()
        self._last_state = "idle"

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._animate)
        self.timer.start(33)

    def elapsed(self):
        return time.monotonic() - self._state_since

    def _animate(self):
        self._tick += 1
        if self.state != self._last_state:
            self._last_state = self.state
            self._state_since = time.monotonic()
        active = self.state in ("recording", "processing")
        if active:
            self._shown_state = self.state
        self._opacity += ((1.0 if active else 0.0) - self._opacity) * 0.28
        if not active and self._opacity < 0.04:
            if self.isVisible():
                self.hide()
                self.preview = ""
            return
        self._lvl += (min(1.0, self.level) - self._lvl) * 0.35
        self._tick_visuals()
        self._layout()
        if active and not self.isVisible():
            self.show()
        self.setWindowOpacity(self._opacity)
        self.update()

    # --- for --shoot: settle animations without a running event loop ---------
    def force(self, state, preview, ticks=70):
        self.state = state
        self._shown_state = state
        self._last_state = state
        self._opacity = 1.0
        self.preview = preview
        self._state_since = time.monotonic() - 7.0   # timers show 0:07
        for i in range(ticks):
            self._tick += 1
            self.level = fake_level(self._tick * 0.033)
            self._lvl += (min(1.0, self.level) - self._lvl) * 0.35
            self._tick_visuals()
        self._layout()

    def _tick_visuals(self):
        pass

    def _layout(self):
        pass


# =============================================================================
# A — Aurora Glass: the current pill, polished. Dark glass, rim light,
# painted soft shadow, gradient bars with glow.
# =============================================================================
class AuroraGlass(BaseVariant):
    NAME = "Aurora · Ember"
    ANCHOR = "center"
    PILL_W = 344
    BASE_H = 46
    PAD = 26                       # margin around the pill for the shadow
    N_BARS = 12
    ACCENT = {"recording": (QtGui.QColor("#ff5c6a"), QtGui.QColor("#ffa04d")),
              "processing": (QtGui.QColor("#ffb02e"), QtGui.QColor("#ffe08a"))}
    TITLE = {"recording": "Listening", "processing": "Polishing…"}
    # theme knobs (overridden by the Aurora sub-variants)
    BG_TOP = QtGui.QColor(30, 30, 46, 244)
    BG_BOT = QtGui.QColor(13, 13, 22, 244)
    RIM_A = 64                     # rim-light alpha at the top edge
    GLOW = True                    # halo behind the bars
    REC_DOT = False                # blinking dot before the title (Mono)

    def _accent(self, state):
        return self.ACCENT.get(state, (QtGui.QColor(150, 150, 172),) * 2)

    def __init__(self):
        super().__init__()
        self._heights = [3.0] * self.N_BARS
        self._phase = [random.uniform(0, 6.28) for _ in range(self.N_BARS)]
        self._speed = [random.uniform(0.35, 0.75) for _ in range(self.N_BARS)]
        self._lines = []
        self.f_title = QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold)
        self.f_hint = QtGui.QFont("Segoe UI", 7)
        self.f_kbd = QtGui.QFont("Segoe UI", 7, QtGui.QFont.Bold)
        self.f_prev = QtGui.QFont("Segoe UI", 9)

    def _tick_visuals(self):
        for i in range(self.N_BARS):
            if self._shown_state == "recording":
                wob = 0.4 + 0.6 * abs(math.sin(self._tick * self._speed[i]
                                               + self._phase[i]))
                target = 2.5 + 11.5 * wob * (0.18 + 1.6 * self._lvl)
            else:
                target = 3 + 6.5 * abs(math.sin(self._tick * 0.22 - i * 0.45))
            self._heights[i] += (min(target, 13.0) - self._heights[i]) * 0.45
        self._lines = wrap_tail(self.preview, self.f_prev,
                                self.PILL_W - 48, 2)

    def _pill_h(self):
        return self.BASE_H + (len(self._lines) * 17 + 8 if self._lines else 0)

    def _layout(self):
        w = self.PILL_W + self.PAD * 2
        h = self._pill_h() + self.PAD * 2
        scr = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        x = scr.x() + (scr.width() - w) // 2
        y = scr.y() + scr.height() - h - 40
        if self.geometry() != QtCore.QRect(x, y, w, h):
            self.setGeometry(x, y, w, h)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        ph = self._pill_h()
        px, py = self.PAD, self.PAD
        r = 20 if self._lines else ph / 2

        # painted soft shadow (3 layers)
        for grow, alpha in ((4, 34), (10, 16), (18, 7)):
            sp = QtGui.QPainterPath()
            sp.addRoundedRect(px - grow / 2, py - grow / 2 + 4,
                              self.PILL_W + grow, ph + grow,
                              r + grow / 2, r + grow / 2)
            p.fillPath(sp, QtGui.QColor(0, 0, 0, alpha))

        # glass body: vertical gradient
        body = QtGui.QPainterPath()
        body.addRoundedRect(px, py, self.PILL_W, ph, r, r)
        g = QtGui.QLinearGradient(0, py, 0, py + ph)
        g.setColorAt(0, self.BG_TOP)
        g.setColorAt(1, self.BG_BOT)
        p.fillPath(body, g)

        # rim light: brighter on top, fades down
        rim = QtGui.QLinearGradient(0, py, 0, py + ph)
        rim.setColorAt(0, QtGui.QColor(255, 255, 255, self.RIM_A))
        rim.setColorAt(0.35, QtGui.QColor(255, 255, 255, 14))
        rim.setColorAt(1, QtGui.QColor(255, 255, 255, 8))
        p.setPen(QtGui.QPen(QtGui.QBrush(rim), 1.2))
        p.drawPath(body)

        state = self._shown_state
        c1, c2 = self._accent(state)
        x0 = px + 22
        grad = QtGui.QLinearGradient(x0, 0, x0 + self.N_BARS * 6, 0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)

        cy = py + self.BASE_H / 2
        if self.GLOW:
            glow = QtGui.QColor(c1)
            glow.setAlpha(36)
        for i in range(self.N_BARS):
            bh = self._heights[i]
            bx = x0 + i * 6
            if self.GLOW:
                halo = QtGui.QPainterPath()
                halo.addRoundedRect(bx - 1.6, cy - bh - 2.2, 6.2,
                                    (bh + 2.2) * 2, 3.1, 3.1)
                p.fillPath(halo, glow)
            bar = QtGui.QPainterPath()
            bar.addRoundedRect(bx, cy - bh, 3.0, bh * 2, 1.5, 1.5)
            p.fillPath(bar, QtGui.QBrush(grad))

        tx = x0 + self.N_BARS * 6 + 12
        if self.REC_DOT:
            dc = QtGui.QColor("#ff5c6a" if state == "recording" else "#ffb02e")
            if state == "recording" and (self._tick // 16) % 2:
                dc.setAlpha(80)
            p.setBrush(dc)
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QPointF(tx + 3, cy), 3.4, 3.4)
            tx += 14
        p.setFont(self.f_title)
        p.setPen(QtGui.QColor(244, 244, 250))
        p.drawText(QtCore.QRectF(tx, py, 170, self.BASE_H),
                   QtCore.Qt.AlignVCenter, self.TITLE.get(state, ""))

        target_rect = QtCore.QRectF(px, py, self.PILL_W - 18, self.BASE_H)
        draw_keycap_hint(p, target_rect, self.f_kbd, ["Ctrl", "Alt", "/"], QtGui.QColor(244, 244, 250), QtGui.QColor(140, 140, 162))

        if self._lines:
            p.setFont(self.f_prev)
            for i, ln in enumerate(self._lines):
                dim = i == 0 and len(self._lines) > 1
                p.setPen(QtGui.QColor(140, 140, 160) if dim
                         else QtGui.QColor(222, 222, 232))
                p.drawText(QtCore.QPointF(px + 22,
                                          py + self.BASE_H + 4 + (i + 0.75) * 17),
                           ln)
        p.end()


# =============================================================================
# B — Ribbon HUD: edge-to-edge strip docked to the bottom of the screen.
# Scrolling voice-memo waveform, cyan on near-black, sharp corners.
# =============================================================================
class RibbonHUD(BaseVariant):
    NAME = "Ribbon HUD"
    ANCHOR = "dock"
    H = 58
    CYAN = QtGui.QColor("#4de8ff")
    TITLE = {"recording": "LISTENING", "processing": "POLISHING"}

    def __init__(self):
        super().__init__()
        self._hist = deque(maxlen=400)
        self.f_title = QtGui.QFont("Segoe UI", 8, QtGui.QFont.DemiBold)
        self.f_title.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 2.0)
        self.f_text = QtGui.QFont("Segoe UI", 10)
        self.f_hint = QtGui.QFont("Segoe UI", 7)
        self.f_kbd = QtGui.QFont("Segoe UI", 7, QtGui.QFont.Bold)

    def _tick_visuals(self):
        self._hist.append(self._lvl + random.uniform(-0.04, 0.04))

    def _layout(self):
        scr = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        geo = QtCore.QRect(scr.x(), scr.y() + scr.height() - self.H,
                           scr.width(), self.H)
        if self.geometry() != geo:
            self.setGeometry(geo)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        state = self._shown_state

        p.fillRect(0, 0, w, h, QtGui.QColor(5, 10, 14, 234))
        top = QtGui.QLinearGradient(0, 0, w, 0)
        edge = QtGui.QColor(self.CYAN)
        edge.setAlpha(0)
        mid = QtGui.QColor(self.CYAN)
        mid.setAlpha(120)
        top.setColorAt(0, edge)
        top.setColorAt(0.5, mid)
        top.setColorAt(1, edge)
        p.fillRect(QtCore.QRectF(0, 0, w, 1.4), QtGui.QBrush(top))

        # waveform: newest at the right, scrolling left. Kept clear of the
        # right hint zone and faded out before the centered transcript.
        cy = h * 0.5
        step = 5
        x_hi, x_lo = w - 130, 262
        n = max(1, (x_hi - x_lo) // step)
        hist = self._hist
        for i in range(n):
            idx = len(hist) - 1 - i
            amp = hist[idx] if idx >= 0 else 0.02
            if state == "processing":
                amp = 0.12 + 0.28 * abs(math.sin(self._tick * 0.2 - i * 0.3))
            bh = 1.5 + amp * (h * 0.36)
            x = x_hi - i * step
            fade = 1.0 - i / n * 1.05
            if fade < 0.05:
                break
            c = QtGui.QColor(self.CYAN)
            c.setAlphaF(0.55 * fade)
            p.fillRect(QtCore.QRectF(x, cy - bh, 2.4, bh * 2), c)

        # left: state + timer
        dot = QtGui.QColor("#ff5c6a" if state == "recording" else "#ffb02e")
        if state == "recording" and (self._tick // 16) % 2:
            dot.setAlpha(90)
        p.setBrush(dot)
        p.setPen(QtCore.Qt.NoPen)
        p.drawEllipse(QtCore.QPointF(26, h / 2), 4, 4)
        p.setFont(self.f_title)
        p.setPen(QtGui.QColor(210, 240, 248))
        m, s = divmod(int(self.elapsed()), 60)
        p.drawText(QtCore.QRectF(40, 0, 220, h), QtCore.Qt.AlignVCenter,
                   f"{self.TITLE.get(state, '')}  {m}:{s:02d}")

        # center: transcript, single line, tail visible
        if self.preview:
            avail = w - 560
            p.setFont(self.f_text)
            txt = elide_front(self.preview, self.f_text, avail)
            flags = QtCore.Qt.AlignVCenter | QtCore.Qt.AlignHCenter
            p.setPen(QtGui.QColor(0, 0, 0, 180))    # legibility over the wave
            p.drawText(QtCore.QRectF(280, 1.2, avail, h), flags, txt)
            p.setPen(QtGui.QColor(238, 248, 252))
            p.drawText(QtCore.QRectF(280, 0, avail, h), flags, txt)

        # right: hint
        target_rect = QtCore.QRectF(0, 0, w - 24, h)
        draw_keycap_hint(p, target_rect, self.f_kbd, ["Ctrl", "Alt", "/"], QtGui.QColor(238, 248, 252), QtGui.QColor(120, 150, 160))
        p.end()


# =============================================================================
# C — Orb Island: light theme. Compact capsule with a pulsing orb while
# listening; morphs into a wide card once transcript text arrives.
# =============================================================================
class OrbIsland(BaseVariant):
    NAME = "Orb Island"
    ANCHOR = "center"
    PAD = 28
    ACCENT = {"recording": QtGui.QColor("#ff4d5e"),
              "processing": QtGui.QColor("#f59e0b")}
    TITLE = {"recording": "Listening", "processing": "Polishing…"}

    def __init__(self):
        super().__init__()
        self._cw, self._ch = 158.0, 40.0
        self._lines = []
        self.f_title = QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold)
        self.f_prev = QtGui.QFont("Segoe UI", 9)
        self.f_hint = QtGui.QFont("Segoe UI", 7)
        self.f_kbd = QtGui.QFont("Segoe UI", 7, QtGui.QFont.Bold)

    def _tick_visuals(self):
        self._lines = wrap_tail(self.preview, self.f_prev, 430 - 52, 2)
        tw = 440.0 if self._lines else 158.0
        th = (52 + len(self._lines) * 17 + 12) if self._lines else 40.0
        self._cw += (tw - self._cw) * 0.22
        self._ch += (th - self._ch) * 0.22

    def _layout(self):
        w = int(self._cw) + self.PAD * 2
        h = int(self._ch) + self.PAD * 2
        scr = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        x = scr.x() + (scr.width() - w) // 2
        y = scr.y() + scr.height() - h - 40
        if self.geometry() != QtCore.QRect(x, y, w, h):
            self.setGeometry(x, y, w, h)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        cw, ch = self._cw, self._ch
        px, py = self.PAD, self.PAD
        r = min(20.0, ch / 2)
        state = self._shown_state
        accent = self.ACCENT.get(state, QtGui.QColor("#9ca3af"))

        for grow, alpha in ((5, 26), (13, 13), (24, 6)):
            sp = QtGui.QPainterPath()
            sp.addRoundedRect(px - grow / 2, py - grow / 2 + 5,
                              cw + grow, ch + grow, r + grow / 2, r + grow / 2)
            p.fillPath(sp, QtGui.QColor(20, 20, 30, alpha))

        body = QtGui.QPainterPath()
        body.addRoundedRect(px, py, cw, ch, r, r)
        p.fillPath(body, QtGui.QColor(253, 253, 254, 248))
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 26), 1))
        p.drawPath(body)

        # orb + pulse rings
        ocx, ocy = px + 22, py + (20 if self._lines else ch / 2)
        if state == "recording":
            for k in (0.0, 0.5):
                t = ((self._tick * 0.02 + k) % 1.0)
                ring = QtGui.QColor(accent)
                ring.setAlphaF(0.5 * (1 - t))
                p.setPen(QtGui.QPen(ring, 1.6))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawEllipse(QtCore.QPointF(ocx, ocy), 6 + t * 10, 6 + t * 10)
        pulse = 0.75 + 0.25 * abs(math.sin(self._tick * 0.11))
        core = QtGui.QColor(accent)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(core)
        rr = 6.5 * (pulse if state == "recording" else 1.0)
        p.drawEllipse(QtCore.QPointF(ocx, ocy), rr, rr)

        p.setFont(self.f_title)
        p.setPen(QtGui.QColor(26, 26, 32))
        p.drawText(QtCore.QRectF(px + 40, py, 200,
                                 40 if self._lines else ch),
                   QtCore.Qt.AlignVCenter, self.TITLE.get(state, ""))

        if self._lines:
            m, s = divmod(int(self.elapsed()), 60)
            
            # Calculate dynamic keycap width for positioning
            fm_kbd = QtGui.QFontMetrics(self.f_kbd)
            total_w = (max(fm_kbd.horizontalAdvance("Ctrl") + 12, 18) +
                       max(fm_kbd.horizontalAdvance("Alt") + 12, 18) +
                       max(fm_kbd.horizontalAdvance("/") + 12, 18) +
                       2 * (fm_kbd.horizontalAdvance("+") + 8)) # gap=4 -> gap * 2 = 8
            
            p.setFont(self.f_hint)
            p.setPen(QtGui.QColor(150, 150, 160))
            # Draw timer text shifted left of the keycap hint dynamically
            p.drawText(QtCore.QRectF(px, py + 6, cw - 18 - total_w - 6, 30),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, f"{m}:{s:02d}")
            
            # Draw keycap hint right-aligned
            target_rect = QtCore.QRectF(px, py + 6, cw - 18, 30)
            draw_keycap_hint(p, target_rect, self.f_kbd, ["Ctrl", "Alt", "/"], QtGui.QColor(80, 80, 90), QtGui.QColor(150, 150, 160))
            p.setFont(self.f_prev)
            for i, ln in enumerate(self._lines):
                dim = i == 0 and len(self._lines) > 1
                p.setPen(QtGui.QColor(138, 139, 149) if dim
                         else QtGui.QColor(45, 46, 56))
                p.drawText(QtCore.QPointF(px + 26,
                                          py + 48 + (i + 0.75) * 17), ln)
        p.end()


# =============================================================================
# D — Terminal Ticker: monospace chip in the bottom-left corner.
# Green-on-black, rec timer, single ticker line, blinking cursor.
# =============================================================================
class TerminalTicker(BaseVariant):
    NAME = "Terminal Ticker"
    ANCHOR = "left"
    H = 38
    GREEN = QtGui.QColor("#6df08a")
    DIMGREEN = QtGui.QColor("#3f9b58")
    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        super().__init__()
        self.f_mono = mono_font(9)
        self._w = 200

    def _prefix(self):
        if self._shown_state == "processing":
            return f"{self.SPIN[(self._tick // 3) % len(self.SPIN)]} polish "
        m, s = divmod(int(self.elapsed()), 60)
        return f"● rec {m}:{s:02d} "

    def _tick_visuals(self):
        fm = QtGui.QFontMetrics(self.f_mono)
        want = fm.horizontalAdvance(self._prefix() + self.preview) + 46
        self._w = max(210, min(640, want))

    def _layout(self):
        scr = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        geo = QtCore.QRect(scr.x() + 24,
                           scr.y() + scr.height() - self.H - 40,
                           int(self._w), self.H)
        if self.geometry() != geo:
            self.setGeometry(geo)

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()

        body = QtGui.QPainterPath()
        body.addRoundedRect(0.5, 0.5, w - 1, h - 1, 6, 6)
        p.fillPath(body, QtGui.QColor(6, 11, 7, 242))
        p.setPen(QtGui.QPen(QtGui.QColor(47, 122, 70, 110), 1))
        p.drawPath(body)

        p.setFont(self.f_mono)
        fm = QtGui.QFontMetrics(self.f_mono)
        x = 14
        cy = h / 2 + fm.ascent() / 2 - 1

        pre = self._prefix()
        if self._shown_state == "recording":
            dot = QtGui.QColor("#ff5c6a")
            if (self._tick // 14) % 2:
                dot.setAlpha(70)
            p.setPen(dot)
            p.drawText(QtCore.QPointF(x, cy), "●")
            p.setPen(self.DIMGREEN)
            p.drawText(QtCore.QPointF(x + fm.horizontalAdvance("● "), cy),
                       pre[2:])
        else:
            p.setPen(QtGui.QColor("#ffd36e"))
            p.drawText(QtCore.QPointF(x, cy), pre[0])
            p.setPen(self.DIMGREEN)
            p.drawText(QtCore.QPointF(x + fm.horizontalAdvance(pre[0]), cy),
                       pre[1:])

        tx = x + fm.horizontalAdvance(pre)
        avail = w - tx - 26
        p.setPen(self.GREEN)
        p.drawText(QtCore.QPointF(tx, cy),
                   elide_front(self.preview, self.f_mono, avail))

        if (self._tick // 9) % 2:
            cx = tx + fm.horizontalAdvance(
                elide_front(self.preview, self.f_mono, avail))
            p.fillRect(QtCore.QRectF(cx + 2, h / 2 - 7, 2.2, 14), self.GREEN)
        p.end()


# --- Aurora Glass theme variations -------------------------------------------
class AuroraArctic(AuroraGlass):
    """Cool ice-blue -> violet accents on a bluer glass."""
    NAME = "Aurora · Arctic"
    ACCENT = {"recording": (QtGui.QColor("#5eb8ff"), QtGui.QColor("#a78bfa")),
              "processing": (QtGui.QColor("#34d399"), QtGui.QColor("#a7f3d0"))}
    BG_TOP = QtGui.QColor(24, 31, 50, 244)
    BG_BOT = QtGui.QColor(10, 14, 26, 244)


class AuroraMono(AuroraGlass):
    """No color at all in the bars — pure greyscale glass; state is carried by
    a small blinking dot (red = rec, amber = polish). The quietest option."""
    NAME = "Aurora · Mono"
    ACCENT = {"recording": (QtGui.QColor("#ececf2"), QtGui.QColor("#8d8d9c")),
              "processing": (QtGui.QColor("#c9c9d4"), QtGui.QColor("#77778a"))}
    BG_TOP = QtGui.QColor(17, 17, 20, 247)
    BG_BOT = QtGui.QColor(8, 8, 10, 247)
    RIM_A = 42
    GLOW = False
    REC_DOT = True


class AuroraPrism(AuroraGlass):
    """Lighter, more transparent glass with a slowly hue-cycling accent."""
    NAME = "Aurora · Prism"
    BG_TOP = QtGui.QColor(28, 28, 44, 206)
    BG_BOT = QtGui.QColor(14, 14, 26, 206)
    RIM_A = 88

    def _accent(self, state):
        if state not in ("recording", "processing"):
            return super()._accent(state)
        speed = 0.0022 if state == "recording" else 0.006
        hue = (self._tick * speed) % 1.0
        c1 = QtGui.QColor.fromHsvF(hue, 0.62, 1.0)
        c2 = QtGui.QColor.fromHsvF((hue + 0.16) % 1.0, 0.55, 1.0)
        return c1, c2


VARIANTS = [AuroraGlass, AuroraArctic, AuroraMono, AuroraPrism,
            RibbonHUD, OrbIsland, TerminalTicker]


# =============================================================================
# Fake dictation driver: idle -> recording (words arrive) -> polishing
# (clean text streams in) -> idle, forever. Space skips to the next phase.
# =============================================================================
class Driver(QtCore.QObject):
    PHASES = (("idle", 1.4), ("recording", 9.0), ("processing", 3.8))

    def __init__(self, get_widget, parent=None):
        super().__init__(parent)
        self.get_widget = get_widget
        self.i = 0
        self.t0 = time.monotonic()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

    def phase(self):
        return self.PHASES[self.i][0]

    def skip(self):
        self.i = (self.i + 1) % len(self.PHASES)
        self.t0 = time.monotonic()

    def _tick(self):
        name, dur = self.PHASES[self.i]
        el = time.monotonic() - self.t0
        if el > dur:
            self.skip()
            return
        w = self.get_widget()
        if name == "recording":
            w.state = "recording"
            w.level = fake_level(el)
            n = int(max(0.0, el - 0.9) / 0.42)
            w.preview = " ".join(RAW.split()[:n])
        elif name == "processing":
            w.state = "processing"
            w.level = 0.0
            n = int(el / 0.13)
            w.preview = " ".join(CLEAN.split()[:n])
        else:
            w.state = "idle"
            w.preview = ""


# =============================================================================
# Floating switcher bar (top-center). Owns the keyboard.
# =============================================================================
class Switcher(QtWidgets.QWidget):
    def __init__(self, widgets, driver):
        super().__init__(None, QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.WindowStaysOnTopHint
                         | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.widgets = widgets
        self.driver = driver
        self.cur = 0
        self.f = QtGui.QFont("Segoe UI", 9)
        scr = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        w = 620
        self.setGeometry(scr.x() + (scr.width() - w) // 2, scr.y() + 16, w, 40)
        t = QtCore.QTimer(self)
        t.timeout.connect(self.update)
        t.start(120)

    def current(self):
        return self.widgets[self.cur]

    def switch(self, d):
        old = self.widgets[self.cur]
        old.state = "idle"          # fades itself out
        self.cur = (self.cur + d) % len(self.widgets)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == QtCore.Qt.Key_Left:
            self.switch(-1)
        elif k == QtCore.Qt.Key_Right:
            self.switch(+1)
        elif k == QtCore.Qt.Key_Space:
            self.driver.skip()
        elif k == QtCore.Qt.Key_Escape:
            QtWidgets.QApplication.quit()

    def mousePressEvent(self, ev):
        x = ev.position().x()
        if x < self.width() / 3:
            self.switch(-1)
        elif x > self.width() * 2 / 3:
            self.switch(+1)
        else:
            self.driver.skip()

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        body = QtGui.QPainterPath()
        body.addRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1,
                            19, 19)
        p.fillPath(body, QtGui.QColor(16, 16, 26, 240))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 40), 1))
        p.drawPath(body)
        p.setFont(self.f)
        p.setPen(QtGui.QColor(235, 235, 245))
        name = f"{self.cur + 1}/{len(self.widgets)} — {self.current().NAME}"
        p.drawText(self.rect(), QtCore.Qt.AlignCenter,
                   f"◀   {name}   ▶      [{self.driver.phase()}]")
        p.setPen(QtGui.QColor(140, 140, 160))
        p.drawText(QtCore.QRectF(0, 0, self.width() - 16, self.height()),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                   "Space: phase · Esc: quit")
        p.end()


# =============================================================================
# --shoot: render every variant x state to PNGs on a fake desktop backdrop.
# =============================================================================
def compose(pm, anchor, dpr):
    ov_w, ov_h = pm.width() / dpr, pm.height() / dpr
    if anchor == "dock":
        cw, ch = int(ov_w), int(ov_h) + 190
    else:
        cw, ch = max(820, int(ov_w) + 200), int(ov_h) + 170
    img = QtGui.QImage(int(cw * dpr), int(ch * dpr),
                       QtGui.QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing)

    # fake desktop: gradient wallpaper + a "document" window + taskbar
    g = QtGui.QLinearGradient(0, 0, cw, ch)
    g.setColorAt(0, QtGui.QColor("#2b3050"))
    g.setColorAt(0.55, QtGui.QColor("#3c2e55"))
    g.setColorAt(1, QtGui.QColor("#1d2036"))
    p.fillRect(QtCore.QRectF(0, 0, cw, ch), g)
    win = QtCore.QRectF(cw * 0.12, 24, cw * 0.76, ch - 110)
    wp = QtGui.QPainterPath()
    wp.addRoundedRect(win, 8, 8)
    p.fillPath(wp, QtGui.QColor(248, 248, 250, 235))
    p.fillRect(QtCore.QRectF(win.x(), win.y(), win.width(), 26),
               QtGui.QColor(230, 230, 236))
    p.setPen(QtGui.QColor(200, 202, 210))
    for i in range(5):
        y = win.y() + 48 + i * 16
        if y > win.bottom() - 14:
            break
        p.drawLine(QtCore.QPointF(win.x() + 20, y),
                   QtCore.QPointF(win.right() - 20 - (i % 3) * 60, y))
    p.fillRect(QtCore.QRectF(0, ch - 44, cw, 44), QtGui.QColor(10, 12, 20, 210))

    if anchor == "dock":
        x, y = 0, ch - 44 - ov_h
    elif anchor == "left":
        x, y = 24, ch - 44 - ov_h - 40
    else:
        x, y = (cw - ov_w) / 2, ch - 44 - ov_h - 40
    p.drawPixmap(QtCore.QPointF(x, y), pm)
    p.end()
    return img


def shoot():
    os.makedirs(SHOTS_DIR, exist_ok=True)
    dpr = QtGui.QGuiApplication.primaryScreen().devicePixelRatio()
    raw_part = " ".join(RAW.split()[:12])
    clean_part = " ".join(CLEAN.split()[:13])
    scenarios = (("1-rec-early", "recording", ""),
                 ("2-rec-preview", "recording", raw_part),
                 ("3-polish", "processing", clean_part))
    for V in VARIANTS:
        for tag, state, preview in scenarios:
            w = V()
            w.timer.stop()
            w.force(state, preview)
            pm = w.grab()
            img = compose(pm, V.ANCHOR, dpr)
            path = os.path.join(SHOTS_DIR, f"{V.__name__}-{tag}.png")
            img.save(path)
            print("wrote", path)
            w.deleteLater()


def main():
    app = QtWidgets.QApplication(sys.argv)
    if "--shoot" in sys.argv:
        shoot()
        return
    widgets = [V() for V in VARIANTS]
    switcher = Switcher(widgets, None)
    driver = Driver(switcher.current, switcher)
    switcher.driver = driver
    switcher.show()
    switcher.activateWindow()
    switcher.raise_()
    switcher.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
