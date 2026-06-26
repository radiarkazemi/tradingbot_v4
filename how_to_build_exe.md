# TraderBot v4 — How to Build the EXE Installer

Step-by-step guide to produce a single-file Windows installer
(`TraderBotV4_Setup_v4.0.0.exe`) from the source code.

---

## Prerequisites (one-time setup)

### 1. Python 3.10 or 3.11 (64-bit)
Download from https://www.python.org/downloads/  
During install: ✅ **Add Python to PATH**

### 2. MetaTrader 5 Python package + dependencies
Open a Command Prompt and run:
```
pip install MetaTrader5 PyQt5 matplotlib pyinstaller
```

### 3. Inno Setup 6 (free installer builder)
Download from https://jrsoftware.org/isinfo.php  
Install with default options.

### 4. (Optional) An `.ico` icon file
Place `icon.ico` in the project root folder before building.  
If you skip this, remove the `icon='icon.ico'` line from `traderbotv4.spec`.

---

## Build Steps

### Step 1 — Compile the EXE

Double-click **`build.bat`** in the project folder, or from Command Prompt:

```cmd
cd C:\path\to\traderbotv4
build.bat
```

This will:
1. Install/upgrade PyInstaller and all requirements
2. Clean any previous build
3. Run PyInstaller using `traderbotv4.spec`
4. Output the compiled app to `dist\TraderBotV4\`

> **Takes 1–3 minutes** on first run. Subsequent builds are faster.

If the build fails, check the error message — most common issues:
- Missing `icon.ico` → remove `icon='icon.ico'` from the spec file
- Missing package → run `pip install <package>` and retry

---

### Step 2 — Create the Installer

1. Open **Inno Setup Compiler** (installed in Step 0)
2. File → Open → select **`setup_installer.iss`**
3. Before building, edit these lines at the top of the file:
   ```
   #define AppVersion   "4.0.0"          ← your version number
   #define AppPublisher "Your Name"       ← your name / company
   #define AppURL       "https://..."     ← your website (optional)
   ```
4. Click **Build → Compile** (or press F9)
5. The installer appears in **`installer_output\TraderBotV4_Setup_v4.0.0.exe`**

---

## What the Installer Does

When a user runs `TraderBotV4_Setup_v4.0.0.exe`:

1. **Welcome screen** — version info, publisher
2. **MT5 check** — warns if MetaTrader 5 is not detected (non-blocking)
3. **Install location** — defaults to `C:\Program Files\TraderBotV4\`
4. **Shortcuts** — Start Menu (always) + Desktop (optional)
5. **Launch** — option to launch TraderBot immediately after install

On first launch, **TraderBot shows the Setup Wizard automatically**:
- Step 1: Welcome
- Step 2: MT5 credentials (login, password, server)
- Step 3: Default symbol and lot preferences
- Step 4: Done → opens the main app

Credentials are saved to `%APPDATA%\TraderBotV4\profile.json`  
(obfuscated — not plain text).

The user can update credentials any time via **⚙ Account & Settings**
in the main app.

---

## File Structure After Build

```
dist/
  TraderBotV4/
    TraderBotV4.exe        ← main executable
    _internal/             ← Python runtime + all dependencies
    ...

installer_output/
  TraderBotV4_Setup_v4.0.0.exe  ← distributable installer
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: MetaTrader5` | MT5 Python API only works on Windows. Build on Windows. |
| App starts but can't connect to MT5 | MT5 terminal must be running and logged in |
| `icon.ico not found` | Remove `icon='icon.ico'` from spec, or add a .ico file |
| Antivirus flags the EXE | False positive — common with PyInstaller. Add exception or sign the EXE. |
| DLL errors at runtime | Install [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) |

---

## Publishing an Update (what you do every release)

When you fix a bug or add a feature, this is the **entire update process**:

### Step 1 — Bump the version number in two places

**`core/updater.py`** line 4:
```python
APP_VERSION = "4.1.0"   # ← change this
```

**`setup_installer.iss`** line 3:
```
#define AppVersion   "4.1.0"   ← change this to match
```

### Step 2 — Build the new installer
```
build.bat
```
Then compile `setup_installer.iss` in Inno Setup → get `TraderBotV4_Setup_v4.1.0.exe`

### Step 3 — Upload the installer to your server

Upload the `.exe` anywhere publicly accessible. Good free options:
- **GitHub Releases** (recommended — free, reliable, fast CDN)
- Your own website
- Google Drive (get a direct download link)

### Step 4 — Update `version.json` on your server

Edit this file and upload it to the same place you set `VERSION_CHECK_URL`:

```json
{
  "version": "4.1.0",
  "download_url": "https://github.com/YOU/REPO/releases/download/v4.1.0/TraderBotV4_Setup_v4.1.0.exe",
  "release_notes": "Fixed gap fill, improved lot scaling",
  "min_version": "4.0.0"
}
```

**That's it.** Within seconds, every running copy of the bot will:
1. Detect the newer version
2. Show a green **"🆕 v4.1.0 available"** banner in the left panel
3. When the user clicks **"Update Now"**:
   - Downloads the installer in the background with a progress bar
   - Stops the bot cleanly
   - Launches the installer silently
   - The installer replaces the old version and relaunches
   - **User never leaves the app** to find a download link

### Setting up GitHub Releases (recommended, free)

1. Create a GitHub account at github.com
2. Create a **new repository** — can be private or public
3. Go to **Settings → Pages** or use Releases
4. For `version.json`: put it in the repo root, use the raw URL:
   `https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/version.json`
5. For installer files: use **GitHub Releases** (Releases tab → Draft new release → attach the .exe)
   Direct download URL format: `https://github.com/USER/REPO/releases/download/v4.1.0/TraderBotV4_Setup_v4.1.0.exe`
6. Set `VERSION_CHECK_URL` in `core/updater.py` to your `version.json` raw URL

### Version numbering convention

Use **MAJOR.MINOR.PATCH**:
- `4.0.1` — bug fix only
- `4.1.0` — new feature
- `5.0.0` — major rewrite / breaking change