; Inno Setup script for the Kikoe installer.
;
; Compile from the repository root with the one-folder build unpacked in BundleDir:
;   iscc /DVersion=0.1.0 /DArch=x64 /DBundleDir=%CD%\build\Kikoe packaging\windows\kikoe.iss
; Arch is x64 or arm64; the installer is written to out\.

#ifndef Version
  #error Pass /DVersion=<version>
#endif
#ifndef Arch
  #error Pass /DArch=x64 or /DArch=arm64
#endif
#ifndef BundleDir
  #error Pass /DBundleDir=<one-folder build>
#endif

#if Arch == "arm64"
  #define Architectures "arm64"
#else
  #define Architectures "x64compatible"
#endif

[Setup]
; Never change the AppId: Windows uses it to find the installed version on upgrade.
AppId={{AA0F90AD-56C8-4D67-B66C-488B60527EAC}
AppName=Kikoe
AppVersion={#Version}
AppPublisher=first-storm
AppPublisherURL=https://github.com/first-storm/Kikoe
AppSupportURL=https://github.com/first-storm/Kikoe/issues
DefaultDirName={autopf}\Kikoe
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed={#Architectures}
ArchitecturesInstallIn64BitMode={#Architectures}
OutputDir=..\..\out
OutputBaseFilename=Kikoe-{#Version}-windows-{#Arch}-setup
UninstallDisplayIcon={app}\Kikoe.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Kikoe"; Filename: "{app}\Kikoe.exe"
Name: "{autodesktop}\Kikoe"; Filename: "{app}\Kikoe.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Kikoe.exe"; Description: "{cm:LaunchProgram,Kikoe}"; Flags: nowait postinstall skipifsilent
