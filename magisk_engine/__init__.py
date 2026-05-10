"""Magisk boot image patching helpers for OpenFlasher."""

from .engine import MagiskPatchOptions, MagiskPatchResult, detect_samsung_device, patch_boot_image

__all__ = ["MagiskPatchOptions", "MagiskPatchResult", "detect_samsung_device", "patch_boot_image"]
