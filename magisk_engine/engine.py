"""Standalone Magisk boot image patching engine.

Portions of the patch flow are adapted from affggh/Magisk_patcher
(Apache-2.0) and Magisk's official boot patch logic. The code here keeps the
engine isolated from OpenFlasher's UI and runs all temporary files in a private
working directory.
"""

from __future__ import annotations

import os
import errno
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import json
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Callable

from .upstream_mp import boot_patch as upstream_boot_patch
from .upstream_mp import lang as upstream_lang


LogFn = Callable[[str], None]
MAGISK_RELEASE_API = "https://api.github.com/repos/topjohnwu/Magisk/releases/latest"

AP_IMAGE_PRIORITIES = (
    "init_boot.img",
    "init_boot.img.lz4",
    "boot.img",
    "boot.img.lz4",
    "recovery.img",
    "recovery.img.lz4",
)

RECOVERY_IMAGE_PRIORITIES = (
    "recovery.img",
    "recovery.img.lz4",
    "init_boot.img",
    "init_boot.img.lz4",
    "boot.img",
    "boot.img.lz4",
)

SAMSUNG_MODEL_INFO = {
    "J410F": {"name": "Samsung Galaxy J4 Core", "arch": "arm", "source": "local"},
}

@dataclass
class MagiskPatchOptions:
    image_path: str
    magisk_apk: str = ""
    output_dir: str = ""
    arch: str = "arm64"
    keep_verity: bool = True
    keep_forceencrypt: bool = True
    patch_vbmeta: bool = False
    recovery_mode: bool = False
    legacy_sar: bool = False
    cleanup: bool = True


@dataclass
class MagiskPatchResult:
    success: bool
    output_path: str = ""
    work_dir: str = ""
    logs: list[str] = field(default_factory=list)
    error: str = ""


def _host_lib_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    if machine in {"aarch64", "arm64", "armv8l", "armv8b"}:
        return "arm64-v8a"
    if machine in {"i386", "i686", "x86"}:
        return "x86"
    if machine.startswith("arm"):
        return "armeabi-v7a"
    return machine


def _target_lib_arch(arch: str) -> str:
    return {
        "arm64": "arm64-v8a",
        "arm": "armeabi-v7a",
        "x86": "x86",
        "x86_64": "x86_64",
    }.get(arch, arch)


def _target_32_arch(arch: str) -> str:
    return {
        "arm64-v8a": "armeabi-v7a",
        "x86_64": "x86",
    }.get(arch, arch)


def _resolve_target_arch(arch: str, image_path: Path, log: LogFn) -> str:
    return arch or "arm64"


def guess_target_arch(image_path: str) -> str:
    device = detect_samsung_device(image_path)
    if device.get("arch"):
        return device["arch"]
    name = Path(image_path).name.lower()
    if "x86_64" in name or "amd64" in name:
        return "x86_64"
    if re.search(r"(^|[_\-.])x86([_\-.]|$)", name) or "i686" in name:
        return "x86"
    if "armv7" in name or "armeabi" in name:
        return "arm"
    return "arm64"


def detect_samsung_device(image_path: str) -> dict[str, str]:
    name = Path(image_path).name
    match = re.search(r"(?:^|[_-])AP_?([A-Z0-9]+?)(?:XX|OX|UE|US|DX|UB|JV|ZS|ZT|B[0-9]|[A-Z]{2,3}[0-9])", name, re.IGNORECASE)
    model = ""
    if match:
        model = match.group(1).upper()
    else:
        fallback = re.search(r"(?:^|[_-])([A-Z][0-9]{3,4}[A-Z0-9]{0,3})(?:XX|OX|UE|US|DX|UB|JV|ZS|ZT)", name, re.IGNORECASE)
        if fallback:
            model = fallback.group(1).upper()
    info = SAMSUNG_MODEL_INFO.get(model, {})
    return {
        "model": model,
        "name": info.get("name", ""),
        "arch": info.get("arch", ""),
        "source": info.get("source", ""),
    }




def _bool_env(value: bool) -> str:
    return "true" if value else "false"


def _sha1(path: Path) -> str:
    h = sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _grep_prop(key: str, path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if key in line and "=" in line:
                return line.split("=", 1)[1].rstrip("\n")
    return ""


def _safe_copy(src: Path, dst: Path) -> None:
    if src.is_file():
        shutil.copyfile(src, dst)


def _remove(*paths: Path) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            path.unlink(missing_ok=True)


def _is_tar_package(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".tar", ".tar.md5", ".tgz", ".tar.gz"))


def _is_lz4(path: Path) -> bool:
    return path.name.lower().endswith(".lz4")


def _decompress_lz4(path: Path, log: LogFn) -> Path:
    out = path.with_suffix("")
    log(f"[MAGISK] Decompressing LZ4: {path.name}")
    proc = subprocess.run(
        ["lz4", "-d", "-f", str(path), str(out)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"lz4 decompress failed: {proc.stdout.strip()}")
    return out


def _compress_lz4(path: Path, output: Path, log: LogFn) -> Path:
    log(f"[MAGISK] Compressing LZ4: {output.name}")
    proc = subprocess.run(
        ["lz4", "-B6", "--content-size", "-f", str(path), str(output)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"lz4 compress failed: {proc.stdout.strip()}")
    return output


def _safe_tar_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    for member in tar.getmembers():
        normalized = os.path.normpath(member.name)
        if os.path.isabs(normalized) or normalized.startswith("..") or "/../" in normalized.replace("\\", "/"):
            raise RuntimeError(f"Unsafe tar member: {member.name}")
        members.append(member)
    return members


def _find_patch_target_member(members: list[tarfile.TarInfo], recovery_mode: bool = False) -> tarfile.TarInfo:
    files = [m for m in members if m.isfile()]
    by_base = {Path(m.name).name.lower(): m for m in files}
    priorities = RECOVERY_IMAGE_PRIORITIES if recovery_mode else AP_IMAGE_PRIORITIES
    for candidate in priorities:
        member = by_base.get(candidate)
        if member:
            return member
    for member in files:
        name = Path(member.name).name.lower()
        if name.startswith(("init_boot.img", "boot.img", "recovery.img")):
            return member
    raise RuntimeError("AP package does not contain boot.img, init_boot.img, or recovery.img.")


def _extract_tar_member(package: Path, member: tarfile.TarInfo, dest_dir: Path) -> Path:
    with tarfile.open(package, "r:*") as tar:
        tar.extract(member, dest_dir)
    return dest_dir / member.name


def _extract_ap_package(package: Path, dest_dir: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(package, "r:*") as tar:
        members = _safe_tar_members(tar)
        tar.extractall(dest_dir, members=members)
    return members


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def _tar_sort_key(path: Path) -> str:
    return path.as_posix()


def _default_openflasher_base_dir() -> Path:
    default = os.path.join(os.path.expanduser("~"), ".local/share/openflasher")
    return Path(os.environ.get("OPENFLASHER_DIR", default)).expanduser()


def _magisk_cache_dir() -> Path:
    return _default_openflasher_base_dir() / "magisk"


def _magisk_cache_state_path(cache_dir: Path) -> Path:
    return cache_dir / "latest.json"


def _load_magisk_cache_state(state_path: Path) -> dict[str, str]:
    try:
        if state_path.is_file():
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if isinstance(k, str)}
    except Exception:
        pass
    return {}


def _save_magisk_cache_state(state_path: Path, state: dict[str, str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_cached_magisk_apk(cache_dir: Path, state: dict[str, str]) -> Path | None:
    preferred_name = (state.get("apk_name") or "").strip()
    if preferred_name:
        candidate = cache_dir / preferred_name
        if candidate.is_file():
            return candidate

    candidates = sorted(
        cache_dir.glob("Magisk*.apk"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _fetch_latest_magisk_release(log: LogFn) -> dict[str, str]:
    request = urllib.request.Request(
        MAGISK_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OpenFlasher/1.0",
        },
    )
    log("[MAGISK] Downloading latest Magisk release metadata...")
    with urllib.request.urlopen(request, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    assets = payload.get("assets") if isinstance(payload, dict) else []
    if not isinstance(assets, list):
        assets = []
    apk_asset = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        if name.startswith("Magisk") and name.endswith(".apk") and "Manager" not in name:
            apk_asset = asset
            break
    if not apk_asset:
        raise RuntimeError("Could not find Magisk APK download URL in latest release.")
    url = str(apk_asset.get("browser_download_url", "")).strip()
    name = str(apk_asset.get("name", "")).strip()
    if not url or not name:
        raise RuntimeError("Latest Magisk release metadata is incomplete.")
    tag = str(payload.get("tag_name", "")).strip() if isinstance(payload, dict) else ""
    return {"tag": tag, "name": name, "url": url}


def _download_latest_magisk(dest: Path, url: str, log: LogFn) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"[MAGISK] Downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return dest


def _free_bytes(path: Path) -> int:
    try:
        stat = os.statvfs(path)
    except Exception:
        return -1
    return int(stat.f_bavail * stat.f_frsize)


def _format_gib(value: int) -> str:
    if value < 0:
        return "unknown"
    return f"{value / (1024 ** 3):.2f} GiB"


def _estimate_required_work_bytes(image: Path) -> int:
    try:
        image_size = max(1, image.stat().st_size)
    except Exception:
        image_size = 512 * 1024 * 1024

    if _is_tar_package(image):
        estimate = (image_size * 5) + (1024 * 1024 * 1024)
        floor = 2 * 1024 * 1024 * 1024
    else:
        estimate = (image_size * 3) + (512 * 1024 * 1024)
        floor = 768 * 1024 * 1024

    ceiling = 64 * 1024 * 1024 * 1024
    return max(floor, min(estimate, ceiling))


def _work_parent_candidates(output_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None):
        if not path:
            return
        resolved = str(path.expanduser())
        if resolved in seen:
            return
        seen.add(resolved)
        candidates.append(Path(resolved))

    add(output_dir)
    add(_default_openflasher_base_dir() / "tmp")
    env_tmp = os.environ.get("TMPDIR", "").strip()
    if env_tmp:
        add(Path(env_tmp))
    add(Path(tempfile.gettempdir()))
    return candidates


def _select_work_parent(image: Path, output_dir: Path, log: LogFn) -> Path:
    required = _estimate_required_work_bytes(image)
    best_path: Path | None = None
    best_free = -1
    checked: list[tuple[str, int]] = []

    for candidate in _work_parent_candidates(output_dir):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except Exception:
            checked.append((str(candidate), -1))
            continue
        free = _free_bytes(candidate)
        checked.append((str(candidate), free))
        if free > best_free:
            best_free = free
            best_path = candidate
        if free >= required:
            log(
                f"[MAGISK] Work dir parent selected: {candidate} "
                f"(free={_format_gib(free)}, required~{_format_gib(required)})"
            )
            return candidate

    details = ", ".join(f"{path}: {_format_gib(free)}" for path, free in checked) or "none"
    raise RuntimeError(
        "Insufficient free space for Magisk patch work directory. "
        f"Required~{_format_gib(required)}. Checked: {details}"
    )


def _resolve_auto_magisk_apk(log: LogFn) -> Path:
    cache_dir = _magisk_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    state_path = _magisk_cache_state_path(cache_dir)
    state = _load_magisk_cache_state(state_path)
    cached_apk = _find_cached_magisk_apk(cache_dir, state)

    try:
        latest = _fetch_latest_magisk_release(log)
    except Exception as exc:
        if cached_apk and cached_apk.is_file():
            log(f"[MAGISK] Using cached Magisk APK due to network/error: {cached_apk.name}")
            return cached_apk
        raise RuntimeError(f"Cannot download latest Magisk and no cached APK found: {exc}") from exc

    latest_tag = latest.get("tag", "")
    latest_name = latest.get("name", "Magisk-latest.apk")
    latest_url = latest.get("url", "")
    target_apk = cache_dir / latest_name
    state_tag = state.get("tag", "")

    if cached_apk and cached_apk.is_file():
        same_tag = bool(state_tag and latest_tag and state_tag == latest_tag)
        same_name = cached_apk.name == latest_name and target_apk.is_file()
        if same_tag or same_name:
            log(f"[MAGISK] Cached Magisk APK is up to date: {cached_apk.name}")
            _save_magisk_cache_state(
                state_path,
                {
                    "tag": latest_tag,
                    "apk_name": cached_apk.name,
                    "url": latest_url,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return cached_apk

    log(f"[MAGISK] Updating cached Magisk APK: {latest_name}")
    try:
        downloaded_apk = _download_latest_magisk(target_apk, latest_url, log)
    except Exception as exc:
        if cached_apk and cached_apk.is_file():
            log(f"[MAGISK] Using cached Magisk APK due to update failure: {cached_apk.name}")
            return cached_apk
        raise RuntimeError(f"Failed to download latest Magisk APK: {exc}") from exc

    _save_magisk_cache_state(
        state_path,
        {
            "tag": latest_tag,
            "apk_name": downloaded_apk.name,
            "url": latest_url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return downloaded_apk


def _extract_magisk_apk(apk: Path, work_dir: Path, arch: str, log: LogFn) -> Path:
    if not apk.is_file():
        raise FileNotFoundError(f"Magisk APK not found: {apk}")
    target_arch = _target_lib_arch(arch)
    target_32 = _target_32_arch(target_arch)
    host_arch = _host_lib_arch()
    bin_dir = work_dir / "bin"
    bin_dir.mkdir(exist_ok=True)
    magiskboot = bin_dir / ("magiskboot.exe" if os.name == "nt" else "magiskboot")

    log(f"[MAGISK] Extracting APK payload for target={target_arch}, host={host_arch}")
    with zipfile.ZipFile(apk) as zf:
        names = set(zf.namelist())
        if "assets/stub.apk" in names:
            (work_dir / "stub.apk").write_bytes(zf.read("assets/stub.apk"))
        else:
            for name in names:
                if name.endswith("stub.apk"):
                    (work_dir / "stub.apk").write_bytes(zf.read(name))
                    break

        host_boot = f"lib/{host_arch}/libmagiskboot.so"
        if host_boot not in names:
            raise RuntimeError(f"Magisk APK does not contain host magiskboot: {host_boot}")
        magiskboot.write_bytes(zf.read(host_boot))
        magiskboot.chmod(0o755)

        magiskinit = f"lib/{target_arch}/libmagiskinit.so"
        magisk32_candidates = [
            f"lib/{target_32}/libmagisk32.so",
            f"lib/{target_32}/libmagisk.so",
            f"lib/{target_arch}/libmagisk32.so",
            f"lib/{target_arch}/libmagisk.so",
        ]
        if magiskinit not in names:
            raise RuntimeError(f"Magisk APK does not contain {magiskinit}")
        magisk32 = next((name for name in magisk32_candidates if name in names), "")
        if not magisk32:
            available = sorted(name for name in names if name.startswith("lib/") and "magisk" in name)
            raise RuntimeError(
                "Selected Magisk APK does not contain a 32-bit Magisk payload for "
                f"{target_32}. Choose a Magisk APK with armeabi-v7a support or select another architecture. "
                f"Available Magisk libs: {', '.join(available) if available else 'none'}"
            )
        (work_dir / "magiskinit").write_bytes(zf.read(magiskinit))
        (work_dir / "magisk32").write_bytes(zf.read(magisk32))
        magisk64 = f"lib/{target_arch}/libmagisk64.so"
        if target_arch in {"arm64-v8a", "x86_64"} and magisk64 in names:
            (work_dir / "magisk64").write_bytes(zf.read(magisk64))

    return magiskboot


class _LogWriter:
    def __init__(self, log: LogFn):
        self.log = log
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.log(f"[MAGISK] {line.strip()}")
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self.log(f"[MAGISK] {self._buffer.strip()}")
        self._buffer = ""


class _Cwd:
    def __init__(self, path: Path):
        self.path = path
        self.old = Path.cwd()

    def __enter__(self):
        os.chdir(self.path)

    def __exit__(self, exc_type, exc, tb):
        os.chdir(self.old)


class _UpstreamPatcher:
    def __init__(self, work_dir: Path, options: MagiskPatchOptions, log: LogFn):
        self.work_dir = work_dir
        self.options = options
        self.log = log
        self.writer = _LogWriter(log)
        self.magiskboot = work_dir / "bin" / ("magiskboot.exe" if os.name == "nt" else "magiskboot")
        self.patcher = upstream_boot_patch.BootPatcher(
            str(self.magiskboot),
            options.keep_verity,
            options.keep_forceencrypt,
            options.patch_vbmeta,
            options.recovery_mode,
            options.legacy_sar,
            None,
            self.writer,
        )

    def patch(self, image: Path) -> Path:
        local_image = self.work_dir / image.name
        if image.resolve() != local_image.resolve():
            shutil.copyfile(image, local_image)
        with _Cwd(self.work_dir):
            ok = self.patcher.patch(local_image.name)
            self.writer.flush()
        if not ok:
            raise RuntimeError("Magisk patch failed.")
        output = self.work_dir / "new-boot.img"
        if not output.is_file():
            candidates = list(self.work_dir.glob("new*.img")) + list(self.work_dir.glob("*patched*.img"))
            if not candidates:
                raise RuntimeError("Magisk did not produce a patched image.")
            output = candidates[0]
        return output


class _BootPatcher:
    def __init__(self, magiskboot: Path, work_dir: Path, options: MagiskPatchOptions, log: LogFn):
        self.magiskboot = magiskboot
        self.work_dir = work_dir
        self.options = options
        self.log = log
        self.env = {
            **os.environ,
            "KEEPVERITY": _bool_env(options.keep_verity),
            "KEEPFORCEENCRYPT": _bool_env(options.keep_forceencrypt),
            "PATCHVBMETAFLAG": _bool_env(options.patch_vbmeta),
            "RECOVERYMODE": _bool_env(options.recovery_mode),
            "LEGACYSAR": _bool_env(options.legacy_sar),
            "MAGISKBOOT_WINSUP_NOCASE": "1",
        }

    def _run(self, args: list[str]) -> tuple[int, str]:
        cmd = [str(self.magiskboot), *args]
        proc = subprocess.run(
            cmd,
            cwd=self.work_dir,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        out = proc.stdout or ""
        for line in out.splitlines():
            if line.strip():
                self.log(f"[MAGISKBOOT] {line.strip()}")
        return proc.returncode, out

    def patch(self, boot_image: Path) -> Path:
        if not boot_image.is_file():
            raise FileNotFoundError(f"Boot image not found: {boot_image}")

        local_boot = self.work_dir / boot_image.name
        shutil.copyfile(boot_image, local_boot)

        self.log("[MAGISK] Unpacking boot image...")
        err, _ = self._run(["unpack", local_boot.name])
        if err == 1:
            raise RuntimeError("Unsupported or unknown boot image format.")
        if err == 2:
            raise RuntimeError("ChromeOS boot images are not supported.")
        if err != 0:
            raise RuntimeError("Unable to unpack boot image.")

        self.log("[MAGISK] Checking ramdisk status...")
        ramdisk = self.work_dir / "ramdisk.cpio"
        if ramdisk.is_file():
            status, _ = self._run(["cpio", "ramdisk.cpio", "test"])
            skip_backup = ""
        else:
            status = 0
            skip_backup = "#"

        image_sha = ""
        if status & 3 == 0:
            self.log("[MAGISK] Stock boot image detected.")
            image_sha = _sha1(local_boot)
            _safe_copy(local_boot, self.work_dir / "stock_boot.img")
            _safe_copy(ramdisk, self.work_dir / "ramdisk.cpio.orig")
        elif status & 3 == 1:
            self.log("[MAGISK] Magisk patched boot image detected.")
            self._run(["cpio", "ramdisk.cpio", "extract .backup/.magisk config.orig", "restore"])
            _safe_copy(ramdisk, self.work_dir / "ramdisk.cpio.orig")
            _remove(self.work_dir / "stock_boot.img")
        elif status & 3 == 2:
            raise RuntimeError("Boot image was patched by an unsupported program. Restore stock boot first.")

        init_name = "init.real" if status & 4 else "init"
        config_orig = self.work_dir / "config.orig"
        if config_orig.is_file():
            image_sha = _grep_prop("SHA1", config_orig)
            _remove(config_orig)

        self.log("[MAGISK] Patching ramdisk...")
        skip32 = "#"
        skip64 = "#"
        if (self.work_dir / "magisk64").is_file():
            self._run(["compress=xz", "magisk64", "magisk64.xz"])
            skip64 = ""
        if (self.work_dir / "magisk32").is_file():
            self._run(["compress=xz", "magisk32", "magisk32.xz"])
            skip32 = ""

        stub = (self.work_dir / "stub.apk").is_file()
        if stub:
            self._run(["compress=xz", "stub.apk", "stub.xz"])

        config = self.work_dir / "config"
        config.write_text(
            f"KEEPVERITY={self.env['KEEPVERITY']}\n"
            f"KEEPFORCEENCRYPT={self.env['KEEPFORCEENCRYPT']}\n"
            f"RECOVERYMODE={self.env['RECOVERYMODE']}\n"
            + (f"SHA1={image_sha}\n" if image_sha else ""),
            encoding="utf-8",
        )

        err, _ = self._run([
            "cpio", "ramdisk.cpio",
            f"add 0750 {init_name} magiskinit",
            "mkdir 0750 overlay.d",
            "mkdir 0750 overlay.d/sbin",
            f"{skip32} add 0644 overlay.d/sbin/magisk32.xz magisk32.xz",
            f"{skip64} add 0644 overlay.d/sbin/magisk64.xz magisk64.xz",
            "add 0644 overlay.d/sbin/stub.xz stub.xz" if stub else "",
            "patch",
            f"{skip_backup} backup ramdisk.cpio.orig",
            "mkdir 000 .backup",
            "add 000 .backup/.magisk config",
        ])
        if err != 0:
            raise RuntimeError("Unable to patch ramdisk.")

        _remove(
            self.work_dir / "ramdisk.cpio.orig",
            self.work_dir / "config",
            self.work_dir / "magisk32.xz",
            self.work_dir / "magisk64.xz",
            self.work_dir / "stub.xz",
        )

        for dt in ("dtb", "kernel_dtb", "extra"):
            if (self.work_dir / dt).is_file():
                err, _ = self._run(["dtb", dt, "test"])
                if err != 0:
                    raise RuntimeError(f"{dt} was patched by old unsupported Magisk.")
                err, _ = self._run(["dtb", dt, "patch"])
                if err == 0:
                    self.log(f"[MAGISK] Patched fstab in {dt}.")

        kernel = self.work_dir / "kernel"
        if kernel.is_file():
            patched = False
            for old, new in (
                (
                    "49010054011440B93FA00F71E9000054010840B93FA00F7189000054001840B91FA00F7188010054",
                    "A1020054011440B93FA00F7140020054010840B93FA00F71E0010054001840B91FA00F7181010054",
                ),
                ("821B8012", "E2FF8F12"),
            ):
                err, _ = self._run(["hexpatch", "kernel", old, new])
                patched = patched or err == 0
            if self.options.legacy_sar:
                err, _ = self._run([
                    "hexpatch", "kernel",
                    "736B69705F696E697472616D667300",
                    "77616E745F696E697472616D667300",
                ])
                patched = patched or err == 0
            if not patched:
                _remove(kernel)

        self.log("[MAGISK] Repacking boot image...")
        err, _ = self._run(["repack", local_boot.name])
        if err != 0:
            raise RuntimeError("Unable to repack boot image.")

        output = self.work_dir / "new-boot.img"
        if not output.is_file():
            candidates = list(self.work_dir.glob("new*.img")) + list(self.work_dir.glob("*patched*.img"))
            if not candidates:
                raise RuntimeError("Magisk did not produce a patched image.")
            output = candidates[0]

        self._run(["cleanup"])
        return output


def _make_output_path(output_dir: Path, stem: str, suffix: str) -> Path:
    final = output_dir / f"{stem}_magisk_patched{suffix}"
    counter = 1
    while final.exists():
        final = output_dir / f"{stem}_magisk_patched_{counter}{suffix}"
        counter += 1
    return final


def _patch_single_image(
    image: Path,
    output_dir: Path,
    patcher: _BootPatcher,
    emit: LogFn,
    output_stem: str | None = None,
) -> Path:
    image_for_patch = image
    if _is_lz4(image):
        image_for_patch = _decompress_lz4(image, emit)

    patched = patcher.patch(image_for_patch)
    suffix = image_for_patch.suffix or ".img"
    final = _make_output_path(output_dir, output_stem or image_for_patch.stem, suffix)
    shutil.copyfile(patched, final)
    emit(f"[MAGISK] Patched image saved: {final}")
    return final


def _patch_ap_package(package: Path, output_dir: Path, patcher: _BootPatcher, options: MagiskPatchOptions, emit: LogFn) -> Path:
    extract_dir = patcher.work_dir / "ap_extract"
    extract_dir.mkdir(exist_ok=True)
    lz4_patch_dir = patcher.work_dir / "lz4_patch_src"
    lz4_patch_dir.mkdir(exist_ok=True)

    emit(f"[MAGISK] Reading AP package: {package.name}")
    members = _extract_ap_package(package, extract_dir)
    target = _find_patch_target_member(members, recovery_mode=options.recovery_mode)
    target_name = Path(target.name).name
    emit(f"[MAGISK] Selected AP image: {target_name}")

    extracted = extract_dir / target.name
    image_for_patch = extracted
    if _is_lz4(extracted):
        # Decompress in a separate workspace so temporary .img files are not packed into AP output.
        lz4_source = lz4_patch_dir / target_name
        shutil.copyfile(extracted, lz4_source)
        image_for_patch = _decompress_lz4(lz4_source, emit)

    patched = patcher.patch(image_for_patch)

    patched_member = extract_dir / target.name
    if target_name.lower().endswith(".lz4"):
        _compress_lz4(patched, patched_member, emit)
    else:
        shutil.copyfile(patched, patched_member)

    output_tar = _make_output_path(output_dir, package.stem.replace(".tar", ""), ".tar")
    emit(f"[MAGISK] Building full patched AP tar: {output_tar}")
    with tarfile.open(output_tar, "w") as tar:
        for path in sorted((p for p in extract_dir.rglob("*") if p.is_file()), key=_tar_sort_key):
            tar.add(path, arcname=path.relative_to(extract_dir).as_posix(), filter=_tar_filter)

    emit(f"[MAGISK] Patched AP package saved: {output_tar}")
    return output_tar


def _prepare_upstream_patcher(apk: Path, work_root: Path, options: MagiskPatchOptions, emit: LogFn) -> _UpstreamPatcher:
    upstream_lang.Language.select = "en_US"
    _extract_magisk_apk(apk, work_root, options.arch, emit)
    return _UpstreamPatcher(work_root, options, emit)


def patch_boot_image(options: MagiskPatchOptions, log: LogFn | None = None) -> MagiskPatchResult:
    logs: list[str] = []
    work_root: Path | None = None

    def emit(message: str) -> None:
        logs.append(message)
        if log:
            log(message)

    keep_work = not options.cleanup
    try:
        image = Path(options.image_path).expanduser().resolve()
        output_dir = Path(options.output_dir or image.parent).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        work_parent = _select_work_parent(image, output_dir, emit)
        work_root = Path(tempfile.mkdtemp(prefix="openflasher-magisk-", dir=str(work_parent)))

        if options.magisk_apk:
            apk = Path(options.magisk_apk).expanduser().resolve()
        else:
            apk = _resolve_auto_magisk_apk(emit)

        target_arch = _resolve_target_arch(options.arch, image, emit)
        options.arch = target_arch
        emit(f"[MAGISK] Preparing upstream Magisk engine for arch={target_arch}")
        patcher = _prepare_upstream_patcher(apk, work_root, options, emit)
        if _is_tar_package(image):
            final = _patch_ap_package(image, output_dir, patcher, options, emit)
        else:
            final = _patch_single_image(image, output_dir, patcher, emit)
        return MagiskPatchResult(True, str(final), str(work_root), logs)
    except OSError as exc:
        keep_work = True
        if exc.errno == errno.ENOSPC:
            message = (
                "No space left on device while preparing Magisk patch workspace. "
                "Please free disk space or choose an output folder on a larger disk."
            )
            emit(f"[MAGISK] Error: {message}")
            return MagiskPatchResult(False, "", str(work_root or ""), logs, message)
        emit(f"[MAGISK] Error: {exc}")
        return MagiskPatchResult(False, "", str(work_root or ""), logs, str(exc))
    except Exception as exc:
        keep_work = True
        emit(f"[MAGISK] Error: {exc}")
        return MagiskPatchResult(False, "", str(work_root or ""), logs, str(exc))
    finally:
        if work_root and not keep_work:
            shutil.rmtree(work_root, ignore_errors=True)
