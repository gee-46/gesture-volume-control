# Infosys GestureVolume: Finger -> Mic Volume
# Compatible with mediapipe >= 0.10.x (Tasks API)
# Windows only. Requires: pip install pycaw comtypes mediapipe opencv-python Pillow matplotlib

import sys, os, time, tempfile, threading, queue, subprocess, platform, traceback, math
import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.components.containers import landmark as mp_landmark
import tkinter as tk
from PIL import Image, ImageTk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from collections import deque, Counter
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)

if platform.system() != "Windows":
    raise SystemExit("This script runs only on Windows.")

# --- Window constants ---
WIN_W = 1280
WIN_H = 720

# --- MediaPipe HandLandmarker model path ---
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")
if not os.path.exists(MODEL_PATH):
    raise SystemExit(f"Model file not found: {MODEL_PATH}\nDownload it from: https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")

# --- Child process code for COM/pycaw mic control ---
child_code = r'''
import sys, traceback
def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)
try:
    from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
    from comtypes.client import CreateObject
    from comtypes import GUID
    from ctypes import POINTER, cast
    from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
except Exception as ex:
    print("ERR IMPORT", ex); eprint("IMPORT TRACEBACK:"); traceback.print_exc(); sys.exit(2)

def out(s):
    print(s); sys.stdout.flush()

try:
    CoInitialize()
except Exception as ex:
    out("ERR COINIT " + str(ex)); traceback.print_exc(); sys.exit(3)

def _create_enum():
    from comtypes.client import CreateObject
    from pycaw.pycaw import IMMDeviceEnumerator
    try:
        return CreateObject("MMDeviceEnumerator.MMDeviceEnumerator", interface=IMMDeviceEnumerator)
    except Exception:
        clsid = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        return CreateObject(clsid, interface=IMMDeviceEnumerator)

def _get_vol_iface():
    enumerator = _create_enum()
    device = enumerator.GetDefaultAudioEndpoint(1, 0)
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    from ctypes import cast, POINTER
    return cast(iface, POINTER(IAudioEndpointVolume))

try:
    vol = _get_vol_iface()
except Exception as ex:
    out("ERR GET_IFACE " + str(ex)); traceback.print_exc()
    try: CoUninitialize()
    except: pass
    sys.exit(4)

out("OK READY")

for raw in sys.stdin:
    line = raw.strip()
    if not line: continue
    try:
        if line.lower() == "get":
            try:
                p = int(round(vol.GetMasterVolumeLevelScalar() * 100))
                out(f"OK GET {p}")
            except Exception as ex:
                out("ERR GET " + str(ex))
        elif line.lower().startswith("set:"):
            try:
                v = max(0, min(100, int(line.split(":",1)[1].strip())))
                vol.SetMasterVolumeLevelScalar(v / 100.0, None)
                out(f"OK SET {v}")
            except Exception as ex:
                out("ERR SET " + str(ex))
        elif line.lower() == "quit":
            out("OK QUIT"); break
        else:
            out("ERR UNKNOWN " + line)
    except Exception as ex:
        out("ERR LOOP " + str(ex)); break

try: CoUninitialize()
except: pass
'''

tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".py", prefix="child_mic_server_")
tmp.write(child_code.encode("utf-8"))
tmp.flush(); tmp.close()
child_path = tmp.name
print("Child script written to:", child_path)

proc = subprocess.Popen([sys.executable, "-u", child_path],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, bufsize=1)

stdout_q = queue.Queue()
stderr_q = queue.Queue()

def _read_stream(stream, q):
    try:
        for line in stream:
            q.put(line.rstrip("\n"))
    except Exception:
        pass
    finally:
        try: stream.close()
        except: pass

t_o = threading.Thread(target=_read_stream, args=(proc.stdout, stdout_q), daemon=True)
t_e = threading.Thread(target=_read_stream, args=(proc.stderr, stderr_q), daemon=True)
t_o.start(); t_e.start()

start_wait = time.time(); ready = False
while time.time() - start_wait < 6.0:
    try:
        ln = stdout_q.get(timeout=0.2)
    except queue.Empty:
        continue
    print("CHILD:", ln)
    if ln.strip().upper().startswith("OK READY"):
        ready = True; break

if not ready:
    while not stderr_q.empty():
        print("CHILD STDERR:", stderr_q.get())
    raise SystemExit("Child mic server did not start correctly.")

class MicController:
    def __init__(self, proc, stdout_q):
        self.proc = proc; self.stdout_q = stdout_q; self.lock = threading.Lock()

    def _send(self, line, timeout=2.0):
        with self.lock:
            if self.proc.poll() is not None:
                raise RuntimeError("Child process has exited.")
            try:
                self.proc.stdin.write(line.strip() + "\n"); self.proc.stdin.flush()
            except Exception as e:
                raise RuntimeError("Failed to write to child stdin: " + str(e))
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    resp = self.stdout_q.get(timeout=0.2)
                    if resp is not None: return resp
                except queue.Empty:
                    continue
            raise TimeoutError("No reply from child")

    def get_volume(self):
        resp = self._send("get")
        if resp.upper().startswith("OK GET"):
            try: return int(resp.split()[-1])
            except: return 50
        return 50

    def set_volume(self, percent):
        try: self._send(f"set:{int(percent)}")
        except: pass

    def close(self):
        try: self._send("quit", timeout=1.0)
        except: pass
        try: self.proc.kill()
        except: pass
        try: self.proc.wait(timeout=1.0)
        except: pass

mc = MicController(proc, stdout_q)

def get_mic_volume_percent():
    try: return mc.get_volume()
    except: return 50

def set_mic_volume_percent(v):
    try: mc.set_volume(int(v))
    except: pass

def get_mic_is_muted():
    return False

def fingers_to_percent(n):
    return {0: 0, 1: 20, 2: 40, 3: 60, 4: 80, 5: 100}.get(n, 0)

print("[MAIN] Setup complete. Launching GUI...")

# ─────────────────────────────────────────────
#  App class — uses mediapipe Tasks HandLandmarker
# ─────────────────────────────────────────────
class App:
    def __init__(self, stable_frames=6, cam_index=0):
        self.stable_frames = stable_frames
        self.cam_index = cam_index

        # 1. Camera
        try: self.cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        except: self.cap = cv2.VideoCapture(cam_index)
        if not self.cap.isOpened(): raise RuntimeError("Cannot open camera.")

        # 2. MediaPipe HandLandmarker (Tasks API)
        base_options = mp_tasks.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=1,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.hand_landmarker = mp_vision.HandLandmarker.create_from_options(options)

        # 3. Drawing utilities (still available in 0.10 as a standalone)
        try:
            from mediapipe.python.solutions import drawing_utils, drawing_styles, hands as hands_sol
            self._drawing_utils = drawing_utils
            self._drawing_styles = drawing_styles
            self._hands_connections = hands_sol.HAND_CONNECTIONS
        except Exception:
            self._drawing_utils = None

        # 4. Logic & State
        self.history = deque(maxlen=self.stable_frames)
        self.current_applied = get_mic_volume_percent()
        self.last_observed = 0
        self.running = True
        self.start_time = time.time()

        # ── UI SETUP ──────────────────────────────────
        self.root = tk.Tk()
        self.root.title("Infosys_GestureVolume: Finger -> Mic Volume")
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.configure(bg="#050505")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=0)
        self.root.columnconfigure(0, weight=1)

        # HEADER
        self.header_frame = tk.Frame(self.root, bg="#111", height=70)
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 5))
        self.header_frame.grid_propagate(False)
        tk.Label(self.header_frame, text="Infosys_GestureVolume: Volume Control with Hand Gestures",
                 font=("Consolas", 18, "bold"), fg="#00ffcc", bg="#111", anchor="w", padx=20
                 ).pack(fill=tk.X, pady=(5, 0))
        tk.Label(self.header_frame,
                 text="Project by BATCH A | SNEHIL GHOSH, GAUTAM N CHIPKAR, AMRUTHA VARSHANI, AYUSH GORGE",
                 font=("Consolas", 10), fg="#cccccc", bg="#111", anchor="w", padx=20
                 ).pack(fill=tk.X, pady=(0, 5))

        # MAIN CONTENT
        self.main_frame = tk.Frame(self.root, bg="#050505")
        self.main_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=3)
        self.main_frame.rowconfigure(0, weight=1)

        # LEFT PANEL (HUD)
        self.hud_panel = tk.Frame(self.main_frame, bg="#050505")
        self.hud_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.hud_panel.rowconfigure(0, weight=1)
        self.hud_panel.rowconfigure(1, weight=1)

        # GRAPH 1: ARC REACTOR
        self.fig_arc = Figure(figsize=(3, 3), dpi=100, facecolor='#050505')
        self.ax_arc = self.fig_arc.add_subplot(111, projection='polar')
        self.ax_arc.set_facecolor('#050505')
        self.ax_arc.grid(False)
        self.ax_arc.set_xticklabels([])
        self.ax_arc.set_yticklabels([])
        self.ax_arc.spines['polar'].set_visible(False)
        self.ax_arc.bar([0], [2.4], width=2*math.pi, color='#151515', bottom=0.0)
        self.arc_bar = self.ax_arc.bar([0], [2.4], width=0, color='#00ffff', bottom=0.0)[0]
        self.ax_arc.set_ylim(0, 2.5)
        self.text_vol = self.ax_arc.text(0, 0, "0%", ha='center', va='center',
                                          color='white', fontsize=18, fontweight='bold', zorder=10)
        self.ax_arc.text(0, -1.5, "VOLUME", ha='center', va='center',
                         color='black', fontsize=9, fontweight='bold', zorder=10)
        self.canvas_arc = FigureCanvasTkAgg(self.fig_arc, master=self.hud_panel)
        self.canvas_arc.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # GRAPH 2: Z-AXIS PROXIMITY
        self.fig_prox = Figure(figsize=(3, 2), dpi=100, facecolor='#050505')
        self.ax_prox = self.fig_prox.add_subplot(111)
        self.ax_prox.set_facecolor('#0f0f0f')
        self.ax_prox.set_title("Z-AXIS PROXIMITY SENSOR", color='#00ffcc', fontsize=9, pad=10)
        self.ax_prox.set_ylabel("Depth Intensity", color='#888', fontsize=8)
        self.ax_prox.tick_params(colors='#888', labelsize=7)
        self.ax_prox.set_xticks([])
        self.ax_prox.grid(True, axis='y', color="#333", linestyle="--", linewidth=0.5)
        self.bar_prox = self.ax_prox.bar([0], [0], width=0.4, color='#00ff00', alpha=0.8)[0]
        self.ax_prox.set_ylim(0, 1.0)
        self.ax_prox.set_xlim(-0.5, 0.5)
        self.ax_prox.axhline(0.8, color='#ff3333', linestyle=':', linewidth=1)
        self.ax_prox.text(0.3, 0.82, "CRITICAL", color='#ff3333', fontsize=6)
        self.canvas_prox = FigureCanvasTkAgg(self.fig_prox, master=self.hud_panel)
        self.canvas_prox.get_tk_widget().grid(row=1, column=0, sticky="nsew", pady=10)

        # RIGHT: VIDEO
        self.video_label = tk.Label(self.main_frame, bg="black")
        self.video_label.grid(row=0, column=1, sticky="nsew")

        # FOOTER
        self.status_label = tk.Label(self.root, text="System Ready", bg="#111", fg="#00ff99",
                                      font=("Consolas", 12), anchor="w", padx=10, pady=5)
        self.status_label.grid(row=2, column=0, sticky="ew")

        self.root.bind("<Key>", self._on_keypress)
        self.root.after(10, self._update_frame)

    def _on_keypress(self, event):
        try:
            if hasattr(event, "char") and event.char and event.char.lower() == "q":
                self._on_close()
        except: pass

    def _detect_fingers(self, lm_list):
        """Count raised fingers from 21 landmarks (NormalizedLandmark list)."""
        if not lm_list or len(lm_list) < 21:
            return 0
        fingers_found = 0
        tips_pips = [(8, 6), (12, 10), (16, 14), (20, 18)]
        for tip, pip in tips_pips:
            try:
                if lm_list[tip].y < lm_list[pip].y:
                    fingers_found += 1
            except: pass
        try:
            # Thumb: compare x coords
            if lm_list[17].x > lm_list[2].x:
                if lm_list[4].x < lm_list[2].x: fingers_found += 1
            else:
                if lm_list[4].x > lm_list[2].x: fingers_found += 1
        except: pass
        return max(0, min(5, fingers_found))

    def _draw_landmarks_manual(self, frame, lm_list, img_w, img_h):
        """Draw hand skeleton manually without old drawing_utils."""
        connections = [
            (0,1),(1,2),(2,3),(3,4),
            (0,5),(5,6),(6,7),(7,8),
            (5,9),(9,10),(10,11),(11,12),
            (9,13),(13,14),(14,15),(15,16),
            (13,17),(17,18),(18,19),(19,20),
            (0,17)
        ]
        pts = [(int(lm.x * img_w), int(lm.y * img_h)) for lm in lm_list]
        for a, b in connections:
            try: cv2.line(frame, pts[a], pts[b], (255, 0, 255), 2)
            except: pass
        for pt in pts:
            try: cv2.circle(frame, pt, 3, (0, 255, 255), -1)
            except: pass

    def _update_frame(self):
        if not self.running: return

        ret, frame = self.cap.read()
        if not ret:
            self.root.after(50, self._update_frame)
            return

        frame = cv2.flip(frame, 1)
        img_h, img_w = frame.shape[:2]
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Run hand detection
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        result = None
        try:
            result = self.hand_landmarker.detect(mp_image)
        except: pass

        fingers_found = 0
        wrist_z_estimate = 0.0

        if result and result.hand_landmarks:
            lm_list = result.hand_landmarks[0]  # first hand

            # Draw skeleton
            self._draw_landmarks_manual(frame, lm_list, img_w, img_h)

            # Count fingers
            fingers_found = self._detect_fingers(lm_list)

            # Z-proximity: wrist(0) to middle MCP(9)
            try:
                x0, y0 = lm_list[0].x, lm_list[0].y
                x9, y9 = lm_list[9].x, lm_list[9].y
                dist = math.sqrt((x9-x0)**2 + (y9-y0)**2)
                norm_prox = (dist - 0.1) * 3.5
                wrist_z_estimate = max(0.0, min(1.0, norm_prox))
            except:
                wrist_z_estimate = 0.0

        # Logic
        self.history.append(fingers_found)
        chosen = self.history[-1]
        try:
            if len(self.history) == self.history.maxlen:
                counts = Counter(self.history)
                most_common = counts.most_common()
                if most_common:
                    chosen = max([v for v, c in most_common if c == most_common[0][1]])
        except: pass

        target_pct = fingers_to_percent(chosen)
        if len(self.history) == self.history.maxlen and target_pct != self.current_applied:
            try:
                set_mic_volume_percent(target_pct)
                self.current_applied = get_mic_volume_percent()
                print(f"[APPLY] Fingers {chosen} -> {target_pct}%")
            except: pass

        self.last_observed = chosen
        muted = get_mic_is_muted()

        # HUD: Arc reactor
        try:
            vol_rad = (self.current_applied / 100.0) * (2 * math.pi)
            self.arc_bar.set_width(vol_rad)
            col = '#00ffff' if self.current_applied < 50 else ('#ff00ff' if self.current_applied < 80 else '#ff3333')
            self.arc_bar.set_color(col)
            self.text_vol.set_text(f"{int(self.current_applied)}%")
            self.canvas_arc.draw_idle()
        except: pass

        # HUD: Proximity
        try:
            self.bar_prox.set_height(wrist_z_estimate)
            prox_col = '#00ff00' if wrist_z_estimate < 0.5 else ('#ffcc00' if wrist_z_estimate < 0.8 else '#ff0000')
            self.bar_prox.set_color(prox_col)
            self.canvas_prox.draw_idle()
        except: pass

        # Video overlay
        display_frame = self._overlay_text(frame.copy(), self.last_observed, self.current_applied, muted)
        img_rgb2 = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(img_rgb2)
        try:
            lbl_w = self.video_label.winfo_width() or 1
            lbl_h = self.video_label.winfo_height() or 1
            pil = pil.resize((lbl_w, lbl_h), Image.LANCZOS)
        except: pass
        imgtk = ImageTk.PhotoImage(image=pil)
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk)

        # Status
        now = time.strftime("%H:%M:%S")
        self.status_label.config(
            text=f"[{now}] | SYS: ONLINE | Fingers: {self.last_observed} | Vol: {self.current_applied}% | Mute: {muted} | Z-Depth: {wrist_z_estimate:.2f}"
        )
        self.root.after(15, self._update_frame)

    def _overlay_text(self, frame, fingers, volume, muted):
        h, w = frame.shape[:2]
        txt1 = f"Fingers: {fingers}"
        txt2 = f"Mic: {volume}%"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale1 = max(0.9, w / 640)
        scale2 = max(0.6, w / 900)
        thickness1 = 3; thickness2 = 2
        (t1_w, t1_h), _ = cv2.getTextSize(txt1, font, scale1, thickness1)
        (t2_w, t2_h), _ = cv2.getTextSize(txt2, font, scale2, thickness2)
        pad = 12
        box_w = max(t1_w, t2_w) + pad*4
        box_h = t1_h + t2_h + pad*3
        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (10 + box_w, 10 + box_h), (6,6,6), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
        org1 = (10 + pad, 10 + pad + t1_h)
        cv2.putText(frame, txt1, org1, font, scale1, (0,220,180), thickness1+2, cv2.LINE_AA)
        cv2.putText(frame, txt1, org1, font, scale1, (255,255,255), thickness1, cv2.LINE_AA)
        org2 = (10 + pad, 10 + pad + t1_h + pad + t2_h)
        cv2.putText(frame, txt2, org2, font, scale2, (0,180,220), thickness2+2, cv2.LINE_AA)
        cv2.putText(frame, txt2, org2, font, scale2, (255,255,255), thickness2, cv2.LINE_AA)
        return frame

    def _on_close(self):
        self.running = False
        try: self.cap.release()
        except: pass
        try: self.hand_landmarker.close()
        except: pass
        try: mc.close()
        except: pass
        try: os.unlink(child_path)
        except: pass
        try: self.root.destroy()
        except: pass
        sys.exit(0)

    def run(self):
        try: self.root.mainloop()
        finally:
            try: mc.close()
            except: pass

if __name__ == "__main__":
    app = App()
    app.run()
