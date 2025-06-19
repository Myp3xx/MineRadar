import sys
import ipaddress
import queue
import threading
import socket
import base64
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QProgressBar,
    QListWidget, QListWidgetItem, QSpinBox, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QSize, QTimer, QByteArray
from PyQt6.QtGui import QColor, QIcon, QPixmap
from mcstatus import JavaServer

def mc_colors_to_html(text):
    color_map = {
        '0': '#000000', '1': '#0000AA', '2': '#00AA00', '3': '#00AAAA',
        '4': '#AA0000', '5': '#AA00AA', '6': '#FFAA00', '7': '#AAAAAA',
        '8': '#555555', '9': '#5555FF', 'a': '#55FF55', 'b': '#55FFFF',
        'c': '#FF5555', 'd': '#FF55FF', 'e': '#FFFF55', 'f': '#FFFFFF',
        'r': None
    }
    result = ""
    current_color = None
    i = 0
    while i < len(text):
        if text[i] == "§" and i + 1 < len(text):
            code = text[i + 1].lower()
            if code == 'r':
                result += "</span>" if current_color else ""
                current_color = None
            elif code in color_map:
                if current_color:
                    result += "</span>"
                current_color = color_map[code]
                if current_color:
                    result += f"<span style='color:{current_color}'>"
            i += 2
        else:
            c = text[i]
            if c == "&":
                result += "&amp;"
            elif c == "<":
                result += "&lt;"
            elif c == ">":
                result += "&gt;"
            else:
                result += c
            i += 1
    if current_color:
        result += "</span>"
    return result


class Worker(QObject):
    server_found = pyqtSignal(str, dict)
    progress_update = pyqtSignal(int, int, int, int)
    finished = pyqtSignal()

    def __init__(self, ip_base, port_start, port_end, max_threads, deviation):
        super().__init__()
        self.ip_base = ip_base
        self.port_start = port_start
        self.port_end = port_end
        self.max_threads = max_threads
        self.deviation = deviation
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        try:
            ip_obj = ipaddress.IPv4Address(self.ip_base)
        except Exception:
            self.finished.emit()
            return

        base_octets = list(map(int, str(ip_obj).split('.')))
        total_checks = (self.deviation * 2 + 1) * (self.port_end - self.port_start + 1)
        checked_count = 0
        found_count = 0

        q = queue.Queue()
        lock = threading.Lock()

        def worker_thread():
            nonlocal checked_count, found_count
            while self._is_running:
                try:
                    ip_check, port_check = q.get(timeout=0.5)
                except queue.Empty:
                    return
                ip_str = ".".join(map(str, ip_check))
                ip_port_str = f"{ip_str}:{port_check}"
                try:
                    server = JavaServer.lookup(ip_port_str)
                    status = server.status()

                    motd1, motd2 = "", ""
                    desc = status.description

                    if isinstance(desc, str):
                        lines = desc.split('\n')
                        motd1 = lines[0] if len(lines) > 0 else ""
                        motd2 = lines[1] if len(lines) > 1 else ""
                    elif isinstance(desc, dict):
                        txt = ""
                        if "text" in desc:
                            txt = desc["text"]
                        elif "extra" in desc and isinstance(desc["extra"], list):
                            txt = "".join([e.get("text", "") for e in desc["extra"]])
                        lines = txt.split('\n')
                        motd1 = lines[0] if len(lines) > 0 else ""
                        motd2 = lines[1] if len(lines) > 1 else ""

                    data = {
                        "version": status.version.name,
                        "players_online": status.players.online,
                        "players_max": status.players.max,
                        "latency": int(status.latency),
                        "motd1": motd1,
                        "motd2": motd2,
                    }
                    with lock:
                        found_count += 1
                    self.server_found.emit(ip_port_str, data)
                except Exception:
                    pass
                with lock:
                    checked_count += 1
                    self.progress_update.emit(checked_count, total_checks, found_count, 0)
                q.task_done()

        for deviation_offset in range(-self.deviation, self.deviation + 1):
            if not self._is_running:
                break
            new_octet4 = base_octets[3] + deviation_offset
            if 0 <= new_octet4 <= 255:
                for port in range(self.port_start, self.port_end + 1):
                    q.put(([base_octets[0], base_octets[1], base_octets[2], new_octet4], port))

        threads = []
        for _ in range(self.max_threads):
            t = threading.Thread(target=worker_thread, daemon=True)
            t.start()
            threads.append(t)

        while self._is_running and any(t.is_alive() for t in threads):
            for t in threads:
                t.join(0.1)

        self.finished.emit()


class ServerFinder(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MineRadar")

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        ip_layout = QHBoxLayout()
        ip_label = QLabel("Server IP or URL:")
        self.ip_edit = QLineEdit()
        ip_layout.addWidget(ip_label)
        ip_layout.addWidget(self.ip_edit)
        self.layout.addLayout(ip_layout)

        port_layout = QHBoxLayout()
        port_label = QLabel("Port range:")
        self.port_start_spin = QSpinBox()
        self.port_start_spin.setRange(1, 65535)
        self.port_start_spin.setValue(25500)
        self.port_end_spin = QSpinBox()
        self.port_end_spin.setRange(1, 65535)
        self.port_end_spin.setValue(25600)
        port_layout.addWidget(port_label)
        port_layout.addWidget(self.port_start_spin)
        port_layout.addWidget(QLabel("-"))
        port_layout.addWidget(self.port_end_spin)
        self.layout.addLayout(port_layout)

        dev_layout = QHBoxLayout()
        dev_label = QLabel("IP deviation (±):")
        self.dev_spin = QSpinBox()
        self.dev_spin.setRange(0, 50)
        self.dev_spin.setValue(10)
        dev_layout.addWidget(dev_label)
        dev_layout.addWidget(self.dev_spin)
        self.layout.addLayout(dev_layout)

        threads_layout = QHBoxLayout()
        threads_label = QLabel("Max threads:")
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 256)
        self.threads_spin.setValue(128)
        threads_layout.addWidget(threads_label)
        threads_layout.addWidget(self.threads_spin)
        self.layout.addLayout(threads_layout)

        control_layout = QHBoxLayout()
        self.search_button = QPushButton("🔍 Search")
        self.search_button.clicked.connect(self.toggle_search)
        control_layout.addWidget(self.search_button)

        self.progress_bar = QProgressBar()
        control_layout.addWidget(self.progress_bar)
        self.layout.addLayout(control_layout)

        self.result_list = QListWidget()
        self.result_list.itemClicked.connect(self.on_item_clicked)
        self.layout.addWidget(self.result_list)

        self.status_layout = QHBoxLayout()
        self.found_label = QLabel("Found: 0")
        self.checked_label = QLabel("Checked: 0")
        self.status_layout.addWidget(self.found_label)
        self.status_layout.addWidget(self.checked_label)
        self.layout.addLayout(self.status_layout)

        self.help_button = QPushButton("❔ Help")
        self.help_button.clicked.connect(self.show_help)
        control_layout.addWidget(self.help_button)

        self.about_button = QPushButton("ℹ️ About")
        self.about_button.clicked.connect(self.show_about)
        control_layout.addWidget(self.about_button)

        self.worker = None
        self.worker_thread = None
        self._is_searching = False

    def toggle_search(self):
        if not self._is_searching:
            self.start_search()
        else:
            self.stop_search()

    def start_search(self):
        ip_or_url = self.ip_edit.text().strip()
        if not ip_or_url:
            self.result_list.addItem("ERROR: IP address or URL is empty.")
            return

        try:
            if "/" in ip_or_url:
                ip_or_url = ip_or_url.split("/")[0]
            if ":" in ip_or_url:
                ip_or_url = ip_or_url.split(":")[0]
            ip_base = str(ipaddress.IPv4Address(ip_or_url))
        except Exception:
            try:
                ip_base = socket.gethostbyname(ip_or_url)
            except Exception:
                self.result_list.addItem(f"ERROR: Invalid IP or URL: {ip_or_url}")
                return

        port_start = self.port_start_spin.value()
        port_end = self.port_end_spin.value()
        if port_end < port_start:
            self.result_list.addItem("ERROR: End port must be >= start port.")
            return

        max_threads = self.threads_spin.value()
        deviation = self.dev_spin.value()

        self.result_list.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum((deviation * 2 + 1) * (port_end - port_start + 1))

        self.found_label.setText("Found: 0")
        self.checked_label.setText("Checked: 0")

        self.worker = Worker(ip_base, port_start, port_end, max_threads, deviation)
        self.worker.server_found.connect(self.on_server_found)
        self.worker.progress_update.connect(self.on_progress_update)
        self.worker.finished.connect(self.on_search_finished)

        self.worker_thread = threading.Thread(target=self.worker.run, daemon=True)
        self._is_searching = True
        self.search_button.setText("🔍 Cancel")
        self.worker_thread.start()

    def stop_search(self):
        if self.worker:
            self.worker.stop()
        self._is_searching = False
        self.search_button.setText("🔍 Search")

    def on_server_found(self, ip_port, data):
        motd_html = mc_colors_to_html(data['motd1']) + "<br>" + mc_colors_to_html(data['motd2'])
        left_text = f"<b>{ip_port}</b><br>{motd_html}"
        right_text = (
            f"Version: <b><span style='color:#fe640b'>{data['version']}</span></b><br>"
            f"Online: <b><span style='color:#04a5e5'>{data['players_online']}/{data['players_max']}</span></b><br>"
            f"Ping: <b><span style='color:#40a02b'>{data['latency']} ms</span></b>"
        )

        item = QListWidgetItem()
        item.setSizeHint(QSize(500, 70))
        item.setData(Qt.ItemDataRole.UserRole, ip_port)
        self.result_list.addItem(item)

        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)

        left_label = QLabel()
        left_label.setObjectName("ip_label")
        left_label.setTextFormat(Qt.TextFormat.RichText)
        left_label.setText(left_text)
        left_label.setWordWrap(True)
        left_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(left_label, stretch=3)

        right_label = QLabel()
        right_label.setTextFormat(Qt.TextFormat.RichText)
        right_label.setText(right_text)
        right_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        layout.addWidget(right_label, stretch=1)

        widget.setLayout(layout)
        self.result_list.setItemWidget(item, widget)

    def on_item_clicked(self, item):
        ip_port = item.data(Qt.ItemDataRole.UserRole)
        QApplication.clipboard().setText(ip_port)

        widget = self.result_list.itemWidget(item)
        if widget:
            ip_label = widget.findChild(QLabel, "ip_label")
            if ip_label:
                original_text = ip_label.text()
                if "(IP copied to clipboard)" not in original_text:
                    ip_label.setText(original_text + " <i style='color:gray'>(IP copied to clipboard)</i>")
                    QTimer.singleShot(2000, lambda: ip_label.setText(original_text))

    def on_progress_update(self, checked, total, found, _):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(checked)
        self.checked_label.setText(f"Checked: {checked}")
        self.found_label.setText(f"Found: {found}")

    def on_search_finished(self):
        self._is_searching = False
        self.search_button.setText("🔍 Search")

    def show_help(self):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.about(
            self,
            "Help",
            (
                "<b>Server IP or URL</b>:<br>"
                "The base IP address or domain name of the server. Used as the starting point for scanning.<br><br>"

                "<b>Port range</b>:<br>"
                "The range of ports to scan. By default, Minecraft uses port 25565.<br><br>"

                "<b>IP deviation (±)</b>:<br>"
                "How many neighboring IPs in the last octet (x.x.x.<b>xx</b>) to scan in both directions from the base IP.<br>"
                "For example, with 192.168.0.100 and deviation ±2, the scanner will check IPs from 192.168.0.98 to 192.168.0.102.<br><br>"

                "<b>Max threads</b>:<br>"
                "The number of threads used for parallel scanning. Higher values increase speed but also CPU load."
            )
        )

    def show_about(self):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.about(
            self,
            "About",
            (
                "<h2><b>MineRadar</b> v0.1</h2><br>"

                "Made with <b><span style='color:#ff0000'>&lt;3</span></b> by <b>Myp3xx</b><br>"
                "Powered by <a href='https://pypi.org/project/PyQt6'>PyQt6</a> and <a href='https://pypi.org/project/mcstatus'>mcstatus</a>.<br><br>"

                "Licensed under the GNU General Public License v3.0<br>"
                "<a href='https://github.com/Myp3xx/MineRadar'>GitHub</a>"
            )
        )

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # that's fucking hilarious
    app_icon = b'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAAXNSR0IArs4c6QAAAldJREFUOI1tkr1v01AUxX/v2U6cryZ12yRAKQghsSKEKoEqmo2ZhQGJigWxMPMfMZe/ASFUASpMUIZWUNKCkrjEiRM7/noML7EA9Sz3Dffcc+65Tzx7cU/JXwZZO0UEAoAsyKhfLbLA5DhmdBAC0O5UGJ/ErN60AZAAqp7l5FrLQpYkAN63GcN3s5y4wIIMYKqvAm6ofMDhrgvA7GIZqyWZJhHiALwu1K5rV4NPIemhovnARsoNcnLv7Yjm3SWMFYmxKpicRgCUGgUA0oHC7ya5em83RGbH4O75uHs+hpR8f+3mzaWWBcA0iai1i4ReDIBQAvOaXlPKDbDXLNIsI80ykiQFoHt0hncSUDBMRqcBf0MdKdSRwkDoEMN+TKWl92uUywxPplzZXCHyUgbdMQDHH84Yjqb4X3SosyRhqmKku+eTZpk+37waQvLj/W+KygSlVQ0pkQi8MMAwjNyNjJMkVw/jhHGg7100DbxpgBSCtVoNBFxyluns75CmKVJIVKSQtQslwn5M0Ito1mtUbT0sSlLa9SWqdhHX9+ns7+SqPx/fwTIklWIBKTxQSvvseWP8cMa606BgGpwOh9x6+zAndh9t5u+ipS8kdXAlnGqFjVWHdafBLEnZ/rjDZWdZp/78PudhMov0ACll7sKdTCkV9PTBk61ziQCu72sH9tyKEIL+2GelUqY/9nlz+yUAn7d3/6kHnVe6H/17xdPtLSWEQCnFosZphlKKURjSXKoRJwmZUtiWRcE0OfMnONUKCjD/JwshckdLto0fhlRte66n4VQrcxfwB5HjDHL54TF9AAAAAElFTkSuQmCC'

    pixmap = QPixmap()
    pixmap.loadFromData(QByteArray.fromBase64(app_icon))
    icon_pixmap = pixmap.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
    icon = QIcon(icon_pixmap)
    app.setWindowIcon(icon)
    
    window = ServerFinder()
    window.resize(800, 600)
    window.show()
    sys.exit(app.exec())
