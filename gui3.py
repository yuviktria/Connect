# gui.py
# PyQt6 GUI client - FULLY PATCHED
# Fixed: Friend Management Thread Safety, Data Persistence, Atomic Writes, Race Conditions

import sys
import os
import json
import socket
import ssl
import base64
import traceback
import requests
import mimetypes
import webbrowser
import threading
import shutil
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QTextEdit, QLineEdit, QStackedWidget, QFileDialog,
    QInputDialog, QMessageBox, QScrollArea, QFrame, QMenu, QGridLayout
)
from PyQt6.QtGui import QIcon, QPixmap, QAction, QFont, QPainter, QPainterPath, QColor
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QTimer, QPoint, QMimeData

# --- Configuration ---
HOST = '192.168.29.114'
PORT = 5000
HTTP_PORT = 5001
GLOBAL_CHAT_FILE = "chat_history.json"
PROFILES_DIR = "profiles"
GLOBAL_CHAT_LOCK = threading.Lock() 

COLOR_BG = "#F5F5F0"
COLOR_PANEL = "#E6D8C3"
COLOR_ACCENT = "#5D866C"
COLOR_DIVIDER = "#C2A68C"

FRIENDS_REFRESH_INTERVAL_MS = 20_000
PENDING_REFRESH_INTERVAL_MS = 20_000

# Small SVG icons (base64)
ACCEPT_SVG_B64 = base64.b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
  <circle cx="12" cy="12" r="12" fill="#28a745"/>
  <path d="M6 12.2l3.2 3.2L18 6.6" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>''').decode('ascii')
DECLINE_SVG_B64 = base64.b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
  <circle cx="12" cy="12" r="12" fill="#dc3545"/>
  <path d="M8 8l8 8M16 8l-8 8" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>''').decode('ascii')

def icon_from_base64(b64: str) -> QIcon:
    data = base64.b64decode(b64)
    pix = QPixmap()
    pix.loadFromData(data)
    return QIcon(pix)

# Networking worker
class ReceiverThread(QThread):
    message_received = pyqtSignal(str)
    connection_error = pyqtSignal(str)

    def __init__(self, sock):
        super().__init__()
        self.sock = sock
        self.running = True

    def run(self):
        try:
            while self.running:
                try:
                    data = self.sock.recv(4096)
                    if not data:
                        break
                    try:
                        text = data.decode('utf-8')
                    except Exception:
                        text = data.decode('latin-1')
                    self.message_received.emit(text)
                except OSError:
                    break
        except Exception as e:
            self.connection_error.emit(str(e))
        finally:
            try:
                self.sock.close()
            except:
                pass

    def stop(self):
        self.running = False
        self.wait(2000)

# Persistence utils
def ensure_profiles_dir():
    try:
        os.makedirs(PROFILES_DIR, exist_ok=True)
    except Exception:
        pass

def load_global_chat_history():
    """
    Loads chat history safely.
    Returns None if the file exists but is unreadable (prevents data wipe).
    """
    if not os.path.exists(GLOBAL_CHAT_FILE):
        return {}
    
    try:
        with open(GLOBAL_CHAT_FILE, 'r') as f:
            data = json.load(f)
            return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: Failed to load chat history ({e}). Returning None to signal failure.")
        return None

def save_global_chat_history(data: dict):
    """
    Atomic write to prevent file corruption during crashes or race conditions.
    """
    tmp_file = GLOBAL_CHAT_FILE + ".tmp"
    try:
        with open(tmp_file, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno()) # Ensure write to disk
        
        # Atomic replace
        shutil.move(tmp_file, GLOBAL_CHAT_FILE)
    except Exception as e:
        print("Failed to write global chat file:", e)
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except:
                pass

# Helpers for circular avatars + online dot
def circular_pixmap(source: QPixmap, size: int) -> QPixmap:
    if source.isNull():
        p = QPixmap(size, size)
        p.fill(Qt.GlobalColor.transparent)
        return p
    sq = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    mask = QPixmap(size, size)
    mask.fill(Qt.GlobalColor.transparent)
    painter = QPainter(mask)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, sq)
    painter.end()
    return mask

def avatar_icon_for(nickname: str, pixmap: QPixmap, size: int = 32, online: bool = False) -> QIcon:
    base = circular_pixmap(pixmap, size)
    painter = QPainter(base)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if online:
        dot_size = max(10, size // 6)
        x = size - dot_size - 2
        y = size - dot_size - 2
        painter.setBrush(QColor("#28a745"))
        painter.setPen(Qt.GlobalColor.transparent)
        painter.drawEllipse(x, y, dot_size, dot_size)
    painter.end()
    return QIcon(base)

class FileManiaWindow(QWidget):
    file_action_signal = pyqtSignal(str, str) 

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FileMania: AI File Analysis")
        self.setFixedSize(400, 300)
        self.setAcceptDrops(True)
        self.file_path = None
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        self.drop_zone = QFrame()
        self.drop_zone.setStyleSheet("border: 2px dashed #AAA; border-radius: 10px; padding: 20px;")
        drop_layout = QVBoxLayout(self.drop_zone)
        self.file_label = QLabel("Drag & Drop File Here")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.setFont(QFont("Arial", 10))
        drop_layout.addWidget(self.file_label)
        
        main_layout.addWidget(self.drop_zone, 1)

        button_frame = QFrame()
        grid_layout = QGridLayout(button_frame)
        
        self.btn_summarize = QPushButton("Summarize")
        self.btn_find_info = QPushButton("Find Info")
        self.btn_generate_report = QPushButton("Generate Report")

        self.btn_summarize.clicked.connect(lambda: self.trigger_action("Summarize"))
        self.btn_find_info.clicked.connect(lambda: self.trigger_action("FindInfo"))
        self.btn_generate_report.clicked.connect(lambda: self.trigger_action("GenerateReport"))
        
        grid_layout.addWidget(self.btn_summarize, 0, 0)
        grid_layout.addWidget(self.btn_find_info, 0, 1)
        grid_layout.addWidget(self.btn_generate_report, 1, 0, 1, 2)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setStyleSheet("""
            background-color: #d9534f;
            color: white;
            border-radius: 8px;
            padding: 6px 12px;
            font-weight: bold;
        """)
        self.cancel_button.clicked.connect(self.close)
        main_layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(button_frame)
        self.update_button_states(False)

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            parent_geom = self.parent().geometry()
            x = parent_geom.x() + (parent_geom.width() - self.width()) // 2
            y = parent_geom.y() + (parent_geom.height() - self.height()) // 2
            self.move(x, y)

    def update_button_states(self, enabled):
        self.btn_summarize.setEnabled(enabled)
        self.btn_find_info.setEnabled(enabled)
        self.btn_generate_report.setEnabled(enabled)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                self.file_path = urls[0].toLocalFile()
                self.file_label.setText(f"Loaded: {os.path.basename(self.file_path)}")
                self.drop_zone.setStyleSheet("border: 2px solid #5D866C; border-radius: 10px; padding: 20px;")
                self.update_button_states(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def trigger_action(self, action):
        if not self.file_path or not os.path.exists(self.file_path):
            QMessageBox.warning(self, "Error", "Please drop a file into the window first.")
            return

        self.file_action_signal.emit(self.file_path, action)
        self.file_path = None
        self.file_label.setText("Drag & Drop File Here")
        self.drop_zone.setStyleSheet("border: 2px dashed #AAA; border-radius: 10px; padding: 20px;")
        self.update_button_states(False)
        self.close()

# Main app
class AgenticChatApp(QMainWindow):

    # --- SIGNALS ---
    ui_message_signal = pyqtSignal(str, bool, str)
    ai_indicator_signal = pyqtSignal(str, bool)
    add_chat_list_item_signal = pyqtSignal(str, QIcon)
    # New signals for thread-safe Friend/Pending updates
    friends_list_data_signal = pyqtSignal(list)
    pending_list_data_signal = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Connect")
        self.setGeometry(100, 100, 1300, 750)
        self.setStyleSheet(f"background-color: {COLOR_BG};")

        ensure_profiles_dir()

        self.sock = None
        self.receiver = None

        self.nickname = None
        self.user_profile_dir = None
        self.user_profile_file = None
        
        self.chat_file = None 
        self.global_history = {}
        self.unread_local = {}

        self.awaiting_friends = False
        self.awaiting_pending = False
        self.pending_entries = []
        self.profile_pics = {}
        self.auto_active = set()
        self.ai_running = set()

        self.init_ui()
        self.prompt_and_connect()
        
        # Connect Signals
        self.ui_message_signal.connect(self.add_message_to_view)
        self.add_chat_list_item_signal.connect(self.on_add_chat_list_item)
        self.ai_indicator_signal.connect(self.update_ai_indicator)
        self.friends_list_data_signal.connect(self.on_update_friends_ui)
        self.pending_list_data_signal.connect(self.on_update_pending_ui)

    def init_ui(self):
        main_layout = QHBoxLayout()
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # Left buttons
        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(100)
        self.left_panel.setStyleSheet(f"background-color: {COLOR_PANEL}; border-right: 1px solid {COLOR_DIVIDER};")
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(6, 20, 6, 6)
        left_layout.setSpacing(10)

        self.chat_btn = QPushButton("Chats")
        self.friend_btn = QPushButton("Friends")
        self.profile_btn = QPushButton("Profile")
        self.btn_file_mania = QPushButton("FileMania")
        self.profile_btn.clicked.connect(lambda: self.stacked_widget.setCurrentWidget(self.profile_page))

        for btn in [self.chat_btn, self.friend_btn, self.profile_btn, self.btn_file_mania]:
            btn.setFixedHeight(36)
            btn.setStyleSheet(f"background-color: {COLOR_ACCENT}; color: white; border-radius: 8px;")
            left_layout.addWidget(btn)
        left_layout.addStretch()

        # Middle
        self.stacked_widget = QStackedWidget()
        self.chat_page = self.create_chat_page()
        self.friend_page = self.create_friend_page()
        self.profile_page = self.create_profile_page()
        self.stacked_widget.addWidget(self.chat_page)
        self.stacked_widget.addWidget(self.friend_page)
        self.stacked_widget.addWidget(self.profile_page)
        self.stacked_widget.setCurrentWidget(self.chat_page)

        self.chat_btn.clicked.connect(lambda: self.stacked_widget.setCurrentWidget(self.chat_page))
        self.friend_btn.clicked.connect(self.on_friends_tab_clicked)
        self.profile_btn.clicked.connect(lambda: self.stacked_widget.setCurrentWidget(self.profile_page))
        self.btn_file_mania.clicked.connect(self.show_file_mania)

        # AI display
        self.ai_display = QTextEdit()
        self.ai_display.setReadOnly(True)
        self.ai_display.setStyleSheet(f"background-color: {COLOR_PANEL}; border-left: 2px solid {COLOR_DIVIDER}; padding: 10px;")
        self.ai_display.setFixedWidth(350)
        self.ai_display.setText("")

        main_layout.addWidget(self.left_panel)
        main_layout.addWidget(self.stacked_widget, 3)
        main_layout.addWidget(self.ai_display, 1)

        self.friends_timer = QTimer(self)
        self.friends_timer.timeout.connect(self.request_friends_list)
        self.pending_timer = QTimer(self)
        self.pending_timer.timeout.connect(self.request_pending_list)

    def create_chat_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left.setFixedWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        search_container = QHBoxLayout()
        self.search_icon = QLabel()
        pix = QPixmap(20, 20)
        pix.fill(Qt.GlobalColor.transparent)
        self.search_icon.setPixmap(pix)
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search chats…")
        self.search_bar.setStyleSheet(f"background-color: {COLOR_PANEL}; border: 1px solid {COLOR_DIVIDER}; border-radius: 15px; padding: 8px 12px;")
        search_container.addWidget(self.search_icon)
        search_container.addWidget(self.search_bar)

        left_layout.addLayout(search_container)

        self.chat_list = QListWidget()
        self.chat_list.setStyleSheet(f"QListWidget::item{{padding:10px; border-bottom:1px solid {COLOR_DIVIDER};}} QListWidget::item:selected{{background-color:{COLOR_ACCENT}; color:white;}}")
        left_layout.addWidget(self.chat_list)
        self.chat_list.itemClicked.connect(self.on_chat_selected)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self.on_chat_context_menu)

        self.search_bar.textChanged.connect(self.filter_chats)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(10, 10, 10, 10)

        self.chat_header = QWidget()
        self.chat_header.setFixedHeight(64)
        ch_layout = QHBoxLayout(self.chat_header)
        ch_layout.setContentsMargins(6, 6, 6, 6)
        self.header_avatar = QLabel()
        self.header_avatar.setFixedSize(48, 48)
        self.header_avatar.setStyleSheet(f"border-radius:24px; background-color:{COLOR_DIVIDER};")
        self.header_name = QLabel("")
        self.header_name.setFont(QFont('Arial', 13))
        ch_layout.addWidget(self.header_avatar)
        ch_layout.addWidget(self.header_name)
        ch_layout.addStretch()
        self.chat_header.hide()

        self.conversation_area = QScrollArea()
        self.conversation_area.setWidgetResizable(True)
        self.chat_widget = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_widget)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.conversation_area.setWidget(self.chat_widget)

        input_row = QHBoxLayout()

        self.attach_btn = QPushButton()
        # Safe loading of icon
        if os.path.exists("attach.png"):
            self.attach_btn.setIcon(QIcon("attach.png")) 
        else:
            self.attach_btn.setText("+") # Fallback text

        self.attach_btn.setFixedSize(35, 35)
        self.attach_btn.setStyleSheet("font-size: 16px; border-radius: 17px;")
        self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_btn.clicked.connect(self.on_attach_clicked)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type a message")
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.on_send_clicked)
        self.input_field.returnPressed.connect(self.on_send_clicked)
        input_row.addWidget(self.attach_btn)
        input_row.addWidget(self.input_field)
        input_row.addWidget(self.send_btn)

        right_layout.addWidget(self.chat_header)
        right_layout.addWidget(self.conversation_area)
        right_layout.addLayout(input_row)

        layout.addWidget(left)
        layout.addWidget(right_container, 1)

        return page
        
    def on_add_chat_list_item(self, name, icon):
        it = QListWidgetItem(name)
        it.setIcon(icon)
        self.chat_list.addItem(it)

    def on_attach_clicked(self):
        current_friend = self.get_current_chat()
        if not current_friend:
            QMessageBox.warning(self, "No Chat Selected", "Please select a chat before sending a file.")
            return

        path, _ = QFileDialog.getOpenFileName(self, "Select File to Send", "")
        if not path:
            return

        upload_thread = threading.Thread(target=self.upload_file, args=(path, current_friend), daemon=True)
        upload_thread.start()

    def upload_file(self, file_path: str, recipient: str):
        try:
            url = f"http://{HOST}:{HTTP_PORT}/"
            filename = os.path.basename(file_path)
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type is None:
                mime_type = 'application/octet-stream'

            with open(file_path, 'rb') as f:
                file_data = f.read()             
                headers = {
                    'X-Filename': filename,
                    'Content-Type': mime_type,
                }
                # Visual indicator only, don't put in chat history yet
                self.ai_indicator_signal.emit("Uploading...", True)
            
                response = requests.post(url, data=file_data, headers=headers, timeout=300)
            
            self.ai_indicator_signal.emit("Uploading...", False)
            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                file_id = data.get("file_id")
                file_name = data.get("filename")
                file_url = data.get("url")

                file_msg_payload = f"FILE|{file_id}|{file_name}|{file_url}"
                payload = f"PRIVATE|{recipient}|{file_msg_payload}"
                self.send_raw(payload)

                self.append_global_message(self.nickname, recipient, file_msg_payload)
                
                # NOW we update the UI
                timestamp = datetime.now().strftime('%H:%M')
                self.ui_message_signal.emit(file_msg_payload, False, timestamp)
                print(f"Successfully uploaded and sent file: {file_name}")

            else:
                raise Exception(data.get("error", "Unknown upload error"))

        except Exception as e:
            self.ai_indicator_signal.emit("Uploading...", False)
            print(f"File upload failed: {e}")
            self.ui_message_signal.emit(f"(Upload Failed: {str(e)})", False, datetime.now().strftime('%H:%M'))

    def on_file_bubble_clicked(self):
        sender_button = self.sender()
        if not sender_button:
            return

        file_url = sender_button.property("file_url")
        file_name = sender_button.property("file_name")

        if not file_url:
            return

        save_path, _ = QFileDialog.getSaveFileName(self, "Save File", file_name)
        if not save_path:
            return

        try:
            response = requests.get(file_url, stream=True)
            response.raise_for_status()

            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            QMessageBox.information(self, "Download Complete", 
                f"File '{file_name}' saved to:\n{save_path}")
            webbrowser.open(f"file:///{os.path.dirname(save_path)}")

        except Exception as e:
            print(f"Download failed: {e}")
            QMessageBox.critical(self, "Download Failed", f"Could not download file: {e}")

    def trigger_clear_chat(self, friend: str):
        reply = QMessageBox.warning(
            self,
            "Clear Chat",
            f"Are you sure you want to permanently clear your chat history with {friend}?\nThis will clear the history for both of you on the server.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.send_raw(f"/clear {friend}")
            self.clear_local_chat(friend)
            current_chat_friend = self.get_current_chat()
            if current_chat_friend == friend:
                self.chat_layout_parent_clear()

            QMessageBox.information(self, "Chat Cleared", f"Chat with {friend} has been cleared.")

    def create_friend_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        pending_header = QLabel("Pending Requests")
        pending_header.setFont(QFont('Arial', 13))
        layout.addWidget(pending_header)

        self.pending_list_widget = QListWidget()
        self.pending_list_widget.setStyleSheet(f"QListWidget::item{{padding:8px; border-bottom:1px solid {COLOR_DIVIDER};}}")
        layout.addWidget(self.pending_list_widget)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        friends_header = QLabel("My Friends")
        friends_header.setFont(QFont('Arial', 13))
        layout.addWidget(friends_header)

        self.friends_list_widget = QListWidget()
        self.friends_list_widget.setStyleSheet(f"QListWidget::item{{padding:10px; border-bottom:1px solid {COLOR_DIVIDER};}} QListWidget::item:selected{{background-color:{COLOR_ACCENT}; color:white;}}")
        layout.addWidget(self.friends_list_widget)

        add_btn = QPushButton("Add Friend")
        add_btn.clicked.connect(self.add_friend_dialog)
        layout.addWidget(add_btn)

        self.friends_list_widget.itemClicked.connect(self.on_friend_item_clicked)

        return page

    def create_profile_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.profile_pic = QLabel()
        default_pp = QPixmap(120, 120)
        default_pp.fill(Qt.GlobalColor.transparent)
        self.profile_pic.setPixmap(default_pp)
        self.profile_pic.setStyleSheet(f"border-radius:60px; background-color:{COLOR_DIVIDER}; margin:10px auto;")
        self.profile_pic.setFixedSize(120, 120)
        self.profile_pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.profile_pic, alignment=Qt.AlignmentFlag.AlignHCenter)

        name_label = QLabel("Name:")
        name_label.setFont(QFont('Arial', 11))
        self.name_field = QLineEdit()
        self.name_field.setFixedWidth(250)
        self.name_field.setStyleSheet(f"border:1px solid {COLOR_DIVIDER}; border-radius:8px; padding:6px;")

        desig_label = QLabel("Designation:")
        desig_label.setFont(QFont('Arial', 11))
        self.desig_field = QLineEdit()
        self.desig_field.setFixedWidth(250)
        self.desig_field.setStyleSheet(f"border:1px solid {COLOR_DIVIDER}; border-radius:8px; padding:6px;")

        upload_btn = QPushButton("Upload Profile Picture")
        upload_btn.setStyleSheet(f"background-color:{COLOR_ACCENT}; color:white; border-radius:8px; padding:6px 12px;")
        upload_btn.clicked.connect(self.upload_profile_picture)

        save_btn = QPushButton("Save Profile")
        save_btn.setStyleSheet(f"background-color:{COLOR_ACCENT}; color:white; border-radius:8px; padding:6px 12px;")
        save_btn.clicked.connect(self.save_profile)

        layout.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.name_field, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(desig_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.desig_field, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(upload_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        return page

    def load_own_profile_pic(self):
        if not self.user_profile_file or not os.path.exists(self.user_profile_file):
            return
        try:
            with open(self.user_profile_file, "r") as f:
                data = json.load(f)
            pic_name = data.get("profile_pic")
            if pic_name:
                pic_path = os.path.join(self.user_profile_dir, pic_name)
                if os.path.exists(pic_path):
                    pixmap = QPixmap(pic_path).scaled(
                        120, 120,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self.profile_pic.setPixmap(pixmap)
                    self.profile_pic.setStyleSheet("border-radius:60px;")
        except Exception as e:
            print("Error loading profile picture:", e)

    def save_profile(self):
        if not self.user_profile_file:
            return
            
        data = {
            "name": self.name_field.text(),
            "designation": self.desig_field.text()
        }
        if os.path.exists(self.user_profile_file):
            try:
                with open(self.user_profile_file, "r") as f:
                    old_data = json.load(f)
                data.update(old_data)
            except Exception:
                pass
        with open(self.user_profile_file, "w") as f:
            json.dump(data, f, indent=4)
        QMessageBox.information(self, "Saved", "Profile saved successfully!")

    def load_profile_info(self):
        if not self.user_profile_file or not os.path.exists(self.user_profile_file):
            return
        try:
            with open(self.user_profile_file, "r") as f:
                data = json.load(f)
            self.name_field.setText(data.get("name", ""))
            self.desig_field.setText(data.get("designation", ""))
        except Exception as e:
            print("Error loading profile info:", e)

    def prompt_and_connect(self):
        nick, ok = QInputDialog.getText(self, "Nickname", "Enter your nickname:")
        if not ok or not nick.strip():
            QMessageBox.critical(self, "Nickname required", "A nickname is required to connect.")
            sys.exit(0)
        self.nickname = nick.strip()
        self.chat_file = f"chat_{self.nickname}.json" 
        
        # ---------------- NEW: ASK FOR PASSWORD ----------------
        password, ok = QInputDialog.getText(
            self,
            "Password",
            "Enter your password:",
            QLineEdit.EchoMode.Password
        )
        if not ok or not password.strip():
            QMessageBox.critical(self, "Password required", "A password is required to login.")
            sys.exit(0)

        # Store login credentials
        self.login_username = self.nickname
        self.login_password = password.strip()
        # ---------------------------------------------------------

        self.user_profile_dir = os.path.join(PROFILES_DIR, self.nickname)
        self.user_profile_file = os.path.join(self.user_profile_dir, "profile.json")
        os.makedirs(self.user_profile_dir, exist_ok=True)
        
        self.load_profile_info()
        self.load_own_profile_pic()

        with GLOBAL_CHAT_LOCK:
            hist = load_global_chat_history()
            self.global_history = hist if hist is not None else {}
            
        self.global_history.setdefault(self.nickname, {})
        self.load_all_profile_pics()
        self.refresh_chat_list_from_history()

        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            cert_path = os.path.join(os.getcwd(), "server.crt")
            if os.path.exists(cert_path):
                context.load_verify_locations(cert_path)
                context.verify_mode = ssl.CERT_REQUIRED
                context.check_hostname = False
            else:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

            self.sock = context.wrap_socket(raw_sock, server_hostname=HOST)
            self.sock.connect((HOST, PORT))

            # ---------------- NEW: SEND LOGIN MESSAGE ----------------
            try:
                login_packet = f"LOGIN|{self.login_username}|{self.login_password}"
                self.sock.send(login_packet.encode())
            except Exception as e:
                QMessageBox.critical(self, "Login Error", f"Failed to send login request: {e}")
                return
            # ----------------------------------------------------------

        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Failed to connect to server: {e}")
            return

        self.receiver = ReceiverThread(self.sock)
        self.receiver.message_received.connect(self.handle_incoming)
        self.receiver.connection_error.connect(self.on_connection_error)
        self.receiver.start()

        self.friends_timer.start(FRIENDS_REFRESH_INTERVAL_MS)
        self.pending_timer.start(PENDING_REFRESH_INTERVAL_MS)

        QTimer.singleShot(700, self.request_friends_list)
        QTimer.singleShot(900, self.request_pending_list)
    def on_connection_error(self, text):
        QMessageBox.critical(self, "Connection Error", text)

    def send_raw(self, text: str):
        try:
            if self.sock:
                self.sock.send(text.encode('utf-8'))
        except Exception as e:
            print("Send failed:", e)

    def request_friends_list(self):
        try:
            self.awaiting_friends = True
            self.send_raw("/friends")
        except Exception as e:
            print("Failed to request friends:", e)

    def request_pending_list(self):
        try:
            self.awaiting_pending = True
            self.send_raw("/pending")
        except Exception as e:
            print("Failed to request pending:", e)

    def add_ai_running(self, ai_name: str):
        marker = f"<{ai_name}> running..."
        if ai_name in self.ai_running:
            return
        self.ai_running.add(ai_name)
        self.ai_display.append(f"{marker}")

    def clear_ai_running(self, ai_name: str):
        if ai_name not in self.ai_running:
            return
        self.ai_running.remove(ai_name)
        current = self.ai_display.toPlainText().splitlines()
        new_lines = [l for l in current if not l.strip().startswith(f"<{ai_name}>")]
        self.ai_display.setPlainText(''.join(new_lines))

    def detect_ai_name_from_text(self, text: str) -> str | None:
        t = text.lower()
        if 'summary' in t or '🧾' in t:
            return 'Summarizer'
        if 'helper' in t or '🧠' in t or 'helper response' in t:
            return 'Helper'
        if 'playbook' in t:
            return 'PlayBook'
        if 'autoai' in t or 'autoai' in text.lower():
            return 'AutoAI'
        if 'filemania' in t or '🧠 filemania' in t:
            return 'FileMania'
        return None

    def handle_incoming(self, raw: str):
        raw = raw.strip()

        # ----------------- LOGIN RESPONSES -----------------
        if raw.startswith("LOGIN_OK"):
            # Login succeeded, proceed normally
            return

        if raw.startswith("LOGIN_FAIL"):
            QMessageBox.critical(self, "Login Failed", "Invalid username or password.")
            sys.exit(0)

        if raw.startswith("FIRST_LOGIN|OK"):
            # Server requires the user to change password
            self.handle_first_login()
            return
        # ---------------------------------------------------

        if raw == "NICK":
            try:
                self.sock.send(self.nickname.encode('utf-8'))
            except Exception:
                pass
            return

        if self.awaiting_friends and raw.startswith("Your friends:"):
            self.awaiting_friends = False
            self.prepare_friends_data(raw)
            return

        if self.awaiting_pending and raw.startswith("Pending friend requests:"):
            self.awaiting_pending = False
            self.prepare_pending_data(raw)
            return

        if "|" in raw:
            sender, msg = raw.split("|", 1)
            
            # 1. Persist to disk (Thread-Safe & Deduped & Defensively Merged)
            self.append_global_message(sender, self.nickname, msg)
            
            current = self.get_current_chat()
            if current and current == sender:
                timestamp = datetime.now().strftime('%H:%M')
                self.ui_message_signal.emit(msg, True, timestamp)
            else:
                self.unread_local.setdefault(sender, []).append(msg)
                self.mark_unread(sender)
            return

        ai_name = self.detect_ai_name_from_text(raw)
        if ai_name:
            self.clear_ai_running(ai_name)
            self.ai_display.append(raw)
            return
        return

    def handle_first_login(self):
        # Ask new password
        new_pass, ok = QInputDialog.getText(
            self,
            "Change Password",
            "Enter NEW password:",
            QLineEdit.EchoMode.Password
        )

        if not ok or not new_pass.strip():
            QMessageBox.warning(self, "Required", "You MUST change your password now.")
            return self.handle_first_login()

        try:
            msg = f"CHANGE_PASS|{self.login_username}|{self.login_password}|{new_pass.strip()}"
            self.sock.send(msg.encode())
            QMessageBox.information(self, "Success", "Password updated. Restart app to login again.")
            sys.exit(0)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update password: {e}")
            sys.exit(0)

    def prepare_friends_data(self, raw_text: str):
        """Parses raw friends string into data list, then emits signal."""
        lines = raw_text.splitlines()
        friends_lines = lines[1:]
        friends_data = []
        
        for ln in friends_lines:
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split()
            name = parts[0]
            symbols = ''.join(parts[1:]) if len(parts) > 1 else ''
            online = '🔥' in symbols
            has_unread = '🗣️' in symbols or '🗣' in symbols
            
            # Pre-loading pixmap here is technically IO but usually fast enough
            # If very slow, move to separate thread, but here it is safe to read
            # effectively decoupling UI update from data parsing
            dp = self.load_profile_pixmap(name)
            
            friends_data.append({
                "name": name,
                "online": online,
                "has_unread": has_unread,
                "pixmap": dp
            })
            
        self.friends_list_data_signal.emit(friends_data)

    def on_update_friends_ui(self, data_list: list):
        """Slot to update Friends and Chat list widgets safely on Main Thread."""
        self.friends_list_widget.clear()
        
        # Map existing items in chat list to preserve them
        existing_chat_items = {
            self.chat_list.item(i).text().split(' (')[0]: self.chat_list.item(i) 
            for i in range(self.chat_list.count())
        }

        for item_data in data_list:
            name = item_data["name"]
            online = item_data["online"]
            has_unread = item_data["has_unread"]
            pixmap = item_data["pixmap"]

            # 1. Update Friends List Widget
            f_item = QListWidgetItem(name)
            if pixmap:
                f_item.setIcon(avatar_icon_for(name, pixmap, size=32, online=online))
            else:
                f_item.setIcon(self.make_status_icon(online))
            f_item.setData(Qt.ItemDataRole.UserRole, {'online': online, 'unread': has_unread})
            self.friends_list_widget.addItem(f_item)

            # 2. Update Chat List Widget
            # Create icons
            if pixmap:
                icon_small = avatar_icon_for(name, pixmap, size=28, online=online)
            else:
                icon_small = self.make_status_icon(online)

            if name not in existing_chat_items:
                chat_item = QListWidgetItem(name)
                chat_item.setIcon(icon_small)
                self.chat_list.addItem(chat_item)
            else:
                existing_item = existing_chat_items[name]
                existing_item.setIcon(icon_small)
                
            # 3. Handle Unread
            if has_unread and not self.unread_local.get(name):
                self.unread_local.setdefault(name, ["(server) unread placeholder"]) 
                self.mark_unread(name)
        
        self.refresh_chat_list_badges()

    def prepare_pending_data(self, raw_text: str):
        lines = raw_text.splitlines()
        pending_lines = []
        for ln in lines[1:]:
            ln = ln.strip()
            if not ln:
                continue
            if ln[0].isdigit() and '.' in ln:
                name = ln.split('.', 1)[1].strip()
            else:
                name = ln
            pending_lines.append(name)
        
        # Store logical list for index matching
        self.pending_entries = pending_lines
        
        # Prepare data for UI
        ui_data = []
        for name in pending_lines:
             dp = self.load_profile_pixmap(name)
             ui_data.append({"name": name, "pixmap": dp})
             
        self.pending_list_data_signal.emit(ui_data)

    def on_update_pending_ui(self, data_list: list):
        self.pending_list_widget.clear()
        
        for idx, item_data in enumerate(data_list):
            name = item_data["name"]
            pixmap = item_data["pixmap"]
            
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 6, 8, 6)
            h.setSpacing(8)
            
            avatar = QLabel()
            if pixmap:
                avatar.setPixmap(circular_pixmap(pixmap, 36))
            else:
                tmp = QPixmap(36,36)
                tmp.fill(Qt.GlobalColor.transparent)
                avatar.setPixmap(tmp)
            avatar.setFixedSize(36, 36)
            avatar.setStyleSheet("border-radius:18px; background-color:#ddd;")
            h.addWidget(avatar)
            
            lbl = QLabel(name)
            lbl.setFont(QFont('Arial', 11))
            h.addWidget(lbl)
            h.addStretch()
            
            accept_btn = QPushButton()
            accept_btn.setIcon(icon_from_base64(ACCEPT_SVG_B64))
            accept_btn.setIconSize(QSize(20, 20))
            accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            accept_btn.setStyleSheet("background:transparent;")
            accept_btn.clicked.connect(lambda _, i=idx: self.respond_pending(i, True))
            
            decline_btn = QPushButton()
            decline_btn.setIcon(icon_from_base64(DECLINE_SVG_B64))
            decline_btn.setIconSize(QSize(20, 20))
            decline_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            decline_btn.setStyleSheet("background:transparent;")
            decline_btn.clicked.connect(lambda _, i=idx: self.respond_pending(i, False))
            
            h.addWidget(accept_btn)
            h.addWidget(decline_btn)
            
            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            self.pending_list_widget.addItem(item)
            self.pending_list_widget.setItemWidget(item, row)

    def make_status_icon(self, online: bool, size=14):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#28a745") if online else QColor("#666666")
        p.setBrush(color)
        p.setPen(Qt.GlobalColor.transparent)
        p.drawEllipse(0, 0, size, size)
        p.end()
        return QIcon(pix)

    def on_friends_tab_clicked(self):
        self.stacked_widget.setCurrentWidget(self.friend_page)
        self.request_friends_list()
        QTimer.singleShot(200, self.request_pending_list)

    def add_message_to_view(self, text, incoming=True, timestamp=None):
        if not timestamp:
            timestamp = datetime.now().strftime('%H:%M')
        else:
            if isinstance(timestamp, str) and ':' in timestamp and len(timestamp) <= 5:
                timestamp_str = timestamp 
            else:
                try:
                    ts = datetime.fromisoformat(timestamp)
                    timestamp_str = ts.strftime('%H:%M')
                except Exception:
                    timestamp_str = datetime.now().strftime('%H:%M')
        is_file_message = False
        if text.startswith("FILE|"):
            try:
                _, file_id, file_name, file_url = text.split("|", 3)
                is_file_message = True
            except Exception:
                pass

        if is_file_message:
            bubble = QPushButton(f"📄 {file_name}") 
            bubble.setCursor(Qt.CursorShape.PointingHandCursor)
            bubble.setToolTip(f"Click to download '{file_name}'")
            bubble.setProperty("file_url", file_url)
            bubble.setProperty("file_name", file_name)
            bubble.clicked.connect(self.on_file_bubble_clicked)
            bubble.setStyleSheet(f"padding:10px; border-radius:12px; background-color:{COLOR_PANEL if incoming else COLOR_ACCENT}; color:{'black' if incoming else 'white'}; text-align: left;")

        else:
            bubble = QLabel(text)
            bubble.setWordWrap(True)        
            bubble.setStyleSheet(f"padding:10px; border-radius:12px; background-color:{COLOR_PANEL if incoming else COLOR_ACCENT}; color:{'black' if incoming else 'white'};")

        bubble.setMaximumWidth(480)
        ts_lbl = QLabel(timestamp_str) 
        ts_lbl.setFont(QFont('Arial', 8))
        ts_lbl.setStyleSheet('color: #666;')
        ts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        v = QVBoxLayout()
        v.setContentsMargins(0,0,0,0)
        v.addWidget(bubble)
        v.addWidget(ts_lbl)
        container_inner = QWidget()
        container_inner.setLayout(v)
        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(8,4,8,4)
        if incoming:
            wrapper.addWidget(container_inner)
            wrapper.addStretch()
        else:
            wrapper.addStretch()
            wrapper.addWidget(container_inner)
        container = QWidget()
        container.setLayout(wrapper)
        self.chat_layout.addWidget(container)
        QTimer.singleShot(50, lambda: self.conversation_area.verticalScrollBar().setValue(self.conversation_area.verticalScrollBar().maximum()))

    def on_chat_selected(self, item: QListWidgetItem):
        if not item: return
        friend = item.text().split(' (')[0]
        
        # 1. Thread-safe load from disk to memory
        with GLOBAL_CHAT_LOCK:
            data = load_global_chat_history()
            if data is not None:
                self.global_history = data
            # If data is None, we keep the existing self.global_history state (Defensive)
            
        self.header_name.setText(friend)
        avatar = self.profile_pics.get(friend)
        if avatar:
            self.header_avatar.setPixmap(circular_pixmap(avatar, 48))
        else:
            p = QPixmap(48,48)
            p.fill(Qt.GlobalColor.transparent)
            self.header_avatar.setPixmap(p)
        self.chat_header.show()
        
        # 2. Clear view and repopulate from refreshed memory
        self.chat_layout_parent_clear()
        
        history = self.load_local_chat(friend)
        
        for m in history:
            incoming = (m.get('sender') != self.nickname)
            self.add_message_to_view(m.get('message'), incoming=incoming, timestamp=m.get('timestamp'))
        
        if friend in self.unread_local and self.unread_local[friend]:
            self.unread_local[friend] = []
            self.send_raw(f"/clearunread {friend}")
            self.refresh_chat_list_badges()

    def chat_layout_parent_clear(self):
        while self.chat_layout.count():
            child = self.chat_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def on_send_clicked(self):
        text = self.input_field.text().strip()
        if not text:
            return
        current = self.get_current_chat()
        if current:
            payload = f"PRIVATE|{current}|{text}"
            self.send_raw(payload)
            self.append_global_message(self.nickname, current, text)
            self.add_message_to_view(text, incoming=False, timestamp=datetime.now().strftime('%H:%M'))
            self.input_field.clear()
        else:
            self.send_raw(text)
            self.input_field.clear()

    def get_current_chat(self):
        cur = self.chat_list.currentItem()
        return cur.text().split(' (')[0] if cur else None

    def load_all_profile_pics(self):
        try:
            for fname in os.listdir(PROFILES_DIR):
                if not fname.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                    continue
                nick = os.path.splitext(fname)[0]
                path = os.path.join(PROFILES_DIR, fname)
                try:
                    pix = QPixmap(path)
                    if not pix.isNull():
                        self.profile_pics[nick] = pix
                except Exception:
                    pass
        except Exception:
            pass

    def load_profile_pixmap(self, nickname):
        if nickname in self.profile_pics:
            return self.profile_pics[nickname]
        path_png = os.path.join(PROFILES_DIR, f"{nickname}.png")
        path_jpg = os.path.join(PROFILES_DIR, f"{nickname}.jpg")
        for p in (path_png, path_jpg):
            if os.path.exists(p):
                try:
                    pix = QPixmap(p)
                    if not pix.isNull():
                        self.profile_pics[nickname] = pix
                        return pix
                except Exception:
                    pass
        return None

    def refresh_chat_list_from_history(self):
        self.chat_list.clear()
        user_hist = self.global_history.get(self.nickname, {})
        for name in sorted(user_hist.keys()):
            item = QListWidgetItem(name)
            dp = self.load_profile_pixmap(name)
            if dp:
                item.setIcon(avatar_icon_for(name, dp, size=28, online=False))
            else:
                item.setIcon(self.make_status_icon(False))
            self.chat_list.addItem(item)

    def append_global_message(self, sender: str, recipient: str, message_text: str):
        """
        Appends a message safely with defensive merge against truncated history files.
        """
        with GLOBAL_CHAT_LOCK:
            file_data = load_global_chat_history()
            
            # Use data loaded from disk, or memory if disk failed (first layer defense)
            data = file_data if file_data is not None else self.global_history
            
            # --- DEFENSIVE MERGE LOGIC (Fix for recurrent truncation) ---
            
            # Check the history list for the specific conversation pair from the file/loaded data
            data.setdefault(sender, {}).setdefault(recipient, [])
            loaded_list = data[sender][recipient]
            
            # Check the same history list from the currently preserved in-memory state
            self.global_history.setdefault(sender, {}).setdefault(recipient, [])
            mem_list = self.global_history[sender][recipient]

            # If the memory list is significantly longer, the loaded data was truncated/stale.
            if len(mem_list) > len(loaded_list):
                # Re-base the loaded data with the longer memory list (Fixes truncation)
                data[sender][recipient] = mem_list
                
                # Symmetrically update the reverse direction using the memory's symmetric list
                self.global_history.setdefault(recipient, {}).setdefault(sender, [])
                data.setdefault(recipient, {})[sender] = self.global_history[recipient][sender]
                print(f"Defensive Merge: Restored {len(mem_list)} messages for {sender} <-> {recipient} from memory before appending.")
            # --- END DEFENSIVE MERGE ---
            
            # Ensure the symmetric recipient keys are present for appending
            if sender != recipient:
                data.setdefault(recipient, {}).setdefault(sender, [])

            # --- DEDUPLICATION LOGIC ---
            target_list = data[sender][recipient]
            is_duplicate = False
            if target_list:
                last_msg = target_list[-1]
                if last_msg['message'] == message_text and last_msg['sender'] == sender:
                    is_duplicate = True
            
            if not is_duplicate:
                entry = {"sender": sender, "message": message_text, "timestamp": datetime.now().isoformat()}
                
                # Append to both sides of the conversation (which now point to the longest history)
                data[sender][recipient].append(entry)
                if sender != recipient:
                     data[recipient][sender].append(entry)
                     
                save_global_chat_history(data)
                self.global_history = data
            else:
                # If duplicate, we still need self.global_history to be the current (merged) state
                if data is not self.global_history:
                     self.global_history = data
                print(f"Duplicate message ignored: {message_text[:20]}...")

        # UI update (signals) - unchanged
        if recipient == self.nickname:
            present = any(self.chat_list.item(i).text().split(' (')[0] == sender for i in range(self.chat_list.count()))
            if not present:
                it = QListWidgetItem(sender)
                dp = self.load_profile_pixmap(sender)
                icon = avatar_icon_for(sender, dp, size=28, online=False) if dp else self.make_status_icon(False)
                self.add_chat_list_item_signal.emit(sender, icon)
                
        elif sender == self.nickname:
            present = any(self.chat_list.item(i).text().split(' (')[0] == recipient for i in range(self.chat_list.count()))
            if not present:
                it = QListWidgetItem(recipient)
                dp = self.load_profile_pixmap(recipient)
                icon = avatar_icon_for(recipient, dp, size=28, online=False) if dp else self.make_status_icon(False)
                self.add_chat_list_item_signal.emit(recipient, icon)

    def load_local_chat(self, friend_name):
        try:
            with GLOBAL_CHAT_LOCK:
                # Ensure we read from the latest state in memory after on_chat_selected reload
                history_list = self.global_history.get(self.nickname, {}).get(friend_name, [])
                return history_list
        except Exception:
            return []

    def clear_local_chat(self, friend_name):
        try:
            with GLOBAL_CHAT_LOCK:
                data = load_global_chat_history()
                if data is None:
                    data = self.global_history
                    
                changed = False
                if self.nickname in data and friend_name in data[self.nickname]:
                    data[self.nickname][friend_name] = []
                    changed = True
                if friend_name in data and self.nickname in data[friend_name]:
                    data[friend_name][self.nickname] = []
                    changed = True
                    
                if changed:
                    save_global_chat_history(data)
                    self.global_history = data
                    return True
        except Exception:
            pass
        return False

    def filter_chats(self):
        query = self.search_bar.text().lower()
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            item.setHidden(query not in item.text().lower())

    def mark_unread(self, sender):
        found = False
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            base = item.text().split(' (')[0]
            if base == sender:
                found = True
                cnt = len(self.unread_local.get(sender, []))
                if cnt:
                    item.setText(f"{sender} ({cnt})")
                else:
                    item.setText(base)
                break
        if not found:
            it = QListWidgetItem(f"{sender} ({len(self.unread_local.get(sender, []))})")
            icon = self.make_status_icon(False)
            self.add_chat_list_item_signal.emit(f"{sender} ({len(self.unread_local.get(sender, []))})", icon)

    def refresh_chat_list_badges(self):
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            base = item.text().split(' (')[0]
            cnt = len(self.unread_local.get(base, []))
            if cnt:
                item.setText(f"{base} ({cnt})")
            else:
                item.setText(base)

    def respond_pending(self, index: int, accept: bool):
        try:
            cmd = f"yes {index+1}" if accept else f"no {index+1}"
            self.send_raw(cmd)
            # Remove from UI logically first to feel responsive
            try:
                name = self.pending_entries[index]
                self.pending_entries.pop(index)
                self.pending_list_widget.takeItem(index)
            except Exception:
                pass
            QTimer.singleShot(800, self.request_friends_list)
            QTimer.singleShot(900, self.request_pending_list)
        except Exception as e:
            print("Failed to respond to pending:", e)

    def add_friend_dialog(self):
        text, ok = QInputDialog.getText(self, "Add Friend", "Enter nickname to send friend request to:")
        if not ok or not text.strip():
            return
        nickname = text.strip()
        self.send_raw(f"/addfriend {nickname}")
        QMessageBox.information(self, "Friend Request", f"Friend request sent to {nickname}.")

    def on_friend_item_clicked(self, item: QListWidgetItem):
        name = item.text().split(' (')[0]
        reply = QMessageBox.question(self, "Friend Action", f"Open chat with {name}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.stacked_widget.setCurrentWidget(self.chat_page)
            for i in range(self.chat_list.count()):
                it = self.chat_list.item(i)
                if it.text().split(' (')[0] == name:
                    self.chat_list.setCurrentItem(it)
                    self.on_chat_selected(it)
                    break

    def upload_profile_picture(self):
        if not self.user_profile_dir or not self.user_profile_file or not self.nickname:
             QMessageBox.warning(self, "Error", "Cannot upload picture before logging in.")
             return
             
        path, _ = QFileDialog.getOpenFileName(self, "Select Profile Picture", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            profile_dir = self.user_profile_dir
            dest = os.path.join(profile_dir, "profile_pic.png")
            public_dest = os.path.join(PROFILES_DIR, f"{self.nickname}.png") 

            pixmap = QPixmap(path).scaled(
                120, 120,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            pixmap.save(dest, "PNG") 
            pixmap.save(public_dest, "PNG") 
            
            self.profile_pic.setPixmap(pixmap)
            self.profile_pic.setStyleSheet("border-radius:60px;")

            data = {}
            if os.path.exists(self.user_profile_file):
                with open(self.user_profile_file, "r") as f:
                    data = json.load(f)
            data["profile_pic"] = "profile_pic.png"
            with open(self.user_profile_file, "w") as f:
                json.dump(data, f, indent=4)

    def on_chat_context_menu(self, pos: QPoint):
        item = self.chat_list.itemAt(pos)
        if not item:
            return
        friend = item.text().split(' (')[0]
        menu = QMenu(self)
        act_sum = QAction('Summarize', self)
        act_play = QAction('PlayBook', self)
        if friend in self.auto_active:
            act_auto = QAction('Stop AutoAI', self)
        else:
            act_auto = QAction('AutoAI', self)
        act_helper = QAction('Helper', self)

        menu.addSeparator()
        act_clear = QAction('Clear Chat', self)

        menu.addAction(act_sum)
        menu.addAction(act_play)
        menu.addAction(act_auto)
        menu.addAction(act_helper)
        menu.addAction(act_clear)
        act_sum.triggered.connect(lambda: self.trigger_summarize(friend))
        act_play.triggered.connect(lambda: self.trigger_playbook(friend))
        act_auto.triggered.connect(lambda: self.trigger_auto(friend))
        act_helper.triggered.connect(lambda: self.trigger_helper(friend))
        act_clear.triggered.connect(lambda: self.trigger_clear_chat(friend))
        menu.exec(self.chat_list.mapToGlobal(pos))

    def trigger_summarize(self, friend: str):
        self.add_ai_running('Summarizer')
        self.send_raw(f"/summarize {friend}")

    def trigger_playbook(self, friend: str):
        self.add_ai_running('PlayBook')
        self.send_raw(f"/playbook {friend}")

    def trigger_auto(self, friend: str):
        if friend in self.auto_active:
            self.add_ai_running('AutoAI')
            self.send_raw(f"/noAuto {friend}")
            try:
                self.auto_active.remove(friend)
            except KeyError:
                pass
            return
        text, ok = QInputDialog.getText(self, "AutoAI timing", "Enter duration (e.g. 15m) or leave blank for until stopped:")
        if not ok:
            return
        self.add_ai_running('AutoAI')
        cmd = f"/Auto {friend} {text}" if text.strip() else f"/Auto {friend}"
        self.send_raw(cmd)
        self.auto_active.add(friend)

    def trigger_helper(self, friend: str):
        prompt, ok = QInputDialog.getText(self, "Helper", f"Enter your request for {friend}:")
        if not ok or not prompt.strip():
            return
        self.add_ai_running('Helper')
        self.send_raw(f"/helper {friend} {prompt}")

    def update_ai_indicator(self, name, active):
        if active:
            self.add_ai_running(name)
        else:
            self.clear_ai_running(name)

    def show_file_mania(self):
        if not hasattr(self, "file_mania_window") or self.file_mania_window is None:
            self.file_mania_window = FileManiaWindow(self)
            self.file_mania_window.file_action_signal.connect(self.handle_file_mania_action)

        self.file_mania_window.show()
        self.file_mania_window.raise_()
        self.file_mania_window.activateWindow()

    def handle_file_mania_action(self, file_path, action):
        threading.Thread(target=self._upload_and_send_file_analysis, 
                         args=(file_path, action), daemon=True).start()
        
    def _upload_and_send_file_analysis(self, file_path, action):
        try:
            url = f"http://{HOST}:{HTTP_PORT}/"
            filename = os.path.basename(file_path)
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type is None: mime_type = 'application/octet-stream'

            with open(file_path, 'rb') as f:
                file_data = f.read()          
                headers = {'X-Filename': filename, 'Content-Type': mime_type}
                
                self.ai_indicator_signal.emit("FileMania", True)
                
                response = requests.post(url, data=file_data, headers=headers, timeout=300)
            
            self.ai_indicator_signal.emit("FileMania", False) # Turn off when done
            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                file_url = data.get("url")
                command_payload = f"/FILEMANIA|{action}|{file_url}"
                self.send_raw(command_payload)
                
            else:
                raise Exception(data.get("error", "Unknown upload error"))

        except Exception as e:
            self.ai_indicator_signal.emit("FileMania", False)
            print(f"FileMania operation failed: {e}")
            self.ui_message_signal.emit(f"File Analysis Failed: {e}", True, datetime.now().strftime('%H:%M'))

# Entrypoint
if __name__ == '__main__':
    from PyQt6.QtWidgets import QApplication

    def create_icons():
        return icon_from_base64(ACCEPT_SVG_B64), icon_from_base64(DECLINE_SVG_B64)

    app = QApplication(sys.argv)
    global ACCEPT_ICON, DECLINE_ICON
    ACCEPT_ICON, DECLINE_ICON = create_icons()

    window = AgenticChatApp()
    window.show()
    sys.exit(app.exec())