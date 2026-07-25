# Liz Coder Plus - Linux Desktop
# Native Qt6 application for the AI agent desktop assistant

import sys
import os
import json
import datetime
import threading
import logging
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTextEdit, QLineEdit, QPushButton, QLabel, QListWidget,
    QListWidgetItem, QComboBox, QFrame, QSystemTrayIcon, QMenu,
    QGroupBox, QScrollArea, QSizePolicy, QMessageBox
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, Slot, QSize, QThread, QObject
)
from PySide6.QtGui import (
    QIcon, QFont, QColor, QPalette, QAction, QTextCursor
)

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False
    print("WARNING: websocket-client not installed, running in offline demo mode")


# ============================================================
# Chat Service - WebSocket client for the backend
# ============================================================

class ChatService(QObject):
    """WebSocket client that connects to the Liz Coder Plus backend."""
    
    message_received = Signal(str, str)  # role, content
    status_changed = Signal(str)  # status text
    connection_state_changed = Signal(str)  # connected/disconnected/connecting/failed
    models_received = Signal(list)  # list of model dicts

    def __init__(self, url: str = "ws://localhost:8000/ws/chat"):
        super().__init__()
        self.url = url
        self.ws = None
        self._mode = "confirmation"
        self._session_id = ""
        self._running = False
        self._reconnect_timer = None
        self._http_base = "http://localhost:8000"

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str):
        self._mode = "automatic" if value == "automatic" else "confirmation"

    @property
    def session_id(self) -> str:
        return self._session_id

    def connect_to_server(self):
        if not HAS_WEBSOCKET:
            self.status_changed.emit("Demo mode (sin backend)")
            return
        
        self.connection_state_changed.emit("connecting")
        self.status_changed.emit("Conectando...")
        
        thread = threading.Thread(target=self._connect_thread, daemon=True)
        thread.start()

    def _connect_thread(self):
        try:
            self.ws = websocket.WebSocket()
            self.ws.settimeout(10)
            self.ws.connect(self.url)
            self._running = True
            self._session_id = os.urandom(16).hex()
            self.connection_state_changed.emit("connected")
            self.status_changed.emit("Conectado")
            
            # Start receive loop
            self._receive_loop()
        except Exception as e:
            self.connection_state_changed.emit("failed")
            self.status_changed.emit(f"Error: {str(e)[:50]}")

    def _receive_loop(self):
        while self._running and self.ws:
            try:
                raw = self.ws.recv()
                if not raw:
                    break
                data = json.loads(raw)
                msg_type = data.get("type", "message")
                
                if msg_type == "chunk":
                    self.message_received.emit("Liz", data.get("content", ""))
                elif msg_type == "message":
                    self.message_received.emit("Liz", data.get("content", ""))
                elif msg_type == "error":
                    self.message_received.emit("Error", data.get("error", data.get("content", "Error desconocido")))
                elif msg_type == "ping":
                    self.ws.send(json.dumps({"type": "pong"}))
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                if self._running:
                    self.connection_state_changed.emit("failed")
                    self.status_changed.emit(f"Desconectado: {str(e)[:30]}")
                break

    def send_message(self, text: str):
        if not text.strip():
            return
        
        # Show user message immediately
        self.message_received.emit("Tu", text)
        
        if not self.ws or not HAS_WEBSOCKET:
            # Demo mode - simulate response
            self._demo_response(text)
            return
        
        try:
            payload = {
                "message": text,
                "session_id": self._session_id,
                "mode": self._mode
            }
            self.ws.send(json.dumps(payload))
        except Exception as e:
            self.message_received.emit("Error", f"No se pudo enviar: {e}")

    def _demo_response(self, user_text: str):
        """Simulate AI response when no backend is connected."""
        def _respond():
            import time
            time.sleep(0.5)
            response = (
                "Hola! Soy Liz Coder Plus, tu asistente de IA de escritorio.\n\n"
                "Actualmente estoy en modo demo sin backend conectado. "
                "Para conectarme al servidor:\n"
                "1. Inicia el backend: `python -m apps.backend.main`\n"
                "2. Luego presiona 'Conectar' en la barra de herramientas.\n\n"
                "Cuando el backend este activo, podre:\n"
                "- Ejecutar comandos en tu terminal\n"
                "- Crear y editar archivos\n"
                "- Buscar en la web\n"
                "- Gestionar tus proyectos\n"
                "- Y mucho mas!\n\n"
                f'Tu mensaje fue: "{user_text}"'
            )
            self.message_received.emit("Liz", response)
        
        threading.Thread(target=_respond, daemon=True).start()

    def disconnect(self):
        self._running = False
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        self.connection_state_changed.emit("disconnected")
        self.status_changed.emit("Desconectado")

    def fetch_models(self):
        """Fetch available models from backend API."""
        if not HAS_WEBSOCKET:
            return
        
        thread = threading.Thread(target=self._fetch_models_thread, daemon=True)
        thread.start()

    def _fetch_models_thread(self):
        import urllib.request
        try:
            url = f"{self._http_base}/api/models"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                models = data.get("models", [])
                if models:
                    self.models_received.emit(models)
        except:
            pass


# ============================================================
# Main Chat Window
# ============================================================

class ChatWindow(QMainWindow):
    """Main window for Liz Coder Plus desktop assistant."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Liz Coder Plus — AI Desktop Assistant")
        self.setMinimumSize(900, 600)
        self.resize(1200, 800)

        # Services
        self.chat = ChatService()
        self.chat.message_received.connect(self.on_message)
        self.chat.status_changed.connect(self.on_status_changed)
        self.chat.connection_state_changed.connect(self.on_connection_state)

        # State
        self._current_assistant_text = ""
        self._conversations: List[Dict] = []
        self._current_mode = "confirmation"

        self._setup_ui()
        self._setup_styles()
        self._add_demo_conversation()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Splitter: sidebar | main
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # ---- SIDEBAR ----
        sidebar = QWidget()
        sidebar.setFixedWidth(280)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(8)

        # Logo
        logo_layout = QHBoxLayout()
        logo_frame = QFrame()
        logo_frame.setFixedSize(36, 36)
        logo_frame.setStyleSheet("background: #6366f1; border-radius: 12px;")
        logo_label = QLabel("L", logo_frame)
        logo_label.setStyleSheet("color: white; font-size: 20px; font-weight: bold;")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_layout.addWidget(logo_frame)
        
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        name_label = QLabel("Liz Coder Plus")
        name_label.setStyleSheet("font-size: 16px; font-weight: 600; color: #1e1e2e;")
        ver_label = QLabel("v0.18.0 — Linux")
        ver_label.setStyleSheet("font-size: 11px; color: #888;")
        title_col.addWidget(name_label)
        title_col.addWidget(ver_label)
        logo_layout.addLayout(title_col)
        logo_layout.addStretch()
        sidebar_layout.addLayout(logo_layout)

        # New chat button
        self.new_chat_btn = QPushButton("Nueva Conversacion")
        self.new_chat_btn.setCursor(Qt.PointingHandCursor)
        self.new_chat_btn.clicked.connect(self.on_new_chat)
        sidebar_layout.addWidget(self.new_chat_btn)

        # Conversations list
        self.conversations_list = QListWidget()
        self.conversations_list.currentRowChanged.connect(self.on_conversation_selected)
        sidebar_layout.addWidget(self.conversations_list)

        # Status indicator
        self.status_frame = QFrame()
        self.status_frame.setStyleSheet("background: #ef4444; border-radius: 6px; padding: 6px;")
        self.status_frame.setFixedHeight(36)
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(8, 0, 8, 0)
        self.status_label = QLabel("Desconectado")
        self.status_label.setStyleSheet("color: white; font-size: 12px; font-weight: 500;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        sidebar_layout.addWidget(self.status_frame)

        # Settings button
        settings_btn = QPushButton("Configuracion")
        settings_btn.clicked.connect(self.show_settings)
        sidebar_layout.addWidget(settings_btn)

        sidebar_layout.addStretch()
        splitter.addWidget(sidebar)

        # ---- MAIN AREA ----
        main_area = QWidget()
        main_layout2 = QVBoxLayout(main_area)
        main_layout2.setContentsMargins(16, 16, 16, 16)
        main_layout2.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.connect_btn = QPushButton("Conectar")
        self.connect_btn.clicked.connect(self.on_connect)
        toolbar.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("Desconectar")
        self.disconnect_btn.clicked.connect(self.on_disconnect)
        toolbar.addWidget(self.disconnect_btn)

        # Mode buttons
        self.mode_confirm_btn = QPushButton("Confirmar")
        self.mode_confirm_btn.setCheckable(True)
        self.mode_confirm_btn.setChecked(True)
        self.mode_confirm_btn.clicked.connect(lambda: self.set_mode("confirmation"))
        toolbar.addWidget(self.mode_confirm_btn)

        self.mode_auto_btn = QPushButton("Automatico")
        self.mode_auto_btn.setCheckable(True)
        self.mode_auto_btn.clicked.connect(lambda: self.set_mode("automatic"))
        toolbar.addWidget(self.mode_auto_btn)

        # Model selector
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(200)
        self.model_combo.addItem("Auto (por defecto)", "auto")
        toolbar.addWidget(self.model_combo)

        toolbar.addStretch()

        clear_btn = QPushButton("Limpiar")
        clear_btn.clicked.connect(self.on_clear)
        toolbar.addWidget(clear_btn)

        main_layout2.addLayout(toolbar)

        # Messages area
        self.messages_area = QTextEdit()
        self.messages_area.setReadOnly(True)
        self.messages_area.setOpenLinks(False)
        self.messages_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout2.addWidget(self.messages_area)

        # Input area
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Escribe un mensaje a Liz...")
        self.input_field.returnPressed.connect(self.on_send)
        input_layout.addWidget(self.input_field)

        self.send_btn = QPushButton("Enviar")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self.on_send)
        input_layout.addWidget(self.send_btn)

        main_layout2.addLayout(input_layout)
        splitter.addWidget(main_area)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # System tray
        self._setup_tray()

    def _setup_tray(self):
        # System tray icon for background running
        menu = QMenu()
        show_action = menu.addAction("Mostrar")
        show_action.triggered.connect(self.show)
        quit_action = menu.addAction("Salir")
        quit_action.triggered.connect(QApplication.quit)
        
        self.tray = QSystemTrayIcon()
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        try:
            self.tray.setToolTip("Liz Coder Plus — AI Desktop Assistant")
            self.tray.show()
        except:
            pass  # Tray not available in all environments

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.activateWindow()

    def _setup_styles(self):
        # Button styles
        btn_style = """
            QPushButton {
                background: #f4f4f5;
                border: 1px solid #e4e4e7;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                color: #1e1e2e;
            }
            QPushButton:hover { background: #e4e4e7; }
            QPushButton:pressed { background: #d4d4d8; }
            QPushButton:checked { background: #6366f1; color: white; border-color: #6366f1; }
        """
        self.connect_btn.setStyleSheet(btn_style.replace("#f4f4f5", "#6366f1").replace("#1e1e2e", "white").replace("#e4e4e7", "#6366f1").replace("#d4d4d8", "#4f46e5"))
        self.send_btn.setStyleSheet(self.connect_btn.styleSheet())
        self.new_chat_btn.setStyleSheet(self.connect_btn.styleSheet())
        
        for btn in [self.disconnect_btn, self.mode_confirm_btn, self.mode_auto_btn]:
            btn.setStyleSheet(btn_style)
        
        # Find settings button
        for child in self.findChildren(QPushButton):
            if child.text() == "Configuracion":
                child.setStyleSheet(btn_style)

        # Messages area
        self.messages_area.setStyleSheet("""
            QTextEdit {
                background: #fafafa;
                border: 1px solid #e4e4e7;
                border-radius: 8px;
                padding: 12px;
                font-size: 14px;
                line-height: 1.5;
                color: #1e1e2e;
            }
        """)

        # Input field
        self.input_field.setStyleSheet("""
            QLineEdit {
                background: white;
                border: 1px solid #e4e4e7;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 14px;
            }
            QLineEdit:focus { border-color: #6366f1; }
        """)

        # Conversations list
        self.conversations_list.setStyleSheet("""
            QListWidget {
                background: #fafafa;
                border: 1px solid #e4e4e7;
                border-radius: 8px;
                padding: 4px;
                font-size: 13px;
            }
            QListWidget::item { padding: 8px; border-radius: 6px; }
            QListWidget::item:selected { background: #e0e7ff; color: #4338ca; }
        """)

        # Main window
        self.setStyleSheet("""
            QMainWindow { background: white; }
            QWidget { background: white; }
            QSplitter::handle { background: #e4e4e7; width: 1px; }
        """)

    def _add_demo_conversation(self):
        self._conversations.append({
            "title": "Bienvenida",
            "preview": "Hola Liz, como estas?",
            "time": datetime.datetime.now().strftime("%H:%m")
        })
        self._refresh_conversations()

    def _refresh_conversations(self):
        self.conversations_list.clear()
        for conv in self._conversations:
            item = QListWidgetItem(f"{conv['title']}\n{conv['preview']} — {conv['time']}")
            self.conversations_list.addItem(item)

    @Slot(str, str)
    def on_message(self, role: str, content: str):
        cursor = self.messages_area.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        if role == "Tu":
            color = "#6366f1"
            prefix = "TU"
        elif role == "Liz":
            color = "#10b981"
            prefix = "LIZ"
        else:
            color = "#ef4444"
            prefix = "ERROR"

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        self.messages_area.insertHtml(
            f'<div style="margin: 8px 0;">'
            f'<span style="color: {color}; font-weight: 600; font-size: 11px;">{prefix}</span>'
            f'<span style="color: #888; font-size: 11px;"> — {timestamp}</span>'
            f'<br><span style="color: #1e1e2e; font-size: 14px; white-space: pre-wrap;">{self._escape_html(content)}</span>'
            f'</div><hr style="border: none; border-top: 1px solid #f0f0f0;">'
        )
        
        scrollbar = self.messages_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _escape_html(self, text: str) -> str:
        return (text.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace("\n", "<br>"))

    @Slot(str)
    def on_status_changed(self, text: str):
        self.status_label.setText(text)
        
        # Color based on state
        if "Conectado" in text:
            self.status_frame.setStyleSheet("background: #22c55e; border-radius: 6px; padding: 6px;")
        elif "Conectando" in text:
            self.status_frame.setStyleSheet("background: #f97316; border-radius: 6px; padding: 6px;")
        elif "Error" in text or "Demo" in text:
            self.status_frame.setStyleSheet("background: #eab308; border-radius: 6px; padding: 6px;")
        else:
            self.status_frame.setStyleSheet("background: #ef4444; border-radius: 6px; padding: 6px;")

    @Slot(str)
    def on_connection_state(self, state: str):
        if state == "connected":
            self.chat.fetch_models()

    def on_connect(self):
        self.chat.connect_to_server()

    def on_disconnect(self):
        self.chat.disconnect()

    def on_send(self):
        text = self.input_field.text().strip()
        if text:
            self.input_field.clear()
            self.chat.send_message(text)

    def on_clear(self):
        self.messages_area.clear()

    def on_new_chat(self):
        self.messages_area.clear()
        idx = len(self._conversations) + 1
        self._conversations.insert(0, {
            "title": f"Conversacion {idx}",
            "preview": "Nueva conversacion...",
            "time": datetime.datetime.now().strftime("%H:%m")
        })
        self._refresh_conversations()
        self.conversations_list.setCurrentRow(0)

    def on_conversation_selected(self, row):
        pass  # Placeholder for switching conversations

    def set_mode(self, mode: str):
        self._current_mode = mode
        self.chat.mode = mode
        self.mode_confirm_btn.setChecked(mode == "confirmation")
        self.mode_auto_btn.setChecked(mode == "automatic")

    def show_settings(self):
        QMessageBox.information(
            self, "Configuracion",
            "Liz Coder Plus v0.18.0 — Linux\n\n"
            "Backend URL: ws://localhost:8000/ws/chat\n"
            "Para cambiar la configuracion, edita el archivo\n"
            "~/.config/liz-coder-plus/config.json"
        )

    def closeEvent(self, event):
        # Minimize to tray instead of closing
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Liz Coder Plus",
            "La app sigue corriendo en la bandeja del sistema.",
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )


# ============================================================
# Entry Point
# ============================================================

def main():
    # High DPI support
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    
    app = QApplication(sys.argv)
    app.setApplicationName("Liz Coder Plus")
    app.setApplicationVersion("0.18.0")
    
    # Set app font
    font = QFont("Noto Sans", 10)
    app.setFont(font)

    window = ChatWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
