param(
    [switch]$Remove,
    [switch]$Shortcut
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    if ($Remove) {
        python -c "from app.desktop.autostart import disable_autostart; removed = disable_autostart(); print('Removed RYNA auto-start.' if removed else 'RYNA auto-start was not enabled.')"
    } else {
        python -c "from pathlib import Path; from app.desktop.autostart import enable_autostart; p = enable_autostart(Path.cwd()); print('Enabled RYNA auto-start with Windows:', p)"
    }
    if ($Shortcut) {
        python -c "from pathlib import Path; from app.desktop.autostart import create_desktop_shortcut; p = create_desktop_shortcut(Path.cwd()); print('Created Desktop shortcut:', p)"
    }
} finally { Pop-Location }
