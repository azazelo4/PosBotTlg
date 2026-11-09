"""Электронный чек: текст для Telegram и WhatsApp, ссылки на страницу чека и бота."""
from urllib.parse import quote

import config
from utils.common import money, h


def item_details(it):
    parts = [it.get('metal_type'), it.get('fineness')]
    if it.get('weight'):
        parts.append(f"{float(it['weight']):g} г")
    if it.get('size'):
        parts.append(f"р. {it['size']}")
    if it.get('inserts'):
        parts.append(it['inserts'])
    return ", ".join(str(p) for p in parts if p)


def public_url(receipt):
    """Ссылка на страницу чека, если адрес админки доступен из интернета (https). Иначе None:
    адрес в сети магазина клиенту бесполезен."""
    if config.WEB_URL and str(config.WEB_URL).startswith("https://") and receipt.get('public_token'):
        return f"{config.WEB_URL}/r/{receipt['public_token']}"
    return None


def bot_link(bot_username, receipt):
    return f"https://t.me/{bot_username}?start=r_{receipt['public_token']}" if bot_username else None


def telegram_text(receipt):
    """HTML для бота."""
    lines = [f"🧾 <b>{h(config.ORG_NAME or 'Чек')}</b>" + (f", {h(receipt.get('store'))}" if receipt.get('store') else ""),
             f"Чек №{receipt['id']} от {receipt['created_at']:%d.%m.%Y %H:%M}", ""]
    for i, it in enumerate(receipt.get('items') or [], 1):
        title = it.get('model') or it.get('product_type') or it['article_number']
        line = f"{i}. {h(title)}, арт. {h(it['article_number'])}"
        details = item_details(it)
        if details:
            line += f"\n   {h(details)}"
        line += f"\n   <b>{money(it['sold_price'])}</b>"
        if it.get('list_price') and float(it['list_price']) > float(it['sold_price']):
            line += f" (скидка {money(float(it['list_price']) - float(it['sold_price']))})"
        if it.get('return_id'):
            line += " — возвращено"
        lines.append(line)
    lines += ["", f"Итого: <b>{money(receipt['total'])}</b>, {h(receipt['payment_type'])}"]
    fiscal = receipt.get('fiscal') or {}
    if fiscal.get('ticket_url'):
        lines.append(f"Фискальный чек: {h(fiscal['ticket_url'])}")
    url = public_url(receipt)
    if url:
        lines.append(f"Чек на сайте: {url}")
    if config.RECEIPT_FOOTER:
        lines += ["", h(config.RECEIPT_FOOTER)]
    return "\n".join(lines)


def whatsapp_text(receipt):
    """Обычный текст (WhatsApp не понимает HTML)."""
    url = public_url(receipt)
    head = f"{config.ORG_NAME or 'Магазин'}: ваш чек №{receipt['id']} на {money(receipt['total'])}."
    if url:
        return f"{head}\n{url}\nСпасибо за покупку!"
    items = "\n".join(f"{i}. {it.get('model') or it['article_number']} — {money(it['sold_price'])}"
                      for i, it in enumerate(receipt.get('items') or [], 1))
    fiscal = (receipt.get('fiscal') or {}).get('ticket_url')
    return f"{head}\n{items}\n" + (f"Фискальный чек: {fiscal}\n" if fiscal else "") + "Спасибо за покупку!"


def whatsapp_url(phone, receipt):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(whatsapp_text(receipt))}" if digits else None
