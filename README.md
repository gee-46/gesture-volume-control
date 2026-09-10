# 🖐️ Infosys GestureVolume — Two-Hand Gesture Audio Control

> **Real-time, ultra-low-latency Microphone & Speaker volume control using two-hand computer vision gestures via webcam.**  
> Project by **Batch A** — Snehil Ghosh · Gautam N Chipkar · Amrutha Varshani · Ayush Gorge

---

## 📸 Overview

**GestureVolume** transforms any standard webcam into an intelligent, touch-free audio control interface. Powered by MediaPipe's Tasks API and Windows Core Audio (`pycaw`), it allows you to control your system **Microphone** or **Speaker** volume with natural hand gestures in real time.

### ✨ Key Features

- 👐 **Two-Hand Responsibility Separation**:
  - **Right Hand**: Dedicated to volume modulation (Continuous Pinch or Discrete Finger Counting).
  - **Left Hand**: Dedicated **Lock / Release Toggle** (raise index finger to freeze/lock the volume at the desired level).
- 🎙️ / 🔊 **Dual Audio Target (Mic vs. Speaker)**: Seamlessly toggle between controlling microphone input sensitivity (`eCapture`) and master speaker output (`eRender`) directly from the launcher.
- ⚡ **Ultra-Low Latency & High FPS**: Threaded camera capture buffer, throttled GUI rendering, and cached COM endpoints eliminate lag.
- 📏 **Distance-Independent 3D Pinch Tracking**: Hand-size normalization (wrist-to-MCP scaling) enables reliable control whether you are close to the camera or up to 3 meters away.
- 📊 **Real-time Cyberpunk HUD**: Live animated plots, hand detection indicators, lock/unlock banners, and an optional debug monitor.

---

## 🎮 Control Modes

Two primary control modes are selectable from the Control Hub:

| Mode | Right Hand (Volume) | Left Hand (Lock Toggle) |
|------|---------------------|-------------------------|
| 🤏 **Continuous Pinch** (`Gesture_Controll.py`) | Thumb & Index pinch distance smoothly adjusts volume (0%–100%) | Raise index finger to toggle **LOCK** (freezes volume) / **UNLOCK** |
| ✋ **Finger Counting** (`Finger_controll.py`) | Show 0–5 fingers to jump directly to 0%, 20%, 40%, 60%, 80%, or 100% | Raise index finger to toggle **LOCK** (freezes volume) / **UNLOCK** |

### 📸 Proof of Concept & Interface

**1. Control Hub Launcher (`main.py`)**  
*Choose control mode and toggle between Microphone or Speaker target.*  
![Control Hub](1.png)

**2. Pinch Gesture Mode (`Gesture_Controll.py`)**  
*Right hand controls smooth volume; Left hand locks/unlocks adjustments.*  
![Pinch Gesture](2.png)

**3. Finger Counting Mode (`Finger_controll.py`)**  
*Right hand counts 0–5 fingers; Left hand manages volume lock.*  
![Finger Counting](3.png)

---

## 🚀 Getting Started

### 🖥️ System Requirements
- **OS:** Windows 10 / 11 *(required for Windows Core Audio APIs)*
- **Python:** 3.8 or higher
- **Webcam:** Any built-in or USB webcam

### 📥 Installation

```bash
# 1. Clone the repository
git clone https://github.com/gee-46/gesture-volume-control.git
cd gesture-volume-control

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch the Control Hub
python main.py
```

> **Note:** The MediaPipe Hand Landmarker model file (`hand_landmarker.task`) is already included in the repository. If missing, it will automatically download on first run.

---

## 🕹️ Controls & Gestures

| Gesture / Input | Hand / Target | Action |
|-----------------|---------------|--------|
| **Thumb + Index Pinch** | 🟢 **Right Hand** (Pinch Mode) | Moving fingers apart increases volume; pinching together decreases volume |
| **0 to 5 Fingers Raised** | 🟢 **Right Hand** (Finger Mode) | Sets volume directly to 0%, 20%, 40%, 60%, 80%, or 100% |
| **Index Finger Only** | 🔵 **Left Hand** (Both Modes) | Toggles **VOLUME LOCKED** (freezes volume) and **VOLUME UNLOCKED** |
| **Volume Target Toggle** | 🎛️ **Control Hub GUI** | Switches between **Microphone** input and **Speaker** output |
| `Ctrl+Alt+Up` / `Ctrl+Alt+Down` | ⌨️ Keyboard Hotkeys | Step volume up / down by 2% manually |
| `Q` | ⌨️ Keyboard | Quit active control window |

---

## ⚡ Technical Optimizations

1. **Threaded Camera Pipeline (`CameraStream`)**: Background daemon thread continuously pulls OpenCV frames, eliminating synchronous camera buffer wait-states on the main thread.
2. **Matplotlib Decoupling (10 FPS)**: Graph drawing is throttled to 10 FPS while gesture recognition and video streaming run at full camera FPS (30–60 FPS), eliminating GUI stalls.
3. **Throttled COM Audio Queries**: System volume is cached locally with instantaneous updates; physical audio device status is polled periodically (every 2.0s) to avoid blocking IPC pipe overhead.
4. **3D Adaptive Normalized Pinch**: Pinch distances are divided by the hand's reference size (3D Euclidean distance between wrist `0` and middle MCP `9`) and stabilized using Exponential Moving Average (`alpha = 0.4`), allowing distance-independent interaction up to 3 meters away.
5. **Mirrored Handedness Correction**: Corrects horizontal camera mirroring (`cv2.flip`) to ensure accurate left vs. right physical hand classification.
6. **Debug Monitor**: Set `DEBUG_MODE = True` at the top of the script to display live FPS, MediaPipe inference latency (ms), camera resolution, and normalized pinch metrics.

---

## 📂 Project Structure

```
gesture-volume-control/
├── main.py                  # Control Hub launcher with Mic/Speaker toggle
├── Gesture_Controll.py      # Mode 1: Continuous Pinch Gesture Control
├── Finger_controll.py       # Mode 2: Discrete Finger Counting Control
├── hand_landmarker.task     # MediaPipe Hand Landmarker model asset
├── requirements.txt         # Python dependencies
├── Finger_controll.ipynb    # Jupyter notebook (reference & prototyping)
├── Gesture_Controll.ipynb   # Jupyter notebook (reference & prototyping)
└── README.md                # Project documentation
```

---

## 💡 Why We Built This

Adjusting volume during calls, streams, or presentations often introduces unnecessary friction. **GestureVolume** provides a touch-free, distraction-free interface built entirely with open-source tools.

Key practical applications:
- 🧑‍🦽 **Accessibility**: Touchless control for individuals with motor impairments.
- 🎙️ **Content Creators & Streamers**: Quick microphone adjustments without breaking focus.
- 🏥 **Sterile Environments**: Hands-free device control in medical, culinary, or laboratory settings.
- 🤖 **Human-Computer Interaction**: Demonstrates responsive, real-time edge computer vision on consumer hardware.

---

## 👨‍💻 Author

**Gautam N Chipkar**  
B.E – Artificial Intelligence & Data Science  

[![GitHub](https://img.shields.io/badge/GitHub-gee--46-181717?logo=github)](https://github.com/gee-46)

---

## 📄 License

This project was developed as part of the **Infosys Springboard Batch A** internship program.

