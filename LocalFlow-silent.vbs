' Launch LocalFlow with no console window (for everyday use / startup).
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "pyw -3.13 localflow.py", 0, False
