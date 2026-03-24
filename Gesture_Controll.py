# Infosys GestureVolume: Pinch Gesture -> Mic Volume
# Compatible with mediapipe >= 0.10.x (Tasks API)
# Windows only. Requires: pip install pycaw comtypes mediapipe opencv-python Pillow matplotlib keyboard

import sys, os, time, tempfile, threading, queue, subprocess, platform, traceback, math
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision
import tkinter as tk
from PIL import Image, ImageTk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from collections import deque
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)

if platform.system() != "Windows":
    raise SystemExit("This script runs only on Windows.")

# --- Model path ---
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hand_landmarker.task")
if not os.path.exists(MODEL_PATH):
    raise SystemExit(
        f"Model file not found: {MODEL_PATH}\n"
        "Download it from:\n"
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
    )

# --- Config ---
CAM_INDEX            = 0
PINCH_PIXEL_THRESHOLD = 40
PINCH_NORM_THRESHOLD  = 0.03
MIN_DIST              = 25
MAX_DIST              = 190
VOLUME_STEP_THRESHOLD = 4

# --- Child process code (COM/pycaw in isolated process) ---
child_code = r'''
import sys, traceback
def eprint(*a, **k): print(*a, file=sys.stderr, **k)
try:
    from comtypes import CLSCTX_ALL, CoInitialize, CoUninitialize
    from comtypes.client import CreateObject
    from comtypes import GUID
    from ctypes import POINTER, cast
    from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
except Exception as ex:
    print("ERR IMPORT", ex); eprint("IMPORT TRACEBACK:"); traceback.print_exc(); sys.exit(2)

def out(s): print(s); sys.stdout.flush()

try: CoInitialize()
except Exception as ex: out("ERR COINIT " + str(ex)); sys.exit(3)

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
    out("ERR GET_IFACE " + str(ex))
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
            except Exception as ex: out("ERR GET " + str(ex))
        elif line.lower().startswith("set:"):
            try:
                v = max(0, min(100, int(line.split(":",1)[1].strip())))
                vol.SetMasterVolumeLevelScalar(v / 100.0, None)
                out(f"OK SET {v}")
            except Exception as ex: out("ERR SET " + str(ex))
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
    except Exception: pass
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
                raise RuntimeError("Failed to write to child: " + str(e))
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    resp = self.stdout_q.get(timeout=0.2)
                    if resp is not None: return resp
                except queue.Empty: continue
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

def set_system_volume(vol_percent):
    set_mic_volume_percent(int(vol_percent))

def get_system_volume():
    return get_mic_volume_percent()

print("[MAIN] Setup complete. Launching GUI...")

# ── Optional keyboard hotkeys ────────────────────────────────────────
try:
    import keyboard
    STEP_PERCENT = 2
    LAST = {"inc": 0, "dec": 0}
    COOLDOWN = 0.12

    def allow_action(k):
        now = time.time()
        if now - LAST[k] >= COOLDOWN:
            LAST[k] = now; return True
        return False

    def hot_inc():
        if not allow_action("inc"): return
        new = min(100, get_mic_volume_percent() + STEP_PERCENT)
        set_mic_volume_percent(new); print("[+] Mic ->", new, "%")

    def hot_dec():
        if not allow_action("dec"): return
        new = max(0, get_mic_volume_percent() - STEP_PERCENT)
        set_mic_volume_percent(new); print("[-] Mic ->", new, "%")

    keyboard.add_hotkey("ctrl+alt+up", hot_inc)
    keyboard.add_hotkey("ctrl+alt+down", hot_dec)
    print("Hotkeys registered: Ctrl+Alt+Up / Ctrl+Alt+Down")
except Exception:
    keyboard = None

# ── Hand detection using mediapipe Tasks API ─────────────────────────
base_options = mp_tasks.BaseOptions(model_asset_path=MODEL_PATH)
landmarker_options = mp_vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=2,
    min_hand_detection_confidence=0.5,
    min_hand_presence_confidence=0.5,
    min_tracking_confidence=0.5
)
hand_landmarker = mp_vision.HandLandmarker.create_from_options(landmarker_options)


def process_frame(frame, prev_pixel_dist, prev_volume):
    """Detect hand, measure pinch distance, update mic volume."""
    img_h, img_w = frame.shape[:2]
    img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
    result = None
    try:
        result = hand_landmarker.detect(mp_image)
    except Exception:
        pass

    norm_dist    = None
    pixel_dist   = None
    pinch        = False
    handedness_label = None
    current_volume   = prev_volume
    aspect_ratio     = None

    if result and result.hand_landmarks:
        lm = result.hand_landmarks[0]     # first detected hand

        # Thumb tip = 4, Index tip = 8
        lm_thumb = lm[4]; lm_index = lm[8]
        dx_n = lm_thumb.x - lm_index.x
        dy_n = lm_thumb.y - lm_index.y
        dz_n = lm_thumb.z - lm_index.z
        norm_dist = math.sqrt(dx_n**2 + dy_n**2 + dz_n**2)

        tx_px = int(round(lm_thumb.x * img_w)); ty_px = int(round(lm_thumb.y * img_h))
        ix_px = int(round(lm_index.x * img_w)); iy_px = int(round(lm_index.y * img_h))
        pixel_dist = math.hypot(tx_px - ix_px, ty_px - iy_px)

        if pixel_dist <= PINCH_PIXEL_THRESHOLD or norm_dist <= PINCH_NORM_THRESHOLD:
            pinch = True

        # Aspect ratio
        xs = [p.x for p in lm]; ys = [p.y for p in lm]
        width = max(xs) - min(xs); height = max(ys) - min(ys)
        if height > 0: aspect_ratio = width / height

        # Draw skeleton manually
        connections = [
            (0,1),(1,2),(2,3),(3,4),
            (0,5),(5,6),(6,7),(7,8),
            (5,9),(9,10),(10,11),(11,12),
            (9,13),(13,14),(14,15),(15,16),
            (13,17),(17,18),(18,19),(19,20),(0,17)
        ]
        pts = [(int(p.x*img_w), int(p.y*img_h)) for p in lm]
        for a, b in connections:
            try: cv2.line(frame, pts[a], pts[b], (0, 255, 0), 2)
            except: pass
        for pt in pts:
            try: cv2.circle(frame, pt, 4, (255, 255, 255), -1)
            except: pass

        # Thumb/Index highlights
        cv2.circle(frame, (tx_px, ty_px), 10, (0, 0, 255), -1)
        cv2.circle(frame, (ix_px, iy_px), 10, (255, 0, 0), -1)
        cv2.line(frame, (tx_px, ty_px), (ix_px, iy_px), (0, 255, 0), 3)

        mid_x = (tx_px + ix_px) // 2; mid_y = (ty_px + iy_px) // 2
        cv2.circle(frame, (mid_x, mid_y), 10, (0, 255, 255), -1)

        # Volume from pixel distance
        if pixel_dist is not None:
            new_volume = int(np.clip(np.interp(pixel_dist, [MIN_DIST, MAX_DIST], [0, 100]), 0, 100))
            if abs(new_volume - prev_volume) >= VOLUME_STEP_THRESHOLD:
                set_system_volume(new_volume)
                current_volume = new_volume

        cv2.putText(frame, f"Volume: {current_volume}%", (mid_x - 80, mid_y - 60),
                    cv2.FONT_HERSHEY_DUPLEX, 1.0, (255, 255, 0), 2, cv2.LINE_AA)

        # Handedness label
        if result.handedness:
            try:
                handedness_label = result.handedness[0][0].display_name
            except Exception:
                handedness_label = None

    # HUD text overlay (top-left)
    lines = []
    if handedness_label:     lines.append(f"Hand: {handedness_label}")
    if norm_dist is not None: lines.append(f"Norm: {norm_dist:.4f}")
    if pixel_dist is not None: lines.append(f"Pixel: {pixel_dist:.1f}px")
    lines.append(f"Pinch: {'YES' if pinch else 'NO'}")
    for i, txt in enumerate(lines):
        cv2.putText(frame, txt, (10, 30 + i*30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

    return frame, norm_dist, pixel_dist, pinch, current_volume, aspect_ratio


# ── Tkinter App ───────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title("Infosys_GestureVolume: Gesture -> Mic Volume")
        root.geometry("1280x780")
        root.configure(bg="#111")
        root.resizable(True, False)
        root.rowconfigure(0, weight=0); root.rowconfigure(1, weight=1); root.rowconfigure(2, weight=0)
        root.columnconfigure(0, weight=1)

        # Header
        hdr = tk.Frame(root, bg="#181818", height=70)
        hdr.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 0))
        hdr.grid_propagate(False)
        tk.Label(hdr, text="Infosys_GestureVolume: Volume Control with Hand Gestures",
                 font=("Consolas", 18, "bold"), fg="#00ffcc", bg="#181818",
                 anchor="e", padx=20).pack(fill=tk.X, pady=(5, 0))
        tk.Label(hdr,
                 text="Project made by BATCH A | SNEHIL GHOSH, GAUTAM N CHIPKAR, AMRUTHA VARSHANI, AYUSH GORGE",
                 font=("Consolas", 12), fg="#cccccc", bg="#181818",
                 anchor="e", padx=20).pack(fill=tk.X, pady=(0, 5))

        # Main area
        main = tk.Frame(root, bg="#111")
        main.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        main.columnconfigure(0, weight=1); main.columnconfigure(1, weight=3)
        main.rowconfigure(0, weight=1)

        # Graph panel
        self.graph_panel = tk.Frame(main, bg="#181818")
        self.graph_panel.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        for i in range(3): self.graph_panel.rowconfigure(i, weight=1)

        # Video panel
        self.video_label = tk.Label(main, bg="#000")
        self.video_label.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

        # Status bar
        self.status_label = tk.Label(root, text="Starting camera...",
                                      bg="#111", fg="#00ff99",
                                      font=("Consolas", 13), anchor="w", padx=10)
        self.status_label.grid(row=2, column=0, sticky="ew", pady=(0, 4))

        # Graph 1 — Live distances
        self.fig1 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax1  = self.fig1.add_subplot(111)
        self.ax1.set_facecolor("#111"); self.ax1.tick_params(colors="white")
        self.ax1.set_title("LIVE PINCH DISTANCE", color="white")
        self.line_norm, = self.ax1.plot([], [], label="Norm", color="#00ffcc")
        self.line_pix,  = self.ax1.plot([], [], label="Pixel (scaled)", color="#ff00ff")
        self.ax1.legend(facecolor="#222", labelcolor="white")
        self.canvas1 = FigureCanvasTkAgg(self.fig1, master=self.graph_panel)
        self.canvas1.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # Graph 2 — Aspect Ratio
        self.fig2 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax2  = self.fig2.add_subplot(111)
        self.ax2.set_facecolor("#111"); self.ax2.tick_params(colors="white")
        self.ax2.set_title("Aspect Ratio", color="white")
        self.line_aspect, = self.ax2.plot([], [], color="#ffcc00")
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=self.graph_panel)
        self.canvas2.get_tk_widget().grid(row=1, column=0, sticky="nsew")

        # Graph 3 — placeholder
        self.fig3 = Figure(figsize=(4, 2), dpi=100, facecolor="#111")
        self.ax3  = self.fig3.add_subplot(111)
        self.ax3.set_facecolor("#111")
        self.ax3.text(0.5, 0.5, "Future Graph", color="gray",
                      ha="center", va="center", fontsize=14)
        self.canvas3 = FigureCanvasTkAgg(self.fig3, master=self.graph_panel)
        self.canvas3.get_tk_widget().grid(row=2, column=0, sticky="nsew")

        # Camera
        try:
            self.cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_DSHOW)
        except Exception:
            self.cap = cv2.VideoCapture(CAM_INDEX)
        if not self.cap.isOpened():
            self.status_label.config(text="Cannot open camera."); return

        self.running       = True
        self.prev_pixel_dist = None
        self.prev_volume   = get_system_volume()
        self.timestamps    = deque(maxlen=150)
        self.norm_dists    = deque(maxlen=150)
        self.pixel_dists   = deque(maxlen=150)
        self.aspect_ratios = deque(maxlen=150)
        self.start_time    = time.time()

        root.bind("<Key>", self._on_keypress)
        root.protocol("WM_DELETE_WINDOW", self.stop_and_close)
        self._update_frame()

    def _on_keypress(self, event):
        if hasattr(event, "char") and event.char and event.char.lower() == "q":
            self.stop_and_close()

    def _update_frame(self):
        if not self.running: return

        ret, frame = self.cap.read()
        if not ret:
            self.status_label.config(text="Failed to read frame.")
            self.stop_and_close(); return

        frame = cv2.flip(frame, 1)
        frame, norm_dist, pixel_dist, pinch, self.prev_volume, aspect_ratio = \
            process_frame(frame, self.prev_pixel_dist, self.prev_volume)
        self.prev_pixel_dist = pixel_dist

        # Show video
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)
        lbl_w = self.video_label.winfo_width() or 960
        lbl_h = self.video_label.winfo_height() or 720
        try:  resample = Image.Resampling.LANCZOS
        except: resample = Image.LANCZOS
        pil_img = pil_img.resize((lbl_w, lbl_h), resample)
        imgtk = ImageTk.PhotoImage(pil_img)
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk)

        # Update graph data
        elapsed = time.time() - self.start_time
        self.timestamps.append(elapsed)
        self.norm_dists.append(norm_dist if norm_dist is not None else float("nan"))
        self.pixel_dists.append(pixel_dist if pixel_dist is not None else float("nan"))
        self.aspect_ratios.append(aspect_ratio if aspect_ratio is not None else float("nan"))

        t_arr    = np.array(self.timestamps)
        norm_arr = np.array(self.norm_dists)
        pix_arr  = np.array(self.pixel_dists)
        asp_arr  = np.array(self.aspect_ratios)

        # Graph 1
        try:
            with np.errstate(all="ignore"):
                factor = 1.0
                if np.nanmax(pix_arr) > 0 and np.nanmax(norm_arr) > 0:
                    factor = (np.nanmax(norm_arr)+1e-6) / (np.nanmax(pix_arr)+1e-6)
            scaled_pix = pix_arr * factor
            self.line_norm.set_data(t_arr, norm_arr)
            self.line_pix.set_data(t_arr, scaled_pix)
            self.ax1.set_xlim(max(0, elapsed-15), elapsed+0.1)
            y_all = np.concatenate([np.nan_to_num(norm_arr), np.nan_to_num(scaled_pix)])
            if np.any(np.isfinite(y_all)):
                self.ax1.set_ylim(np.nanmin(y_all)-0.1, np.nanmax(y_all)+0.1)
            self.canvas1.draw_idle()
        except Exception: pass

        # Graph 2
        try:
            self.line_aspect.set_data(t_arr, asp_arr)
            self.ax2.set_xlim(max(0, elapsed-15), elapsed+0.1)
            fin = asp_arr[np.isfinite(asp_arr)]
            if len(fin) > 0:
                self.ax2.set_ylim(fin.min()-0.1, fin.max()+0.1)
            self.canvas2.draw_idle()
        except Exception: pass

        # Status bar
        now      = time.strftime("%H:%M:%S")
        ns  = f"{norm_dist:.4f}"  if norm_dist   is not None else "0.0000"
        ps  = f"{pixel_dist:.1f}" if pixel_dist  is not None else "0.0"
        asp = f"{aspect_ratio:.2f}" if aspect_ratio is not None else "N/A"
        self.status_label.config(
            text=f"[{now}] | Norm={ns} | Pixel={ps}px | Aspect={asp} | Volume={self.prev_volume}%"
        )
        self.root.after(50, self._update_frame)

    def stop_and_close(self):
        if not getattr(self, "running", False):
            try: self.root.destroy()
            except: pass
            return
        self.running = False
        try:
            if self.cap and self.cap.isOpened(): self.cap.release()
        except: pass
        try: hand_landmarker.close()
        except: pass
        if keyboard:
            try: keyboard.unhook_all_hotkeys()
            except: pass
        try: mc.close()
        except: pass
        try: os.unlink(child_path)
        except: pass
        try: self.root.destroy()
        except: pass


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    try:
        root.mainloop()
    finally:
        try: mc.close()
        except: pass
        if keyboard:
            try: keyboard.unhook_all_hotkeys()
            except: pass
        print("Exited GUI and cleaned up.")
