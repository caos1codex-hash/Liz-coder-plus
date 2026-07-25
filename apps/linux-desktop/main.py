# ============================================================
# Liz Coder Plus — Linux Desktop (PySide6 / Qt6)
# Native desktop app for the AI PC-control agent.
# ============================================================

import sys
import os
import json
import html as html_mod
import datetime
import threading
import logging
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTextBrowser, QLineEdit, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QComboBox, QFrame,
    QSystemTrayIcon, QMenu, QSizePolicy, QMessageBox,
    QAbstractScrollArea
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, Slot, QSize, QThread, QObject
)
from PySide6.QtGui import (
    QFont, QColor, QPalette, QAction, QTextCursor, QIcon, QPixmap
)

__version__ = "0.19.0"

logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("liz-desktop")

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False
    logger.warning("websocket-client not installed — running in offline demo mode")


# ============================================================
# Chat Service — WebSocket client for the backend
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

        if self._running:
            logger.info("Already connected or connecting")
            return

        self.connection_state_changed.emit("connecting")
        self.status_changed.emit("Conectando...")

        thread = threading.Thread(target=self._connect_thread, daemon=True)
        thread.start()

    def _connect_thread(self):
        try:
            import websocket as ws_mod
            self.ws = ws_mod.WebSocket()
            self.ws.settimeout(10)
            self.ws.connect(self.url)
            self._running = True
            self._session_id = os.urandom(16).hex()
            self.connection_state_changed.emit("connected")
            self.status_changed.emit("Conectado")
            logger.info("Connected to %s", self.url)
            self._receive_loop()
        except Exception as e:
            logger.error("Connection failed: %s", e)
            self.connection_state_changed.emit("failed")
            self.status_changed.emit(f"Error: {str(e)[:50]}")

    def _receive_loop(self):
        import websocket as ws_mod
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
                    self.message_received.emit(
                        "Error",
                        data.get("error", data.get("content", "Error desconocido"))
                    )
                elif msg_type == "ping":
                    try:
                        self.ws.send(json.dumps({"type": "pong"}))
                    except Exception:
                        pass
            except ws_mod.WebSocketTimeoutException:
                continue
            except Exception as e:
                if self._running:
                    logger.warning("Receive error: %s", e)
                    self.connection_state_changed.emit("failed")
                    self.status_changed.emit(f"Desconectado: {str(e)[:30]}")
                break

    def send_message(self, text: str):
        if not text.strip():
            return

        self.message_received.emit("Tu", text)

        if not self.ws or not HAS_WEBSOCKET:
            self._demo_response(text)
            return

        try:
            payload = {
                "message": text,
                "session_id": self._session_id,
                "mode": self._mode
            }
            self.ws.send(json.dumps(payload))
            logger.debug("Sent message (%d chars)", len(text))
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
                "1. Inicia el backend: python -m apps.backend.main\n"
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
            except Exception:
                pass
            self.ws = None
        self.connection_state_changed.emit("disconnected")
        self.status_changed.emit("Desconectado")
        logger.info("Disconnected")

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
        except Exception:
            pass


# ============================================================
# Main Chat Window
# ============================================================

class ChatWindow(QMainWindow):
    """Main window for Liz Coder Plus desktop assistant."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Liz Coder Plus — AI Desktop Assistant v{__version__}")
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
        logo_frame.setStyleSheet(
            "background: #6366f1; border-radius: 12px; border: none;"
        )
        logo_label = QLabel("L", logo_frame)
        logo_label.setStyleSheet(
            "color: white; font-size: 20px; font-weight: bold;"
        )
        logo_label.setAlignment(Qt.AlignCenter)
        logo_layout.addWidget(logo_frame)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        name_label = QLabel("Liz Coder Plus")
        name_label.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #1e1e2e;"
        )
        ver_label = QLabel(f"v{__version__} — Linux")
        ver_label.setStyleSheet("font-size: 11px; color: #888;")
        title_col.addWidget(name_label)
        title_col.addWidget(ver_label)
        logo_layout.addLayout(title_col)
        logo_layout.addStretch()
        sidebar_layout.addLayout(logo_layout)

        # New chat button
        self.new_chat_btn = QPushButton("  Nueva Conversacion")
        self.new_chat_btn.setCursor(Qt.PointingHandCursor)
        self.new_chat_btn.clicked.connect(self.on_new_chat)
        sidebar_layout.addWidget(self.new_chat_btn)

        # Conversations list
        self.conversations_list = QListWidget()
        self.conversations_list.currentRowChanged.connect(
            self.on_conversation_selected
        )
        sidebar_layout.addWidget(self.conversations_list)

        # Status indicator
        self.status_frame = QFrame()
        self.status_frame.setFixedHeight(36)
        self.status_frame.setStyleSheet(
            "background: #ef4444; border-radius: 6px; border: none;"
        )
        status_layout = QHBoxLayout(self.status_frame)
        status_layout.setContentsMargins(8, 0, 8, 0)
        self.status_label = QLabel("Desconectado")
        self.status_label.setStyleSheet(
            "color: white; font-size: 12px; font-weight: 500;"
        )
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        sidebar_layout.addWidget(self.status_frame)

        # Settings button
        self.settings_btn = QPushButton("  Configuracion")
        self.settings_btn.clicked.connect(self.show_settings)
        sidebar_layout.addWidget(self.settings_btn)

        sidebar_layout.addStretch()
        splitter.addWidget(sidebar)

        # ---- MAIN AREA ----
        main_area = QWidget()
        main_area_layout = QVBoxLayout(main_area)
        main_area_layout.setContentsMargins(16, 16, 16, 16)
        main_area_layout.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.connect_btn = QPushButton("  Conectar")
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.clicked.connect(self.on_connect)
        toolbar.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("  Desconectar")
        self.disconnect_btn.setCursor(Qt.PointingHandCursor)
        self.disconnect_btn.clicked.connect(self.on_disconnect)
        toolbar.addWidget(self.disconnect_btn)

        # Mode buttons
        self.mode_confirm_btn = QPushButton("Confirmar")
        self.mode_confirm_btn.setCheckable(True)
        self.mode_confirm_btn.setChecked(True)
        self.mode_confirm_btn.clicked.connect(
            lambda: self.set_mode("confirmation")
        )
        toolbar.addWidget(self.mode_confirm_btn)

        self.mode_auto_btn = QPushButton("Automatico")
        self.mode_auto_btn.setCheckable(True)
        self.mode_auto_btn.clicked.connect(
            lambda: self.set_mode("automatic")
        )
        toolbar.addWidget(self.mode_auto_btn)

        # Model selector
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(200)
        self.model_combo.addItem("Auto (por defecto)", "auto")
        toolbar.addWidget(self.model_combo)

        toolbar.addStretch()

        clear_btn = QPushButton("  Limpiar")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self.on_clear)
        toolbar.addWidget(clear_btn)

        main_area_layout.addLayout(toolbar)

        # Messages area (QTextBrowser instead of QTextEdit)
        self.messages_area = QTextBrowser()
        self.messages_area.setOpenExternalLinks(False)
        self.messages_area.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        main_area_layout.addWidget(self.messages_area)

        # Input area
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Escribe un mensaje a Liz...")
        self.input_field.returnPressed.connect(self.on_send)
        input_layout.addWidget(self.input_field)

        self.send_btn = QPushButton("  Enviar")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self.on_send)
        input_layout.addWidget(self.send_btn)

        main_area_layout.addLayout(input_layout)
        splitter.addWidget(main_area)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # System tray
        self._setup_tray()

    def _setup_tray(self):
        menu = QMenu()
        show_action = menu.addAction("Mostrar")
        show_action.triggered.connect(self.show)
        quit_action = menu.addAction("Salir")
        quit_action.triggered.connect(self._force_quit)

        self.tray = QSystemTrayIcon()
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        try:
            self.tray.setToolTip("Liz Coder Plus — AI Desktop Assistant")
            self.tray.show()
        except Exception:
            pass  # Tray not available in all environments

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.activateWindow()

    def _force_quit(self):
        self.chat.disconnect()
        QApplication.quit()

    def _setup_styles(self):
        # Primary button style (indigo)
        primary_btn = """
            QPushButton {
                background: #6366f1;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
                color: white;
            }
            QPushButton:hover { background: #4f46e5; }
            QPushButton:pressed { background: #4338ca; }
        """
        # Secondary button style
        secondary_btn = """
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
            QPushButton:checked {
                background: #6366f1; color: white;
                border: 1px solid #6366f1;
            }
        """

        self.connect_btn.setStyleSheet(primary_btn)
        self.send_btn.setStyleSheet(primary_btn)
        self.new_chat_btn.setStyleSheet(primary_btn)

        self.disconnect_btn.setStyleSheet(secondary_btn)
        self.mode_confirm_btn.setStyleSheet(secondary_btn)
        self.mode_auto_btn.setStyleSheet(secondary_btn)
        self.settings_btn.setStyleSheet(secondary_btn)

        # Messages area
        self.messages_area.setStyleSheet("""
            QTextBrowser {
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
            QListWidget::item {
                padding: 8px;
                border-radius: 6px;
            }
            QListWidget::item:selected {
                background: #e0e7ff;
                color: #4338ca;
            }
        """)

        # Main window background
        self.setStyleSheet("""
            QMainWindow { background: white; }
            QSplitter::handle { background: #e4e4e7; width: 1px; }
        """)

    def _add_demo_conversation(self):
        self._conversations.append({
            "title": "Bienvenida",
            "preview": "Hola Liz, como estas?",
            "time": datetime.datetime.now().strftime("%H:%M")
        })
        self._refresh_conversations()

    def _refresh_conversations(self):
        self.conversations_list.clear()
        for conv in self._conversations:
            item = QListWidgetItem(
                f"{conv['title']}\n{conv['preview']} — {conv['time']}"
            )
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
            f'<span style="color: {color}; font-weight: 600; font-size: 11px;">'
            f'{prefix}</span>'
            f'<span style="color: #888; font-size: 11px;"> — {timestamp}</span>'
            f'<br><span style="color: #1e1e2e; font-size: 14px; '
            f'white-space: pre-wrap;">'
            f'{self._escape_html(content)}</span>'
            f'</div><hr style="border: none; '
            f'border-top: 1px solid #f0f0f0;">'
        )

        scrollbar = self.messages_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _escape_html(self, text: str) -> str:
        return html_mod.escape(text).replace("\n", "<br>")

    @Slot(str)
    def on_status_changed(self, text: str):
        self.status_label.setText(text)

        if "Conectado" in text:
            self.status_frame.setStyleSheet(
                "background: #22c55e; border-radius: 6px; border: none;"
            )
        elif "Conectando" in text:
            self.status_frame.setStyleSheet(
                "background: #f97316; border-radius: 6px; border: none;"
            )
        elif "Error" in text or "Demo" in text:
            self.status_frame.setStyleSheet(
                "background: #eab308; border-radius: 6px; border: none;"
            )
        else:
            self.status_frame.setStyleSheet(
                "background: #ef4444; border-radius: 6px; border: none;"
            )

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
            "time": datetime.datetime.now().strftime("%H:%M")
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
            self,
            "Configuracion",
            f"Liz Coder Plus v{__version__} — Linux\n\n"
            "Backend URL: ws://localhost:8000/ws/chat\n"
            "Para cambiar la configuracion, edita el archivo\n"
            "~/.config/liz-coder-plus/config.json"
        )

    def closeEvent(self, event):
        self.chat.disconnect()
        event.accept()


# ============================================================
# Entry Point
# ============================================================

def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("Liz Coder Plus")
    app.setApplicationVersion(__version__)

    font = QFont("Noto Sans", 10)
    app.setFont(font)

    logger.info("Liz Coder Plus v%s starting...", __version__)
    window = ChatWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
