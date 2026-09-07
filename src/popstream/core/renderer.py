from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from popstream.core.icons import icon_pixmap
from popstream.core.profile import ButtonSlot
from popstream.core.specs import DeviceSpec
from popstream.ui.theme import C, contrast_on


@dataclass
class RuntimeVisual:
    title: str | None = None
    image: QImage | None = None
    flash: str = ""  # "", "alert", "ok"
    tint: str = ""  # "", "danger", "ok"


def render_key(
    spec: DeviceSpec,
    slot: ButtonSlot,
    runtime: RuntimeVisual | None,
    default_icon: QPixmap | None,
    icon_kind: str = "grid",
) -> QImage:
    size = max(64, spec.key_size if spec.has_lcd else 72)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    custom = (slot.background or "").strip()
    fill = QColor(custom) if custom else QColor("#141414")
    if not fill.isValid():
        fill = QColor("#141414")
        custom = ""
    image.fill(fill)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), size * 0.12, size * 0.12)
    painter.setClipPath(clip)

    runtime = runtime or RuntimeVisual()
    ink = (slot.text_color or "").strip()
    ink_q = QColor(ink)
    if not ink_q.isValid():
        ink = ""
    danger = runtime.tint == "danger"
    ok = runtime.tint == "ok"
    icon_color = C.danger if danger else C.ok if ok else (ink or (contrast_on(custom) if custom else C.accent))
    title_color = ink or (contrast_on(custom) if custom else C.text)

    if runtime.image is not None and not runtime.image.isNull():
        painter.drawImage(image.rect(), runtime.image)
    elif slot.icon_path:
        pix = QPixmap(slot.icon_path)
        if not pix.isNull():
            if danger:
                pix = _colorize_pixmap(pix, C.danger)
            elif ok:
                pix = _colorize_pixmap(pix, C.ok)
            painter.drawPixmap(image.rect(), pix)
        elif not slot.empty:
            _draw_icon(painter, icon_pixmap(icon_kind, size, icon_color), size)
    elif not slot.empty:
        pix = icon_pixmap(icon_kind, size, icon_color)
        if pix is not None:
            _draw_icon(painter, pix, size)
    elif not custom:
        painter.fillRect(image.rect(), QColor("#161616"))

    title = runtime.title if runtime.title is not None else slot.title
    if slot.show_title and title:
        _draw_title(painter, title, size, slot.font_size, slot.title_align, title_color)

    if runtime.flash == "alert":
        painter.fillRect(image.rect(), QColor(200, 40, 40, 90))
        painter.drawPixmap(size // 4, size // 4, icon_pixmap("alert", size // 2))
    elif runtime.flash == "ok":
        painter.fillRect(image.rect(), QColor(40, 160, 80, 80))
        painter.drawPixmap(size // 4, size // 4, icon_pixmap("check", size // 2))

    painter.end()
    return image


def _colorize_pixmap(pixmap: QPixmap, color: str) -> QPixmap:
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(color))
    painter.end()
    return tinted


def _draw_icon(painter: QPainter, pixmap: QPixmap, size: int) -> None:
    margin = int(size * 0.14)
    target = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.drawPixmap(target.toRect(), pixmap)


def _draw_title(
    painter: QPainter,
    text: str,
    size: int,
    font_size: int,
    align: str,
    color: str,
) -> None:
    bar_h = int(size * 0.32)
    if align == "top":
        bar = QRectF(0, 0, size, bar_h)
        grad = QLinearGradient(0, 0, 0, bar_h)
        grad.setColorAt(0, QColor(0, 0, 0, 190))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        v_align = Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
    elif align == "middle":
        bar = QRectF(0, size * 0.35, size, size * 0.3)
        painter.fillRect(bar, QColor(0, 0, 0, 120))
        v_align = Qt.AlignmentFlag.AlignCenter
    else:
        bar = QRectF(0, size - bar_h, size, bar_h)
        grad = QLinearGradient(0, size - bar_h, 0, size)
        grad.setColorAt(0, QColor(0, 0, 0, 0))
        grad.setColorAt(1, QColor(0, 0, 0, 200))
        painter.fillRect(bar, grad)
        v_align = Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter

    if align != "middle":
        painter.fillRect(bar, grad)

    font = QFont("Inter, Segoe UI, Sans Serif")
    font.setPixelSize(max(9, int(font_size * size / 72)))
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QPen(QColor(color)))
    painter.drawText(bar.adjusted(4, 2, -4, -4), int(v_align | Qt.TextFlag.TextWordWrap), text)
