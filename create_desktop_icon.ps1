# create_desktop_icon.ps1
# Automates the creation of a Windows Desktop shortcut for the OSRS Bloomberg Terminal.

$desktopPath = "c:\Users\londo\OneDrive\Desktop"
$shortcutPath = Join-Path $desktopPath "OSRS Bloomberg Terminal.lnk"

Write-Host "Creating desktop shortcut at: $shortcutPath"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = "c:\Users\londo\OneDrive\Desktop\osrs-ge-quant\run_suite.bat"
$Shortcut.WorkingDirectory = "c:\Users\londo\OneDrive\Desktop\osrs-ge-quant"
$Shortcut.Description = "OSRS Grand Exchange Bloomberg Terminal Quantitative Trading Suite"
# Use shell32.dll index 172 (gold shield/key lock) or 43 (gold star) to fit the OSRS gold aesthetic.
$Shortcut.IconLocation = "shell32.dll, 43"
$Shortcut.Save()

Write-Host "Shortcut created successfully!"
