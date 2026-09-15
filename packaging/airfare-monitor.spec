# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [
    (str(ROOT / "resources" / "airports.zh.json"), "resources"),
    (str(ROOT / "resources" / "routes.default.yaml"), "resources"),
    (str(ROOT / "resources" / "settings.default.yaml"), "resources"),
    (str(ROOT / "resources" / "styles.qss"), "resources"),
]
datas += collect_data_files("airportsdata")
hiddenimports = collect_submodules("DrissionPage")
hiddenimports += collect_submodules("keyring.backends")

a = Analysis(
    [str(ROOT / "packaging" / "desktop_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[], datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)

# Qt 6 uses the Windows system ICU (icuuc.dll). Some developer environments
# expose an unrelated, version-suffixed Poppler ICU through PATH; PyInstaller's
# dependency scan can then collect it under the same name. That DLL shadows the
# system ICU at runtime and QtWidgets fails with ERROR_PROC_NOT_FOUND. API-set
# forwarders found through tool-specific PATH entries are system components too
# and must not be redistributed from those entries.
def _is_environment_system_dll(entry):
    destination = Path(entry[0]).name.lower()
    return (
        destination == "icuuc.dll"
        or (destination.startswith("icudt") and destination.endswith(".dll"))
        or destination.startswith("api-ms-win-")
    )


a.binaries = [entry for entry in a.binaries if not _is_environment_system_dll(entry)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AirfareMonitor", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, icon=str(ROOT / "resources" / "app.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AirfareMonitor")
