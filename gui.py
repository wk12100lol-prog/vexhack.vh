import os, sys, json, time, struct, zlib, math, random, zipfile, io, subprocess
from datetime import datetime
from urllib.request import urlopen, Request, HTTPError
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTreeWidget, QTreeWidgetItem, QSplitter,
    QProgressBar, QComboBox, QFrame, QFileDialog, QMessageBox,
    QMenu, QAbstractItemView, QTextEdit, QCheckBox, QLineEdit,
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QSlider, QButtonGroup, QRadioButton, QGroupBox, QFormLayout,
    QSpinBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPointF, QRectF, QSize
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QBrush, QDragEnterEvent, QDropEvent, QFontDatabase, QCursor, QAction, QIcon, QPixmap
from compression import CMPArchive, VHArchive, CMPCompressor, VHCompressor

VERSION = "2.6.0"
GITHUB_REPO = "wk12100lol-prog/vexhack.vh"

ARCHIVERS = {
    "CMP": {"ext": ".cmp", "cls": CMPArchive, "color": "#ff6b9d", "desc": "Standard"},
    "VH":  {"ext": ".vh",  "cls": VHArchive,  "color": "#00ffa3", "desc": "Very High"},
}

# ── THEME ──
BG_DARK = "#0d0f1a"
BG_CARD = "rgba(255,255,255,0.04)"
BORDER = "1px solid rgba(255,255,255,0.08)"
FONT_MAIN = "Segoe UI, Arial"
PINK = "#ff6b9d"
GREEN = "#00ffa3"
TEXT = "#c8ccd4"
TEXT_DIM = "#6a6f85"

def _ss(widget, stylesheet):
    widget.setStyleSheet(stylesheet)

def _make_btn(text, color=PINK):
    btn = QPushButton(text)
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    btn.setStyleSheet(f"""
        QPushButton {{
            background: {color}; color: #0d0f1a; font-weight: bold;
            font-family: {FONT_MAIN}; font-size: 13px;
            border: none; border-radius: 6px; padding: 8px 20px;
        }}
        QPushButton:hover {{ opacity: 0.85; }}
        QPushButton:disabled {{ background: #2a2d3a; color: #6a6f85; }}
    """)
    return btn

def _make_card():
    f = QFrame(); f.setStyleSheet(f"background: {BG_CARD}; border: {BORDER}; border-radius: 8px;")
    return f

def _lbl(text, color=TEXT_DIM, size=12):
    l = QLabel(text)
    l.setStyleSheet(f"font-size: {size}px; color: {color}; background: transparent; border: none; font-family: {FONT_MAIN};")
    return l


# ── UPDATE CHECKER ──

class UpdateChecker(QThread):
    finished = pyqtSignal(dict)
    def __init__(self, repo):
        super().__init__()
        self.repo = repo
    def run(self):
        try:
            url = f"https://api.github.com/repos/{self.repo}/releases/latest"
            req = Request(url, headers={"User-Agent": "VEXARCHIVE", "Accept": "application/json"})
            resp = urlopen(req, timeout=8)
            data = json.loads(resp.read().decode())
            tag = data.get("tag_name", "")
            html_url = data.get("html_url", "")
            body = (data.get("body") or "")[:200]
            assets = data.get("assets", [])
            zip_url = None
            for a in assets:
                if a["name"].endswith(".zip"):
                    zip_url = a["browser_download_url"]
                    break
            if not zip_url:
                zip_url = data.get("zipball_url")
            self.finished.emit({"tag": tag, "url": html_url, "body": body, "zip_url": zip_url, "ok": True})
        except HTTPError as e:
            if e.code == 404:
                self.finished.emit({"ok": False, "error": "Brak wydan na GitHub. Utworz pierwszy release!"})
            else:
                self.finished.emit({"ok": False, "error": f"GitHub API: {e.code} {e.reason}"})
        except Exception as e:
            self.finished.emit({"ok": False, "error": str(e)})


class UpdateDownloader(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(dict)
    def __init__(self, zip_url):
        super().__init__()
        self.zip_url = zip_url
    def run(self):
        try:
            req = Request(self.zip_url, headers={"User-Agent": "VEXARCHIVE"})
            resp = urlopen(req, timeout=30)
            total = int(resp.headers.get("Content-Length", 0))
            data = bytearray()
            chunk_size = 65536
            while True:
                chunk = resp.read(chunk_size)
                if not chunk: break
                data.extend(chunk)
                if total:
                    self.progress.emit(int(len(data) / total * 100))
            self.finished.emit({"ok": True, "data": bytes(data)})
        except Exception as e:
            self.finished.emit({"ok": False, "error": str(e)})


# ── ARCHIVE SCANNER ──

class ArchiveScanner(QThread):
    progress = pyqtSignal(int, str)
    found = pyqtSignal(str, int, str, str)
    scanning = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, drives, excluded_dirs=None):
        super().__init__()
        self.drives = drives
        self._excluded = tuple(excluded_dirs) if excluded_dirs else ()
        self._stop = False
    def stop(self):
        self._stop = True

    def _skip_dir(self, dp):
        prefixes = self._excluded or (r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData", r"C:\$Recycle.Bin", r"C:\System Volume Information")
        return dp.startswith(prefixes)

    def _count_dirs(self):
        total = 0
        for drive in self.drives:
            if self._stop: return total
            self.progress.emit(-1, f"Zliczam katalogi na {drive}...")
            for root, dirs, files in os.walk(drive, topdown=True):
                if self._stop: return total
                try:
                    for d in list(dirs):
                        if self._skip_dir(os.path.join(root, d)):
                            dirs.remove(d)
                    total += 1
                except: pass
        return total

    def run(self):
        exts = (".cmp", ".vh")
        count = 0
        total_dirs = self._count_dirs()
        dirs_done = 0
        update_interval = max(1, total_dirs // 200) if total_dirs > 0 else 50
        for drive in self.drives:
            if self._stop: break
            for root, dirs, files in os.walk(drive, topdown=True):
                if self._stop: break
                try:
                    for d in list(dirs):
                        if self._skip_dir(os.path.join(root, d)):
                            dirs.remove(d)
                    for f in files:
                        if f.lower().endswith(exts):
                            fp = os.path.join(root, f)
                            try:
                                sz = os.path.getsize(fp)
                                mt = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
                                fmt = "CMP" if f.lower().endswith(".cmp") else "VH"
                                self.found.emit(fp, sz, fmt, mt)
                                count += 1
                            except: pass
                except: pass
                dirs_done += 1
                if dirs_done % update_interval == 0:
                    pct = min(99, int(dirs_done / total_dirs * 100)) if total_dirs > 0 else 0
                    self.progress.emit(pct, f"{drive}: {count} znalezionych ({dirs_done}/{total_dirs} katalogow)")
                    self.scanning.emit(root)
        self.finished.emit(count)

# ── STATS CHART ──

class StatsChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
    def set_data(self, data):
        self._data = data[:100]
    def paintEvent(self, event):
        if not self._data: return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin = 40
        cw, ch = w - margin * 2, h - margin * 2
        if cw < 10 or ch < 10: return

        p.setPen(QColor(TEXT_DIM))
        p.drawText(QRectF(0, 0, w, margin), Qt.AlignmentFlag.AlignCenter, f"Wspolczynnik kompresji ({len(self._data)} plikow)")

        bars = min(len(self._data), 50)
        bw = cw / bars
        max_ratio = max(r for _, _, _, r, _ in self._data) or 1

        for i in range(bars):
            _, orig, comp, ratio, fmt = self._data[i]
            bh = (ratio / max_ratio) * ch
            x = margin + i * bw
            y = margin + ch - bh
            color = QColor(PINK if fmt == "CMP" else GREEN)
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(int(x), int(y), int(bw) - 1, int(bh))

        # axis labels
        p.setPen(QColor(TEXT_DIM))
        p.drawLine(margin, margin, margin, margin + ch)
        p.drawLine(margin, margin + ch, margin + cw, margin + ch)
        p.drawText(QRectF(0, margin + ch - 10, margin - 4, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{max_ratio*100:.0f}%")
        p.drawText(QRectF(0, margin + ch - 10, margin - 4, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{max_ratio*100:.0f}%")
        p.drawText(QRectF(margin, margin + ch + 4, cw, 20), Qt.AlignmentFlag.AlignCenter, f"Pliki ({bars})")

    def minimumSizeHint(self):
        return QSize(200, 150)

# ── PARTICLES CANVAS ──

class ParticlesWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._particles = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        for _ in range(40):
            self._particles.append({
                "x": random.random(), "y": random.random(),
                "vx": (random.random() - 0.5) * 0.002,
                "vy": (random.random() - 0.5) * 0.002,
                "r": random.uniform(1, 2.5),
                "a": random.uniform(0.1, 0.4),
            })
        self._timer.start(50)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    def _tick(self):
        for p in self._particles:
            p["x"] += p["vx"]; p["y"] += p["vy"]
            if p["x"] < 0 or p["x"] > 1: p["vx"] *= -1
            if p["y"] < 0 or p["y"] > 1: p["vy"] *= -1
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        for pt in self._particles:
            color = QColor(PINK) if random.random() > 0.5 else QColor(GREEN)
            color.setAlphaF(pt["a"])
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(pt["x"] * w, pt["y"] * h), pt["r"], pt["r"])


# ── MAIN WINDOW ──

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"VEXARCHIVE v{VERSION}")
        logo_icon = QIcon(os.path.join(os.path.dirname(__file__) or ".", "logo.png"))
        if not logo_icon.isNull():
            self.setWindowIcon(logo_icon)
        self._settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
        self._settings = self._load_settings()
        self.resize(1100, 720)
        self._setup_ui()
        # drag drop
        self.setAcceptDrops(True)
        # files for packing
        self._pack_files = []
        self._log_lines = []
        self._log("VEXARCHIVE v{} uruchomiony".format(VERSION))
        # discord popup timer
        self._discord_timer = QTimer(self)
        self._discord_timer.timeout.connect(self._maybe_show_discord)
        self._discord_timer.start(60000)

    def _setup_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        cw.setStyleSheet(f"background: {BG_DARK}; font-family: {FONT_MAIN}; color: {TEXT};")

        # particles
        self._particles = ParticlesWidget(cw)
        self._particles.resize(self.width(), self.height())

        # header
        hdr = QWidget()
        hdr.setFixedHeight(50)
        hdr.setStyleSheet("background: rgba(255,255,255,0.03); border-bottom: 1px solid rgba(255,255,255,0.06);")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(16, 0, 16, 0)
        logo_label = QLabel()
        logo_pix = QPixmap(os.path.join(os.path.dirname(__file__) or ".", "logo.png"))
        if not logo_pix.isNull():
            logo_label.setPixmap(logo_pix.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            logo_label.setStyleSheet("background: transparent; border: none;")
            hl.addWidget(logo_label)
        title = QLabel(f"VEXARCHIVE")
        title.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {PINK}; background: transparent; border: none;")
        hl.addWidget(title)
        ver = QLabel(f"v{VERSION}")
        ver.setStyleSheet(f"font-size: 10px; color: {TEXT_DIM}; padding-top: 14px; background: transparent; border: none;")
        hl.addWidget(ver)
        hl.addStretch()
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._settings_btn.setFixedWidth(32)
        self._settings_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {TEXT_DIM}; border: 1px solid transparent; border-radius: 4px; padding: 4px; font-size: 16px; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.06); color: {TEXT}; }}
        """)
        self._settings_btn.clicked.connect(self._show_settings)
        hl.addWidget(self._settings_btn)
        self._about_btn = QPushButton("?")
        self._about_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._about_btn.setFixedWidth(28)
        self._about_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {TEXT_DIM}; border: 1px solid transparent; border-radius: 10px; padding: 2px; font-size: 13px; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.06); color: {GREEN}; }}
        """)
        self._about_btn.clicked.connect(self._show_about)
        hl.addWidget(self._about_btn)
        self._update_btn = QPushButton("⬇ Sprawdz aktualizacje")
        self._update_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._update_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {GREEN}; border: 1px solid {GREEN}; border-radius: 4px; padding: 4px 12px; font-size: 11px; }}
            QPushButton:hover {{ background: rgba(0,255,163,0.1); }}
            QPushButton:disabled {{ color: #2a2d3a; border-color: #2a2d3a; }}
        """)
        self._update_btn.clicked.connect(self._check_updates)
        hl.addWidget(self._update_btn)

        # tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{ background: transparent; border: none; }}
            QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; border: none; padding: 10px 18px; font-size: 12px; font-weight: bold; }}
            QTabBar::tab:selected {{ color: {PINK}; border-bottom: 2px solid {PINK}; }}
            QTabBar::tab:hover {{ color: {TEXT}; }}
        """)
        self._tabs.addTab(self._build_pack_tab(), "Pakowanie")
        self._tabs.addTab(self._build_unpack_tab(), "Rozpakowanie")
        self._tabs.addTab(self._build_preview_tab(), "Podglad")
        self._tabs.addTab(self._build_repair_tab(), "Naprawa")
        self._tabs.addTab(self._build_compare_tab(), "Porownanie")
        self._tabs.addTab(self._build_scanner_tab(), "Skaner")
        self._tabs.addTab(self._build_stats_tab(), "Statystyki")
        self._tabs.addTab(self._build_discord_tab(), "Discord")
        self._tabs.addTab(self._build_log_tab(), "Log")

        # layout
        lo = QVBoxLayout(cw); lo.setContentsMargins(0, 0, 0, 0); lo.setSpacing(0)
        lo.addWidget(hdr)
        lo.addWidget(self._tabs, 1)

    # ── TAB: PACK ──
    def _build_pack_tab(self):
        w = QWidget(); lo = QHBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)

        # left: file list
        left = _make_card()
        ll = QVBoxLayout(left); ll.setContentsMargins(12, 12, 12, 12)
        ll.addWidget(_lbl("Pliki do spakowania:", TEXT, 13))
        self._pack_tree = QTreeWidget()
        self._pack_tree.setHeaderLabels(["Nazwa", "Rozmiar"])
        self._pack_tree.setColumnWidth(0, 250)
        self._pack_tree.setStyleSheet(f"""
            QTreeWidget {{ background: rgba(0,0,0,0.3); border: {BORDER}; border-radius: 4px; color: {TEXT}; font-size: 12px; }}
            QTreeWidget::item {{ padding: 4px; }}
            QHeaderView::section {{ background: rgba(255,255,255,0.05); color: {TEXT_DIM}; border: none; padding: 4px; }}
        """)
        self._pack_tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        ll.addWidget(self._pack_tree, 1)
        btn_row = QHBoxLayout()
        self._add_files_btn = QPushButton("+ Dodaj pliki")
        self._add_files_btn.clicked.connect(self._add_pack_files)
        self._add_folder_btn = QPushButton("+ Dodaj folder")
        self._add_folder_btn.clicked.connect(self._add_pack_folder)
        self._clear_btn = QPushButton("Wyczysc")
        self._clear_btn.clicked.connect(lambda: (self._pack_tree.clear(), self._pack_files.clear()))
        for b in (self._add_files_btn, self._add_folder_btn, self._clear_btn):
            b.setStyleSheet(f"""
                QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT}; border: {BORDER}; border-radius: 4px; padding: 5px 12px; font-size: 11px; }}
                QPushButton:hover {{ background: rgba(255,255,255,0.1); }}
            """)
        btn_row.addWidget(self._add_files_btn); btn_row.addWidget(self._add_folder_btn); btn_row.addStretch(); btn_row.addWidget(self._clear_btn)
        ll.addLayout(btn_row)

        # right: options
        right = QWidget()
        rl = QVBoxLayout(right); rl.setContentsMargins(12, 0, 0, 0)

        opt_card = _make_card()
        ol = QVBoxLayout(opt_card); ol.setContentsMargins(16, 16, 16, 16)
        ol.addWidget(_lbl("Opcje pakowania:", PINK, 14))

        # format
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(_lbl("Format:"))
        self._pack_fmt = QComboBox()
        self._pack_fmt.addItems(list(ARCHIVERS.keys()))
        self._pack_fmt.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        fmt_row.addWidget(self._pack_fmt); fmt_row.addStretch()
        ol.addLayout(fmt_row)

        # compression level
        lvl_row = QHBoxLayout()
        lvl_row.addWidget(_lbl("Poziom kompresji:"))
        self._pack_level = QComboBox()
        self._pack_level.addItems([f"{i} - {'Szybki' if i<=3 else 'Sredni' if i<=7 else 'Max'}" for i in range(1, 11)])
        self._pack_level.setCurrentIndex(4)
        self._pack_level.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        lvl_row.addWidget(self._pack_level); lvl_row.addStretch()
        ol.addLayout(lvl_row)

        # password
        pw_row = QHBoxLayout()
        pw_row.addWidget(_lbl("Haslo (AES-256):"))
        self._pack_password = QLineEdit()
        self._pack_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._pack_password.setPlaceholderText("opcjonalne")
        self._pack_password.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        pw_row.addWidget(self._pack_password, 1)
        ol.addLayout(pw_row)

        # CRC
        self._pack_crc = QCheckBox("Dodaj CRC32")
        self._pack_crc.setChecked(True)
        self._pack_crc.setStyleSheet(f"color: {TEXT}; font-size: 12px;")
        ol.addWidget(self._pack_crc)

        ol.addStretch()

        # pack button
        self._pack_btn = _make_btn("SPAKUJ", PINK)
        self._pack_btn.clicked.connect(self._do_pack)
        ol.addWidget(self._pack_btn)

        rl.addWidget(opt_card)
        rl.addStretch()

        lo.addWidget(left, 2)
        lo.addWidget(right, 1)
        return w

    def _add_pack_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Wybierz pliki")
        for fp in files:
            self._add_pack_file(fp)
    def _add_pack_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Wybierz folder")
        if folder:
            for root, dirs, fnames in os.walk(folder):
                for fn in fnames:
                    fp = os.path.join(root, fn)
                    rel = os.path.relpath(fp, os.path.dirname(folder))
                    self._add_pack_file(fp, rel)
    def _add_pack_file(self, fp, name=None):
        if fp in self._pack_files: return
        self._pack_files.append(fp)
        sz = os.path.getsize(fp)
        item = QTreeWidgetItem([name or os.path.basename(fp), _fmt_size(sz)])
        item.setData(0, Qt.ItemDataRole.UserRole, fp)
        self._pack_tree.addTopLevelItem(item)

    def _do_pack(self):
        if not self._pack_files:
            QMessageBox.warning(self, "Blad", "Nie wybrano plikow.")
            return
        fmt = self._pack_fmt.currentText()
        arch = ARCHIVERS[fmt]
        out_path, _ = QFileDialog.getSaveFileName(self, "Zapisz archiwum", f"archiwum{arch['ext']}", f"{arch['desc']} (*{arch['ext']})")
        if not out_path: return
        pw = self._pack_password.text().strip() or None
        level = self._pack_level.currentIndex() + 1
        use_crc = self._pack_crc.isChecked()
        self._log(f"Pakowanie {len(self._pack_files)} plikow ({fmt}, poziom {level})...")
        self._particles._timer.setInterval(30)
        try:
            files_data = []
            for fp in self._pack_files:
                with open(fp, "rb") as f:
                    files_data.append((os.path.basename(fp), f.read()))
            arch["cls"].pack(files_data, out_path, password=pw, use_crc=use_crc, compression_level=level)
            QMessageBox.information(self, "Gotowe", f"Archiwum utworzone:\n{out_path}")
            self._log(f"OK: {out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Blad", f"Nie udalo sie spakowac:\n{e}")
            self._log(f"BLAD: {e}")
        finally:
            self._particles._timer.setInterval(50)

    # ── TAB: UNPACK ──
    def _build_unpack_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)

        card = _make_card()
        cl = QVBoxLayout(card); cl.setContentsMargins(20, 20, 20, 20)
        cl.addWidget(_lbl("Rozpakowywanie archiwum:", PINK, 14))

        ar = QHBoxLayout()
        ar.addWidget(_lbl("Archiwum:"))
        self._unpack_path = QLineEdit()
        self._unpack_path.setReadOnly(True)
        self._unpack_path.setPlaceholderText("Wybierz plik .cmp lub .vh")
        self._unpack_path.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 6px; border-radius: 4px;")
        ar.addWidget(self._unpack_path, 1)
        self._unpack_browse = QPushButton("Przegladaj")
        self._unpack_browse.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT}; border: {BORDER}; border-radius: 4px; padding: 6px 14px; }} QPushButton:hover {{ background: rgba(255,255,255,0.1); }}")
        self._unpack_browse.clicked.connect(self._browse_unpack)
        ar.addWidget(self._unpack_browse)
        cl.addLayout(ar)

        dr = QHBoxLayout()
        dr.addWidget(_lbl("Katalog wyjsciowy:"))
        self._unpack_out = QLineEdit()
        self._unpack_out.setReadOnly(True)
        self._unpack_out.setPlaceholderText("Wybierz gdzie wypakowac")
        self._unpack_out.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 6px; border-radius: 4px;")
        dr.addWidget(self._unpack_out, 1)
        self._unpack_out_btn = QPushButton("Wybierz")
        self._unpack_out_btn.setStyleSheet(self._unpack_browse.styleSheet())
        self._unpack_out_btn.clicked.connect(lambda: self._unpack_out.setText(QFileDialog.getExistingDirectory(self, "Katalog wyjsciowy") or self._unpack_out.text()))
        dr.addWidget(self._unpack_out_btn)
        cl.addLayout(dr)

        pwr = QHBoxLayout()
        pwr.addWidget(_lbl("Haslo (jesli zaszyfrowane):"))
        self._unpack_password = QLineEdit()
        self._unpack_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._unpack_password.setPlaceholderText("opcjonalne")
        self._unpack_password.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        pwr.addWidget(self._unpack_password, 1)
        cl.addLayout(pwr)

        self._unpack_skip_crc = QCheckBox("Pomin bledy CRC (odzyskiwanie)")
        self._unpack_skip_crc.setStyleSheet(f"color: {TEXT}; font-size: 12px;")
        cl.addWidget(self._unpack_skip_crc)

        cl.addStretch()
        self._unpack_btn = _make_btn("ROZPAKUJ", GREEN)
        self._unpack_btn.clicked.connect(self._do_unpack)
        cl.addWidget(self._unpack_btn)
        lo.addWidget(card)
        lo.addStretch()
        return w

    def _browse_unpack(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz archiwum", "", "Archiwa VEXARCHIVE (*.cmp *.vh);;Wszystkie (*)")
        if path:
            self._unpack_path.setText(path)

    def _do_unpack(self):
        path = self._unpack_path.text()
        out = self._unpack_out.text()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Blad", "Wybierz archiwum.")
            return
        if not out:
            out = os.path.join(os.path.dirname(path), "wypakowane")
        pw = self._unpack_password.text().strip() or None
        skip_crc = self._unpack_skip_crc.isChecked()
        self._log(f"Rozpakowywanie {path}...")
        try:
            # detect format
            with open(path, "rb") as f:
                magic = f.read(4)
            if magic in (CMPArchive.V2_MAGIC, CMPCompressor.MAGIC):
                ArchCls = CMPArchive
                name = "CMP"
            elif magic in (VHArchive.V2_MAGIC, VHCompressor.MAGIC):
                ArchCls = VHArchive
                name = "VH"
            else:
                QMessageBox.warning(self, "Blad", "Nieznany format archiwum")
                return
            files = ArchCls.unpack(path, out, password=pw, skip_crc=skip_crc)
            if files is None:
                QMessageBox.warning(self, "Blad", "Nie mozna odczytac archiwum (zle haslo? uszkodzone?)")
                return
            QMessageBox.information(self, "Gotowe", f"Wypakowano {len(files)} plikow do:\n{out}")
            self._log(f"OK: wypakowano {len(files)} plikow ({name})")
        except Exception as e:
            QMessageBox.critical(self, "Blad", str(e))
            self._log(f"BLAD: {e}")

    # ── TAB: PREVIEW ──
    def _build_preview_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)

        # top bar
        top = QHBoxLayout()
        top.addWidget(_lbl("Archiwum:"))
        self._preview_path = QLineEdit()
        self._preview_path.setReadOnly(True)
        self._preview_path.setPlaceholderText("Wybierz .cmp lub .vh")
        self._preview_path.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 6px; border-radius: 4px;")
        top.addWidget(self._preview_path, 1)
        self._preview_browse = QPushButton("Przegladaj")
        self._preview_browse.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT}; border: {BORDER}; border-radius: 4px; padding: 6px 14px; }} QPushButton:hover {{ background: rgba(255,255,255,0.1); }}")
        self._preview_browse.clicked.connect(lambda: self._load_preview(True))
        top.addWidget(self._preview_browse)
        # password
        self._preview_password = QLineEdit()
        self._preview_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._preview_password.setPlaceholderText("haslo")
        self._preview_password.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px; max-width: 120px;")
        self._preview_password.returnPressed.connect(lambda: self._load_preview(False))
        top.addWidget(self._preview_password)
        lo.addLayout(top)

        # search
        sr = QHBoxLayout()
        sr.addWidget(_lbl("Szukaj:"))
        self._preview_search = QLineEdit()
        self._preview_search.setPlaceholderText("filtruj pliki...")
        self._preview_search.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        self._preview_search.textChanged.connect(self._filter_preview)
        sr.addWidget(self._preview_search, 1)
        self._preview_info = _lbl("", TEXT_DIM, 11)
        sr.addWidget(self._preview_info)
        lo.addLayout(sr)

        self._preview_tree = QTreeWidget()
        self._preview_tree.setHeaderLabels(["Nazwa", "Originalny", "Skompresowany", "Ratio", "CRC", "Szyfr"])
        self._preview_tree.setColumnWidth(0, 280)
        self._preview_tree.setStyleSheet(f"""
            QTreeWidget {{ background: rgba(0,0,0,0.3); border: {BORDER}; border-radius: 4px; color: {TEXT}; font-size: 12px; }}
            QTreeWidget::item {{ padding: 3px; }}
            QHeaderView::section {{ background: rgba(255,255,255,0.05); color: {TEXT_DIM}; border: none; padding: 4px; }}
        """)
        lo.addWidget(self._preview_tree, 1)
        return w

    def _load_preview(self, browse=True):
        if browse:
            path, _ = QFileDialog.getOpenFileName(self, "Wybierz archiwum", "", "Archiwa VEXARCHIVE (*.cmp *.vh);;Wszystkie (*)")
            if not path: return
            self._preview_path.setText(path)
        path = self._preview_path.text()
        if not path or not os.path.isfile(path): return
        pw = self._preview_password.text().strip() or None
        self._preview_tree.clear()
        try:
            with open(path, "rb") as f:
                magic = f.read(4)
            if magic in (CMPArchive.V2_MAGIC, CMPCompressor.MAGIC):
                ArchCls = CMPArchive
            elif magic in (VHArchive.V2_MAGIC, VHCompressor.MAGIC):
                ArchCls = VHArchive
            else:
                QMessageBox.warning(self, "Blad", "Nieznany format")
                return
            files = ArchCls.list_files(path, password=pw)
            if files is None:
                QMessageBox.warning(self, "Blad", "Nie mozna odczytac (zle haslo?)")
                return
            total_orig = 0; total_comp = 0
            for name, orig, comp, has_crc, is_enc, crc_val in files:
                ratio = f"{comp/orig*100:.1f}%" if orig else "-"
                crc_str = f"{crc_val:08X}" if has_crc else "-"
                enc_str = "AES-256" if is_enc else "Nie"
                item = QTreeWidgetItem([name, _fmt_size(orig), _fmt_size(comp), ratio, crc_str, enc_str])
                if ratio != "-":
                    r = comp/orig
                    if r < 0.5: item.setForeground(3, QBrush(QColor(GREEN)))
                    elif r > 0.9: item.setForeground(3, QBrush(QColor("#ff6b6b")))
                self._preview_tree.addTopLevelItem(item)
                total_orig += orig; total_comp += comp
            self._preview_info.setText(f"{len(files)} plikow, {_fmt_size(total_orig)} -> {_fmt_size(total_comp)} ({total_comp/total_orig*100:.1f}%)")
        except Exception as e:
            QMessageBox.warning(self, "Blad", str(e))

    def _filter_preview(self, text):
        text = text.lower()
        for i in range(self._preview_tree.topLevelItemCount()):
            item = self._preview_tree.topLevelItem(i)
            item.setHidden(text not in item.text(0).lower())

    # ── TAB: REPAIR ──
    def _build_repair_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)
        card = _make_card()
        cl = QVBoxLayout(card); cl.setContentsMargins(20, 20, 20, 20)
        cl.addWidget(_lbl("Naprawa uszkodzonego archiwum:", PINK, 14))
        cl.addWidget(_lbl("Proba odzyskania danych z archiwow z blednym CRC lub uszkodzona struktura.", TEXT_DIM, 11))

        ar = QHBoxLayout()
        ar.addWidget(_lbl("Uszkodzone archiwum:"))
        self._repair_in = QLineEdit()
        self._repair_in.setReadOnly(True); self._repair_in.setPlaceholderText("Wybierz .cmp lub .vh")
        self._repair_in.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 6px; border-radius: 4px;")
        ar.addWidget(self._repair_in, 1)
        b = QPushButton("Przegladaj")
        b.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT}; border: {BORDER}; border-radius: 4px; padding: 6px 14px; }} QPushButton:hover {{ background: rgba(255,255,255,0.1); }}")
        b.clicked.connect(lambda: self._repair_in.setText(QFileDialog.getOpenFileName(self, "Wybierz archiwum", "", "Archiwa (*.cmp *.vh)")[0] or self._repair_in.text()))
        ar.addWidget(b)
        cl.addLayout(ar)

        ar2 = QHBoxLayout()
        ar2.addWidget(_lbl("Zapisz jako:"))
        self._repair_out = QLineEdit()
        self._repair_out.setPlaceholderText("np. naprawione.cmp")
        self._repair_out.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 6px; border-radius: 4px;")
        ar2.addWidget(self._repair_out, 1)
        b2 = QPushButton("...")
        b2.setStyleSheet(b.styleSheet())
        b2.clicked.connect(lambda: self._repair_out.setText(QFileDialog.getSaveFileName(self, "Zapisz jako", "naprawione.cmp", "Archiwa (*.cmp *.vh)")[0] or self._repair_out.text()))
        ar2.addWidget(b2)
        cl.addLayout(ar2)

        pwr = QHBoxLayout()
        pwr.addWidget(_lbl("Haslo:"))
        self._repair_password = QLineEdit()
        self._repair_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._repair_password.setPlaceholderText("opcjonalne")
        self._repair_password.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        pwr.addWidget(self._repair_password, 1)
        cl.addLayout(pwr)

        self._repair_btn = _make_btn("NAPRAW", "#ff6b6b")
        self._repair_btn.clicked.connect(self._do_repair)
        cl.addWidget(self._repair_btn)

        # results
        self._repair_result = QTextEdit()
        self._repair_result.setReadOnly(True)
        self._repair_result.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; border-radius: 4px; font-size: 11px; padding: 8px; font-family: Consolas, monospace;")
        self._repair_result.setMaximumHeight(200)
        cl.addWidget(self._repair_result)

        lo.addWidget(card)
        return w

    def _do_repair(self):
        inp = self._repair_in.text()
        out = self._repair_out.text()
        if not inp or not os.path.isfile(inp):
            QMessageBox.warning(self, "Blad", "Wybierz uszkodzone archiwum.")
            return
        if not out:
            base, ext = os.path.splitext(inp)
            out = base + "_repaired" + ext
            self._repair_out.setText(out)
        pw = self._repair_password.text().strip() or None
        self._log(f"Naprawa {inp}...")
        try:
            from repair import repair_archive
            r = repair_archive(inp, out, password=pw)
            txt = f"--- Wynik naprawy ---\n"
            txt += f"Razem: {r['total']}\n"
            txt += f"Odzyskane: {r['recovered']}\n"
            txt += f"Utracone: {r['lost']}\n"
            if r['errors']:
                txt += f"\nBledy ({len(r['errors'])}):\n"
                for e in r['errors'][:50]:
                    txt += f"  - {e}\n"
            if r['recovered'] > 0:
                txt += f"\nZapisano odzyskane pliki do: {out}\n"
            self._repair_result.setText(txt)
            self._log(f"Naprawa: {r['recovered']}/{r['total']} odzyskanych")
        except Exception as e:
            QMessageBox.critical(self, "Blad", str(e))
            self._log(f"BLAD naprawy: {e}")

    # ── TAB: COMPARE ──
    def _build_compare_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)
        lo.addWidget(_lbl("Porownanie CMP vs VH", PINK, 14))
        lo.addWidget(_lbl("Wybierz pliki i skompresuj oboma formatami, aby porownac.", TEXT_DIM, 11))

        cf = _make_card()
        cl = QVBoxLayout(cf); cl.setContentsMargins(16, 16, 16, 16)
        ar = QHBoxLayout()
        ar.addWidget(_lbl("Pliki do testu:"))
        self._cmp_files_btn = QPushButton("Wybierz pliki")
        self._cmp_files_btn.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT}; border: {BORDER}; border-radius: 4px; padding: 6px 14px; }} QPushButton:hover {{ background: rgba(255,255,255,0.1); }}")
        self._cmp_files_btn.clicked.connect(self._select_cmp_files)
        ar.addWidget(self._cmp_files_btn)
        self._cmp_files_label = _lbl("nie wybrano", TEXT_DIM)
        ar.addWidget(self._cmp_files_label, 1)
        cl.addLayout(ar)

        lvl_row = QHBoxLayout()
        lvl_row.addWidget(_lbl("Poziom:"))
        self._cmp_level = QComboBox()
        self._cmp_level.addItems([str(i) for i in range(1, 11)])
        self._cmp_level.setCurrentIndex(4)
        self._cmp_level.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        lvl_row.addWidget(self._cmp_level); lvl_row.addStretch()
        cl.addLayout(lvl_row)

        self._cmp_btn = _make_btn("POROWNAJ", GREEN)
        self._cmp_btn.clicked.connect(self._do_compare)
        cl.addWidget(self._cmp_btn)
        lo.addWidget(cf)

        self._cmp_table = QTableWidget()
        self._cmp_table.setColumnCount(5)
        self._cmp_table.setHorizontalHeaderLabels(["Plik", "Original", "CMP", "VH", "Zwyciezca"])
        self._cmp_table.setStyleSheet(f"""
            QTableWidget {{ background: rgba(0,0,0,0.3); border: {BORDER}; border-radius: 4px; color: {TEXT}; font-size: 12px; gridline-color: rgba(255,255,255,0.05); }}
            QHeaderView::section {{ background: rgba(255,255,255,0.05); color: {TEXT_DIM}; border: none; padding: 6px; font-weight: bold; }}
        """)
        self._cmp_table.horizontalHeader().setStretchLastSection(True)
        lo.addWidget(self._cmp_table, 1)
        return w

    def _select_cmp_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Wybierz pliki do porownania")
        if files:
            self._cmp_files = files
            self._cmp_files_label.setText(f"{len(files)} plikow")

    def _do_compare(self):
        if not hasattr(self, '_cmp_files') or not self._cmp_files:
            QMessageBox.warning(self, "Blad", "Wybierz pliki do porownania")
            return
        level = self._cmp_level.currentIndex() + 1
        cmp_passes = max(1, level); cmp_pairs = max(8, level * 3)
        vh_passes = max(1, level); vh_pairs = max(16, level * 5)
        self._cmp_table.setRowCount(0)
        cmp_total, vh_total, orig_total = 0, 0, 0
        cmp_wins, vh_wins = 0, 0
        for fp in self._cmp_files:
            with open(fp, "rb") as f:
                data = f.read()
            orig_total += len(data)
            cmp_data = CMPCompressor.compress(data, passes=cmp_passes, num_pairs=cmp_pairs)
            vh_data = VHCompressor.compress(data, passes=vh_passes, num_pairs=vh_pairs)
            cmp_total += len(cmp_data); vh_total += len(vh_data)
            row = self._cmp_table.rowCount()
            self._cmp_table.insertRow(row)
            name = os.path.basename(fp)
            winner = "CMP" if len(cmp_data) < len(vh_data) else "VH" if len(vh_data) < len(cmp_data) else "="
            if winner == "CMP": cmp_wins += 1
            elif winner == "VH": vh_wins += 1
            self._cmp_table.setItem(row, 0, QTableWidgetItem(name))
            self._cmp_table.setItem(row, 1, QTableWidgetItem(_fmt_size(len(data))))
            self._cmp_table.setItem(row, 2, QTableWidgetItem(f"{_fmt_size(len(cmp_data))} ({len(cmp_data)/len(data)*100:.1f}%)"))
            self._cmp_table.setItem(row, 3, QTableWidgetItem(f"{_fmt_size(len(vh_data))} ({len(vh_data)/len(data)*100:.1f}%)"))
            witem = QTableWidgetItem(winner)
            witem.setForeground(QBrush(QColor(PINK if winner == "CMP" else GREEN if winner == "VH" else TEXT)))
            self._cmp_table.setItem(row, 4, witem)
        # summary row
        row = self._cmp_table.rowCount()
        self._cmp_table.insertRow(row)
        self._cmp_table.setItem(row, 0, QTableWidgetItem("RAZEM"))
        self._cmp_table.setItem(row, 1, QTableWidgetItem(_fmt_size(orig_total)))
        self._cmp_table.setItem(row, 2, QTableWidgetItem(f"{_fmt_size(cmp_total)} ({cmp_total/orig_total*100:.1f}%)"))
        self._cmp_table.setItem(row, 3, QTableWidgetItem(f"{_fmt_size(vh_total)} ({vh_total/orig_total*100:.1f}%)"))
        witem = QTableWidgetItem(f"CMP {cmp_wins}-{vh_wins} VH")
        witem.setForeground(QBrush(QColor(PINK if cmp_wins > vh_wins else GREEN if vh_wins > cmp_wins else TEXT)))
        self._cmp_table.setItem(row, 4, witem)
        self._cmp_table.resizeColumnsToContents()
        self._log(f"Porownanie: CMP {cmp_total/orig_total*100:.1f}% vs VH {vh_total/orig_total*100:.1f}%")

    # ── TAB: SCANNER ──
    def _build_scanner_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)
        lo.addWidget(_lbl("Skaner archiwow", PINK, 14))
        lo.addWidget(_lbl("Znajdz wszystkie pliki .cmp i .vh na komputerze.", TEXT_DIM, 11))

        top = QHBoxLayout()
        self._scan_btn = _make_btn("🔍 SKANUJ WSZYSTKIE DYSKI", PINK)
        self._scan_btn.clicked.connect(self._start_scan)
        top.addWidget(self._scan_btn)
        self._scan_stop_btn = QPushButton("STOP")
        self._scan_stop_btn.setStyleSheet(f"""
            QPushButton {{ background: #ff6b6b; color: white; font-weight: bold; border: none; border-radius: 6px; padding: 8px 20px; font-size: 13px; }}
            QPushButton:disabled {{ background: #2a2d3a; color: #6a6f85; }}
        """)
        self._scan_stop_btn.clicked.connect(self._stop_scan)
        self._scan_stop_btn.setEnabled(False)
        top.addWidget(self._scan_stop_btn)
        top.addStretch()
        self._scan_status = _lbl("Gotowy", TEXT_DIM, 12)
        top.addWidget(self._scan_status)
        lo.addLayout(top)

        self._scan_progress = QProgressBar()
        self._scan_progress.setStyleSheet(f"""
            QProgressBar {{ background: #1a1c2a; border: none; border-radius: 3px; height: 6px; text-align: center; font-size: 0px; }}
            QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PINK}, stop:1 {GREEN}); border-radius: 3px; }}
        """)
        self._scan_progress.setFixedHeight(6)
        lo.addWidget(self._scan_progress)
        self._scan_current = _lbl("", TEXT_DIM, 10)
        self._scan_current.setStyleSheet(f"font-size: 10px; color: {TEXT_DIM}; background: transparent; border: none; padding: 2px 0;")
        lo.addWidget(self._scan_current)

        self._scan_tree = QTreeWidget()
        self._scan_tree.setHeaderLabels(["Sciezka", "Rozmiar", "Format", "Data modyfikacji"])
        self._scan_tree.setColumnWidth(0, 450)
        self._scan_tree.setSortingEnabled(True)
        self._scan_tree.setStyleSheet(f"""
            QTreeWidget {{ background: rgba(0,0,0,0.3); border: {BORDER}; border-radius: 4px; color: {TEXT}; font-size: 12px; }}
            QTreeWidget::item {{ padding: 3px; }}
            QHeaderView::section {{ background: rgba(255,255,255,0.05); color: {TEXT_DIM}; border: none; padding: 4px; }}
        """)
        self._scan_tree.itemDoubleClicked.connect(self._scan_item_double_click)
        lo.addWidget(self._scan_tree, 1)

        bottom = QHBoxLayout()
        self._scan_count = _lbl("Znaleziono: 0", TEXT_DIM)
        bottom.addWidget(self._scan_count)
        bottom.addStretch()
        self._scan_clear_btn = QPushButton("Wyczysc")
        self._scan_clear_btn.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT}; border: {BORDER}; border-radius: 4px; padding: 4px 12px; }} QPushButton:hover {{ background: rgba(255,255,255,0.1); }}")
        self._scan_clear_btn.clicked.connect(lambda: (self._scan_tree.clear(), self._scan_count.setText("Znaleziono: 0")))
        bottom.addWidget(self._scan_clear_btn)
        lo.addLayout(bottom)
        return w

    def _start_scan(self):
        self._scan_tree.clear()
        self._scan_btn.setEnabled(False)
        self._scan_stop_btn.setEnabled(True)
        self._scan_status.setText("Skanowanie...")
        self._scan_progress.setRange(0, 0)
        self._scan_count.setText("Znaleziono: 0")
        self._scan_found_count = 0
        # get available drives
        import string
        drives = []
        for letter in string.ascii_uppercase:
            d = f"{letter}:\\"
            if os.path.exists(d):
                drives.append(d)
        # use settings excluded dirs
        excluded = [d.strip() for d in self._settings.get("exclude_dirs", "").split(",") if d.strip()]
        self._scanner = ArchiveScanner(drives, excluded)
        self._scanner.progress.connect(self._on_scan_progress)
        self._scanner.found.connect(self._on_scan_found)
        self._scanner.scanning.connect(self._on_scanning)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scanner.start()
        self._log(f"Skanowanie {len(drives)} dyskow...")

    def _stop_scan(self):
        if hasattr(self, '_scanner') and self._scanner.isRunning():
            self._scanner.stop()
            self._scan_status.setText("Zatrzymano")
            self._scan_progress.setRange(0, 100)
            self._scan_progress.setValue(100)
            self._scan_btn.setEnabled(True)
            self._scan_stop_btn.setEnabled(False)
            self._log("Skanowanie zatrzymane")

    def _on_scan_progress(self, val, msg):
        self._scan_status.setText(msg)
        if val < 0:
            self._scan_progress.setRange(0, 0)
        else:
            self._scan_progress.setRange(0, 100)
            self._scan_progress.setValue(val)

    def _on_scan_found(self, path, size, fmt, mtime):
        self._scan_found_count += 1
        item = QTreeWidgetItem([path, _fmt_size(size), fmt, mtime])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        color = PINK if fmt == "CMP" else GREEN
        item.setForeground(2, QBrush(QColor(color)))
        self._scan_tree.addTopLevelItem(item)
        self._scan_count.setText(f"Znaleziono: {self._scan_found_count}")
        if self._scan_found_count % 100 == 0:
            QApplication.processEvents()

    def _on_scanning(self, path):
        self._scan_current.setText(f"  Skanuje: {path}")
        QApplication.processEvents()

    def _on_scan_finished(self, count):
        self._scan_btn.setEnabled(True)
        self._scan_stop_btn.setEnabled(False)
        self._scan_progress.setRange(0, 100)
        self._scan_progress.setValue(100)
        self._scan_status.setText(f"Skanowanie zakonczone: {count} archiwow")
        self._scan_count.setText(f"Znaleziono: {count}")
        self._log(f"Skanowanie zakonczone: {count} archiwow")

    def _scan_item_double_click(self, item, col):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            # load in preview tab
            self._preview_path.setText(path)
            self._load_preview(False)
            self._tabs.setCurrentIndex(2)  # switch to preview tab

    # ── TAB: DISCORD ──
    def _build_discord_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)

        card = _make_card()
        cl = QVBoxLayout(card); cl.setContentsMargins(32, 32, 32, 32)
        cl.setSpacing(16)

        icon_lbl = QLabel("[ 💬 ]")
        icon_lbl.setStyleSheet(f"font-size: 64px; background: transparent; border: none;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(icon_lbl)

        title_lbl = QLabel("DOLACZ DO VEXHACK")
        title_lbl.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {PINK}; background: transparent; border: none;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(title_lbl)

        desc = QLabel(
            "Serwer Discord dla uzytkownikow VEXARCHIVE i VEXHACK.\n\n"
            " ‎• Wspolna zabawa i integracja\n"
            " ‎• Pomoc techniczna i support\n"
            " ‎• Nowosci i aktualizacje\n"
            " ‎• Dzielenie sie archiwami\n"
            " ‎• Tryby, mody, narzedzia\n\n"
            "Dolacz teraz i poznaj reszte ekipy!"
        )
        desc.setStyleSheet(f"font-size: 13px; color: {TEXT}; background: transparent; border: none; line-height: 1.6;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        cl.addWidget(desc)

        status_lbl = QLabel("🟢 SERWER AKTYWNY")
        status_lbl.setStyleSheet(f"font-size: 12px; color: {GREEN}; background: transparent; border: none;")
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(status_lbl)

        cl.addStretch()

        join_btn = QPushButton("   DOLACZ DO DISCORDA   ")
        join_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        join_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5865F2, stop:1 {PINK});
                color: white; font-weight: bold; font-size: 15px;
                border: none; border-radius: 8px; padding: 14px 40px;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
        """)
        join_btn.clicked.connect(self._open_discord_tab)
        cl.addWidget(join_btn, 0, Qt.AlignmentFlag.AlignCenter)

        invite_lbl = QLabel("dc.gg/vexhack.py")
        invite_lbl.setStyleSheet(f"font-size: 11px; color: {TEXT_DIM}; background: transparent; border: none;")
        invite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(invite_lbl)

        lo.addWidget(card, 1, Qt.AlignmentFlag.AlignCenter)
        return w

    def _open_discord_tab(self):
        import webbrowser
        webbrowser.open("https://dc.gg/vexhack.py")

    def _show_about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("O programie")
        dlg.setFixedSize(380, 320)
        dlg.setStyleSheet(f"background: {BG_DARK}; color: {TEXT}; font-family: {FONT_MAIN};")

        # glow border
        dlg.setObjectName("aboutDlg")
        dlg.setStyleSheet(f"""
            #aboutDlg {{ background: {BG_DARK}; border: 2px solid {PINK}; border-radius: 16px; }}
            QLabel {{ background: transparent; border: none; }}
        """)

        lo = QVBoxLayout(dlg); lo.setContentsMargins(24, 24, 24, 24)
        lo.setSpacing(6)

        icon = QLabel()
        pix = QPixmap(os.path.join(os.path.dirname(__file__) or ".", "logo.png"))
        if not pix.isNull():
            icon.setPixmap(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(icon)

        lo.addSpacing(4)
        t = _lbl(f"VEXARCHIVE v{VERSION}", PINK, 22)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(t)

        s = _lbl("Narzedzie do archiwizacji z kompresja BPE+RLE", TEXT_DIM, 11)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setWordWrap(True)
        lo.addWidget(s)

        lo.addSpacing(12)

        def info_row(label, value, val_color=TEXT):
            r = QHBoxLayout()
            r.addStretch()
            r.addWidget(_lbl(label, TEXT_DIM, 12))
            r.addWidget(_lbl(value, val_color, 12))
            r.addStretch()
            lo.addLayout(r)

        info_row("Autor: ", "v0idvex", PINK)
        info_row("Licencja: ", "MIT", GREEN)
        info_row("Wersja: ", VERSION, PINK)

        lo.addSpacing(8)
        repo_lbl = _lbl("github.com/v0idvex/vexhack.vh", TEXT_DIM, 10)
        repo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        repo_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_DIM}; background: transparent; border: none; padding: 4px;")
        lo.addWidget(repo_lbl)

        stack_lbl = _lbl("Zbudowano z PyQt6, cryptography, Pillow", TEXT_DIM, 10)
        stack_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(stack_lbl)

        lo.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PINK}, stop:1 {GREEN});
                color: #0d0f1a; font-weight: bold; font-size: 14px;
                border: none; border-radius: 8px; padding: 10px 48px;
            }}
            QPushButton:hover {{ opacity: 0.85; }}
        """)
        ok_btn.clicked.connect(dlg.accept)
        lo.addWidget(ok_btn, 0, Qt.AlignmentFlag.AlignCenter)
        dlg.exec()

    # ── TAB: STATS ──
    def _build_stats_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)
        lo.addWidget(_lbl("Statystyki kompresji", PINK, 14))
        lo.addWidget(_lbl("Analiza efektywnosci kompresji dla plikow .cmp i .vh.", TEXT_DIM, 11))

        top = QHBoxLayout()
        self._stats_scan_btn = QPushButton("Skanuj i analizuj")
        self._stats_scan_btn.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT}; border: {BORDER}; border-radius: 4px; padding: 6px 16px; }} QPushButton:hover {{ background: rgba(255,255,255,0.1); }}")
        self._stats_scan_btn.clicked.connect(self._stats_scan)
        top.addWidget(self._stats_scan_btn)
        self._stats_clear_btn = QPushButton("Wyczysc")
        self._stats_clear_btn.setStyleSheet(self._stats_scan_btn.styleSheet())
        self._stats_clear_btn.clicked.connect(lambda: (self._stats_table.setRowCount(0), self._stats_chart.update()))
        top.addWidget(self._stats_clear_btn)
        top.addStretch()
        self._stats_info = _lbl("", TEXT_DIM, 11)
        top.addWidget(self._stats_info)
        lo.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)

        # left: table
        self._stats_table = QTableWidget()
        self._stats_table.setColumnCount(5)
        self._stats_table.setHorizontalHeaderLabels(["Plik", "Original", "Skompresowany", "Ratio", "Format"])
        self._stats_table.setStyleSheet(f"""
            QTableWidget {{ background: rgba(0,0,0,0.3); border: {BORDER}; border-radius: 4px; color: {TEXT}; font-size: 11px; gridline-color: rgba(255,255,255,0.05); }}
            QHeaderView::section {{ background: rgba(255,255,255,0.05); color: {TEXT_DIM}; border: none; padding: 4px; font-weight: bold; }}
        """)
        self._stats_table.setSortingEnabled(True)
        self._stats_table.horizontalHeader().setStretchLastSection(True)
        split.addWidget(self._stats_table)

        # right: chart
        self._stats_chart = StatsChart()
        self._stats_chart.setStyleSheet(f"background: rgba(0,0,0,0.3); border: {BORDER}; border-radius: 4px;")
        split.addWidget(self._stats_chart)
        split.setSizes([400, 300])

        lo.addWidget(split, 1)
        return w

    def _stats_scan(self):
        import string
        drives = []
        for letter in string.ascii_uppercase:
            d = f"{letter}:\\"
            if os.path.exists(d): drives.append(d)
        excluded = [d.strip() for d in self._settings.get("exclude_dirs", "").split(",") if d.strip()]
        self._stats_info.setText("Skanowanie...")
        self._stats_scan_btn.setEnabled(False)
        self._stats_data = []
        self._stats_scanner = ArchiveScanner(drives, excluded)
        self._stats_scanner.found.connect(self._stats_on_found)
        self._stats_scanner.finished.connect(self._stats_on_finished)
        self._stats_scanner.start()

    def _stats_on_found(self, path, size, fmt, mtime):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            from compression import CMPCompressor, VHCompressor
            if fmt == "CMP":
                comp = CMPCompressor.compress(raw, passes=5, num_pairs=24)
            else:
                comp = VHCompressor.compress(raw, passes=5, num_pairs=48)
            ratio = len(comp) / len(raw) if raw else 1
            self._stats_data.append((path, len(raw), len(comp), ratio, fmt))
            row = self._stats_table.rowCount()
            self._stats_table.insertRow(row)
            self._stats_table.setItem(row, 0, QTableWidgetItem(os.path.basename(path)))
            self._stats_table.setItem(row, 1, QTableWidgetItem(_fmt_size(len(raw))))
            self._stats_table.setItem(row, 2, QTableWidgetItem(_fmt_size(len(comp))))
            ritem = QTableWidgetItem(f"{ratio*100:.1f}%")
            if ratio < 0.5: ritem.setForeground(QBrush(QColor(GREEN)))
            elif ratio > 0.9: ritem.setForeground(QBrush(QColor("#ff6b6b")))
            self._stats_table.setItem(row, 3, ritem)
            self._stats_table.setItem(row, 4, QTableWidgetItem(fmt))
        except: pass

    def _stats_on_finished(self, count):
        self._stats_scan_btn.setEnabled(True)
        self._stats_info.setText(f"Przeanalizowano {len(self._stats_data)} plikow")
        self._stats_chart.set_data(self._stats_data)
        self._stats_chart.update()
        self._stats_table.resizeColumnsToContents()
    def _build_log_tab(self):
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(16, 12, 16, 12)
        self._log_widget = QTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setStyleSheet(f"""
            background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; border-radius: 4px;
            font-size: 12px; padding: 12px; font-family: Consolas, 'Courier New', monospace;
        """)
        lo.addWidget(self._log_widget, 1)
        clear_btn = QPushButton("Wyczysc log")
        clear_btn.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.06); color: {TEXT_DIM}; border: {BORDER}; border-radius: 4px; padding: 6px 14px; }} QPushButton:hover {{ color: {TEXT}; }}")
        clear_btn.clicked.connect(lambda: (self._log_widget.clear(), self._log_lines.clear()))
        lo.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignRight)
        return w

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._log_lines.append(line)
        if hasattr(self, '_log_widget'):
            self._log_widget.append(line)

    # ── DRAG & DROP ──
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    def dragMoveEvent(self, event):
        event.acceptProposedAction()
    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                self._add_pack_file(path)

    # ── RESIZE ──
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._particles.resize(self.width(), self.height())

    # ── SETTINGS ──
    def _load_settings(self):
        default = {"auto_update": True, "exclude_dirs": "C:\\Windows,C:\\Program Files,C:\\ProgramData,C:\\$Recycle.Bin", "discord_enabled": True}
        try:
            if os.path.isfile(self._settings_path):
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                    for k in default: s.setdefault(k, default[k])
                    return s
        except: pass
        return dict(default)

    def _save_settings(self):
        try:
            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
        except: pass

    def _show_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Ustawienia")
        dlg.setFixedSize(400, 300)
        dlg.setStyleSheet(f"background: {BG_DARK}; color: {TEXT}; font-family: {FONT_MAIN};")
        lo = QVBoxLayout(dlg); lo.setContentsMargins(20, 20, 20, 20)

        lo.addWidget(_lbl("Ustawienia", PINK, 16))

        # auto update
        au = QCheckBox("Automatycznie sprawdzaj aktualizacje")
        au.setChecked(self._settings.get("auto_update", True))
        au.setStyleSheet(f"color: {TEXT}; font-size: 12px;")
        lo.addWidget(au)

        # discord
        dc = QCheckBox("Pokazuj zaproszenie do Discorda")
        dc.setChecked(self._settings.get("discord_enabled", True))
        dc.setStyleSheet(f"color: {TEXT}; font-size: 12px;")
        lo.addWidget(dc)

        # excluded dirs
        lo.addWidget(_lbl("Pominiete katalogi (przecinki):", TEXT_DIM, 11))
        ed = QLineEdit(self._settings.get("exclude_dirs", ""))
        ed.setStyleSheet(f"background: rgba(0,0,0,0.3); color: {TEXT}; border: {BORDER}; padding: 4px 8px; border-radius: 4px;")
        lo.addWidget(ed)

        lo.addStretch()

        # buttons
        br = QHBoxLayout()
        ok = _make_btn("ZAPISZ", PINK)
        cancel = QPushButton("ANULUJ")
        cancel.setStyleSheet(f"QPushButton {{ background: transparent; color: {TEXT_DIM}; border: {BORDER}; border-radius: 4px; padding: 8px 20px; }} QPushButton:hover {{ color: {TEXT}; }}")
        br.addStretch(); br.addWidget(cancel); br.addWidget(ok)
        lo.addLayout(br)

        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(lambda: self._save_settings_dlg(dlg, au.isChecked(), dc.isChecked(), ed.text()))
        ok.clicked.connect(dlg.accept)

        dlg.exec()

    def _save_settings_dlg(self, dlg, auto_update, discord_enabled, exclude_dirs):
        self._settings["auto_update"] = auto_update
        self._settings["discord_enabled"] = discord_enabled
        self._settings["exclude_dirs"] = exclude_dirs
        self._save_settings()

    def _maybe_show_discord(self):
        if not self._settings.get("discord_enabled", True): return
        if random.random() > 0.008: return
        dlg = QDialog(self)
        dlg.setWindowTitle(" ")
        dlg.setFixedSize(340, 200)
        dlg.setStyleSheet(f"background: {BG_DARK}; border: 2px solid {PINK}; border-radius: 12px;")
        lo = QVBoxLayout(dlg); lo.setContentsMargins(24, 24, 24, 24)
        lo.setSpacing(12)

        lbl = QLabel("DOLACZ DO NAS NA DISCORDZIE!")
        lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {PINK}; background: transparent; border: none;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(lbl)

        sub = QLabel("Spotkajmy sie na serwerze VEXHACK.\nWspolna zabawa, pomoc i nowosci!")
        sub.setStyleSheet(f"font-size: 12px; color: {TEXT}; background: transparent; border: none;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        lo.addWidget(sub)

        icon_lbl = QLabel("[ 💬 ]")
        icon_lbl.setStyleSheet(f"font-size: 40px; background: transparent; border: none;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(icon_lbl)

        br = QHBoxLayout()
        join_btn = QPushButton("DOLACZ")
        join_btn.setStyleSheet(f"""
            QPushButton {{ background: {PINK}; color: #0d0f1a; font-weight: bold; font-size: 13px; border: none; border-radius: 6px; padding: 8px 28px; }}
            QPushButton:hover {{ background: #ff8ab0; }}
        """)
        join_btn.clicked.connect(lambda: self._open_discord(dlg))
        later_btn = QPushButton("Nie teraz")
        later_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {TEXT_DIM}; border: none; padding: 8px; }} QPushButton:hover {{ color: {TEXT}; }}")
        later_btn.clicked.connect(dlg.reject)
        br.addStretch(); br.addWidget(later_btn); br.addWidget(join_btn)
        lo.addLayout(br)

        dlg.exec()

    def _open_discord(self, dlg):
        import webbrowser
        webbrowser.open("https://dc.gg/vexhack.py")
        dlg.accept()

    # ── UPDATE ──
    def _check_updates(self):
        self._update_btn.setEnabled(False)
        self._update_btn.setText("⬇ Sprawdzanie...")
        self._update_worker = UpdateChecker(GITHUB_REPO)
        self._update_worker.finished.connect(self._on_update_check)
        self._update_worker.start()

    def _on_update_check(self, res):
        self._update_btn.setEnabled(True)
        if not res.get("ok"):
            self._update_btn.setText("⬇ Blad")
            QMessageBox.warning(self, "Blad", f"Nie mozna sprawdzic aktualizacji:\n{res.get('error', '?')}")
            QTimer.singleShot(3000, lambda: self._update_btn.setText("⬇ Sprawdz aktualizacje"))
            return
        tag = res.get("tag", "")
        if not tag:
            self._update_btn.setText("⬇ Brak wersji")
            QMessageBox.information(self, "Aktualizacje", "Brak wydan na GitHub.")
            QTimer.singleShot(3000, lambda: self._update_btn.setText("⬇ Sprawdz aktualizacje"))
            return
        current = VERSION.lstrip("v")
        latest = tag.lstrip("v")
        is_newer = self._version_cmp(latest, current) > 0
        if not is_newer:
            self._update_btn.setText("✔ Aktualny")
            QMessageBox.information(self, "Aktualizacje", f"Masz najnowsza wersje ({VERSION}).")
            QTimer.singleShot(3000, lambda: self._update_btn.setText("⬇ Sprawdz aktualizacje"))
            return
        self._update_btn.setText(f"⬇ v{latest} dostepne!")
        reply = QMessageBox.question(self, "Aktualizacja",
            f"Dostepna nowa wersja: {tag}\n\n{res.get('body', '')}\n\nPobrac i zainstalowac?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes and res.get("zip_url"):
            self._download_update(res["zip_url"], tag)

    def _version_cmp(self, a, b):
        pa = [int(x) for x in a.split(".")]
        pb = [int(x) for x in b.split(".")]
        for i in range(max(len(pa), len(pb))):
            va = pa[i] if i < len(pa) else 0
            vb = pb[i] if i < len(pb) else 0
            if va != vb: return va - vb
        return 0

    def _download_update(self, zip_url, tag):
        self._update_downloader = UpdateDownloader(zip_url)
        self._update_downloader.progress.connect(lambda p: self._update_btn.setText(f"⬇ Pobieranie {p}%"))
        self._update_downloader.finished.connect(lambda r: self._apply_update(r, tag))
        self._update_downloader.start()

    def _apply_update(self, res, tag):
        if not res.get("ok"):
            QMessageBox.critical(self, "Blad", f"Nie udalo sie pobrac aktualizacji:\n{res.get('error', '?')}")
            self._update_btn.setText("⬇ Sprawdz aktualizacje")
            return
        try:
            z = zipfile.ZipFile(io.BytesIO(res["data"]))
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
            names = z.namelist()
            # detect common root dir (GitHub zipballs have one, Compress-Archive doesn't)
            roots = set()
            for n in names:
                parts = n.split("/")
                if len(parts) > 1 and parts[0]: roots.add(parts[0])
            skip_root = len(roots) == 1 and all(n.startswith(list(roots)[0] + "/") or n == list(roots)[0] + "/" for n in names)
            for name in names:
                if skip_root:
                    parts = name.split("/")
                    if len(parts) > 1: parts = parts[1:]
                    else: continue
                else:
                    parts = name.split("/")
                target = os.path.join(base, *parts)
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "wb") as f:
                        f.write(z.read(name))
            QMessageBox.information(self, "OK", f"Zaktualizowano do {tag}. Restart...")
            subprocess.Popen([sys.executable] + sys.argv)
            QApplication.quit()
        except Exception as e:
            QMessageBox.critical(self, "Blad", f"Nie udalo sie zainstalowac:\n{e}")
            self._update_btn.setText("⬇ Sprawdz aktualizacje")


def _fmt_size(sz):
    if sz < 1024: return f"{sz} B"
    elif sz < 1024*1024: return f"{sz/1024:.1f} KB"
    else: return f"{sz/1024/1024:.1f} MB"


def run_gui():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("vexhack.vexarchive")
    except: pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    run_gui()
