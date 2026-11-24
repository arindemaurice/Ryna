' Double-click to launch RYNA silently with ZERO terminal window (stays on even when all terminals are closed)
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
sh.Run "pythonw.exe -m ai.orchestration.main", 0, False
