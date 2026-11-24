' Double-click to launch RYNA with ZERO terminal window (pythonw.exe has no console; flag 1 ensures RYNA's robot window shows!)
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
sh.Run "pythonw.exe -m ai.orchestration.main", 1, False
