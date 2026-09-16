
# 🖐️ GestureVolume — Two-Hand Gesture Audio Control

> **Real-time, low-latency microphone and speaker volume control using hand gestures and computer vision.**

GestureVolume turns a standard webcam into a touch-free audio control interface. Using **MediaPipe**, **OpenCV**, and **Windows Core Audio**, the system detects hand gestures in real time and translates them into microphone or speaker volume adjustments.

---

## 📸 Overview

GestureVolume provides two gesture-based methods for controlling system audio:

- 🤏 **Continuous Pinch Control** — smoothly adjust volume using the distance between your thumb and index finger.
- ✋ **Finger Counting Control** — set volume directly by raising 0–5 fingers.
- 👐 **Two-Hand Interaction** — one hand controls volume while the other handles locking/unlocking.
- 🎙️ **Microphone & Speaker Control** — switch between input and output audio devices.
- ⚡ **Real-Time Processing** — optimized camera and gesture-processing pipeline for responsive interaction.

The goal is to create a practical **touch-free Human-Computer Interaction (HCI)** system using commonly available hardware.

---

## ✨ Features

### 👐 Two-Hand Gesture Control

The system assigns different responsibilities to each hand:

**Right Hand**
- Controls the audio volume.
- Supports continuous pinch control.
- Supports discrete finger-counting control.

**Left Hand**
- Acts as a volume lock/unlock controller.
- Raising the index finger toggles the volume lock.

### 🎙️ Dual Audio Target

The application can control:

- 🔊 **Speaker / System Output**
- 🎙️ **Microphone / System Input**

The target can be selected directly from the control hub.

### ⚡ Real-Time Performance

The application is designed to minimize interaction latency using:

- Threaded camera capture
- Cached audio endpoints
- Throttled GUI rendering
- Efficient MediaPipe inference
- Background frame acquisition

### 📏 Distance-Independent Pinch Detection

Pinch distance is normalized using the detected hand's size.

This allows gesture interaction to remain relatively consistent when the user's hand moves closer to or farther from the camera.

The normalized distance is also smoothed using an **Exponential Moving Average (EMA)** to reduce unwanted volume fluctuations.

### 📊 Debug & Monitoring HUD

An optional debug mode provides information such as:

- FPS
- MediaPipe inference latency
- Camera resolution
- Normalized pinch distance
- Hand detection status
- Volume state
- Lock/unlock status

---

# 🎮 Control Modes

GestureVolume provides two primary interaction modes.

| Mode | Right Hand | Left Hand |
|------|------------|-----------|
| 🤏 **Continuous Pinch** | Thumb + index distance controls volume continuously | Index finger toggles volume lock |
| ✋ **Finger Counting** | 0–5 fingers set volume directly | Index finger toggles volume lock |

---

## 🤏 Continuous Pinch Mode

The distance between the **thumb** and **index finger** determines the volume level.

- Fingers close together → lower volume
- Fingers farther apart → higher volume

### Example

```text
Pinch Distance
      ↓
Hand Landmark Processing
      ↓
Normalized Distance
      ↓
Smoothing / EMA
      ↓
Volume Mapping
      ↓
System Audio
````

Volume is mapped across the range:

```text
0% ───────────────────────────── 100%
```

---

## ✋ Finger Counting Mode

The number of raised fingers determines the volume.

| Fingers Raised | Volume |
| -------------: | -----: |
|              0 |     0% |
|              1 |    20% |
|              2 |    40% |
|              3 |    60% |
|              4 |    80% |
|              5 |   100% |

This mode is useful when discrete volume levels are preferred over continuous control.

---

## 🔒 Volume Lock

The **left hand** controls the volume lock.

Raise the **index finger** to toggle between:

```text
VOLUME UNLOCKED
        ↓
   Gesture Control
        ↓
   Desired Volume
        ↓
VOLUME LOCKED
```

When locked, right-hand gestures no longer modify the current volume.

---

# 🕹️ Controls

| Gesture / Input        | Action                       |
| ---------------------- | ---------------------------- |
| 🤏 Thumb + Index Pinch | Continuous volume adjustment |
| ✋ 0–5 Fingers          | Set volume to 0–100%         |
| ☝️ Left Index Finger   | Toggle volume lock/unlock    |
| 🎛️ Audio Target       | Switch microphone/speaker    |
| `Ctrl + Alt + Up`      | Increase volume by 2%        |
| `Ctrl + Alt + Down`    | Decrease volume by 2%        |
| `Q`                    | Quit active control window   |

---

# 🛠️ Tech Stack

### Computer Vision

* Python
* OpenCV
* MediaPipe Hand Landmarker

### Audio Control

* Pycaw
* Windows Core Audio APIs

### Interface & Visualization

* Tkinter
* Matplotlib

### Development

* Jupyter Notebook
* Git & GitHub

---

# ⚙️ Technical Architecture

```text
                ┌─────────────────┐
                │     Webcam      │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐
                │  OpenCV Capture  │
                └────────┬────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ MediaPipe Hand       │
              │ Landmark Detection   │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ Gesture Processing   │
              │                      │
              │ • Pinch Detection    │
              │ • Finger Counting    │
              │ • Handedness         │
              │ • Lock Detection     │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ Volume Mapping       │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ Windows Core Audio   │
              │        / Pycaw       │
              └──────────┬───────────┘
                         │
                         ▼
                  🔊 Speaker / 🎙️ Mic
```

---

# ⚡ Performance Optimizations

### 1. Threaded Camera Pipeline

Camera frames are captured using a background daemon thread rather than blocking the main application loop.

This helps prevent camera buffering from affecting gesture-processing responsiveness.

### 2. Decoupled Visualization

Matplotlib rendering is throttled independently from camera and gesture processing.

```text
Camera / Gesture Processing
        ↓
   High FPS Pipeline
        ↓
     Gesture Data
        ↓
  ┌───────────────┐
  │ Visualization │
  │   ~10 FPS     │
  └───────────────┘
```

This prevents expensive graph rendering from slowing down the main interaction loop.

### 3. Cached Audio Endpoints

Windows audio endpoints are cached locally instead of repeatedly querying the system.

Device state is periodically refreshed to reduce unnecessary COM communication.

### 4. Adaptive Pinch Normalization

Pinch distance is normalized relative to the detected hand size:

```text
Normalized Pinch Distance
=
Thumb ↔ Index Distance
────────────────────────
Reference Hand Size
```

The reference hand size is calculated using the distance between selected hand landmarks.

This makes the interaction less dependent on the user's distance from the camera.

### 5. EMA Smoothing

An Exponential Moving Average is applied to the normalized pinch distance:

```text
Smoothed Value =
α × Current Value
+
(1 - α) × Previous Value
```

The implementation uses:

```text
α = 0.4
```

This reduces sudden fluctuations caused by small landmark detection variations.

### 6. Handedness Correction

Because webcam frames are horizontally mirrored, the system accounts for camera mirroring when determining physical left/right hand assignment.

---

# 📂 Project Structure

```text
gesture-volume-control/
│
├── main.py
│   └── Control hub and audio target selection
│
├── Gesture_Controll.py
│   └── Continuous pinch-based volume control
│
├── Finger_controll.py
│   └── Finger-counting volume control
│
├── hand_landmarker.task
│   └── MediaPipe hand landmark model
│
├── requirements.txt
│   └── Python dependencies
│
├── Gesture_Controll.ipynb
│   └── Gesture control experimentation
│
├── Finger_controll.ipynb
│   └── Finger counting experimentation
│
└── README.md
```

---

# 🚀 Getting Started

## Requirements

* **Windows 10 / 11**
* **Python 3.8+**
* Webcam
* Working microphone and/or speaker

> Windows is required because the project uses Windows Core Audio APIs through `pycaw`.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/gee-46/gesture-volume-control.git
```

### 2. Navigate to the project

```bash
cd gesture-volume-control
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the application

```bash
python main.py
```

---

# 🖥️ Application Flow

When the application starts, the **Control Hub** allows you to select:

```text
┌─────────────────────────────┐
│       GestureVolume         │
│                             │
│      Gesture Control        │
│                             │
│     Finger Counting         │
│                             │
│   Volume Target: SPEAKER    │
└─────────────────────────────┘
```

From here you can choose the desired gesture mode and audio target.

---

# 💡 Use Cases

GestureVolume can be useful in scenarios where traditional physical controls are inconvenient.

### 🎙️ Content Creation

Adjust microphone levels while recording, streaming, or podcasting.

### 🧑‍💻 Video Conferencing

Control audio without reaching for keyboard or mouse controls.

### ♿ Accessibility

Provides an alternative interaction method for users who may have difficulty operating conventional controls.

### 🧪 Touch-Free Environments

Gesture-based interaction can be useful where minimizing physical contact with controls is desirable.

### 🤖 Human-Computer Interaction

Demonstrates how computer vision can be used to build natural, real-time interfaces.

---

# 🔬 What This Project Demonstrates

This project combines several practical concepts:

* Real-time computer vision
* Hand landmark detection
* Gesture recognition
* Coordinate normalization
* Signal smoothing
* Multithreaded camera processing
* Windows audio APIs
* Human-Computer Interaction
* Real-time visualization
* Performance optimization

Rather than relying on a physical controller, the system uses **vision-based interaction as the control layer**.

---

# 📸 Interface

### Control Hub

Select the gesture control mode and audio target.

![Control Hub](1.png)

### Continuous Pinch Mode

Control volume continuously using thumb and index finger distance.

![Pinch Gesture](2.png)

### Finger Counting Mode

Control volume using the number of raised fingers.

![Finger Counting](3.png)

---

# 👨‍💻 Contributors

* **Snehil Ghosh**
* **Gautam N Chipkar**
* **Amrutha Varshani**
* **Ayush Gorge**

---

# 📄 License

This project is available for educational and personal use.

```

### What I removed/changed

- Removed **all Infosys references**
- Removed **“Infosys Springboard Batch A”**
- Removed organization-specific wording
- Removed the internship-program framing
- Kept the **actual technical implementation**
- Kept all four contributors
- Made the README more like an **independent computer-vision project**
- Added a cleaner **architecture section**
- Added clearer explanations of the normalization, EMA, threading, and audio pipeline
- Removed unnecessary repetition and marketing-heavy wording

One thing I would also change in the GitHub repo itself: **`Gesture_Controll.py` → `gesture_control.py`** and **`Finger_controll.py` → `finger_control.py`**. The current naming works, but the lowercase `snake_case` convention looks more professional for a Python project.
```
