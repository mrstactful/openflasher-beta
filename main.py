    #!/usr/bin/env python3
"""
OpenFlasher - Heimdall GUI (Odin Alternatifi) - Profesyonel Sürüm
Samsung cihazları için açık kaynak flaşlama aracı.
"""

import sys, os, traceback, subprocess, shutil, re, time, shlex, pty, select
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSizePolicy, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QLineEdit, QProgressBar,
    QTextEdit, QFrame, QDialog, QFormLayout,
    QMessageBox, QGroupBox, QComboBox, QTabWidget, QCheckBox,
    QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, pyqtSlot, QMutex, QMutexLocker, QPropertyAnimation, QEasingCurve, QLocale, QPoint
from PyQt6.QtGui import QFont, QTextCursor, QDragEnterEvent, QDropEvent, QIcon, QPainter, QPixmap, QColor, QPolygon

from magisk_engine import MagiskPatchOptions, patch_boot_image


# ─── Custom CheckBox with Checkmark Only ───────────────────────────────────────
class CheckBox(QCheckBox):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._is_dark = True
        self.stateChanged.connect(self._update_icon)
        self._update_icon()

    def _update_icon(self, state=None):
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        if self._is_dark:
            bg = QColor("#1a1a1a")
            border = QColor("#888888")
            fg = QColor("#ffffff")
        else:
            bg = QColor("#f6f8fa")
            border = QColor("#24292f")
            fg = QColor("#000000")
        
        painter.fillRect(1, 1, 16, 16, bg)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(1, 1, 16, 16)
        
        if self.isChecked():
            painter.setPen(fg)
            painter.setFont(QFont("sans-serif", 11, QFont.Weight.Bold))
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "✓")
        
        painter.end()
        self.setIcon(QIcon(pixmap))
        self.setIconSize(pixmap.size())

    def set_dark_mode(self, is_dark):
        self._is_dark = is_dark
        self._update_icon()

    def setChecked(self, checked):
        super().setChecked(checked)
        self._update_icon()


# ─── Global Hata Yakalayıcı ───────────────────────────────────────────────────
def global_exception_handler(exc_type, exc_value, exc_traceback):
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print("Kritik hata:\n", error_msg)
    if QApplication.instance():     
        QMessageBox.critical(None, "Kritik Hata", f"Beklenmeyen hata:\n\n{error_msg}")
    sys.exit(1)

sys.excepthook = global_exception_handler

# ─── Yapılandırma ──────────────────────────────────────────────────────────────
DEFAULT_BASE_DIR = os.path.join(os.path.expanduser("~"), ".local/share/openflasher")
BASE_DIR = os.environ.get("OPENFLASHER_DIR", DEFAULT_BASE_DIR)
TMP_DIR = os.path.join(BASE_DIR, "tmp")
SUPPORTED_SLOTS = ["BL", "AP", "CP", "CSC", "USERDATA"]
LOG_FILE = os.environ.get("OPENFLASHER_LOG", os.path.join(os.path.expanduser("~"), "openflasher.log"))
MIN_FREE_SPACE_GB = 2

def format_shell_cmd(args):
    """Return a shell-safe single-line command string for logs."""
    out = []
    for a in args:
        s = str(a)
        if not s:
            out.append('""')
            continue
        if re.search(r"[^\w@%+=:,./-]", s):
            out.append('"' + s.replace('"', '\\"') + '"')
        else:
            out.append(s)
    return " ".join(out)

def tr(key, lang, fallback=""):
    return TRANSLATIONS.get(key, {}).get(lang, fallback)

def get_system_theme():
    """Sistem temasını algıla (True = Dark, False = Light)"""
    try:
        from PyQt6.QtWidgets import QApplication, QStyleFactory
        app = QApplication.instance()
        if app is None:
            return True
        palette = app.palette()
        window_color = palette.window().color()
        brightness = (window_color.red() + window_color.green() + window_color.blue()) / 3
        return brightness < 128
    except Exception:
        return True

def get_system_language():
    """Sistem dilini algıla (tr_TR -> tr, diğerleri -> en)"""
    try:
        locale_name = QLocale.system().name().lower()
        if locale_name.startswith("tr"):
            return "tr"
    except Exception:
        pass
    return "en"

def get_app_icon():
    """Draw the application icon used by windows and the task bar."""
    icon = QIcon()
    for size in (32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = max(2, size // 16)
        radius = max(4, size // 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#121826"))
        painter.drawRoundedRect(margin, margin, size - margin * 2, size - margin * 2, radius, radius)

        accent = QColor("#00b4d8")
        painter.setBrush(accent)
        bolt = [
            QPoint(size * 52 // 100, size * 15 // 100),
            QPoint(size * 25 // 100, size * 56 // 100),
            QPoint(size * 46 // 100, size * 56 // 100),
            QPoint(size * 38 // 100, size * 85 // 100),
            QPoint(size * 75 // 100, size * 42 // 100),
            QPoint(size * 54 // 100, size * 42 // 100),
        ]
        painter.drawPolygon(QPolygon(bolt))

        painter.end()
        icon.addPixmap(pixmap)
    return icon

def check_dependencies():
    missing = []
    if not shutil.which("heimdall"): missing.append("heimdall")
    if not shutil.which("lz4"): missing.append("lz4")
    if not shutil.which("tar"): missing.append("tar")
    
    # PyQt6 kontrolü
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        missing.append("python3-pyqt6")
    
    return missing

def get_free_space_gb(path):
    try:
        stat = os.statvfs(path)
        return (stat.f_bavail * stat.f_frsize) / (1024**3)
    except Exception:
        return -1

def rotate_log():
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = LOG_FILE + f".{ts}.bak"
            if os.path.exists(LOG_FILE + ".bak"):
                os.remove(LOG_FILE + ".bak")
            os.rename(LOG_FILE, bak)
    except Exception:
        pass

# ─── Çoklu Dil ────────────────────────────────────────────────────────────────
LANGUAGES = {"en": "English", "tr": "Türkçe"}
TRANSLATIONS = {
    "password_placeholder": {"en": "Enter password...", "tr": "Parolanızı girin..."},
    "password_empty_error": {"en": "⚠ Password cannot be empty!", "tr": "⚠ Parola boş bırakılamaz!"},
    "password_wrong_error": {"en": "⚠ Wrong password!", "tr": "⚠ Sudo şifresi yanlış!"},
    "theme_toggle": {"en": "Dark / Light", "tr": "Koyu / Açık"},
    "select_or_drop_file": {"en": "Select or drop files...", "tr": "Dosya seçin veya sürükleyin..."},
    "theme_btn_dark": {"en": "☀️ Light", "tr": "☀️ Açık"},
    "theme_btn_light": {"en": "🌙 Dark", "tr": "🌙 Koyu"},
    "auto_reboot": {"en": "Auto Reboot", "tr": "Otomatik Başlat"},
    "repartition": {"en": "Re-Partition", "tr": "Yeniden Bölümle"},
    "dry_run": {"en": "Dry Run", "tr": "Kuru Çalışma"},
    "udev_btn": {"en": "Fix USB Permissions", "tr": "USB İzinlerini Düzelt"},
    "magisk_patch_btn": {"en": "Magisk Patch", "tr": "Magisk Yamala"},
    "magisk_select_btn": {"en": "Select File", "tr": "Dosya Seç"},
    "reboot_download": {"en": "Download Mode", "tr": "Download Mod"},
    "reboot_recovery": {"en": "Recovery Mode", "tr": "Recovery Mod"},
    "csc_warning": {"en": "⚠ CSC file selected! This will FACTORY RESET.", "tr": "⚠ CSC seçildi! Cihaz SIFIRLANIR."},
    "csc_confirm": {"en": "CSC file is selected. This will FACTORY RESET the device. Continue?", "tr": "CSC dosyası seçili. Cihaz SIFIRLANACAK. Devam edilsin mi?"},
    "pass_text": {"en": "PASS!", "tr": "BAŞARILI!"},
    "fail_text": {"en": "FAIL!", "tr": "BAŞARISIZ!"},
    "status_waiting": {"en": "Waiting for device...", "tr": "Cihaz bekleniyor..."},
    "status_disconnected": {"en": "⚠ Disconnected!", "tr": "⚠ Bağlantı kesildi!"},
    "status_connected": {"en": "✓ Connected │ ", "tr": "✓ Bağlı │ "},
    "device_connected": {"en": " connected", "tr": " bağlandı"},
    "time_estimate": {"en": "Time: ", "tr": "Süre: "},
    "ready": {"en": "Ready", "tr": "Hazır"},
    "analyzing": {"en": "Analyzing...", "tr": "Analiz..."},
    "flashing": {"en": "Flashing...", "tr": "Flaşlanıyor..."},
    "flashing_partition": {"en": "{} flashing... {}%", "tr": "{} flaşlanıyor... %{}"},
    "sudo_ok": {"en": "[SYS] Root access granted.", "tr": "[SYS] Root yetkisi alındı."},
    "missing_tools": {"en": "[WARN] Missing: ", "tr": "[UYARI] Eksik: "},
    "missing_tools_error": {"en": "Required tools are missing! Please install: ", "tr": "Gerekli araçlar eksik! Lütfen yükleyin: "},
    "sys_started": {"en": "[SYS] Started.", "tr": "[SYS] Başlatıldı."},
    "connect_download_mode": {"en": "[SYS] Connect device in Download Mode.", "tr": "[SYS] Cihazı Download Mode'da bağlayın."},
    "low_disk_space": {"en": "Less than {} GB free space in temporary directory! Flash may fail.", "tr": "Geçici dizinde {} GB'tan az boş alan var! Flaş başarısız olabilir."},
    "device_detected": {"en": "Samsung device detected in Download Mode!", "tr": "Samsung cihazı Download Mode'da algılandı!"},
    "usb_monitor_started": {"en": "[USB] Monitor started, waiting for Download Mode...", "tr": "[USB] Cihaz monitörü başlatıldı, Download Mode bekleniyor..."},
    "usb_device_found": {"en": "[USB] Device connected ({})", "tr": "[USB] Cihaz bağlandı ({})"},
    "usb_device_disconnected": {"en": "[USB] Device disconnected!", "tr": "[USB] Cihaz bağlantısı kesildi!"},
    "usb_monitor_error": {"en": "[USB] Monitor error: {}", "tr": "[USB] Monitor hatası: {}"},
    "pit_downloading": {"en": "[PIT] Downloading PIT file...", "tr": "[PIT] PIT dosyası indiriliyor..."},
    "pit_downloaded": {"en": "[PIT] PIT file received ({} bytes)", "tr": "[PIT] PIT dosyası alındı ({} bytes)"},
    "pit_retry": {"en": "[PIT] Device not responding, retrying...", "tr": "[PIT] Cihaz yanıt vermiyor, tekrar deneniyor..."},
    "pit_connection_lost": {"en": "Device connection lost", "tr": "Cihaz bağlantısı kesildi"},
    "pit_usb_check_error": {"en": "[PIT] USB check error: {}", "tr": "[PIT] USB kontrol hatası: {}"},
    "pit_failed": {"en": "PIT download failed (exit code: {})", "tr": "PIT alınamadı (exit code: {})"},
    "pit_timeout": {"en": "PIT download timeout (30s)", "tr": "PIT indirme zaman aşımı (30s)"},
    "pit_generic_error": {"en": "Error: {}", "tr": "Hata: {}"},
    "analyzing_slot": {"en": "[ANA] Processing {}: {}", "tr": "[ANA] {} işleniyor: {}"},
    "analyze_error": {"en": "{} error: {}", "tr": "{} hatası: {}"},
    "flash_cancelled": {"en": "[FLASH] Flashing cancelled.", "tr": "[FLASH] Flaşlama iptal edildi."},
    "flash_cancelled_by_user": {"en": "Cancelled by user", "tr": "Kullanıcı tarafından iptal edildi"},
    "flash_cancel_error": {"en": "[FLASH] Cancel error: {}", "tr": "[FLASH] İptal hatası: {}"},
    "flash_dry_run_complete": {"en": "[DRY-RUN] Dry run completed.", "tr": "[DRY-RUN] Sanal flaşlama tamamlandı."},
    "flash_dry_run_finished": {"en": "Dry run completed", "tr": "Kuru çalışma tamamlandı"},
    "flash_started": {"en": "[FLASH] Flashing started...", "tr": "[FLASH] Flaşlama başlatıldı..."},
    "flash_success": {"en": "Success! ({} seconds)", "tr": "Başarılı! ({} saniye)"},
    "flash_heimdall_error": {"en": "Heimdall error code: {}", "tr": "Heimdall hata kodu: {}"},
    "flash_heimdall_not_found": {"en": "heimdall or sudo not found!", "tr": "heimdall veya sudo bulunamadı!"},
    "flash_generic_error": {"en": "Error: {}", "tr": "Hata: {}"},
    "usb_monitor_ui": {"en": "[USB] Monitor started...", "tr": "[USB] Cihaz monitörü başlatıldı..."},
    "device_scanning": {"en": "[SCAN] Scanning device...", "tr": "[TARAMA] Cihaz taranıyor..."},
    "reboot_command": {"en": "[REBOOT] sudo heimdall {}", "tr": "[REBOOT] sudo heimdall {}"},
    "reboot_error": {"en": "[REBOOT] Error: {}", "tr": "[REBOOT] Hata: {}"},
    "udev_rule_created": {"en": "[UDEV] USB permission rule created and reloaded.", "tr": "[UDEV] USB izin kuralı oluşturuldu ve yenilendi."},
    "udev_ready": {"en": "USB permissions are already ready.", "tr": "USB izinleri zaten hazır."},
    "udev_missing_title": {"en": "USB Permissions Missing", "tr": "USB İzinleri Eksik"},
    "udev_missing_confirm": {"en": "USB permission rule is missing:\n{}\nCreate it now?", "tr": "USB izin kuralı eksik:\n{}\nŞimdi oluşturulsun mu?"},
    "udev_created_message": {"en": "USB permission rule was created and rules were reloaded.", "tr": "USB izin kuralı oluşturuldu ve kurallar yenilendi."},
    "pit_success": {"en": "[PIT] ✓ PIT file received successfully.", "tr": "[PIT] ✓ PIT dosyası başarıyla alındı."},
    "pit_ready_flash": {"en": "[PIT] PIT file is ready for flashing.", "tr": "[PIT] PIT dosyası flaşlamaya hazır."},
    "pit_failure": {"en": "[PIT] Failed: {}", "tr": "[PIT] Başarısız: {}"},
    "pit_not_available": {"en": "[PIT] WARNING: PIT file not available!", "tr": "[PIT] UYARI: PIT dosyası mevcut değil!"},
    "pit_parse_failed": {"en": "[PIT] WARNING: PIT file could not be parsed.", "tr": "[PIT] UYARI: PIT dosyası okunup çözümlenemedi."},
    "pit_requesting": {"en": "[PIT] Requesting PIT from device...", "tr": "[PIT] Cihazdan PIT isteniyor..."},
    "flash_process_started": {"en": "[START] Flash process started...{}", "tr": "[START] Flaşlama süreci başlatıldı...{}"},
    "pit_cancelled": {"en": "[PIT] Cancelled.", "tr": "[PIT] İptal edildi."},
    "analysis_complete": {"en": "[ANA] ✓ File analysis complete", "tr": "[ANA] ✓ Dosya analizi tamamlandı"},
    "analysis_warning": {"en": "[ANA] ⚠ Uncertain file in {} slot: {}", "tr": "[ANA] ⚠ {} slotunda emin olunamayan dosya: {}"},
    "analysis_skip": {"en": "[ANA] ✕ File skipped: {}", "tr": "[ANA] ✕ Dosya atlandı: {}"},
    "option_repartition": {"en": "[OPT] ⚠ Re-Partition active.", "tr": "[OPT] ⚠ Re-Partition aktif."},
    "option_auto_reboot": {"en": "[OPT] Auto Reboot will be applied.", "tr": "[OPT] Auto Reboot uygulanacak."},
    "success_message": {"en": "[✓] {}", "tr": "[✓] {}"},
    "error_message": {"en": "[✗] ERROR: {}", "tr": "[✗] HATA: {}"},
    "magisk_boot_selected": {"en": "[MAGISK] Manual magiskboot selected: {}", "tr": "[MAGISK] Manuel magiskboot seçildi: {}"},
    "magisk_using_custom": {"en": "[MAGISK] Using custom magiskboot: {}", "tr": "[MAGISK] Manuel magiskboot kullanılıyor: {}"},
    "magisk_success": {"en": "[MAGISK] ✓ {} -> {}", "tr": "[MAGISK] ✓ {} -> {}"},
    "magisk_error": {"en": "[MAGISK] ✕ {}", "tr": "[MAGISK] ✕ {}"},
    "sudo_failed": {"en": "Cannot run sudo:{}", "tr": "Sudo çalıştırılamadı:{}"},
    "root_required_title": {"en": "ROOT PERMISSION REQUIRED", "tr": "ROOT YETKİSİ GEREKİYOR"},
    "root_required_message": {"en": "Password required for operations.\nYour password will be requested only once.", "tr": "İşlemler için sistem parolası gereklidir.\nŞifreniz sadece bir kez istenecek."},
    "cancel": {"en": "CANCEL", "tr": "İPTAL"},
    "ok": {"en": "OK", "tr": "ONAYLA"},
    "confirm_selection": {"en": "Confirm Selection", "tr": "Seçimi Onayla"},
    "skip_file": {"en": "Skip This File", "tr": "Bu Dosyayı Atla"},
    "write_to_partition": {"en": "Write to Selected Partition", "tr": "Seçili Bölüme Yaz"},
    "check_udev": {"en": "Fix USB Permissions", "tr": "USB İzinlerini Düzelt"},
    "reboot_system": {"en": "Reboot System", "tr": "Sistemi Başlat"},
    "download_mode": {"en": "Download Mode", "tr": "Download Mod"},
    "recovery_mode": {"en": "Recovery Mode", "tr": "Recovery Mod"},
    "send": {"en": "Send", "tr": "Gönder"},
    "magisk_patch": {"en": "Magisk Patch", "tr": "Magisk Yamala"},
    "manual": {"en": "Manual", "tr": "Manuel"},
    "waiting": {"en": "Waiting...", "tr": "Bekleniyor..."},
    "select_or_drop": {"en": "Select or Drop Files", "tr": "Dosyaları Seçin veya Bırakın"},
    "browse": {"en": "Browse", "tr": "Gözat"},
    "preview": {"en": "Preview", "tr": "Ön İzleme"},
    "clear": {"en": "Clear", "tr": "Temizle"},
    "cancel_btn": {"en": "■ CANCEL", "tr": "■ İPTAL"},
    "reset_btn": {"en": "↺ RESET", "tr": "↺ SIFIRLA"},
    "exit_btn": {"en": "✕ EXIT", "tr": "✕ ÇIKIŞ"},
    "start_btn": {"en": "▶ START", "tr": "▶ BAŞLAT"},
    "flash_slots_group": {"en": "FLASH SLOTS", "tr": "FLASH BÖLÜMLERİ"},
    "log_tab": {"en": "Log", "tr": "Günlük"},
    "options_tab": {"en": "Options", "tr": "Ayarlar"},
    "reboot_tab": {"en": "Reboot", "tr": "Reboot"},
    "terminal_tab": {"en": "Terminal", "tr": "Terminal"},
    "magisk_tab": {"en": "Magisk Beta", "tr": "Magisk Beta"},
    "window_title": {"en": "OpenFlasher v1.0", "tr": "OpenFlasher v1.0"},
    "openflasher_title": {"en": "OPEN FLASHER", "tr": "OPEN FLASHER"},
    "heimdall_frontend": {"en": " │  Heimdall Frontend", "tr": " │  Heimdall Arayüzü"},
    "language_label": {"en": "Language:", "tr": "Dil:"},
    "device_info_group": {"en": "DEVICE INFO", "tr": "CİHAZ BİLGİSİ"},
    "vendor_label": {"en": "Vendor:", "tr": "Üretici:"},
    "model_label": {"en": "Model:", "tr": "Model:"},
    "pit_label": {"en": "PIT:", "tr": "PIT:"},
    "about_title": {"en": "About", "tr": "Hakkında"},
    "about_text": {"en": "OpenFlasher v1.0\n\nSamsung Flashing Tool", "tr": "OpenFlasher v1.0\n\nSamsung Flaşlama Aracı"},
    "select_file": {"en": "Select File", "tr": "Dosya Seç"},
    "select_slot": {"en": "Select Slot", "tr": "Bölüm Seç"},
    "slot_empty": {"en": "Empty", "tr": "Boş"},
    "slot_file": {"en": "File", "tr": "Dosya"},
    "slot_size": {"en": "Size", "tr": "Boyut"},
    "slot_status": {"en": "Status", "tr": "Durum"},
    "slot_actions": {"en": "Actions", "tr": "İşlemler"},
    "slot_select": {"en": "Select", "tr": "Seç"},
    "slot_clear": {"en": "Clear", "tr": "Temizle"},
    "slot_browse": {"en": "Browse", "tr": "Gözat"},
    "slot_eye": {"en": "Preview", "tr": "Ön İzleme"},
    "slot_info": {"en": "Info", "tr": "Bilgi"},
    "slot_error": {"en": "Error", "tr": "Hata"},
    "slot_warning": {"en": "Warning", "tr": "Uyarı"},
    "slot_success": {"en": "Success", "tr": "Başarılı"},
    "slot_invalid": {"en": "Invalid", "tr": "Geçersiz"},
    "slot_missing": {"en": "Missing", "tr": "Eksik"},
    "slot_loaded": {"en": "Loaded", "tr": "Yüklendi"},
    "slot_unloaded": {"en": "Unloaded", "tr": "Yüklenmedi"},
    "slot_ready": {"en": "Ready", "tr": "Hazır"},
    "slot_flashing": {"en": "Flashing", "tr": "Flaşlanıyor"},
    "slot_finished": {"en": "Finished", "tr": "Tamamlandı"},
    "slot_failed": {"en": "Failed", "tr": "Başarısız"},
    "slot_cancelled": {"en": "Cancelled", "tr": "İptal Edildi"},
    "slot_retry": {"en": "Retry", "tr": "Tekrar Dene"},
    "slot_skip": {"en": "Skip", "tr": "Atla"},
    "slot_continue": {"en": "Continue", "tr": "Devam Et"},
    "slot_back": {"en": "Back", "tr": "Geri"},
    "slot_next": {"en": "Next", "tr": "İleri"},
    "slot_finish": {"en": "Finish", "tr": "Bitir"},
    "slot_cancel": {"en": "Cancel", "tr": "İptal"},
    "slot_save": {"en": "Save", "tr": "Kaydet"},
    "slot_load": {"en": "Load", "tr": "Yükle"},
    "slot_export": {"en": "Export", "tr": "Dışa Aktar"},
    "slot_import": {"en": "Import", "tr": "İçe Aktar"},
    "slot_refresh": {"en": "Refresh", "tr": "Yenile"},
    "slot_search": {"en": "Search", "tr": "Arama"},
    "slot_filter": {"en": "Filter", "tr": "Filtre"},
    "slot_sort": {"en": "Sort", "tr": "Sırala"},
    "slot_add": {"en": "Add", "tr": "Ekle"},
    "slot_remove": {"en": "Remove", "tr": "Kaldır"},
    "slot_edit": {"en": "Edit", "tr": "Düzenle"},
    "slot_duplicate": {"en": "Duplicate", "tr": "Çoğalt"},
    "slot_move": {"en": "Move", "tr": "Taşı"},
    "slot_copy": {"en": "Copy", "tr": "Kopyala"},
    "slot_paste": {"en": "Paste", "tr": "Yapıştır"},
    "slot_cut": {"en": "Cut", "tr": "Kes"},
    "slot_undo": {"en": "Undo", "tr": "Geri Al"},
    "slot_redo": {"en": "Redo", "tr": "Yinele"},
    "slot_zoom_in": {"en": "Zoom In", "tr": "Yakınlaştır"},
    "slot_zoom_out": {"en": "Zoom Out", "tr": "Uzaklaştır"},
    "slot_zoom_reset": {"en": "Reset Zoom", "tr": "Yakınlaştırmayı Sıfırla"},
    "slot_fullscreen": {"en": "Fullscreen", "tr": "Tam Ekran"},
    "slot_settings": {"en": "Settings", "tr": "Ayarlar"},
    "slot_help": {"en": "Help", "tr": "Yardım"},
    "slot_about": {"en": "About", "tr": "Hakkında"},
    "slot_update": {"en": "Update", "tr": "Güncelle"},
    "slot_check": {"en": "Check", "tr": "Denetle"},
    "slot_test": {"en": "Test", "tr": "Test"},
    "slot_debug": {"en": "Debug", "tr": "Hata Ayıklama"},
    "slot_verbose": {"en": "Verbose", "tr": "Ayrıntılı"},
    "slot_quiet": {"en": "Quiet", "tr": "Sessiz"},
    "slot_force": {"en": "Force", "tr": "Zorla"},
    "slot_ignore": {"en": "Ignore", "tr": "Yok Say"},
    "slot_confirm": {"en": "Confirm", "tr": "Onayla"},
    "slot_discard": {"en": "Discard", "tr": "İptal Et"},
    "slot_apply": {"en": "Apply", "tr": "Uygula"},
    "slot_save_as": {"en": "Save As", "tr": "Farklı Kaydet"},
    "slot_open": {"en": "Open", "tr": "Aç"},
    "slot_new": {"en": "New", "tr": "Yeni"},
    "slot_close": {"en": "Close", "tr": "Kapat"},
    "slot_print": {"en": "Print", "tr": "Yazdır"},
    "slot_export_pdf": {"en": "Export PDF", "tr": "PDF Olarak Dışa Aktar"},
    "slot_export_csv": {"en": "Export CSV", "tr": "CSV Olarak Dışa Aktar"},
    "slot_export_json": {"en": "Export JSON", "tr": "JSON Olarak Dışa Aktar"},
    "slot_import_csv": {"en": "Import CSV", "tr": "CSV Olarak İçe Aktar"},
    "slot_import_json": {"en": "Import JSON", "tr": "JSON Olarak İçe Aktar"},
    "slot_import_xml": {"en": "Import XML", "tr": "XML Olarak İçe Aktar"},
    "slot_export_xml": {"en": "Export XML", "tr": "XML Olarak Dışa Aktar"},
    "slot_import_txt": {"en": "Import TXT", "tr": "TXT Olarak İçe Aktar"},
    "slot_export_txt": {"en": "Export TXT", "tr": "TXT Olarak Dışa Aktar"},
    "slot_import_doc": {"en": "Import DOC", "tr": "DOC Olarak İçe Aktar"},
    "slot_export_doc": {"en": "Export DOC", "tr": "DOC Olarak Dışa Aktar"},
    "slot_import_ppt": {"en": "Import PPT", "tr": "PPT Olarak İçe Aktar"},
    "slot_export_ppt": {"en": "Export PPT", "tr": "PPT Olarak Dışa Aktar"},
    "slot_import_xls": {"en": "Import XLS", "tr": "XLS Olarak İçe Aktar"},
    "slot_export_xls": {"en": "Export XLS", "tr": "XLS Olarak Dışa Aktar"},
    "slot_import_rtf": {"en": "Import RTF", "tr": "RTF Olarak İçe Aktar"},
    "slot_export_rtf": {"en": "Export RTF", "tr": "RTF Olarak Dışa Aktar"},
    "slot_import_html": {"en": "Import HTML", "tr": "HTML Olarak İçe Aktar"},
    "slot_export_html": {"en": "Export HTML", "tr": "HTML Olarak Dışa Aktar"},
    "slot_import_md": {"en": "Import MD", "tr": "MD Olarak İçe Aktar"},
    "slot_export_md": {"en": "Export MD", "tr": "MD Olarak Dışa Aktar"},
    "error": {"en": "Error", "tr": "Hata"},
    "tar_read_error": {"en": "Cannot read tar archive: {}", "tr": "Tar arşivi okunamadı: {}"},
    "device_detected_lsusb": {"en": "Samsung device detected via lsusb", "tr": "Samsung cihazı lsusb ile algılandı"},
    "heimdall_detect_failed": {"en": "Heimdall detect failed, trying lsusb...", "tr": "Heimdall algılama başarısız, lsusb deneniyor..."},
    "tt_start": {"en": "Start flashing", "tr": "Flaşlamayı başlat"},
    "tt_cancel": {"en": "Cancel operation", "tr": "İşlemi iptal et"},
    "tt_reset": {"en": "Clear all files", "tr": "Tüm dosyaları temizle"},
    "tt_exit": {"en": "Close OpenFlasher", "tr": "OpenFlasher'ı kapat"},
    "tt_theme": {"en": "Toggle theme", "tr": "Tema değiştir"},
    "tt_browse_slot": {"en": "Browse firmware", "tr": "Firmware seç"},
    "tt_surgical": {"en": "Surgical mode", "tr": "Cerrahî seçim"},
    "tt_clear_slot": {"en": "Clear slot", "tr": "Slotu temizle"},
    "select_files": {"en": "Select Files", "tr": "Dosyaları Seç"},
    "unknown_file_detected": {"en": "Unknown File Detected", "tr": "Bilinmeyen Dosya Tespiti"},
    "files_count": {"en": "{} file(s)", "tr": "{} dosya"},
    "all_files": {"en": "all", "tr": "tümü"},
    "unknown_slot_file": {"en": "slot unknown file:", "tr": "slotunda tanınmayan dosya:"},
    "select_partition": {"en": "Which partition to write to device?", "tr": "Cihazdaki hangi bölüme yazılsın?"},
    "language_changed_tr": {"en": "[SYS] Language set to Turkish", "tr": "[SYS] Dil Türkçe olarak ayarlandı"},
    "language_changed_en": {"en": "[SYS] Dil English olarak ayarlandı", "tr": "[SYS] Language set to English"},
    "theme_changed_dark": {"en": "[SYS] Theme set to Dark", "tr": "[SYS] Tema Koyu mod olarak ayarlandı"},
    "theme_changed_light": {"en": "[SYS] Tema Açık mod olarak ayarlandı", "tr": "[SYS] Theme set to Light"},
    "device_not_connected_error": {"en": "[WARN] No device connected!", "tr": "[UYARI] Cihaz bağlı değil!"},
    "magisk_title": {"en": "Magisk Patching", "tr": "Magisk Yamalama"},
    "magisk_desc": {"en": "Patch boot, init_boot, recovery, or AP package.", "tr": "boot, init_boot, recovery veya AP paketini yamalayın."},
    "magisk_file_placeholder": {"en": "No file selected...", "tr": "Dosya seçilmedi..."},
    "magisk_boot_placeholder": {"en": "Will be auto-downloaded...", "tr": "Otomatik indirilecek..."},
    "magisk_tab_title": {"en": "Magisk", "tr": "Magisk"},
    "magisk_image_label": {"en": "Image/AP:", "tr": "İmaj/AP:"},
    "magisk_apk_label": {"en": "Magisk APK:", "tr": "Magisk APK:"},
    "magisk_output_label": {"en": "Output:", "tr": "Çıktı:"},
    "magisk_arch_label": {"en": "Target arch:", "tr": "Hedef mimari:"},
    "magisk_auto_latest": {"en": "Empty = latest Magisk", "tr": "Boş = en güncel Magisk"},
    "magisk_keep_verity": {"en": "Keep Verity", "tr": "Verity Koru"},
    "magisk_keep_forceencrypt": {"en": "Keep Force Encrypt", "tr": "Şifrelemeyi Koru"},
    "magisk_patch_vbmeta": {"en": "Patch vbmeta", "tr": "vbmeta Yamala"},
    "magisk_recovery_mode": {"en": "Recovery Mode", "tr": "Recovery Modu"},
    "magisk_legacy_sar": {"en": "Legacy SAR", "tr": "Legacy SAR"},
    "magisk_start": {"en": "Patch Image", "tr": "İmajı Yamala"},
    "magisk_select_image": {"en": "Select image or AP package", "tr": "İmaj veya AP paketi seç"},
    "magisk_select_apk": {"en": "Select Magisk APK", "tr": "Magisk APK seç"},
    "magisk_select_output": {"en": "Select output folder", "tr": "Çıktı klasörü seç"},
    "magisk_need_image": {"en": "Please select a boot image first.", "tr": "Önce bir boot imajı seçin."},
    "magisk_started": {"en": "[MAGISK] Patch started...", "tr": "[MAGISK] Yamalama başladı..."},
    "magisk_finished": {"en": "[MAGISK] Patched image ready: {}", "tr": "[MAGISK] Yamalanmış imaj hazır: {}"},
    "pit_temp_deleted": {"en": "[PIT] Temporary PIT file removed.", "tr": "[PIT] Geçici PIT dosyası silindi."},
    "ana_skip_non_flash": {"en": "[ANA] Skip non-flash file: {}", "tr": "[ANA] Flashlanamaz dosya atlandı: {}"},
    "ana_auto_map": {"en": "[ANA] Auto map: {} -> {}", "tr": "[ANA] Otomatik eşleme: {} -> {}"},
    "ana_auto_fallback": {"en": "[ANA] Auto fallback: {} -> {} ({} default)", "tr": "[ANA] Otomatik varsayılan: {} -> {} ({} varsayılanı)"},
    "ana_skip_duplicate": {"en": "[ANA] Skip duplicate partition: {} ({})", "tr": "[ANA] Tekrarlanan bölüm atlandı: {} ({})"},
    "analysis_skip_unmapped": {"en": "[ANA] ✕ Unmapped file skipped: {} ({})", "tr": "[ANA] ✕ Eşleşmeyen dosya atlandı: {} ({})"},
    "analysis_skip_unsafe_no_pit": {"en": "[ANA] ✕ Unsafe partition skipped without PIT confirmation: {} -> {}", "tr": "[ANA] ✕ PIT doğrulaması olmadan riskli bölüm atlandı: {} -> {}"},
    "analysis_skip_not_in_pit": {"en": "[ANA] ✕ Partition not found in PIT, skipped: {} -> {}", "tr": "[ANA] ✕ Bölüm PIT içinde yok, atlandı: {} -> {}"},
    "pit_missing_title": {"en": "PIT Not Available", "tr": "PIT Dosyası Yok"},
    "pit_missing_confirm": {"en": "PIT file could not be obtained. Are you sure you want to continue without PIT?", "tr": "PIT dosyası alınamadı. PIT olmadan devam etmek istediğinize emin misiniz?"},
    "yes": {"en": "Yes", "tr": "Evet"},
    "no": {"en": "No", "tr": "Hayır"},
    "flash_process_started_pid": {"en": "[FLASH] Process started (pid={})", "tr": "[FLASH] Süreç başlatıldı (pid={})"},
    "sudo_invalid_password": {"en": "Stored sudo password is invalid. Reopen app and grant root again.", "tr": "Saklanan sudo parolası geçersiz. Uygulamayı yeniden açıp root yetkisini tekrar verin."},
    "sudo_session_expired": {"en": "Sudo session expired. Reopen app and grant root again.", "tr": "Sudo oturumu sona erdi. Uygulamayı yeniden açıp root yetkisini tekrar verin."},
    "cancel_reason_user": {"en": "user request", "tr": "kullanıcı isteği"},
    "cancel_reason_close": {"en": "window closed", "tr": "pencere kapandı"},
    "progress_ready": {"en": "Ready", "tr": "Hazır"},
    "home_csc_selected": {"en": "[CSC] HOME_CSC selected; user data should be preserved.", "tr": "[CSC] HOME_CSC seçildi; kullanıcı verileri korunmalıdır."},
}

# ─── Tema CSS'leri (aynı kaldı) ──────────────────────────────────────────────
STYLE_DARK = """
QMainWindow, QWidget#central { background-color: #0b0f14; }
QFrame#top_bar, QFrame#bottom_bar { background-color: #11161d; border: 1px solid #1e2733; border-radius: 4px; }
QGroupBox { background-color: #121820; border: 1px solid #1e2733; border-radius: 4px; margin-top: 8px; padding-top: 14px; color: #ffffff; font-family: 'Times New Roman'; font-size: 16px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #ffffff; }
QLabel { color: #ffffff; font-family: 'Times New Roman'; font-size: 15px; }
QLabel#title_lbl { color: #ffffff; background: transparent; border: none; font-size: 24px; }
QLabel#sub_lbl { color: #888888; background: transparent; border: none; font-size: 14px; }
QLineEdit { background-color: #0a0f14; border: 1px solid #1e2733; border-radius: 3px; color: #ffffff; font-family: 'Times New Roman'; font-size: 15px; padding: 3px 6px; }
QLineEdit:focus { border: 1px solid #ffffff; }
QPushButton { background-color: #333333; border: 1px solid #555555; border-radius: 3px; color: #ffffff; font-family: 'Times New Roman'; font-size: 15px; font-weight: bold; padding: 4px 10px; min-width: 60px; }
QPushButton:hover { background-color: #444444; border-color: #ffffff; color: #ffffff; }
QPushButton:disabled { background-color: #222222; color: #666666; border-color: #333333; }
QPushButton#btn_start { background-color: #005f8c; border: 2px solid #00b4d8; color: white; font-size: 15px; min-width: 22px; min-height: 22px; border-radius: 4px; }
QPushButton#btn_start:hover { background-color: #0077b6; }
QPushButton#btn_cancel, QPushButton#btn_reset { background-color: #5a3e1b; border: 2px solid #bd561d; color: white; font-size: 15px; min-width: 22px; min-height: 22px; border-radius: 4px; }
QPushButton#btn_cancel:hover, QPushButton#btn_reset:hover { background-color: #bd561d; }
QPushButton#btn_exit { background-color: #3d1a1a; border: 2px solid #8b1a1a; color: white; font-size: 15px; min-width: 22px; min-height: 22px; border-radius: 4px; transition: all 0.2s ease; }
QPushButton#btn_exit:hover { background-color: #8b1a1a; box-shadow: 0 0 8px rgba(139, 26, 26, 0.6); }
QPushButton#btn_theme, QPushButton#btn_lang { background: transparent; border: 1px solid #555555; border-radius: 3px; color: #ffffff; font-size: 15px; min-width: 22px; min-height: 22px; transition: all 0.2s ease; }
QPushButton#btn_theme:hover, QPushButton#btn_lang:hover { background: #333333; border-color: #ffffff; }
QPushButton#browse_btn, QPushButton#eye_btn, QPushButton#clear_btn { background-color: #333333; border: 1px solid #555555; border-radius: 5px; color: #ffffff; font-family: 'Times New Roman'; font-size: 15px; font-weight: bold; padding: 0px; min-width: 0px; width: 28px; height: 28px; transition: all 0.2s ease; }
QPushButton#browse_btn:hover, QPushButton#eye_btn:hover, QPushButton#clear_btn:hover { background-color: #444444; border-color: #ffffff; box-shadow: 0 0 6px rgba(0, 180, 216, 0.5); }
QTextEdit#log_screen { background-color: #0a0f14; border: 1px solid #1e2733; border-radius: 3px; color: #ffffff; font-family: 'Times New Roman'; font-size: 14px; }
QProgressBar {
    background-color: #0a0f14;
    border: 1px solid #1e2733;
    border-radius: 6px;
    color: #ffffff;
    font-size: 15px;
    font-weight: bold;
    height: 24px;
    text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0066cc, stop:1 #00ccff);
    border-radius: 5px;
    margin: 1px;
}
QTabWidget::pane { border: 1px solid #1e2733; border-radius: 3px; background-color: #121820; }
QTabBar::tab { background: #0a0f14; color: #ffffff; border: 1px solid #1e2733; padding: 6px 14px; font-weight: bold; font-family: 'Times New Roman'; font-size: 15px; border-top-left-radius: 3px; border-top-right-radius: 3px; }
QTabBar::tab:selected { background: #121820; color: #ffffff; border-bottom-color: #121820; }
QCheckBox { color: #ffffff; font-family: 'Times New Roman'; font-size: 15px; font-weight: bold; }
QCheckBox::indicator { width: 0px; height: 0px; border: none; }
QComboBox { background-color: #0a0f14; border: 1px solid #1e2733; border-radius: 3px; color: #ffffff; font-size: 15px; padding: 3px 6px; }
QComboBox QAbstractItemView { background-color: #1a1a1a; color: #ffffff; selection-background-color: #005f8c; }
QListWidget { background-color: #0a0f14; border: 1px solid #1e2733; color: #ffffff; font-size: 15px; }
"""

STYLE_LIGHT = """
QMainWindow, QWidget#central { background-color: #f2f5f7; }
QFrame#top_bar, QFrame#bottom_bar { background-color: #ffffff; border: 1px solid #d0d7de; border-radius: 4px; }
QGroupBox { background-color: #ffffff; border: 1px solid #d0d7de; border-radius: 4px; margin-top: 8px; padding-top: 14px; color: #000000; font-family: 'Times New Roman'; font-size: 16px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #000000; }
QLabel { color: #000000; font-family: 'Times New Roman'; font-size: 15px; }
QLabel#title_lbl { color: #000000; background: transparent; border: none; font-size: 24px; }
QLabel#sub_lbl { color: #666666; background: transparent; border: none; font-size: 14px; }
QLabel#pass_fail_label { font-size: 26px; font-weight: bold; }
QLineEdit { background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 3px; color: #000000; font-family: 'Times New Roman'; font-size: 15px; padding: 3px 6px; }
QLineEdit:focus { border: 1px solid #000000; background-color: #ffffff; }
QPushButton { background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 3px; color: #000000; font-family: 'Times New Roman'; font-size: 15px; font-weight: bold; padding: 4px 10px; min-width: 60px; }
QPushButton:hover { background-color: #f3f4f6; border-color: #000000; color: #000000; }
QPushButton:disabled { background-color: #eaebec; color: #8c959f; border-color: #d0d7de; }
QPushButton#btn_start { background-color: #1a7f37; border: 2px solid #116329; color: white; font-size: 15px; min-width: 22px; min-height: 22px; border-radius: 4px; }
QPushButton#btn_start:hover { background-color: #238636; }
QPushButton#btn_cancel, QPushButton#btn_reset { background-color: #bf8700; border: 2px solid #9a6700; color: white; font-size: 15px; min-width: 22px; min-height: 22px; border-radius: 4px; }
QPushButton#btn_cancel:hover, QPushButton#btn_reset:hover { background-color: #9a6700; }
QPushButton#btn_exit { background-color: #cf222e; border: 2px solid #a41e26; color: white; font-size: 15px; min-width: 22px; min-height: 22px; border-radius: 4px; transition: all 0.2s ease; }
QPushButton#btn_exit:hover { background-color: #a41e26; box-shadow: 0 0 8px rgba(164, 30, 38, 0.6); }
QPushButton#btn_theme, QPushButton#btn_lang { background: transparent; border: 1px solid #d0d7de; border-radius: 3px; color: #000000; font-size: 15px; min-width: 22px; min-height: 22px; transition: all 0.2s ease; }
QPushButton#btn_theme:hover, QPushButton#btn_lang:hover { background: #f3f4f6; border-color: #000000; }
QPushButton#browse_btn, QPushButton#eye_btn, QPushButton#clear_btn { background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 5px; color: #000000; font-family: 'Times New Roman'; font-size: 15px; font-weight: bold; padding: 0px; min-width: 0px; width: 28px; height: 28px; transition: all 0.2s ease; }
QPushButton#browse_btn:hover, QPushButton#eye_btn:hover, QPushButton#clear_btn:hover { background-color: #f3f4f6; border-color: #000000; box-shadow: 0 0 6px rgba(4, 81, 165, 0.4); }
QTextEdit#log_screen { background-color: #ffffff; border: 1px solid #d0d7de; border-radius: 3px; color: #000000; font-family: 'Times New Roman'; font-size: 14px; }
QProgressBar {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    color: #000000;
    font-size: 15px;
    font-weight: bold;
    height: 24px;
    text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3182ce, stop:1 #63b3ed);
    border-radius: 5px;
    margin: 1px;
}
QTabWidget::pane { border: 1px solid #d0d7de; border-radius: 3px; background-color: #ffffff; }
QTabBar::tab { background: #eaebec; color: #000000; border: 1px solid #d0d7de; padding: 6px 14px; font-weight: bold; font-family: 'Times New Roman'; font-size: 15px; border-top-left-radius: 3px; border-top-right-radius: 3px; }
QTabBar::tab:selected { background: #ffffff; color: #000000; border-bottom-color: #ffffff; }
QCheckBox { color: #000000; font-family: 'Times New Roman'; font-size: 15px; font-weight: bold; }
QCheckBox::indicator { width: 0px; height: 0px; border: none; }
QComboBox { background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 3px; color: #000000; font-family: 'Times New Roman'; font-size: 15px; padding: 3px 6px; }
QComboBox QAbstractItemView { background-color: #ffffff; color: #000000; selection-background-color: #0366d6; }
QListWidget { background-color: #ffffff; border: 1px solid #d0d7de; color: #000000; font-size: 15px; }
"""

# ─── SudoPasswordDialog ──────────────────────────────────────────────────────
class SudoPasswordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_lang = parent.current_lang
        self.selected_theme = get_system_theme()
        self._accepted_password = ""
        self.setWindowTitle("Authorization")
        self._normal_size = (280, 175)
        self._error_size = (280, 205)
        self.setFixedSize(*self._normal_size)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 7, 22, 8)
        layout.setSpacing(6)

        top_btn_layout = QHBoxLayout()
        top_btn_layout.setSpacing(10)
        top_btn_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_lang = QPushButton("English" if self.current_lang == "en" else "Türkçe")
        self.btn_lang.setObjectName("btn_lang")
        self.btn_lang.setFixedSize(116, 38)
        self.btn_lang.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_lang.setAutoDefault(False)
        self.btn_lang.setDefault(False)
        self.btn_lang.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lang.clicked.connect(self._toggle_language)
        top_btn_layout.addWidget(self.btn_lang)

        self.btn_theme = QPushButton(TRANSLATIONS["theme_toggle"].get(self.current_lang, "Dark / Light"))
        self.btn_theme.setObjectName("theme_btn")
        self.btn_theme.setFixedSize(116, 38)
        self.btn_theme.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_theme.setAutoDefault(False)
        self.btn_theme.setDefault(False)
        self.btn_theme.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.clicked.connect(self._toggle_theme)
        top_btn_layout.addWidget(self.btn_theme)
        layout.addLayout(top_btn_layout)

        icon_label = QLabel("")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedHeight(0)
        icon_label.setStyleSheet("background: transparent;")
        layout.addWidget(icon_label)
        self.icon_label = icon_label

        title = QLabel(TRANSLATIONS["root_required_title"].get(self.current_lang, "ROOT PERMISSION REQUIRED"))
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 12px; font-weight: 800; letter-spacing: 0.5px;")
        title.hide()
        layout.addWidget(title)
        self.title_label = title

        info = QLabel(TRANSLATIONS["root_required_message"].get(self.current_lang, "Password required for operations.\nYour password will be requested only once."))
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("font-size: 10px; opacity: 0.9; line-height: 1.3;")
        info.hide()
        layout.addWidget(info)
        self.info_label = info

        pw_row = QHBoxLayout()
        pw_row.setSpacing(8)

        pw_container = QFrame()
        pw_container.setObjectName("pw_container")
        pw_container.setFixedHeight(36)
        pw_layout = QHBoxLayout(pw_container)
        pw_layout.setContentsMargins(10, 4, 10, 4)
        pw_layout.setSpacing(0)
        self.pw_edit = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_edit.setPlaceholderText(TRANSLATIONS.get("password_placeholder", {}).get(self.current_lang, "Enter password..."))
        self.pw_edit.setMinimumHeight(24)
        self.pw_edit.returnPressed.connect(self._on_ok_clicked)
        self.pw_edit.setStyleSheet("border: none; background: transparent;")
        pw_layout.addWidget(self.pw_edit)

        self.btn_toggle_pw = QPushButton("👁")
        self.btn_toggle_pw.setObjectName("btn_toggle_pw")
        self.btn_toggle_pw.setFixedSize(36, 36)
        self.btn_toggle_pw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_pw.clicked.connect(self._toggle_password_visibility)
        self.btn_toggle_pw.setToolTip("Show/Hide password")
        self.btn_toggle_pw.setText("")
        self._pw_visible = False
        pw_row.addWidget(pw_container, 1)
        pw_row.addWidget(self.btn_toggle_pw, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(pw_row)

        self.error_label = QLabel("")
        self.error_label.setObjectName("error_label")
        self.error_label.setProperty("error", False)
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label.setFixedHeight(30)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        layout.addSpacing(1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_cancel = QPushButton(TRANSLATIONS["cancel"].get(self.current_lang, "CANCEL"))
        btn_cancel.setObjectName("cancel")
        btn_cancel.setFixedHeight(38)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        self.btn_cancel = btn_cancel
        btn_ok = QPushButton(TRANSLATIONS["ok"].get(self.current_lang, "OK"))
        btn_ok.setObjectName("btn_ok")
        btn_ok.setFixedHeight(38)
        btn_ok.clicked.connect(self._on_ok_clicked)
        btn_layout.addWidget(btn_ok)
        self.btn_ok = btn_ok
        layout.addLayout(btn_layout)
        self._update_theme_ui()
        self._update_eye_icon()

        QTimer.singleShot(100, lambda: self.pw_edit.setFocus())

    def _toggle_password_visibility(self):
        self._pw_visible = not self._pw_visible
        if self._pw_visible:
            self.pw_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._update_eye_icon()

    def _update_eye_icon(self):
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.selected_theme:
            stroke = QColor("#f2f4f8")
            pupil = QColor("#f2f4f8")
            slash = QColor("#ff7f9f")
        else:
            stroke = QColor("#1f2538")
            pupil = QColor("#1f2538")
            slash = QColor("#b0304a")
        pen = painter.pen()
        pen.setColor(stroke)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._pw_visible:
            painter.drawEllipse(2, 5, 14, 8)
            painter.setBrush(pupil)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(7, 7, 4, 4)
        else:
            painter.drawArc(3, 8, 12, 6, 0, 180 * 16)
            painter.drawLine(4, 12, 2, 14)
            painter.drawLine(7, 14, 7, 16)
            painter.drawLine(11, 14, 11, 16)
            painter.drawLine(14, 12, 16, 14)
            pen = painter.pen()
            pen.setColor(slash)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(4, 5, 14, 5)
        painter.end()
        self.btn_toggle_pw.setIcon(QIcon(pixmap))
        self.btn_toggle_pw.setIconSize(pixmap.size())

    def _show_error(self, message):
        self.setFixedSize(*self._error_size)
        self.error_label.setProperty("error", True)
        self.error_label.style().unpolish(self.error_label)
        self.error_label.style().polish(self.error_label)
        self.error_label.setText(message)
        self.error_label.show()
        QTimer.singleShot(3000, self._hide_error)

    def _hide_error(self):
        self.error_label.hide()
        self.error_label.clear()
        self.error_label.setProperty("error", False)
        self.error_label.style().unpolish(self.error_label)
        self.error_label.style().polish(self.error_label)
        self.setFixedSize(*self._normal_size)

    def _on_ok_clicked(self):
        password = self.pw_edit.text()
        if not password:
            self._show_error(TRANSLATIONS.get("password_empty_error", {}).get(self.current_lang, "⚠ Password cannot be empty!"))
            self.pw_edit.setFocus()
            return
        try:
            proc = subprocess.run(
                ["sudo", "-S", "-k", "true"],
                input=password + "\n",
                text=True,
                capture_output=True,
                timeout=10
            )
        except Exception as e:
            self._show_error(TRANSLATIONS["sudo_failed"].get(self.current_lang, "Cannot run sudo:{}").format(e))
            self.pw_edit.setFocus()
            return
        if proc.returncode != 0:
            self._show_error(TRANSLATIONS.get("password_wrong_error", {}).get(self.current_lang, "⚠ Wrong password. Please try again."))
            self.pw_edit.selectAll()
            self.pw_edit.setFocus()
            return
        self._accepted_password = password
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def _update_theme_ui(self):
        if self.selected_theme:
            self.btn_theme.setText(TRANSLATIONS["theme_toggle"].get(self.current_lang, "Dark / Light"))
            self.setStyleSheet("""
                QDialog {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #171b2d, stop:1 #252b43);
                    border-radius: 0px;
                    border: 1px solid rgba(255, 255, 255, 0.22);
                }
                QLabel {
                    color: rgba(255, 255, 255, 0.95);
                    font-family: 'Noto Sans', sans-serif;
                    font-size: 12px;
                    background: transparent;
                }
                QLabel#title {
                    color: #f0f2f4;
                    font-size: 14px;
                    font-weight: 900;
                    letter-spacing: 1px;
                    background: transparent;
                }
                QLabel#error_label {
                    color: transparent;
                    font-size: 10px;
                    font-weight: bold;
                    padding: 6px 8px;
                    background: transparent;
                    border-radius: 8px;
                    border: 1px solid transparent;
                }
                QLabel#error_label[error="true"] {
                    color: #ff8a80;
                    background: rgba(110, 20, 30, 0.35);
                    border: 1px solid rgba(255, 138, 128, 0.26);
                }
                QFrame#pw_container {
                    background: rgba(0, 0, 0, 0.18);
                    border-radius: 3px;
                    border: 1px solid rgba(255, 255, 255, 0.55);
                }
                QLineEdit {
                    background: transparent;
                    border: none;
                    color: #ffffff;
                    font-family: 'Noto Sans', sans-serif;
                    font-size: 12px;
                    padding: 4px 0;
                }
                QLineEdit::placeholder {
                    color: rgba(255, 255, 255, 0.45);
                }
                QPushButton {
                    font-family: 'Noto Sans', sans-serif;
                    font-weight: 700;
                    border-radius: 20px;
                    padding: 12px 22px;
                    border: none;
                }
                QPushButton#btn_lang {
                    background: rgba(10, 16, 28, 0.48);
                    color: #f3f3f4;
                    font-size: 11px;
                    font-weight: 700;
                    border: 2px solid rgba(232, 232, 232, 0.9);
                    padding: 0 14px;
                    border-radius: 19px;
                    text-align: center;
                }
                QPushButton#btn_lang:hover {
                    background: rgba(30, 38, 58, 0.85);
                }
                QPushButton#btn_lang:focus {
                    outline: none;
                    border: 2px solid rgba(232, 232, 232, 0.9);
                }
                QPushButton#theme_btn {
                    background: rgba(10, 16, 28, 0.48);
                    color: #f3f3f4;
                    font-size: 11px;
                    font-weight: 700;
                    border: 2px solid rgba(232, 232, 232, 0.9);
                    padding: 0 14px;
                    border-radius: 19px;
                }
                QPushButton#theme_btn:hover {
                    background: rgba(30, 38, 58, 0.85);
                }
                QPushButton#theme_btn:focus {
                    outline: none;
                    border: 2px solid rgba(232, 232, 232, 0.9);
                }
                QPushButton#btn_toggle_pw {
                    background: rgba(255, 255, 255, 0.14);
                    color: rgba(255, 255, 255, 0.98);
                    font-size: 14px;
                    font-weight: 700;
                    border-radius: 3px;
                    border: 1px solid rgba(255, 255, 255, 0.55);
                    padding: 0;
                }
                QPushButton#btn_toggle_pw:hover {
                    background: rgba(255, 255, 255, 0.24);
                    color: #ffffff;
                }
                QPushButton#cancel {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5c1e36, stop:1 #6e263f);
                    color: #ff4f7f;
                    border: 1px solid rgba(255, 120, 150, 0.35);
                    font-size: 11px;
                    font-weight: 800;
                }
                QPushButton#cancel:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6a2940, stop:1 #7f324c);
                }
                QPushButton#btn_ok {
                    background: #efefef;
                    color: #2c2e35;
                    font-size: 11px;
                    font-weight: 800;
                    border: 1px solid rgba(255, 255, 255, 0.7);
                }
                QPushButton#btn_ok:hover {
                    background: #ffffff;
                }
            """)
            top_button_style = """
                background: rgba(10, 16, 28, 0.48);
                color: #f3f3f4;
                font-family: 'Noto Sans', sans-serif;
                font-size: 11px;
                font-weight: 700;
                border: 2px solid rgba(232, 232, 232, 0.9);
                padding: 0 14px;
                border-radius: 0px;
                text-align: center;
            """
        else:
            self.btn_theme.setText(TRANSLATIONS["theme_toggle"].get(self.current_lang, "Dark / Light"))
            self.setStyleSheet("""
                QDialog {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #eef2f7, stop:1 #dde5ef);
                    border-radius: 0px;
                    border: 1px solid rgba(25, 35, 50, 0.25);
                }
                QLabel {
                    color: rgba(0, 0, 0, 0.85);
                    font-family: 'Noto Sans', sans-serif;
                    font-size: 12px;
                    background: transparent;
                }
                QLabel#title {
                    color: #1f2538;
                    font-size: 14px;
                    font-weight: 900;
                    letter-spacing: 1px;
                    background: transparent;
                }
                QLabel#error_label {
                    color: transparent;
                    font-size: 10px;
                    font-weight: bold;
                    padding: 6px 8px;
                    background: transparent;
                    border-radius: 8px;
                    border: 1px solid transparent;
                }
                QLabel#error_label[error="true"] {
                    color: #d32f2f;
                    background: rgba(211, 47, 47, 0.1);
                    border: 1px solid rgba(211, 47, 47, 0.3);
                }
                QFrame#pw_container {
                    background: rgba(255, 255, 255, 0.72);
                    border-radius: 3px;
                    border: 1px solid rgba(31, 37, 56, 0.45);
                }
                QLineEdit {
                    background: transparent;
                    border: none;
                    color: #1f2538;
                    font-family: 'Noto Sans', sans-serif;
                    font-size: 12px;
                    padding: 4px 0;
                }
                QLineEdit::placeholder {
                    color: rgba(31, 37, 56, 0.45);
                }
                QPushButton {
                    font-family: 'Noto Sans', sans-serif;
                    font-weight: 700;
                    border-radius: 20px;
                    padding: 12px 22px;
                    border: none;
                }
                QPushButton#btn_lang {
                    background: rgba(255, 255, 255, 0.74);
                    color: #1f2538;
                    font-size: 11px;
                    font-weight: 700;
                    border: 2px solid rgba(31, 37, 56, 0.5);
                    padding: 0 14px;
                    border-radius: 19px;
                    text-align: center;
                }
                QPushButton#btn_lang:hover {
                    background: #ffffff;
                }
                QPushButton#btn_lang:focus {
                    outline: none;
                    border: 2px solid rgba(31, 37, 56, 0.5);
                }
                QPushButton#theme_btn {
                    background: rgba(255, 255, 255, 0.74);
                    color: #1f2538;
                    font-size: 11px;
                    font-weight: 700;
                    border: 2px solid rgba(31, 37, 56, 0.5);
                    padding: 0 14px;
                    border-radius: 19px;
                }
                QPushButton#theme_btn:hover {
                    background: #ffffff;
                }
                QPushButton#theme_btn:focus {
                    outline: none;
                    border: 2px solid rgba(31, 37, 56, 0.5);
                }
                QPushButton#btn_toggle_pw {
                    background: rgba(31, 37, 56, 0.12);
                    color: rgba(31, 37, 56, 0.95);
                    font-size: 14px;
                    font-weight: 700;
                    border-radius: 3px;
                    border: 1px solid rgba(31, 37, 56, 0.45);
                    padding: 0;
                }
                QPushButton#btn_toggle_pw:hover {
                    background: rgba(31, 37, 56, 0.2);
                    color: #1f2538;
                }
                QPushButton#cancel {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8f2d44, stop:1 #ad3a58);
                    color: #ffd6df;
                    border: 1px solid rgba(121, 18, 47, 0.45);
                    font-size: 11px;
                    font-weight: 800;
                }
                QPushButton#cancel:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9d344d, stop:1 #be4765);
                }
                QPushButton#btn_ok {
                    background: #f8f8f8;
                    color: #20252f;
                    font-size: 11px;
                    font-weight: 800;
                    border: 1px solid rgba(31, 37, 56, 0.2);
                }
                QPushButton#btn_ok:hover {
                    background: #ffffff;
                }
            """)
            top_button_style = """
                background: rgba(255, 255, 255, 0.74);
                color: #1f2538;
                font-family: 'Noto Sans', sans-serif;
                font-size: 11px;
                font-weight: 700;
                border: 2px solid rgba(31, 37, 56, 0.5);
                padding: 0 14px;
                border-radius: 0px;
                text-align: center;
            """
        self.btn_lang.setStyleSheet(top_button_style)
        self.btn_theme.setStyleSheet(top_button_style)
        self._update_eye_icon()
    
    def _toggle_theme(self):
        self.selected_theme = not self.selected_theme
        self._update_theme_ui()

    def _toggle_language(self):
        if self.current_lang == "en":
            self.current_lang = "tr"
        else:
            self.current_lang = "en"
        self.btn_lang.setText("English" if self.current_lang == "en" else "Türkçe")
        self.title_label.setText(TRANSLATIONS["root_required_title"].get(self.current_lang, "ROOT PERMISSION REQUIRED"))
        self.info_label.setText(TRANSLATIONS["root_required_message"].get(self.current_lang, "Password required for operations.\nYour password will be requested only once."))
        self.pw_edit.setPlaceholderText(TRANSLATIONS.get("password_placeholder", {}).get(self.current_lang, "Enter password..."))
        self.btn_theme.setText(TRANSLATIONS["theme_toggle"].get(self.current_lang, "Dark / Light"))
        self.btn_cancel.setText(TRANSLATIONS.get("cancel", {}).get(self.current_lang, "CANCEL"))
        self.btn_ok.setText(TRANSLATIONS.get("ok", {}).get(self.current_lang, "OK"))
        self._update_theme_ui()

    def get_theme(self):
        return self.selected_theme

    def get_language(self):
        return self.current_lang
    
    def get_password(self):
        pw = self._accepted_password or self.pw_edit.text()
        self._accepted_password = ""
        self.pw_edit.clear()
        return pw

# ─── USBMonitor (DÜZELTİLDİ) ─────────────────────────────────────────────────
class USBMonitor(QThread):
    device_connected = pyqtSignal(str, str, str)  # port, vendor, product
    device_disconnected = pyqtSignal()
    log_message = pyqtSignal(str)
    
    def __init__(self, lang="en"):
        super().__init__()
        self.lang = lang if lang in LANGUAGES else "en"
        self._running = True
        self._was_connected = False

    def set_language(self, lang):
        self.lang = lang if lang in LANGUAGES else "en"
        
    def run(self):
        self.log_message.emit(tr("usb_monitor_started", self.lang, "[USB] Monitor started, waiting for Download Mode..."))
        while self._running:
            try:
                device_info = self._check_device()
                
                if device_info and not self._was_connected:
                    self._was_connected = True
                    msg = tr("usb_device_found", self.lang, "[USB] Device connected ({})").format(device_info[0])
                    self.log_message.emit(msg)
                    self.device_connected.emit(*device_info)
                elif not device_info and self._was_connected:
                    self._was_connected = False
                    self.log_message.emit(tr("usb_device_disconnected", self.lang, "[USB] Device disconnected!"))
                    self.device_disconnected.emit()
                    
            except Exception as e:
                msg = tr("usb_monitor_error", self.lang, "[USB] Monitor error: {}").format(str(e))
                self.log_message.emit(msg)
            
            # Her 0.5 saniyede bir kontrol et (hızlı algılama için)
            for _ in range(5):  # 5 x 100ms = 0.5 saniye
                if not self._running:
                    break
                time.sleep(0.1)
    
    def _check_device(self):
        """Cihazı sadece lsusb ile kontrol et - heimdall reboot'a neden olabilir"""
        
        # Sadece lsusb kullan - heimdall reboot'a neden olabilir
        DOWNLOAD_MODE_PRODUCT_IDS = ["685d", "685c", "6601", "62c6", "6860", "6865", "6866"]
        try:
            result = subprocess.run(
                ["lsusb"], 
                capture_output=True, 
                text=True, 
                timeout=2
            )
            
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "04e8" in line.lower():
                        for pid in DOWNLOAD_MODE_PRODUCT_IDS:
                            if pid in line.lower():
                                match = re.search(r"Bus\s+(\d+)\s+Device\s+(\d+)", line)
                                port = f"USB:{match.group(1)}:{match.group(2)}" if match else "USB:???"
                                return (port, "Samsung", f"Download Mode (PID:{pid})")
        except Exception:
            pass
        
        return None
    
    def stop(self):
        self._running = False
        self._was_connected = False

# ─── Diğer sınıflar (PITDownloader, FileAnalyzer, FlashWorker, vs.) aynı kaldı ───
# ... [Önceki kodda yer alan tüm diğer sınıflar burada aynen korunuyor] ...

class PITDownloader(QThread):
    pit_ready = pyqtSignal(str)
    pit_failed = pyqtSignal(str, str)
    log_message = pyqtSignal(str)
    _lock = QMutex()
    def __init__(self, lang="en"):
        super().__init__()
        self.lang = lang if lang in LANGUAGES else "en"
        self._abort = False

    def set_language(self, lang):
        self.lang = lang if lang in LANGUAGES else "en"

    def abort(self):
        with QMutexLocker(self._lock):
            self._abort = True
    def run(self):
        os.makedirs(TMP_DIR, mode=0o700, exist_ok=True)
        pit_path = os.path.join(TMP_DIR, "device.pit")
        
        self.log_message.emit(tr("pit_downloading", self.lang, "[PIT] Downloading PIT file..."))
        self.log_message.emit(tr("pit_requesting", self.lang, "[PIT] Requesting PIT from device..."))
        
        try:
            result = subprocess.run(
                ["heimdall", "download-pit", "--output", pit_path, "--no-reboot"],
                capture_output=True, text=True, timeout=30
            )
            
            with QMutexLocker(self._lock):
                if self._abort:
                    return
            
            if os.path.exists(pit_path) and os.path.getsize(pit_path) > 0:
                msg = tr("pit_downloaded", self.lang, "[PIT] PIT file received ({} bytes)").format(os.path.getsize(pit_path))
                self.log_message.emit(msg)
                self.pit_ready.emit(pit_path)
            else:
                self.log_message.emit(tr("pit_retry", self.lang, "[PIT] Device not responding, retrying..."))
                time.sleep(1)
                
                # Cihaz bağlantısını kontrol et
                try:
                    result_check = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=2)
                    if "04e8" not in result_check.stdout.lower():
                        self.pit_failed.emit("pit_connection_lost", "")
                        return
                except Exception as e:
                    msg = tr("pit_usb_check_error", self.lang, "[PIT] USB check error: {}").format(e)
                    self.log_message.emit(msg)
                
                result2 = subprocess.run(
                    ["heimdall", "download-pit", "--output", pit_path, "--no-reboot"],
                    capture_output=True, text=True, timeout=30
                )
                if os.path.exists(pit_path) and os.path.getsize(pit_path) > 0:
                    self.pit_ready.emit(pit_path)
                else:
                    self.pit_failed.emit("pit_failed", str(result.returncode))
                    
        except subprocess.TimeoutExpired:
            with QMutexLocker(self._lock):
                if self._abort:
                    return
            self.pit_failed.emit("pit_timeout", "")
        except Exception as e:
            with QMutexLocker(self._lock):
                if self._abort:
                    return
            self.pit_failed.emit("pit_generic_error", str(e))

class FileAnalyzer(QThread):
    analysis_done = pyqtSignal(dict)
    error = pyqtSignal(str)
    log_message = pyqtSignal(str)
    _lock = QMutex()
    def __init__(self, slot_files: dict, filter_members: dict = None, lang: str = "en"):
        super().__init__()
        self.slot_files = slot_files
        self.filter_members = filter_members or {}
        self.lang = lang if lang in LANGUAGES else "en"
        self._abort = False
    def abort(self):
        with QMutexLocker(self._lock):
            self._abort = True
    def run(self):
        results = {}
        work_dir = os.path.join(TMP_DIR, "extracted")
        os.makedirs(work_dir, mode=0o700, exist_ok=True)
        for slot, tar_path in self.slot_files.items():
            with QMutexLocker(self._lock):
                if self._abort:
                    return
            if not tar_path: continue
            msg = tr("analyzing_slot", self.lang, "[ANA] Processing {}: {}").format(slot, os.path.basename(tar_path))
            self.log_message.emit(msg)
            try:
                allowed = self.filter_members.get(slot, None)
                files = self._analyze_tar(slot, tar_path, work_dir, allowed)
                results[slot] = files
            except Exception as e:
                msg = tr("analyze_error", self.lang, "{} error: {}").format(slot, e)
                self.error.emit(msg)
                return
        with QMutexLocker(self._lock):
            if self._abort:
                return
        self.analysis_done.emit(results)

    def _analyze_tar(self, slot, tar_path, work_dir, allowed_names):
        slot_dir = os.path.join(work_dir, slot)
        os.makedirs(slot_dir, mode=0o700, exist_ok=True)
        files_info = []
        direct_name = os.path.basename(tar_path)
        if self._is_flashable_file(direct_name):
            extracted_path = tar_path
            original_name = direct_name
            if direct_name.endswith(".lz4"):
                copied_path = os.path.join(slot_dir, direct_name)
                shutil.copyfile(tar_path, copied_path)
                extracted_path = self._decompress_lz4(copied_path, slot_dir)
                original_name = os.path.basename(extracted_path)
            clean_name = self._clean_filename(original_name)
            partition, conf, desc = self._resolve_partition(slot, clean_name)
            return [{"original": original_name, "clean": clean_name, "path": extracted_path, "partition": partition, "confidence": conf}]
        list_cmd = ["tar", "-tf", tar_path]
        result = subprocess.run(list_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"Tar liste hatası: {result.stderr or 'Bilinmeyen hata'}")
        all_members = [m for m in result.stdout.splitlines() if m and not m.endswith('/')]
        if allowed_names is not None:
            members = [m for m in all_members if os.path.basename(m) in set(allowed_names)]
        else:
            members = all_members
        for member in members:
            with QMutexLocker(self._lock):
                if self._abort:
                    raise Exception("İptal edildi")
            original_name = os.path.basename(member)
            if not self._is_flashable_file(original_name):
                self.log_message.emit(tr("ana_skip_non_flash", self.lang, "[ANA] Skip non-flash file: {}").format(original_name))
                continue
            normalized_member = os.path.normpath(member)
            if os.path.isabs(normalized_member) or normalized_member.startswith("..") or "/../" in normalized_member.replace("\\", "/"):
                raise ValueError(f"Tar üyesi güvenli değil: {member}")
            
            # Dizin yapısını oluştur (iç içe dizinler için)
            member_dir = os.path.dirname(normalized_member)
            if member_dir:
                full_dir = os.path.join(slot_dir, member_dir)
                os.makedirs(full_dir, mode=0o700, exist_ok=True)
            
            extract_result = subprocess.run(["tar", "-xf", tar_path, "-C", slot_dir, member], capture_output=True, text=True)
            if extract_result.returncode != 0:
                raise ValueError(f"Tar çıkarma hatası ({member}): {extract_result.stderr or 'Bilinmeyen hata'}")
            
            # Dizin yapısını koru - taşıma yapma
            extracted_path = os.path.abspath(os.path.join(slot_dir, normalized_member))
            slot_root = os.path.abspath(slot_dir) + os.sep
            if not (extracted_path + os.sep).startswith(slot_root) and extracted_path != os.path.abspath(slot_dir):
                raise ValueError(f"Tar üyesi hedef klasör dışına çıkıyor: {member}")
            
            # LZ4 decompress işlemi
            if original_name.endswith(".lz4"):
                extracted_path = self._decompress_lz4(extracted_path, slot_dir)
                original_name = os.path.basename(extracted_path)
            
            clean_name = self._clean_filename(original_name)
            # Dosyayı temizlenmiş ismiyle yeniden adlandır (aynı dizinde)
            if clean_name != original_name:
                final_path = os.path.join(os.path.dirname(extracted_path), clean_name)
                if extracted_path != final_path:
                    shutil.move(extracted_path, final_path)
                extracted_path = final_path
            else:
                final_path = extracted_path
                
            partition, conf, desc = self._resolve_partition(slot, clean_name)
            files_info.append({"original": original_name, "clean": clean_name, "path": final_path, "partition": partition, "confidence": conf})
        return files_info

    def _is_flashable_file(self, filename):
        lower = filename.lower()
        return lower.endswith((".img", ".img.ext4", ".ext4", ".bin", ".mbn", ".elf", ".sparse", ".lz4"))

    def _resolve_partition(self, slot, filename):
        stripped = filename.lower()
        for suf in [".img", ".bin", ".mbn", ".elf", ".lz4", ".ext4", ".sparse"]:
            stripped = stripped.replace(suf, "")
        best_key, best_prio = None, -1
        PARTITION_DB = {
            "sboot": ("SBOOT", "Bootloader", 20), "bootloader": ("BOOTLOADER", "Bootloader", 20),
            "aboot": ("ABOOT", "Bootloader", 18), "xbl": ("XBL", "Bootloader", 18),
            "abl": ("ABL", "Bootloader", 18), "tz": ("TZ", "TrustZone", 18),
            "hyp": ("HYP", "Hypervisor", 18), "keymaster": ("KEYMASTER", "Keymaster", 18),
            "cmnlib64": ("CMNLIB64", "CMNLIB64", 18), "cmnlib": ("CMNLIB", "CMNLIB", 17),
            "devcfg": ("DEVCFG", "DEVCFG", 18), "qupfw": ("QUPFW", "QUPFW", 18),
            "aop": ("AOP", "AOP", 18), "lksecapp": ("LKSECAPP", "LKSECAPP", 18),
            "vendor_boot": ("VENDOR_BOOT", "Vendor Boot", 16), "init_boot": ("INIT_BOOT", "Init Boot", 16),
            "boot": ("BOOT", "Kernel", 10), "recovery": ("RECOVERY", "Recovery", 10),
            "system_ext": ("SYSTEM_EXT", "System Ext", 16), "system": ("SYSTEM", "System", 10),
            "vendor": ("VENDOR", "Vendor", 10), "product": ("PRODUCT", "Product", 10),
            "odm": ("ODM", "ODM", 10), "userdata": ("USERDATA", "Data", 10),
            "modem": ("CP", "Modem", 10), "radio": ("RADIO", "Radio", 10),
            "bluetooth": ("BLUETOOTH", "Bluetooth", 10), "dsp": ("DSP", "DSP", 10),
            "csc": ("CSC", "CSC", 10), "cache": ("CACHE", "Cache", 10),
            "hidden": ("HIDDEN", "Hidden", 10), "omr": ("OMR", "OMR", 10),
            "prism": ("PRISM", "Prism", 10), "optics": ("OPTICS", "Optics", 10),
            "vbmeta_system": ("VBMETA_SYSTEM", "VBMETA System", 16),
            "vbmeta_vendor": ("VBMETA_VENDOR", "VBMETA Vendor", 16),
            "vbmeta": ("VBMETA", "VBMETA", 10), "super": ("SUPER", "Super", 10),
            "dtbo": ("DTBO", "DTBO", 10), "up_param": ("UP_PARAM", "Param", 12),
            "param": ("PARAM", "Param", 10), "keystorage": ("KEYSTORAGE", "Keystorage", 10),
            "metadata": ("METADATA", "Metadata", 10), "persist": ("PERSIST", "Persist", 10),
            "efs": ("EFS", "EFS", 10), "sec_efs": ("SEC_EFS", "SEC EFS", 12),
            "cm": ("CM", "CM", 10), "uh": ("UH", "UH", 10), "dt": ("DT", "DT", 9)
        }
        SLOT_DEFAULTS = {"BL": ("SBOOT", "Bootloader"), "CP": ("CP", "Modem"), "CSC": ("CSC", "CSC")}
        for key, (part, desc, prio) in PARTITION_DB.items():
            if key in stripped and prio > best_prio:
                best_key, best_prio = key, prio
        if best_key: return PARTITION_DB[best_key][0], "exact", PARTITION_DB[best_key][1]
        # Never force AP unknown files to SYSTEM.
        if slot == "AP":
            return "UNKNOWN", "unmapped", "Unmapped"
        dp, dd = SLOT_DEFAULTS.get(slot, (slot, slot))
        return dp, "default", dd
    def _decompress_lz4(self, lz4_path, out_dir):
        out_path = os.path.join(out_dir, os.path.basename(lz4_path)[:-4])
        result = subprocess.run(["lz4", "-d", "-f", lz4_path, out_path], capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"LZ4 açma hatası: {result.stderr or 'Bilinmeyen hata'}")
        if os.path.exists(lz4_path):
            try:
                os.remove(lz4_path)
            except OSError:
                pass
        return out_path
    def _clean_filename(self, name):
        while name.count(".img") > 1: name = name[:name.rindex(".img")] + ".img"
        return name

class FlashWorker(QThread):
    progress = pyqtSignal(int)
    partition_progress = pyqtSignal(str, int, int)
    log_message = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    def __init__(self, flash_args: list, pit_path: str, auto_reboot: bool, repartition: bool, dry_run: bool = False, sudo_password: str = "", lang: str = "en"):
        super().__init__()
        self.flash_args = flash_args
        self.pit_path = pit_path
        self.auto_reboot = auto_reboot
        self.repartition = repartition
        self.dry_run = dry_run
        self.sudo_password = sudo_password or ""
        self.lang = lang if lang in LANGUAGES else "en"
        self._process = None
        self._aborted = False
        self._abort_reason = "unknown"
        self._start_time = None
        self._partition_files = {}
        self._partition_order = self._extract_partition_order()

    def _extract_partition_order(self):
        order = []
        for i in range(0, len(self.flash_args), 2):
            arg = self.flash_args[i]
            if isinstance(arg, str) and arg.startswith("--"):
                partition = arg[2:].upper()
                order.append(partition)
                if i + 1 < len(self.flash_args):
                    self._partition_files[partition] = self.flash_args[i + 1]
        return order

    def _partition_index(self, partition):
        partition = (partition or "").upper()
        try:
            return self._partition_order.index(partition)
        except ValueError:
            return -1

    def _overall_progress(self, partition, percent):
        total = len(self._partition_order)
        if total <= 0:
            return max(0, min(100, percent))
        idx = self._partition_index(partition)
        if idx < 0:
            return max(0, min(100, percent))
        return max(0, min(100, int(((idx + (percent / 100.0)) / total) * 100)))

    def _emit_partition_progress(self, partition, percent):
        overall = self._overall_progress(partition, percent)
        self.partition_progress.emit(partition.upper(), max(0, min(100, percent)), overall)

    def _estimated_partition_percent(self, partition, started_at):
        path = self._partition_files.get((partition or "").upper(), "")
        try:
            size_mb = max(1.0, os.path.getsize(path) / (1024 * 1024))
        except OSError:
            size_mb = 128.0
        expected_seconds = max(8.0, min(360.0, size_mb / 35.0))
        elapsed = max(0.0, time.time() - started_at)
        return min(95, int((elapsed / expected_seconds) * 100))

    def abort(self, reason="unknown"):
        self._aborted = True
        self._abort_reason = reason or "unknown"
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(3)
                if self._process.poll() is None:
                    self._process.kill()
                    self._process.wait(1)
                reason_text = tr("cancel_reason_user", self.lang, "user request") if self._abort_reason == "user_cancel" else tr("cancel_reason_close", self.lang, "window closed") if self._abort_reason == "window_close" else self._abort_reason
                self.log_message.emit(f"{tr('flash_cancelled', self.lang, '[FLASH] Flashing cancelled.')} (reason: {reason_text})")
                self.finished.emit(False, f"{tr('flash_cancelled_by_user', self.lang, 'Cancelled by user')} ({reason_text})")
            except Exception as e:
                msg = tr("flash_cancel_error", self.lang, "[FLASH] Cancel error: {}").format(str(e))
                self.log_message.emit(msg)
                self.finished.emit(False, tr("flash_cancel_error", self.lang, "Cancel error: {}").format(str(e)))
    def run(self):
        self._start_time = time.time()
        use_password = bool(self.sudo_password)
        cmd = ["sudo", "-S", "-p", "", "heimdall", "flash"] if use_password else ["sudo", "-n", "heimdall", "flash"]
        if self.repartition: cmd.append("--repartition")
        if not self.auto_reboot: cmd.append("--no-reboot")
        if self.pit_path: cmd += ["--pit", self.pit_path]
        cmd += self.flash_args
        self.log_message.emit(f"[CMD] {format_shell_cmd(cmd)}")
        if self.dry_run:
            self.log_message.emit(tr("flash_dry_run_complete", self.lang, "[DRY-RUN] Dry run completed."))
            self.finished.emit(True, tr("flash_dry_run_finished", self.lang, "Dry run completed"))
            return
        if use_password:
            sudo_check = subprocess.run(
                ["sudo", "-S", "-p", "", "-k", "true"],
                input=self.sudo_password + "\n",
                capture_output=True,
                text=True
            )
            if sudo_check.returncode != 0:
                self.finished.emit(False, tr("sudo_invalid_password", self.lang, "Stored sudo password is invalid. Reopen app and grant root again."))
                return
        else:
            sudo_check = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
            if sudo_check.returncode != 0:
                self.finished.emit(False, tr("sudo_session_expired", self.lang, "Sudo session expired. Reopen app and grant root again."))
                return
        self.log_message.emit(tr("flash_started", self.lang, "[FLASH] Flashing started..."))
        self.progress.emit(-1)
        try:
            master_fd, slave_fd = pty.openpty()
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if use_password else None,
                stdout=slave_fd,
                stderr=slave_fd,
                text=True,
                bufsize=1
            )
            os.close(slave_fd)
            if use_password and self._process.stdin:
                self._process.stdin.write(self.sudo_password + "\n")
                self._process.stdin.flush()
                self._process.stdin.close()
            self.log_message.emit(tr("flash_process_started_pid", self.lang, "[FLASH] Process started (pid={})").format(self._process.pid))
            any_percent = False
            current_partition = ""
            current_partition_started_at = None
            current_partition_has_real_percent = False
            current_partition_last_percent = 0

            def process_heimdall_output(line):
                nonlocal any_percent, current_partition, current_partition_started_at, current_partition_has_real_percent, current_partition_last_percent
                line = line.strip()
                if not line:
                    return
                upload_match = re.search(r"\bUploading\s+([A-Za-z0-9_]+)\b", line)
                if upload_match:
                    current_partition = upload_match.group(1).upper()
                    current_partition_started_at = time.time()
                    current_partition_has_real_percent = False
                    current_partition_last_percent = 0
                    self._emit_partition_progress(current_partition, 0)
                    self.log_message.emit(f"[HM] {line}")
                    return
                success_match = re.search(r"\b([A-Za-z0-9_]+)\s+upload successful\b", line, re.IGNORECASE)
                if success_match:
                    done_partition = success_match.group(1).upper()
                    self._emit_partition_progress(done_partition, 100)
                    current_partition = ""
                    current_partition_started_at = None
                    current_partition_has_real_percent = False
                    current_partition_last_percent = 0
                    self.log_message.emit(f"[HM] {line}")
                    return
                percentages = re.findall(r"(\d+)%", line)
                if percentages:
                    any_percent = True
                    percent = int(percentages[-1])
                    if current_partition:
                        current_partition_has_real_percent = True
                        current_partition_last_percent = percent
                        self._emit_partition_progress(current_partition, percent)
                    else:
                        self.progress.emit(percent)
                    return
                self.log_message.emit(f"[HM] {line}")

            def process_inline_percent(text):
                nonlocal any_percent, current_partition_has_real_percent, current_partition_last_percent
                percentages = re.findall(r"(\d+)\s*%", text)
                if not percentages:
                    return
                percent = max(0, min(100, int(percentages[-1])))
                any_percent = True
                if current_partition:
                    current_partition_has_real_percent = True
                    if percent != current_partition_last_percent:
                        current_partition_last_percent = percent
                        self._emit_partition_progress(current_partition, percent)
                else:
                    self.progress.emit(percent)

            buffer = ""
            while True:
                if self._aborted:
                    break
                ready, _, _ = select.select([master_fd], [], [], 0.2)
                if not ready:
                    if current_partition and current_partition_started_at and not current_partition_has_real_percent:
                        estimated = self._estimated_partition_percent(current_partition, current_partition_started_at)
                        if estimated > current_partition_last_percent:
                            current_partition_last_percent = estimated
                            self._emit_partition_progress(current_partition, estimated)
                    if self._process.poll() is not None:
                        if buffer:
                            process_heimdall_output(buffer)
                        break
                    continue
                try:
                    data = os.read(master_fd, 1024)
                except OSError:
                    if buffer:
                        process_heimdall_output(buffer)
                    break
                if not data:
                    if buffer:
                        process_heimdall_output(buffer)
                    break
                text = data.decode("utf-8", errors="replace")
                for ch in text:
                    if ch in ("\n", "\r"):
                        process_heimdall_output(buffer)
                        buffer = ""
                    else:
                        buffer += ch
                process_inline_percent(buffer)
            try:
                os.close(master_fd)
            except OSError:
                pass
            if not self._aborted:
                self._process.wait()
                elapsed = time.time() - self._start_time
                if self._process.returncode == 0:
                    self.progress.emit(100)
                    msg = tr("flash_success", self.lang, "Success! ({} seconds)").format(int(elapsed))
                    self.finished.emit(True, msg)
                else:
                    msg = tr("flash_heimdall_error", self.lang, "Heimdall error code: {}").format(self._process.returncode)
                    self.finished.emit(False, msg)
        except FileNotFoundError:
            self.finished.emit(False, tr("flash_heimdall_not_found", self.lang, "heimdall or sudo not found!"))
        except Exception as e:
            msg = tr("flash_generic_error", self.lang, "Error: {}").format(str(e))
            self.finished.emit(False, msg)

class MagiskPatchWorker(QThread):
    log_message = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, options: MagiskPatchOptions, lang: str = "en"):
        super().__init__()
        self.options = options
        self.lang = lang if lang in LANGUAGES else "en"

    def run(self):
        result = patch_boot_image(self.options, self.log_message.emit)
        if result.success:
            self.finished.emit(True, result.output_path)
        else:
            self.finished.emit(False, result.error or "Magisk patch failed")

class IDComWidget(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(28)
        self._connected = False
        self._port = ""
        self._blink_state = False
        self.is_dark_mode = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(3)
        self.indicator = QLabel("●")
        self.indicator.setFont(QFont("Times New Roman", 12, QFont.Weight.Bold))
        self.indicator.setFixedWidth(9)
        layout.addWidget(self.indicator)
        self.label = QLabel("ID")
        self.label.setFont(QFont("Times New Roman", 11, QFont.Weight.Bold))
        layout.addWidget(self.label)
        self.port_label = QLabel("")
        self.port_label.setFont(QFont("Times New Roman", 11))
        self.port_label.setMinimumWidth(50)
        layout.addWidget(self.port_label)
        layout.addStretch()
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink)
        self.set_theme(True)
        self._update_compact_width()

    def _update_compact_width(self):
        label_w = self.label.fontMetrics().horizontalAdvance(self.label.text())
        port_text = self.port_label.text() or "000:000"
        port_w = self.port_label.fontMetrics().horizontalAdvance(port_text)
        # margins + indicator + spacings + label + port
        width = 6 + 6 + 9 + 3 + label_w + 3 + max(50, port_w) + 6
        self.setFixedWidth(width)
    def set_theme(self, is_dark):
        self.is_dark_mode = is_dark
        if self._connected: self._set_connected_style()
        else: self._set_disconnected_style()
    def _set_disconnected_style(self):
        if self.is_dark_mode:
            self.setStyleSheet("QFrame { background: transparent; border: 1px solid #555555; border-radius: 3px; }")
            self.indicator.setStyleSheet("border: none; background: transparent; color: #5a5a5a;")
            self.label.setStyleSheet("border: none; background: transparent; color: #d6d6d6;")
            self.port_label.setStyleSheet("border: none; background: transparent; color: #7a7a7a;")
        else:
            self.setStyleSheet("QFrame { background: transparent; border: 1px solid #d0d7de; border-radius: 3px; }")
            self.indicator.setStyleSheet("border: none; background: transparent; color: #9aa2ad;")
            self.label.setStyleSheet("border: none; background: transparent; color: #24292f;")
            self.port_label.setStyleSheet("border: none; background: transparent; color: #6a737d;")
    def _set_connected_style(self):
        if self.is_dark_mode:
            self.setStyleSheet("QFrame { background: transparent; border: 1px solid #555555; border-radius: 3px; }")
            self.label.setStyleSheet("border: none; background: transparent; color: #ffffff;")
            self.port_label.setStyleSheet("border: none; background: transparent; color: #ffffff;")
        else:
            self.setStyleSheet("QFrame { background: transparent; border: 1px solid #d0d7de; border-radius: 3px; }")
            self.label.setStyleSheet("border: none; background: transparent; color: #24292f;")
            self.port_label.setStyleSheet("border: none; background: transparent; color: #24292f;")
    def set_connected(self, port: str):
        self._connected = True
        self._port = port
        display_port = port
        if display_port.startswith("USB:"):
            display_port = display_port[4:]
        self.port_label.setText(display_port)
        self._update_compact_width()
        self._set_connected_style()
        self._blink_timer.start(500)
    def set_disconnected(self):
        self._connected = False
        self._port = ""
        self.port_label.setText("")
        self._update_compact_width()
        self._blink_timer.stop()
        self._set_disconnected_style()
    def _blink(self):
        self._blink_state = not self._blink_state
        if self.is_dark_mode:
            color = "#2bd96b" if self._blink_state else "#1f9f4a"
        else:
            color = "#1f9f4a" if self._blink_state else "#15773a"
        self.indicator.setStyleSheet(f"border: none; background: transparent; color: {color};")

class AnimatedTabWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._animation = None
        
    def setCurrentIndex(self, index):
        old_index = self.currentIndex()
        if old_index != index and old_index >= 0:
            current_widget = self.widget(old_index)
            new_widget = self.widget(index)
            if current_widget and new_widget:
                self._animate_tabs(current_widget, new_widget, old_index < index)
        super().setCurrentIndex(index)
    
    def _animate_tabs(self, old_widget, new_widget, forward=True):
        old_widget.setGeometry(new_widget.geometry())
        new_widget.setGeometry(new_widget.width() if forward else -new_widget.width(), 
                               new_widget.y(), new_widget.width(), new_widget.height())
        new_widget.show()
        self._animation = QPropertyAnimation(new_widget, b"pos")
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        start_pos = new_widget.pos()
        end_pos = old_widget.pos()
        self._animation.setStartValue(start_pos)
        self._animation.setEndValue(end_pos)
        self._animation.start()
        QTimer.singleShot(200, lambda: old_widget.hide())

class SlotRow(QWidget):
    file_changed = pyqtSignal(str, str, list)
    def __init__(self, slot_name: str, current_lang: str = "en"):
        super().__init__()
        self.slot_name = slot_name
        self.current_lang = current_lang
        self._file_path = ""
        self.last_dir = os.path.expanduser("~")
        self.selected_members = []
        self.surgical_mode_active = False
        self.setAcceptDrops(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        lbl = QLabel(slot_name)
        lbl.setFixedWidth(88)
        lbl.setFont(QFont("Times New Roman", 15, QFont.Weight.Bold))
        layout.addWidget(lbl)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText(TRANSLATIONS.get("select_or_drop_file", {}).get(self.current_lang, "Select or drop files..."))
        self.path_edit.setFixedHeight(28)
        layout.addWidget(self.path_edit)
        self.browse_btn = QPushButton("...")
        self.browse_btn.setObjectName("browse_btn")
        self.browse_btn.setFixedSize(28, 28)
        self.browse_btn.setFont(QFont("Times New Roman", 13))
        self.browse_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.browse_btn.clicked.connect(self._browse)
        layout.addWidget(self.browse_btn)
        self.eye_btn = QPushButton("")
        self.eye_btn.setObjectName("eye_btn")
        self.eye_btn.setFixedSize(28, 28)
        self.eye_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.eye_btn.clicked.connect(self._surgical_select)
        self.eye_btn.setEnabled(False)
        layout.addWidget(self.eye_btn)
        self._update_eye_icon()
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setObjectName("clear_btn")
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.setFont(QFont("Times New Roman", 16))
        self.clear_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.clear_btn.clicked.connect(self._clear)
        layout.addWidget(self.clear_btn)
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            allowed = ['.tar', '.tar.gz', '.tgz', '.md5', '.img', '.lz4', '.zip', '.ap']
            if path.endswith('.tar.md5'):
                ext = '.tar.md5'
            if ext in allowed or path.endswith('.tar.md5'):
                event.acceptProposedAction()
                self.setStyleSheet("SlotRow { border: 2px dashed #00b4d8; }")
            else:
                self.setStyleSheet("")
        else:
            self.setStyleSheet("")
    def dragLeaveEvent(self, event):
        self.setStyleSheet("")
    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet("")
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                self._set_file(path)
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, f"{self.slot_name} Seç", self.last_dir, "Tüm firmware (*.tar *.tar.md5 *.tar.gz *.tgz *.img *.lz4 *.zip *.ap);;TAR (*.tar *.tar.md5 *.tar.gz *.tgz);;İmaj (*.img);;LZ4 (*.lz4);;Tüm (*)")
        if path: self._set_file(path)
    def _set_file(self, path):
        self._file_path = path
        self.path_edit.setText(os.path.basename(path))
        self.last_dir = os.path.dirname(path)
        self.selected_members = []
        self.surgical_mode_active = False
        self.eye_btn.setEnabled(True)
        self._update_eye_icon()
        self.file_changed.emit(self.slot_name, path, [])
    def _surgical_select(self):
        if not self._file_path or not os.path.exists(self._file_path):
            return
        dlg = SurgicalDialog(self._file_path, self.selected_members, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.surgical_mode_active = True
            self.selected_members = dlg.get_selected_files()
            files_text = TRANSLATIONS["files_count"].get(self.current_lang, "{} file(s)").format(len(self.selected_members))
            self.path_edit.setText(f"{os.path.basename(self._file_path)} [{files_text}]")
            self.file_changed.emit(self.slot_name, self._file_path, self.selected_members)
        elif not dlg.has_any_checked():
            # User backed out without selecting anything; treat as if surgical mode was never used.
            self.surgical_mode_active = False
            self.selected_members = []
            self.path_edit.setText(os.path.basename(self._file_path))
            self.file_changed.emit(self.slot_name, self._file_path, [])
    def _clear(self):
        self._file_path = ""
        self.path_edit.clear()
        self.selected_members = []
        self.surgical_mode_active = False
        self.eye_btn.setEnabled(False)
        self._update_eye_icon()
        self.file_changed.emit(self.slot_name, "", [])
    def set_enabled(self, enabled: bool):
        self.browse_btn.setEnabled(enabled)
        self.clear_btn.setEnabled(enabled)
        self.eye_btn.setEnabled(enabled and bool(self._file_path))
        self._update_eye_icon()
    def set_tooltip_texts(self, translations, lang):
        self.browse_btn.setToolTip(translations["tt_browse_slot"].get(lang, ""))
        self.eye_btn.setToolTip(translations["tt_surgical"].get(lang, ""))
        self.clear_btn.setToolTip(translations["tt_clear_slot"].get(lang, ""))

    def _update_eye_icon(self):
        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.eye_btn.isEnabled()
        c = self.eye_btn.palette().buttonText().color()
        if not enabled:
            c.setAlpha(120)
        pen = painter.pen()
        pen.setColor(c)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(1, 4, 12, 7)
        painter.setBrush(c)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(6, 6, 3, 3)
        painter.end()
        self.eye_btn.setIcon(QIcon(pixmap))
        self.eye_btn.setIconSize(pixmap.size())

class SurgicalDialog(QDialog):
    def __init__(self, tar_path, initial_selection, parent=None):
        super().__init__(parent)
        self.tar_path = tar_path
        self.selected_files = initial_selection.copy()
        # Prefer window-level language state when available.
        if parent and hasattr(parent, "window") and parent.window() and hasattr(parent.window(), "current_lang"):
            self.current_lang = parent.window().current_lang
        else:
            self.current_lang = parent.current_lang if parent and hasattr(parent, "current_lang") else "en"
        self.setWindowTitle(TRANSLATIONS["select_files"].get(self.current_lang, "Select Files"))
        self.setMinimumSize(350, 450)
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        layout.addWidget(self.list_widget)
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton(TRANSLATIONS["confirm_selection"].get(self.current_lang, "Confirm Selection"))
        btn_ok.clicked.connect(self._accept)
        btn_cancel = QPushButton(TRANSLATIONS["cancel"].get(self.current_lang, "CANCEL"))
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        self._populate_list()
    def _populate_list(self):
        try:
            proc = subprocess.run(["tar", "-tf", self.tar_path], capture_output=True, text=True, check=True)
            for m in [x for x in proc.stdout.splitlines() if x and not x.endswith('/')]:
                name = os.path.basename(m)
                item = QListWidgetItem(name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                # Cerrahi seçim penceresi açıldığında varsayılan olarak hiçbir öğe seçili gelmez.
                is_checked = name in self.selected_files
                item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
                self.list_widget.addItem(item)
        except Exception as e:
            QMessageBox.critical(self, TRANSLATIONS["error"].get(self.current_lang, "Error"), TRANSLATIONS["tar_read_error"].get(self.current_lang, "Cannot read tar archive: {}").format(e))
    def _accept(self):
        self.selected_files = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                self.selected_files.append(item.text())
        self.accept()
    def get_selected_files(self):
        return self.selected_files
    def has_any_checked(self):
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).checkState() == Qt.CheckState.Checked:
                return True
        return False

class UnknownPartitionDialog(QDialog):
    def __init__(self, filename, slot, pit_partitions, parent=None):
        super().__init__(parent)
        self.current_lang = parent.current_lang if parent else "en"
        self.setWindowTitle(TRANSLATIONS["unknown_file_detected"].get(self.current_lang, "Unknown File Detected"))
        self.setFixedWidth(420)
        layout = QVBoxLayout(self)
        lbl_info = QLabel(f"⚠ <b>{slot}</b> {TRANSLATIONS.get('unknown_slot_file', {}).get(self.current_lang, 'slotunda tanınmayan dosya:')}<br><b>{filename}</b><br><br>{TRANSLATIONS.get('select_partition', {}).get(self.current_lang, 'Cihazdaki hangi bölüme yazılsın?')}")
        lbl_info.setWordWrap(True)
        lbl_info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(lbl_info)
        self.combo = QComboBox()
        if pit_partitions:
            self.combo.addItems(pit_partitions)
        else:
            self.combo.addItems(["BOOT", "RECOVERY", "SYSTEM", "VENDOR", "USERDATA", "RADIO", "CACHE", "OMR", "HIDDEN"])
        layout.addWidget(self.combo)
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton(TRANSLATIONS["write_to_partition"].get(self.current_lang, "Write to Selected Partition"))
        btn_ok.clicked.connect(self.accept)
        btn_skip = QPushButton(TRANSLATIONS["skip_file"].get(self.current_lang, "Skip This File"))
        btn_skip.clicked.connect(self.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_skip)
        layout.addLayout(btn_layout)
    def get_partition(self):
        return self.combo.currentText()

# ─── Ana Pencere (MainWindow) ─────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, default_dark=True):
        super().__init__()
        self.setWindowTitle("OpenFlasher v1.0")
        self.setWindowIcon(get_app_icon())
        self.setMinimumSize(900, 580)
        self.is_dark_mode = default_dark
        self.current_lang = get_system_language()
        self._device_connected = False
        self._pit_path = None
        self._flashing = False
        self._slot_files = {s: "" for s in SUPPORTED_SLOTS}
        self._slot_members = {s: [] for s in SUPPORTED_SLOTS}
        self._mutex = QMutex()
        self._pit_downloader = None
        self._file_analyzer = None
        self._flash_worker = None
        self._magisk_worker = None
        self._magisk_output_path = ""
        self._usb_monitor = None
        self._flashing_start_time = None
        self._elapsed_timer = None
        self._pass_fail_timer = None
        self._device_vendor_name = '---'
        self._device_product_name = '---'
        self._device_port = ""
        self._start_time = None
        self._sudo_password = ""
        self._log_entries = []
        self._log_detect_cache = {}
        self._log_patterns = self._build_log_patterns()
        self._magisk_log_last = ""

        self._setup_ui()
        self._apply_theme()
        self._update_tooltips()

        if not self._acquire_sudo():
            sys.exit(0)

        self.log(TRANSLATIONS["sudo_ok"][self.current_lang], True)

        missing = check_dependencies()
        if missing:
            self.log(TRANSLATIONS["missing_tools"][self.current_lang] + ', '.join(missing), True)
            self.btn_start.setEnabled(False)

        # USB monitörü başlat - otomatik tarama için
        self._usb_monitor = USBMonitor(self.current_lang)
        self._usb_monitor.log_message.connect(self.log)
        self._usb_monitor.device_connected.connect(self._on_device_connected)
        self._usb_monitor.device_disconnected.connect(self._on_device_disconnected)
        self._usb_monitor.start()
        self.log(TRANSLATIONS["usb_monitor_ui"].get(self.current_lang, "[USB] Monitor started..."))
        
        self._update_buttons()

        self.log(TRANSLATIONS["sys_started"][self.current_lang], True)
        self.log(TRANSLATIONS["connect_download_mode"][self.current_lang])

        rotate_log()
        with open(LOG_FILE, "a") as f:
            f.write(f"\n{'='*50}\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - OpenFlasher oturumu başladı\n{'='*50}\n")

    def _acquire_sudo(self):
        dlg = SudoPasswordDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        password = dlg.get_password()
        selected_theme = dlg.get_theme()
        selected_lang = dlg.get_language()
        try:
            self.is_dark_mode = selected_theme
            self.current_lang = selected_lang
            self._sudo_password = password
            subprocess.run(["sudo", "-S", "-v"], input=password + "\n", text=True, capture_output=True, timeout=5)
            self.sudo_timer = QTimer(self)
            self.sudo_timer.timeout.connect(self._keep_sudo_alive)
            self.sudo_timer.start(600000)
            self._apply_theme()
            self._retranslate_ui()
            self._update_tooltips()
            return True
        except Exception as e:
            password = None
            QMessageBox.critical(self, TRANSLATIONS["error"].get(self.current_lang, "Error"), TRANSLATIONS["sudo_failed"].get(self.current_lang, "Cannot run sudo:{}" ).format(e))
            return False

    def _keep_sudo_alive(self):
        if self._sudo_password:
            subprocess.run(["sudo", "-S", "-p", "", "-v"], input=self._sudo_password + "\n", text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["sudo", "-n", "-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _setup_ui(self):
        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)

        top_bar = QFrame(objectName="top_bar")
        t_layout = QHBoxLayout(top_bar)
        t_layout.setContentsMargins(6, 3, 6, 3)
        self.title_lbl = QLabel("OPEN FLASHER", objectName="title_lbl")
        self.title_lbl.setFont(QFont("Times New Roman", 24, QFont.Weight.Bold))
        t_layout.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(" │  Heimdall Frontend", objectName="sub_lbl")
        t_layout.addWidget(self.sub_lbl)
        t_layout.addStretch()
        self.btn_lang = QPushButton("EN/TR", objectName="btn_lang")
        self.btn_lang.clicked.connect(self._toggle_language)
        t_layout.addWidget(self.btn_lang)
        self.btn_theme = QPushButton(TRANSLATIONS["theme_btn_light"].get(self.current_lang, "☀️ Light"), objectName="btn_theme")
        self.btn_theme.clicked.connect(self._toggle_theme)
        t_layout.addWidget(self.btn_theme)
        self.idcom = IDComWidget()
        t_layout.addWidget(self.idcom)
        main_layout.addWidget(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tabs = AnimatedTabWidget()
        self._create_log_tab()
        self._create_options_tab()
        self._create_reboot_tab()
        self._create_terminal_tab()
        self._create_magisk_tab()
        splitter.addWidget(self.tabs)

        right_panel = QWidget()
        r_layout = QVBoxLayout(right_panel)
        r_layout.setContentsMargins(3, 0, 3, 0)
        slots_group = QGroupBox("FLASH SLOTS")
        self.slots_group = slots_group
        slots_layout = QVBoxLayout(slots_group)
        self.slot_rows = {}
        for slot in SUPPORTED_SLOTS:
            row = SlotRow(slot, self.current_lang)
            row.file_changed.connect(self._on_file_changed)
            slots_layout.addWidget(row)
            self.slot_rows[slot] = row
        r_layout.addWidget(slots_group)

        self.pass_fail_label = QLabel("", objectName="pass_fail_label", alignment=Qt.AlignmentFlag.AlignCenter)
        r_layout.addWidget(self.pass_fail_label)
        self.time_label = QLabel("", alignment=Qt.AlignmentFlag.AlignCenter)
        self.time_label.setFont(QFont("Times New Roman", 13))
        r_layout.addWidget(self.time_label)
        r_layout.addStretch()

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 500])
        main_layout.addWidget(splitter, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(24)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(TRANSLATIONS["progress_ready"].get(self.current_lang, "Ready"))
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.progress_bar)

        bottom_bar = QFrame(objectName="bottom_bar")
        b_layout = QHBoxLayout(bottom_bar)
        b_layout.setContentsMargins(6, 3, 6, 3)
        self.status_label = QLabel(TRANSLATIONS["waiting"].get(self.current_lang, "Waiting..."))
        self.status_label.hide()
        b_layout.addStretch()
        self.btn_start = QPushButton(TRANSLATIONS["start_btn"].get(self.current_lang, "▶ START"), objectName="btn_start")
        self.btn_start.clicked.connect(self._on_start)
        b_layout.addWidget(self.btn_start)
        self.btn_cancel = QPushButton(TRANSLATIONS["cancel_btn"].get(self.current_lang, "■ CANCEL"), objectName="btn_cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setEnabled(False)
        b_layout.addWidget(self.btn_cancel)
        self.btn_reset = QPushButton(TRANSLATIONS["reset_btn"].get(self.current_lang, "↺ RESET"), objectName="btn_reset")
        self.btn_reset.clicked.connect(self._on_reset)
        b_layout.addWidget(self.btn_reset)
        self.btn_exit = QPushButton(TRANSLATIONS["exit_btn"].get(self.current_lang, "✕ EXIT"), objectName="btn_exit")
        self.btn_exit.clicked.connect(self.close)
        b_layout.addWidget(self.btn_exit)
        main_layout.addWidget(bottom_bar)

        self._retranslate_ui()

    def _create_log_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.log_screen = QTextEdit(objectName="log_screen", readOnly=True)
        layout.addWidget(self.log_screen)
        self.tabs.addTab(tab, "Log")

    def _create_options_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 15, 12, 15)
        layout.setSpacing(10)
        self.chk_auto_reboot = CheckBox("Auto Reboot")
        self.chk_auto_reboot.set_dark_mode(self.is_dark_mode)
        self.chk_auto_reboot.setChecked(False)
        layout.addWidget(self.chk_auto_reboot)
        self.chk_repartition = CheckBox("Re-Partition")
        self.chk_repartition.set_dark_mode(self.is_dark_mode)
        layout.addWidget(self.chk_repartition)
        self.chk_dry_run = CheckBox("Dry Run")
        self.chk_dry_run.set_dark_mode(self.is_dark_mode)
        layout.addWidget(self.chk_dry_run)
        self.btn_udev = QPushButton(TRANSLATIONS["udev_btn"].get(self.current_lang, "Check UDEV"))
        self.btn_udev.clicked.connect(self._check_udev)
        layout.addWidget(self.btn_udev)
        layout.addStretch()
        self.tabs.addTab(tab, "Options")

    def _create_reboot_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 20)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Cihaz Komutları", font=QFont("Times New Roman", 15, QFont.Weight.Bold)))
        self.btn_reboot_sys = QPushButton(TRANSLATIONS["reboot_system"].get(self.current_lang, "Reboot System"))
        self.btn_reboot_sys.clicked.connect(lambda: self._send_heimdall_command("close-pc-screen"))
        layout.addWidget(self.btn_reboot_sys)
        self.btn_reboot_dl = QPushButton(TRANSLATIONS["reboot_download"].get(self.current_lang, "Download Mode"))
        self.btn_reboot_dl.clicked.connect(lambda: self._send_heimdall_command("reboot-bootloader"))
        layout.addWidget(self.btn_reboot_dl)
        self.btn_reboot_rec = QPushButton(TRANSLATIONS["reboot_recovery"].get(self.current_lang, "Recovery Mode"))
        self.btn_reboot_rec.clicked.connect(lambda: self._send_heimdall_command("reboot-recovery"))
        layout.addWidget(self.btn_reboot_rec)
        layout.addStretch()
        self.tabs.addTab(tab, "Reboot")

    def _create_terminal_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.terminal_output = QPlainTextEdit(readOnly=True, font=QFont("Times New Roman", 16))
        layout.addWidget(self.terminal_output)
        cmd_layout = QHBoxLayout()
        self.terminal_input = QLineEdit(placeholderText="heimdall komutu girin...")
        self.terminal_input.returnPressed.connect(self._execute_terminal_command)
        cmd_layout.addWidget(self.terminal_input)
        btn_send = QPushButton(TRANSLATIONS["send"].get(self.current_lang, "Send"))
        btn_send.clicked.connect(self._execute_terminal_command)
        cmd_layout.addWidget(btn_send)
        layout.addLayout(cmd_layout)
        self.tabs.addTab(tab, "Terminal")

    def _create_magisk_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.magisk_title_lbl = QLabel(TRANSLATIONS["magisk_title"].get(self.current_lang, "Magisk Patching"))
        self.magisk_title_lbl.setFont(QFont("Times New Roman", 15, QFont.Weight.Bold))
        layout.addWidget(self.magisk_title_lbl)

        self.magisk_desc_lbl = QLabel(TRANSLATIONS["magisk_desc"].get(self.current_lang, "Patch boot, init_boot, recovery, or AP package."))
        self.magisk_desc_lbl.setWordWrap(True)
        layout.addWidget(self.magisk_desc_lbl)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        image_row = QHBoxLayout()
        self.magisk_image_edit = QLineEdit()
        self.magisk_image_edit.setReadOnly(True)
        self.magisk_image_edit.setPlaceholderText(TRANSLATIONS["magisk_file_placeholder"].get(self.current_lang, "No file selected..."))
        self.magisk_image_edit.setMinimumWidth(0)
        self.magisk_image_btn = QPushButton(TRANSLATIONS["select_file"].get(self.current_lang, "Select File"))
        self.magisk_image_btn.setFixedWidth(86)
        self.magisk_image_btn.clicked.connect(self._browse_magisk_image)
        image_row.addWidget(self.magisk_image_edit, 1)
        image_row.addWidget(self.magisk_image_btn)
        self.magisk_image_label = QLabel(TRANSLATIONS["magisk_image_label"].get(self.current_lang, "Image/AP:"))
        form.addRow(self.magisk_image_label, image_row)

        apk_row = QHBoxLayout()
        self.magisk_apk_edit = QLineEdit()
        self.magisk_apk_edit.setReadOnly(True)
        self.magisk_apk_edit.setPlaceholderText(TRANSLATIONS["magisk_auto_latest"].get(self.current_lang, "Empty = latest Magisk"))
        self.magisk_apk_edit.setMinimumWidth(0)
        self.magisk_apk_btn = QPushButton(TRANSLATIONS["select_file"].get(self.current_lang, "Select File"))
        self.magisk_apk_btn.setFixedWidth(86)
        self.magisk_apk_btn.clicked.connect(self._browse_magisk_apk)
        apk_row.addWidget(self.magisk_apk_edit, 1)
        apk_row.addWidget(self.magisk_apk_btn)
        self.magisk_apk_label = QLabel(TRANSLATIONS["magisk_apk_label"].get(self.current_lang, "Magisk APK:"))
        form.addRow(self.magisk_apk_label, apk_row)

        output_row = QHBoxLayout()
        self.magisk_output_edit = QLineEdit()
        self.magisk_output_edit.setReadOnly(True)
        self.magisk_output_edit.setPlaceholderText(os.path.expanduser("~"))
        self.magisk_output_edit.setMinimumWidth(0)
        self.magisk_output_btn = QPushButton(TRANSLATIONS["browse"].get(self.current_lang, "Browse"))
        self.magisk_output_btn.setFixedWidth(86)
        self.magisk_output_btn.clicked.connect(self._browse_magisk_output)
        output_row.addWidget(self.magisk_output_edit, 1)
        output_row.addWidget(self.magisk_output_btn)
        self.magisk_output_label = QLabel(TRANSLATIONS["magisk_output_label"].get(self.current_lang, "Output:"))
        form.addRow(self.magisk_output_label, output_row)

        self.magisk_arch_combo = QComboBox()
        self.magisk_arch_combo.addItems(["arm64", "arm", "x86_64", "x86"])
        self.magisk_arch_label = QLabel(TRANSLATIONS["magisk_arch_label"].get(self.current_lang, "Target arch:"))
        form.addRow(self.magisk_arch_label, self.magisk_arch_combo)
        layout.addLayout(form)

        options_layout = QVBoxLayout()
        options_layout.setSpacing(4)
        options_row_1 = QHBoxLayout()
        options_row_1.setSpacing(10)
        options_row_2 = QHBoxLayout()
        options_row_2.setSpacing(10)
        self.chk_magisk_keep_verity = CheckBox(TRANSLATIONS["magisk_keep_verity"].get(self.current_lang, "Keep Verity"))
        self.chk_magisk_keep_forceencrypt = CheckBox(TRANSLATIONS["magisk_keep_forceencrypt"].get(self.current_lang, "Keep Force Encrypt"))
        self.chk_magisk_patch_vbmeta = CheckBox(TRANSLATIONS["magisk_patch_vbmeta"].get(self.current_lang, "Patch vbmeta"))
        self.chk_magisk_recovery = CheckBox(TRANSLATIONS["magisk_recovery_mode"].get(self.current_lang, "Recovery Mode"))
        self.chk_magisk_legacy_sar = CheckBox(TRANSLATIONS["magisk_legacy_sar"].get(self.current_lang, "Legacy SAR"))
        for chk in (
            self.chk_magisk_keep_verity,
            self.chk_magisk_keep_forceencrypt,
            self.chk_magisk_patch_vbmeta,
            self.chk_magisk_recovery,
            self.chk_magisk_legacy_sar,
        ):
            chk.set_dark_mode(self.is_dark_mode)
        self.chk_magisk_keep_verity.setChecked(True)
        self.chk_magisk_keep_forceencrypt.setChecked(True)
        options_row_1.addWidget(self.chk_magisk_keep_verity)
        options_row_1.addWidget(self.chk_magisk_keep_forceencrypt)
        options_row_1.addWidget(self.chk_magisk_patch_vbmeta)
        options_row_1.addStretch()
        options_row_2.addWidget(self.chk_magisk_recovery)
        options_row_2.addWidget(self.chk_magisk_legacy_sar)
        options_row_2.addStretch()
        options_layout.addLayout(options_row_1)
        options_layout.addLayout(options_row_2)
        layout.addLayout(options_layout)

        action_row = QHBoxLayout()
        self.btn_magisk_patch = QPushButton(TRANSLATIONS["magisk_start"].get(self.current_lang, "Patch Image"))
        self.btn_magisk_patch.setFixedWidth(118)
        self.btn_magisk_patch.clicked.connect(self._on_magisk_patch)
        action_row.addStretch()
        action_row.addWidget(self.btn_magisk_patch)
        layout.addLayout(action_row)

        self.magisk_output_path_edit = QLineEdit()
        self.magisk_output_path_edit.setReadOnly(True)
        self.magisk_output_path_edit.setPlaceholderText(TRANSLATIONS["magisk_boot_placeholder"].get(self.current_lang, "Will be auto-downloaded..."))
        layout.addWidget(self.magisk_output_path_edit)

        self.magisk_log_screen = QPlainTextEdit(readOnly=True)
        self.magisk_log_screen.setMinimumHeight(120)
        self.magisk_log_screen.setFont(QFont("Times New Roman", 12))
        layout.addWidget(self.magisk_log_screen, 1)
        layout.addStretch()
        self.tabs.addTab(tab, "Magisk Beta")

    def _send_heimdall_command(self, subcommand):
        if not self._device_connected:
            QMessageBox.warning(self, "Cihaz Yok", "Önce bir cihaz bağlayın.")
            return
        msg = TRANSLATIONS["reboot_command"].get(self.current_lang, "[REBOOT] sudo heimdall {}").format(subcommand)
        self.log(msg)
        try:
            subprocess.Popen(["sudo", "heimdall", subcommand], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            msg = TRANSLATIONS["reboot_error"].get(self.current_lang, "[REBOOT] Error: {}").format(e)
            self.log(msg)

    def _execute_terminal_command(self):
        cmd = self.terminal_input.text().strip()
        if not cmd: return
        self.terminal_input.clear()
        self.terminal_output.appendPlainText(f"$ {cmd}")
        try:
            proc = subprocess.Popen(["sudo"] + shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            out, _ = proc.communicate(timeout=30)
            if out: self.terminal_output.appendPlainText(out.strip())
            self.terminal_output.appendPlainText(f"(çıkış kodu: {proc.returncode})")
        except Exception as e:
            self.terminal_output.appendPlainText(f"Hata: {e}")

    def _browse_magisk_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            TRANSLATIONS["magisk_select_image"].get(self.current_lang, "Select boot/init_boot/recovery image"),
            os.path.expanduser("~"),
            "Android Images/AP (*.img *.bin *.lz4 *.tar *.tar.md5 *.tgz *.tar.gz);;AP Packages (*.tar *.tar.md5 *.tgz *.tar.gz);;Images (*.img *.bin *.lz4);;All Files (*)",
        )
        if path:
            self.magisk_image_edit.setText(path)
            if not self.magisk_output_edit.text():
                self.magisk_output_edit.setText(os.path.dirname(path))

    def _browse_magisk_apk(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            TRANSLATIONS["magisk_select_apk"].get(self.current_lang, "Select Magisk APK"),
            os.path.expanduser("~"),
            "Android APK (*.apk);;All Files (*)",
        )
        if path:
            self.magisk_apk_edit.setText(path)

    def _browse_magisk_output(self):
        path = QFileDialog.getExistingDirectory(
            self,
            TRANSLATIONS["magisk_select_output"].get(self.current_lang, "Select output folder"),
            self.magisk_output_edit.text() or os.path.expanduser("~"),
        )
        if path:
            self.magisk_output_edit.setText(path)

    def _on_magisk_patch(self):
        image_path = self.magisk_image_edit.text().strip()
        if not image_path:
            QMessageBox.warning(self, "Magisk", TRANSLATIONS["magisk_need_image"].get(self.current_lang, "Please select a boot image first."))
            return
        if self._magisk_worker and self._magisk_worker.isRunning():
            return
        options = MagiskPatchOptions(
            image_path=image_path,
            magisk_apk=self.magisk_apk_edit.text().strip(),
            output_dir=self.magisk_output_edit.text().strip() or os.path.dirname(image_path),
            arch=self.magisk_arch_combo.currentText(),
            keep_verity=self.chk_magisk_keep_verity.isChecked(),
            keep_forceencrypt=self.chk_magisk_keep_forceencrypt.isChecked(),
            patch_vbmeta=self.chk_magisk_patch_vbmeta.isChecked(),
            recovery_mode=self.chk_magisk_recovery.isChecked(),
            legacy_sar=self.chk_magisk_legacy_sar.isChecked(),
        )
        self.log(TRANSLATIONS["magisk_started"].get(self.current_lang, "[MAGISK] Patch started..."), True)
        self.magisk_log_screen.clear()
        self._magisk_log_last = ""
        self._append_magisk_log(self._strip_magisk_prefix(TRANSLATIONS["magisk_started"].get(self.current_lang, "[MAGISK] Patch started...")))
        self.magisk_output_path_edit.clear()
        self.btn_magisk_patch.setEnabled(False)
        self._magisk_worker = MagiskPatchWorker(options, self.current_lang)
        self._magisk_worker.log_message.connect(self._append_magisk_log_filtered)
        self._magisk_worker.finished.connect(self._on_magisk_finished)
        self._magisk_worker.start()
        self.tabs.setCurrentIndex(4)

    def _append_magisk_log(self, message: str):
        if hasattr(self, "magisk_log_screen") and self.magisk_log_screen:
            self.magisk_log_screen.appendPlainText(message)

    def _strip_magisk_prefix(self, message: str):
        return re.sub(r"^\[(?:MAGISK|MAGISKBOOT)\]\s*", "", message)

    def _append_magisk_log_filtered(self, message: str):
        simplified = self._simplify_magisk_log(message)
        if simplified and simplified != self._magisk_log_last:
            self._magisk_log_last = simplified
            self._append_magisk_log(self._strip_magisk_prefix(simplified))

    def _simplify_magisk_log(self, message: str):
        if "Downloading latest Magisk release metadata" in message:
            return "[MAGISK] Magisk sürümü kontrol ediliyor..."
        if "Cached Magisk APK is up to date" in message:
            return "[MAGISK] Magisk APK güncel, mevcut dosya kullanılacak."
        if "Updating cached Magisk APK" in message:
            return "[MAGISK] Yeni Magisk APK indiriliyor..."
        if "Using cached Magisk APK due to network/error" in message:
            return "[MAGISK] İnternet yok, mevcut Magisk APK kullanılacak."
        if "Using cached Magisk APK due to update failure" in message:
            return "[MAGISK] Güncelleme başarısız, mevcut Magisk APK kullanılacak."
        if "Downloading https://" in message:
            return "[MAGISK] Magisk indiriliyor..."
        if "Extracting APK payload" in message:
            return "[MAGISK] Magisk dosyaları hazırlanıyor..."
        if "Preparing upstream Magisk engine" in message or "Start decompress needed" in message:
            return "[MAGISK] Magisk dosyaları hazırlanıyor..."
        if "Reading AP package" in message:
            return "[MAGISK] AP paketi okunuyor..."
        if "Selected AP image" in message:
            return "[MAGISK] Yamanacak imaj seçildi."
        if "Decompressing LZ4" in message:
            return "[MAGISK] İmaj açılıyor..."
        if "Unpacking boot image" in message:
            return "[MAGISK] Boot imajı ayrıştırılıyor..."
        if "Unpacking boot image" in message or "Unpacking boot image" in message or "Unpacking boot" in message:
            return "[MAGISK] Boot imajı ayrıştırılıyor..."
        if "Stock boot image detected" in message:
            return "[MAGISK] Orijinal boot imajı algılandı."
        if "Stock boot image" in message or "original boot" in message:
            return "[MAGISK] Orijinal boot imajı algılandı."
        if "Magisk patched boot image detected" in message:
            return "[MAGISK] Daha önce yamalanmış boot imajı algılandı."
        if "Patching ramdisk" in message:
            return "[MAGISK] Ramdisk yamalanıyor..."
        if "patch ramdisk" in message.lower():
            return "[MAGISK] Ramdisk yamalanıyor..."
        if "Repacking boot image" in message:
            return "[MAGISK] Boot imajı yeniden paketleniyor..."
        if "repack" in message.lower() and "boot" in message.lower():
            return "[MAGISK] Boot imajı yeniden paketleniyor..."
        if "Compressing LZ4" in message:
            return "[MAGISK] İmaj sıkıştırılıyor..."
        if "Building full patched AP tar" in message:
            return "[MAGISK] AP paketi yeniden oluşturuluyor..."
        if "Building patched AP tar" in message:
            return "[MAGISK] AP paketi oluşturuluyor..."
        if "Patched AP package saved" in message or "Patched image saved" in message:
            return "[MAGISK] Çıktı kaydedildi."
        if "[MAGISK] Error:" in message:
            return message
        return ""

    @pyqtSlot(bool, str)
    def _on_magisk_finished(self, success, message):
        self.btn_magisk_patch.setEnabled(True)
        if success:
            self._magisk_output_path = message
            self.magisk_output_path_edit.setText(message)
            self._append_magisk_log(self._strip_magisk_prefix(TRANSLATIONS["magisk_finished"].get(self.current_lang, "[MAGISK] Patched image ready: {}").format(message)))
            self.log(TRANSLATIONS["magisk_finished"].get(self.current_lang, "[MAGISK] Patched image ready: {}").format(message), True)
            QMessageBox.information(self, "Magisk", TRANSLATIONS["magisk_finished"].get(self.current_lang, "[MAGISK] Patched image ready: {}").format(message))
        else:
            err = TRANSLATIONS["magisk_error"].get(self.current_lang, "[MAGISK] ✕ {}").format(message)
            self._append_magisk_log(self._strip_magisk_prefix(err))
            self.log(err, True)
            QMessageBox.critical(self, "Magisk", err)

    def _confirm_yes_no(self, title, text, default_button=QMessageBox.StandardButton.No):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(default_button)
        yes_btn = box.button(QMessageBox.StandardButton.Yes)
        no_btn = box.button(QMessageBox.StandardButton.No)
        if yes_btn:
            yes_btn.setText(TRANSLATIONS["yes"].get(self.current_lang, "Yes"))
        if no_btn:
            no_btn.setText(TRANSLATIONS["no"].get(self.current_lang, "No"))
        return box.exec() == QMessageBox.StandardButton.Yes

    def _check_udev(self):
        rule_path = "/etc/udev/rules.d/51-openflasher.rules"
        l = self.current_lang
        if os.path.exists(rule_path):
            QMessageBox.information(self, "UDEV", TRANSLATIONS["udev_ready"].get(l, "USB permissions are already ready."))
            return
        if self._confirm_yes_no(
            TRANSLATIONS["udev_missing_title"].get(l, "USB Permissions Missing"),
            TRANSLATIONS["udev_missing_confirm"].get(l, "USB permission rule is missing:\n{}\nCreate it now?").format(rule_path)
        ):
            try:
                sudo_input = (self._sudo_password + "\n") if self._sudo_password else None
                sudo_cmd = ["sudo", "-S", "-p", ""] if self._sudo_password else ["sudo", "-n"]
                subprocess.run(
                    sudo_cmd + ["tee", rule_path],
                    input=(sudo_input or "") + 'SUBSYSTEM=="usb", ATTR{idVendor}=="04e8", MODE="0666"\n',
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=True
                )
                subprocess.run(
                    sudo_cmd + ["udevadm", "control", "--reload-rules"],
                    input=sudo_input,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=True
                )
                subprocess.run(
                    sudo_cmd + ["udevadm", "trigger"],
                    input=sudo_input,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=True
                )
                QMessageBox.information(self, TRANSLATIONS["slot_success"].get(l, "Success"), TRANSLATIONS["udev_created_message"].get(l, "USB permission rule was created and rules were reloaded."))
                self.log(TRANSLATIONS["udev_rule_created"].get(self.current_lang, "[UDEV] Rule created."))
            except Exception as e:
                QMessageBox.critical(self, TRANSLATIONS["error"].get(l, "Error"), f"Kural oluşturulamadı:\n{e}")

    def _toggle_language(self):
        if self.current_lang == "en":
            self.current_lang = "tr"
        else:
            self.current_lang = "en"
        if self._usb_monitor:
            self._usb_monitor.set_language(self.current_lang)
        if self._pit_downloader:
            self._pit_downloader.set_language(self.current_lang)
        self.btn_lang.setText("EN/TR")
        self._retranslate_ui()
        self._update_tooltips()
        self._rerender_log_screen()
        
    def _toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        self._apply_theme()
        self._update_status_style()
        self._retranslate_ui()
        self._update_tooltips()
        self._rerender_log_screen()
        for chk in (self.chk_auto_reboot, self.chk_repartition, self.chk_dry_run):
            if hasattr(chk, 'set_dark_mode'):
                chk.set_dark_mode(self.is_dark_mode)
        for chk in (
            self.chk_magisk_keep_verity,
            self.chk_magisk_keep_forceencrypt,
            self.chk_magisk_patch_vbmeta,
            self.chk_magisk_recovery,
            self.chk_magisk_legacy_sar,
        ):
            if hasattr(chk, 'set_dark_mode'):
                chk.set_dark_mode(self.is_dark_mode)
        
    def _apply_theme(self):
        self.setStyleSheet(STYLE_DARK if self.is_dark_mode else STYLE_LIGHT)
        self.idcom.set_theme(self.is_dark_mode)

    def _update_status_style(self):
        color = "#39d353" if self._device_connected else "#bcd0f0" if self.is_dark_mode else "#24292f"
        self.status_label.setStyleSheet(f"color: {color}; border: none; background: transparent;")

    def _update_tooltips(self):
        l = self.current_lang
        self.btn_start.setToolTip(TRANSLATIONS["tt_start"].get(l, ""))
        self.btn_cancel.setToolTip(TRANSLATIONS["tt_cancel"].get(l, ""))
        self.btn_reset.setToolTip(TRANSLATIONS["tt_reset"].get(l, ""))
        self.btn_exit.setToolTip(TRANSLATIONS["tt_exit"].get(l, ""))
        self.btn_theme.setToolTip(TRANSLATIONS["tt_theme"].get(l, ""))
        self.btn_lang.setToolTip("Language / Dil")
        for row in self.slot_rows.values():
            row.set_tooltip_texts(TRANSLATIONS, l)

    def _retranslate_ui(self):
        l = self.current_lang
        self.progress_bar.setFormat(TRANSLATIONS["progress_ready"].get(l, "Ready"))
        self.setWindowTitle(TRANSLATIONS["window_title"].get(l, "OpenFlasher v1.0"))
        self.title_lbl.setText(TRANSLATIONS["openflasher_title"].get(l, "OPEN FLASHER"))
        self.sub_lbl.setText(TRANSLATIONS["heimdall_frontend"].get(l, " │  Heimdall Frontend"))
        self.tabs.setTabText(0, TRANSLATIONS["log_tab"].get(l, "Log"))
        self.tabs.setTabText(1, TRANSLATIONS["options_tab"].get(l, "Options"))
        self.tabs.setTabText(2, TRANSLATIONS["reboot_tab"].get(l, "Reboot"))
        self.tabs.setTabText(3, TRANSLATIONS["terminal_tab"].get(l, "Terminal"))
        self.tabs.setTabText(4, TRANSLATIONS["magisk_tab"].get(l, "Magisk Beta"))
        self.slots_group.setTitle(TRANSLATIONS["flash_slots_group"].get(l, "FLASH SLOTS"))
        self.btn_start.setText(TRANSLATIONS["start_btn"].get(l, "▶ START"))
        self.btn_cancel.setText(TRANSLATIONS["cancel_btn"].get(l, "■ CANCEL"))
        self.btn_reset.setText(TRANSLATIONS["reset_btn"].get(l, "↺ RESET"))
        self.btn_exit.setText(TRANSLATIONS["exit_btn"].get(l, "✕ EXIT"))
        self.chk_auto_reboot.setText(TRANSLATIONS["auto_reboot"].get(l, "Auto Reboot"))
        self.chk_repartition.setText(TRANSLATIONS["repartition"].get(l, "Re-Partition"))
        self.chk_dry_run.setText(TRANSLATIONS["dry_run"].get(l, "Dry Run"))
        self.btn_udev.setText(TRANSLATIONS["udev_btn"].get(l, "Check UDEV"))
        self.btn_reboot_sys.setText(TRANSLATIONS["reboot_system"].get(l, "Reboot System"))
        self.btn_reboot_dl.setText(TRANSLATIONS["reboot_download"].get(l, "Download Mode"))
        self.btn_reboot_rec.setText(TRANSLATIONS["reboot_recovery"].get(l, "Recovery Mode"))
        self.magisk_title_lbl.setText(TRANSLATIONS["magisk_title"].get(l, "Magisk Patching"))
        self.magisk_desc_lbl.setText(TRANSLATIONS["magisk_desc"].get(l, "Patch boot, init_boot, recovery, or AP package."))
        self.magisk_image_label.setText(TRANSLATIONS["magisk_image_label"].get(l, "Image/AP:"))
        self.magisk_apk_label.setText(TRANSLATIONS["magisk_apk_label"].get(l, "Magisk APK:"))
        self.magisk_output_label.setText(TRANSLATIONS["magisk_output_label"].get(l, "Output:"))
        self.magisk_arch_label.setText(TRANSLATIONS["magisk_arch_label"].get(l, "Target arch:"))
        self.magisk_image_btn.setText(TRANSLATIONS["select_file"].get(l, "Select File"))
        self.magisk_apk_btn.setText(TRANSLATIONS["select_file"].get(l, "Select File"))
        self.magisk_output_btn.setText(TRANSLATIONS["browse"].get(l, "Browse"))
        self.chk_magisk_keep_verity.setText(TRANSLATIONS["magisk_keep_verity"].get(l, "Keep Verity"))
        self.chk_magisk_keep_forceencrypt.setText(TRANSLATIONS["magisk_keep_forceencrypt"].get(l, "Keep Force Encrypt"))
        self.chk_magisk_patch_vbmeta.setText(TRANSLATIONS["magisk_patch_vbmeta"].get(l, "Patch vbmeta"))
        self.chk_magisk_recovery.setText(TRANSLATIONS["magisk_recovery_mode"].get(l, "Recovery Mode"))
        self.chk_magisk_legacy_sar.setText(TRANSLATIONS["magisk_legacy_sar"].get(l, "Legacy SAR"))
        self.btn_magisk_patch.setText(TRANSLATIONS["magisk_start"].get(l, "Patch Image"))
        self.magisk_image_edit.setPlaceholderText(TRANSLATIONS["magisk_file_placeholder"].get(l, "No file selected..."))
        self.magisk_apk_edit.setPlaceholderText(TRANSLATIONS["magisk_auto_latest"].get(l, "Empty = latest Magisk"))
        self.magisk_output_path_edit.setPlaceholderText(TRANSLATIONS["magisk_boot_placeholder"].get(l, "Will be auto-downloaded..."))
        self.btn_theme.setText(TRANSLATIONS["theme_btn_dark" if self.is_dark_mode else "theme_btn_light"].get(l, "☀️ Light"))
        self.btn_lang.setText("EN/TR")

        # Update slot widgets placeholders
        for slot_widget in self.slot_rows.values():
            slot_widget.current_lang = l
            slot_widget.path_edit.setPlaceholderText(TRANSLATIONS.get("select_or_drop_file", {}).get(l, "Select or drop files..."))
        
        if not self._device_connected:
            self.status_label.setText(TRANSLATIONS["status_waiting"].get(l, "Waiting for device..."))
        else:
            # When device is connected, update status text with current language
            self.status_label.setText(TRANSLATIONS["status_connected"].get(l, "✓ Connected │ ") + self._device_port)

    @pyqtSlot(str, str, str)
    def _on_device_connected(self, port, vendor, product):
        self._device_connected = True
        self._device_vendor_name = vendor
        self._device_product_name = product
        self._device_port = port
        self.idcom.set_connected(port)
        l = self.current_lang
        self.status_label.setText(TRANSLATIONS["status_connected"].get(l, "✓ Connected │ ") + port)
        self._update_status_style()
        self._update_buttons()
        QTimer.singleShot(100, self._download_pit)

    @pyqtSlot()
    def _on_device_disconnected(self):
        if self._flashing:
            self.log(TRANSLATIONS["status_disconnected"].get(self.current_lang, "⚠ Disconnected!"))
            self.idcom.set_disconnected()
            return
        self._device_connected = False
        self._device_vendor_name = '---'
        self._device_product_name = '---'
        self._device_port = ""
        self.idcom.set_disconnected()
        l = self.current_lang
        self.status_label.setText(TRANSLATIONS["status_disconnected"].get(l, "⚠ Disconnected!"))
        self._update_status_style()
        self._clear_pit_file(log_removed=True)
        self.pass_fail_label.clear()
        self.time_label.clear()
        self._update_buttons()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(TRANSLATIONS["progress_ready"].get(self.current_lang, "Ready"))
        self.progress_bar.setRange(0, 100)

    def _is_home_csc_file(self, path):
        return os.path.basename(path or "").upper().startswith("HOME_CSC")

    def _on_file_changed(self, slot, path, members):
        with QMutexLocker(self._mutex):
            self._slot_files[slot] = path
            self._slot_members[slot] = members
        if path:
            if slot == "CSC":
                if self._is_home_csc_file(path):
                    self.log(TRANSLATIONS["home_csc_selected"].get(self.current_lang, "[CSC] HOME_CSC selected; user data should be preserved."))
                else:
                    QMessageBox.warning(self, "Uyarı", TRANSLATIONS["csc_warning"].get(self.current_lang, "⚠ CSC file selected!"))
        self._update_buttons()

    def _clear_pit_file(self, log_removed=False):
        pit_path = self._pit_path
        self._pit_path = None
        if pit_path and os.path.exists(pit_path):
            try:
                os.remove(pit_path)
                if log_removed:
                    self.log(TRANSLATIONS["pit_temp_deleted"].get(self.current_lang, "[PIT] Temporary PIT file removed."))
            except Exception as e:
                self.log(f"[PIT] Cleanup error: {e}")

    def _download_pit(self):
        if self._pit_downloader and self._pit_downloader.isRunning():
            self._pit_downloader.abort()
            self._pit_downloader.wait(2000)

        # Always fetch a fresh PIT from the connected device.
        self._clear_pit_file(log_removed=False)
        self._pit_downloader = PITDownloader(self.current_lang)
        self._pit_downloader.log_message.connect(self.log)
        self._pit_downloader.pit_ready.connect(self._on_pit_ready)
        self._pit_downloader.pit_failed.connect(self._on_pit_failed)
        self._pit_downloader.start()

    def _on_pit_ready(self, path):
        self._pit_path = path
        self.log(TRANSLATIONS["pit_success"].get(self.current_lang, "[PIT] ✓ PIT file received successfully."))
        self.log(TRANSLATIONS["pit_ready_flash"].get(self.current_lang, "[PIT] PIT file is ready for flashing."))

    @pyqtSlot(str, str)
    def _on_pit_failed(self, error_code, error_arg):
        if error_code == "pit_failed":
            reason = TRANSLATIONS["pit_failed"].get(self.current_lang, "PIT download failed (exit code: {})").format(error_arg)
        elif error_code == "pit_timeout":
            reason = TRANSLATIONS["pit_timeout"].get(self.current_lang, "PIT download timeout (30s)")
        elif error_code == "pit_connection_lost":
            reason = TRANSLATIONS["pit_connection_lost"].get(self.current_lang, "Device connection lost")
        elif error_code == "pit_generic_error":
            reason = TRANSLATIONS["pit_generic_error"].get(self.current_lang, "Error: {}").format(error_arg)
        else:
            reason = error_arg or error_code
        msg = TRANSLATIONS["pit_failure"].get(self.current_lang, "[PIT] Failed: {}").format(reason)
        self.log(msg, True)
        self.log(TRANSLATIONS["pit_not_available"].get(self.current_lang, "[PIT] WARNING: PIT file not available!"), True)

    def _on_start(self):
        if not self._device_connected:
            msg = TRANSLATIONS["device_not_connected_error"].get(self.current_lang, "[WARN] No device connected!")
            self.log(msg)
            return

        missing = check_dependencies()
        if missing:
            msg = TRANSLATIONS["missing_tools_error"][self.current_lang] + ', '.join(missing)
            QMessageBox.critical(self, "Eksik Bağımlılık", msg)
            return

        free = get_free_space_gb(TMP_DIR)
        if free != -1 and free < MIN_FREE_SPACE_GB:
            warn = TRANSLATIONS["low_disk_space"][self.current_lang].format(MIN_FREE_SPACE_GB)
            QMessageBox.warning(self, "Disk Alanı", warn)

        with QMutexLocker(self._mutex):
            if self._flashing: return
            self._flashing = True

        selected = {s: p for s, p in self._slot_files.items() if p}
        if not selected:
            self._flashing = False
            QMessageBox.warning(self, "Uyarı", "Dosya seçmelisiniz!")
            return

        if not self._pit_path or not os.path.exists(self._pit_path):
            pit_reply = self._confirm_yes_no(
                TRANSLATIONS["pit_missing_title"].get(self.current_lang, "PIT Not Available"),
                TRANSLATIONS["pit_missing_confirm"].get(self.current_lang, "PIT file could not be obtained. Are you sure you want to continue without PIT?"),
                QMessageBox.StandardButton.No
            )
            if not pit_reply:
                self._flashing = False
                return

        if "CSC" in selected and not self._is_home_csc_file(selected.get("CSC")):
            if not self._confirm_yes_no("CSC Uyarısı", TRANSLATIONS["csc_confirm"][self.current_lang]):
                self._flashing = False
                return

        dry_run = self.chk_dry_run.isChecked()
        with QMutexLocker(self._mutex):
            self._flashing_start_time = time.time()
        self._start_elapsed_timer()
        if self._pass_fail_timer:
            self._pass_fail_timer.stop()
            self._pass_fail_timer = None
        self._update_buttons()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(TRANSLATIONS["analyzing"].get(self.current_lang, "Analyzing..."))
        self.progress_bar.setRange(0, 0)
        dry_run_text = " (DRY RUN)" if dry_run else ""
        msg = TRANSLATIONS["flash_process_started"].get(self.current_lang, "[START] Flash process started...{}").format(dry_run_text)
        self.log(msg, True)
        self.tabs.setCurrentIndex(0)
        self.pass_fail_label.clear()
        filter_map = {}
        for slot in selected:
            row = self.slot_rows.get(slot)
            if row and row.surgical_mode_active:
                filter_map[slot] = list(self._slot_members.get(slot, []))
            else:
                filter_map[slot] = None
        self._file_analyzer = FileAnalyzer(selected, filter_map, self.current_lang)
        self._file_analyzer.log_message.connect(self.log)
        self._file_analyzer.analysis_done.connect(self._on_analysis_done)
        self._file_analyzer.error.connect(self._on_flash_error)
        self._file_analyzer.start()

    def _on_cancel(self):
        if self._flashing:
            if self._flash_worker and self._flash_worker.isRunning():
                if self._confirm_yes_no("İptal", "Flaşlama iptal edilsin mi?"):
                    self._flash_worker.abort("user_cancel")
                    self.btn_cancel.setEnabled(False)
            elif self._file_analyzer and self._file_analyzer.isRunning():
                if self._confirm_yes_no("İptal", "Dosya analizi iptal edilsin mi?"):
                    self._file_analyzer.abort()
                    self.btn_cancel.setEnabled(False)
        elif self._pit_downloader and self._pit_downloader.isRunning():
            self._pit_downloader.abort()
            self.log(TRANSLATIONS["pit_cancelled"].get(self.current_lang, "[PIT] Cancelled."))

    def _get_pit_partitions(self):
        if not self._pit_path or not os.path.exists(self._pit_path): return []
        try:
            res = subprocess.run(["heimdall", "print-pit", "--file", self._pit_path], capture_output=True, text=True)
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            partitions = re.findall(r'Partition Name\s*:\s*(\S+)', output)
            if not partitions:
                self.log(TRANSLATIONS["pit_parse_failed"].get(self.current_lang, "[PIT] WARNING: PIT file could not be parsed."))
            return partitions
        except Exception as e:
            self.log(TRANSLATIONS["pit_parse_failed"].get(self.current_lang, "[PIT] WARNING: PIT file could not be parsed."))
            return []

    def _guess_partition_from_filename(self, filename):
        token = filename.lower()
        # Remove common firmware/compression suffixes repeatedly.
        while True:
            updated = re.sub(r"\.(img|bin|mbn|elf|lz4|ext4|sparse)$", "", token)
            if updated == token:
                break
            token = updated
        token = re.sub(r"[^a-z0-9_]", "_", token)
        token = re.sub(r"_+", "_", token).strip("_")
        if not token:
            return None
        return token.upper()

    def _pit_alias_candidates(self, partition, filename):
        partition = (partition or "").upper()
        guessed = self._guess_partition_from_filename(filename)
        candidates = []
        if guessed:
            candidates.append(guessed)
        aliases = {
            "SBOOT": ["BOOTLOADER", "ABOOT", "SBL"],
            "BOOTLOADER": ["SBOOT", "ABOOT", "SBL"],
            "XBL": ["XBL_A", "XBL_B"],
            "ABL": ["ABL_A", "ABL_B"],
            "VENDOR_BOOT": ["VENDOR_BOOT_A", "VENDOR_BOOT_B"],
            "INIT_BOOT": ["INIT_BOOT_A", "INIT_BOOT_B"],
            "VBMETA": ["VBMETA_A", "VBMETA_B"],
            "VBMETA_SYSTEM": ["VBMETA_SYSTEM_A", "VBMETA_SYSTEM_B"],
            "VBMETA_VENDOR": ["VBMETA_VENDOR_A", "VBMETA_VENDOR_B"],
            "CP": ["MODEM", "RADIO"],
            "MODEM": ["CP", "RADIO"],
            "RADIO": ["CP", "MODEM"],
            "CSC": ["CACHE", "HIDDEN", "OMR", "PRISM", "OPTICS"],
            "CACHE": ["CSC"],
            "HIDDEN": ["CSC", "OMR"],
            "USERDATA": ["DATA"],
        }
        candidates.extend(aliases.get(partition, []))
        seen = set()
        return [c for c in candidates if c and not (c in seen or seen.add(c))]

    def _ensure_partition_exists_in_pit(self, slot, finfo, pit_partitions, pit_lookup):
        if not pit_lookup:
            return True
        current = finfo["partition"].upper()
        if current in pit_lookup:
            finfo["partition"] = pit_lookup[current]
            return True
        for candidate in self._pit_alias_candidates(current, finfo["clean"]):
            if candidate in pit_lookup:
                old_partition = finfo["partition"]
                finfo["partition"] = pit_lookup[candidate]
                finfo["confidence"] = "pit_alias"
                self.log(TRANSLATIONS["ana_auto_map"].get(self.current_lang, "[ANA] Auto map: {} -> {}").format(f"{finfo['clean']} ({old_partition})", finfo["partition"]))
                return True

        msg = TRANSLATIONS["analysis_warning"].get(self.current_lang, "[ANA] ⚠ Uncertain file in {} slot: {}").format(slot, f"{finfo['clean']} -> {finfo['partition']}")
        self.log(msg)
        dlg = UnknownPartitionDialog(finfo["clean"], slot, pit_partitions, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected_partition = dlg.get_partition()
            if selected_partition.upper() not in pit_lookup:
                finfo["skip"] = True
                self.log(TRANSLATIONS["analysis_skip"].get(self.current_lang, "[ANA] ✕ File skipped: {}").format(finfo["clean"]))
                return False
            finfo["partition"] = pit_lookup[selected_partition.upper()]
            finfo["confidence"] = "user_selected"
            return True
        finfo["skip"] = True
        self.log(TRANSLATIONS["analysis_skip"].get(self.current_lang, "[ANA] ✕ File skipped: {}").format(finfo["clean"]))
        return False

    def _is_risky_partition(self, partition):
        risky = {
            "BOOTLOADER", "SBOOT", "ABOOT", "SBL", "XBL", "ABL", "TZ", "HYP",
            "KEYMASTER", "CMNLIB", "CMNLIB64", "DEVCFG", "QUPFW", "AOP",
            "LKSECAPP", "EFS", "SEC_EFS", "PERSIST", "PARAM", "UP_PARAM",
            "USERDATA", "PIT", "BOOT", "RECOVERY"
        }
        return (partition or "").upper() in risky

    def _requires_pit_confirmation(self, finfo):
        return finfo.get("confidence") in {"default", "exact", "pit_alias"} and self._is_risky_partition(finfo.get("partition", ""))

    def _finalize_partition_for_flash(self, finfo, pit_lookup):
        if not pit_lookup:
            return not self._requires_pit_confirmation(finfo)
        partition = finfo["partition"].upper()
        if partition in pit_lookup:
            finfo["partition"] = pit_lookup[partition]
            return True
        for candidate in self._pit_alias_candidates(partition, finfo["clean"]):
            if candidate in pit_lookup:
                old_partition = finfo["partition"]
                finfo["partition"] = pit_lookup[candidate]
                finfo["confidence"] = "pit_alias"
                self.log(TRANSLATIONS["ana_auto_map"].get(self.current_lang, "[ANA] Auto map: {} -> {}").format(f"{finfo['clean']} ({old_partition})", finfo["partition"]))
                return True
        self.log(TRANSLATIONS["analysis_skip_not_in_pit"].get(self.current_lang, "[ANA] ✕ Partition not found in PIT, skipped: {} -> {}").format(finfo["clean"], finfo["partition"]))
        return False

    @pyqtSlot(dict)
    def _on_analysis_done(self, results):
        self.log(TRANSLATIONS["analysis_complete"].get(self.current_lang, "[ANA] ✓ File analysis complete"))
        pit_partitions = self._get_pit_partitions()
        pit_lookup = {p.upper(): p for p in pit_partitions}
        for slot, files in results.items():
            for finfo in files:
                if not pit_lookup and self._requires_pit_confirmation(finfo):
                    finfo["skip"] = True
                    self.log(TRANSLATIONS["analysis_skip_unsafe_no_pit"].get(self.current_lang, "[ANA] ✕ Unsafe partition skipped without PIT confirmation: {} -> {}").format(finfo["clean"], finfo["partition"]))
                    continue
                if finfo["confidence"] == "default":
                    guessed = self._guess_partition_from_filename(finfo["clean"])
                    if guessed and guessed in pit_lookup:
                        finfo["partition"] = pit_lookup[guessed]
                        finfo["confidence"] = "pit_filename"
                        self.log(TRANSLATIONS["ana_auto_map"].get(self.current_lang, "[ANA] Auto map: {} -> {}").format(finfo["clean"], finfo["partition"]))
                        continue

                    row = self.slot_rows.get(slot)
                    if not (row and row.surgical_mode_active):
                        # Odin-like automatic behavior: continue with slot default without prompting.
                        self.log(TRANSLATIONS["ana_auto_fallback"].get(self.current_lang, "[ANA] Auto fallback: {} -> {} ({} default)").format(finfo["clean"], finfo["partition"], slot))
                        self._ensure_partition_exists_in_pit(slot, finfo, pit_partitions, pit_lookup)
                        continue

                    msg = TRANSLATIONS["analysis_warning"].get(self.current_lang, "[ANA] ⚠ Uncertain file in {} slot: {}").format(slot, finfo['clean'])
                    self.log(msg)
                    dlg = UnknownPartitionDialog(finfo['clean'], slot, pit_partitions, self)
                    if dlg.exec() == QDialog.DialogCode.Accepted:
                        finfo["partition"] = dlg.get_partition()
                        finfo["confidence"] = "user_selected"
                    else:
                        finfo["skip"] = True
                        msg = TRANSLATIONS["analysis_skip"].get(self.current_lang, "[ANA] ✕ File skipped: {}").format(finfo['clean'])
                        self.log(msg)
                elif finfo["confidence"] == "unmapped":
                    guessed = self._guess_partition_from_filename(finfo["clean"])
                    if guessed and guessed in pit_lookup:
                        finfo["partition"] = pit_lookup[guessed]
                        finfo["confidence"] = "pit_filename"
                        self.log(TRANSLATIONS["ana_auto_map"].get(self.current_lang, "[ANA] Auto map: {} -> {}").format(finfo["clean"], finfo["partition"]))
                    else:
                        finfo["skip"] = True
                        self.log(TRANSLATIONS["analysis_skip_unmapped"].get(self.current_lang, "[ANA] ✕ Unmapped file skipped: {} ({})").format(finfo["clean"], slot))
                if not finfo.get("skip"):
                    self._ensure_partition_exists_in_pit(slot, finfo, pit_partitions, pit_lookup)
        flash_args = []
        seen_partitions = set()
        plan_items = []
        skipped_items = []
        duplicate_items = []
        risky_partitions = set()
        for slot, files in results.items():
            for finfo in files:
                if not finfo.get("skip"):
                    if not self._finalize_partition_for_flash(finfo, pit_lookup):
                        skipped_items.append(f"{slot}: {finfo.get('clean', finfo.get('original', '?'))}")
                        continue
                    partition = finfo["partition"].upper()
                    if partition in seen_partitions:
                        duplicate_msg = TRANSLATIONS["ana_skip_duplicate"].get(self.current_lang, "[ANA] Skip duplicate partition: {} ({})").format(partition, finfo["clean"])
                        self.log(duplicate_msg)
                        duplicate_items.append(f"{partition}: {finfo['clean']}")
                        continue
                    seen_partitions.add(partition)
                    if self._is_risky_partition(partition):
                        risky_partitions.add(partition)
                    plan_items.append({
                        "slot": slot,
                        "file": finfo["clean"],
                        "partition": finfo["partition"],
                    })
                    flash_args += [f"--{finfo['partition']}", finfo["path"]]
                else:
                    skipped_items.append(f"{slot}: {finfo.get('clean', finfo.get('original', '?'))}")
        if not flash_args:
            self._on_flash_error("Flaş edilecek dosya kalmadı!")
            return
        auto_reboot = self.chk_auto_reboot.isChecked()
        repartition = self.chk_repartition.isChecked()
        dry_run = self.chk_dry_run.isChecked()
        if repartition: self.log(TRANSLATIONS["option_repartition"].get(self.current_lang, "[OPT] ⚠ Re-Partition active."))
        if auto_reboot: self.log(TRANSLATIONS["option_auto_reboot"].get(self.current_lang, "[OPT] Auto Reboot will be applied."))
        if dry_run:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setFormat("Kuru Çalışma")
        else:
            self.progress_bar.setFormat(TRANSLATIONS["flashing"].get(self.current_lang, "Flashing..."))
        pit_path = self._pit_path if self._pit_path and os.path.exists(self._pit_path) else ""
        self._flash_worker = FlashWorker(
            flash_args,
            pit_path,
            auto_reboot,
            repartition,
            dry_run,
            sudo_password=self._sudo_password,
            lang=self.current_lang
        )
        self._flash_worker.progress.connect(self._on_flash_progress)
        self._flash_worker.partition_progress.connect(self._on_partition_progress)
        self._flash_worker.log_message.connect(self.log)
        self._flash_worker.finished.connect(self._on_flash_finished)
        self._flash_worker.start()
        self.btn_cancel.setEnabled(True)

    def _on_flash_progress(self, value):
        if value == -1: self.progress_bar.setRange(0, 0)
        else: self.progress_bar.setRange(0, 100); self.progress_bar.setValue(value)

    @pyqtSlot(str, int, int)
    def _on_partition_progress(self, partition, percent, overall):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        l = self.current_lang
        self.progress_bar.setFormat(TRANSLATIONS["flashing_partition"].get(l, "{} flashing... {}%").format(partition, percent))

    def _start_elapsed_timer(self):
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)
        self._elapsed_timer.start(1000)
        self._update_elapsed_time()

    def _stop_elapsed_timer(self):
        if self._elapsed_timer:
            self._elapsed_timer.stop()
            self._elapsed_timer = None

    def _update_elapsed_time(self):
        if not self._flashing_start_time:
            return
        elapsed = int(time.time() - self._flashing_start_time)
        self.time_label.setText(f"{TRANSLATIONS['time_estimate'].get(self.current_lang, 'Time: ')}{elapsed}s")

    @pyqtSlot(bool, str)
    def _on_flash_finished(self, success, message):
        self._flashing = False
        self._stop_elapsed_timer()
        self._update_buttons()
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        l = self.current_lang
        if success:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("✓ Tamamlandı!")
            self.pass_fail_label.setText(TRANSLATIONS["pass_text"].get(l, "PASS!"))
            color = "#39d353" if self.is_dark_mode else "#116329"
            self.pass_fail_label.setStyleSheet(f"color: {color};")
            self._start_pulse_animation(color)
            msg = TRANSLATIONS["success_message"].get(l, "[✓] {}").format(message)
            self.log(msg, True)
            elapsed = time.time() - self._flashing_start_time if self._flashing_start_time else 0
            self.time_label.setText(f"{TRANSLATIONS['time_estimate'].get(l, 'Time: ')}{elapsed:.0f}s (tamamlandı)")
            QMessageBox.information(self, "Başarılı", f"✓ {message}")
        else:
            self._on_flash_error(message)

    def _start_pulse_animation(self, color):
        """PASS/FAIL etiketi için pulse (yanıp sönme) animasyonu"""
        self._pulse_timer = QTimer(self)
        self._pulse_state = False
        original_style = f"color: {color};"
        pulse_style = f"color: {color}; text-shadow: 0 0 10px {color};"
        
        def toggle_pulse():
            self._pulse_state = not self._pulse_state
            self.pass_fail_label.setStyleSheet(pulse_style if self._pulse_state else original_style)
        
        self._pulse_timer.timeout.connect(toggle_pulse)
        self._pulse_timer.start(500)
        QTimer.singleShot(5000, self._pulse_timer.stop)

    def _reset_pass_fail(self):
        if not self._flashing:
            self.pass_fail_label.clear()
            self.time_label.clear()
            self._pass_fail_timer = None

    def _on_flash_error(self, msg):
        self._flashing = False
        self._stop_elapsed_timer()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFormat("✗ Hata!")
        self.progress_bar.setValue(0)
        self._update_buttons()
        self.btn_cancel.setEnabled(False)
        l = self.current_lang
        self.pass_fail_label.setText(TRANSLATIONS["fail_text"].get(l, "FAIL!"))
        color = "#ff4444" if self.is_dark_mode else "#cf222e"
        self.pass_fail_label.setStyleSheet(f"color: {color};")
        self._start_pulse_animation(color)
        self.time_label.clear()
        msg = TRANSLATIONS["error_message"].get(l, "[✗] ERROR: {}").format(msg)
        self.log(msg, True)
        QMessageBox.critical(self, "Hata", msg)
        self._pass_fail_timer = QTimer.singleShot(5000, self._reset_pass_fail)

    def _on_reset(self):
        self._stop_elapsed_timer()
        for row in self.slot_rows.values(): row._clear()
        self._slot_files = {s: "" for s in SUPPORTED_SLOTS}
        self._slot_members = {s: [] for s in SUPPORTED_SLOTS}
        self._log_entries.clear()
        self.log_screen.clear()
        self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0); self.progress_bar.setFormat(TRANSLATIONS["progress_ready"].get(self.current_lang, "Ready"))
        self.pass_fail_label.clear(); self.time_label.clear()
        self.chk_auto_reboot.setChecked(True); self.chk_repartition.setChecked(False); self.chk_dry_run.setChecked(False)
        self._update_buttons()

    def _update_buttons(self):
        missing = check_dependencies()
        can_start = any(self._slot_files.values()) and not self._flashing and not missing
        magisk_busy = self._magisk_worker is not None and self._magisk_worker.isRunning()
        self.btn_start.setEnabled(can_start)
        self.btn_reset.setEnabled(not self._flashing)
        self.btn_cancel.setEnabled(self._flashing)
        self.btn_theme.setEnabled(not self._flashing)
        self.btn_magisk_patch.setEnabled(not self._flashing and not magisk_busy)
        for chk in (self.chk_auto_reboot, self.chk_repartition, self.chk_dry_run):
            chk.setEnabled(not self._flashing)
        for chk in (
            self.chk_magisk_keep_verity,
            self.chk_magisk_keep_forceencrypt,
            self.chk_magisk_patch_vbmeta,
            self.chk_magisk_recovery,
            self.chk_magisk_legacy_sar,
        ):
            chk.setEnabled(not self._flashing and not magisk_busy)
        for row in self.slot_rows.values():
            row.set_enabled(not self._flashing)

    def _build_log_patterns(self):
        patterns = []
        for key, langs in TRANSLATIONS.items():
            if not isinstance(langs, dict):
                continue
            for lang, template in langs.items():
                if not isinstance(template, str):
                    continue
                if "{}" in template:
                    regex = "^" + re.escape(template).replace(r"\{\}", r"(.+?)") + "$"
                    patterns.append((key, re.compile(regex), True))
                else:
                    patterns.append((key, template, False))
        return patterns

    def _to_log_entry(self, message: str):
        cached = self._log_detect_cache.get(message)
        if cached is not None:
            return dict(cached)
        for key, pattern, is_regex in self._log_patterns:
            if is_regex:
                match = pattern.match(message)
                if match:
                    entry = {"type": "translated", "key": key, "args": list(match.groups())}
                    self._log_detect_cache[message] = entry
                    return dict(entry)
            else:
                if message == pattern:
                    entry = {"type": "translated", "key": key, "args": []}
                    self._log_detect_cache[message] = entry
                    return dict(entry)
        entry = {"type": "raw", "text": message}
        self._log_detect_cache[message] = entry
        return dict(entry)

    def _entry_to_message(self, entry):
        if entry.get("type") == "translated":
            key = entry.get("key", "")
            args = entry.get("args", [])
            if key == "pit_failure" and args:
                nested = self._to_log_entry(args[0])
                args = [self._entry_to_message(nested)]
            template = TRANSLATIONS.get(key, {}).get(self.current_lang) or TRANSLATIONS.get(key, {}).get("en")
            if template:
                try:
                    return template.format(*args)
                except Exception:
                    return template
        return entry.get("text", "")

    def _append_log_line(self, message: str):
        arrow = "➜"
        arrow_color = "#4a6741" if self.is_dark_mode else "#0f5323"
        if hasattr(self, 'log_screen') and self.log_screen:
            self.log_screen.append(f"<span style='color:{arrow_color}; opacity: 0.7;'>{arrow}</span> <span style='opacity: 0.9;'>{message}</span>")
            self.log_screen.moveCursor(QTextCursor.MoveOperation.End)

    def _rerender_log_screen(self):
        if not hasattr(self, 'log_screen') or not self.log_screen:
            return
        self.log_screen.clear()
        for entry in self._log_entries:
            self._append_log_line(self._entry_to_message(entry))

    def log(self, message: str, to_file: bool = False):
        entry = self._to_log_entry(message)
        self._log_entries.append(entry)
        self._append_log_line(self._entry_to_message(entry))
        arrow = "➜"
        rotate_log()
        with open(LOG_FILE, "a") as f:
            f.write(f"{arrow} {message}\n")

    def closeEvent(self, event):
        self._stop_elapsed_timer()
        if self._flashing:
            if not self._confirm_yes_no("Çıkış", "Flaşlama devam ediyor. Çıkmak istediğinize emin misiniz?"):
                event.ignore(); return
            if self._flash_worker and self._flash_worker.isRunning():
                self._flash_worker.abort("window_close")

        threads = [
            self._usb_monitor,
            self._pit_downloader,
            self._file_analyzer,
            self._flash_worker,
            self._magisk_worker
        ]

        for thread in threads:
            if thread and thread.isRunning():
                if hasattr(thread, 'abort'):
                    thread.abort()
                if hasattr(thread, 'stop'):
                    thread.stop()
                thread.requestInterruption()
                thread.quit()
                thread.wait(2000)
                if thread.isRunning():
                    thread.terminate()
                    thread.wait(500)

        if self._usb_monitor:
            self._usb_monitor.stop()

        try:
            if os.path.exists(TMP_DIR):
                for root, dirs, files in os.walk(TMP_DIR, topdown=False):
                    for name in files:
                        file_path = os.path.join(root, name)
                        try:
                            os.unlink(file_path)
                        except PermissionError:
                            try:
                                os.chmod(file_path, 0o700)
                                os.unlink(file_path)
                            except Exception as e:
                                with open(LOG_FILE, "a") as lf:
                                    lf.write(f"[CLEANUP] chmod/unlink hatası: {file_path} - {e}\n")
                        except Exception as e:
                            with open(LOG_FILE, "a") as lf:
                                lf.write(f"[CLEANUP] unlink hatası: {file_path} - {e}\n")
                    for name in dirs:
                        dir_path = os.path.join(root, name)
                        try:
                            os.rmdir(dir_path)
                        except Exception as e:
                            with open(LOG_FILE, "a") as lf:
                                lf.write(f"[CLEANUP] rmdir hatası: {dir_path} - {e}\n")
                try:
                    os.rmdir(TMP_DIR)
                except Exception as e:
                    with open(LOG_FILE, "a") as lf:
                        lf.write(f"[CLEANUP] TMP_DIR silinemedi: {e}\n")
        except Exception as e:
            with open(LOG_FILE, "a") as lf:
                lf.write(f"[CLEANUP] Genel temizlik hatası: {e}\n")
        rotate_log()
        with open(LOG_FILE, "a") as f:
            f.write(f"{'='*50}\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - OpenFlasher oturumu kapandı\n{'='*50}\n\n")
        self._sudo_password = ""
        event.accept()

def main():
    missing = check_dependencies()
    if missing:
        app = QApplication(sys.argv)
        app.setWindowIcon(get_app_icon())
        msg = QMessageBox()
        msg.setWindowTitle("Eksik Bağımlılıklar")
        msg.setText(f"Aşağıdaki bağımlılıklar eksik:\n{', '.join(missing)}\n\nKurulum scripti (install_deps.sh) çalıştırılsın mı?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            subprocess.Popen(["bash", os.path.join(os.path.dirname(os.path.abspath(__file__)), "install_deps.sh")])
        sys.exit(1)
    
    app = QApplication(sys.argv)
    app.setApplicationName("OpenFlasher")
    app.setApplicationVersion("1.0")
    app.setWindowIcon(get_app_icon())
    system_is_dark = get_system_theme()
    window = MainWindow(default_dark=system_is_dark)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()              
