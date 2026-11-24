; RYNA AI Desktop Assistant - Official Windows Installer Script
; Copyright (c) 2026 Arinde Maurice. All rights reserved.

#define MyAppName "RYNA AI Desktop Assistant"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Arinde Maurice"
#define MyAppExeName "RYNA.exe"

[Setup]
AppId={{8D6B4F1C-2A7E-4D9A-B10E-9F53A82471C0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright (c) 2026 Arinde Maurice. All rights reserved.
VersionInfoVersion=1.0.0.0
VersionInfoCompany=Arinde Maurice
VersionInfoDescription=RYNA AI Desktop Assistant Setup
VersionInfoProductName=RYNA AI Desktop Assistant
VersionInfoCopyright=Copyright (c) 2026 Arinde Maurice
DefaultDirName={localappdata}\RYNA
DefaultGroupName=RYNA
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE
SetupIconFile=..\app\desktop\assets\ryna_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist
OutputBaseFilename=RYNA-Setup-1.0.0
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a RYNA shortcut on your Desktop"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start RYNA automatically with Windows (always-on desktop companion)"; GroupDescription: "Always-On Behavior:"
Name: "installollama"; Description: "Install Ollama and fast offline AI model (qwen2.5:1.5b)"; GroupDescription: "AI Engine & Offline Voice:"
Name: "trustcert"; Description: "Install Arinde Maurice code-signing certificate (Current User)"; GroupDescription: "Security & Verification:"

[Files]
Source: "..\dist\RYNA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\certs\ArindeMaurice-CodeSign.cer"; DestDir: "{app}\certs"; Flags: ignoreversion
Source: "..\packaging\post_install_setup.ps1"; DestDir: "{app}\packaging"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\RYNA"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "RYNA AI Desktop Assistant by Arinde Maurice"
Name: "{group}\Uninstall RYNA"; Filename: "{uninstallexe}"
Name: "{autodesktop}\RYNA"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "RYNA AI Desktop Assistant by Arinde Maurice"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\post_install_setup.ps1"" -InstallDir ""{app}"" -InstallOllama -TrustCert -EnableAutostart"; StatusMsg: "Setting up Ollama, offline AI model (qwen2.5:1.5b), and Windows startup..."; Tasks: installollama autostart trustcert; Flags: runascurrentuser
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\post_install_setup.ps1"" -InstallDir ""{app}"" -InstallOllama -EnableAutostart"; StatusMsg: "Setting up Ollama, offline AI model (qwen2.5:1.5b), and Windows startup..."; Tasks: installollama autostart and not trustcert; Flags: runascurrentuser
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\post_install_setup.ps1"" -InstallDir ""{app}"" -InstallOllama -TrustCert"; StatusMsg: "Setting up Ollama and offline AI model (qwen2.5:1.5b)..."; Tasks: installollama trustcert and not autostart; Flags: runascurrentuser
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\post_install_setup.ps1"" -InstallDir ""{app}"" -InstallOllama"; StatusMsg: "Setting up Ollama and offline AI model (qwen2.5:1.5b)..."; Tasks: installollama and not autostart and not trustcert; Flags: runascurrentuser
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\post_install_setup.ps1"" -InstallDir ""{app}"" -TrustCert -EnableAutostart"; StatusMsg: "Configuring RYNA certificate and Windows startup..."; Tasks: autostart trustcert and not installollama; Flags: runascurrentuser
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\post_install_setup.ps1"" -InstallDir ""{app}"" -EnableAutostart"; StatusMsg: "Configuring RYNA Windows startup..."; Tasks: autostart and not installollama and not trustcert; Flags: runascurrentuser
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\post_install_setup.ps1"" -InstallDir ""{app}"" -TrustCert"; StatusMsg: "Installing Arinde Maurice publisher certificate..."; Tasks: trustcert and not installollama and not autostart; Flags: runascurrentuser
Filename: "{app}\{#MyAppExeName}"; Description: "Launch RYNA now"; Flags: nowait postinstall skipifsilent
