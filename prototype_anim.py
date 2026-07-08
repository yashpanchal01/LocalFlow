# =============================================================================
# PROTOTYPE — THROWAWAY. Delete once a winning easing is folded into
# localflow.py's Overlay class.
#
# Question: how should the overlay MOVE?
#   1. The pill's height-expand (46px -> taller as transcript lines appear) is
#      currently INSTANT — _pill_h() snaps and _animate() jumps setGeometry in
#      one frame (localflow.py:314-317). We want a hard S-curve (ease-in-out)
#      tween between the two heights.
#   2. Transcript text POPS in at full colour (localflow.py:407-413). We want a
#      smooth opacity fade.
#
# This renders the REAL "Aurora Mono" pill (paint copied verbatim from the
# production Overlay) driven by a fake dictation loop — no mic, no models, no
# hotkey — so it's safe to run beside the real app. Only the MOTION model is
# new: every quantity (window opacity, pill height, per-line text alpha) is a
# time-based eased tween instead of the old per-frame snap/exponential.
#
# Five motion "personalities", switchable live:
#   V0  Current (before)     : instant height snap + pop text + expo window fade
#   V1  InOutCubic           : gentle S
#   V2  InOutQuint           : harder S
#   V3  Hard-S bezier        : cubic-bezier(.85,0,.15,1) — very pronounced S
#   V4  InOutBack            : S with a tiny settle/overshoot
#
# Run live :  py -3.13 prototype_anim.py
#             </> switch variant  ·  Space replay  ·  Esc quit
#             (click the caption bar first if keys stop responding)
# Verify   :  py -3.13 prototype_anim.py --shoot
#             renders the hard-S expand to PNGs + heights.csv in the scratchpad
#             (no interaction) so the S-curve can be checked without watching.
# =============================================================================
import math
import os
import random
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets

SHOTS_DIR = (r"C:\Users\YASHPA~1\AppData\Local\Temp\claude"
             r"\C--Users-YASH-PANCHAL-localflow"
             r"\c0ae1efb-e24f-429e-89a0-0a6ea03fed04\scratchpad\anim_shots")

RAW = ("so i was thinking we could test claude code with ollama today um and "
       "maybe see how the new overlay animation looks")
CLEAN = ("So I was thinking we could test Claude Code with Ollama today, and "
         "maybe see how the new overlay animation looks.")

# ---------------------------------------------------------------------------
# Easing — pure functions p(0..1) -> value(0..1-ish). QEasingCurve for the
# named ones; a CSS-style cubic-bezier solver for the custom "hard S".
# ---------------------------------------------------------------------------
def qt_ease(kind):
    curve = QtCore.QEasingCurve(kind)
    return lambda p: curve.valueForProgress(max(0.0, min(1.0, p)))


def cubic_bezier(x1, y1, x2, y2):
    """cubic-bezier(x1,y1,x2,y2) timing function, same math as browsers."""
    def bez(a1, a2, t):
        return (((1 - 3 * a2 + 3 * a1) * t + (3 * a2 - 6 * a1)) * t + 3 * a1) * t

    def solve(p):
        p = max(0.0, min(1.0, p))
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


E_CUBIC = qt_ease(QtCore.QEasingCurve.InOutCubic)
E_QUINT = qt_ease(QtCore.QEasingCurve.InOutQuint)
E_BACK = qt_ease(QtCore.QEasingCurve.InOutBack)
E_HARDS = cubic_bezier(0.85, 0.0, 0.15, 1.0)

# variant = (key, label, expand(curve,ms), textfade(curve,ms), winfade(curve,ms))
# curve None + ms 0 in expand/text  => snap (V0);  winfade None => expo (V0).
VARIANTS = [
    ("V0", "Current (before) — snap + pop",
     (None, 0),        (None, 0),        (None, 0)),
    ("V1", "InOutCubic — gentle S",
     (E_CUBIC, 260),   (E_CUBIC, 200),   (E_CUBIC, 180)),
    ("V2", "InOutQuint — harder S",
     (E_QUINT, 300),   (E_CUBIC, 200),   (E_QUINT, 200)),
    ("V3", "Hard-S bezier(.85,0,.15,1)",
     (E_HARDS, 320),   (E_CUBIC, 220),   (E_HARDS, 200)),
    ("V4", "InOutBack — S + settle",
     (E_BACK, 340),    (E_CUBIC, 200),   (E_CUBIC, 190)),
]

# demo timeline (seconds within one loop)
WORD_START, WORD_STEP = 0.5, 0.11
PROC_AT = 3.2
FADE_AT = 4.4
CYCLE = 5.3


def wrap_tail(text, font, avail, max_lines=2):
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


class Pill(QtWidgets.QWidget):
    # ---- Aurora Mono constants (verbatim from localflow.py Overlay) ----
    PILL_W = 344
    BASE_H = 46
    PAD = 26
    N_BARS = 12
    BG_TOP = QtGui.QColor(17, 17, 20, 247)
    BG_BOT = QtGui.QColor(8, 8, 10, 247)
    RIM_A = 42
    TEXT = QtGui.QColor(244, 244, 250)
    DIM = QtGui.QColor(140, 140, 162)
    PREV = QtGui.QColor(224, 224, 234)
    PREV_OLD = QtGui.QColor(150, 150, 160)
    BARS = {"recording": (QtGui.QColor("#ececf2"), QtGui.QColor("#8d8d9c")),
            "processing": (QtGui.QColor("#c9c9d4"), QtGui.QColor("#77778a"))}
    DOT = {"recording": QtGui.QColor("#ff5c6a"),
           "processing": QtGui.QColor("#ffb02e")}
    TITLE = {"recording": "Listening", "processing": "Polishing…"}

    def __init__(self, controls=None):
        super().__init__(None,
                         QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.WindowStaysOnTopHint
                         | QtCore.Qt.Tool
                         | QtCore.Qt.WindowTransparentForInput
                         | QtCore.Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)

        self.controls = controls
        self.variant_idx = 3            # start on the hard-S so it's obvious

        # demo/runtime state
        self._shown_state = "idle"
        self._lvl = 0.0
        self._tick = 0
        self._heights = [3.0] * self.N_BARS
        self._phase = [random.uniform(0, 6.28) for _ in range(self.N_BARS)]
        self._speed = [random.uniform(0.35, 0.75) for _ in range(self.N_BARS)]
        self._lines = []
        self._prev_nlines = 0

        # tweened quantities
        self._op = 0.0                  # window opacity
        self._op_frm = self._op_to = 0.0
        self._op_t0 = 0.0
        self._h = float(self.BASE_H)     # pill height
        self._h_frm = self._h_to = self._h
        self._h_t0 = 0.0
        self._bf_t0 = -10.0              # bottom-line fade start
        self._paint_ph = float(self.BASE_H)
        self._paint_bottom_alpha = 1.0

        self.f_title = QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold)
        self.f_hint = QtGui.QFont("Segoe UI", 7)
        self.f_kbd = QtGui.QFont("Segoe UI", 7, QtGui.QFont.Bold)
        self.f_prev = QtGui.QFont("Segoe UI", 9)

        self.setWindowOpacity(0.0)
        self.cycle_t0 = time.perf_counter()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._frame)
        self.timer.start(16)             # ~60fps (real app runs 33ms/~30fps)

    def replay(self):
        self.cycle_t0 = time.perf_counter()

    # -- one animation frame ---------------------------------------------------
    def _frame(self):
        now = time.perf_counter()
        self._tick += 1
        cy = (now - self.cycle_t0) % CYCLE

        # ---- fake dictation timeline -> state / preview / level ----
        if cy < FADE_AT:
            if cy < PROC_AT:
                state = "recording"
                n = max(0, int((cy - WORD_START) / WORD_STEP))
                preview = " ".join(RAW.split()[:n])
                self.level = 0.30 + 0.45 * abs(math.sin(cy * 6.0))
            else:
                state = "processing"
                preview = CLEAN
                self.level = 0.12
        else:
            state = "idle"
            preview = ""
            self.level = 0.0

        _, _, exp, txt, win = VARIANTS[self.variant_idx]
        exp_curve, exp_ms = exp
        txt_curve, txt_ms = txt
        win_curve, win_ms = win

        active = state in ("recording", "processing")
        if active:
            self._shown_state = state

        # ---- window opacity ----
        target_op = 1.0 if active else 0.0
        if win_curve is None:                       # V0: old exponential fade
            self._op += (target_op - self._op) * 0.28
        else:
            if target_op != self._op_to:
                self._op_frm, self._op_to, self._op_t0 = self._op, target_op, now
            if win_ms <= 0:
                self._op = target_op
            else:
                p = min(1.0, (now - self._op_t0) * 1000.0 / win_ms)
                self._op = self._op_frm + (self._op_to - self._op_frm) * win_curve(p)

        if not active and self._op < 0.04:
            if self.isVisible():
                self.hide()
                self._reset_after_hide()
            return
        if active and not self.isVisible():
            self._h = float(self.BASE_H)
            self._h_to = self._h_frm = self._h
            self._place(self._h)
            self.show()
        self.setWindowOpacity(max(0.0, min(1.0, self._op)))

        # ---- mic level + bar heights (verbatim behaviour) ----
        self._lvl += (min(1.0, self.level) - self._lvl) * 0.35
        for i in range(self.N_BARS):
            if self._shown_state == "recording":
                wobble = 0.4 + 0.6 * abs(math.sin(self._tick * self._speed[i]
                                                  + self._phase[i]))
                target = 2.5 + 11.5 * wobble * (0.18 + 1.6 * self._lvl)
            else:
                target = 3 + 6.5 * abs(math.sin(self._tick * 0.22 - i * 0.45))
            self._heights[i] += (min(target, 13.0) - self._heights[i]) * 0.45

        # ---- transcript lines (tail 2), detect a NEW line -> fade it in ----
        avail = self.PILL_W - 48
        lines = wrap_tail(preview, self.f_prev, avail, 2) if preview else []
        if len(lines) > self._prev_nlines:
            self._bf_t0 = now                       # a new line appeared
        self._prev_nlines = len(lines)
        self._lines = lines

        if txt_curve is None or txt_ms <= 0:        # V0: pop
            self._paint_bottom_alpha = 1.0
        else:
            p = min(1.0, (now - self._bf_t0) * 1000.0 / txt_ms)
            self._paint_bottom_alpha = txt_curve(p)

        # ---- pill height tween (THE headline: 46 -> taller, S-curved) ----
        target_h = self.BASE_H + (len(lines) * 17 + 8 if lines else 0)
        if target_h != self._h_to:
            self._h_frm, self._h_to, self._h_t0 = self._h, target_h, now
        if exp_curve is None or exp_ms <= 0:        # V0: instant snap
            self._h = float(target_h)
        else:
            p = min(1.0, (now - self._h_t0) * 1000.0 / exp_ms)
            self._h = self._h_frm + (self._h_to - self._h_frm) * exp_curve(p)

        self._paint_ph = self._h
        self._place(self._h)
        self.update()

    def _reset_after_hide(self):
        self._lines = []
        self._prev_nlines = 0
        self._h = self._h_to = self._h_frm = float(self.BASE_H)
        self._paint_bottom_alpha = 1.0

    def _place(self, ph):
        # bottom-anchored: window bottom stays fixed, pill grows UPWARD.
        win_w = self.PILL_W + self.PAD * 2
        win_h = int(round(ph)) + self.PAD * 2
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - win_w) // 2
        y = screen.y() + screen.height() - win_h - 30
        self.setGeometry(x, y, win_w, win_h)

    # -- painting (Aurora Mono, verbatim; only per-line alpha is new) ----------
    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        ph = self._paint_ph
        px, py = self.PAD, self.PAD
        state = self._shown_state
        r = 20 if self._lines else ph / 2

        for grow, alpha in ((4, 30), (10, 15), (18, 6)):
            sp = QtGui.QPainterPath()
            sp.addRoundedRect(px - grow / 2, py - grow / 2 + 4,
                              self.PILL_W + grow, ph + grow,
                              r + grow / 2, r + grow / 2)
            p.fillPath(sp, QtGui.QColor(0, 0, 0, alpha))

        body = QtGui.QPainterPath()
        body.addRoundedRect(px, py, self.PILL_W, ph, r, r)
        g = QtGui.QLinearGradient(0, py, 0, py + ph)
        g.setColorAt(0, self.BG_TOP)
        g.setColorAt(1, self.BG_BOT)
        p.fillPath(body, g)

        rim = QtGui.QLinearGradient(0, py, 0, py + ph)
        rim.setColorAt(0, QtGui.QColor(255, 255, 255, self.RIM_A))
        rim.setColorAt(0.35, QtGui.QColor(255, 255, 255, 12))
        rim.setColorAt(1, QtGui.QColor(255, 255, 255, 7))
        p.setPen(QtGui.QPen(QtGui.QBrush(rim), 1.2))
        p.drawPath(body)

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

        p.setFont(self.f_title)
        p.setPen(self.TEXT)
        p.drawText(QtCore.QRectF(tx, py, 170, self.BASE_H),
                   QtCore.Qt.AlignVCenter, self.TITLE.get(state, ""))

        # hotkey hint, right-aligned inside the pill
        target_rect = QtCore.QRectF(px, py, self.PILL_W - 18, self.BASE_H)
        draw_keycap_hint(p, target_rect, self.f_kbd, ["Ctrl", "Alt", "/"], self.TEXT, self.DIM)

        # transcript preview — NEW: newest line fades in via _paint_bottom_alpha
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

    # -- offscreen render for --shoot (deterministic, no live loop) ------------
    def shoot_render(self, ph, bottom_alpha, lines, state="recording"):
        self._paint_ph = ph
        self._paint_bottom_alpha = bottom_alpha
        self._lines = lines
        self._shown_state = state
        win_w = self.PILL_W + self.PAD * 2
        win_h = int(round(self.BASE_H + 42)) + self.PAD * 2   # room for 2 lines
        self.resize(win_w, win_h)
        return self.grab()


class Controls(QtWidgets.QWidget):
    """Focusable caption bar at top-centre — shows the current variant and
    eats the keys (the pill itself is transparent-for-input)."""

    def __init__(self):
        super().__init__(None,
                         QtCore.Qt.FramelessWindowHint
                         | QtCore.Qt.WindowStaysOnTopHint
                         | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.pill = None
        self.resize(560, 54)
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.x() + (screen.width() - 560) // 2, screen.y() + 28)
        self.f = QtGui.QFont("Segoe UI", 10, QtGui.QFont.DemiBold)
        self.f2 = QtGui.QFont("Segoe UI", 8)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == QtCore.Qt.Key_Escape:
            QtWidgets.QApplication.quit()
        elif k in (QtCore.Qt.Key_Right, QtCore.Qt.Key_Down):
            self.pill.variant_idx = (self.pill.variant_idx + 1) % len(VARIANTS)
            self.pill.replay(); self.update()
        elif k in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Up):
            self.pill.variant_idx = (self.pill.variant_idx - 1) % len(VARIANTS)
            self.pill.replay(); self.update()
        elif k == QtCore.Qt.Key_Space:
            self.pill.replay()

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        body = QtGui.QPainterPath()
        body.addRoundedRect(0, 0, self.width(), self.height(), 12, 12)
        p.fillPath(body, QtGui.QColor(24, 24, 30, 235))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1))
        p.drawPath(body)
        key, label, exp, _txt, _win = VARIANTS[self.pill.variant_idx]
        p.setFont(self.f)
        p.setPen(QtGui.QColor(240, 240, 248))
        p.drawText(QtCore.QRectF(0, 4, self.width(), 26),
                   QtCore.Qt.AlignHCenter, f"◀   {key} · {label}   ▶")
        p.setFont(self.f2)
        p.setPen(QtGui.QColor(150, 150, 165))
        sub = "expand snap" if exp[0] is None else f"expand {exp[1]}ms"
        p.drawText(QtCore.QRectF(0, 28, self.width(), 22),
                   QtCore.Qt.AlignHCenter,
                   f"{sub}    ·    ←/→ switch   ·   Space replay   ·   Esc quit")
        p.end()


def run_live():
    app = QtWidgets.QApplication(sys.argv)
    controls = Controls()
    pill = Pill(controls)
    controls.pill = pill
    controls.show()
    controls.raise_()
    controls.activateWindow()
    controls.setFocus()
    sys.exit(app.exec())


def run_shoot():
    os.makedirs(SHOTS_DIR, exist_ok=True)
    app = QtWidgets.QApplication(sys.argv)
    pill = Pill()
    lines = wrap_tail(CLEAN, pill.f_prev, pill.PILL_W - 48, 2)
    h0, h1 = float(Pill.BASE_H), float(Pill.BASE_H + 42)
    rows = []
    n = 12
    for i in range(n + 1):
        prog = i / n
        e = E_HARDS(prog)
        h = h0 + (h1 - h0) * e
        pm = pill.shoot_render(h, e, lines, "recording")
        pm.save(os.path.join(SHOTS_DIR, f"hardS_{i:02d}_p{prog:.2f}.png"))
        rows.append((prog, e, h))
    with open(os.path.join(SHOTS_DIR, "heights.csv"), "w") as f:
        f.write("progress,eased,height,delta_height\n")
        prev = None
        for prog, e, h in rows:
            d = "" if prev is None else f"{h - prev:.3f}"
            f.write(f"{prog:.4f},{e:.4f},{h:.3f},{d}\n")
            prev = h
    print(f"wrote {n + 1} PNGs + heights.csv to {SHOTS_DIR}")


if __name__ == "__main__":
    if "--shoot" in sys.argv:
        run_shoot()
    else:
        run_live()
