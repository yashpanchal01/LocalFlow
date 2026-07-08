import subprocess
import sys
import os
import shutil

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

def setup_ollama():
    ollama_bin = shutil.which("ollama")
    if ollama_bin:
        print("\n[Setup] Ollama detected. Pulling cleanup model qwen2.5:3b...")
        try:
            # Run ollama pull and stream output to console
            subprocess.run(["ollama", "pull", "qwen2.5:3b"])
        except Exception as e:
            print(f"[WARNING] Could not automatically pull qwen2.5:3b: {e}")
    else:
        print("\n" + "="*70)
        print("[OLLAMA INFO]")
        print("Ollama was not found on your system PATH.")
        print("To enable high-quality transcript formatting and cleanup, please:")
        print("  1. Download and install Ollama from: https://ollama.com")
        print("  2. Run the command: ollama pull qwen2.5:3b")
        print("="*70 + "\n")

def save_ico_file():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(script_dir, "localflow.ico")
    png_path = os.path.join(script_dir, "docs", "aurora_mono_brushed_icon.png")
    
    print(f"\n[Setup] Converting {png_path} to localflow.ico...")
    try:
        from PySide6 import QtGui, QtWidgets
        # Create a dummy app to initialize Qt GUI subsystem safely
        _qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        
        pm = QtGui.QPixmap(png_path)
        if pm.isNull():
            raise ValueError(f"Could not load PNG image: {png_path}")
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
    
    # GPU detection
    gpu_available = has_nvidia_gpu()
    if gpu_available:
        print("[Setup] NVIDIA GPU detected. Adding CUDA/cuDNN packages for GPU acceleration...")
        reqs.extend(["nvidia-cublas-cu12", "nvidia-cudnn-cu12"])
    else:
        print("[Setup] No NVIDIA GPU detected. Falling back to CPU execution.")
        
    # Install all packages
    install_packages(reqs)
    
    # Setup Ollama model
    setup_ollama()
    
    # Generate custom ICO file matching tray icon
    ico_path = save_ico_file()
    
    # Create Start Menu shortcut
    setup_start_menu_shortcut(ico_path)
    
    # Create startup shortcut
    setup_startup_shortcut(ico_path)
    
    print("\n[Setup] Dependency setup helper completed successfully!")

if __name__ == "__main__":
    main()
