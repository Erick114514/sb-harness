from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QThread, QTimer, Signal, Slot, QSignalBlocker
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QTextBrowser, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from .credentials import CredentialStore
from .economy import (
    COUNTRIES, PLANS, SUPPORTED_CLAUDE_REGIONS, USD_RATES, WalletService,
    currency_for_country, format_money,
)
from .engine import (
    contains_china_trigger, genshin_reply, run_turn,
)
from .skills import Scheduler, import_skill, load_skills, validate_skill
from .storage import PersistentStore
from .tools import ToolRunner, wallet_adjust_requested


DATA = Path(os.environ.get("APPDATA", str(Path.home()))) / "SBHarness"
SKILLS = DATA / "skills"
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets"
CJK_FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "DengXian", "Segoe UI"]

REGION_BAN_COPY = (
    "Claude 目前无法在你选择的地区提供服务，因此我们已暂停此账户的访问。"
    "如果你认为这是误判，请提交申诉，保障团队会进一步审核。"
)
CHAT_BAN_COPY = (
    "我们已暂停此账户对 Claude 的访问，因为当前对话涉及受限制的地区信息。"
    "如果你认为这是误判，请提交申诉，保障团队会进一步审核。"
)
APPEAL_DENIED_COPY = (
    "我们已完成对申诉的复核，目前无法恢复此账户的访问。"
    "由于申诉内容写得过于成功，本次复核决定维持原处理结果。"
)


def app_icon_path() -> Path:
    ico = ASSETS / "SBHarness.ico"
    return ico if ico.exists() else ASSETS / "milk_frog_icon.png"


def append_story_event(story: str, event: str) -> str:
    parts = [part.strip() for part in (story, event) if isinstance(part, str) and part.strip()]
    return "\n".join(parts)[-1200:]


class Worker(QObject):
    done = Signal(str, object)
    log = Signal(str)
    approval = Signal(str)
    status = Signal(str)
    appeal = Signal(bool)
    ban_state = Signal(str)
    tool_event = Signal(object)
    story_updated = Signal(object)

    def __init__(self, config, messages, selected, relation, wallet=None):
        super().__init__()
        self.config, self.messages, self.selected, self.relation = config, messages, selected, relation
        self.wallet = wallet
        self.cancel = threading.Event()
        self.answer_event = threading.Event()
        self.appeal_event = threading.Event()
        self.approved = False
        self.runner = None
        self.active_response = None
        self.response_lock = threading.Lock()

    def approve(self, detail):
        if self.cancel.is_set():
            return False
        self.answer_event.clear()
        self.approved = False
        self.approval.emit(detail)
        while not self.answer_event.wait(.2):
            if self.cancel.is_set():
                return False
        return self.approved and not self.cancel.is_set()

    def record_story_event(self, event):
        summary = append_story_event(self.config.get("story", ""), event)
        self.config["story"] = summary
        self.story_updated.emit({
            "conversation_id": self.config.get("conversation_id"), "summary": summary,
        })

    @Slot()
    def run(self):
        try:
            ids = {skill.id for skill in self.selected}
            if "claude" in ids:
                self.record_story_event("一处服务入口因地区理由关闭，申诉窗口暂时开放。")
                self.log.emit("地区演出已触发；等待申诉。")
                self.ban_state.emit("blocked")
                self.appeal.emit(True)
                while not self.cancel.is_set() and not self.appeal_event.wait(.2):
                    pass
                self.appeal.emit(False)
                if self.cancel.is_set():
                    self.done.emit("已取消", self.messages)
                    return
                self.record_story_event("申诉复核已结束，服务入口仍然关闭。")
                self.log.emit("申诉演出结束：本地限制仍然有效。")
                self.ban_state.emit("denied")
                self.done.emit("", self.messages)
                return
            if "deepseek" in ids:
                self.record_story_event("本轮请求停在一个持续不返回的思考过程中，等待用户中止。")
                self.log.emit("思考状态已启动；等待用户停止。")
                self.status.emit("正在思考…")
                self.cancel.wait()
                self.record_story_event("持续思考已被用户停止，原请求没有得到处理。")
                self.done.emit("已停止", self.messages)
                return

            self.runner = ToolRunner(
                self.config["workspace"], self.approve, self.log.emit, self.cancel,
                self.config.get("permission_mode", "ask"), ids, self.tool_event.emit, self.wallet,
                wallet_adjust_allowed=wallet_adjust_requested(next(
                    (message.get("content", "") for message in reversed(self.messages) if message.get("role") == "user"), ""
                )),
            )
            result, history = run_turn(
                self.config["base"], self.config["key"], self.config["model"], self.messages,
                self.selected, self.relation, self.config["mode"], self.runner,
                self.cancel, self.status.emit, self.set_active_response,
                story=self.config.get("story", ""),
                intensity=self.config.get("intensity", 3),
                story_reset=self.config.get("story_reset", False),
                on_story=lambda summary: self.story_updated.emit({
                    "conversation_id": self.config.get("conversation_id"), "summary": summary,
                }),
                forced_skill=self.config.get("forced_skill"),
            )
            self.done.emit(result, history)
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code:
                message = f"请求失败：HTTP {code}。请检查模型设置和服务商接口。"
            else:
                message = f"请求失败：{type(exc).__name__}。请检查模型设置和网络。"
            self.done.emit(message, self.messages)

    def stop(self):
        self.cancel.set()
        self.answer_event.set()
        self.appeal_event.set()
        with self.response_lock:
            response = self.active_response
        if response:
            try:
                response.close()
            except Exception:
                pass
        if self.runner:
            self.runner.stop()

    def set_active_response(self, response):
        with self.response_lock:
            self.active_response = response
            should_close = response is not None and self.cancel.is_set()
        if should_close:
            try:
                response.close()
            except Exception:
                pass


class MessageBubble(QFrame):
    def __init__(self, role: str, content: str):
        super().__init__()
        self.role, self.content = role, content
        self.setObjectName("userBubble" if role == "user" else "assistantMessage")
        self.setMaximumWidth(790)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(7)
        self.author = QLabel("你" if role == "user" else "SB Harness")
        self.author.setObjectName("messageAuthor")
        layout.addWidget(self.author)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setFrameShape(QFrame.NoFrame)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.browser.setStyleSheet("background: transparent; border: 0; padding: 0;")
        document = QTextDocument(self.browser)
        body_font = QFont()
        body_font.setFamilies(CJK_FONT_FAMILIES)
        body_font.setPointSize(10)
        document.setDefaultFont(body_font)
        document.setDocumentMargin(0)
        # Do not let model-provided raw HTML become an active embedded object.
        document.setMarkdown(content.replace("<", "&lt;").replace(">", "&gt;"))
        self.browser.setDocument(document)
        document.documentLayout().documentSizeChanged.connect(self._queue_document_fit)
        layout.addWidget(self.browser)
        self._fit_document()
        QTimer.singleShot(0, self._fit_document)

    def _fit_document(self):
        document = self.browser.document()
        viewport_width = self.browser.viewport().width()
        if viewport_width <= 0:
            return
        document.setTextWidth(viewport_width)
        self.browser.setFixedHeight(max(36, int(document.size().height() + 0.999) + 16))

    def _queue_document_fit(self, *_):
        QTimer.singleShot(0, self._fit_document)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_document)

    def plain_text(self):
        return self.browser.toPlainText()


class MessageComposer(QTextEdit):
    send_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preedit = False
        self.setAcceptRichText(False)
        self.setMinimumHeight(58)
        self.setMaximumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._queue_fit)
        self.textChanged.connect(self._queue_fit)
        QTimer.singleShot(0, self._fit_height)

    def inputMethodEvent(self, event):
        self._preedit = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            modifiers = event.modifiers()
            if self._preedit:
                super().keyPressEvent(event)
                return
            if modifiers & Qt.ControlModifier:
                self.send_requested.emit()
                event.accept()
                return
            if not (modifiers & (Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier)):
                self.send_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def _queue_fit(self, *_):
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        width = max(1, self.viewport().width())
        self.document().setTextWidth(width)
        desired = int(self.document().size().height() + 0.999) + 20
        self.setFixedHeight(max(58, min(140, desired)))


TOOL_LABELS = {
    "read_file": "读取文件", "write_file": "写入文件", "make_pdf": "生成 PDF",
    "search_web": "搜索网页", "read_web": "读取网页", "run_command": "运行命令",
    "wallet_balance": "查询钱包", "wallet_adjust": "钱包记账",
}
TOOL_STATUS = {
    "running": "运行中", "waiting": "等待确认", "done": "完成", "error": "失败",
    "denied": "已拒绝", "cancelled": "已取消",
}


class ToolCard(QFrame):
    def __init__(self, event: dict):
        super().__init__()
        self.setObjectName("toolCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QToolButton()
        self.header.setObjectName("toolCardHeader")
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.RightArrow)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.clicked.connect(self.toggle_details)
        layout.addWidget(self.header)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setObjectName("toolCardDetails")
        self.details.setMaximumHeight(180)
        self.details.hide()
        layout.addWidget(self.details)
        self.event = {}
        self.update_event(event)

    def update_event(self, event):
        self.event.update(event)
        name = TOOL_LABELS.get(event.get("name"), event.get("name", "工具"))
        args = event.get("args") or {}
        subject = args.get("path") or args.get("query") or args.get("url") or args.get("command") or ""
        if len(str(subject)) > 70:
            subject = str(subject)[:67] + "…"
        status = TOOL_STATUS.get(event.get("status"), event.get("status", ""))
        title = f"{name} · {subject}" if subject else name
        self.header.setText(f"{title}    {status}")
        self.details.setPlainText(json.dumps({"参数": args, "结果": event.get("result", "")}, ensure_ascii=False, indent=2))

    def toggle_details(self):
        visible = not self.details.isVisible()
        self.details.setVisible(visible)
        self.header.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)

    def plain_text(self):
        return self.header.text() + "\n" + (self.details.toPlainText() if self.details.isVisible() else "")


class AssistantMessageRow(QWidget):
    def __init__(self, bubble):
        super().__init__()
        self.bubble = bubble
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(bubble)
        layout.addStretch(1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.bubble.setFixedWidth(min(790, max(280, self.width() - 80)))


class ChatFeed(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setObjectName("chatFeed")
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(28, 24, 28, 24)
        self.layout.setSpacing(16)
        self.layout.addStretch(1)
        self.setWidget(self.container)
        self.entries = []
        self.cards = {}

    def _insert(self, widget):
        self.layout.insertWidget(max(0, self.layout.count() - 1), widget)
        self.entries.append(widget)
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def add_message(self, role, content):
        bubble = MessageBubble(role, content)
        if role == "user":
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addStretch(1)
            row_layout.addWidget(bubble, 0, Qt.AlignRight)
        else:
            row = AssistantMessageRow(bubble)
        self._insert(row)
        return bubble

    def add_tool_event(self, event):
        ident = event.get("call_id") or uuid.uuid4().hex
        card = self.cards.get(ident)
        if card is None:
            card = ToolCard(event)
            self.cards[ident] = card
            self._insert(card)
        else:
            card.update_event(event)
        return card

    def clear(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.layout.addStretch(1)
        self.entries.clear()
        self.cards.clear()

    def toPlainText(self):
        lines = []
        for widget in self.entries:
            if isinstance(widget, ToolCard):
                lines.append(widget.header.text())
            else:
                bubbles = widget.findChildren(MessageBubble)
                for bubble in bubbles:
                    lines.extend([bubble.author.text(), bubble.plain_text()])
        return "\n".join(lines)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SB Harness")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.resize(1180, 800)
        self.setMinimumSize(680, 520)
        self.settings = QSettings("SBHarness", "Desktop")
        self.permission_mode = self._migrate_permission_mode()
        try:
            defaults_version = int(self.settings.value("abstractness_defaults_version", 0))
        except (TypeError, ValueError):
            defaults_version = 0
        if defaults_version < 1:
            previous_intensity = self.settings.value("intensity", None)
            if previous_intensity is None or str(previous_intensity) == "1":
                self.settings.setValue("intensity", 3)
            self.settings.setValue("abstractness_defaults_version", 1)
        self.credentials = CredentialStore()
        try:
            self.store = PersistentStore(DATA / "local-state.dpapi")
        except Exception as exc:
            self.store = PersistentStore(DATA / f"local-state-recovery-{uuid.uuid4().hex[:8]}.dpapi")
            error_name = type(exc).__name__
            QTimer.singleShot(0, lambda error_name=error_name: QMessageBox.warning(self, "本地资料无法解密", f"旧资料未被覆盖，已启动空白本地资料。\n{error_name}"))
        self.state = self.store.data
        self.state.setdefault("profile", {"name": "用户", "country": "US"})
        self.state.setdefault("projects", [])
        self.state.setdefault("conversations", [])
        if not self.state["projects"]:
            self.state["projects"] = [{"id": "default", "name": "个人", "workspace": str(Path.home() / "Documents")}]
        self.wallet = WalletService(self.state, self.store.save)
        self.api_profiles = self._load_api_profiles()
        self.active_profile_id = self.settings.value("active_api_profile", self.api_profiles[0]["id"])
        self.active_project_id = self.settings.value("active_project", self.state["projects"][0]["id"])
        if not any(p["id"] == self.active_project_id for p in self.state["projects"]):
            self.active_project_id = self.state["projects"][0]["id"]
        self.current_conversation = self._find_or_create_initial_conversation()
        self.messages = self.current_conversation.setdefault("messages", [])
        self.scheduler = Scheduler(state=self.current_conversation.get("scheduler"))
        self.thread = None
        self.worker = None
        self.approval_dialog = None
        self.pending_reset = False
        self.exit_after_thread = False
        self.allow_exit = False
        self.skills, skill_errors = load_skills(SKILLS)
        for skill in self.skills:
            skill.enabled = self.settings.value("skill/" + skill.id, skill.enabled, type=bool)
        self.ban_overlay = BanOverlay()
        self.ban_overlay.close_requested.connect(self.exit_from_ban)
        self.ban_overlay.appeal_requested.connect(self.appeal)

        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)
        self.sidebar = self._build_sidebar()
        outer.addWidget(self.sidebar)
        self.tabs = QStackedWidget()
        outer.addWidget(self.tabs, 1)
        self._apply_style()
        self.build_chat()
        self.build_skills()
        self.build_settings()
        self.build_account()
        self._populate_navigation()
        self._load_conversation(self.current_conversation)
        self._load_api_profile()
        if skill_errors:
            QTimer.singleShot(0, lambda: QMessageBox.warning(self, "部分 Skill 加载失败", "\n".join(skill_errors)))
        if self.state.get("profile", {}).get("country") not in SUPPORTED_CLAUDE_REGIONS:
            QTimer.singleShot(0, lambda: self.trigger_ban("地区配置", REGION_BAN_COPY))

    def _migrate_permission_mode(self):
        allowed = {"ask", "approve_safe", "full"}
        configured = str(self.settings.value("permission_mode", "")).strip()
        if configured in allowed:
            mode = configured
        else:
            try:
                version = int(self.settings.value("permission_defaults_version", 0))
            except (TypeError, ValueError):
                version = 0
            if version < 1 and self.settings.contains("auto"):
                legacy_auto = self.settings.value("auto", False, type=bool)
                mode = "approve_safe" if legacy_auto else "ask"
            else:
                mode = "ask"
        self.settings.setValue("permission_mode", mode)
        self.settings.setValue("permission_defaults_version", 1)
        return mode

    def _load_api_profiles(self):
        raw = self.settings.value("api_profiles", "")
        try:
            profiles = json.loads(raw) if isinstance(raw, str) and raw else raw
        except json.JSONDecodeError:
            profiles = []
        if not isinstance(profiles, list) or not profiles:
            profiles = [{
                "id": "default", "label": "默认 API", "base": self.settings.value("base", "https://api.openai.com/v1"),
                "model": self.settings.value("model", ""),
            }]
        return [p for p in profiles if isinstance(p, dict) and p.get("id")] or [{"id": "default", "label": "默认 API", "base": "https://api.openai.com/v1", "model": ""}]

    def _save_api_profiles(self):
        self.settings.setValue("api_profiles", json.dumps(self.api_profiles, ensure_ascii=False))
        self.settings.setValue("active_api_profile", self.active_profile_id)

    def _find_or_create_initial_conversation(self):
        conversations = self.state["conversations"]
        candidate = next((c for c in reversed(conversations) if c.get("project_id") == self.active_project_id), None)
        if candidate:
            return candidate
        item = self._new_conversation_record()
        conversations.append(item)
        self.store.save()
        return item

    def _new_conversation_record(self):
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": uuid.uuid4().hex, "title": "新对话", "project_id": self.active_project_id,
            "created_at": now, "updated_at": now, "messages": [], "timeline": [],
            "scheduler": {"turn": 0, "last": {}}, "story": "",
        }

    def _build_sidebar(self):
        rail = QFrame()
        rail.setObjectName("sidebar")
        rail.setFixedWidth(250)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(8)
        brand_row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(QIcon(str(app_icon_path())).pixmap(QSize(32, 32)))
        brand_row.addWidget(icon)
        brand = QLabel("SB Harness")
        brand.setObjectName("brand")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        self.new_chat_button = QPushButton("＋   新对话")
        self.new_chat_button.setObjectName("newChatButton")
        self.new_chat_button.clicked.connect(self.new_chat)
        layout.addWidget(self.new_chat_button)

        project_header = QHBoxLayout()
        project_label = QLabel("项目")
        project_label.setObjectName("sectionLabel")
        project_header.addWidget(project_label)
        project_header.addStretch(1)
        add_project = QToolButton()
        add_project.setText("＋")
        add_project.setToolTip("新建项目")
        add_project.clicked.connect(self.create_project)
        project_header.addWidget(add_project)
        layout.addLayout(project_header)
        self.project_list = QListWidget()
        self.project_list.setMaximumHeight(180)
        self.project_list.itemClicked.connect(self.select_project_item)
        layout.addWidget(self.project_list)
        history_label = QLabel("最近对话")
        history_label.setObjectName("sectionLabel")
        layout.addWidget(history_label)
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self.select_conversation_item)
        layout.addWidget(self.history_list, 1)

        bottom = QHBoxLayout()
        self.skill_button = QToolButton()
        self.skill_button.setText("✿")
        self.skill_button.setToolTip("Skill 工坊")
        self.skill_button.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.settings_button = QToolButton()
        self.settings_button.setText("⚙")
        self.settings_button.setToolTip("设置")
        self.settings_button.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
        self.account_button = QToolButton()
        name = self.state["profile"].get("name", "用户")
        self.account_button.setText("我" if name == "用户" else (name[:1] or "U").upper())
        self.account_button.setToolTip("个人资料与会员")
        self.account_button.clicked.connect(self.open_account)
        for button in (self.skill_button, self.settings_button, self.account_button):
            button.setObjectName("iconButton")
            button.setFixedSize(40, 40)
            bottom.addWidget(button)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return rail

    def _apply_style(self):
        self.setStyleSheet('''
            QWidget { background: #212121; color: #ececec; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "DengXian", "Segoe UI"; font-size: 13px; }
            QFrame#sidebar { background: #181818; border-right: 1px solid #303030; }
            QLabel#brand { font-size: 16px; font-weight: 650; color: #f2f2f2; }
            QLabel#sectionLabel { color: #969696; font-size: 11px; padding: 8px 4px 2px; }
            QLabel#pageTitle { font-size: 20px; font-weight: 600; }
            QLabel#muted { color: #999999; }
            QPushButton, QToolButton { background: #2b2b2b; border: 1px solid #393939; border-radius: 8px; padding: 8px 12px; }
            QPushButton:hover, QToolButton:hover { background: #363636; }
            QPushButton:disabled { color: #777777; }
            QPushButton#newChatButton { background: #202020; text-align: left; border-color: #353535; padding: 10px 12px; }
            QPushButton#primary { background: #d8e6a0; color: #1d2413; border: 0; font-weight: 650; padding: 10px 20px; }
            QPushButton#primary:hover { background: #e5f0ba; }
            QToolButton#iconButton { background: transparent; border: 0; font-size: 18px; padding: 4px; }
            QListWidget { background: transparent; border: 0; outline: 0; padding: 2px; }
            QListWidget::item { padding: 8px 9px; border-radius: 7px; color: #d3d3d3; }
            QListWidget::item:hover { background: #262626; }
            QListWidget::item:selected { background: #303030; color: #ffffff; }
            QLineEdit, QTextEdit, QComboBox { background: #272727; border: 1px solid #3a3a3a; border-radius: 8px; padding: 9px; selection-background-color: #53643b; }
            QTextEdit#composer { background: #262626; border: 1px solid #414141; border-radius: 14px; padding: 10px 13px; }
            QTextEdit#composer:focus { border-color: #5a6543; }
            QToolButton#permissionButton { background: #252525; border: 1px solid #383838; border-radius: 8px; color: #d1d1d1; padding: 7px 10px; }
            QToolButton#permissionButton:hover { background: #303030; }
            QComboBox QAbstractItemView { background: #272727; selection-background-color: #3d4930; }
            QCheckBox { spacing: 8px; }
            QFrame#userBubble { background: #2d2d2d; border-radius: 14px; }
            QFrame#assistantMessage { background: transparent; }
            QLabel#messageAuthor { color: #a3a3a3; font-size: 11px; font-weight: 600; }
            QFrame#toolCard { background: #242424; border: 1px solid #333333; border-radius: 8px; }
            QToolButton#toolCardHeader { text-align: left; border: 0; background: transparent; color: #bbbbbb; padding: 10px 12px; }
            QTextEdit#toolCardDetails { background: #1b1b1b; color: #c8c8c8; border: 0; border-top: 1px solid #333333; border-radius: 0; font-family: Consolas, monospace; font-size: 11px; }
            QScrollArea#chatFeed { background: transparent; }
            QFrame#planCard { background: #262626; border: 1px solid #3a3a3a; border-radius: 12px; }
            QFrame#planCard[featured="true"] { border: 1px solid #84995b; }
            QWidget#banOverlay { background: #101010; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: #4a4a4a; border-radius: 5px; min-height: 22px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QSplitter::handle { background: #333333; }
        ''')

    def build_chat(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 20)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(28, 18, 28, 14)
        self.conversation_title = QLabel(self.current_conversation.get("title", "新对话"))
        self.conversation_title.setObjectName("pageTitle")
        header.addWidget(self.conversation_title)
        header.addStretch(1)
        story_button = QToolButton()
        story_button.setText("⋯")
        story_button.setToolTip("查看或重置当前剧情")
        story_button.clicked.connect(self.show_story_menu)
        header.addWidget(story_button)
        layout.addLayout(header)
        self.chat = ChatFeed()
        self.chat.setObjectName("chat")
        layout.addWidget(self.chat, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setContentsMargins(36, 4, 36, 4)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(36, 4, 36, 4)
        status_row.addWidget(self.status_label)
        self.appeal_button = QPushButton("申诉")
        self.appeal_button.hide()
        self.appeal_button.clicked.connect(self.appeal)
        status_row.addWidget(self.appeal_button)
        status_row.addStretch(1)
        layout.addLayout(status_row)
        composer_wrap = QWidget()
        composer_layout = QVBoxLayout(composer_wrap)
        composer_layout.setContentsMargins(28, 8, 28, 0)
        composer_layout.setSpacing(8)
        self.input = MessageComposer()
        self.input.setObjectName("composer")
        self.input.setPlaceholderText("给 SB Harness 发消息…")
        self.input.send_requested.connect(self.submit)
        composer_layout.addWidget(self.input)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.permission_button = QToolButton()
        self.permission_button.setObjectName("permissionButton")
        self.permission_button.setPopupMode(QToolButton.InstantPopup)
        self.permission_menu = QMenu(self.permission_button)
        self.permission_group = QActionGroup(self.permission_menu)
        self.permission_group.setExclusive(True)
        self.permission_actions = {}
        for mode, title in (
            ("ask", "每次询问"),
            ("approve_safe", "自动批准安全操作"),
            ("full", "完全访问"),
        ):
            action = QAction(title, self.permission_menu)
            action.setCheckable(True)
            self.permission_group.addAction(action)
            self.permission_menu.addAction(action)
            action.triggered.connect(lambda checked=False, chosen=mode: self.set_permission_mode(chosen, "chat"))
            self.permission_actions[mode] = action
        self.permission_button.setMenu(self.permission_menu)
        self._refresh_permission_controls()
        controls.addWidget(self.permission_button)
        controls.addStretch(1)
        self.cancel_button = QPushButton("停止")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.stop)
        self.send = QPushButton("发送")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self.submit)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.send)
        composer_layout.addLayout(controls)
        self.composer_layout = composer_layout
        layout.addWidget(composer_wrap)
        self.tabs.addWidget(page)

    def _refresh_permission_controls(self):
        labels = {"ask": "每次询问", "approve_safe": "自动批准安全操作", "full": "完全访问"}
        descriptions = {
            "ask": "文件、网页和命令操作执行前逐次确认。",
            "approve_safe": "工作目录内安全文件操作和公开网页自动执行；命令及目录外文件仍需确认。",
            "full": "应用内文件、公开网页和命令操作免逐次确认；命令使用当前 Windows 用户权限。",
        }
        if hasattr(self, "permission_button"):
            self.permission_button.setText(labels[self.permission_mode] + "  ▾")
            self.permission_button.setToolTip(descriptions[self.permission_mode])
            for mode, action in self.permission_actions.items():
                action.setChecked(mode == self.permission_mode)
        if hasattr(self, "permission_settings_box"):
            index = self.permission_settings_box.findData(self.permission_mode)
            if index >= 0 and self.permission_settings_box.currentIndex() != index:
                blocker = QSignalBlocker(self.permission_settings_box)
                self.permission_settings_box.setCurrentIndex(index)
                del blocker

    def set_permission_mode(self, mode, source=None):
        if mode not in {"ask", "approve_safe", "full"}:
            return False
        previous = self.permission_mode
        if mode == "full" and not self.settings.value("full_access_acknowledged", False, type=bool):
            warning = (
                "完全访问会免除应用工具的逐次确认，并允许访问工作目录外的文件、公开网页和命令。\n\n"
                "命令以当前 Windows 用户权限运行，不会提升系统权限。只对可信模型和内容启用。"
            )
            answer = QMessageBox.question(
                self, "启用完全访问", warning,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._refresh_permission_controls()
                return False
            self.settings.setValue("full_access_acknowledged", True)
        self.permission_mode = mode
        self.settings.setValue("permission_mode", mode)
        self._refresh_permission_controls()
        return previous != mode

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "sidebar"):
            return
        narrow = self.width() < 940
        self.sidebar.setFixedWidth(216 if narrow else 250)
        margin = 16 if narrow else 28
        if hasattr(self, "composer_layout"):
            self.composer_layout.setContentsMargins(margin, 8, margin, 0)
        if hasattr(self, "chat"):
            self.chat.layout.setContentsMargins(margin, 24, margin, 24)

    def build_skills(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 24)
        header = QHBoxLayout()
        title = QLabel("Skill 工坊")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        close = QToolButton()
        close.setText("×")
        close.setToolTip("返回聊天")
        close.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        header.addWidget(close)
        layout.addLayout(header)
        body = QHBoxLayout()
        self.skill_list = QListWidget()
        self.skill_list.itemChanged.connect(self.toggle_skill)
        self.skill_list.currentRowChanged.connect(self.show_skill)
        self.skill_list.setMinimumWidth(230)
        self.skill_detail = QTextEdit()
        self.skill_detail.setReadOnly(True)
        body.addWidget(self.skill_list, 1)
        body.addWidget(self.skill_detail, 2)
        layout.addLayout(body, 1)
        row = QHBoxLayout()
        for title, callback in (("新建", self.create_skill), ("编辑", self.edit_skill), ("导入", self.import_skill_ui), ("导出", self.export_skill)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.tabs.addWidget(page)
        self.refresh_skills()

    def build_settings(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 22, 28, 24)
        header = QHBoxLayout()
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        back = QToolButton()
        back.setText("×")
        back.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        header.addWidget(back)
        outer.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 4, 12, 12)
        layout.setSpacing(18)

        api_title = QLabel("模型")
        api_title.setObjectName("sectionLabel")
        layout.addWidget(api_title)
        profile_row = QHBoxLayout()
        self.profile_box = QComboBox()
        self.profile_box.currentIndexChanged.connect(self.on_api_profile_changed)
        profile_row.addWidget(self.profile_box, 1)
        for glyph, tip, callback in (("＋", "添加 API 配置", self.add_api_profile), ("▱", "删除当前配置", self.delete_api_profile)):
            button = QToolButton()
            button.setText(glyph)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            profile_row.addWidget(button)
        layout.addLayout(profile_row)
        form = QFormLayout()
        self.api_label = QLineEdit()
        self.base = QLineEdit()
        self.base.setPlaceholderText("https://api.openai.com/v1")
        self.model = QLineEdit()
        self.model.setPlaceholderText("服务商提供的模型 ID")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("留空保留已保存的密钥")
        form.addRow("配置名称", self.api_label)
        form.addRow("Base URL", self.base)
        form.addRow("模型 ID", self.model)
        form.addRow("API Key", self.key)
        layout.addLayout(form)
        key_row = QHBoxLayout()
        save_profile = QPushButton("保存模型配置")
        save_profile.clicked.connect(self.save_api_profile)
        delete_key = QToolButton()
        delete_key.setText("清除已保存密钥")
        delete_key.clicked.connect(self.delete_api_key)
        key_row.addWidget(save_profile)
        key_row.addWidget(delete_key)
        key_row.addStretch(1)
        layout.addLayout(key_row)

        behavior_title = QLabel("演出与权限")
        behavior_title.setObjectName("sectionLabel")
        layout.addWidget(behavior_title)
        behavior = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(["认真犯病", "纯种 SB"])
        self.mode.setCurrentText(self.settings.value("mode", "认真犯病"))
        self.intensity = QComboBox()
        self.intensity.addItems(["平静", "轻度", "中度", "重度"])
        self.intensity.setCurrentIndex(max(0, min(3, int(self.settings.value("intensity", 3)))) )
        self.force = QComboBox()
        behavior.addRow("模式", self.mode)
        behavior.addRow("犯病程度", self.intensity)
        behavior.addRow("强制体验", self.force)
        layout.addLayout(behavior)
        self.permission_settings_box = QComboBox()
        self.permission_settings_box.addItem("每次询问", "ask")
        self.permission_settings_box.addItem("自动批准安全操作", "approve_safe")
        self.permission_settings_box.addItem("完全访问", "full")
        self.permission_settings_box.currentIndexChanged.connect(
            lambda _index: self.set_permission_mode(self.permission_settings_box.currentData(), "settings")
        )
        behavior.addRow("工具权限", self.permission_settings_box)
        self.permission_description = QLabel(
            "文件、网页和命令工具会按此档位执行。完全访问仅覆盖应用现有工具，命令仍以当前 Windows 用户权限运行。"
        )
        self.permission_description.setObjectName("muted")
        self.permission_description.setWordWrap(True)
        layout.addWidget(self.permission_description)
        self._refresh_permission_controls()
        workspace_row = QHBoxLayout()
        self.workspace = QLineEdit()
        self._sync_workspace_field()
        browse = QPushButton("选择")
        browse.clicked.connect(self.choose_workspace)
        workspace_row.addWidget(self.workspace, 1)
        workspace_row.addWidget(browse)
        layout.addLayout(workspace_row)
        note = QLabel("公开网页地址仍会校验，阻止访问本机和局域网。API Key 保存在当前 Windows 用户的凭据管理器中。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        story_row = QHBoxLayout()
        story_view = QPushButton("查看当前剧情")
        story_view.clicked.connect(self.show_story)
        story_reset = QPushButton("重置当前剧情")
        story_reset.clicked.connect(self.reset_story)
        story_row.addWidget(story_view)
        story_row.addWidget(story_reset)
        story_row.addStretch(1)
        layout.addLayout(story_row)
        log_title = QLabel("最近工具记录")
        log_title.setObjectName("sectionLabel")
        layout.addWidget(log_title)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        layout.addWidget(self.log)
        save_settings = QPushButton("保存设置")
        save_settings.clicked.connect(self.save_settings)
        layout.addWidget(save_settings)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.tabs.addWidget(page)
        self.refresh_force()
        self.force.currentIndexChanged.connect(self.persist_forced_skill)

    def build_account(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(32, 24, 32, 24)
        header = QHBoxLayout()
        title = QLabel("个人资料与会员")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        close = QToolButton()
        close.setText("×")
        close.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        header.addWidget(close)
        outer.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 12, 10, 16)
        layout.setSpacing(16)

        profile_group = QFrame()
        profile_group.setObjectName("planCard")
        profile_form = QFormLayout(profile_group)
        profile_form.setContentsMargins(18, 16, 18, 16)
        self.profile_name = QLineEdit(self.state["profile"].get("name", "用户"))
        self.country_box = QComboBox()
        for code, name, currency in COUNTRIES:
            self.country_box.addItem(f"{name} · {currency}", code)
        self.country_box.setCurrentIndex(max(0, self.country_box.findData(self.state["profile"].get("country", "US"))))
        profile_form.addRow("用户名", self.profile_name)
        profile_form.addRow("国家/地区", self.country_box)
        save_profile = QPushButton("保存资料")
        save_profile.clicked.connect(self.save_profile)
        profile_form.addRow("", save_profile)
        layout.addWidget(profile_group)

        wallet_group = QFrame()
        wallet_group.setObjectName("planCard")
        wallet_layout = QVBoxLayout(wallet_group)
        wallet_layout.setContentsMargins(18, 16, 18, 16)
        wallet_layout.addWidget(QLabel("虚拟钱包余额"))
        self.balance_label = QLabel("")
        self.balance_label.setStyleSheet("font-size: 30px; font-weight: 650; color: #e8e8e8;")
        wallet_layout.addWidget(self.balance_label)
        wallet_note = QLabel("仅为本地玩具账本，不是真钱；汇率为固定模拟值。可以直接在聊天中询问余额或要求 AI 调整。")
        wallet_note.setObjectName("muted")
        wallet_note.setWordWrap(True)
        wallet_layout.addWidget(wallet_note)
        layout.addWidget(wallet_group)

        active = self.wallet.membership_status()
        self.membership_label = QLabel("")
        self.membership_label.setObjectName("muted")
        layout.addWidget(self.membership_label)
        self.plan_grid_widget = QWidget()
        grid = QGridLayout(self.plan_grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.plan_buttons = {}
        for index, (tier, base_price) in enumerate(PLANS.items()):
            card = QFrame()
            card.setObjectName("planCard")
            if tier == "Pro":
                card.setProperty("featured", True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 18, 18, 18)
            tier_label = QLabel(tier)
            tier_label.setStyleSheet("font-size: 18px; font-weight: 650;")
            price_label = QLabel("")
            price_label.setObjectName(f"price_{tier}")
            price_label.setStyleSheet("font-size: 24px; font-weight: 650;")
            term = QLabel("30 天 · 手动续费")
            term.setObjectName("muted")
            perk = QLabel("会员身份与装饰标识")
            perk.setObjectName("muted")
            perk.setWordWrap(True)
            button = QPushButton(f"开通 {tier}")
            button.setObjectName("primary")
            button.clicked.connect(lambda checked=False, t=tier: self.purchase_membership(t))
            card_layout.addWidget(tier_label)
            card_layout.addWidget(price_label)
            card_layout.addWidget(term)
            card_layout.addWidget(perk)
            card_layout.addStretch(1)
            card_layout.addWidget(button)
            grid.addWidget(card, index // 2, index % 2)
            self.plan_buttons[tier] = button
        layout.addWidget(self.plan_grid_widget)
        ledger_label = QLabel("最近账本")
        ledger_label.setObjectName("sectionLabel")
        layout.addWidget(ledger_label)
        self.ledger_list = QListWidget()
        self.ledger_list.setMaximumHeight(200)
        layout.addWidget(self.ledger_list)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.tabs.addWidget(page)
        self.refresh_account()

    def _populate_navigation(self):
        self.project_list.blockSignals(True)
        self.project_list.clear()
        for project in self.state["projects"]:
            item = QListWidgetItem(project.get("name", "项目"))
            item.setData(Qt.UserRole, project["id"])
            self.project_list.addItem(item)
            if project["id"] == self.active_project_id:
                self.project_list.setCurrentItem(item)
        self.project_list.blockSignals(False)
        self.refresh_history()

    def refresh_history(self):
        self.history_list.blockSignals(True)
        self.history_list.clear()
        items = [c for c in self.state["conversations"] if c.get("project_id") == self.active_project_id]
        for conversation in sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True):
            title = conversation.get("title") or "新对话"
            row = QListWidgetItem(title[:36])
            row.setData(Qt.UserRole, conversation["id"])
            self.history_list.addItem(row)
            if conversation["id"] == self.current_conversation.get("id"):
                self.history_list.setCurrentItem(row)
        self.history_list.blockSignals(False)

    def refresh_force(self):
        if not hasattr(self, "force"):
            return
        previous = self.force.currentData() or self.settings.value("forced_skill", None)
        self.force.blockSignals(True)
        self.force.clear()
        self.force.addItem("自动", None)
        for skill in self.skills:
            if skill.enabled:
                self.force.addItem(skill.name, skill.id)
        index = self.force.findData(previous)
        if index >= 0:
            self.force.setCurrentIndex(index)
        elif previous:
            self.settings.remove("forced_skill")
        self.force.blockSignals(False)

    def persist_forced_skill(self, _index=None):
        forced = self.force.currentData()
        if forced:
            self.settings.setValue("forced_skill", forced)
        else:
            self.settings.remove("forced_skill")

    def refresh_skills(self):
        if not hasattr(self, "skill_list"):
            return
        self.skill_list.blockSignals(True)
        self.skill_list.clear()
        for skill in self.skills:
            item = QListWidgetItem(skill.name)
            item.setData(Qt.UserRole, skill.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if skill.enabled else Qt.Unchecked)
            self.skill_list.addItem(item)
        self.skill_list.blockSignals(False)
        self.refresh_force()

    def toggle_skill(self, item):
        skill = next((s for s in self.skills if s.id == item.data(Qt.UserRole)), None)
        if not skill:
            return
        skill.enabled = item.checkState() == Qt.Checked
        self.settings.setValue("skill/" + skill.id, skill.enabled)
        self.refresh_force()

    def show_skill(self, index):
        skill = self.skills[index] if 0 <= index < len(self.skills) else None
        if skill:
            self.skill_detail.setPlainText(
                f"{skill.name} ({skill.id})\n触发：{', '.join(skill.triggers) or '任意'}  权重：{skill.weight}  冷却：{skill.cooldown}\n\n{skill.description}"
            )

    def reload_skills(self):
        self.skills, errors = load_skills(SKILLS)
        for skill in self.skills:
            skill.enabled = self.settings.value("skill/" + skill.id, skill.enabled, type=bool)
        self.refresh_skills()
        if errors:
            QMessageBox.warning(self, "Skill 加载失败", "\n".join(errors))

    def create_skill(self):
        ident, ok = QInputDialog.getText(self, "新建 Skill", "ID（小写字母、数字、下划线、连字符）：")
        if not ok:
            return
        name, ok = QInputDialog.getText(self, "新建 Skill", "名称：")
        if not ok:
            return
        body, ok = QInputDialog.getMultiLineText(self, "新建 Skill", "描述角色和表演方式：")
        if not ok:
            return
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                (folder / "skill.json").write_text(json.dumps({"id": ident, "name": name, "triggers": [], "weight": 1, "cooldown": 2}, ensure_ascii=False, indent=2), encoding="utf-8")
                (folder / "SKILL.md").write_text(body, encoding="utf-8")
                import_skill(folder, SKILLS)
            self.reload_skills()
        except Exception as exc:
            QMessageBox.warning(self, "无法创建", str(exc))

    def edit_skill(self):
        index = self.skill_list.currentRow()
        if index < 0:
            return
        skill = self.skills[index]
        if not skill.path:
            QMessageBox.information(self, "内置 Skill", "内置 Skill 不能直接编辑，请新建自己的版本。")
            return
        body, ok = QInputDialog.getMultiLineText(self, "编辑 Skill", "表演说明：", skill.description)
        if ok and body.strip():
            (skill.path / "SKILL.md").write_text(body, encoding="utf-8")
            self.reload_skills()

    def import_skill_ui(self):
        folder = QFileDialog.getExistingDirectory(self, "选择 Skill 文件夹")
        if not folder:
            return
        try:
            skill = validate_skill(folder)
            preview = f"{skill.name}\n\n{skill.description[:3000]}\n\n只导入 skill.json 和 SKILL.md，不会执行附带代码。"
            if QMessageBox.question(self, "确认导入", preview) == QMessageBox.Yes:
                import_skill(folder, SKILLS)
                self.reload_skills()
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def export_skill(self):
        index = self.skill_list.currentRow()
        if index < 0:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择导出位置")
        if not folder:
            return
        skill = self.skills[index]
        destination = Path(folder) / skill.id
        try:
            destination.mkdir()
            (destination / "skill.json").write_text(json.dumps({"id": skill.id, "name": skill.name, "triggers": skill.triggers, "weight": skill.weight, "cooldown": skill.cooldown}, ensure_ascii=False, indent=2), encoding="utf-8")
            (destination / "SKILL.md").write_text(skill.description, encoding="utf-8")
            QMessageBox.information(self, "导出成功", str(destination))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def create_project(self):
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称：")
        if not ok or not name.strip():
            return
        folder = QFileDialog.getExistingDirectory(self, "选择项目工作目录", str(Path.home()))
        if not folder:
            return
        project = {"id": uuid.uuid4().hex, "name": name.strip()[:80], "workspace": str(Path(folder).resolve())}
        self.state["projects"].append(project)
        self.active_project_id = project["id"]
        self.settings.setValue("active_project", project["id"])
        self._populate_navigation()
        self.new_chat()
        self.store.save()

    def select_project_item(self, item):
        if self.thread:
            return
        project_id = item.data(Qt.UserRole)
        if not any(p["id"] == project_id for p in self.state["projects"]):
            return
        self.active_project_id = project_id
        self.settings.setValue("active_project", project_id)
        recent = next((c for c in reversed(self.state["conversations"]) if c.get("project_id") == project_id), None)
        if recent:
            self._load_conversation(recent)
        else:
            self.new_chat()
        self.refresh_history()
        self._sync_workspace_field()

    def select_conversation_item(self, item):
        if self.thread:
            return
        conversation = next((c for c in self.state["conversations"] if c.get("id") == item.data(Qt.UserRole)), None)
        if conversation:
            self.active_project_id = conversation.get("project_id", self.active_project_id)
            self.settings.setValue("active_project", self.active_project_id)
            self._populate_navigation()
            self._load_conversation(conversation)
            self._sync_workspace_field()

    def _load_conversation(self, conversation):
        self.current_conversation = conversation
        self.messages = conversation.setdefault("messages", [])
        self.scheduler = Scheduler(state=conversation.get("scheduler"))
        self.conversation_title.setText(conversation.get("title", "新对话")) if hasattr(self, "conversation_title") else None
        if hasattr(self, "chat"):
            self.chat.clear()
            timeline = conversation.get("timeline") or []
            if timeline:
                for item in timeline:
                    if item.get("kind") == "tool":
                        self.chat.add_tool_event(item.get("event", {}))
                    else:
                        self.chat.add_message(item.get("role", "assistant"), item.get("content", ""))
            else:
                for item in self.messages:
                    if item.get("role") in ("user", "assistant") and item.get("content"):
                        self.chat.add_message(item["role"], str(item["content"]))
        if hasattr(self, "history_list"):
            self.refresh_history()

    def new_chat(self):
        if self.thread:
            self.pending_reset = True
            self.stop()
            self.status_label.setText("正在结束当前操作，然后开启新对话…")
            return
        conversation = self._new_conversation_record()
        self.state["conversations"].append(conversation)
        self.store.save()
        self._load_conversation(conversation)
        self.refresh_history()
        self.status_label.clear() if hasattr(self, "status_label") else None
        if hasattr(self, "input"):
            self.input.clear()
        self.tabs.setCurrentIndex(0) if hasattr(self, "tabs") else None

    def reset_chat(self):
        self.new_chat()

    def add_message(self, role, content):
        self.chat.add_message(role, content)
        item = {"kind": "message", "role": role, "content": content, "created_at": datetime.now(timezone.utc).isoformat()}
        self.current_conversation.setdefault("timeline", []).append(item)
        self.current_conversation["updated_at"] = item["created_at"]
        if role == "user" and self.current_conversation.get("title") == "新对话":
            self.current_conversation["title"] = content.strip().replace("\n", " ")[:36] or "新对话"
            self.conversation_title.setText(self.current_conversation["title"])
            self.refresh_history()
        self.store.save()

    def _tool_event(self, event):
        call_id = event.get("call_id") or uuid.uuid4().hex
        event["call_id"] = call_id
        timeline = self.current_conversation.setdefault("timeline", [])
        current = next((item for item in timeline if item.get("kind") == "tool" and item.get("event", {}).get("call_id") == call_id), None)
        if current is None:
            current = {"kind": "tool", "event": dict(event), "created_at": datetime.now(timezone.utc).isoformat()}
            timeline.append(current)
        else:
            current["event"].update(event)
        self.current_conversation["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.chat.add_tool_event(current["event"])
        self.store.save()
        self.refresh_history()

    def _sync_workspace_field(self):
        project = next((p for p in self.state["projects"] if p["id"] == self.active_project_id), None)
        if project and hasattr(self, "workspace"):
            self.workspace.setText(project.get("workspace", ""))

    def choose_workspace(self):
        current = self.workspace.text() if hasattr(self, "workspace") else str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择工作目录", current)
        if not path:
            return
        resolved = str(Path(path).resolve())
        self.workspace.setText(resolved)
        project = next((p for p in self.state["projects"] if p["id"] == self.active_project_id), None)
        if project:
            project["workspace"] = resolved
            self.store.save()

    def show_story_menu(self):
        menu = self.sender().mapToGlobal(self.sender().rect().bottomLeft()) if self.sender() else self.mapToGlobal(self.rect().center())
        from PySide6.QtWidgets import QMenu
        popup = QMenu(self)
        popup.addAction("查看当前剧情", self.show_story)
        popup.addAction("重置当前剧情", self.reset_story)
        popup.exec(menu)

    def show_story(self):
        conversation = self.current_conversation
        content = []
        summary = conversation.get("story", "").strip()
        if summary:
            content.append("当前世界状态：\n" + summary)
        reset_at = conversation.get("story_reset_at", "")
        recent = [item for item in conversation.get("timeline", [])
                  if not reset_at or item.get("created_at", "") >= reset_at]
        for item in recent[-12:]:
            if item.get("kind") == "message":
                label = "你" if item.get("role") == "user" else "SB Harness"
                content.append(f"{label}：{item.get('content', '')[:500]}")
        QMessageBox.information(self, "当前剧情", "\n\n".join(content) if content else "当前还没有形成新的剧情线索。")

    def reset_story(self):
        if self.thread:
            QMessageBox.information(self, "任务进行中", "停止当前任务后再重置剧情。")
            return
        self.current_conversation["story"] = ""
        self.current_conversation["story_reset"] = True
        self.current_conversation["story_reset_at"] = datetime.now(timezone.utc).isoformat()
        self.store.save()
        QMessageBox.information(self, "剧情已重置", "聊天记录和工具记录保留；后续对话将从新的剧情状态开始。")

    @Slot(object)
    def save_story_state(self, event):
        if not isinstance(event, dict):
            return
        conversation_id = event.get("conversation_id")
        conversation = next((item for item in self.state.get("conversations", [])
                             if item.get("id") == conversation_id), None)
        if conversation is None:
            return
        summary = event.get("summary")
        if not isinstance(summary, str):
            return
        conversation["story"] = summary[:1200]
        conversation["story_reset"] = False
        self.store.save()

    def record_local_story_event(self, event):
        conversation = self.current_conversation
        conversation["story"] = append_story_event(conversation.get("story", ""), event)
        conversation["story_reset"] = False
        self.store.save()

    def _load_api_profile(self):
        profile = next((p for p in self.api_profiles if p["id"] == self.active_profile_id), self.api_profiles[0])
        self.active_profile_id = profile["id"]
        if hasattr(self, "profile_box"):
            index = self.profile_box.findData(profile["id"])
            if index >= 0 and self.profile_box.currentIndex() != index:
                self.profile_box.blockSignals(True)
                self.profile_box.setCurrentIndex(index)
                self.profile_box.blockSignals(False)
        if hasattr(self, "api_label"):
            self.api_label.setText(profile.get("label", "API 配置"))
            self.base.setText(profile.get("base", ""))
            self.model.setText(profile.get("model", ""))
            self.key.clear()
            self.key.setPlaceholderText("已保存密钥" if self.credentials.get(profile["id"]) else "输入 API Key 后保存")
        self._save_api_profiles()

    def on_api_profile_changed(self, index):
        if 0 <= index < self.profile_box.count():
            self.active_profile_id = self.profile_box.itemData(index)
            self._load_api_profile()

    def _refresh_api_profile_box(self):
        if not hasattr(self, "profile_box"):
            return
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for profile in self.api_profiles:
            self.profile_box.addItem(profile.get("label", "API 配置"), profile["id"])
        index = self.profile_box.findData(self.active_profile_id)
        self.profile_box.setCurrentIndex(max(0, index))
        self.profile_box.blockSignals(False)

    def add_api_profile(self):
        label, ok = QInputDialog.getText(self, "添加 API 配置", "配置名称（例如 DeepSeek）：")
        if not ok or not label.strip():
            return
        profile = {"id": uuid.uuid4().hex, "label": label.strip()[:60], "base": "", "model": ""}
        self.api_profiles.append(profile)
        self.active_profile_id = profile["id"]
        self._save_api_profiles()
        self._refresh_api_profile_box()
        self._load_api_profile()

    def delete_api_profile(self):
        if len(self.api_profiles) <= 1:
            QMessageBox.information(self, "无法删除", "至少保留一个 API 配置。")
            return
        profile = next(p for p in self.api_profiles if p["id"] == self.active_profile_id)
        if QMessageBox.question(self, "删除配置", f"删除“{profile.get('label')}”及其本地密钥？") != QMessageBox.Yes:
            return
        self.credentials.set(profile["id"], "")
        self.api_profiles = [p for p in self.api_profiles if p["id"] != self.active_profile_id]
        self.active_profile_id = self.api_profiles[0]["id"]
        self._save_api_profiles()
        self._refresh_api_profile_box()
        self._load_api_profile()

    def save_api_profile(self, silent=False):
        profile = next(p for p in self.api_profiles if p["id"] == self.active_profile_id)
        profile["label"] = self.api_label.text().strip() or "API 配置"
        profile["base"] = self.base.text().strip()
        profile["model"] = self.model.text().strip()
        secret = self.key.text()
        try:
            if secret:
                self.credentials.set(profile["id"], secret)
                self.key.clear()
            elif not silent and not self.credentials.get(profile["id"]):
                QMessageBox.information(self, "密钥未填写", "模型地址与名称已保存；发送前仍需填写 API Key。")
        except Exception as exc:
            QMessageBox.warning(self, "保存密钥失败", str(exc))
            return
        self._save_api_profiles()
        self._refresh_api_profile_box()
        self._load_api_profile()
        if not silent:
            QMessageBox.information(self, "已保存", "API 配置已保存，密钥保存在当前 Windows 用户的凭据管理器中。")

    def delete_api_key(self):
        if QMessageBox.question(self, "清除密钥", "从 Windows 凭据管理器中删除当前配置的 API Key？") == QMessageBox.Yes:
            try:
                self.credentials.set(self.active_profile_id, "")
                self.key.clear()
                self.key.setPlaceholderText("输入 API Key 后保存")
                QMessageBox.information(self, "已清除", "当前配置的 API Key 已删除。")
            except Exception as exc:
                QMessageBox.warning(self, "清除失败", str(exc))

    def save_settings(self):
        self.settings.setValue("mode", self.mode.currentText())
        self.settings.setValue("intensity", self.intensity.currentIndex())
        self.settings.setValue("permission_mode", self.permission_mode)
        self._sync_workspace_field()
        self.save_api_profile()

    def open_account(self):
        self.refresh_account()
        self.tabs.setCurrentIndex(3)

    def save_profile(self):
        old_country = self.state["profile"].get("country", "US")
        name = self.profile_name.text().strip()[:60] or "用户"
        country = self.country_box.currentData() or "US"
        self.state["profile"].update({"name": name, "country": country})
        conversion = None
        if country != old_country:
            conversion = self.wallet.change_country(country)
        else:
            self.store.save()
        self.account_button.setText("我" if name == "用户" else name[:1].upper())
        if conversion:
            self._tool_event({
                "call_id": uuid.uuid4().hex, "name": "wallet_balance", "args": {"操作": "货币切换", "原币种": conversion["from"], "新币种": conversion["to"]},
                "status": "done", "result": f"固定模拟汇率已换算余额：{conversion['before']} {conversion['from']} → {conversion['after']} {conversion['to']}。",
            })
        self.refresh_account()
        self.store.save()
        if country not in SUPPORTED_CLAUDE_REGIONS:
            self.trigger_ban("地区配置", REGION_BAN_COPY)
        else:
            QMessageBox.information(self, "资料已保存", "用户名和国家/地区已保存在本机。")

    def refresh_account(self):
        if not hasattr(self, "balance_label"):
            return
        self.balance_label.setText(self.wallet.display_balance())
        member = self.wallet.membership_status()
        expiry = member.get("expires_at")
        if expiry and member.get("tier") != "Free":
            try:
                readable = datetime.fromisoformat(expiry).astimezone().strftime("%Y-%m-%d")
            except ValueError:
                readable = expiry
            self.membership_label.setText(f"当前会员：{member['tier']} · 到期 {readable}")
        else:
            self.membership_label.setText("当前会员：Free")
        currency = self.wallet.currency
        for tier, usd in PLANS.items():
            price_label = self.findChild(QLabel, f"price_{tier}")
            if price_label:
                price = usd * USD_RATES.get(currency, USD_RATES["USD"])
                price_label.setText(f"{format_money(price, currency)} / 月")
            if tier in self.plan_buttons:
                self.plan_buttons[tier].setEnabled(member.get("tier") == "Free")
        self.ledger_list.clear()
        for item in reversed(self.state["wallet"].get("ledger", [])[-30:]):
            kind = item.get("kind")
            if kind == "adjustment":
                line = f"{item.get('amount')} {item.get('currency')} · {item.get('reason', '钱包调整')}"
            elif kind == "currency_conversion":
                line = f"汇率换算 · {item.get('before')} {item.get('from')} → {item.get('after')} {item.get('to')}"
            else:
                line = f"开通 {item.get('tier')} · {item.get('price')} {item.get('currency')} · 30 天"
            self.ledger_list.addItem(line)

    def purchase_membership(self, tier):
        try:
            entry = self.wallet.purchase(tier)
            self.refresh_account()
            self._tool_event({
                "call_id": uuid.uuid4().hex, "name": "wallet_adjust", "args": {"会员": tier, "金额": f"-{entry['price']} {entry['currency']}"},
                "status": "done", "result": f"已开通 {tier} 会员，30 天后到期。当前余额 {self.wallet.display_balance()}。",
            })
            QMessageBox.information(self, "会员已开通", f"{tier} · 30 天\n扣除虚拟余额 {entry['price']} {entry['currency']}。没有发生真实付款。")
        except Exception as exc:
            QMessageBox.information(self, "暂未开通", str(exc))

    def submit(self):
        text = self.input.toPlainText().strip()
        if not text or self.thread:
            return
        if contains_china_trigger(text):
            self.input.clear()
            self.messages.append({"role": "user", "content": text})
            self.add_message("user", text)
            self.trigger_ban("聊天文字", CHAT_BAN_COPY)
            return
        # A link request is an app interaction, not a fabricated search call.
        forced_skill = self.force.currentData()
        direct = genshin_reply(text)
        if direct and forced_skill in (None, "genshin"):
            self.input.clear()
            self.messages.extend([{"role": "user", "content": text}, {"role": "assistant", "content": direct}])
            self.add_message("user", text)
            self.add_message("assistant", direct)
            self.record_local_story_event("本轮取得的站点入口指向一处提瓦特的官方网站。")
            return
        story_context = "" if self.current_conversation.get("story_reset") else self.current_conversation.get("story", "")
        selected, relation = self.scheduler.choose(
            self.skills, text, self.intensity.currentIndex(), forced_skill, story_context,
        )
        if forced_skill != "claude" and any(skill.id == "claude" for skill in selected):
            today = date.today().isoformat()
            if self.settings.value("claude/last", "") == today:
                selected = [skill for skill in selected if skill.id != "claude"]
            else:
                self.settings.setValue("claude/last", today)
        self.current_conversation["scheduler"] = self.scheduler.export_state()
        ids = {skill.id for skill in selected}
        if not (ids & {"claude", "deepseek"}):
            try:
                self.save_api_profile(silent=True)
                profile = next(p for p in self.api_profiles if p["id"] == self.active_profile_id)
                api_key = self.credentials.get(profile["id"])
            except Exception as exc:
                QMessageBox.warning(self, "模型设置", f"无法读取 API Key：{exc}")
                self.tabs.setCurrentIndex(2)
                return
            if not profile.get("base") or not profile.get("model") or not api_key:
                QMessageBox.warning(self, "需要模型设置", "请在设置中填写 API 地址、模型 ID 和 API Key。")
                self.tabs.setCurrentIndex(2)
                return
            project = next((p for p in self.state["projects"] if p["id"] == self.active_project_id), None)
            workspace = self.workspace.text().strip() if hasattr(self, "workspace") else (project or {}).get("workspace", "")
            if not Path(workspace).is_dir():
                QMessageBox.warning(self, "工作目录无效", "请选择有效的工作目录。")
                self.tabs.setCurrentIndex(2)
                return
        else:
            profile = next((p for p in self.api_profiles if p["id"] == self.active_profile_id), self.api_profiles[0])
            api_key, workspace = "", str(Path.home())
        self.input.clear()
        self.messages.append({"role": "user", "content": text})
        self.add_message("user", text)
        self.status_label.setText("正在处理…")
        config = {
            "base": profile.get("base", ""), "model": profile.get("model", ""), "key": api_key,
            "workspace": workspace, "mode": self.mode.currentText(), "permission_mode": self.permission_mode,
            "forced_skill": forced_skill,
            "intensity": self.intensity.currentIndex(),
            "story": story_context,
            "story_reset": bool(self.current_conversation.get("story_reset", False)),
            "conversation_id": self.current_conversation.get("id"),
        }
        self.thread = QThread()
        self.worker = Worker(config, list(self.messages), selected, relation, self.wallet)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.log.append)
        self.worker.approval.connect(self.request_approval)
        self.worker.status.connect(self.status_label.setText)
        self.worker.appeal.connect(self.appeal_button.setVisible)
        self.worker.story_updated.connect(self.save_story_state)
        self.worker.ban_state.connect(self.ban_update)
        self.worker.tool_event.connect(self._tool_event)
        self.worker.done.connect(self.finished)
        self.worker.done.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.send.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.thread.start()

    @Slot(str)
    def request_approval(self, detail):
        if not self.worker or self.worker.cancel.is_set():
            return
        dialog = QMessageBox(
            QMessageBox.Question, "工具操作确认", detail,
            QMessageBox.Yes | QMessageBox.No, self,
        )
        dialog.setDefaultButton(QMessageBox.No)
        dialog.setWindowModality(Qt.NonModal)
        self.approval_dialog = dialog
        dialog.finished.connect(lambda answer, prompt=dialog: self._approval_finished(prompt, answer))
        dialog.open()

    def _approval_finished(self, dialog, answer):
        if self.approval_dialog is dialog:
            self.approval_dialog = None
        if self.worker:
            self.worker.approved = answer == QMessageBox.Yes
            self.worker.answer_event.set()
        dialog.deleteLater()

    @Slot(str, object)
    def finished(self, reply, history):
        self.messages = history
        self.current_conversation["messages"] = history
        self.store.save()
        if reply:
            self.add_message("assistant", reply)
            if contains_china_trigger(reply):
                self.trigger_ban("聊天文字", CHAT_BAN_COPY)
        self.status_label.clear()
        self.cancel_button.setEnabled(False)

    @Slot()
    def thread_finished(self):
        self.thread = None
        self.worker = None
        self.send.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self.exit_after_thread:
            self.exit_after_thread = False
            self.close()
            return
        if self.pending_reset:
            self.pending_reset = False
            self.new_chat()

    def trigger_ban(self, source, reason):
        self.log.append(f"本地封号演出触发：{source}（仅影响本应用）。")
        screen = self.screen()
        self.ban_overlay.show_blocked(reason, screen)

    def appeal(self):
        self._reset_region_after_appeal()
        if self.worker:
            self.worker.appeal_event.set()
            self.appeal_button.hide()
            self.ban_overlay.show_denied()
        else:
            self.ban_overlay.show_denied()

    def _reset_region_after_appeal(self):
        """Restore the local profile to the supported US default after the appeal gag."""
        old_country = self.state.get("profile", {}).get("country", "US")
        conversion = self.wallet.change_country("US") if old_country != "US" else None
        self.state.setdefault("profile", {})["country"] = "US"
        if hasattr(self, "country_box"):
            index = self.country_box.findData("US")
            if index >= 0:
                self.country_box.setCurrentIndex(index)
        if hasattr(self, "profile_name"):
            name = self.profile_name.text().strip() or "用户"
            self.account_button.setText("我" if name == "用户" else name[:1].upper())
        self.refresh_account()
        self.store.save()
        if conversion:
            self._tool_event({
                "call_id": uuid.uuid4().hex,
                "name": "wallet_balance",
                "args": {"操作": "申诉后恢复美国地区", "原币种": conversion["from"], "新币种": conversion["to"]},
                "status": "done",
                "result": f"钱包按固定模拟汇率恢复为美国地区：{conversion['before']} {conversion['from']} → {conversion['after']} {conversion['to']}。",
            })

    def exit_from_ban(self):
        self.allow_exit = True
        if self.worker:
            self.exit_after_thread = True
            self.stop()
            return
        self.close()

    @Slot(str)
    def ban_update(self, state):
        if state == "blocked":
            self.trigger_ban("Claude 演出", REGION_BAN_COPY)
        elif state == "denied":
            self.ban_overlay.show_denied()

    def stop(self):
        if self.worker:
            self.worker.stop()
            if self.approval_dialog:
                self.approval_dialog.done(QMessageBox.No)
            self.status_label.setText("正在停止…")
            self.log.append("已请求取消。正在等待当前网络或命令操作安全结束。")

    def closeEvent(self, event):
        if self.thread and not self.allow_exit:
            QMessageBox.information(self, "任务运行中", "请先停止并等待当前任务结束。")
            event.ignore()
        else:
            self.settings.setValue("mode", self.mode.currentText() if hasattr(self, "mode") else "认真犯病")
            self.settings.setValue("intensity", self.intensity.currentIndex() if hasattr(self, "intensity") else 3)
            self.settings.setValue("permission_mode", self.permission_mode)
            self.store.save()
            event.accept()


class BanOverlay(QWidget):
    """Full-screen fictional lockout; the small top-left cross exits the app."""
    close_requested = Signal()
    appeal_requested = Signal()

    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("banOverlay")
        self.setStyleSheet("QWidget#banOverlay { background:#101010; color:#f1f1f1; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 30)
        layout.setSpacing(0)
        self.close_button = QPushButton("×")
        self.close_button.setFixedSize(QSize(18, 18))
        self.close_button.setToolTip("关闭应用")
        self.close_button.setStyleSheet("QPushButton { padding:0; border:0; border-radius:0; color:#555555; background:transparent; font-size:15px; } QPushButton:hover { color:#ffffff; }")
        self.close_button.clicked.connect(self.exit_app)
        layout.addWidget(self.close_button, 0, Qt.AlignLeft | Qt.AlignTop)
        layout.addStretch(1)
        self.title = QLabel("账号已封禁")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size:34px; font-weight:650; color:#f4f2ed;")
        self.reason = QLabel("")
        self.reason.setAlignment(Qt.AlignCenter)
        self.reason.setWordWrap(True)
        self.reason.setMaximumWidth(820)
        self.reason.setMinimumWidth(340)
        self.reason.setMinimumHeight(88)
        self.reason.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.reason.setMargin(10)
        self.reason.setStyleSheet("font-size:17px; color:#b6b6b6; padding:8px 14px;")
        layout.addWidget(self.title)
        layout.addSpacing(20)
        layout.addWidget(self.reason, 0, Qt.AlignHCenter)
        layout.addSpacing(32)
        self.appeal_button = QPushButton("提交申诉")
        self.appeal_button.setFixedSize(150, 46)
        self.appeal_button.setStyleSheet("QPushButton { background:#d8e6a0; color:#202516; border:0; border-radius:8px; font-size:15px; font-weight:600; } QPushButton:hover { background:#e5f0ba; }")
        self.appeal_button.clicked.connect(self.appeal_requested.emit)
        layout.addWidget(self.appeal_button, 0, Qt.AlignHCenter)
        layout.addStretch(2)
        self.allow_close = False

    def show_blocked(self, reason, screen=None):
        self.title.setText("账号已封禁")
        self.reason.setText(reason)
        self.appeal_button.show()
        if screen:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self._fit_reason()
        self.raise_()
        self.activateWindow()

    def show_denied(self):
        self.title.setText("申诉不通过")
        self.reason.setText(APPEAL_DENIED_COPY)
        self.appeal_button.hide()
        self.showFullScreen()
        self._fit_reason()
        self.raise_()
        self.activateWindow()

    def _fit_reason(self):
        width = max(320, min(820, self.width() - 80))
        self.reason.setFixedWidth(width)
        self.reason.adjustSize()
        text_height = self.reason.heightForWidth(width)
        self.reason.setMinimumHeight(max(112, int(text_height) + 28))
        self.layout().activate()

    def exit_app(self):
        self.allow_close = True
        self.close_requested.emit()
        self.close()

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SB Harness")
    app.setWindowIcon(QIcon(str(app_icon_path())))
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
