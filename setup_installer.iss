; ╔══════════════════════════════════════════════════════════════════╗
; ║  TraderBot v4 — Inno Setup Installer Script                     ║
; ║  Requires: Inno Setup 6.x  (https://jrsoftware.org/isinfo.php) ║
; ║  Run AFTER build.bat has produced dist\TraderBotV4\             ║
; ╚══════════════════════════════════════════════════════════════════╝

#define AppName      "TraderBot v4"
#define AppVersion   "4.0.0"
#define AppPublisher "Radiar Kazemi"
#define AppURL       "https://github.com/radiarkazemi/tradingbot_v4"
#define AppExeName   "TraderBotV4.exe"
#define AppId        "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; AppPublisherURL={#AppURL}
; AppSupportURL={#AppURL}
; AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\TraderBotV4
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Require admin so we can install to Program Files and create shortcuts
PrivilegesRequired=admin
OutputDir=installer_output
OutputBaseFilename=TraderBotV4_Setup_v{#AppVersion}
; Place a 55×58 bmp as SetupIconFile or leave commented out
; SetupIconFile={src}\logo.ico   ; commented out — use default Inno Setup icon
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardResizable=yes
; Minimum Windows 10
MinVersion=10.0
; Show license page (create LICENSE.txt first)
; LicenseFile=LICENSE.txt
; Show readme after install
; InfoAfterFile=INSTALL_README.txt
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes
CloseApplicationsFilter=*.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenuicon";  Description: "Create Start Menu shortcut"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; All compiled bot files from PyInstaller output
Source: "dist\TraderBotV4\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "session_*.json,profile.json,*.log"

[Icons]
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";    Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
; Launch the app after install
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the user profile data on uninstall (optional — comment out to keep)
; Type: filesandordirs; Name: "{userappdata}\TraderBotV4"

[Code]
// ── Check MT5 is installed ───────────────────────────────────────
function IsMT5Installed(): Boolean;
var
  MT5Path: string;
begin
  Result := RegQueryStringValue(HKLM,
    'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\terminal64.exe',
    '', MT5Path);
  if not Result then
    Result := RegQueryStringValue(HKCU,
      'SOFTWARE\MetaQuotes\MetaTrader 5', 'InstallPath', MT5Path);
  if not Result then
    Result := FileExists(ExpandConstant('{pf}\MetaTrader 5\terminal64.exe'));
end;

procedure InitializeWizard();
begin
  // Nothing extra needed — setup wizard runs inside the app on first launch
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  // Warn if MT5 not detected (non-blocking — some brokers install to custom paths)
  if (CurPageID = wpReady) and not IsMT5Installed() then
  begin
    if MsgBox(
      'MetaTrader 5 was not detected on this machine.' + #13#10 + #13#10 +
      'TraderBot v4 requires MT5 to be installed and running.' + #13#10 +
      'Download it from your broker''s website.' + #13#10 + #13#10 +
      'Continue installation anyway?',
      mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;
