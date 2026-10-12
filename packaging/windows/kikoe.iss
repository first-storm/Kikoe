; Pass AppId, AppVersion and AppArch from scripts/package.py / the build workflow.
[Setup]
AppId={#AppId}
AppName=Kikoe
AppVersion={#AppVersion}
AppPublisher=first-storm
AppPublisherURL=https://github.com/first-storm/Kikoe
DefaultDirName={localappdata}\Programs\Kikoe
DefaultGroupName=Kikoe
PrivilegesRequired=lowest
ArchitecturesAllowed={#AppArch}
ArchitecturesInstallIn64BitMode={#AppArch}
OutputDir=..\..\dist
OutputBaseFilename=Kikoe-{#AppVersion}-windows-{#AppArch}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\kikoe.exe
LicenseFile=..\..\LICENSE

[Files]
Source: "..\..\dist\kikoe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Kikoe"; Filename: "{app}\kikoe.exe"
Name: "{autodesktop}\Kikoe"; Filename: "{app}\kikoe.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\kikoe.exe"; Description: "Launch Kikoe"; Flags: nowait postinstall skipifsilent
