"""Repeatable, unsigned internal Windows release builder (no installation)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def release_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    package = ROOT / "src" / "airfare_monitor" / "__init__.py"
    installer = ROOT / "packaging" / "installer.iss"
    if f'__version__ = "{version}"' not in package.read_text(encoding="utf-8"):
        raise RuntimeError("Python 包版本号与 pyproject.toml 不一致")
    if f'#define MyAppVersion "{version}"' not in installer.read_text(encoding="utf-8"):
        raise RuntimeError("安装器版本号与 pyproject.toml 不一致")
    return version


def find_inno_compiler() -> Path:
    candidates = [
        shutil.which("ISCC.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 7" / "ISCC.exe",
    ]
    return next((Path(item) for item in candidates if item and Path(item).is_file()), None) or _missing_compiler()


def _missing_compiler() -> Path:
    raise FileNotFoundError("未找到 Inno Setup 7 ISCC.exe；先安装编译器再运行发布构建")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release() -> Path:
    version = release_version()
    compiler = find_inno_compiler()
    icon = ROOT / "resources" / "app.ico"
    if not icon.is_file():
        raise FileNotFoundError("缺少 resources/app.ico；先运行 packaging/generate_icon.py")
    build_root = ROOT / "build" / f"release-{version}"
    dist_root = ROOT / "dist" / f"release-{version}"
    output_root = ROOT / "release" / f"v{version}"
    output_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--workpath", str(build_root), "--distpath", str(dist_root),
         str(ROOT / "packaging" / "airfare-monitor.spec")],
        cwd=ROOT, check=True,
    )
    app_root = dist_root / "AirfareMonitor"
    if not (app_root / "AirfareMonitor.exe").is_file():
        raise RuntimeError("PyInstaller 未生成 AirfareMonitor.exe")
    with TemporaryDirectory(prefix="airfare-release-smoke-") as temporary:
        smoke_root = Path(temporary)
        report = smoke_root / "result.json"
        subprocess.run(
            [str(app_root / "AirfareMonitor.exe"), "--ui-smoke-test",
             "--user-root", str(smoke_root / "user"), "--smoke-report", str(report)],
            cwd=ROOT, check=True, timeout=90,
        )
        if json.loads(report.read_text(encoding="utf-8")).get("status") != "ok":
            raise RuntimeError("打包后的 Qt UI 烟测失败；不编译安装器")
    subprocess.run(
        [str(compiler), f"-dBuildRoot={app_root}", f"-dOutputRoot={output_root}",
         str(ROOT / "packaging" / "installer.iss")],
        cwd=ROOT, check=True,
    )
    installer = output_root / f"AirfareMonitorSetup-{version}.exe"
    if not installer.is_file():
        raise RuntimeError("Inno Setup 未生成安装器 EXE")
    checksum = output_root / f"{installer.name}.sha256"
    checksum.write_text(f"{sha256_file(installer)}  {installer.name}\n", encoding="ascii")
    print(f"安装器：{installer}\nSHA-256：{checksum}\n程序目录：{app_root}")
    return installer


if __name__ == "__main__":
    build_release()
