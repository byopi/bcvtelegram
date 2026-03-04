import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, ContextTypes,
                           CallbackQueryHandler, MessageHandler, filters,
                           ConversationHandler)
from pyDolarVenezuela.pages import BCV
import requests
from bs4 import BeautifulSoup
from pyDolarVenezuela import Monitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VE_TZ = timezone(timedelta(hours=-4))

CANAL     = "@botsgfa"
CANAL_URL = "https://t.me/botsgfa"

# Estados para ConversationHandler del panel admin
(
    ADMIN_MENU,
    ESPERANDO_MSG_GLOBAL,
    ESPERANDO_USUARIO,
    ESPERANDO_MSG_USUARIO,
) = range(4)

# Cache
_cache = {
    "bcv":     {"rates": None, "date": None},
    "binance": {"rate":  None, "date": None},
}


# ── Servidor HTTP para UptimeRobot ───────────────────────────────────────────

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()


# ── Utilidades generales ─────────────────────────────────────────────────────

def get_admin_id() -> int | None:
    val = os.environ.get("ADMIN_ID")
    return int(val) if val else None

def get_ve_now():
    return datetime.now(VE_TZ)

def es_admin(user_id: int) -> bool:
    admin_id = get_admin_id()
    return admin_id is not None and user_id == admin_id

def es_privado(update: Update) -> bool:
    return update.message.chat.type == "private"

def format_number(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def get_date_str():
    meses = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    now = get_ve_now()
    return f"{now.day} de {meses[now.month - 1]} de {now.year}"


# ── Tasas ────────────────────────────────────────────────────────────────────

def fetch_bcv_rates():
    """
    USD: Monitor(BCV, 'USD') — oficial BCV
    EUR: scraping directo bcv.org.ve
    """
    today_ve = get_ve_now().date()
    c = _cache["bcv"]
    if c["rates"] and c["date"] == today_ve:
        return c["rates"]
    try:
        rates = {}

        # USD desde BCV oficial
        try:
            usd = Monitor(BCV, 'USD').get_value_monitors("usd")
            if usd and usd.price:
                rates["USD"] = float(usd.price)
        except Exception as e:
            logger.error(f"Error USD BCV: {e}")

        # EUR scraping directo de bcv.org.ve
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get("https://www.bcv.org.ve/", headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            euro_div = soup.find("div", {"id": "euro"})
            if euro_div:
                strong = euro_div.find("strong")
                if strong:
                    rates["EUR"] = float(strong.text.strip().replace(",", "."))
        except Exception as e:
            logger.error(f"Error EUR scraping: {e}")

        if rates:
            c["rates"] = rates
            c["date"]  = today_ve
            logger.info(f"Tasas actualizadas {today_ve}: {rates}")
        return rates or c["rates"]

    except Exception as e:
        logger.error(f"Error fetch_bcv_rates: {e}")
        return c["rates"]


def fetch_binance_rate():
    """Binance/USDT scrapeado desde exchangemonitor.net"""
    today_ve = get_ve_now().date()
    c = _cache["binance"]
    if c["rate"] and c["date"] == today_ve:
        return c["rate"]
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get("https://exchangemonitor.net/venezuela/dolar-binance", headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        # El precio aparece en un elemento con clase "price" o similar
        # Buscar el valor principal de la tasa
        price_el = soup.find("p", {"class": "specific-value"})
        if not price_el:
            price_el = soup.find("div", {"class": "price"})
        if not price_el:
            # Buscar en meta tags
            meta = soup.find("meta", {"property": "og:description"})
            if meta:
                import re
                match = re.search(r"[\d.,]+", meta.get("content", "").replace(".", "").replace(",", "."))
                if match:
                    c["rate"] = float(match.group())
                    c["date"] = today_ve
                    return c["rate"]
        if price_el:
            import re
            text = price_el.text.strip().replace(".", "").replace(",", ".")
            match = re.search(r"[\d.]+", text)
            if match:
                price = float(match.group())
                c["rate"] = price
                c["date"] = today_ve
                logger.info(f"Binance actualizado {today_ve}: {price}")
                return price
        logger.warning("No se pudo scrapear tasa Binance")
        return c["rate"]
    except Exception as e:
        logger.error(f"Error Binance scraping: {e}")
        return c["rate"]


def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Mensaje global",        callback_data="admin_global")],
        [InlineKeyboardButton("✉️ Mensaje a usuario",     callback_data="admin_usuario")],
        [InlineKeyboardButton("❌ Cerrar panel",          callback_data="admin_cerrar")],
    ])

async def gfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return  # ignorar silenciosamente si no es admin
    await update.message.reply_text(
        "🔐 *Panel de Administrador*\n\n¿Qué deseas hacer?",
        parse_mode="Markdown",
        reply_markup=admin_menu_keyboard()
    )
    return ADMIN_MENU

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not es_admin(query.from_user.id):
        return ConversationHandler.END

    data = query.data

    if data == "admin_global":
        await query.edit_message_text(
            "📣 *Mensaje global*\n\nEscribe el mensaje que quieres enviar a todos los usuarios "
            "que han interactuado con el bot.\n\n_Escribe /cancelar para salir._",
            parse_mode="Markdown"
        )
        return ESPERANDO_MSG_GLOBAL

    elif data == "admin_usuario":
        await query.edit_message_text(
            "✉️ *Mensaje a usuario*\n\nEnvíame el *ID numérico* o *@username* del destinatario.\n\n"
            "_Escribe /cancelar para salir._",
            parse_mode="Markdown"
        )
        return ESPERANDO_USUARIO

    elif data == "admin_cerrar":
        await query.edit_message_text("✅ Panel cerrado.")
        return ConversationHandler.END

async def recibir_msg_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return ConversationHandler.END

    texto = update.message.text
    if texto == "/cancelar":
        await update.message.reply_text("❌ Operación cancelada.")
        return ConversationHandler.END

    usuarios = context.bot_data.get("usuarios", set())
    if not usuarios:
        await update.message.reply_text("⚠️ No hay usuarios registrados aún.")
        return ConversationHandler.END

    enviados = 0
    fallidos = 0
    for uid in usuarios:
        try:
            await context.bot.send_message(chat_id=uid, text=texto)
            enviados += 1
        except Exception:
            fallidos += 1

    await update.message.reply_text(
        f"✅ Mensaje global enviado.\n📤 Enviados: {enviados}\n❌ Fallidos: {fallidos}"
    )
    return ConversationHandler.END

async def recibir_usuario_destino(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return ConversationHandler.END

    texto = update.message.text
    if texto == "/cancelar":
        await update.message.reply_text("❌ Operación cancelada.")
        return ConversationHandler.END

    # Guardar destinatario en context.user_data
    context.user_data["admin_destino"] = texto.strip()
    await update.message.reply_text(
        f"✉️ Destinatario: `{texto.strip()}`\n\nAhora escribe el mensaje a enviar.\n\n"
        "_Escribe /cancelar para salir._",
        parse_mode="Markdown"
    )
    return ESPERANDO_MSG_USUARIO

async def recibir_msg_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return ConversationHandler.END

    texto = update.message.text
    if texto == "/cancelar":
        await update.message.reply_text("❌ Operación cancelada.")
        return ConversationHandler.END

    destino = context.user_data.get("admin_destino")
    try:
        # Intentar como int (ID) o string (@username)
        chat_id = int(destino) if destino.lstrip("-").isdigit() else destino
        await context.bot.send_message(chat_id=chat_id, text=texto)
        await update.message.reply_text(f"✅ Mensaje enviado a `{destino}`.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ No se pudo enviar el mensaje a `{destino}`.\nError: {e}", parse_mode="Markdown")

    context.user_data.pop("admin_destino", None)
    return ConversationHandler.END

async def cancelar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END


# ── Registro de usuarios ─────────────────────────────────────────────────────

async def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra cualquier usuario que interactúe en privado para el mensaje global."""
    if update.effective_chat and update.effective_chat.type == "private":
        uid = update.effective_user.id
        if "usuarios" not in context.bot_data:
            context.bot_data["usuarios"] = set()
        context.bot_data["usuarios"].add(uid)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta la variable de entorno TELEGRAM_BOT_TOKEN")

    threading.Thread(target=run_http_server, daemon=True).start()

    app = Application.builder().token(token).build()

    # Comandos públicos
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("bcv",       bcv))
    app.add_handler(CommandHandler("calcular",  calcular))
    app.add_handler(CommandHandler("convertir", convertir))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))

    # Panel admin con ConversationHandler
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("gfa", gfa)],
        states={
            ADMIN_MENU: [
                CallbackQueryHandler(admin_callback, pattern="^admin_")
            ],
            ESPERANDO_MSG_GLOBAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_msg_global),
                CommandHandler("cancelar", cancelar_admin),
            ],
            ESPERANDO_USUARIO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_usuario_destino),
                CommandHandler("cancelar", cancelar_admin),
            ],
            ESPERANDO_MSG_USUARIO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_msg_usuario),
                CommandHandler("cancelar", cancelar_admin),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar_admin)],
    )
    app.add_handler(admin_conv)

    # Middleware para registrar usuarios
    app.add_handler(MessageHandler(filters.ALL, registrar_usuario), group=1)

    logger.info("Bot iniciado...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
