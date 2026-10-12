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

Dim cmdStr
cmdStr = "%comspec% /c """ & Chr(34) & batPath & Chr(34) & """"
WshShell.Run cmdStr, 0, False

Set WshShell = Nothing
Set fso = Nothing
