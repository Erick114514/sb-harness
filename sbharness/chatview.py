"""Chat pieces for SB Harness: messages, tool calls with inline approval, and the composer.

Every piece takes the active Theme, so switching styles rebuilds the conversation with the
same timeline but a different presentation.
"""
from __future__ import annotations

import html
import json
import uuid

from PySide6 import QtGui
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStyle,
    QStyledItemDelegate, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from .engine import replace_addresses_with_genshin
from .themes import Theme, get_theme, usable_families

CJK_FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "DengXian", "Segoe UI"]

TOOL_LABELS = {
    "read_file": "读取文件", "write_file": "写入文件", "make_pdf": "生成 PDF",
    "search_web": "搜索网页", "read_web": "读取网页", "run_command": "运行命令",
    "wallet_balance": "查询钱包", "wallet_adjust": "钱包记账",
}
TOOL_STATUS = {
    "running": "运行中", "waiting": "等待确认", "done": "完成", "error": "失败",
    "denied": "已拒绝", "cancelled": "已取消",
}
STATUS_TONE = {
    "running": "muted", "waiting": "warn", "done": "ok", "error": "danger",
    "denied": "danger", "cancelled": "muted",
}
_DIGITS = "零一二三四五六七八九"


def chinese_number(number: int) -> str:
    if 0 <= number < 10:
        return _DIGITS[number]
    if 10 <= number < 100:
        tens, ones = divmod(number, 10)
        return ("" if tens == 1 else _DIGITS[tens]) + "十" + (_DIGITS[ones] if ones else "")
    return str(number)


def literal_tooltip(text) -> str:
    """Qt renders a tooltip as HTML when its first line looks like markup; escape such text so
    tool arguments always show literally and can never load an image (file:// or UNC)."""
    text = str(text or "")
    if QtGui.Qt.mightBeRichText(text):
        return "<p style='white-space:pre-wrap'>" + html.escape(text, quote=False) + "</p>"
    return text


def open_web_link(url):
    """Only https links leave the app. Any other target (file:, UNC, search-ms:, a drive path)
    would be handed to the Windows shell, so it is ignored."""
    if url.scheme().lower() == "https" and url.host():
        QDesktopServices.openUrl(url)


def strip_images(document):
    """Swap every image for its alt text before the first layout: Qt opens file:// and UNC
    image paths by itself, and Windows then offers the user's NTLM credentials to that host."""
    cursor = QTextCursor(document)
    while True:
        cursor = document.find(chr(0xFFFC), cursor)  # QChar::ObjectReplacementCharacter
        if cursor.isNull():
            return
        image = cursor.charFormat()
        if image.isImageFormat():
            alt = image.stringProperty(QTextFormat.Property.ImageAltText).strip()
            cursor.insertText(f"[图片：{alt}]" if alt else "[图片]", QTextCharFormat())


def repolish(widget):
    """Re-apply the stylesheet after a dynamic property used in a selector changes."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _polyline(painter, points):
    painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))


def _draw_icon(painter, kind, colour):
    """Draw a 24x24 line icon. Symbol glyphs such as ⚙ turn into colour emoji on Windows."""
    if kind == "plus":
        _polyline(painter, [(12, 5), (12, 19)])
        _polyline(painter, [(5, 12), (19, 12)])
    elif kind == "chevron_down":
        _polyline(painter, [(7, 10), (12, 15), (17, 10)])
    elif kind == "chevron_right":
        _polyline(painter, [(10, 7), (15, 12), (10, 17)])
    elif kind == "check":
        _polyline(painter, [(5, 12.5), (9.5, 17), (19, 7.5)])
    elif kind == "cross":
        _polyline(painter, [(6.5, 6.5), (17.5, 17.5)])
        _polyline(painter, [(17.5, 6.5), (6.5, 17.5)])
    elif kind == "dash":
        _polyline(painter, [(7, 12), (17, 12)])
    elif kind == "arrow_up":
        _polyline(painter, [(12, 18.5), (12, 6)])
        _polyline(painter, [(6.5, 11.5), (12, 6), (17.5, 11.5)])
    elif kind == "stop":
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2.5, 2.5)
    elif kind == "list":
        _polyline(painter, [(5, 7), (19, 7)])
        _polyline(painter, [(5, 12), (19, 12)])
        _polyline(painter, [(5, 17), (14, 17)])
    elif kind == "folder":
        _polyline(painter, [(3.5, 6.5), (9, 6.5), (11, 8.7), (20.5, 8.7), (20.5, 18.5), (3.5, 18.5), (3.5, 6.5)])
    elif kind == "book":
        path = QPainterPath()
        path.moveTo(3, 6.5)
        path.cubicTo(5.5, 5, 8.5, 5, 12, 7)
        path.cubicTo(15.5, 5, 18.5, 5, 21, 6.5)
        path.lineTo(21, 19)
        path.cubicTo(18.5, 17.5, 15.5, 17.5, 12, 19.5)
        path.cubicTo(8.5, 17.5, 5.5, 17.5, 3, 19)
        path.closeSubpath()
        painter.drawPath(path)
        _polyline(painter, [(12, 7), (12, 19.5)])
    elif kind == "shield":
        path = QPainterPath()
        path.moveTo(12, 3.5)
        path.lineTo(19, 6.3)
        path.lineTo(19, 11.7)
        path.cubicTo(19, 15.8, 16.1, 19, 12, 20.5)
        path.cubicTo(7.9, 19, 5, 15.8, 5, 11.7)
        path.lineTo(5, 6.3)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "sparkle":
        painter.drawPolygon(QPolygonF([
            QPointF(12, 4), QPointF(13.9, 9.1), QPointF(19, 11), QPointF(13.9, 12.9),
            QPointF(12, 18), QPointF(10.1, 12.9), QPointF(5, 11), QPointF(10.1, 9.1),
        ]))
    elif kind == "sliders":
        for x1, y, x2 in ((4, 8, 13), (17, 8, 20), (4, 16, 7), (11, 16, 20)):
            _polyline(painter, [(x1, y), (x2, y)])
        painter.drawEllipse(QPointF(15, 8), 2, 2)
        painter.drawEllipse(QPointF(9, 16), 2, 2)
    elif kind == "clock":
        painter.drawEllipse(QPointF(12, 12), 7.5, 7.5)
        _polyline(painter, [(12, 8), (12, 12), (15, 14)])
    elif kind == "dots":
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        for x in (6, 12, 18):
            painter.drawEllipse(QPointF(x, 12), 1.7, 1.7)


def icon_pixmap(kind, colour, size=18, stroke=1.8) -> QPixmap:
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size * scale / 24, size * scale / 24)
    pen = QPen(QColor(colour))
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    _draw_icon(painter, kind, QColor(colour))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def line_icon(kind, colour="#cfcfcf", size=20, stroke=1.8) -> QIcon:
    return QIcon(icon_pixmap(kind, colour, size, stroke))


def intensity_icon(level, colour, dim, size=16) -> QIcon:
    """Four rising bars; the first `level + 1` are lit (平静 lights one, 重度 lights all four)."""
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(scale * size / 16, scale * size / 16)
    painter.setPen(Qt.NoPen)
    for index, height in enumerate((5, 7, 9, 12)):
        painter.setBrush(QColor(colour if index <= level else dim))
        painter.drawRoundedRect(QRectF(2 + index * 3.4, 14 - height, 2.4, height), 0.8, 0.8)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


class ElidedLabel(QLabel):
    """One-line label that elides long text instead of forcing its parent wider."""

    def __init__(self, text="", parent=None, mode=Qt.ElideRight):
        super().__init__(parent)
        self._full_text = ""
        self._mode = mode
        # Titles, paths and tool arguments are data: never interpret them as HTML.
        self.setTextFormat(Qt.PlainText)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text or "")
        self.setToolTip(literal_tooltip(self._full_text))
        self.updateGeometry()
        self._apply_elision()

    def text(self):
        return self._full_text

    def clear(self):
        self.setText("")

    def sizeHint(self):
        self.ensurePolished()
        margins = self.contentsMargins()
        width = self.fontMetrics().horizontalAdvance(self._full_text)
        width += margins.left() + margins.right() + 2 * self.margin() + 2
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elision()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_elision()

    def _apply_elision(self):
        shown = self._full_text
        width = self.contentsRect().width() - 2 * self.margin()
        if self.isVisible() and width > 0:
            shown = self.fontMetrics().elidedText(self._full_text, self._mode, width)
        QLabel.setText(self, shown)


class NavItemDelegate(QStyledItemDelegate):
    """Paints sidebar conversations: one elided line, an optional date, a rounded highlight.

    The 1px-wide size hint makes rows take the view's width, so long titles elide
    instead of producing a sideways scrollbar.
    """

    DATE_ROLE = Qt.UserRole + 1

    def __init__(self, parent=None, theme: Theme | None = None):
        super().__init__(parent)
        self.theme = theme or get_theme(None)

    def sizeHint(self, option, index):
        return QSize(1, 40)

    def paint(self, painter, option, index):
        t = self.theme
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(0, 2, 0, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t.nav_selected if selected else t.raised))
            painter.drawRoundedRect(QRectF(rect), t.radius_sm, t.radius_sm)
        font = QFont(option.font)
        font.setBold(selected and t.show_dates)
        left, right = rect.left() + 12, rect.right() - 12
        date = index.data(self.DATE_ROLE) if t.show_dates else None
        if date:
            date_font = QFont(option.font)
            date_font.setPixelSize(12)
            painter.setFont(date_font)
            width = QFontMetrics(date_font).horizontalAdvance(date)
            painter.setPen(QColor(t.faint))
            painter.drawText(QRect(right - width, rect.top(), width, rect.height()), Qt.AlignVCenter | Qt.AlignRight, date)
            right -= width + 10
        painter.setFont(font)
        painter.setPen(QColor(t.text if selected else t.muted))
        width = max(0, right - left)
        text = QFontMetrics(font).elidedText(str(index.data(Qt.DisplayRole) or ""), Qt.ElideRight, width)
        painter.drawText(QRect(left, rect.top(), width, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()


class MessageBubble(QFrame):
    def __init__(self, role: str, content: str, theme: Theme | None = None):
        super().__init__()
        theme = theme or get_theme(None)
        if role == "assistant":
            content = replace_addresses_with_genshin(content)
        self.role, self.content, self.theme = role, content, theme
        self.prompt = role == "user" and theme.user_style == "prompt"
        self.bubble = role == "user" and not self.prompt
        # Room left under the last line so the text never clips; a bubble's own padding covers most of it.
        self._slack = 16 if role == "assistant" else 4
        self.setObjectName("userBubble" if self.bubble else ("userPrompt" if self.prompt else "assistantMessage"))
        self.setSizePolicy(QSizePolicy.Preferred if self.bubble else QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*((16, 10, 16, 10) if self.bubble else (0, 0, 0, 0)))
        layout.setSpacing(0)
        # Kept for copying and tests; the styles show who is speaking through layout instead.
        self.author = QLabel("你" if role == "user" else "SB Harness")
        self.author.setObjectName("messageAuthor")
        self.author.hide()
        layout.addWidget(self.author)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.prompt_mark = QLabel(">")
        self.prompt_mark.setObjectName("promptMark")
        self.prompt_mark.setVisible(self.prompt)
        row.addWidget(self.prompt_mark, 0, Qt.AlignTop)
        self.browser = QTextBrowser()
        # Links never open by themselves (no setSource, no shell); only https gets through.
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(open_web_link)
        self.browser.setFrameShape(QFrame.NoFrame)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.browser.setStyleSheet("background: transparent; border: 0; padding: 0;")
        document = QTextDocument(self.browser)
        families = list(usable_families(theme.prose_font if role == "assistant" else theme.ui_font))
        families += [name for name in CJK_FONT_FAMILIES if name not in families]
        body_font = QFont()
        body_font.setFamilies(families)
        body_font.setPixelSize(theme.prose_size if role == "assistant" else theme.font_size + 1)
        document.setDefaultFont(body_font)
        document.setDocumentMargin(0)
        # Do not let model-provided raw HTML become an active embedded object.
        document.setMarkdown(content.replace("<", "&lt;").replace(">", "&gt;"))
        strip_images(document)
        self._set_line_height(document, theme.prose_line_height if role == "assistant" else 140)
        document.setTextWidth(-1)
        self._ideal_width = int(document.idealWidth() + 1)
        self.browser.setDocument(document)
        document.documentLayout().documentSizeChanged.connect(self._queue_document_fit)
        row.addWidget(self.browser, 1)
        layout.addLayout(row)
        self._fit_document()
        QTimer.singleShot(0, self._fit_document)

    @staticmethod
    def _set_line_height(document, percent):
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        block = QTextBlockFormat()
        block.setLineHeight(float(percent), QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(block)

    def sizeHint(self):
        hint = super().sizeHint()
        if self.bubble:
            margins = self.layout().contentsMargins()
            hint.setWidth(self._ideal_width + margins.left() + margins.right() + 6)
        return hint

    def _fit_document(self):
        document = self.browser.document()
        viewport_width = self.browser.viewport().width()
        if viewport_width <= 0:
            return
        document.setTextWidth(viewport_width)
        self.browser.setFixedHeight(max(20, int(document.size().height() + 0.999) + self._slack))

    def _queue_document_fit(self, *_):
        QTimer.singleShot(0, self._fit_document)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_document)

    def plain_text(self):
        return self.browser.toPlainText()


class MessageRow(QWidget):
    """A user bubble sits on the right at its natural width; everything else fills the column."""

    def __init__(self, bubble: MessageBubble):
        super().__init__()
        self.bubble = bubble
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if bubble.bubble:
            layout.addStretch(1)
            layout.addWidget(bubble)
        else:
            layout.addWidget(bubble, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.bubble.bubble:
            self.bubble.setMaximumWidth(max(160, int(self.width() * 0.78)))


class MessageComposer(QTextEdit):
    send_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preedit = False
        self.setAcceptRichText(False)
        self.setMinimumHeight(44)
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
        desired = int(self.document().size().height() + 0.999) + 12
        self.setFixedHeight(max(44, min(140, desired)))


class StatusMarker(QWidget):
    """The state of a tool call: a coloured dot, or a check, cross or clock."""

    GLYPHS = {"done": "check", "error": "cross", "denied": "cross", "waiting": "clock", "cancelled": "dash"}

    def __init__(self, theme: Theme, kind: str):
        super().__init__()
        self.theme, self.kind, self.status = theme, kind, "running"
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_status(self, status):
        self.status = status or ""
        self.update()

    def paintEvent(self, _event):
        t = self.theme
        tone = STATUS_TONE.get(self.status, "muted")
        colour = QColor({"ok": t.ok, "warn": t.warn, "danger": t.danger}.get(tone, t.faint))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.kind == "dot":
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(QRectF(4, 4, 8, 8))
        else:
            painter.scale(16 / 24, 16 / 24)
            pen = QPen(colour)
            pen.setWidthF(2.4)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            _draw_icon(painter, self.GLYPHS.get(self.status, "dots"), colour)
        painter.end()


class StampBadge(QWidget):
    """A rubber stamp: dashed while waiting, solid once approved or rejected."""

    TEXT = {"pending": "待批", "approved": "批准", "denied": "驳回"}

    def __init__(self, theme: Theme, diameter: int = 76):
        super().__init__()
        self.theme, self.state = theme, "pending"
        self.setFixedSize(diameter + 8, diameter + 8)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_state(self, state):
        self.state = state
        self.update()

    def paintEvent(self, _event):
        final = self.state != "pending"
        colour = QColor(self.theme.seal)
        if not final:
            colour.setAlphaF(0.75)
        radius = min(self.width(), self.height()) / 2 - 4
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(8 if self.state == "denied" else -12)
        pen = QPen(colour)
        pen.setWidthF(max(1.4, radius / 12) if final else 1.4)
        if not final:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), radius, radius)
        font = QFont()
        font.setFamilies(list(usable_families(self.theme.label_font)))
        font.setBold(True)
        font.setPixelSize(max(9, int(radius * (0.55 if final else 0.42))))
        painter.setFont(font)
        painter.drawText(QRectF(-radius, -radius, radius * 2, radius * 2), Qt.AlignCenter, self.TEXT.get(self.state, ""))
        painter.end()


class ApprovalPanel(QFrame):
    """Allow or reject a tool call right where it appears in the conversation."""

    decided = Signal(bool)

    def __init__(self, theme: Theme, kind: str):
        super().__init__()
        self.setObjectName("approvalPanel")
        self.setProperty("variant", kind)
        self.theme, self.kind = theme, kind
        self._done = False
        outer = QHBoxLayout(self)
        margins = {"pill": (16, 26, 16, 14), "footnote": (26, 0, 0, 2), "console": (0, 0, 0, 2)}
        outer.setContentsMargins(*margins.get(kind, (0, 2, 0, 0)))
        outer.setSpacing(12)
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        outer.addLayout(body, 1)
        console = kind == "console"
        self.heading = QLabel("确认")
        self.heading.setObjectName("approvalHeading")
        self.heading.setVisible(console)
        self.question = QLabel("是否执行此操作？")
        self.question.setObjectName("approvalQuestion")
        self.question.setVisible(console)
        verb = "批准" if kind == "exhibit" else "允许"
        self.hint = QLabel(f"{verb}后才会执行。")
        self.hint.setObjectName("approvalHint")
        self.hint.setWordWrap(True)
        self.hint.setVisible(not console)
        self.detail = QPlainTextEdit()
        self.detail.setObjectName("approvalDetail")
        self.detail.setReadOnly(True)
        self.detail.hide()
        for widget in (self.heading, self.question, self.detail, self.hint):
            body.addWidget(widget)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        if console:
            self.allow_button = QPushButton("[Y] 允许")
            self.deny_button = QPushButton("[N] 拒绝")
            self.allow_button.setObjectName("secondary")
            self.deny_button.setObjectName("secondary")
            default = QLabel("（默认值为“N”）")
            default.setObjectName("approvalHint")
            for widget in (self.allow_button, self.deny_button, default):
                buttons.addWidget(widget)
            buttons.addStretch(1)
        else:
            self.allow_button = QPushButton("批准" if kind == "exhibit" else "允许")
            self.deny_button = QPushButton("驳回" if kind == "exhibit" else "拒绝")
            self.allow_button.setObjectName("primary")
            self.deny_button.setObjectName("secondary")
            if kind in ("exhibit", "footnote"):
                buttons.addWidget(self.allow_button)
                buttons.addWidget(self.deny_button)
                buttons.addStretch(1)
            else:
                buttons.addStretch(1)
                buttons.addWidget(self.deny_button)
                buttons.addWidget(self.allow_button)
        body.addLayout(buttons)
        self.stamp = StampBadge(theme) if kind == "exhibit" else None
        if self.stamp is not None:
            outer.addWidget(self.stamp, 0, Qt.AlignVCenter)
        self.allow_button.clicked.connect(lambda: self._decide(True))
        self.deny_button.clicked.connect(lambda: self._decide(False))

    def _decide(self, approved):
        if self._done:
            return
        self._done = True
        self.allow_button.setEnabled(False)
        self.deny_button.setEnabled(False)
        self.decided.emit(approved)

    def set_detail(self, detail):
        lines = str(detail or "").splitlines()
        body = "\n".join(lines[1:]).strip()
        self.detail.setPlainText(body)
        self.detail.setVisible(bool(body))
        if body:
            self.detail.ensurePolished()
            rows = min(7, body.count("\n") + 1)
            self.detail.setFixedHeight(rows * self.detail.fontMetrics().lineSpacing() + 18)
        self._done = False
        self.allow_button.setEnabled(True)
        self.deny_button.setEnabled(True)
        if self.stamp is not None:
            self.stamp.set_state("pending")

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.kind != "pill":
            return
        # The note hangs on the fridge door by a magnet.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        centre = QPointF(self.width() / 2, 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.theme.accent))
        painter.drawEllipse(centre, 9, 9)
        painter.setBrush(QColor(255, 255, 255, 80))
        painter.drawEllipse(QPointF(centre.x() - 2.5, centre.y() - 3), 3.2, 2.2)
        painter.end()


class ToolRow(QFrame):
    """One line per tool call: state, action, subject and status. Click to see arguments and result."""

    def __init__(self, event: dict, theme: Theme | None = None, index: int = 1, first_in_group: bool = True):
        super().__init__()
        self.theme = theme or get_theme(None)
        self.kind = self.theme.tool_style
        self.index = index
        self.setObjectName("toolRow")
        self.setProperty("variant", self.kind)
        self.setProperty("pending", False)
        # Not named `event`: an instance attribute must never shadow a Qt virtual such as QObject.event().
        self.tool_event = {}
        self.call_id = event.get("call_id")
        self._title = ""
        self._status = ""
        self._status_key = ""
        self._open = False
        # Callbacks live in a dict: a callable instance attribute makes PySide look up virtual
        # overrides on the instance, which is how a shadowed name turns into a crash.
        self._callbacks = {}
        self.pending = False
        self.approval = None

        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(6)
        self.rule = QFrame()
        self.rule.setObjectName("footRule")
        self.rule.setFixedSize(48, 1)
        self.rule.setVisible(self.kind == "footnote" and first_in_group)
        self.outer.addWidget(self.rule)

        self.line = QPushButton()
        self.line.setObjectName("toolLine")
        self.line.setProperty("variant", self.kind)
        self.line.setCursor(Qt.PointingHandCursor)
        # Ignored: a long command must not make the chat column wider than the window.
        self.line.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.line.setMinimumHeight(40)
        self.line.clicked.connect(self.toggle_details)
        line_layout = QHBoxLayout(self.line)
        line_layout.setContentsMargins(*((14, 0, 12, 0) if self.kind == "pill" else (2, 0, 4, 0)))
        line_layout.setSpacing(14 if self.kind == "exhibit" else 10)
        self.marker = StatusMarker(self.theme, "dot" if self.kind == "dot" else "glyph")
        self.number = QLabel()
        if self.kind == "exhibit":
            self.number.setObjectName("exhibitLabel")
            self.number.setText("证物" + chinese_number(index))
        elif self.kind == "footnote":
            self.number.setObjectName("footnoteNumber")
            self.number.setText(str(index))
            self.number.setMinimumWidth(14)
        numbered = self.kind in ("exhibit", "footnote")
        self.marker.setVisible(not numbered)
        self.number.setVisible(numbered)
        self.action = QLabel()
        self.action.setObjectName("toolAction")
        # The tool name comes from the model; an unknown one is shown as given.
        self.action.setTextFormat(Qt.PlainText)
        self.subject = ElidedLabel(mode=Qt.ElideMiddle)
        self.subject.setObjectName("toolSubject")
        self.status_label = QLabel()
        self.status_label.setObjectName("toolStatus")
        self.status_label.setTextFormat(Qt.PlainText)
        self.stamp = StampBadge(self.theme, diameter=30) if self.kind == "exhibit" else None
        self.chevron = QLabel()
        self.chevron.setFixedSize(16, 16)
        parts = [self.marker, self.number, self.action, self.subject, self.status_label, self.stamp, self.chevron]
        for part in parts:
            if part is not None:
                part.setAttribute(Qt.WA_TransparentForMouseEvents)
        line_layout.addWidget(self.marker)
        line_layout.addWidget(self.number)
        line_layout.addWidget(self.action)
        line_layout.addWidget(self.subject, 1)
        line_layout.addWidget(self.status_label)
        if self.stamp is not None:
            self.stamp.hide()
            line_layout.addWidget(self.stamp)
        line_layout.addWidget(self.chevron)
        self.outer.addWidget(self.line)

        self.details = QPlainTextEdit()
        self.details.setObjectName("toolDetails")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        self.details.hide()
        self.outer.addWidget(self.details)
        self.update_event(event)
        self._update_chevron()

    def _status_text(self, key):
        if self.kind == "exhibit" and key == "waiting":
            return "待批"
        return TOOL_STATUS.get(key, key or "")

    def update_event(self, event):
        self.tool_event.update(event or {})
        merged = self.tool_event
        if not self.call_id:
            self.call_id = merged.get("call_id")
        name = TOOL_LABELS.get(merged.get("name"), merged.get("name") or "工具")
        args = merged.get("args") or {}
        subject = ""
        if isinstance(args, dict):
            subject = args.get("path") or args.get("query") or args.get("url") or args.get("command") or ""
        subject = str(subject)
        if len(subject) > 70:
            subject = subject[:67] + "…"
        self._status_key = str(merged.get("status") or "")
        self._status = self._status_text(self._status_key)
        self._title = f"{name} · {subject}" if subject else name
        self.action.setText(name)
        self.subject.setText(subject)
        self.status_label.setText(self._status)
        self.status_label.setProperty("tone", STATUS_TONE.get(self._status_key, "muted"))
        repolish(self.status_label)
        self.marker.set_status(self._status_key)
        self.line.setToolTip(literal_tooltip(self._title))
        decision = merged.get("approval")
        if self.stamp is not None and decision in ("approved", "denied"):
            self.stamp.set_state(decision)
            self.stamp.show()
        self.details.setPlainText(json.dumps({"参数": args, "结果": merged.get("result", "")}, ensure_ascii=False, indent=2))

    def title_text(self):
        return f"{self._title}    {self._status}"

    def plain_text(self):
        return self.title_text() + "\n" + (self.details.toPlainText() if self.details.isVisible() else "")

    def toggle_details(self):
        self._open = not self._open
        self.details.setVisible(self._open)
        self._update_chevron()

    def _update_chevron(self):
        self.chevron.setPixmap(icon_pixmap("chevron_down" if self._open else "chevron_right", self.theme.faint, 16))

    def is_waiting(self):
        return self._status_key == "waiting" and not self.pending

    def show_approval(self, detail, on_decide):
        if self.approval is None:
            self.approval = ApprovalPanel(self.theme, self.kind)
            self.approval.decided.connect(self._decided)
            self.outer.addWidget(self.approval)
        self._callbacks["decide"] = on_decide
        self.approval.set_detail(detail)
        self.approval.show()
        self.pending = True
        card = self.kind in ("dot", "exhibit")
        self.setProperty("pending", card)
        if card:
            self.outer.setContentsMargins(14, 4, 14, 14)
        repolish(self)
        repolish(self.line)

    def _decided(self, approved):
        callback = self._callbacks.get("decide")
        if callback is not None:
            callback(approved)

    def finish_approval(self, approved, cancelled=False):
        self.pending = False
        self._callbacks.pop("decide", None)
        if self.approval is not None:
            self.approval.hide()
        self.setProperty("pending", False)
        self.outer.setContentsMargins(0, 0, 0, 0)
        repolish(self)
        repolish(self.line)
        if not cancelled:
            decision = "approved" if approved else "denied"
            self.tool_event["approval"] = decision
            if self.stamp is not None:
                self.stamp.set_state(decision)
                self.stamp.show()


ToolCard = ToolRow


class ChatFeed(QScrollArea):
    """The conversation: one centred reading column shared by messages and tool calls."""

    def __init__(self, theme: Theme | None = None):
        super().__init__()
        self.theme = theme or get_theme(None)
        self.setObjectName("chatFeed")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.container.setObjectName("feedContent")
        self.outer = QHBoxLayout(self.container)
        self.outer.setContentsMargins(28, 0, 28, 0)
        self.outer.setSpacing(0)
        self.column = QWidget()
        self.column.setObjectName("feedColumn")
        self.outer.addStretch(1)
        self.outer.addWidget(self.column, 1000)
        self.outer.addStretch(1)
        self.items = QVBoxLayout(self.column)
        self.items.setContentsMargins(0, 12, 0, 24)
        # Short conversations sit just above the composer, like a chat that has scrolled to the end.
        self.items.addStretch(1)
        self.setWidget(self.container)
        self.entries = []
        self.cards = {}
        self._tool_count = 0
        # Follow new content while the reader is at the end; stay put once they scroll up.
        self._stick = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._keep_at_bottom)
        bar.valueChanged.connect(self._track_stick)
        self.set_theme(self.theme)

    def _keep_at_bottom(self, _minimum, maximum):
        if self._stick:
            self.verticalScrollBar().setValue(maximum)

    def _track_stick(self, value):
        self._stick = value >= self.verticalScrollBar().maximum() - 4

    def set_theme(self, theme: Theme):
        self.theme = theme
        self.column.setMaximumWidth(theme.column_width)
        self.items.setSpacing(theme.feed_spacing)

    def set_side_margin(self, margin):
        self.outer.setContentsMargins(margin, 0, margin, 0)

    def _insert(self, widget):
        self.items.addWidget(widget)
        self.entries.append(widget)
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        self._stick = True
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def add_message(self, role, content):
        bubble = MessageBubble(role, content, self.theme)
        self._insert(MessageRow(bubble))
        return bubble

    def add_tool_event(self, event):
        ident = event.get("call_id") or uuid.uuid4().hex
        row = self.cards.get(ident)
        if row is None:
            self._tool_count += 1
            first = not self.entries or not isinstance(self.entries[-1], ToolRow)
            row = ToolRow(event, self.theme, self._tool_count, first)
            self.cards[ident] = row
            self._insert(row)
        else:
            row.update_event(event)
        return row

    def waiting_row(self):
        for widget in reversed(self.entries):
            if isinstance(widget, ToolRow) and widget.is_waiting():
                return widget
        return None

    def add_standalone_approval(self, detail):
        """An approval request with no tool row to attach to (kept out of the saved timeline)."""
        first_line = (str(detail or "").splitlines() or [""])[0].strip().rstrip("：:")
        return self.add_tool_event({
            "call_id": "approval-" + uuid.uuid4().hex, "name": first_line or "需要确认的操作",
            "args": {}, "status": "waiting",
        })

    def clear(self):
        while self.items.count() > 1:
            item = self.items.takeAt(1)
            widget = item.widget()
            if widget:
                # Hide now: deleteLater only runs when control returns to the event loop,
                # and until then the old rows would still paint under the new ones.
                widget.hide()
                widget.deleteLater()
        self._stick = True
        self.entries.clear()
        self.cards.clear()
        self._tool_count = 0

    def toPlainText(self):
        lines = []
        for widget in self.entries:
            if isinstance(widget, ToolRow):
                lines.append(widget.title_text())
            else:
                for bubble in widget.findChildren(MessageBubble):
                    lines.extend([bubble.author.text(), bubble.plain_text()])
        return "\n".join(lines)
