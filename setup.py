import subprocess
import sys
import os
import shutil
import re

def has_nvidia_gpu():
    try:
        # Check if nvidia-smi is in PATH and works
        res = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        # Check standard Windows directories
        for path in [
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
        ]:
            if os.path.exists(path):
                return True
        return False

def install_packages(packages):
    print(f"\n[Setup] Installing dependencies: {', '.join(packages)}")
    cmd = [sys.executable, "-m", "pip", "install", "-U"] + packages
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"[ERROR] Failed to install packages: {packages}")
        sys.exit(1)

def get_hardware_profile():
    # 1. Check System RAM
    ram_gb = 8.0  # default fallback
    try:
        res = subprocess.run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"], 
                             capture_output=True, text=True, check=True)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line and line.isdigit():
                ram_gb = int(line) / (1024 ** 3)
                break
    except Exception:
        pass
        
    # 2. Check GPU & VRAM
    has_gpu = False
    vram_mb = 0
    try:
        gpu_ok = False
        try:
            res = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            gpu_ok = (res.returncode == 0)
        except Exception:
            pass
        if not gpu_ok:
            for path in [
                r"C:\Windows\System32\nvidia-smi.exe",
                r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
            ]:
                if os.path.exists(path):
                    gpu_ok = True
                    break
        if gpu_ok:
            res = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, check=True)
            for line in res.stdout.splitlines():
                line = line.strip()
                if line and line.isdigit():
                    vram_mb = int(line)
                    has_gpu = True
                    break
    except Exception:
        pass

    # Profile logic:
    if has_gpu:
        if vram_mb >= 5500:  # ~6GB VRAM
            return {
                "name": "High-End / Modern GPU (>= 6GB VRAM)",
                "whisper_model": "distil-whisper/distil-large-v3.5-ct2",
                "whisper_compute": "int8_float16",
                "ollama_model": "qwen2.5:3b",
                "has_gpu": True,
                "ram_gb": ram_gb,
                "vram_gb": vram_mb / 1024.0,
                "msg": "Perfect fit. Both models will run with full GPU acceleration."
            }
        elif vram_mb >= 3500:  # ~4GB VRAM
            return {
                "name": "Mid-Range GPU (~4GB VRAM)",
                "whisper_model": "distil-whisper/distil-large-v3.5-ct2",
                "whisper_compute": "int8",
                "ollama_model": "qwen2.5:1.5b",
                "has_gpu": True,
                "ram_gb": ram_gb,
                "vram_gb": vram_mb / 1024.0,
                "msg": "Optimized for 4GB GPU VRAM. Whisper runs in 8-bit quantization and Ollama uses a 1.5B model to prevent VRAM overflow."
            }
        else:  # < 4GB VRAM
            return {
                "name": "Low-VRAM GPU (< 4GB VRAM)",
                "whisper_model": "small.en",
                "whisper_compute": "int8",
                "ollama_model": "qwen2.5:0.5b",
                "has_gpu": True,
                "ram_gb": ram_gb,
                "vram_gb": vram_mb / 1024.0,
                "msg": "Low VRAM detected. Running lightweight models to fit within GPU limits."
            }
    else:
        whisper_m = "small.en" if ram_gb >= 8.0 else "tiny.en"
        return {
            "name": "CPU-Only Mode (No NVIDIA GPU)",
            "whisper_model": whisper_m,
            "whisper_compute": "int8",
            "ollama_model": "qwen2.5:1.5b" if ram_gb >= 8.0 else "qwen2.5:0.5b",
            "has_gpu": False,
            "ram_gb": ram_gb,
            "vram_gb": 0,
            "msg": "CPU fallback mode. Lightweight models configured for fast CPU inference."
        }

def update_localflow_config(whisper_model, whisper_compute, ollama_model):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    lf_path = os.path.join(script_dir, "localflow.py")
    if not os.path.exists(lf_path):
        print(f"[WARNING] localflow.py not found at {lf_path}. Skipping config update.")
        return
        
    try:
        with open(lf_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        content, n1 = re.subn(r'(WHISPER_MODEL\s*=\s*)["\'][^"\']+["\']', rf'\1"{whisper_model}"', content)
        content, n2 = re.subn(r'(WHISPER_COMPUTE\s*=\s*)["\'][^"\']+["\']', rf'\1"{whisper_compute}"', content)
        content, n3 = re.subn(r'(OLLAMA_MODEL\s*=\s*)["\'][^"\']+["\']', rf'\1"{ollama_model}"', content)
        
        if n1 or n2 or n3:
            with open(lf_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[Setup] Updated localflow.py configuration:")
            print(f"  - WHISPER_MODEL = \"{whisper_model}\"")
            print(f"  - WHISPER_COMPUTE = \"{whisper_compute}\"")
            print(f"  - OLLAMA_MODEL = \"{ollama_model}\"")
        else:
            print("[WARNING] Could not locate configuration lines in localflow.py to update.")
    except Exception as e:
        print(f"[WARNING] Failed to update localflow.py configuration: {e}")

def setup_ollama(ollama_model):
    ollama_bin = shutil.which("ollama")
    if ollama_bin:
        print(f"\n[Setup] Ollama detected. Pulling cleanup model {ollama_model}...")
        try:
            subprocess.run(["ollama", "pull", ollama_model])
        except Exception as e:
            print(f"[WARNING] Could not automatically pull {ollama_model}: {e}")
    else:
        print("\n" + "="*70)
        print("[OLLAMA INFO]")
        print("Ollama was not found on your system PATH.")
        print("To enable high-quality transcript formatting and cleanup, please:")
        print("  1. Download and install Ollama from: https://ollama.com")
        print(f"  2. Run the command: ollama pull {ollama_model}")
        print("="*70 + "\n")

def save_ico_file():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(script_dir, "localflow.ico")
    
    print("\n[Setup] Generating application icon (localflow.ico)...")
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        # Create a dummy app to initialize Qt GUI subsystem safely
        _qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        
        pm = QtGui.QPixmap(64, 64)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setBrush(QtGui.QColor(20, 20, 32))
        p.setPen(QtGui.QPen(QtGui.QColor(48, 48, 74), 2))
        p.drawRoundedRect(5, 5, 54, 54, 16, 16)
        p.setBrush(QtGui.QColor(126, 132, 170))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(26, 13, 12, 24, 6, 6)
        pen = QtGui.QPen(QtGui.QColor(126, 132, 170), 3)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(20, 22, 24, 22, 180 * 16, 180 * 16)
        p.drawLine(32, 44, 32, 50)
        p.drawLine(25, 51, 39, 51)
        p.end()
        pm.save(ico_path, "ICO")
        print(f"[Setup] Generated icon file successfully at:\n  {ico_path}")
    except Exception as e:
        print(f"[WARNING] Failed to generate localflow.ico: {e}")
    return ico_path

def setup_startup_shortcut(ico_path):
    print("\nWould you like LocalFlow to start automatically on Windows boot? (y/n)")
    try:
        choice = input("> ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        choice = 'n'
        
    if choice == 'y':
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            vbs_path = os.path.join(script_dir, "LocalFlow-silent.vbs")
            startup_dir = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
            lnk_path = os.path.join(startup_dir, "LocalFlow.lnk")
            
            # PowerShell command to create a Windows shortcut (.lnk) with custom icon
            ps_cmd = (
                f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk_path}');"
                f"$s.TargetPath='{vbs_path}';"
                f"$s.WorkingDirectory='{script_dir}';"
                f"$s.IconLocation='{ico_path}';"
                f"$s.Save()"
            )
            subprocess.run(["powershell", "-Command", ps_cmd], check=True, stdout=subprocess.DEVNULL)
            print(f"[Setup] Autostart shortcut successfully created at:\n  {lnk_path}")
        except Exception as e:
            print(f"[WARNING] Failed to create autostart shortcut: {e}")
    else:
        print("[Setup] Skipped autostart shortcut configuration.")

def setup_start_menu_shortcut(ico_path):
    print("\n[Setup] Creating Start Menu shortcut...")
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        vbs_path = os.path.join(script_dir, "LocalFlow-silent.vbs")
        programs_dir = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")
        lnk_path = os.path.join(programs_dir, "LocalFlow.lnk")
        
        # PowerShell command to create a Windows shortcut (.lnk) with custom icon
        ps_cmd = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk_path}');"
            f"$s.TargetPath='{vbs_path}';"
            f"$s.WorkingDirectory='{script_dir}';"
            f"$s.IconLocation='{ico_path}';"
            f"$s.Save()"
        )
        subprocess.run(["powershell", "-Command", ps_cmd], check=True, stdout=subprocess.DEVNULL)
        print(f"[Setup] Start Menu shortcut successfully created at:\n  {lnk_path}")
    except Exception as e:
        print(f"[WARNING] Failed to create Start Menu shortcut: {e}")

def main():
    print("[Setup] Beginning LocalFlow dependency installation...")
    
    # Detect hardware profile
    profile = get_hardware_profile()
    
    print("\n" + "="*70)
    print(f"[Hardware Detection]")
    print(f"  - System RAM: {profile['ram_gb']:.2f} GB")
    if profile['has_gpu']:
        print(f"  - NVIDIA GPU: Yes ({profile['vram_gb']:.2f} GB VRAM)")
    else:
        print(f"  - NVIDIA GPU: No")
    print(f"  - Assigned Profile: {profile['name']}")
    print(f"  - Status: {profile['msg']}")
    print("="*70 + "\n")

    # Define core requirements
    reqs = [
        "numpy",
        "requests",
        "sounddevice",
        "pyperclip",
        "pynput",
        "faster-whisper",
        "PySide6"
    ]
    
    if profile['has_gpu']:
        print("[Setup] NVIDIA GPU detected. Adding CUDA/cuDNN packages for GPU acceleration...")
        reqs.extend(["nvidia-cublas-cu12", "nvidia-cudnn-cu12"])
    else:
        print("[Setup] No NVIDIA GPU detected. Falling back to CPU execution.")
        
    # Install all packages
    install_packages(reqs)
    
    # Update localflow.py configuration based on the detected hardware profile
    update_localflow_config(profile['whisper_model'], profile['whisper_compute'], profile['ollama_model'])
    
    # Setup Ollama model
    setup_ollama(profile['ollama_model'])
    
    # Generate custom ICO file matching tray icon
    ico_path = save_ico_file()
    
    # Create Start Menu shortcut
    setup_start_menu_shortcut(ico_path)
    
    # Create startup shortcut
    setup_startup_shortcut(ico_path)
    
    print("\n[Setup] Dependency setup helper completed successfully!")

if __name__ == "__main__":
    main()
