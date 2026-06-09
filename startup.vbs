' Stet startup launcher for Windows
' This VBScript wrapper ensures:
'   1. Working directory is set to the project root
'   2. The correct executable is launched (frozen build or source)
'   3. No console window flashes at boot
'
' Used by: Task Scheduler (Stet Startup) or registry Run key.
' NOTE: This script is path-agnostic — it uses WScript.ScriptFullName
'       so it survives folder renames without edits.

Dim fso, scriptDir, shell
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' scriptDir = directory containing this .vbs file = project root
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

' Detect frozen/compiled build (Stet.exe exists next to this script)
Dim exePath
exePath = scriptDir & "\Stet.exe"

If fso.FileExists(exePath) Then
    ' Frozen build: run Stet.exe directly
    ' 0 = hidden window, False = don't wait for completion
    shell.Run """" & exePath & """", 0, False
Else
    ' Source build: run pythonw.exe with main.py
    Dim pythonw, mainPy
    pythonw = scriptDir & "\venv\Scripts\pythonw.exe"
    mainPy = scriptDir & "\main.py"

    ' Fallback: if venv doesn't have pythonw, try python
    If Not fso.FileExists(pythonw) Then
        pythonw = scriptDir & "\venv\Scripts\python.exe"
    End If

    If Not fso.FileExists(pythonw) Then
        ' Last resort: system Python
        pythonw = "pythonw.exe"
    End If

    shell.Run """" & pythonw & """ """ & mainPy & """", 0, False
End If
