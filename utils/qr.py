"""QR-коды без внешних зависимостей: матрица из кодировщика reportlab, PNG — через Pillow (зависимость reportlab)."""
from io import BytesIO


def qr_matrix(text, level='M'):
    """Строки модулей QR: True — тёмный."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    widget = QrCodeWidget(text, barLevel=level)
    widget.qr.make()
    return [[bool(cell) for cell in row] for row in widget.qr.modules]


def qr_png(text, scale=10, border=4):
    """PNG с QR-кодом (для отправки фото в Telegram)."""
    from PIL import Image
    matrix = qr_matrix(text)
    size = (len(matrix) + 2 * border) * scale
    img = Image.new("1", (size, size), 1)
    pixels = img.load()
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                x0, y0 = (c + border) * scale, (r + border) * scale
                for y in range(y0, y0 + scale):
                    for x in range(x0, x0 + scale):
                        pixels[x, y] = 0
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
