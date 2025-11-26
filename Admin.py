import sys
import os
import json
import random
import string
import hashlib
import smtplib
import ssl
import requests
import threading
from datetime import datetime
from email.message import EmailMessage

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QLabel, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QComboBox, QMessageBox, QTabWidget, QSplitter, QListWidget
)
from PyQt6.QtGui import QFont, QColor, QIcon
from PyQt6.QtCore import Qt, QTimer

# --- Configuration & Styling (Matches gui3.py) ---
USERS_DB_FILE = "users_db.json"
PROFILES_DIR = "profiles"
TEMP_PASS_FILE = "temporary_passwords.json"  # external file to store temp password hashes

# Placeholder AI Webhooks (You will replace these later)
N8N_ONBOARD_WEBHOOK = "WebhookUrl"

# Email Configuration (Replace with your details)
ADMIN_EMAIL = "saavla57@gmail.com"
ADMIN_APP_PASSWORD = "rybb qgoc fqex zpkq"  # Google App Password

COLOR_BG = "#F5F5F0"
COLOR_PANEL = "#E6D8C3"
COLOR_ACCENT = "#5D866C"
COLOR_DIVIDER = "#C2A68C"
COLOR_TEXT = "#2b2b2b"

# --- Backend Logic: User Management & Email ---

def load_users_db():
    if not os.path.exists(USERS_DB_FILE):
        return {}
    try:
        with open(USERS_DB_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_users_db(data):
    with open(USERS_DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_temp_passwords():
    """Load the temporary passwords file (username -> { temp_hash, must_change })."""
    if not os.path.exists(TEMP_PASS_FILE):
        return {}
    try:
        with open(TEMP_PASS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_temp_passwords(data):
    """Save the temporary passwords dictionary atomically."""
    tmp = TEMP_PASS_FILE + ".tmp"
    try:
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, TEMP_PASS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except:
            pass

def set_temp_password(username, plain_password):
    """Store SHA-256 hash of temp password and flag must_change true."""
    tp = load_temp_passwords()
    tp[username] = {
        "temp_password_hash": hashlib.sha256(plain_password.encode()).hexdigest(),
        "must_change": True,
        "created_at": datetime.now().isoformat()
    }
    save_temp_passwords(tp)

def clear_temp_password(username):
    """Remove temp password entry after user changes password or it is invalidated."""
    tp = load_temp_passwords()
    if username in tp:
        tp.pop(username, None)
        save_temp_passwords(tp)

def hash_password(password):
    """Basic SHA-256 hashing for the project."""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_temp_password(length=10):
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(random.choice(chars) for _ in range(length))

def send_welcome_email(recipient_email, username, temp_password, department):
    """Sends the welcome email using smtplib."""
    try:
        msg = EmailMessage()
        msg['Subject'] = "Welcome to Connect Chat - Login Credentials"
        msg['From'] = ADMIN_EMAIL
        msg['To'] = recipient_email
        msg.set_content(f"""
        Welcome to the {department} Team!

        Your account has been created by the Administrator.
        
        Username: {username}
        Temporary Password: {temp_password}

        Please log in immediately. You will be asked to change this password upon first login.
        
        Best regards,
        IT Admin Team
        """)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as smtp:
            smtp.login(ADMIN_EMAIL, ADMIN_APP_PASSWORD)
            smtp.send_message(msg)
        return True, "Email sent successfully."
    except Exception as e:
        return False, str(e)

# --- GUI Components ---

class AdminWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Connect Admin: AI-Integrated Dashboard")
        self.setGeometry(100, 100, 1000, 700)
        self.setStyleSheet(f"background-color: {COLOR_BG}; color: {COLOR_TEXT}; font-family: Arial;")

        # Central Widget & Tabs (single HR Onboarding tab as requested)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {COLOR_DIVIDER}; }}
            QTabBar::tab {{ background: {COLOR_PANEL}; padding: 10px; margin: 2px; border-radius: 4px; }}
            QTabBar::tab:selected {{ background: {COLOR_ACCENT}; color: white; }}
        """)

        self.hr_tab = HRTab()
        self.tabs.addTab(self.hr_tab, "HR Onboarding")

        main_layout.addWidget(self.tabs)

class HRTab(QWidget):
    def __init__(self):
        super().__init__()
        self.layout = QHBoxLayout(self)

        # --- Section 2: Middle (Work Area) ---
        work_area = QWidget()
        work_layout = QVBoxLayout(work_area)
        work_layout.setSpacing(16)

        # 2a. Manual Entry
        manual_group = QGroupBox("Manual User Creation")
        manual_group.setStyleSheet(f"QGroupBox {{ font-weight: bold; border: 1px solid {COLOR_DIVIDER}; margin-top: 10px; }} QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 3px; }}")
        manual_layout = QVBoxLayout()
        manual_layout.setSpacing(8)

        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("Username (e.g. JohnDoe)")
        self.input_name.setStyleSheet(self.input_style())

        self.combo_dept = QComboBox()
        self.combo_dept.addItems(["General", "IT", "HR", "Marketing", "Sales", "Management"])
        self.combo_dept.setStyleSheet(self.input_style())

        self.input_email = QLineEdit()
        self.input_email.setPlaceholderText("Employee Email (e.g. john@company.com)")
        self.input_email.setStyleSheet(self.input_style())

        self.btn_manual_create = QPushButton("Create User & Send Email")
        self.btn_manual_create.setStyleSheet(self.btn_style())
        self.btn_manual_create.clicked.connect(self.handle_manual_create)

        manual_layout.addWidget(QLabel("Username:"))
        manual_layout.addWidget(self.input_name)
        manual_layout.addWidget(QLabel("Department:"))
        manual_layout.addWidget(self.combo_dept)
        manual_layout.addWidget(QLabel("Email:"))
        manual_layout.addWidget(self.input_email)
        manual_layout.addWidget(self.btn_manual_create)
        manual_group.setLayout(manual_layout)

        # 2b. AI Entry
        ai_group = QGroupBox("✨ AI Auto-Onboard")
        ai_group.setStyleSheet(manual_group.styleSheet())
        ai_layout = QVBoxLayout()
        ai_layout.setSpacing(8)

        self.ai_input = QTextEdit()
        self.ai_input.setPlaceholderText("Paste HR email or type instructions here...\nEx: 'Add Sarah Smith to IT and Mike Ross to Legal.'")
        self.ai_input.setStyleSheet(f"border: 1px solid {COLOR_DIVIDER}; border-radius: 5px; padding: 6px; background: white;")
        self.ai_input.setFixedHeight(140)

        self.btn_ai_create = QPushButton("Process with AI & Create Users")
        self.btn_ai_create.setStyleSheet(self.btn_style(ai=True))
        self.btn_ai_create.clicked.connect(self.handle_ai_create)

        ai_layout.addWidget(self.ai_input)
        ai_layout.addWidget(self.btn_ai_create)

        # Status label for both manual & AI actions
        self.ai_status = QLabel("")
        self.ai_status.setStyleSheet("color: #444; padding: 6px; font-style: italic;")
        ai_layout.addWidget(self.ai_status)

        ai_group.setLayout(ai_layout)

        work_layout.addWidget(manual_group)
        work_layout.addWidget(ai_group)

        # --- Section 3: Right (Database View) ---
        db_area = QWidget()
        db_layout = QVBoxLayout(db_area)
        db_layout.setSpacing(8)

        db_layout.addWidget(QLabel("<b>Existing Users</b>"))
        self.user_table = QTableWidget()
        self.user_table.setColumnCount(3)
        self.user_table.setHorizontalHeaderLabels(["User", "Dept", "Email"])
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        db_layout.addWidget(self.user_table)

        db_layout.addWidget(QLabel("<b>Active Departments (Groups)</b>"))
        self.group_list = QListWidget()
        db_layout.addWidget(self.group_list)

        # Add to main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(work_area)
        splitter.addWidget(db_area)
        splitter.setSizes([600, 350])

        self.layout.addWidget(splitter)

        self.refresh_db_view()

    def input_style(self):
        return f"padding: 8px; border: 1px solid {COLOR_DIVIDER}; border-radius: 4px; background: white;"

    def btn_style(self, ai=False):
        color = "#4A6FA5" if ai else COLOR_ACCENT
        return f"background-color: {color}; color: white; padding: 10px; border-radius: 5px; font-weight: bold;"

    def refresh_db_view(self):
        """Loads users from JSON and populates the table."""
        db = load_users_db()
        self.user_table.setRowCount(0)
        departments = set()

        for user, data in db.items():
            row = self.user_table.rowCount()
            self.user_table.insertRow(row)
            self.user_table.setItem(row, 0, QTableWidgetItem(user))
            self.user_table.setItem(row, 1, QTableWidgetItem(data.get('department', 'General')))
            self.user_table.setItem(row, 2, QTableWidgetItem(data.get('email', '')))
            departments.add(data.get('department', 'General'))

        self.group_list.clear()
        for d in sorted(departments):
            self.group_list.addItem(f"🏢 {d}")

    # core logic accepts an optional temp_password (from AI) - unchanged behavior
    def create_user_logic(self, username, dept, email, temp_password=None):
        """The core logic for creating a user, hashing pass, and emailing."""
        if not username or not email:
            return False, "Missing fields"

        db = load_users_db()
        if username in db:
            return False, "User already exists"

        # use AI password if provided, else generate
        temp_pass_var = temp_password if temp_password else generate_temp_password()

        # Send Email
        success, msg = send_welcome_email(email, username, temp_pass_var, dept)

        email_status = "Email Sent" if success else f"Email Failed ({msg})"
        print(f"DEBUG: Created {username} with pass {temp_pass_var}")  # For your testing

        # Hash & Store
        pass_hash = hash_password(temp_pass_var)

        db[username] = {
            "password_hash": pass_hash,
            "department": dept,
            "email": email,
            "force_password_change": True,
            "joined_at": datetime.now().isoformat()
        }
        save_users_db(db)

        # store temp password hash externally for first-login checks
        try:
            set_temp_password(username, temp_pass_var)
        except Exception as e:
            print(f"Warning: failed to write temp password file for {username}: {e}")

        # wipe sensitive in-memory var
        temp_pass_var = None

        self.refresh_db_view()
        return True, f"User created! {email_status}"

    def handle_manual_create(self):
        u = self.input_name.text().strip()
        d = self.combo_dept.currentText()
        e = self.input_email.text().strip()

        # UI feedback + disable button
        self.ai_status.setText("⏳ Creating user and sending email...")
        self.btn_manual_create.setEnabled(False)
        QApplication.processEvents()

        try:
            success, msg = self.create_user_logic(u, d, e)
            if success:
                QMessageBox.information(self, "Success", msg)
                self.input_name.clear()
                self.input_email.clear()
                self.ai_status.setText("✅ User created and emailed.")
            else:
                QMessageBox.warning(self, "Error", msg)
                self.ai_status.setText(f"❌ {msg}")
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))
            self.ai_status.setText("❌ Error creating user.")
        finally:
            self.btn_manual_create.setEnabled(True)
            # clear status after a short delay
            QTimer.singleShot(5000, lambda: self.ai_status.setText(""))

    def handle_ai_create(self):
        text = self.ai_input.toPlainText().strip()
        if not text:
            return

        # UI feedback + disable AI button
        self.ai_status.setText("⏳ Sending to AI Brain and creating users...")
        self.btn_ai_create.setEnabled(False)
        QApplication.processEvents()

        try:
            response = requests.post(
                N8N_ONBOARD_WEBHOOK,
                json={"text": text},
                timeout=20
            )
            response.raise_for_status()

            ai_users = response.json()

            # Validate expected structure
            if not isinstance(ai_users, list):
                raise ValueError("AI response is not a JSON array.")

            log = []
            for user in ai_users:
                # minimal sanity checks
                name = user.get('name')
                dept = user.get('dept', 'General')
                email = user.get('email')
                password = user.get('password')  # may be provided by AI

                if not name or not email:
                    log.append(f"{name or 'UNKNOWN'}: Skipped (missing name/email)")
                    continue

                s, m = self.create_user_logic(
                    username=name,
                    dept=dept,
                    email=email,
                    temp_password=password
                )
                log.append(f"{name}: {m}")

            QMessageBox.information(self, "AI Report", "\n".join(log))
            self.ai_status.setText("✅ AI onboarding complete.")
        except requests.exceptions.RequestException as re:
            self.ai_status.setText("❌ AI request failed.")
            QMessageBox.critical(self, "AI Error", f"Failed to reach AI: {re}")
        except Exception as e:
            self.ai_status.setText("❌ Processing error.")
            QMessageBox.critical(self, "AI Error", f"Error processing AI response: {e}")
        finally:
            self.btn_ai_create.setEnabled(True)
            self.ai_input.clear()
            QTimer.singleShot(6000, lambda: self.ai_status.setText(""))

if __name__ == "__main__":
    # ensure profiles dir exists
    try:
        os.makedirs(PROFILES_DIR, exist_ok=True)
    except Exception:
        pass

    app = QApplication(sys.argv)
    window = AdminWindow()
    window.show()
    sys.exit(app.exec())
