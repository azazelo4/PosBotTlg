"""Ценники в PDF: одна страница = одна этикетка нужного размера.

PDF печатается на термопринтере (Xprinter и т. п.) через обычный драйвер:
размер страницы совпадает с размером этикетки, масштаб — 100 %.
На этикетке QR-код с артикулом — его сканирует касса в Telegram Mini App.
"""
import io
import logging
import os
from decimal import Decimal

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from config import LABEL_WIDTH_MM, LABEL_HEIGHT_MM, LABEL_TITLE
from utils.common import money

logger = logging.getLogger(__name__)

FONT_DIRS = [os.environ.get("LABEL_FONT_DIR", ""), "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
             "/usr/share/fonts/TTF", "C:/Windows/Fonts"]
FONT, FONT_BOLD = "LabelSans", "LabelSans-Bold"
METAL_SHORT = {'золото': 'Au', 'серебро': 'Ag', 'бижутерия': 'Биж.'}

_fonts_ready = False


def _register_fonts():
    """Кириллице нужен TTF-шрифт: стандартные шрифты PDF её не содержат."""
    global _fonts_ready
    if _fonts_ready:
        return
    for directory in FONT_DIRS:
        regular = os.path.join(directory, "DejaVuSans.ttf")
        bold = os.path.join(directory, "DejaVuSans-Bold.ttf")
        if directory and os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont(FONT, regular))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, bold))
            _fonts_ready = True
            return
    raise RuntimeError("Не найден шрифт DejaVuSans для ценников. Установите fonts-dejavu-core или задайте LABEL_FONT_DIR.")


def _fit(text, font, size, max_width, min_size):
    """Уменьшает шрифт, а если не помогает — обрезает текст с многоточием."""
    text = str(text or '')
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.25
    if pdfmetrics.stringWidth(text, font, size) > max_width:
        while text and pdfmetrics.stringWidth(text + "…", font, size) > max_width:
            text = text[:-1]
        text += "…"
    return text, size


def _wrap(text, font, size, max_width, max_lines):
    words, lines, current = str(text or '').split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        lines[-1], _ = _fit(lines[-1] + " …", font, size, max_width, size)
    return [(_fit(line, font, size, max_width, size)[0]) for line in lines]


def details_line(product):
    parts = []
    metal = METAL_SHORT.get(product.get('metal_type'), product.get('metal_type') or '')
    if product.get('fineness'):
        metal = f"{metal} {product['fineness']}".strip()
    if metal:
        parts.append(metal)
    if product.get('weight'):
        parts.append(f"{Decimal(str(product['weight'])).normalize():f} г")
    if product.get('size'):
        parts.append(f"р. {product['size']}")
    return " · ".join(parts)


def _draw_label(c, product, w, h):
    k = h / (30 * mm)  # масштаб шрифтов: базовый макет рассчитан на высоту 30 мм
    pad = 1.5 * mm
    qr_size = min(h * 0.58, w * 0.42)
    text_w = w - 2 * pad - qr_size - 1 * mm

    # QR с артикулом — справа сверху
    _draw_qr(c, product['article_number'], w - pad - qr_size, h - pad - qr_size, qr_size)
    article, size = _fit(product['article_number'], FONT, 5.5 * k, qr_size + 1 * mm, 4)
    c.setFont(FONT, size)
    c.drawCentredString(w - pad - qr_size / 2, h - pad - qr_size - size - 0.3 * mm, article)

    # Текст слева
    y = h - pad
    title = LABEL_TITLE or product.get('Store') or ''
    if title:
        t, size = _fit(title, FONT, 5 * k, text_w, 3.5)
        y -= size
        c.setFont(FONT, size)
        c.drawString(pad, y, t)
        y -= 0.6 * mm
    name = product.get('model') or product.get('product_type') or ''
    name_size = 6.5 * k
    for line in _wrap(name, FONT_BOLD, name_size, text_w, 2):
        y -= name_size
        c.setFont(FONT_BOLD, name_size)
        c.drawString(pad, y, line)
        y -= 0.3 * mm
    for extra in (details_line(product), product.get('inserts')):
        if extra:
            t, size = _fit(extra, FONT, 5.5 * k, text_w, 3.5)
            y -= size + 0.3 * mm
            c.setFont(FONT, size)
            c.drawString(pad, y, t)

    # Цена — крупно внизу по всей ширине
    price, size = _fit(money(product.get('price')), FONT_BOLD, 11 * k, w - 2 * pad, 6)
    c.setFont(FONT_BOLD, size)
    c.drawString(pad, pad, price)


def _draw_qr(c, value, x, y, size):
    qr = QrCodeWidget(str(value), barLevel='M', barBorder=0)
    x0, y0, x1, y1 = qr.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(qr)
    renderPDF.draw(drawing, c, x, y)


def _draw_label_portrait(c, product, w, h):
    """Вертикальная этикетка (выше, чем шире): текст сверху, QR по центру, цена внизу."""
    k = w / (20 * mm)  # масштаб относительно ширины 20 мм
    pad = 1.2 * mm
    text_w = w - 2 * pad
    y = h - pad
    title = LABEL_TITLE or product.get('Store') or ''
    if title:
        t, size = _fit(title, FONT, 4.2 * k, text_w, 3.5)
        y -= size
        c.setFont(FONT, size)
        c.drawString(pad, y, t)
        y -= 0.4 * mm
    name_size = 5.2 * k
    for line in _wrap(product.get('model') or product.get('product_type') or '', FONT_BOLD, name_size, text_w, 2):
        y -= name_size
        c.setFont(FONT_BOLD, name_size)
        c.drawString(pad, y, line)
        y -= 0.2 * mm
    for extra in (details_line(product), product.get('inserts')):
        if extra:
            t, size = _fit(extra, FONT, 4.4 * k, text_w, 3.5)
            y -= size + 0.2 * mm
            c.setFont(FONT, size)
            c.drawString(pad, y, t)
    price, price_size = _fit(money(product.get('price')), FONT_BOLD, 8.5 * k, text_w, 5)
    article, art_size = _fit(product['article_number'], FONT, 4.4 * k, text_w, 3.5)
    bottom = pad + price_size + 0.8 * mm + art_size + 0.4 * mm
    qr_size = max(min(text_w, y - 0.8 * mm - bottom), 6 * mm)
    _draw_qr(c, product['article_number'], (w - qr_size) / 2, bottom, qr_size)
    c.setFont(FONT, art_size)
    c.drawCentredString(w / 2, bottom - art_size - 0.1 * mm, article)
    c.setFont(FONT_BOLD, price_size)
    c.drawCentredString(w / 2, pad, price)


def generate_labels_pdf(products, copies="one", width_mm=None, height_mm=None):
    """copies: 'one' — одна этикетка на артикул, 'stock' — по количеству на складе."""
    _register_fonts()
    w = (width_mm or LABEL_WIDTH_MM) * mm
    h = (height_mm or LABEL_HEIGHT_MM) * mm
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(w, h))
    c.setTitle("Ценники")
    count = 0
    for product in products:
        n = max(int(product.get('quantity') or 0), 1) if copies == "stock" else 1
        for _ in range(n):
            (_draw_label_portrait if h > w * 1.15 else _draw_label)(c, product, w, h)
            c.showPage()
            count += 1
    if count == 0:
        raise ValueError("Нет товаров для ценников.")
    c.save()
    return buffer.getvalue(), count
