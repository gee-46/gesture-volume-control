# 🖐️ Infosys GestureVolume — Control with Hand Gestures

> **Real-time microphone volume control using hand gestures via webcam.**  
> Project by **Batch A** — Snehil Ghosh · Gautam N Chipkar · Amrutha Varshani · Ayush Gorge

---

## 📸 Overview

GestureVolume uses your webcam and MediaPipe hand tracking to control your system **microphone volume** in real time — no buttons, no sliders. Just your hand.

Two control modes are available:

| Mode | How it works |
|------|-------------|
| ✋ **Finger Counting** | Show 0–5 fingers → mic volume jumps to 0%, 20%, 40%, 60%, 80%, or 100% |
| 🤏 **Pinch Gesture** | Pinch thumb & index finger and slide apart/together for smooth volume control |

---

## 🖥️ Requirements

- **OS:** Windows only *(uses `pycaw` + `comtypes` for mic control)*
- **Python:** 3.8 or higher
- **Webcam:** Any standard USB or built-in webcam

### Python Dependencies

Install everything with one command:

```bash
pip install -r requirements.txt
```

| Package | Purpose |
|---------|---------|
| `mediapipe >= 0.10.0` | Hand landmark detection |
| `opencv-python >= 4.5.0` | Webcam capture & frame processing |
| `Pillow >= 9.0.0` | Image display in Tkinter |
| `matplotlib >= 3.5.0` | Live graphs in the HUD |
| `pycaw` | Windows microphone volume control |
| `comtypes` | Windows COM interface (required by pycaw) |
| `keyboard >= 0.13.5` | Optional hotkeys (Ctrl+Alt+Up/Down) |
| `numpy >= 1.21.0` | Numerical operations |

### MediaPipe Model File

Download the hand landmark model and place it in the **project root**:

```bash
curl -o hand_landmarker.task "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
```

Or download manually from:  
[https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)

---

## 🚀 Getting Started

```bash
# 1. Clone the repo
git clone https://github.com/Light-seekr/ISB_Batch-A_Project.git
cd ISB_Batch-A_Project

# 2. Install dependencies
pip install -r requirements.txt

# 3. Download the model file (if not already present)
curl -o hand_landmarker.task "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"

# 4. Launch the app
python main.py
```

---

## 📂 Project Structure

```
ISB_Batch-A_Project/
├── main.py                  # Launcher — Control Hub (start here)
├── Finger_controll.py       # Mode 1: Finger Counting
├── Gesture_Controll.py      # Mode 2: Pinch Gesture
├── hand_landmarker.task     # MediaPipe model file (download separately)
├── requirements.txt         # Python dependencies
├── Finger_controll.ipynb    # Jupyter notebook (reference)
└── Gesture_Controll.ipynb   # Jupyter notebook (reference)
```

---

## 🎮 Controls

| Action | Result |
|--------|--------|
| Show 0–5 fingers (Finger mode) | Sets mic to 0–100% in steps |
| Slide pinch apart/together (Gesture mode) | Smoothly adjusts mic volume |
| `Ctrl+Alt+Up` | Increase mic volume by 2% |
| `Ctrl+Alt+Down` | Decrease mic volume by 2% |
| Press `Q` or close window | Exit the app |

---

## ⚠️ Notes

- **Windows only** — mic control relies on Windows Audio APIs (`pycaw`/`comtypes`)
- **Run from terminal** (`python main.py`) — Tkinter GUIs do not work reliably inside Jupyter Lab or VS Code's interactive window
- The `hand_landmarker.task` model file is **not included** in the repo (too large); download it separately using the instructions above
- Make sure your **webcam is accessible** and not in use by another application

---

## 📄 License

This project was developed as part of the **Infosys Springboard Batch A** internship program.
