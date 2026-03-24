"""
Infosys GestureVolume - Control Hub
Recreated to match original launcher design.
Run: python main.py
"""
import tkinter as tk
import subprocess
import sys
import os
import math

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BG      = "#020c0c"
CYAN    = "#00e5cc"
CYAN_DIM= "#005544"
BTN_BG  = "#0a1a1a"
BTN_FG  = CYAN
TEXT_DIM= "#338877"

WIN_W, WIN_H = 860, 520


class ControlHub(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Infosys_GestureVolume — Control Hub")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._build()

    # ── Draw decorative circuit-board border on a Canvas ───────────
    def _build(self):
        self.canvas = tk.Canvas(self, width=WIN_W, height=WIN_H,
                                bg=BG, highlightthickness=0)
        self.canvas.place(x=0, y=0)
        self._draw_border()

        # ── Title ────────────────────────────────────────────────────
        self.canvas.create_text(
            WIN_W // 2, 78,
            text="Gesture Volume: Volume Control with Hand Gestures",
            font=("Courier New", 18, "bold"), fill=CYAN, anchor="center"
        )
        self.canvas.create_text(
            WIN_W // 2, 108,
            text="Made by SNEHIL GHOSH, GAUTAM N CHIPKAR, AMRUTHA VARSHANI, AYUSH GORGE",
            font=("Courier New", 9), fill=TEXT_DIM, anchor="center"
        )

        # ── Buttons ──────────────────────────────────────────────────
        self._make_button(WIN_W // 2, 220, "Gesture Control",  self._launch_gesture)
        self._make_button(WIN_W // 2, 278, "Finger Counting",  self._launch_finger)

        # ── Footer ───────────────────────────────────────────────────
        self.canvas.create_text(
            WIN_W // 2, WIN_H - 16,
            text="Gesture Volume: Volume Control with Hand Gestures",
            font=("Courier New", 8), fill=TEXT_DIM, anchor="center"
        )

    def _make_button(self, cx, cy, label, cmd):
        W, H = 200, 38
        x1, y1 = cx - W // 2, cy - H // 2
        x2, y2 = cx + W // 2, cy + H // 2

        rect = self.canvas.create_rectangle(x1, y1, x2, y2,
                                            fill=BTN_BG, outline=CYAN, width=1)
        text = self.canvas.create_text(cx, cy, text=label,
                                       font=("Courier New", 11), fill=BTN_FG)

        def on_enter(_):
            self.canvas.itemconfig(rect, fill="#0d2a2a", outline="#00ffdd")
            self.canvas.itemconfig(text, fill="#00ffdd")
        def on_leave(_):
            self.canvas.itemconfig(rect, fill=BTN_BG, outline=CYAN)
            self.canvas.itemconfig(text, fill=BTN_FG)
        def on_click(_):
            cmd()

        for item in (rect, text):
            self.canvas.tag_bind(item, "<Enter>", on_enter)
            self.canvas.tag_bind(item, "<Leave>", on_leave)
            self.canvas.tag_bind(item, "<Button-1>", on_click)

    # ── Circuit-board decorative frame ───────────────────────────────
    def _draw_border(self):
        c = self.canvas
        pad = 18          # inner border inset
        lw  = 1

        # outer rect
        c.create_rectangle(pad, pad, WIN_W-pad, WIN_H-pad,
                            outline=CYAN_DIM, width=lw)
        # inner rect
        p2 = pad + 10
        c.create_rectangle(p2, p2, WIN_W-p2, WIN_H-p2,
                            outline=CYAN_DIM, width=lw)

        # corner L-brackets (top-left, top-right, bottom-left, bottom-right)
        sz = 28
        for (ox, oy, sx, sy) in [
            (pad, pad, 1, 1), (WIN_W-pad, pad, -1, 1),
            (pad, WIN_H-pad, 1, -1), (WIN_W-pad, WIN_H-pad, -1, -1)
        ]:
            c.create_line(ox, oy, ox+sx*sz, oy, fill=CYAN, width=2)
            c.create_line(ox, oy, ox, oy+sy*sz, fill=CYAN, width=2)

        # horizontal rule under title area
        c.create_line(pad+20, 128, WIN_W-pad-20, 128, fill=CYAN_DIM, width=lw)

        # small tick marks along top border
        for x in range(pad+40, WIN_W-pad-40, 22):
            c.create_line(x, pad, x, pad+6, fill=CYAN_DIM, width=1)

        # small tick marks along bottom border
        for x in range(pad+40, WIN_W-pad-40, 22):
            c.create_line(x, WIN_H-pad, x, WIN_H-pad-6, fill=CYAN_DIM, width=1)

        # left side bar graphics (waveform-like) — top-left quadrant
        self._draw_waveform(c, 34, 140, 60, 80)

        # bottom bar graphics
        self._draw_waveform(c, 120, WIN_H-36, 180, 14)

        # right corner decorative boxes
        for i, h in enumerate([10, 16, 10, 6]):
            rx = WIN_W - pad - 60 + i*14
            c.create_rectangle(rx, WIN_H-pad-h-4, rx+10, WIN_H-pad-4,
                                fill=CYAN_DIM, outline="")

    def _draw_waveform(self, c, x, y, width, height):
        """Draw a simple bar-chart waveform decoration."""
        bars = [0.3, 0.6, 0.9, 0.7, 0.5, 1.0, 0.8, 0.4, 0.6, 0.3]
        bw = width // len(bars)
        for i, h in enumerate(bars):
            bh = int(h * height)
            c.create_rectangle(x + i*bw, y - bh, x + i*bw + bw-2, y,
                                fill=CYAN_DIM, outline="")

    # ── Launch helpers ───────────────────────────────────────────────
    def _launch(self, script):
        path = os.path.join(BASE_DIR, script)
        subprocess.Popen([sys.executable, path])
        self.destroy()

    def _launch_gesture(self): self._launch("Gesture_Controll.py")
    def _launch_finger(self):  self._launch("Finger_controll.py")


if __name__ == "__main__":
    ControlHub().mainloop()

