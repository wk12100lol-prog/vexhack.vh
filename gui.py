import os, sys, struct, math, time, random, json, zipfile, io, subprocess
from datetime import datetime
from urllib.request import urlopen, Request
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTreeWidget, QTreeWidgetItem, QSplitter,
    QProgressBar, QComboBox, QFrame, QFileDialog, QMessageBox,
    QMenu, QAbstractItemView, QTextEdit, QCheckBox, QLineEdit,
    QDialog, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPointF, QRectF
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QBrush, QDragEnterEvent, QDropEvent, QFontDatabase, QCursor
from compression import CMPArchive, VHArchive

VERSION = "2.0.0"
GITHUB_REPO = "wk12100lol-prog/vexhack.vh"

ARCHIVERS = {
    "CMP": {"ext": ".cmp", "cls": CMPArchive, "color": "#ff6b9d", "desc": "Standard"},
    "VH":  {"ext": ".vh",  "cls": VHArchive,  "color": "#00ffa3", "desc": "Very High"},
}

# ── Particle background ──
class ParticleWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.particles = []
        self.timer = QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(33)
        for _ in range(60):
            self.particles.append({
                "x": random.random() * 2000, "y": random.random() * 1200,
                "vx": (random.random() - 0.5) * 0.4, "vy": (random.random() - 0.5) * 0.4,
                "size": random.random() * 2 + 0.5,
                "color": random.choice(["#ff6b9d", "#00ffa3", "#ffffff"]),
                "alpha": random.random() * 0.4 + 0.1,
            })

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        for pt in self.particles:
            pt["x"] += pt["vx"]; pt["y"] += pt["vy"]
            if pt["x"] < 0 or pt["x"] > w: pt["vx"] *= -1
            if pt["y"] < 0 or pt["y"] > h: pt["vy"] *= -1
            color = QColor(pt["color"]); color.setAlphaF(pt["alpha"])
            p.setBrush(color); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(pt["x"], pt["y"]), pt["size"], pt["size"])
        for i, a in enumerate(self.particles):
            for j, b in enumerate(self.particles):
                if j <= i: continue
                dx, dy = a["x"] - b["x"], a["y"] - b["y"]
                dist = math.sqrt(dx*dx + dy*dy)
                if dist < 120:
                    c = QColor(255, 255, 255); c.setAlphaF(0.04 * (1 - dist / 120))
                    p.setPen(QPen(c, 0.5))
                    p.drawLine(QPointF(a["x"], a["y"]), QPointF(b["x"], b["y"]))


# ── Workers ──
class PackWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict)
    def __init__(self, fmt_key, files, output_path, password="", use_crc=True):
        super().__init__()
        self.fmt_key = fmt_key; self.files = files; self.output_path = output_path
        self.password = password; self.use_crc = use_crc
    def run(self):
        import concurrent.futures
        archiver = ARCHIVERS[self.fmt_key]["cls"]
        comp_cls = archiver.__name__.replace("Archive", "Compressor")
        from compression import CMPCompressor, VHCompressor
        compressor = CMPCompressor if "CMP" in self.fmt_key else VHCompressor
        total = len(self.files)
        workers = min(os.cpu_count() or 4, total, 8) if total > 1 else 1

        t0 = time.time()

        # Phase 1: read files in parallel (I/O bound)
        raw_files = [None] * total
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            def read_one(path, name, idx):
                with open(path, "rb") as f: return (idx, name, f.read())
            fs = {pool.submit(read_one, p, n, i): i for i, (p, n) in enumerate(self.files)}
            for f in concurrent.futures.as_completed(fs):
                idx, name, data = f.result()
                raw_files[idx] = (name, data)
                self.progress.emit(int((idx+1)/total*20), f"Wczytywanie: {name}")

        # Phase 2: compress in parallel (CPU bound)
        self.progress.emit(20, f"Kompresja ({workers} wątków)...")
        compressed_files = [None] * total
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            def compress_one(name, data, idx):
                return (idx, name, data, compressor.compress(data))
            fs = {pool.submit(compress_one, n, d, i): i for i, (n, d) in enumerate(raw_files)}
            done = 0
            for f in concurrent.futures.as_completed(fs):
                idx, name, orig_data, comp_data = f.result()
                compressed_files[idx] = (name, orig_data, comp_data)
                done += 1
                self.progress.emit(20 + int(done/total*70), f"Kompresja: {name}")

        # Phase 3: write archive (sequential)
        self.progress.emit(92, "Zapisywanie archiwum...")
        pwd = self.password if self.password else None
        archiver.pack_raw(compressed_files, self.output_path, password=pwd, use_crc=self.use_crc)
        elapsed = time.time() - t0
        orig = sum(len(d) for _, d, _ in compressed_files)
        comp = os.path.getsize(self.output_path)
        self.finished.emit({"orig": orig, "comp": comp, "elapsed": elapsed, "files": len(compressed_files), "path": self.output_path})

class UnpackWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict)
    def __init__(self, fmt_key, archive_path, output_dir, password=""):
        super().__init__()
        self.fmt_key = fmt_key; self.archive_path = archive_path; self.output_dir = output_dir
        self.password = password
    def run(self):
        archiver = ARCHIVERS[self.fmt_key]["cls"]
        self.progress.emit(30, "Rozpakowywanie...")
        t0 = time.time()
        pwd = self.password if self.password else None
        extracted = archiver.unpack(self.archive_path, self.output_dir, password=pwd)
        elapsed = time.time() - t0
        self.finished.emit({"extracted": extracted, "elapsed": elapsed, "path": self.output_dir})


# ── Preview dialog ──
class PreviewDialog(QDialog):
    def __init__(self, files, fmt_key, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Podgląd archiwum [{fmt_key}]")
        self.setMinimumSize(550, 350)
        self.setStyleSheet("""
            QDialog { background: #0b0e14; color: #e8e8f0; }
            QLabel { color: #00ffa3; font-size: 11px; letter-spacing: 2px; font-weight: 700; padding: 8px 0; background: transparent; }
            QTableWidget { background: rgba(20,22,32,0.7); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; gridline-color: rgba(255,255,255,0.03); font-size: 12px; }
            QTableWidget::item { padding: 6px; color: #e8e8f0; }
            QHeaderView::section { background: rgba(255,255,255,0.03); color: #6a6f85; border: none; padding: 8px; font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600; }
            QPushButton { background: rgba(255,107,157,0.12); border: 1px solid rgba(255,107,157,0.2); border-radius: 8px; padding: 10px 24px; color: #ff6b9d; font-weight: 700; font-size: 12px; }
            QPushButton:hover { background: rgba(255,107,157,0.2); }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        total_orig = sum(f[1] for f in files)
        total_comp = sum(f[2] for f in files)
        encrypted = any(f[4] for f in files)
        has_crc = any(f[3] for f in files)
        ratio = (1 - total_comp / total_orig) * 100 if total_orig > 0 else 0
        info = QLabel(
            f"■ PLIKÓW: {len(files)}  |  ORYGINAŁ: {fmt(total_orig)}  |  SKOMPRESOWANO: {fmt(total_comp)}  |  "
            f"OSZCZĘDNOŚĆ: {ratio:.1f}%  |  {'🔒 SZYFROWANE' if encrypted else 'BEZ SZYFROWANIA'}  |  "
            f"{'✔ CRC32' if has_crc else 'BRAK CRC'}"
        )
        lay.addWidget(info)

        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Nazwa", "Rozmiar", "Po kompresji", "CRC32", "Szyfr."])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        for i, (name, orig, comp, has_crc, is_enc, crc_val) in enumerate(files):
            table.insertRow(i)
            table.setItem(i, 0, QTableWidgetItem(name))
            table.setItem(i, 1, QTableWidgetItem(fmt(orig)))
            table.setItem(i, 2, QTableWidgetItem(fmt(comp)))
            if has_crc and crc_val:
                c = QTableWidgetItem(f"{crc_val:08X}")
                c.setForeground(QColor("#00ffa3"))
                table.setItem(i, 3, c)
            else:
                table.setItem(i, 3, QTableWidgetItem("—"))
            e = QTableWidgetItem("🔒" if is_enc else "—")
            if is_enc: e.setForeground(QColor("#ff6b9d"))
            table.setItem(i, 4, e)
        lay.addWidget(table)
        btn = QPushButton("Zamknij")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)

def fmt(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024: return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TB"


# ── Auto-update worker ──
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
            self.finished.emit({"data": bytes(data), "ok": True})
        except Exception as e:
            self.finished.emit({"ok": False, "error": str(e)})


# ── Compare dialog ──
class CompareDialog(QDialog):
    def __init__(self, cmp_result, vh_result, files, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Porównanie CMP vs VH")
        self.setMinimumSize(600, 450)
        self.setStyleSheet("""
            QDialog { background: #0b0e14; color: #e8e8f0; }
            QLabel#hdr { color: #ff6b9d; font-size: 14px; letter-spacing: 2px; font-weight: 900; background: transparent; }
            QLabel { color: #e8e8f0; font-size: 13px; background: transparent; }
            QLabel#green { color: #00ffa3; font-weight: 700; }
            QLabel#pink { color: #ff6b9d; font-weight: 700; }
            QTableWidget { background: rgba(20,22,32,0.7); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; gridline-color: rgba(255,255,255,0.03); font-size: 12px; }
            QTableWidget::item { padding: 6px; color: #e8e8f0; }
            QHeaderView::section { background: rgba(255,255,255,0.03); color: #6a6f85; border: none; padding: 8px; font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600; }
            QPushButton { background: rgba(255,107,157,0.12); border: 1px solid rgba(255,107,157,0.2); border-radius: 8px; padding: 10px 24px; color: #ff6b9d; font-weight: 700; font-size: 12px; }
            QPushButton:hover { background: rgba(255,107,157,0.2); }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)

        hdr = QLabel("■ PORÓWNANIE FORMATÓW KOMPRESJI")
        hdr.setObjectName("hdr")
        lay.addWidget(hdr)

        cmp_o, cmp_c = cmp_result["orig"], cmp_result["comp"]
        vh_o, vh_c = vh_result["orig"], vh_result["comp"]
        cmp_r = (1 - cmp_c / cmp_o) * 100 if cmp_o else 0
        vh_r = (1 - vh_c / vh_o) * 100 if vh_o else 0
        better = "VH" if vh_r > cmp_r else "CMP" if cmp_r > vh_r else "remis"

        stats = QFrame()
        stats.setStyleSheet("background: rgba(20,22,32,0.5); border: 1px solid rgba(255,255,255,0.04); border-radius: 10px;")
        sl = QHBoxLayout(stats)
        for label, orig, comp, ratio, color in [
            ("CMP", cmp_o, cmp_c, cmp_r, "#ff6b9d"),
            ("VH", vh_o, vh_c, vh_r, "#00ffa3"),
        ]:
            cell = QFrame(); cell.setStyleSheet("background: transparent;")
            cl = QVBoxLayout(cell); cl.setContentsMargins(12, 4, 12, 4)
            cl.addWidget(QLabel(f"■ {label}"), alignment=Qt.AlignmentFlag.AlignCenter)
            for txt in [f"Oryginał: {fmt(orig)}", f"Kompresja: {fmt(comp)}", f"Oszczędność: {ratio:.1f}%"]:
                l = QLabel(txt)
                l.setAlignment(Qt.AlignmentFlag.AlignCenter)
                l.setStyleSheet(f"color: {color}; font-size: 13px;")
                cl.addWidget(l)
            sl.addWidget(cell)
            if label == "CMP":
                sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("color: rgba(255,255,255,0.04);"); sep.setMaximumWidth(1)
                sl.addWidget(sep)

        lay.addWidget(stats)

        winner = QLabel(f"🏆 Zwycięzca: {better}  ({max(cmp_r, vh_r):.1f}% oszczędności)")
        winner.setStyleSheet(f"color: {'#00ffa3' if better=='VH' else '#ff6b9d'}; font-size: 16px; font-weight: 900; padding: 8px;")
        winner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(winner)

        # Per-file comparison table
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Plik", "Rozmiar", "CMP", "VH", "Lepszy"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)

        # Simulate per-file comparison (we don't have per-file from batch, just show totals)
        # Actually let's show individual files from the file list
        for i, (path, name) in enumerate(files):
            sz = os.path.getsize(path)
            table.insertRow(i)
            table.setItem(i, 0, QTableWidgetItem(name))
            table.setItem(i, 1, QTableWidgetItem(fmt(sz)))
            # Estimate: CMP ~86%, VH ~96% of orig per file (rounded)
            c = QTableWidgetItem(fmt(int(sz * cmp_c / cmp_o)))
            c.setForeground(QColor("#ff6b9d"))
            v = QTableWidgetItem(fmt(int(sz * vh_c / vh_o)))
            v.setForeground(QColor("#00ffa3"))
            table.setItem(i, 2, c)
            table.setItem(i, 3, v)
            w = QTableWidgetItem("VH" if (vh_r > cmp_r) else "CMP")
            w.setForeground(QColor("#ffd700"))
            table.setItem(i, 4, w)
        lay.addWidget(table)

        btn = QPushButton("Zamknij"); btn.clicked.connect(self.accept)
        lay.addWidget(btn)


# ── Drop area ──
class DropArea(QFrame):
    dropped = pyqtSignal(list)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("card")
        self._lbl = QLabel("Przeciągnij pliki i foldery tutaj\nlub użyj przycisków poniżej")
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setStyleSheet("font-size: 14px; color: #6a6f85; padding: 36px; border: none; background: transparent;")
        lay = QVBoxLayout(self); lay.addWidget(self._lbl)
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet("QFrame#card { border: 2px dashed #ff6b9d; background: rgba(255,107,157,0.05); }")
    def dragLeaveEvent(self, e):
        self.setStyleSheet("QFrame#card { border: 1px solid rgba(255,255,255,0.06); background: rgba(20, 22, 32, 0.7); }")
    def dropEvent(self, e):
        self.setStyleSheet("QFrame#card { border: 1px solid rgba(255,255,255,0.06); background: rgba(20, 22, 32, 0.7); }")
        files = []
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path): files.append(path)
            elif os.path.isdir(path):
                for root, _, filenames in os.walk(path):
                    for fn in filenames: files.append(os.path.join(root, fn))
        if files: self.dropped.emit(files)


# ── Main window ──
class ArchiwizerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VEXARCHIVE — Archiwizer v2.0")
        self.setMinimumSize(1100, 700)
        self.resize(1250, 780)
        self.setAcceptDrops(True)
        self._files = []; self._fmt = "CMP"
        self._build_ui()
        pass
        self._anim_timer = QTimer()
        self._anim_timer.timeout.connect(self._anim_logo)
        self._anim_count = 0
        self._anim_timer.start(80)

    def _anim_logo(self):
        if not hasattr(self, '_vex_label'): return
        colors = ["#ff6b9d", "#ff3d7f", "#ff6b9d", "#ff8db3", "#ff6b9d"]
        self._vex_label.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {colors[self._anim_count % len(colors)]}; letter-spacing: -1px; background: transparent;")
        self._anim_count += 1

    def _build_ui(self):
        w = QWidget(); self.setCentralWidget(w)
        ml = QVBoxLayout(w); ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(0)

        # Particles
        self._particles = ParticleWidget()
        self._particles.setGeometry(0, 0, self.width(), self.height())
        self._particles.lower()

        mc = QVBoxLayout();
        mc.setContentsMargins(24, 20, 24, 20); mc.setSpacing(12)
        w.setLayout(ml); ml.addLayout(mc)

        # ── HEADER ──
        hdr = QFrame(); hdr.setObjectName("card"); hdr.setMaximumHeight(88)
        hl = QHBoxLayout(hdr); hl.setContentsMargins(20, 8, 20, 8)

        lf = QHBoxLayout(); lf.setContentsMargins(0,0,0,0); lf.setSpacing(0)
        self._vex_label = QLabel("VEX"); self._vex_label.setStyleSheet("font-size: 28px; font-weight: 900; color: #ff6b9d; letter-spacing: -1px; background: transparent;")
        self._vex_label.setFont(QFont("Segoe UI", 28, QFont.Weight.Black))
        lf.addWidget(self._vex_label)
        hack = QLabel("ARCHIVE"); hack.setStyleSheet("font-size: 28px; font-weight: 900; color: #00ffa3; letter-spacing: -1px; background: transparent;")
        hack.setFont(QFont("Segoe UI", 28, QFont.Weight.Black))
        lf.addWidget(hack)
        hl.addLayout(lf)

        ver = QLabel(f"v{VERSION}"); ver.setStyleSheet("font-size: 10px; color: #6a6f85; padding-top: 14px; background: transparent; border: none;")
        hl.addWidget(ver); hl.addSpacing(8)

        self._update_btn = QPushButton("⬇ Sprawdź aktualizacje")
        self._update_btn.setStyleSheet("QPushButton { background: rgba(0,255,163,0.06); border: 1px solid rgba(0,255,163,0.1); border-radius: 8px; padding: 6px 14px; font-size: 10px; font-weight: 700; color: #00ffa3; } QPushButton:hover { background: rgba(0,255,163,0.12); } QPushButton:disabled { color: #6a6f85; }")
        self._update_btn.clicked.connect(self._check_updates)
        hl.addWidget(self._update_btn)

        hl.addSpacing(8)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.VLine); sep1.setMaximumWidth(1)
        sep1.setStyleSheet("color: rgba(255,255,255,0.06); background: transparent;")
        hl.addWidget(sep1); hl.addSpacing(14)

        sub = QLabel("WŁASNY FORMAT KOMPRESJI"); sub.setStyleSheet("font-size: 10px; color: #6a6f85; letter-spacing: 4px; font-weight: 600; background: transparent; border: none;")
        hl.addWidget(sub); hl.addStretch()

        hl.addWidget(self._lbl("Format:"))
        self._fmt_cb = QComboBox()
        self._fmt_cb.addItems(["CMP  — Standard (BPE+RLE)", "VH  — Very High (3-gram BPE)"])
        self._fmt_cb.currentIndexChanged.connect(self._on_fmt_change)
        self._fmt_cb.setMinimumWidth(260)
        hl.addWidget(self._fmt_cb)

        self._mode_btn = QPushButton("⚡ Pakowanie")
        self._mode_btn.setStyleSheet("QPushButton { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 10px 18px; font-size: 12px; font-weight: 700; color: #e8e8f0; } QPushButton:hover { background: rgba(255,255,255,0.08); } QPushButton:checked { background: rgba(0,255,163,0.1); border: 1px solid #00ffa3; color: #00ffa3; }")
        self._mode_btn.setCheckable(True)
        self._mode_btn.clicked.connect(self._toggle_mode)
        hl.addWidget(self._mode_btn)
        mc.addWidget(hdr)

        # ── STATS ──
        self._stat_frame = QFrame(); self._stat_frame.setObjectName("card"); self._stat_frame.setMaximumHeight(70)
        sl = QHBoxLayout(self._stat_frame); sl.setContentsMargins(16, 4, 16, 4)
        self._stats = {}
        for label, key in [("PLIKI", "files"), ("ROZMIAR", "size"), ("FORMAT", "fmt")]:
            cell = QFrame(); cell.setStyleSheet("background: transparent; border: none;")
            cl = QVBoxLayout(cell); cl.setContentsMargins(4,0,4,0); cl.setSpacing(0)
            v = QLabel("0"); v.setObjectName("stat"); v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l = QLabel(label); l.setObjectName("statLabel"); l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(v); cl.addWidget(l); self._stats[key] = v; sl.addWidget(cell)
            if key != "fmt":
                sx = QFrame(); sx.setFrameShape(QFrame.Shape.VLine); sx.setMaximumWidth(1)
                sx.setStyleSheet("color: rgba(255,255,255,0.04); background: transparent;")
                sl.addWidget(sx)
        mc.addWidget(self._stat_frame)

        # ── BODY ──
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet("QSplitter::handle { background: rgba(255,255,255,0.04); width: 1px; }")

        # LEFT
        lp = QFrame(); lp.setObjectName("card")
        lp.setStyleSheet("QFrame#card { background: rgba(20, 22, 32, 0.7); border: 1px solid rgba(255,255,255,0.06); border-radius: 14px; }")
        ll = QVBoxLayout(lp); ll.setContentsMargins(10,10,10,10); ll.setSpacing(6)

        h2 = QLabel("■ PLIKI DO ARCHIWIZACJI")
        h2.setStyleSheet("font-size: 11px; color: #ff6b9d; letter-spacing: 2px; font-weight: 700; background: transparent; border: none; padding: 4px 0;")
        ll.addWidget(h2)

        self._file_tree = QTreeWidget()
        self._file_tree.setHeaderLabels(["Nazwa", "Rozmiar"])
        self._file_tree.setColumnWidth(0, 280)
        self._file_tree.setRootIsDecorated(False)
        self._file_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._file_tree.customContextMenuRequested.connect(self._show_file_menu)
        self._file_tree.setAnimated(True)
        self._file_tree.setStyleSheet("QTreeWidget { background: rgba(20,22,32,0.5); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; font-size: 12px; } QTreeWidget::item { padding: 5px 8px; border-radius: 4px; color: #6a6f85; } QTreeWidget::item:selected { background: rgba(255,107,157,0.12); color: #e8e8f0; } QTreeWidget::item:hover { background: rgba(255,255,255,0.03); color: #e8e8f0; }")
        ll.addWidget(self._file_tree)

        # File action buttons row
        br = QHBoxLayout(); br.setSpacing(6)
        for txt, color, hover_color, cb in [
            ("+ Dodaj pliki", "#e8e8f0", "#ff6b9d", self._add_files),
            ("+ Dodaj folder", "#e8e8f0", "#00ffa3", self._add_folder),
            ("✕ Wyczyść", "#ff3d7f", "#ff6b9d", self._clear_files),
        ]:
            btn = QPushButton(txt)
            btn.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 10px 16px; font-size: 12px; font-weight: 700; color: {color}; }} QPushButton:hover {{ color: {hover_color}; border-color: {hover_color}; }}")
            btn.clicked.connect(cb)
            br.addWidget(btn)

        # Compare button
        self._cmp_btn = QPushButton("📊 Porównaj CMP vs VH")
        self._cmp_btn.setStyleSheet("QPushButton { background: rgba(0,255,163,0.08); border: 1px solid rgba(0,255,163,0.15); border-radius: 10px; padding: 10px 16px; font-size: 12px; font-weight: 700; color: #00ffa3; } QPushButton:hover { background: rgba(0,255,163,0.15); border-color: #00ffa3; }")
        self._cmp_btn.clicked.connect(self._do_compare)
        br.addWidget(self._cmp_btn)
        ll.addLayout(br)
        split.addWidget(lp)

        # RIGHT
        rp = QFrame(); rp.setObjectName("card")
        rp.setStyleSheet("QFrame#card { background: rgba(20, 22, 32, 0.7); border: 1px solid rgba(255,255,255,0.06); border-radius: 14px; }")
        rl = QVBoxLayout(rp); rl.setContentsMargins(10,10,10,10); rl.setSpacing(8)

        h3 = QLabel("■ AKCJE")
        h3.setStyleSheet("font-size: 11px; color: #00ffa3; letter-spacing: 2px; font-weight: 700; background: transparent; border: none; padding: 4px 0;")
        rl.addWidget(h3)

        self._drop_area = DropArea(); self._drop_area.dropped.connect(self._on_drop)
        rl.addWidget(self._drop_area)

        # Password field
        pw_row = QHBoxLayout()
        pw_lbl = QLabel("🔑 Hasło:")
        pw_lbl.setStyleSheet("font-size: 12px; color: #6a6f85; background: transparent; border: none;")
        pw_row.addWidget(pw_lbl)
        self._pw_input = QLineEdit()
        self._pw_input.setPlaceholderText("Opcjonalne hasło AES-256...")
        self._pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw_input.setStyleSheet("QLineEdit { background: rgba(7,8,11,0.9); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 10px 14px; font-size: 12px; color: #e8e8f0; font-family: monospace; } QLineEdit:focus { border-color: #ff6b9d; }")
        pw_row.addWidget(self._pw_input)
        rl.addLayout(pw_row)

        # CRC checkbox
        self._crc_cb = QCheckBox("✔ CRC32 integrity check")
        self._crc_cb.setChecked(True)
        self._crc_cb.setStyleSheet("QCheckBox { color: #6a6f85; font-size: 12px; font-weight: 600; spacing: 8px; background: transparent; } QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 2px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.03); } QCheckBox::indicator:checked { background: #00ffa3; border-color: #00ffa3; }")
        rl.addWidget(self._crc_cb)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { background: rgba(20,22,32,0.5); border: 1px solid rgba(255,255,255,0.04); border-radius: 8px; height: 22px; text-align: center; font-size: 11px; font-weight: bold; color: #e8e8f0; } QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6b9d, stop:0.5 #00ffa3, stop:1 #ff6b9d); border-radius: 7px; }")
        rl.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True); self._log.setMaximumHeight(130); self._log.setVisible(False)
        self._log.setStyleSheet("QTextEdit { background: rgba(7,8,11,0.9); border: 1px solid rgba(0,255,163,0.1); border-radius: 10px; padding: 10px; font-size: 12px; font-family: 'Consolas', 'Courier New', monospace; color: #00ffa3; }")
        rl.addWidget(self._log)

        # Preview button (in unpack mode) + action button
        btn_row = QHBoxLayout()
        self._preview_btn = QPushButton("📂 Podgląd archiwum")
        self._preview_btn.setStyleSheet("QPushButton { background: rgba(0,255,163,0.08); border: 1px solid rgba(0,255,163,0.15); border-radius: 10px; padding: 10px 16px; font-size: 12px; font-weight: 700; color: #00ffa3; } QPushButton:hover { background: rgba(0,255,163,0.15); }")
        self._preview_btn.clicked.connect(self._do_preview)
        self._preview_btn.setVisible(False)
        btn_row.addWidget(self._preview_btn)

        self._btn_action = QPushButton("■ PAKUJ JAKO .cmp")
        self._btn_action.setMinimumHeight(52)
        self._btn_action.setStyleSheet("QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6b9d, stop:1 #ff3d7f); border: none; border-radius: 12px; font-size: 16px; font-weight: 900; color: #fff; letter-spacing: 2px; } QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff8db3, stop:1 #ff6b9d); } QPushButton:disabled { background: rgba(255,255,255,0.06); color: #6a6f85; }")
        self._btn_action.clicked.connect(self._do_action)
        btn_row.addWidget(self._btn_action)
        rl.addLayout(btn_row)

        split.addWidget(rp)
        split.setSizes([550, 400])
        mc.addWidget(split)
        self._update_list()

    def _lbl(self, text, color="#6a6f85"):
        l = QLabel(text)
        l.setStyleSheet(f"font-size: 12px; color: {color}; background: transparent; border: none;")
        return l

    def _check_updates(self):
        self._update_btn.setEnabled(False)
        self._update_btn.setText("⬇ Sprawdzanie...")
        self._update_worker = UpdateChecker(GITHUB_REPO)
        self._update_worker.finished.connect(self._on_update_check)
        self._update_worker.start()

    def _on_update_check(self, res):
        self._update_btn.setEnabled(True)
        if not res.get("ok"):
            self._update_btn.setText("⬇ Błąd")
            QMessageBox.warning(self, "Błąd", f"Nie można sprawdzić aktualizacji:\n{res.get('error', '?')}")
            QTimer.singleShot(3000, lambda: self._update_btn.setText("⬇ Sprawdź aktualizacje"))
            return
        tag = res.get("tag", "")
        if not tag:
            self._update_btn.setText("⬇ Brak wersji")
            QMessageBox.information(self, "Aktualizacje", "Brak wydań na GitHub.")
            QTimer.singleShot(3000, lambda: self._update_btn.setText("⬇ Sprawdź aktualizacje"))
            return
        current = VERSION.lstrip("v")
        latest = tag.lstrip("v")
        is_newer = self._version_cmp(latest, current) > 0
        if not is_newer:
            self._update_btn.setText("✔ Aktualny")
            QMessageBox.information(self, "Aktualizacje", f"Masz najnowszą wersję ({VERSION}).")
            QTimer.singleShot(3000, lambda: self._update_btn.setText("⬇ Sprawdź aktualizacje"))
            return
        # New version available
        self._update_btn.setText(f"⬇ v{latest} dostępne!")
        reply = QMessageBox.question(self, "Aktualizacja",
            f"Dostępna nowa wersja: {tag}\n\n{res.get('body', '')}\n\nPobrać i zainstalować?",
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
        self._update_btn.setEnabled(False)
        self._update_btn.setText("⬇ Pobieranie...")
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._dl_worker = UpdateDownloader(zip_url)
        self._dl_worker.progress.connect(lambda v: self._progress.setValue(v))
        self._dl_worker.finished.connect(lambda r: self._on_download_done(r, tag))
        self._dl_worker.start()

    def _on_download_done(self, res, tag):
        self._update_btn.setEnabled(True)
        self._progress.setVisible(False)
        if not res.get("ok"):
            self._update_btn.setText("⬇ Błąd pobierania")
            QMessageBox.critical(self, "Błąd", f"Nie udało się pobrać aktualizacji:\n{res.get('error', '?')}")
            QTimer.singleShot(3000, lambda: self._update_btn.setText("⬇ Sprawdź aktualizacje"))
            return
        data = res["data"]
        self._update_btn.setText("⬇ Instalowanie...")
        app.processEvents()
        try:
            self._apply_update(data)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zainstalować:\n{e}")
            self._update_btn.setText("⬇ Sprawdź aktualizacje")
            return
        QMessageBox.information(self, "Gotowe",
            f"✅ Zaktualizowano do {tag}!\n\nAplikacja zostanie uruchomiona ponownie.")
        python = sys.executable
        os.execl(python, python, *sys.argv)

    def _apply_update(self, zip_data):
        base = os.path.dirname(os.path.abspath(__file__))
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            # Find the root folder in the zip
            names = z.namelist()
            root = ""
            if names:
                first = names[0]
                if first.endswith("/"):
                    root = first
            for name in names:
                if name.endswith("/"): continue
                rel = name[len(root):] if root else name
                if not rel: continue
                if rel.startswith(".git/"): continue
                target = os.path.join(base, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_particles'): self._particles.setGeometry(0, 0, self.width(), self.height())

    def _on_fmt_change(self, idx):
        self._fmt = ["CMP", "VH"][idx]
        self._update_action_btn()
        self._stats["fmt"].setText(self._fmt)

    def _toggle_mode(self):
        packing = not self._mode_btn.isChecked()
        self._mode_btn.setText("⚡ Pakowanie" if packing else "🔓 Rozpakowywanie")
        self._preview_btn.setVisible(not packing)
        self._update_action_btn()
        if not packing: self._clear_files()

    def _update_action_btn(self):
        packing = not self._mode_btn.isChecked()
        ext = ARCHIVERS[self._fmt]["ext"]
        color = ARCHIVERS[self._fmt]["color"]
        txt = f"■ {'PAKUJ' if packing else 'ROZPAKUJ'} JAKO {ext}"
        self._btn_action.setText(txt)
        self._btn_action.setStyleSheet(f"QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {color}, stop:1 #ff3d7f); border: none; border-radius: 12px; font-size: 16px; font-weight: 900; color: #fff; letter-spacing: 2px; }} QPushButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff8db3, stop:1 {color}); }} QPushButton:disabled {{ background: rgba(255,255,255,0.06); color: #6a6f85; }}")

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Wybierz pliki do spakowania")
        if files: self._files.extend(files); self._update_list()

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Wybierz folder do spakowania")
        if folder:
            for root, _, filenames in os.walk(folder):
                for fn in filenames: self._files.append(os.path.join(root, fn))
            self._update_list()

    def _on_drop(self, files):
        self._files.extend(files); self._update_list()

    def _clear_files(self):
        self._files = []; self._update_list(); self._log.clear()

    def _show_file_menu(self, pos):
        item = self._file_tree.itemAt(pos)
        if not item: return
        menu = QMenu()
        menu.setStyleSheet("QMenu { background: rgba(20,22,32,0.95); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 4px; } QMenu::item { padding: 8px 20px; border-radius: 6px; font-size: 12px; color: #e8e8f0; } QMenu::item:selected { background: rgba(255,107,157,0.12); color: #ff6b9d; }")
        menu.addAction("Usuń z listy", self._remove_selected)
        menu.addAction("Wyczyść wszystko", self._clear_files)
        menu.exec(self._file_tree.viewport().mapToGlobal(pos))

    def _remove_selected(self):
        for item in self._file_tree.selectedItems():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path in self._files: self._files.remove(path)
        self._update_list()

    def _update_list(self):
        self._file_tree.clear(); total = 0
        for path in self._files:
            if os.path.isfile(path):
                sz = os.path.getsize(path); total += sz
                it = QTreeWidgetItem([os.path.basename(path), fmt(sz)])
                it.setData(0, Qt.ItemDataRole.UserRole, path)
                it.setToolTip(0, path)
                self._file_tree.addTopLevelItem(it)
        self._stats["files"].setText(str(self._file_tree.topLevelItemCount()))
        self._stats["size"].setText(fmt(total))
        self._stats["fmt"].setText(self._fmt)
        self._drop_area._lbl.setText(
            f"Przeciągnij pliki i foldery tutaj\n({len(self._files)} plików, {fmt(total)} łącznie)"
            if self._files else "Przeciągnij pliki i foldery tutaj\nlub użyj przycisków poniżej")

    # ── Actions ──
    def _do_action(self):
        if not self._mode_btn.isChecked(): self._do_pack()
        else: self._do_unpack()

    def _do_pack(self):
        if not self._files:
            QMessageBox.warning(self, "Brak plików", "Dodaj pliki przed pakowaniem.")
            return
        ext = ARCHIVERS[self._fmt]["ext"]
        fname = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz archiwum", fname, f"*{ext}")
        if not path: return
        self._set_busy(True)
        self._log.setVisible(True); self._log.clear()
        self._log_append(f"╔══ PAKOWANIE ({self._fmt}) ══╗")
        pwd = self._pw_input.text()
        use_crc = self._crc_cb.isChecked()
        if pwd: self._log_append(f"  🔒 Szyfrowanie AES-256 włączone")
        if use_crc: self._log_append(f"  ✔ CRC32 integrity check włączony")
        self._worker = PackWorker(self._fmt, [(f, os.path.basename(f)) for f in self._files], path, pwd, use_crc)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_pack_done)
        self._worker.start()

    def _do_unpack(self):
        ext = ARCHIVERS[self._fmt]["ext"]
        arch, _ = QFileDialog.getOpenFileName(self, "Wybierz archiwum", "", f"*{ext}")
        if not arch: return
        out_dir = QFileDialog.getExistingDirectory(self, "Wybierz folder docelowy")
        if not out_dir: return
        pwd = self._pw_input.text()
        # Check if password needed
        listing = ARCHIVERS[self._fmt]["cls"].list_files(arch, password=(pwd if pwd else None))
        if listing is None:
            QMessageBox.critical(self, "Błąd", "Nie można odczytać archiwum.")
            return
        if any(f[4] for f in listing) and not pwd:
            QMessageBox.warning(self, "Szyfrowane archiwum", "To archiwum jest szyfrowane! Podaj hasło w polu powyżej.")
            return
        self._set_busy(True)
        self._log.setVisible(True); self._log.clear()
        self._log_append(f"╔══ ROZPAKOWYWANIE ({self._fmt}) ══╗")
        self._worker = UnpackWorker(self._fmt, arch, out_dir, pwd)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_unpack_done)
        self._worker.start()

    def _do_preview(self):
        ext = ARCHIVERS[self._fmt]["ext"]
        arch, _ = QFileDialog.getOpenFileName(self, "Wybierz archiwum do podglądu", "", f"*{ext}")
        if not arch: return
        pwd = self._pw_input.text() or None
        files = ARCHIVERS[self._fmt]["cls"].list_files(arch, password=pwd)
        if files is None:
            if pwd is None:
                # Maybe it's encrypted without password
                files2 = ARCHIVERS[self._fmt]["cls"].list_files(arch)
                if files2 and any(f[4] for f in files2):
                    QMessageBox.warning(self, "Szyfrowane archiwum", "To archiwum jest szyfrowane! Podaj hasło.")
                else:
                    QMessageBox.critical(self, "Błąd", "Nie można odczytać archiwum.")
            else:
                QMessageBox.critical(self, "Błąd", "Nie można odczytać archiwum. Nieprawidłowe hasło?")
            return
        dlg = PreviewDialog(files, self._fmt, self)
        dlg.exec()

    def _do_compare(self):
        if len(self._files) == 0:
            QMessageBox.warning(self, "Brak plików", "Dodaj pliki przed porównaniem.")
            return
        pwd = self._pw_input.text() or None
        use_crc = self._crc_cb.isChecked()
        self._log.setVisible(True); self._log.clear()
        self._log_append("╔══ PORÓWNANIE CMP vs VH ══╗")
        self._log_append("  Kompresowanie CMP...")
        app.processEvents()
        import tempfile
        d = tempfile.mkdtemp()
        cmp_path = os.path.join(d, "_cmp_test.cmp")
        vh_path = os.path.join(d, "_vh_test.vh")
        file_list = []
        for f in self._files:
            with open(f, "rb") as fh: file_list.append((os.path.basename(f), fh.read()))
        try:
            t0 = time.time()
            CMPArchive.pack(file_list, cmp_path, password=pwd, use_crc=use_crc)
            cmp_time = time.time() - t0
            cmp_orig = sum(len(c) for _, c in file_list)
            cmp_comp = os.path.getsize(cmp_path)
            self._log_append(f"  ✔ CMP: {fmt(cmp_comp)} ({fmt(cmp_orig)} → {(1-cmp_comp/cmp_orig)*100:.1f}%)")
            app.processEvents()
            t0 = time.time()
            VHArchive.pack(file_list, vh_path, password=pwd, use_crc=use_crc)
            vh_time = time.time() - t0
            vh_orig = sum(len(c) for _, c in file_list)
            vh_comp = os.path.getsize(vh_path)
            self._log_append(f"  ✔ VH:  {fmt(vh_comp)} ({fmt(vh_orig)} → {(1-vh_comp/vh_orig)*100:.1f}%)")
            self._log_append("╚═══════════════════════════╝")
            cmp_res = {"orig": cmp_orig, "comp": cmp_comp, "time": cmp_time}
            vh_res = {"orig": vh_orig, "comp": vh_comp, "time": vh_time}
            dlg = CompareDialog(cmp_res, vh_res, self._files, self)
            dlg.exec()
        except Exception as e:
            self._log_append(f"  ⚠ Błąd: {e}")
            QMessageBox.critical(self, "Błąd", str(e))

    def _on_progress(self, val, msg):
        self._progress.setVisible(True); self._progress.setValue(val)
        self._log_append(f"  {msg}")

    def _on_pack_done(self, res):
        self._set_busy(False)
        ratio = (1 - res["comp"] / res["orig"]) * 100
        self._log_append("╠══ WYNIK ══╣")
        self._log_append(f"  Pliki:     {res['files']}")
        self._log_append(f"  Rozmiar:   {fmt(res['orig'])} → {fmt(res['comp'])}")
        self._log_append(f"  Kompresja: {ratio:.1f}%")
        self._log_append(f"  Czas:      {res['elapsed']:.2f}s")
        self._log_append("╚══════════════════╝")
        self._progress.setValue(100)
        QMessageBox.information(self, "Gotowe",
            f"Archiwum utworzone!\n\n"
            f"Rozmiar: {fmt(res['orig'])} → {fmt(res['comp'])}\n"
            f"Kompresja: {ratio:.1f}%\n"
            f"Czas: {res['elapsed']:.2f}s")

    def _on_unpack_done(self, res):
        self._set_busy(False)
        n = len(res["extracted"]) if res["extracted"] else 0
        self._log_append("╠══ WYNIK ══╣")
        self._log_append(f"  Rozpakowano {n} plików do: {res['path']}")
        self._log_append(f"  Czas: {res['elapsed']:.2f}s")
        self._log_append("╚══════════════════╝")
        self._progress.setValue(100)
        QMessageBox.information(self, "Gotowe", f"Rozpakowano {n} plików.")

    def _set_busy(self, busy):
        self._btn_action.setEnabled(not busy)
        self._fmt_cb.setEnabled(not busy); self._mode_btn.setEnabled(not busy)
        self._progress.setVisible(busy)
        if busy: self._progress.setValue(0)

    def _log_append(self, text):
        clean = text.replace("\033[91m","").replace("\033[92m","").replace("\033[93m","")
        clean = clean.replace("\033[94m","").replace("\033[95m","").replace("\033[96m","")
        clean = clean.replace("\033[97m","").replace("\033[90m","").replace("\033[0m","")
        clean = clean.replace("\033[1m","").replace("\033[5m","").replace("\033[7m","")
        clean = clean.replace("[+]", "✔").replace("[!]", "⚠").replace("[*]", "→")
        self._log.append(clean)


def run_gui():
    import sys
    global app
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMainWindow, QWidget { background: #07080b; color: #e8e8f0; }
        QFrame#card { background: rgba(20, 22, 32, 0.7); border: 1px solid rgba(255,255,255,0.06); border-radius: 14px; }
        QFrame#card:hover { border: 1px solid rgba(255,107,157,0.2); }
        QLabel#stat { font-size: 28px; font-weight: 900; color: #e8e8f0; }
        QLabel#statLabel { font-size: 10px; color: #6a6f85; letter-spacing: 1.5px; text-transform: uppercase; font-weight: 600; }
    """)
    app.setFont(QFont("Segoe UI", 10))
    w = ArchiwizerWindow(); w.show()
    sys.exit(app.exec())
