# Puts "zwiftbridge" in the Start Menu and on the Desktop.
#
# The Windows twin of install_app.sh. Like that one, the shortcut is a
# launcher, not a copy: it runs the code in THIS checkout, so edits show up on
# the next launch. Re-run this only if you move the repo.
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $pythonw)) {
    Write-Host "No .venv here. Create it first:"
    Write-Host "  py -3 -m venv .venv"
    Write-Host "  .venv\Scripts\pip install -e ."
    exit 1
}

Write-Host "Installing the desktop shell into .venv"
& (Join-Path $repo ".venv\Scripts\pip.exe") install --quiet --upgrade pywebview

# pythonw, not python: no console window behind the app window. The desktop
# module opens the window itself and shuts the mDNS advertisement down when it
# closes, which a browser tab cannot do.
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "zwiftbridge.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "zwiftbridge.lnk")
)

$icon = Join-Path $repo "src\zwiftbridge\assets\icons\icon.ico"
if (-not (Test-Path $icon)) { $icon = $pythonw }

$shell = New-Object -ComObject WScript.Shell
foreach ($target in $targets) {
    $link = $shell.CreateShortcut($target)
    $link.TargetPath = $pythonw
    $link.Arguments = "-m zwiftbridge.desktop"
    $link.WorkingDirectory = $repo
    $link.IconLocation = $icon
    $link.Description = "Zwift Ride controller to MyWhoosh"
    $link.Save()
    Write-Host "Installed $target"
}

Write-Host ""
Write-Host "Both shortcuts run the code in $repo, so edits show up on relaunch."
Write-Host "Windows asks for Bluetooth permission on first use; allow it for Python."
