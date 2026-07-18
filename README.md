# LocalFlow 🌊

A system-wide, fully local voice dictation tool for Windows. Dictate in any text field, anywhere, with real-time feedback and AI-powered transcript cleaning — all powered on-device by Whisper and Ollama.

---

## 🌟 Key Features

- **Dual Dictation Modes:**
  - 🔑 **Tap-to-Talk (`Ctrl + Alt + /`):** Tap once to start dictating, tap again to stop and paste. Long-press (600ms) while recording to cancel.
  - 🗣️ **Push-to-Talk (`Tab`):** Hold to talk; recording captures audio as long as the key is physically held, and immediately processes and pastes upon release.
- **Escape to Cancel (`Esc`):** Easily discard the current dictation mid-speech without pasting.
- **AI-Powered Cleanup:** Utilizes local Ollama models (like Qwen2.5) to automatically remove filler words (*um, uh, like*), fix punctuation and capitalization, and resolve natural self-corrections (e.g., *"Monday... no wait, Tuesday"* -> *"Tuesday"*). The cleanup is strictly verbatim-preserving: a guard compares the polished text against the raw transcript, and any rewrite that drops sentences, shaves the opening words, or answers your dictation as if it were a question is rejected in favor of your original words.
- **Developer-Tuned:** Whisper is biased toward programming vocabulary (Git, Django, TypeScript, MCP, regex, ...) and the polish step preserves technical terms and formats spoken code — *"get user data in api dot py returns none"* becomes `` the function `get_user_data` in `api.py` returns `None` ``.
- **Fallback Cleaners:** If Ollama is down, fallback regex logic cleans basic fillers so you never lose your transcription — and dictations paste instantly instead of waiting on a dead connection. LocalFlow keeps probing in the background and re-enables AI cleanup automatically the moment Ollama comes back.
- **Glassmorphic UI Overlay:** A sleek, animated dark-glass pill at the bottom of the screen displays real-time transcription progress, audio volume visualizers, and state signals (● listening / ● polishing).
- **Persistent Dictionary:** Customize Whisper's vocabulary by adding jargon, names, or code syntax to [dictionary.txt](file:///c:/Users/YASH%20PANCHAL/localflow/dictionary.txt) — updates are live-loaded on the next dictation without restarting the app.

---

## 💻 Hardware Profiles & Requirements

During setup, LocalFlow detects your system's hardware configuration (RAM & GPU VRAM) and automatically applies the optimal profile in [localflow.py](file:///c:/Users/YASH%20PANCHAL/localflow/localflow.py):

| Profile | Hardware Spec | Whisper Model | Ollama Model | Description |
| :--- | :--- | :--- | :--- | :--- |
| **High-End GPU** | NVIDIA GPU (>= 6GB VRAM) | `distil-whisper/distil-large-v3.5-ct2` (int8_float16) | `qwen2.5:3b` | Ultimate accuracy and speed; both models run fully GPU-accelerated. |
| **Mid-Range GPU** | NVIDIA GPU (~4GB VRAM) | `distil-whisper/distil-large-v3.5-ct2` (int8) | `qwen2.5:1.5b` | Optimized footprint to fit within 4GB VRAM limits without overflow. |
| **Low-VRAM GPU** | NVIDIA GPU (< 4GB VRAM) | `small.en` (int8) | `qwen2.5:0.5b` | Lightweight model configurations suited for legacy or low-memory GPUs. |
| **CPU Only** | No NVIDIA GPU | `small.en` / `tiny.en` (int8) | `qwen2.5:1.5b` / `0.5b` | CPU fallback mode utilizing highly optimized, CPU-friendly model quantizations. |

---

## 🚀 Installation & Setup

LocalFlow is designed for Windows 10/11. Follow these simple steps to set it up:

### 1. Prerequisites
- **Python 3.10+**: Ensure Python is installed and check **"Add Python to PATH"** during installation.
- **Ollama (Optional but Recommended)**: Download and install Ollama from [ollama.com](https://ollama.com). Running the desktop client or background service allows the setup script to automatically pull your profile's cleanup LLM.
- **Windows Developer Mode (Optional)**: If you plan to use HuggingFace symlinks or download custom models without warnings, turning on Developer Mode in Windows Settings is recommended.

### 2. Download the Repository
Open a terminal (Command Prompt, PowerShell, or Git Bash) and run:
```cmd
git clone https://github.com/yashpanchal01/LocalFlow.git
cd LocalFlow
```

### 3. Run the Installer
Run the setup script by double-clicking [setup.bat](file:///c:/Users/YASH%20PANCHAL/localflow/setup.bat) in your file explorer, or running the following in your terminal:
```cmd
setup.bat
```
This installer will automatically:
1. Detect your CPU, RAM, and GPU specs.
2. Create a Python virtual environment (`.venv`) and upgrade `pip`.
3. Install dependencies including CUDA/cuDNN bindings if an NVIDIA GPU is detected.
4. Auto-configure the optimal model settings directly inside [localflow.py](file:///c:/Users/YASH%20PANCHAL/localflow/localflow.py).
5. Auto-pull the correct cleanup model from Ollama.
6. Generate a custom application icon ([localflow.ico](file:///c:/Users/YASH%20PANCHAL/localflow/localflow.ico)).
7. Create shortcuts in your **Start Menu** and ask if you'd like to add LocalFlow to your **Windows Startup** folder.

---

## 🏃 Running LocalFlow

There are two ways to run the application:

- 🪟 **Debug/Console Mode (`LocalFlow.bat`):** Launches LocalFlow with a terminal window. This is highly recommended to monitor logs and status messages.
- 🥷 **Silent Background Mode (`LocalFlow-silent.vbs`):** Launches the app silently in the background. It will place a microphone icon in the system tray, from which you can quit the app.
- 🔄 **Autostart:** To run it automatically when Windows boots, you can accept the shortcut option during installation, or manually place a shortcut of `LocalFlow-silent.vbs` into `shell:startup` (Win+R -> type `shell:startup` -> press Enter).

---

## 🛠️ Configuration & Customization

You can customize LocalFlow's behavior in two ways:

### 📖 Custom Dictionary
Edits to [dictionary.txt](file:///c:/Users/YASH%20PANCHAL/localflow/dictionary.txt) (created after first run) take effect **immediately** on the next dictation. Add one word, name, command, or technical term per line. Lines beginning with `#` are ignored.

### ⚙️ Setting Tweaks
All other configurations sit at the top of [localflow.py](file:///c:/Users/YASH%20PANCHAL/localflow/localflow.py). You can manually edit these:
- `OLLAMA_URL`: Default is `"http://127.0.0.1:11434"`.
- `CLEANUP_PROMPT`: The instructions given to the LLM for editing transcriptions.
- `PTT_VK`: The Windows virtual keycode for Push-to-Talk (default is `0x09` for `Tab`).
- `HOTKEY_VK`: The keycode for Tap-to-Talk (default is `0xBF` for `/`).
