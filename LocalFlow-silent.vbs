' Launch LocalFlow with no console window (for everyday use / startup).
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Check if .venv exists
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FolderExists(".venv") Then
    MsgBox "Virtual environment (.venv) not found. Please run setup.bat first!", 16, "LocalFlow Error"
    WScript.Quit 1
End If

shell.Run ".venv\Scripts\pythonw.exe localflow.py", 0, False
