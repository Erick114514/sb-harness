"""Interface styles for SB Harness.

Each style is a set of colour, type and shape tokens plus a few layout switches. The Qt
stylesheet and palette are generated from the tokens, so every page follows the chosen style.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap

SANS = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimHei", "DengXian")
SERIF = ("Source Han Serif SC", "Noto Serif SC", "Source Han Serif", "SimSun", "Microsoft YaHei UI")
MONO = ("Cascadia Mono", "Consolas", "Microsoft YaHei UI", "Microsoft YaHei")


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    summary: str
    dark: bool
    # Surfaces
    ground: str
    sidebar: str
    sidebar_rule: str
    panel: str
    surface: str
    input_bg: str
    raised: str
    nav_selected: str
    border: str
    border_strong: str
    # Text
    text: str
    muted: str
    faint: str
    # Accent and states
    accent: str
    accent_hover: str
    on_accent: str
    focus: str
    selection: str
    ok: str
    warn: str
    danger: str
    link: str
    seal: str
    # Chat pieces
    user_bg: str
    user_text: str
    user_border: str
    tool_bg: str
    tool_hover: str
    note_bg: str
    note_border: str
    note_muted: str
    composer_bg: str
    composer_border: str
    stop_bg: str
    stop_text: str
    stop_border: str
    new_chat_bg: str
    # Type
    ui_font: tuple = SANS
    prose_font: tuple = SANS
    title_font: tuple = SANS
    label_font: tuple = SANS
    mono_font: tuple = MONO
    font_size: int = 14
    prose_size: int = 15
    prose_line_height: int = 170
    title_size: int = 17
    title_weight: int = 700
    # Shape
    radius: int = 16
    radius_sm: int = 10
    bubble_radius: int = 18
    bubble_tail: int = 6
    # Layout switches
    nav: str = "sidebar"          # sidebar | rail | none
    door: bool = False            # main area as an inset rounded panel
    tool_style: str = "dot"       # dot | pill | exhibit | console | footnote
    user_style: str = "bubble"    # bubble | prompt
    send_shape: str = "round"     # round | text
    show_dates: bool = False
    column_width: int = 680
    feed_spacing: int = 14


YOLK = Theme(
    id="yolk", name="奶蛙", summary="深色，配色取自奶蛙图标", dark=True,
    ground="#1F1D1A", sidebar="#181715", sidebar_rule="#2B2824", panel="#1F1D1A",
    surface="#262420", input_bg="#262420", raised="#2C2925", nav_selected="#2C2925",
    border="#34302B", border_strong="#4A4339",
    text="#F2E8D5", muted="#ADA493", faint="#9A9282",
    accent="#FFC53D", accent_hover="#FFD166", on_accent="#201E1B", focus="#A08B55", selection="#5A4C2A",
    ok="#9CCB8C", warn="#FFC53D", danger="#E8A091", link="#FFC53D", seal="#E8A091",
    user_bg="#EFE2C4", user_text="#2A2620", user_border="none",
    tool_bg="transparent", tool_hover="#262420",
    note_bg="#2A2724", note_border="#4A4339", note_muted="#ADA493",
    composer_bg="#262420", composer_border="#3A3632",
    stop_bg="#3A3632", stop_text="#F2E8D5", stop_border="none", new_chat_bg="transparent",
    bubble_radius=20,
)

FRIDGE = Theme(
    id="fridge", name="冰箱", summary="浅色搪瓷，主区是一扇圆角门", dark=False,
    ground="#E8EDEB", sidebar="#E8EDEB", sidebar_rule="transparent", panel="#FBFCFB",
    surface="#FFFFFF", input_bg="#FFFFFF", raised="#EEF3F1", nav_selected="#FBFCFB",
    border="#D2DBD7", border_strong="#C5CFCB",
    text="#1C2723", muted="#56635E", faint="#6A7671",
    accent="#2B50C8", accent_hover="#3A60D8", on_accent="#FFFFFF", focus="#2B50C8", selection="#C9D6F5",
    ok="#2E7D5B", warn="#8A6200", danger="#B4412F", link="#2B50C8", seal="#B4412F",
    user_bg="#E8EDEB", user_text="#1C2723", user_border="none",
    tool_bg="#EAF1F3", tool_hover="#E1EBEE",
    note_bg="#FFF3C9", note_border="#EADFAF", note_muted="#5C5636",
    composer_bg="#EFF3F1", composer_border="#EFF3F1",
    stop_bg="#1C2723", stop_text="#FFFFFF", stop_border="none", new_chat_bg="#FBFCFB",
    ui_font=("Bahnschrift",) + SANS, title_font=("Bahnschrift",) + SANS,
    radius=24, radius_sm=14, door=True, tool_style="pill",
)

DOCKET = Theme(
    id="docket", name="公文", summary="表单蓝线，工具调用编号，审批盖章", dark=False,
    ground="#FFFFFF", sidebar="#EEF1F4", sidebar_rule="#DCE3EE", panel="#FFFFFF",
    surface="#FFFFFF", input_bg="#FFFFFF", raised="#F3F6FA", nav_selected="#FFFFFF",
    border="#DCE3EE", border_strong="#C9D3E6",
    text="#1E2228", muted="#555D66", faint="#6B737C",
    accent="#2A4A8A", accent_hover="#355AA3", on_accent="#FFFFFF", focus="#2A4A8A", selection="#D5DEEF",
    ok="#2F6B4F", warn="#B23A33", danger="#C2302A", link="#2A4A8A", seal="#C2302A",
    user_bg="#F6F8FB", user_text="#1E2228", user_border="1px solid #C9D3E6",
    tool_bg="transparent", tool_hover="#F6F8FB",
    note_bg="#FFFFFF", note_border="#C9D3E6", note_muted="#555D66",
    composer_bg="#FFFFFF", composer_border="#C9D3E6",
    stop_bg="#1E2228", stop_text="#FFFFFF", stop_border="none", new_chat_bg="#FFFFFF",
    ui_font=("Segoe UI",) + SANS, title_font=SERIF, label_font=SERIF,
    title_size=21, radius=6, radius_sm=6, bubble_radius=6, bubble_tail=6,
    tool_style="exhibit", show_dates=True,
)

CONSOLE = Theme(
    id="console", name="蓝屏", summary="PowerShell 蓝的终端，没有侧栏", dark=True,
    ground="#012456", sidebar="#012456", sidebar_rule="#1D4A8A", panel="#012456",
    surface="#0A2C62", input_bg="#0A2C62", raised="#0B3470", nav_selected="#0B3470",
    border="#1D4A8A", border_strong="#2B5596",
    text="#EEEDF0", muted="#A9B8D3", faint="#7F93B8",
    accent="#F2D45C", accent_hover="#F7E07E", on_accent="#012456", focus="#F2D45C", selection="#2B5596",
    ok="#7CD992", warn="#F2D45C", danger="#FF9A8F", link="#9CC8FF", seal="#FF9A8F",
    user_bg="transparent", user_text="#EEEDF0", user_border="none",
    tool_bg="transparent", tool_hover="#06306B",
    note_bg="transparent", note_border="transparent", note_muted="#A9B8D3",
    composer_bg="transparent", composer_border="transparent",
    stop_bg="transparent", stop_text="#EEEDF0", stop_border="1px solid #2B5596", new_chat_bg="transparent",
    ui_font=MONO, prose_font=MONO, title_font=MONO, label_font=MONO,
    prose_size=14, prose_line_height=175, title_size=14, title_weight=600,
    radius=4, radius_sm=4, bubble_radius=0, bubble_tail=0,
    nav="none", tool_style="console", user_style="prompt", send_shape="text", column_width=860,
)

FOOTNOTE = Theme(
    id="footnote", name="脚注", summary="阅读优先，工具调用写成脚注", dark=False,
    ground="#F6F7F4", sidebar="#F6F7F4", sidebar_rule="transparent", panel="#F6F7F4",
    surface="#FFFFFF", input_bg="#FFFFFF", raised="#ECEEE8", nav_selected="#ECEEE8",
    border="#E2E5E0", border_strong="#D5D9D2",
    text="#1E2329", muted="#5A6168", faint="#6B7178",
    accent="#2F58CC", accent_hover="#3D67DB", on_accent="#FFFFFF", focus="#2F58CC", selection="#CCD8F5",
    ok="#2F7D5B", warn="#2F58CC", danger="#B03A2E", link="#2F58CC", seal="#B03A2E",
    user_bg="#ECEEE8", user_text="#1E2329", user_border="none",
    tool_bg="transparent", tool_hover="#EEF0EB",
    note_bg="transparent", note_border="transparent", note_muted="#5A6168",
    composer_bg="#FFFFFF", composer_border="#E2E5E0",
    stop_bg="#1E2329", stop_text="#FFFFFF", stop_border="none", new_chat_bg="transparent",
    prose_font=SERIF, title_font=SERIF,
    prose_size=16, prose_line_height=185, title_size=26, title_weight=600,
    radius=16, radius_sm=12, bubble_radius=16, bubble_tail=16,
    nav="rail", tool_style="footnote", column_width=640, feed_spacing=16,
)

THEMES = {theme.id: theme for theme in (YOLK, FRIDGE, DOCKET, CONSOLE, FOOTNOTE)}
DEFAULT_THEME = YOLK.id


def get_theme(theme_id) -> Theme:
    return THEMES.get(str(theme_id or ""), THEMES[DEFAULT_THEME])


_REGULAR_STYLES = {"regular", "normal", "book", "roman", "medium", "text"}
_usable_cache = {}


def usable_families(families) -> tuple:
    """Skip installed families that cannot draw regular text, such as a lone Heavy weight.

    Otherwise Qt matches that family first and every paragraph comes out in Heavy.
    Families that are not installed are kept; Qt simply moves on to the next one.
    """
    families = tuple(families)
    if families in _usable_cache:
        return _usable_cache[families]
    try:
        from PySide6.QtGui import QFontDatabase
        installed = set(QFontDatabase.families())
    except Exception:
        return families
    keep = []
    for name in families:
        if name in installed:
            styles = {style.lower() for style in QFontDatabase.styles(name)}
            if styles and not styles & _REGULAR_STYLES:
                continue
        keep.append(name)
    result = tuple(keep) or families
    if installed:
        _usable_cache[families] = result
    return result


def font_families(families) -> str:
    return ", ".join(f'"{name}"' for name in usable_families(families))


def build_palette(t: Theme) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.Window: t.ground, QPalette.WindowText: t.text, QPalette.Base: t.input_bg,
        QPalette.AlternateBase: t.raised, QPalette.Text: t.text, QPalette.Button: t.surface,
        QPalette.ButtonText: t.text, QPalette.BrightText: t.accent, QPalette.Highlight: t.selection,
        QPalette.HighlightedText: t.text, QPalette.ToolTipBase: t.surface, QPalette.ToolTipText: t.text,
        QPalette.PlaceholderText: t.faint, QPalette.Link: t.link, QPalette.LinkVisited: t.link,
    }
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for role, colour in roles.items():
            palette.setColor(group, role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(t.faint))
    return palette


def build_stylesheet(t: Theme) -> str:
    ui = font_families(t.ui_font)
    prose = font_families(t.prose_font)
    title = font_families(t.title_font)
    label = font_families(t.label_font)
    mono = font_families(t.mono_font)
    round_send = t.send_shape == "round"
    door_border = f"1px solid {t.border}" if t.door else "none"
    door_radius = t.radius if t.door else 0
    pill = t.tool_style == "pill"
    return f"""
QWidget {{ color: {t.text}; font-family: {ui}; font-size: {t.font_size}px; }}
QMainWindow, QDialog, QMessageBox, QInputDialog {{ background: {t.ground}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; padding: 5px 8px; }}

QFrame#sidebar {{ background: {t.sidebar}; border: none; border-right: 1px solid {t.sidebar_rule}; }}
QFrame#rail {{ background: {t.sidebar}; border: none; border-right: 1px solid {t.sidebar_rule}; }}
QLabel#brand {{ font-weight: 700; font-size: {t.font_size + 1}px; padding: 4px 2px; }}
QPushButton#newChat {{ background: {t.new_chat_bg}; border: 1px solid {t.border}; border-radius: {t.radius_sm}px; min-height: 42px; padding: 0; }}
QPushButton#newChat:hover, QPushButton#projectSwitch:hover {{ background: {t.raised}; }}
QPushButton#projectSwitch {{ background: transparent; border: none; border-radius: {t.radius_sm}px; min-height: 50px; padding: 0; }}
QLabel#projectName {{ font-weight: 600; }}
QLabel#hint, QLabel#projectPath {{ color: {t.faint}; font-size: 12px; }}
QListWidget#historyList {{ background: transparent; border: none; outline: none; padding: 0; }}
QToolButton#navIcon {{ background: transparent; border: none; border-radius: {t.radius_sm}px; padding: 0; }}
QToolButton#navIcon:hover {{ background: {t.raised}; }}
QToolButton#avatar {{ background: {t.user_bg if t.user_bg != "transparent" else t.surface}; color: {t.user_text}; border: {t.user_border}; border-radius: 16px; padding: 0; font-weight: 700; font-size: 13px; }}

QFrame#mainPanel {{ background: {t.panel}; border: {door_border}; border-radius: {door_radius}px; }}
QLabel#chatTitle {{ font-family: {title}; font-size: {t.title_size}px; font-weight: {t.title_weight}; }}
QLabel#titlePrefix {{ color: {t.faint}; font-family: {title}; font-size: {t.title_size}px; }}
QLabel#titleSuffix {{ color: {t.accent}; font-family: {title}; font-size: {t.title_size}px; }}
QToolButton#ghost {{ background: transparent; border: none; color: {t.muted}; border-radius: {t.radius_sm}px; padding: 0 10px; min-height: 36px; }}
QToolButton#ghost:hover {{ background: {t.raised}; color: {t.text}; }}
QToolButton#ghost::menu-indicator, QToolButton#chip::menu-indicator {{ image: none; width: 0px; }}

QScrollArea#chatFeed {{ background: transparent; border: none; }}
QWidget#feedContent, QWidget#feedColumn, QWidget#pageContent {{ background: transparent; }}
QFrame#userBubble {{ background: {t.user_bg}; color: {t.user_text}; border: {t.user_border};
    border-top-left-radius: {t.bubble_radius}px; border-top-right-radius: {t.bubble_radius}px;
    border-bottom-left-radius: {t.bubble_radius}px; border-bottom-right-radius: {t.bubble_tail}px; }}
QFrame#assistantMessage QTextBrowser {{ font-family: {prose}; font-size: {t.prose_size}px; }}
QFrame#userBubble QTextBrowser, QFrame#userPrompt QTextBrowser {{ font-family: {ui}; font-size: {t.font_size + 1}px; }}
QFrame#userBubble QTextBrowser {{ color: {t.user_text}; }}
QFrame#userPrompt, QFrame#assistantMessage {{ background: transparent; border: none; }}
QLabel#messageAuthor {{ color: {t.faint}; font-size: 12px; }}
QLabel#promptMark {{ color: {t.accent}; font-family: {mono}; }}

QFrame#toolRow {{ background: transparent; border: none; }}
QFrame#toolRow[pending="true"][variant="dot"] {{ background: {t.note_bg}; border: 1px solid {t.note_border}; border-radius: {t.radius}px; }}
QFrame#toolRow[pending="true"][variant="exhibit"] {{ background: {t.note_bg}; border: 1px solid {t.note_border}; border-radius: {t.radius_sm}px; }}
QPushButton#toolLine {{ background: {t.tool_bg}; border: none; border-radius: {t.radius_sm if pill else 6}px; min-height: 40px; padding: 0; text-align: left; }}
QPushButton#toolLine:hover {{ background: {t.tool_hover}; }}
QPushButton#toolLine[variant="exhibit"] {{ border-bottom: 1px solid {t.border}; border-radius: 0; }}
QFrame#toolRow[pending="true"] QPushButton#toolLine {{ background: transparent; border: none; }}
QPushButton#toolLine[variant="footnote"] QLabel {{ font-size: 13px; }}
QLabel#toolAction {{ color: {t.text}; }}
QLabel#toolSubject, QLabel#toolStatus {{ color: {t.muted}; }}
QLabel#toolStatus[tone="warn"] {{ color: {t.warn}; }}
QLabel#toolStatus[tone="danger"] {{ color: {t.danger}; }}
QLabel#exhibitLabel {{ color: {t.accent}; font-family: {label}; font-weight: 700; letter-spacing: 1px; }}
QLabel#footnoteNumber {{ color: {t.accent}; font-weight: 700; }}
QFrame#footRule {{ background: {t.border_strong}; border: none; }}
QPlainTextEdit#toolDetails, QPlainTextEdit#approvalDetail {{ background: {t.surface if not t.dark else t.raised}; color: {t.muted};
    border: 1px solid {t.border}; border-radius: {min(t.radius_sm, 10)}px; font-family: {mono}; font-size: 12px; padding: 4px 6px; }}

QFrame#approvalPanel {{ background: transparent; border: none; }}
QFrame#approvalPanel[variant="pill"] {{ background: {t.note_bg}; border: 1px solid {t.note_border}; border-radius: 6px; margin-top: 9px; }}
QLabel#approvalHint {{ color: {t.note_muted}; font-size: 13px; }}
QLabel#approvalHeading {{ color: {t.accent}; font-weight: 700; }}
QLabel#approvalQuestion {{ color: {t.text}; }}
QPushButton#primary {{ background: {t.accent}; color: {t.on_accent}; border: none; border-radius: {t.radius_sm}px; padding: 0 18px; min-height: 36px; font-weight: 700; }}
QPushButton#primary:hover {{ background: {t.accent_hover}; }}
QPushButton#primary:disabled {{ background: {t.raised}; color: {t.faint}; }}
QPushButton#secondary {{ background: transparent; color: {t.text}; border: 1px solid {t.border_strong}; border-radius: {t.radius_sm}px; padding: 0 16px; min-height: 36px; }}
QPushButton#secondary:hover {{ background: {t.raised}; }}

QFrame#composerDock {{ background: transparent; border: none; border-top: 1px solid {t.border if t.nav == "none" else "transparent"}; }}
QFrame#composerBox {{ background: {t.composer_bg}; border: 1px solid {t.composer_border}; border-radius: {t.radius if t.radius > 6 else t.radius_sm}px; }}
QFrame#composerBox[focus="true"] {{ border-color: {t.focus if t.nav != "none" else t.composer_border}; }}
QTextEdit#composerInput {{ background: transparent; border: none; padding: 0; font-size: {t.font_size + 1}px; selection-background-color: {t.selection}; }}
QToolButton#chip {{ background: transparent; border: none; color: {t.muted}; border-radius: {t.radius_sm}px; padding: 0 8px; min-height: 34px; font-size: 13px; }}
QToolButton#chip:hover {{ background: {t.raised}; color: {t.text}; }}
QPushButton#actionButton {{ border: none; border-radius: {"20px" if round_send else f"{t.radius_sm}px"}; padding: {"0" if round_send else "0 14px"}; min-height: {40 if round_send else 34}px; font-size: 13px; font-weight: 700; }}
QPushButton#actionButton[state="send"] {{ background: {t.accent}; color: {t.on_accent}; }}
QPushButton#actionButton[state="send"]:hover {{ background: {t.accent_hover}; }}
QPushButton#actionButton[state="stop"] {{ background: {t.stop_bg}; color: {t.stop_text}; border: {t.stop_border}; }}
QLabel#statusLine, QLabel#muted {{ color: {t.muted}; }}
QLabel#statusLine {{ font-size: 12px; }}

QLabel#pageTitle {{ font-family: {title}; font-size: 20px; font-weight: 700; }}
QLabel#sectionLabel {{ color: {t.faint}; font-size: 12px; padding: 10px 0 2px; }}
QLabel#balance {{ font-size: 30px; font-weight: 700; }}
QPushButton, QToolButton {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; border-radius: {t.radius_sm}px; padding: 6px 12px; }}
QPushButton:hover, QToolButton:hover {{ background: {t.raised}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t.faint}; }}
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QListWidget {{ background: {t.input_bg}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: {t.radius_sm}px; padding: 6px 8px; selection-background-color: {t.selection}; selection-color: {t.text}; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {t.focus}; }}
QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; selection-background-color: {t.raised}; selection-color: {t.text}; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {t.raised}; color: {t.text}; }}
QFrame#planCard {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {t.radius_sm + 2}px; }}
QFrame#planCard[featured="true"] {{ border: 1px solid {t.accent}; }}
QToolButton#styleCard {{ background: transparent; border: 1px solid {t.border}; border-radius: {t.radius_sm}px; padding: 8px 6px; font-size: 12px; }}
QToolButton#styleCard:hover {{ background: {t.raised}; }}
QToolButton#styleCard:checked {{ border: 2px solid {t.accent}; }}

QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; padding: 4px; }}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.raised}; }}
QMenu::item:disabled {{ color: {t.faint}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border_strong}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border_strong}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""


def paint_theme_preview(t: Theme, width: int = 132, height: int = 84) -> QPixmap:
    """A small picture of the chat window in this style, used on the style cards in Settings."""
    scale = 2
    pixmap = QPixmap(width * scale, height * scale)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(scale, scale)
    outline = QPen(QColor(t.border_strong))
    outline.setWidthF(1)
    p.setPen(outline)
    p.setBrush(QColor(t.ground))
    p.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 8, 8)
    p.setPen(Qt.NoPen)
    left = 6.0
    if t.nav == "sidebar":
        p.setBrush(QColor(t.sidebar))
        p.drawRoundedRect(QRectF(1, 1, 34, height - 2), 7, 7)
        p.setBrush(QColor(t.nav_selected if t.nav_selected != t.sidebar else t.raised))
        p.drawRoundedRect(QRectF(6, 20, 24, 6), 2, 2)
        p.setBrush(QColor(t.faint))
        for row in range(3):
            p.drawRoundedRect(QRectF(8, 31 + row * 8, 18 - row * 3, 2.5), 1, 1)
        left = 40.0
    elif t.nav == "rail":
        p.setBrush(QColor(t.faint))
        for row in range(4):
            p.drawEllipse(QPointF(9, 14 + row * 11), 2.2, 2.2)
        left = 18.0
    panel = QRectF(left, 5, width - left - 5, height - 10)
    if t.door:
        p.setBrush(QColor(t.panel))
        p.setPen(QPen(QColor(t.border)))
        p.drawRoundedRect(panel, 7, 7)
        p.setPen(Qt.NoPen)
    content = panel.adjusted(8, 6, -8, -6)
    p.setBrush(QColor(t.text))
    p.drawRoundedRect(QRectF(content.left(), content.top() + 1, 30, 3), 1.5, 1.5)
    if t.user_style == "bubble":
        p.setBrush(QColor(t.user_bg))
        p.drawRoundedRect(QRectF(content.right() - 34, content.top() + 10, 34, 9), 4, 4)
    else:
        p.setBrush(QColor(t.accent))
        p.drawRect(QRectF(content.left(), content.top() + 13, 3, 3))
        p.setBrush(QColor(t.text))
        p.drawRoundedRect(QRectF(content.left() + 6, content.top() + 13, 34, 3), 1.5, 1.5)
    p.setBrush(QColor(t.muted))
    for row, length in enumerate((0.9, 0.75, 0.55)):
        p.drawRoundedRect(QRectF(content.left(), content.top() + 25 + row * 7, content.width() * length, 2.5), 1, 1)
    box = QRectF(content.left(), content.bottom() - 11, content.width(), 11)
    if t.composer_bg != "transparent":
        p.setBrush(QColor(t.composer_bg))
        p.setPen(QPen(QColor(t.composer_border if t.composer_border != "transparent" else t.border)))
        p.drawRoundedRect(box, 5, 5)
        p.setPen(Qt.NoPen)
    p.setBrush(QColor(t.accent))
    p.drawEllipse(QPointF(box.right() - 6, box.center().y()), 3.2, 3.2)
    p.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap
