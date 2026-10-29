#define MyAppName "SB Harness"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "Erick114514"
#define MyAppExeName "SBHarness-Standalone.exe"
#define RepoRoot SourcePath + "..\"

[Setup]
AppId={{B9E4AF17-7AB2-4C92-8DA9-4F39C1EA5D71}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\SB Harness
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#RepoRoot}dist
OutputBaseFilename=SBHarness-Setup-v{#MyAppVersion}
SetupIconFile={#RepoRoot}assets\SBHarness.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile={#RepoRoot}LICENSE
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
Uninstallable=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#RepoRoot}dist\SBHarness-Standalone.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\{#MyAppName}\使用说明"; Filename: "{app}\README.md"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch SB Harness"; Flags: postinstall nowait skipifsilent
