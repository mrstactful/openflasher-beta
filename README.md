# OpenFlasher Beta

OpenFlasher is a Linux GUI frontend for Samsung firmware flashing with Heimdall, plus integrated Magisk patch workflow.

## Status

- Current channel: **Beta**
- Magisk tab is marked as **Magisk Beta**
- Recommended for advanced users who understand Odin/Heimdall flashing risks

## Download

Use the latest pre-release assets from:

- **Releases:** https://github.com/mrstactful/openflasher-beta/releases

Main artifacts:

- `OpenFlasher-Beta-x86_64.AppImage`
- `openflash-beta-YYYYMMDD.zip`

## Features

- Samsung slot-based flashing workflow (`BL`, `AP`, `CP`, `CSC`, `USERDATA`)
- PIT-assisted partition checks
- AP package Magisk patch support
- Real-time flashing logs and progress UI
- Turkish and English UI

## Quick Start (AppImage)

```bash
chmod +x OpenFlasher-Beta-x86_64.AppImage
./OpenFlasher-Beta-x86_64.AppImage
```

## Quick Start (Source)

### Requirements

- Linux x86_64
- `heimdall`, `lz4`, `tar`, `usbutils`
- Python 3
- PyQt6

### Run

```bash
python3 main.py
```

## Important Notes

- Flashing operations require **root** privileges.
- USB permissions/udev setup may be required on some systems.
- Always verify firmware/model compatibility before flashing.
- This project is still beta; keep backups before any flash operation.

## Known Scope

- Primary target: Linux x86_64
- AppImage bundles app/runtime and core toolchain wrappers, but system-level permission model still depends on host OS
