import os
import re
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, ContextTypes,
                           CallbackQueryHandler, MessageHandler, filters,
                           ConversationHandler)
from pyDolarVenezuela.pages import BCV
from pyDolarVenezuela import Monitor

logging.basicConfig(level=logging.WARNING)
logging.getLogger("__main__").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

VE_TZ     = timezone(timedelta(hours=-4))
CANAL     = "@botsgfa"
CANAL_URL = "https://t.me/botsgfa"

# Estados ConversationHandler admin
(ADMIN_MENU, ESPERANDO_MSG_GLOBAL, ESPERANDO_USUARIO, ESPERANDO_MSG_USUARIO) = range(4)

# Cache en memoria + archivo para persistir entre reinicios
CACHE_FILE = "/tmp/rates_cache.json"

_cache = {
    "bcv":     {"rates": None, "date": None},
    "binance": {"rate":  None, "date": None},
}


def save_cache():
    """Guarda el cache en disco."""
    import json
    try:
        data = {
            "bcv_rates": _cache["bcv"]["rates"],
            "bcv_date":  str(_cache["bcv"]["date"]) if _cache["bcv"]["date"] else None,
            "bin_rate":  _cache["binance"]["rate"],
            "bin_date":  str(_cache["binance"]["date"]) if _cache["binance"]["date"] else None,
        }
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"No se pudo guardar cache: {e}")


def load_cache():
    """Carga el cache desde disco al arrancar."""
    import json
    from datetime import date
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        if data.get("bcv_rates") and data.get("bcv_date"):
            _cache["bcv"]["rates"] = data["bcv_rates"]
            _cache["bcv"]["date"]  = date.fromisoformat(data["bcv_date"])
        if data.get("bin_rate") and data.get("bin_date"):
            _cache["binance"]["rate"] = data["bin_rate"]
            _cache["binance"]["date"] = date.fromisoformat(data["bin_date"])
        logger.info(f"Cache cargado: BCV={_cache['bcv']['rates']} Binance={_cache['binance']['rate']}")
    except Exception:
        logger.info("No hay cache previo, se obtendrán tasas frescas.")


def get_effective_date():
    """
    Fecha efectiva de la tasa:
    - Lunes a viernes → hoy
    - Sábado → viernes anterior
    - Domingo → viernes anterior
    """
    now = get_ve_now()
    weekday = now.weekday()
    if weekday == 5:
        return (now - timedelta(days=1)).date()
    elif weekday == 6:
        return (now - timedelta(days=2)).date()
    return now.date()


def should_fetch():
    """
    Fetch solo de lunes a viernes, y solo si aún no hemos
    cargado la tasa de hoy. Una vez cargada, no vuelve a pedir
    hasta las 00:00 VE del día siguiente.
    Fin de semana → nunca hace fetch, usa cache del viernes.
    """
    now = get_ve_now()
    if now.weekday() >= 5:  # sábado o domingo
        return False
    # Solo permite fetch si el cache es de un día anterior
    cached_date = _cache["bcv"]["date"]
    effective = get_effective_date()
    return cached_date != effective


# ── HTTP server para UptimeRobot ─────────────────────────────────────────────

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


# ── Utilidades ───────────────────────────────────────────────────────────────

def get_ve_now():
    return datetime.now(VE_TZ)

def get_admin_id():
    val = os.environ.get("ADMIN_ID")
    return int(val) if val else None

def es_admin(user_id):
    admin_id = get_admin_id()
    return admin_id is not None and user_id == admin_id

def es_privado(update):
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
    c = _cache["bcv"]
    cache_date = get_effective_date()

    # Ya tenemos la tasa de hoy (o del viernes si es fin de semana) → no tocar
    if c["rates"] and c["date"] == cache_date:
        return c["rates"]

    # Si no debemos hacer fetch (fin de semana o ya fue cargada hoy) → cache
    if not should_fetch():
        return c["rates"]

    try:
        rates = {}
        all_monitors = Monitor(BCV, 'USD').get_all_monitors()
        items = list(vars(all_monitors).items()) if hasattr(all_monitors, '__dict__') else []
        if isinstance(all_monitors, list):
            items = [(getattr(i, 'key', getattr(i, 'title', '')), i) for i in all_monitors]
        for key, val in items:
            key_lower = str(key).lower()
            price = getattr(val, 'price', None)
            if not price:
                continue
            if 'usd' in key_lower or 'dolar' in key_lower or 'dollar' in key_lower:
                rates["USD"] = float(price)
            elif 'eur' in key_lower or 'euro' in key_lower:
                rates["EUR"] = float(price)

        if rates:
            c["rates"] = rates
            c["date"]  = cache_date
            logger.info(f"Tasas BCV actualizadas {cache_date}: {rates}")
            save_cache()
        return rates or c["rates"]

    except Exception as e:
        logger.error(f"Error fetch_bcv_rates: {e}")
        return c["rates"]


def fetch_binance_rate():
    """Obtiene precio USDT/VES desde la API P2P oficial de Binance (tiempo real)."""
    c = _cache["binance"]
    # Binance: cachear 5 minutos máximo para que sea tiempo real
    now_ve = get_ve_now()
    if c["rate"] and c["date"] == now_ve.date():
        cached_ts = _cache["binance"].get("ts")
        if cached_ts and (now_ve.timestamp() - cached_ts) < 300:
            return c["rate"]
    try:
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        payload = {
            "asset": "USDT",
            "fiat": "VES",
            "merchantCheck": False,
            "page": 1,
            "publishType": "BUY",
            "rows": 5,
            "tradeType": "BUY"
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        data = r.json()
        prices = [float(ad["adv"]["price"]) for ad in data.get("data", []) if ad.get("adv", {}).get("price")]
        if prices:
            price = sum(prices) / len(prices)  # promedio de los primeros anuncios
            c["rate"] = price
            c["date"] = now_ve.date()
            c["ts"]   = now_ve.timestamp()
            logger.info(f"Binance P2P actualizado: {price}")
            return price
        logger.warning("No se obtuvieron precios de Binance P2P")
        return c["rate"]
    except Exception as e:
        logger.error(f"Error Binance P2P: {e}")
        return c["rate"]


# ── Suscripción ──────────────────────────────────────────────────────────────

async def check_suscripcion(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CANAL, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"No se pudo verificar suscripción: {e}")
        return False

async def pedir_suscripcion(update):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Suscribirme al canal", url=CANAL_URL)],
        [InlineKeyboardButton("✅ Ya me suscribí", callback_data="check_sub")]
    ])
    await update.message.reply_text(
        "⚠️ Para usar este bot necesitas estar suscrito a nuestro canal.\n\n"
        "Una vez suscrito, presiona el botón *'Ya me suscribí'* para continuar.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ── Comandos públicos ────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if es_privado(update):
        if not await check_suscripcion(update.effective_user.id, context):
            await pedir_suscripcion(update)
            return
    await update.message.reply_text(
        "¡Gracias por iniciarme! Puedes ver la tasa BCV del día de hoy a través del comando /bcv, "
        "calcular cuánto es en bolívares cierta cantidad de dólares o euros con /calcular, "
        "y convertir bolívares a dólares o euros con /convertir"
    )


async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if es_privado(update):
        if not await check_suscripcion(update.effective_user.id, context):
            await pedir_suscripcion(update)
            return

    rates   = fetch_bcv_rates()
    binance = fetch_binance_rate()

    if not rates:
        await update.message.reply_text("❌ No se pudo obtener la tasa del BCV en este momento. Intenta más tarde.")
        return

    usd_str     = format_number(rates["USD"]) if rates.get("USD") else "No disponible"
    eur_str     = format_number(rates["EUR"]) if rates.get("EUR") else "No disponible"
    binance_str = format_number(binance)      if binance           else "No disponible"

    msg = (
        f"*TASAS DEL DÍA*\n"
        f"📅 {get_date_str()}\n\n"
        f"🇪🇺 Euro: Bs. {eur_str}\n"
        f"🇺🇸 Dólar: Bs. {usd_str}\n"
        f"💲 Binance / USDT: Bs. {binance_str}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def calcular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if es_privado(update):
        if not await check_suscripcion(update.effective_user.id, context):
            await pedir_suscripcion(update)
            return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Indica la cantidad y moneda.\nEjemplos:\n"
            "/calcular 20 — dólares BCV (por defecto)\n"
            "/calcular 20 eur — euros BCV\n"
            "/calcular 20 usdt — Binance/USDT"
        )
        return

    try:
        cantidad = float(args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valor no válido. Ejemplo: /calcular 20")
        return

    moneda = args[1].lower() if len(args) > 1 else "usd"

    if moneda in ("eur", "euro", "euros", "€"):
        rates = fetch_bcv_rates()
        tasa  = rates.get("EUR") if rates else None
        simbolo = "€"; fuente = "BCV"
    elif moneda in ("usdt", "binance", "crypto"):
        tasa    = fetch_binance_rate()
        simbolo = "USDT"; fuente = "Binance"
    else:
        rates = fetch_bcv_rates()
        tasa  = rates.get("USD") if rates else None
        simbolo = "$"; fuente = "BCV"

    if not tasa:
        await update.message.reply_text("❌ No se pudo obtener la tasa. Intenta más tarde.")
        return

    cant_str = str(int(cantidad)) if cantidad == int(cantidad) else str(cantidad)
    msg = f"{cant_str} {simbolo} ({fuente}) en bolívares serían: Bs. {format_number(cantidad * tasa)}"
    await update.message.reply_text(msg)


async def convertir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if es_privado(update):
        if not await check_suscripcion(update.effective_user.id, context):
            await pedir_suscripcion(update)
            return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Indica la cantidad en bolívares y la moneda destino.\nEjemplos:\n"
            "/convertir 10000 — Bs. a dólares BCV (por defecto)\n"
            "/convertir 10000 eur — Bs. a euros BCV\n"
            "/convertir 10000 usdt — Bs. a Binance/USDT"
        )
        return

    try:
        cantidad = float(args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valor no válido. Ejemplo: /convertir 10000")
        return

    moneda = args[1].lower() if len(args) > 1 else "usd"

    if moneda in ("eur", "euro", "euros", "€"):
        rates = fetch_bcv_rates()
        tasa  = rates.get("EUR") if rates else None
        simbolo = "€"; nombre = "euros (BCV)"
    elif moneda in ("usdt", "binance", "crypto"):
        tasa    = fetch_binance_rate()
        simbolo = "USDT"; nombre = "Binance / USDT"
    else:
        rates = fetch_bcv_rates()
        tasa  = rates.get("USD") if rates else None
        simbolo = "$"; nombre = "dólares (BCV)"

    if not tasa:
        await update.message.reply_text("❌ No se pudo obtener la tasa. Intenta más tarde.")
        return

    msg = f"Bs. {format_number(cantidad)} en {nombre} serían: {simbolo}{format_number(cantidad / tasa)}"
    await update.message.reply_text(msg)


async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await check_suscripcion(query.from_user.id, context):
        await query.edit_message_text(
            "✅ ¡Suscripción verificada! Ya puedes usar todos los comandos:\n\n"
            "/bcv — Ver tasas del día\n"
            "/calcular 20 — USD a Bs.\n"
            "/calcular 20 eur — EUR a Bs.\n"
            "/calcular 20 usdt — Binance/USDT a Bs.\n"
            "/convertir 10000 — Bs. a USD\n"
            "/convertir 10000 eur — Bs. a EUR\n"
            "/convertir 10000 usdt — Bs. a USDT"
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Suscribirme al canal", url=CANAL_URL)],
            [InlineKeyboardButton("✅ Ya me suscribí", callback_data="check_sub")]
        ])
        await query.edit_message_text(
            "❌ Aún no estás suscrito al canal. Suscríbete y vuelve a intentarlo.",
            reply_markup=keyboard
        )


# ── Panel Admin (/gfa) ────────────────────────────────────────────────────────

def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Mensaje global",    callback_data="admin_global")],
        [InlineKeyboardButton("✉️ Mensaje a usuario", callback_data="admin_usuario")],
        [InlineKeyboardButton("❌ Cerrar panel",      callback_data="admin_cerrar")],
    ])

async def gfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return
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

    if query.data == "admin_global":
        await query.edit_message_text(
            "📣 *Mensaje global*\n\nEscribe el mensaje a enviar a todos los usuarios.\n\n_/cancelar para salir._",
            parse_mode="Markdown"
        )
        return ESPERANDO_MSG_GLOBAL

    elif query.data == "admin_usuario":
        await query.edit_message_text(
            "✉️ *Mensaje a usuario*\n\nEnvíame el ID numérico o @username del destinatario.\n\n_/cancelar para salir._",
            parse_mode="Markdown"
        )
        return ESPERANDO_USUARIO

    elif query.data == "admin_cerrar":
        await query.edit_message_text("✅ Panel cerrado.")
        return ConversationHandler.END

async def recibir_msg_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return ConversationHandler.END
    if update.message.text == "/cancelar":
        await update.message.reply_text("❌ Operación cancelada.")
        return ConversationHandler.END

    usuarios = context.bot_data.get("usuarios", set())
    if not usuarios:
        await update.message.reply_text("⚠️ No hay usuarios registrados aún.")
        return ConversationHandler.END

    enviados = fallidos = 0
    for uid in usuarios:
        try:
            await context.bot.send_message(chat_id=uid, text=update.message.text)
            enviados += 1
        except Exception:
            fallidos += 1

    await update.message.reply_text(f"✅ Enviado.\n📤 Exitosos: {enviados}\n❌ Fallidos: {fallidos}")
    return ConversationHandler.END

async def recibir_usuario_destino(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return ConversationHandler.END
    if update.message.text == "/cancelar":
        await update.message.reply_text("❌ Operación cancelada.")
        return ConversationHandler.END
    context.user_data["admin_destino"] = update.message.text.strip()
    await update.message.reply_text(
        f"✉️ Destinatario: `{update.message.text.strip()}`\n\nAhora escribe el mensaje.\n\n_/cancelar para salir._",
        parse_mode="Markdown"
    )
    return ESPERANDO_MSG_USUARIO

async def recibir_msg_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return ConversationHandler.END
    if update.message.text == "/cancelar":
        await update.message.reply_text("❌ Operación cancelada.")
        return ConversationHandler.END

    destino = context.user_data.get("admin_destino")
    try:
        chat_id = int(destino) if destino.lstrip("-").isdigit() else destino
        await context.bot.send_message(chat_id=chat_id, text=update.message.text)
        await update.message.reply_text(f"✅ Mensaje enviado a `{destino}`.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ No se pudo enviar a `{destino}`.\nError: {e}", parse_mode="Markdown")

    context.user_data.pop("admin_destino", None)
    return ConversationHandler.END

async def cancelar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END


# ── Registro de usuarios ──────────────────────────────────────────────────────

async def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and update.effective_chat.type == "private":
        if "usuarios" not in context.bot_data:
            context.bot_data["usuarios"] = set()
        context.bot_data["usuarios"].add(update.effective_user.id)


# ── Comando /settasa (solo admin) ────────────────────────────────────────────

async def settasa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        rates = _cache["bcv"]["rates"] or {}
        usd_str = format_number(rates["USD"]) if rates.get("USD") else "N/A"
        eur_str = format_number(rates["EUR"]) if rates.get("EUR") else "N/A"
        msg = "Tasas actuales en cache:\nUSD: Bs. " + usd_str + "\nEUR: Bs. " + eur_str + "\n\nUso: /settasa 443.26 510.34"
        await update.message.reply_text(msg)
        return

    try:
        usd = float(args[0].replace(",", "."))
        eur = float(args[1].replace(",", ".")) if len(args) > 1 else (_cache["bcv"]["rates"] or {}).get("EUR")
    except (ValueError, IndexError):
        await update.message.reply_text("Formato invalido. Ejemplo: /settasa 443.26 510.34")
        return

    rates = {}
    if usd:
        rates["USD"] = usd
    if eur:
        rates["EUR"] = eur

    effective = get_effective_date()
    _cache["bcv"]["rates"] = rates
    _cache["bcv"]["date"]  = effective
    save_cache()

    usd_str = format_number(usd)
    eur_str = format_number(eur) if eur else "N/A"
    msg = "Tasas fijadas manualmente:\nUSD: Bs. " + usd_str + "\nEUR: Bs. " + eur_str + "\nFecha efectiva: " + str(effective)
    await update.message.reply_text(msg)


# ── Main ──────────────────────────────────────────────────────────────────────

def preload_rates():
    """Carga cache del disco y fetch si hace falta."""
    load_cache()
    fetch_bcv_rates()
    fetch_binance_rate()
    logger.info(f"Tasas listas: BCV={_cache['bcv']['rates']} Binance={_cache['binance']['rate']}")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta la variable de entorno TELEGRAM_BOT_TOKEN")

    threading.Thread(target=run_http_server, daemon=True).start()
    preload_rates()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("settasa",   settasa))
    app.add_handler(CommandHandler("bcv",       bcv))
    app.add_handler(CommandHandler("calcular",  calcular))
    app.add_handler(CommandHandler("convertir", convertir))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))

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
    app.add_handler(MessageHandler(filters.ALL, registrar_usuario), group=1)

    logger.info("Bot iniciado...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, poll_interval=2.0, timeout=20)


if __name__ == "__main__":
    main()
