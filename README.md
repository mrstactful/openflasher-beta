# OpenFlasher Beta

OpenFlasher is an **Odin for Linux** style GUI for Samsung firmware flashing with Heimdall, plus an integrated Magisk patch workflow.
It is a practical **Samsung Odin alternative for Linux** (Heimdall GUI + AP/Magisk patch support).

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

## Discoverability Keywords

These terms are intentionally included so users can find this project easily:

- Odin for Linux
- Samsung Odin alternative
- Samsung firmware flash tool Linux
- Heimdall GUI
- Samsung ROM flashing Linux
- Samsung AP patch Magisk

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

## License

Flash Samsung devices

Copyright (C) 2026 Sami Kibar

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
