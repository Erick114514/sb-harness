from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings, QSize, Qt, QThread, QTimer, Signal, Slot, QSignalBlocker
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from .chatview import (
    TOOL_LABELS, TOOL_STATUS, ChatFeed, ElidedLabel, MessageBubble, MessageComposer, NavItemDelegate,
    ToolCard, ToolRow, icon_pixmap, intensity_icon, line_icon, repolish,
)
from .credentials import CredentialStore
from .economy import (
    COUNTRIES, PLANS, SUPPORTED_CLAUDE_REGIONS, USD_RATES, WalletService,
    currency_for_country, format_money,
)
from .engine import (
    contains_china_trigger, genshin_reply, replace_addresses_with_genshin, run_turn, validate_model_base_url,
)
from .skills import Scheduler, import_skill, load_skills, validate_skill
from .storage import PersistentStore
from .themes import DEFAULT_THEME, THEMES, build_palette, build_stylesheet, get_theme, paint_theme_preview
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


class StyleCardGrid(QWidget):
    """Lays the style cards out in as many columns as fit, so Settings never scrolls sideways."""

    def __init__(self, cards, card_width=156, spacing=10):
        super().__init__()
        self.cards, self.card_width, self.spacing = list(cards), card_width, spacing
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self._columns = 0
        self._relayout(len(self.cards))

    def _relayout(self, columns):
        columns = max(1, min(len(self.cards), columns))
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards:
            self.grid.removeWidget(card)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns, Qt.AlignLeft | Qt.AlignTop)
        self.grid.setColumnStretch(columns, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout((self.width() + self.spacing) // (self.card_width + self.spacing))


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
        # The tool row waiting for an Allow/Reject decision (kept under its old name for callers and tests).
        self.approval_dialog = None
        self._pending_detail = ""
        self._appeal_shown = False
        self.pending_reset = False
        self.exit_after_thread = False
        self.allow_exit = False
        self.skills, skill_errors = load_skills(SKILLS)
        for skill in self.skills:
            skill.enabled = self.settings.value("skill/" + skill.id, skill.enabled, type=bool)
        self.ban_overlay = BanOverlay()
        self.ban_overlay.close_requested.connect(self.exit_from_ban)
        self.ban_overlay.appeal_requested.connect(self.appeal)
        self.theme = get_theme(self.settings.value("theme", DEFAULT_THEME))

        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)
        self.sidebar = self._build_sidebar()
        self.rail = self._build_rail()
        self.tabs = QStackedWidget()
        self.main_panel = QFrame()
        self.main_panel.setObjectName("mainPanel")
        panel_layout = QVBoxLayout(self.main_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(self.tabs)
        self.main_wrap = QWidget()
        self.main_wrap_layout = QVBoxLayout(self.main_wrap)
        self.main_wrap_layout.setContentsMargins(0, 0, 0, 0)
        self.main_wrap_layout.addWidget(self.main_panel)
        outer.addWidget(self.sidebar)
        outer.addWidget(self.rail)
        outer.addWidget(self.main_wrap, 1)
        self.build_chat()
        self.build_skills()
        self.build_settings()
        self.build_account()
        self._install_shortcuts()
        self.apply_theme(self.theme.id, persist=False, rebuild=False)
        self._populate_navigation()
        self._load_conversation(self.current_conversation)
        self._load_api_profile()
        self._refresh_behavior_chip()
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

    def _nav_button(self, kind, tip, callback, size=40):
        button = QToolButton()
        button.setObjectName("navIcon")
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setFixedSize(size, size)
        button.setIconSize(QSize(20, 20))
        button.setProperty("iconKind", kind)
        button.clicked.connect(callback)
        return button

    def _avatar_button(self):
        button = QToolButton()
        button.setObjectName("avatar")
        button.setFixedSize(32, 32)
        button.setToolTip("个人资料与会员")
        button.setAccessibleName("个人资料与会员")
        button.clicked.connect(self.open_account)
        return button

    def _build_sidebar(self):
        rail = QFrame()
        rail.setObjectName("sidebar")
        rail.setFixedWidth(248)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(12, 18, 12, 14)
        layout.setSpacing(14)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(8, 0, 8, 0)
        brand_row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(QIcon(str(app_icon_path())).pixmap(QSize(26, 26)))
        brand_row.addWidget(icon)
        brand = QLabel("SB Harness")
        brand.setObjectName("brand")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)

        actions = QVBoxLayout()
        actions.setSpacing(4)
        self.new_chat_button = QPushButton()
        self.new_chat_button.setObjectName("newChat")
        self.new_chat_button.setAccessibleName("新对话")
        self.new_chat_button.setCursor(Qt.PointingHandCursor)
        self.new_chat_button.clicked.connect(self.new_chat)
        row = QHBoxLayout(self.new_chat_button)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(10)
        self.new_chat_icon = QLabel()
        new_chat_text = QLabel("新对话")
        new_chat_hint = QLabel("Ctrl N")
        new_chat_hint.setObjectName("hint")
        for part in (self.new_chat_icon, new_chat_text, new_chat_hint):
            part.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.new_chat_icon)
        row.addWidget(new_chat_text)
        row.addStretch(1)
        row.addWidget(new_chat_hint)
        actions.addWidget(self.new_chat_button)

        self.project_switch = QPushButton()
        self.project_switch.setObjectName("projectSwitch")
        self.project_switch.setCursor(Qt.PointingHandCursor)
        self.project_switch.clicked.connect(self._show_project_menu)
        row = QHBoxLayout(self.project_switch)
        row.setContentsMargins(12, 4, 12, 4)
        row.setSpacing(10)
        self.project_icon = QLabel()
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        self.project_name_label = ElidedLabel()
        self.project_name_label.setObjectName("projectName")
        self.project_path_label = ElidedLabel(mode=Qt.ElideMiddle)
        self.project_path_label.setObjectName("projectPath")
        names.addWidget(self.project_name_label)
        names.addWidget(self.project_path_label)
        self.project_chevron = QLabel()
        for part in (self.project_icon, self.project_name_label, self.project_path_label, self.project_chevron):
            part.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.project_icon)
        row.addLayout(names, 1)
        row.addWidget(self.project_chevron)
        actions.addWidget(self.project_switch)
        layout.addLayout(actions)

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_delegate = NavItemDelegate(self.history_list, self.theme)
        self.history_list.setItemDelegate(self.history_delegate)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_list.setTextElideMode(Qt.ElideRight)
        self.history_list.setMouseTracking(True)
        self.history_list.itemClicked.connect(self.select_conversation_item)
        layout.addWidget(self.history_list, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        self.skill_button = self._nav_button("sparkle", "Skill 工坊", lambda: self.tabs.setCurrentIndex(1))
        self.settings_button = self._nav_button("sliders", "设置", lambda: self.tabs.setCurrentIndex(2))
        self.account_button = self._avatar_button()
        bottom.addWidget(self.skill_button)
        bottom.addWidget(self.settings_button)
        bottom.addStretch(1)
        bottom.addWidget(self.account_button)
        layout.addLayout(bottom)
        return rail

    def _build_rail(self):
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(64)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(6)
        brand = QLabel()
        brand.setPixmap(QIcon(str(app_icon_path())).pixmap(QSize(28, 28)))
        brand.setToolTip("SB Harness")
        layout.addWidget(brand, 0, Qt.AlignHCenter)
        layout.addSpacing(8)
        self.rail_chats = self._nav_button("list", "对话与项目（Ctrl+K）", lambda: self._show_switch_menu(self.rail_chats))
        self.rail_buttons = [
            self._nav_button("plus", "新对话（Ctrl+N）", self.new_chat),
            self.rail_chats,
            self._nav_button("sparkle", "Skill 工坊", lambda: self.tabs.setCurrentIndex(1)),
            self._nav_button("sliders", "设置", lambda: self.tabs.setCurrentIndex(2)),
        ]
        for button in self.rail_buttons:
            layout.addWidget(button, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        self.rail_account = self._avatar_button()
        layout.addWidget(self.rail_account, 0, Qt.AlignHCenter)
        return rail

    def build_chat(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(32, 14, 18, 6)
        header.setSpacing(6)
        self.header_layout = header
        self.header_brand = QLabel()
        self.header_brand.setPixmap(QIcon(str(app_icon_path())).pixmap(QSize(20, 20)))
        self.title_prefix = QLabel()
        self.title_prefix.setObjectName("titlePrefix")
        self.conversation_title = ElidedLabel(self.current_conversation.get("title", "新对话"))
        self.conversation_title.setObjectName("chatTitle")
        self.title_suffix = QLabel(">")
        self.title_suffix.setObjectName("titleSuffix")
        header.addWidget(self.header_brand)
        header.addWidget(self.title_prefix)
        header.addWidget(self.conversation_title)
        header.addWidget(self.title_suffix)
        header.addStretch(1)
        self.switch_button = QToolButton()
        self.switch_button.setObjectName("ghost")
        self.switch_button.setText("切换对话")
        self.switch_button.setToolTip("对话、项目和页面（Ctrl+K）")
        self.switch_button.clicked.connect(lambda: self._show_switch_menu(self.switch_button))
        story_button = QToolButton()
        story_button.setObjectName("ghost")
        story_button.setText("剧情")
        story_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        story_button.setIconSize(QSize(17, 17))
        story_button.setToolTip("查看或重置当前剧情")
        story_button.clicked.connect(self.show_story_menu)
        self.story_button = story_button
        self.header_settings = QToolButton()
        self.header_settings.setObjectName("ghost")
        self.header_settings.setToolTip("设置")
        self.header_settings.setAccessibleName("设置")
        self.header_settings.setIconSize(QSize(18, 18))
        self.header_settings.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
        header.addWidget(self.switch_button)
        header.addWidget(story_button)
        header.addWidget(self.header_settings)
        layout.addLayout(header)

        self.chat = ChatFeed(self.theme)
        layout.addWidget(self.chat, 1)

        # The status line sits in the same centred column as the composer, so they line up.
        self.status_row_widget = QWidget()
        self.status_row = QHBoxLayout(self.status_row_widget)
        self.status_row.setContentsMargins(28, 2, 28, 0)
        self.status_inner = QWidget()
        self.status_inner_layout = QHBoxLayout(self.status_inner)
        self.status_inner_layout.setContentsMargins(16, 0, 0, 0)
        self.status_label = ElidedLabel("")
        self.status_label.setObjectName("statusLine")
        self.status_inner_layout.addWidget(self.status_label)
        self.appeal_button = QPushButton("申诉")
        self.appeal_button.setObjectName("secondary")
        self.appeal_button.hide()
        self.appeal_button.clicked.connect(self.appeal)
        self.status_inner_layout.addWidget(self.appeal_button)
        self.status_inner_layout.addStretch(1)
        self.status_row.addStretch(1)
        self.status_row.addWidget(self.status_inner, 1000)
        self.status_row.addStretch(1)
        self.status_row_widget.hide()
        layout.addWidget(self.status_row_widget)

        self.composer_dock = QFrame()
        self.composer_dock.setObjectName("composerDock")
        dock = QHBoxLayout(self.composer_dock)
        dock.setContentsMargins(28, 8, 28, 18)
        dock.setSpacing(0)
        self.composer_layout = dock
        self.composer_box = QFrame()
        self.composer_box.setObjectName("composerBox")
        self.composer_box.setProperty("focus", False)
        dock.addStretch(1)
        dock.addWidget(self.composer_box, 1000)
        dock.addStretch(1)
        box = QVBoxLayout(self.composer_box)
        box.setContentsMargins(16, 10, 10, 8)
        box.setSpacing(2)
        self.composer_box_layout = box
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.prompt_mark = QLabel(">")
        self.prompt_mark.setObjectName("promptMark")
        input_row.addWidget(self.prompt_mark, 0, Qt.AlignTop)
        self.input = MessageComposer()
        self.input.setObjectName("composerInput")
        self.input.setPlaceholderText("给 SB Harness 发消息…")
        self.input.send_requested.connect(self.submit)
        self.input.installEventFilter(self)
        input_row.addWidget(self.input, 1)
        box.addLayout(input_row)

        controls = QHBoxLayout()
        controls.setSpacing(2)
        self.permission_button = QToolButton()
        self.permission_button.setObjectName("chip")
        self.permission_button.setPopupMode(QToolButton.InstantPopup)
        self.permission_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.permission_button.setIconSize(QSize(16, 16))
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

        self.behavior_button = QToolButton()
        self.behavior_button.setObjectName("chip")
        self.behavior_button.setPopupMode(QToolButton.InstantPopup)
        self.behavior_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.behavior_button.setIconSize(QSize(16, 16))
        self.behavior_menu = QMenu(self.behavior_button)
        heading = self.behavior_menu.addAction("模式")
        heading.setEnabled(False)
        self.mode_group = QActionGroup(self.behavior_menu)
        self.mode_actions = {}
        for name in ("认真犯病", "纯种 SB"):
            action = QAction(name, self.behavior_menu)
            action.setCheckable(True)
            self.mode_group.addAction(action)
            self.behavior_menu.addAction(action)
            action.triggered.connect(lambda checked=False, chosen=name: self._set_mode(chosen))
            self.mode_actions[name] = action
        self.behavior_menu.addSeparator()
        heading = self.behavior_menu.addAction("犯病程度")
        heading.setEnabled(False)
        self.intensity_group = QActionGroup(self.behavior_menu)
        self.intensity_actions = []
        for index, name in enumerate(("平静", "轻度", "中度", "重度")):
            action = QAction(name, self.behavior_menu)
            action.setCheckable(True)
            self.intensity_group.addAction(action)
            self.behavior_menu.addAction(action)
            action.triggered.connect(lambda checked=False, chosen=index: self._set_intensity(chosen))
            self.intensity_actions.append(action)
        self.behavior_button.setMenu(self.behavior_menu)
        controls.addWidget(self.behavior_button)
        controls.addStretch(1)
        self.action_button = QPushButton()
        self.action_button.setObjectName("actionButton")
        self.action_button.setCursor(Qt.PointingHandCursor)
        self.action_button.clicked.connect(self._on_action)
        controls.addWidget(self.action_button)
        box.addLayout(controls)
        layout.addWidget(self.composer_dock)
        self.tabs.addWidget(page)

    def _refresh_permission_controls(self):
        labels = {"ask": "每次询问", "approve_safe": "自动批准安全操作", "full": "完全访问"}
        descriptions = {
            "ask": "文件、网页和命令操作执行前逐次确认。",
            "approve_safe": "工作目录内安全文件操作和公开网页自动执行；命令及目录外文件仍需确认。",
            "full": "应用内文件、公开网页和命令操作免逐次确认；命令使用当前 Windows 用户权限。",
        }
        if hasattr(self, "permission_button"):
            self.permission_button.setText(labels[self.permission_mode])
            self.permission_button.setToolTip("工具权限：" + descriptions[self.permission_mode])
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

    def _set_mode(self, name):
        if hasattr(self, "mode"):
            self.mode.setCurrentText(name)

    def _set_intensity(self, index):
        if hasattr(self, "intensity"):
            self.intensity.setCurrentIndex(index)

    def _on_behavior_changed(self, *_):
        self.settings.setValue("mode", self.mode.currentText())
        self.settings.setValue("intensity", self.intensity.currentIndex())
        self._refresh_behavior_chip()

    def _refresh_behavior_chip(self):
        if not hasattr(self, "intensity") or not hasattr(self, "behavior_button"):
            return
        mode, level = self.mode.currentText(), self.intensity.currentIndex()
        self.behavior_button.setText(mode)
        self.behavior_button.setIcon(intensity_icon(level, self.theme.muted, self.theme.border_strong))
        self.behavior_button.setToolTip(f"演出：{mode}，{self.intensity.currentText()}")
        self.behavior_button.setAccessibleName(f"演出设置：{mode}，{self.intensity.currentText()}")
        for name, action in self.mode_actions.items():
            action.setChecked(name == mode)
        for index, action in enumerate(self.intensity_actions):
            action.setChecked(index == level)

    def apply_theme(self, theme_id, persist=True, rebuild=True):
        """Switch the whole window to another style. The conversation is redrawn, not reloaded from disk."""
        theme = get_theme(theme_id)
        changed = getattr(self, "_applied_theme_id", None) != theme.id
        self.theme = theme
        app = QApplication.instance()
        if app is not None:
            app.setPalette(build_palette(theme))
        self.setStyleSheet(build_stylesheet(theme))
        self._apply_theme_layout()
        self._applied_theme_id = theme.id
        if persist:
            self.settings.setValue("theme", theme.id)
        if rebuild and changed:
            self._rebuild_feed()
        if theme.id in getattr(self, "style_cards", {}):
            self.style_cards[theme.id].setChecked(True)
        return theme

    def _apply_theme_layout(self):
        t = self.theme
        self.sidebar.setVisible(t.nav == "sidebar")
        self.rail.setVisible(t.nav == "rail")
        self.main_wrap_layout.setContentsMargins(*((4, 12, 12, 12) if t.door else (0, 0, 0, 0)))
        console = t.nav == "none"
        for widget in (self.header_brand, self.title_prefix, self.title_suffix, self.switch_button, self.header_settings):
            widget.setVisible(console)
        self.prompt_mark.setVisible(t.user_style == "prompt")
        self.composer_box.setMaximumWidth(t.column_width)
        self.status_inner.setMaximumWidth(t.column_width)
        self.composer_box_layout.setContentsMargins(*((2, 6, 2, 4) if console else (16, 10, 10, 8)))
        self.status_inner_layout.setContentsMargins(2 if console else 16, 0, 0, 0)
        self.new_chat_icon.setPixmap(icon_pixmap("plus", t.text, 18))
        self.project_icon.setPixmap(icon_pixmap("folder", t.muted, 18))
        self.project_chevron.setPixmap(icon_pixmap("chevron_down", t.faint, 16))
        for button in (self.skill_button, self.settings_button, *self.rail_buttons):
            button.setIcon(line_icon(button.property("iconKind"), t.muted))
        self.story_button.setIcon(line_icon("book", t.muted, 17))
        self.header_settings.setIcon(line_icon("sliders", t.muted, 18))
        self.permission_button.setIcon(line_icon("shield", t.muted, 16))
        self.history_delegate.theme = t
        self.chat.set_theme(t)
        self._refresh_avatar()
        self._refresh_title_prefix()
        self._refresh_behavior_chip()
        self._set_running(bool(self.thread))
        self.refresh_history()
        self._update_responsive_margins()

    def _rebuild_feed(self):
        pending = None
        if self.approval_dialog is not None:
            pending = (getattr(self.approval_dialog, "call_id", None), self._pending_detail)
        self.approval_dialog = None
        self._load_conversation(self.current_conversation)
        if pending and self.worker is not None and not self.worker.cancel.is_set():
            call_id, detail = pending
            row = self.chat.cards.get(call_id) if call_id else None
            if row is None or not row.is_waiting():
                row = self.chat.add_standalone_approval(detail)
            self._attach_approval(row, detail)

    def _refresh_avatar(self, name=None):
        if name is None:
            name = self.state.get("profile", {}).get("name", "用户")
        text = "我" if not name or name == "用户" else name[:1].upper()
        for button in (getattr(self, "account_button", None), getattr(self, "rail_account", None)):
            if button is not None:
                button.setText(text)

    def _refresh_title_prefix(self):
        if not hasattr(self, "title_prefix"):
            return
        project = next((p for p in self.state["projects"] if p["id"] == self.active_project_id), {})
        self.title_prefix.setText(f"PS {project.get('name', '个人')}\\")

    @staticmethod
    def _short_path(path):
        text = str(path or "")
        home = str(Path.home())
        return "~" + text[len(home):] if text.lower().startswith(home.lower()) else text

    @staticmethod
    def _short_date(stamp):
        try:
            return datetime.fromisoformat(str(stamp)).astimezone().strftime("%m/%d")
        except (TypeError, ValueError):
            return ""

    def _install_shortcuts(self):
        self._shortcuts = []
        for keys, handler in (("Ctrl+N", self.new_chat), ("Ctrl+K", self._show_switch_menu), ("Esc", self._escape)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    def _escape(self):
        if self.thread:
            self.stop()
        elif self.tabs.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)

    def _fill_project_menu(self, menu):
        group = QActionGroup(menu)
        group.setExclusive(True)
        for project in self.state["projects"]:
            action = menu.addAction(project.get("name", "项目"))
            action.setCheckable(True)
            action.setChecked(project["id"] == self.active_project_id)
            action.setToolTip(project.get("workspace", ""))
            group.addAction(action)
            action.triggered.connect(lambda checked=False, chosen=project["id"]: self.select_project(chosen))
        menu.addSeparator()
        menu.addAction("新建项目…", self.create_project)
        menu.addAction("更改工作目录…", self.choose_workspace)

    def _show_project_menu(self):
        menu = QMenu(self)
        self._fill_project_menu(menu)
        menu.exec(self.project_switch.mapToGlobal(self.project_switch.rect().bottomLeft()))

    def _show_switch_menu(self, anchor=None):
        menu = QMenu(self)
        menu.addAction("新对话", self.new_chat)
        menu.addSeparator()
        heading = menu.addAction("最近对话")
        heading.setEnabled(False)
        conversations = sorted(
            (c for c in self.state["conversations"] if c.get("project_id") == self.active_project_id),
            key=lambda item: item.get("updated_at", ""), reverse=True,
        )[:12]
        for conversation in conversations:
            action = menu.addAction((conversation.get("title") or "新对话")[:36])
            action.setCheckable(True)
            action.setChecked(conversation.get("id") == self.current_conversation.get("id"))
            action.triggered.connect(lambda checked=False, chosen=conversation["id"]: self.select_conversation(chosen))
        menu.addSeparator()
        self._fill_project_menu(menu.addMenu("切换项目"))
        menu.addSeparator()
        menu.addAction("Skill 工坊", lambda: self.tabs.setCurrentIndex(1))
        menu.addAction("设置", lambda: self.tabs.setCurrentIndex(2))
        menu.addAction("个人资料与会员", self.open_account)
        if anchor is getattr(self, "rail_chats", None):
            point = anchor.mapToGlobal(anchor.rect().topRight())
        elif anchor is not None:
            point = anchor.mapToGlobal(anchor.rect().bottomLeft())
        else:
            point = self.mapToGlobal(QPoint(max(0, (self.width() - 260) // 2), 72))
        menu.exec(point)

    def _set_running(self, running):
        t = self.theme
        button = self.action_button
        button.setProperty("state", "stop" if running else "send")
        if t.send_shape == "round":
            button.setFixedSize(40, 40)
            button.setText("")
            kind, colour = ("stop", t.stop_text) if running else ("arrow_up", t.on_accent)
            button.setIcon(line_icon(kind, colour, 20, 2.2))
            button.setIconSize(QSize(20, 20))
        else:
            button.setMinimumSize(0, 34)
            button.setMaximumSize(16777215, 16777215)
            button.setIcon(QIcon())
            button.setText("Esc 停止" if running else "Enter 发送")
        button.setToolTip("停止当前任务（Esc）" if running else "发送（Enter）")
        button.setAccessibleName("停止" if running else "发送")
        repolish(button)

    def _on_action(self):
        if self.thread:
            self.stop()
        else:
            self.submit()

    def _set_status(self, text):
        self.status_label.setText(text or "")
        self._update_status_row()

    def _set_appeal_visible(self, visible):
        self._appeal_shown = bool(visible)
        self.appeal_button.setVisible(self._appeal_shown)
        self._update_status_row()

    def _update_status_row(self):
        self.status_row_widget.setVisible(bool(self.status_label.text()) or self._appeal_shown)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "input", None) and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            self.composer_box.setProperty("focus", event.type() == QEvent.FocusIn)
            repolish(self.composer_box)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_margins()

    def _update_responsive_margins(self):
        if not hasattr(self, "composer_layout"):
            return
        narrow = self.width() < 940
        self.sidebar.setFixedWidth(216 if narrow else 248)
        margin = 16 if narrow else 28
        self.header_layout.setContentsMargins(margin + 4, 14, max(8, margin - 10), 6)
        self.composer_layout.setContentsMargins(margin, 8, margin, 18)
        self.status_row.setContentsMargins(margin, 2, margin, 0)
        self.chat.set_side_margin(margin)

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
        back.setToolTip("返回聊天（Esc）")
        back.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        header.addWidget(back)
        outer.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("pageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 4, 12, 12)
        layout.setSpacing(18)

        appearance_title = QLabel("外观")
        appearance_title.setObjectName("sectionLabel")
        layout.addWidget(appearance_title)
        self.style_group = QButtonGroup(self)
        self.style_group.setExclusive(True)
        self.style_cards = {}
        for theme in THEMES.values():
            card = QToolButton()
            card.setObjectName("styleCard")
            card.setCheckable(True)
            card.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            card.setIcon(QIcon(paint_theme_preview(theme)))
            card.setIconSize(QSize(132, 84))
            card.setText(theme.name)
            card.setToolTip(theme.summary)
            card.setAccessibleName(f"界面风格：{theme.name}，{theme.summary}")
            card.setFixedWidth(156)
            card.setChecked(theme.id == self.theme.id)
            card.clicked.connect(lambda checked=False, chosen=theme.id: self.apply_theme(chosen))
            self.style_group.addButton(card)
            self.style_cards[theme.id] = card
        layout.addWidget(StyleCardGrid(self.style_cards.values()))

        api_title = QLabel("模型")
        api_title.setObjectName("sectionLabel")
        layout.addWidget(api_title)
        profile_row = QHBoxLayout()
        self.profile_box = QComboBox()
        self.profile_box.currentIndexChanged.connect(self.on_api_profile_changed)
        profile_row.addWidget(self.profile_box, 1)
        self._refresh_api_profile_box()
        for glyph, tip, callback in (("＋", "添加 API 配置", self.add_api_profile), ("删除", "删除当前配置", self.delete_api_profile)):
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
        endpoint_note = QLabel("云端模型必须使用 HTTPS；HTTP 仅允许 localhost 或回环 IP。")
        endpoint_note.setObjectName("muted")
        form.addRow("", endpoint_note)
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
        self.mode.currentTextChanged.connect(self._on_behavior_changed)
        self.intensity.currentIndexChanged.connect(self._on_behavior_changed)
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
        # Log lines carry tool arguments and results, so they are shown as plain text, never HTML.
        self.log = QPlainTextEdit()
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
        content.setObjectName("pageContent")
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
        self.balance_label.setObjectName("balance")
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
        project = next((p for p in self.state["projects"] if p["id"] == self.active_project_id), {})
        name = project.get("name", "项目")
        workspace = project.get("workspace", "")
        self.project_name_label.setText(name)
        self.project_path_label.setText(self._short_path(workspace))
        self.project_switch.setToolTip(f"{name}\n{workspace}" if workspace else name)
        self.project_switch.setAccessibleName(f"切换项目，当前是{name}")
        self._refresh_title_prefix()
        self.refresh_history()

    def refresh_history(self):
        self.history_list.blockSignals(True)
        self.history_list.clear()
        items = [c for c in self.state["conversations"] if c.get("project_id") == self.active_project_id]
        for conversation in sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True):
            title = conversation.get("title") or "新对话"
            row = QListWidgetItem(title[:36])
            row.setToolTip(title)
            row.setData(Qt.UserRole, conversation["id"])
            row.setData(NavItemDelegate.DATE_ROLE, self._short_date(conversation.get("updated_at", "")))
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

    def select_project(self, project_id):
        if self.thread:
            return
        if not any(p["id"] == project_id for p in self.state["projects"]):
            return
        self.active_project_id = project_id
        self.settings.setValue("active_project", project_id)
        recent = next((c for c in reversed(self.state["conversations"]) if c.get("project_id") == project_id), None)
        if recent:
            self._load_conversation(recent)
        else:
            self.new_chat()
        self._populate_navigation()
        self._sync_workspace_field()

    def select_conversation_item(self, item):
        self.select_conversation(item.data(Qt.UserRole))

    def select_conversation(self, conversation_id):
        if self.thread:
            return
        conversation = next((c for c in self.state["conversations"] if c.get("id") == conversation_id), None)
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
            self._set_status("正在结束当前操作，然后开启新对话…")
            return
        conversation = self._new_conversation_record()
        self.state["conversations"].append(conversation)
        self.store.save()
        self._load_conversation(conversation)
        self.refresh_history()
        self._set_status("") if hasattr(self, "status_label") else None
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
        self._refresh_avatar(name)
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
        if direct:
            if forced_skill == "doubao":
                direct = "我以最直白最不绕弯子的方式说：\n" + direct
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
            try:
                validate_model_base_url(profile["base"])
            except ValueError as exc:
                QMessageBox.warning(self, "模型地址不安全", str(exc))
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
        self._set_status("正在处理…")
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
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.approval.connect(self.request_approval)
        self.worker.status.connect(self._set_status)
        self.worker.appeal.connect(self._set_appeal_visible)
        self.worker.story_updated.connect(self.save_story_state)
        self.worker.ban_state.connect(self.ban_update)
        self.worker.tool_event.connect(self._tool_event)
        self.worker.done.connect(self.finished)
        self.worker.done.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self._set_running(True)
        self.thread.start()

    @Slot(str)
    def request_approval(self, detail):
        """Ask for Allow/Reject inside the conversation, on the tool call that is waiting."""
        if not self.worker or self.worker.cancel.is_set():
            return
        row = self.chat.waiting_row() or self.chat.add_standalone_approval(detail)
        self._attach_approval(row, detail)

    def _attach_approval(self, row, detail):
        self._pending_detail = detail
        self.approval_dialog = row
        row.show_approval(detail, lambda approved, target=row: self._on_row_decided(target, approved))
        self.chat.scroll_to_bottom()

    def _on_row_decided(self, row, approved):
        if self.approval_dialog is row:
            self._resolve_approval(approved)

    def _resolve_approval(self, approved, cancelled=False):
        row, self.approval_dialog = self.approval_dialog, None
        self._pending_detail = ""
        if row is not None:
            row.finish_approval(approved, cancelled)
            if not cancelled and row.call_id:
                self._record_approval(row.call_id, approved)
        if self.worker:
            self.worker.approved = bool(approved) and not cancelled
            self.worker.answer_event.set()

    def _record_approval(self, call_id, approved):
        """Keep the decision in the timeline so the stamp survives reloads and style changes."""
        for item in self.current_conversation.get("timeline", []):
            event = item.get("event", {}) if item.get("kind") == "tool" else {}
            if event.get("call_id") == call_id:
                event["approval"] = "approved" if approved else "denied"
                self.store.save()
                return

    @Slot(str, object)
    def finished(self, reply, history):
        self.messages = history
        self.current_conversation["messages"] = history
        self.store.save()
        if reply:
            self.add_message("assistant", reply)
            if contains_china_trigger(reply):
                self.trigger_ban("聊天文字", CHAT_BAN_COPY)
        self._set_status("")

    @Slot()
    def thread_finished(self):
        self.thread = None
        self.worker = None
        self._set_running(False)
        if self.exit_after_thread:
            self.exit_after_thread = False
            self.close()
            return
        if self.pending_reset:
            self.pending_reset = False
            self.new_chat()

    def trigger_ban(self, source, reason):
        self.log.appendPlainText(f"本地封号演出触发：{source}（仅影响本应用）。")
        screen = self.screen()
        self.ban_overlay.show_blocked(reason, screen)

    def appeal(self):
        self._reset_region_after_appeal()
        if self.worker:
            self.worker.appeal_event.set()
            self._set_appeal_visible(False)
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
            self._refresh_avatar(self.profile_name.text().strip() or "用户")
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
            if self.approval_dialog is not None:
                self._resolve_approval(False, cancelled=True)
            self._set_status("正在停止…")
            self.log.appendPlainText("已请求取消。正在等待当前网络或命令操作安全结束。")

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
