' Uruchamia Claude AutoShutdown bez okna konsoli.
' Kazdy blad jest POKAZYWANY - cicha porazka launchera jest gorsza niz brak launchera.
Option Explicit

Dim sh, fso, baseDir, script, pythonw, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(baseDir, "autoshutdown.py")

If Not fso.FileExists(script) Then
    MsgBox "Nie znalazlem pliku:" & vbCrLf & script & vbCrLf & vbCrLf & _
           "Uruchom launcher z katalogu programu.", vbCritical, "Claude AutoShutdown"
    WScript.Quit 1
End If

' pythonw.exe = Python bez okna konsoli. Szukamy go obok python.exe z PATH.
pythonw = "pythonw.exe"
On Error Resume Next
sh.CurrentDirectory = baseDir
cmd = """" & pythonw & """ """ & script & """"
sh.Run cmd, 0, False
If Err.Number <> 0 Then
    MsgBox "Nie udalo sie uruchomic Pythona." & vbCrLf & vbCrLf & _
           "Komenda: " & cmd & vbCrLf & _
           "Blad: " & Err.Description & vbCrLf & vbCrLf & _
           "Sprawdz, czy Python jest w PATH (wpisz w terminalu: pythonw --version).", _
           vbCritical, "Claude AutoShutdown"
    WScript.Quit 1
End If
On Error GoTo 0
