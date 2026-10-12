' ==============================================================================
' scripts/launch_silent.vbs
' Launches Focus Engine and Cloudflare Tunnel completely hidden without terminal windows.
' Window style 0 = Hidden, bWaitOnReturn = False.
' ==============================================================================
Option Explicit

Dim WshShell, fso, scriptDir, batPath

Set WshShell = WScript.CreateObject("WScript.Shell")
Set fso = WScript.CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = scriptDir & "\start_engine.bat"

If Not fso.FileExists(batPath) Then
    batPath = "D:\new_p\focus_engine\scripts\start_engine.bat"
End If

WshShell.CurrentDirectory = "D:\new_p\focus_engine"

' Run start_engine.bat through cmd.exe /c completely hidden (0)
WshShell.Run "cmd.exe /c """ & batPath & """", 0, False

Set WshShell = Nothing
Set fso = Nothing
