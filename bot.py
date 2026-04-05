import os
import re
import logging
import threading
import requests
import time
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, ContextTypes,
                           CallbackQueryHandler, MessageHandler, filters,
                           ConversationHandler)
# Se mantienen las librerías solicitadas
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

# Configuración de Proxy (Añadir en las variables de entorno de Render)
# Ejemplo: http://usuario:password@host:puerto
PROXY_URL = os.environ.get("PROXY_URL")

# Estados ConversationHandler admin
(ADMIN_MENU, ESPERANDO_MSG_GLOBAL, ESPERANDO_USUARIO, ESPERANDO_MSG_USUARIO) = range(4)

# Cache en memoria + archivo para persistir entre reinicios
CACHE_FILE = "/tmp/rates_cache.json"

_cache = {
    "bcv":     {"rates": None, "date": None},
    "binance": {"rate":  None, "date": None},
}


# ── Antiflood ─────────────────────────────────────────────────────────────────
_flood_data: dict = defaultdict(lambda: {"timestamps": [], "muted_until": 0.0})

def check_flood(user_id: int) -> bool:
    now = time.monotonic()
    data = _flood_data[user_id]
    if now < data["muted_until"]:
        return True
    data["timestamps"] = [t for t in data["timestamps"] if now - t < 10] # ventana 10s
    data["timestamps"].append(now)
    if len(data["timestamps"]) > 3: # max 3 msgs
        data["muted_until"] = now + 30
        data["timestamps"]  = []
        return True
    return False


# ── Sistema de Ban ────────────────────────────────────────────────────────────
def get_baneados(context: ContextTypes.DEFAULT_TYPE) -> set:
    if "baneados" not in context.bot_data:
        context.bot_data["baneados"] = set()
    return context.bot_data["baneados"]

def esta_baneado(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return user_id in get_baneados(context)

async def banear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    if not context.args: return
    objetivo = context.args[0].strip()
    baneados = get_baneados(context)
    ban_id = int(objetivo) if objetivo.lstrip("-").isdigit() else objetivo.lower()
    baneados.add(ban_id)
    await update.message.reply_text(f"🔨 Usuario `{objetivo}` baneado.")

async def desbanear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    if not context.args: return
    objetivo = context.args[0].strip()
    baneados = get_baneados(context)
    ban_id = int(objetivo) if objetivo.lstrip("-").isdigit() else objetivo.lower()
    baneados.discard(ban_id)
    await update.message.reply_text(f"✅ Usuario `{objetivo}` desbaneado.")

async def lista_baneados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    baneados = get_baneados(context)
    if not baneados:
        await update.message.reply_text("✅ No hay baneados.")
        return
    await update.message.reply_text(f"Lista: {baneados}")

# ── Guard ──
async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user: return False
    if esta_baneado(user.id, context): return True
    if user.username and ("@" + user.username.lower()) in get_baneados(context): return True
    if check_flood(user.id): return True
    return False


# ── Cache utils ──────────────────────────────────────────────────────────────
def save_cache():
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
    except Exception:
        logger.info("Iniciando sin cache previo.")

def get_effective_date():
    now = get_ve_now()
    weekday = now.weekday()
    if weekday == 5: return (now - timedelta(days=1)).date()
    elif weekday == 6: return (now - timedelta(days=2)).date()
    return now.date()

def should_fetch():
    now = get_ve_now()
    if now.weekday() >= 5: return False
    return _cache["bcv"]["date"] != get_effective_date()


# ── HTTP server ──
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()


# ── Utilidades ──
def get_ve_now(): return datetime.now(VE_TZ)
def get_admin_id():
    val = os.environ.get("ADMIN_ID")
    return int(val) if val else None
def es_admin(user_id): return user_id == get_admin_id()
def es_privado(update): return update.message.chat.type == "private"
def format_number(value): return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
def get_date_str():
    meses = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    now = get_ve_now()
    return f"{now.day} de {meses[now.month - 1]} de {now.year}"


# ── Tasas (MODIFICADO CON PYDOLARVENEZUELA + PROXY) ──────────────────────────

def fetch_bcv_rates():
    c = _cache["bcv"]
    cache_date = get_effective_date()

    # Si ya tenemos la tasa de hoy en cache, no buscamos nada
    if c["rates"] and c["date"] == cache_date:
        return c["rates"]
    
    # Si es fin de semana, usamos lo último que tengamos
    if not should_fetch():
        return c["rates"]

    try:
        # Configurar proxies correctamente para pyDolarVenezuela
        # La librería espera un diccionario o None
        pdv_proxy = {"https": PROXY_URL, "http": PROXY_URL} if PROXY_URL else None
        
        # Instanciar el monitor con el proxy
        monitor = Monitor(page=BCV, proxies=pdv_proxy)
        
        # Obtener los datos
        data = monitor.get_all_monitors()
        
        rates = {}
        for m in data:
            # pyDolarVenezuela usa objetos con atributo 'key' y 'price'
            key = getattr(m, 'key', '').lower()
            price = getattr(m, 'price', 0)
            
            if key == 'usd':
                rates["USD"] = float(price)
            elif key == 'eur':
                rates["EUR"] = float(price)

        if rates.get("USD") and rates.get("EUR"):
            c["rates"] = rates
            c["date"]  = cache_date
            save_cache()
            logger.info(f"✅ Tasas actualizadas: {rates}")
            return rates
            
    except Exception as e:
        logger.error(f"⚠️ Error en pyDolar (Proxy?): {e}")

    # --- FALLBACK (Si lo de arriba falla, intentamos scraping directo) ---
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        
        r = requests.get("https://www.bcv.org.ve/", headers=headers, proxies=proxies, timeout=15, verify=False)
        soup = BeautifulSoup(r.text, "html.parser")
        
        fallback = {}
        usd_div = soup.find("div", {"id": "dolar"})
        eur_div = soup.find("div", {"id": "euro"})
        
        if usd_div: fallback["USD"] = float(usd_div.find("strong").text.strip().replace(",", "."))
        if eur_div: fallback["EUR"] = float(eur_div.find("strong").text.strip().replace(",", "."))
        
        if fallback:
            c["rates"], c["date"] = fallback, cache_date
            save_cache()
            return fallback
    except Exception as e:
        logger.error(f"❌ Fallback fallido: {e}")
        
    return c["rates"] # Retorna lo que haya en memoria si todo falla


def fetch_binance_rate():
    c = _cache["binance"]
    now_ve = get_ve_now()
    if c["rate"] and c["date"] == now_ve.date():
        cached_ts = c.get("ts")
        if cached_ts and (now_ve.timestamp() - cached_ts) < 300:
            return c["rate"]
    try:
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        payload = {"asset": "USDT", "fiat": "VES", "merchantCheck": False, "page": 1, "publishType": "BUY", "rows": 5, "tradeType": "BUY"}
        # Binance también usa el proxy si está configurado
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        r = requests.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"}, proxies=proxies, timeout=10)
        data = r.json()
        prices = [float(ad["adv"]["price"]) for ad in data.get("data", []) if ad.get("adv", {}).get("price")]
        if prices:
            price = sum(prices) / len(prices)
            c.update({"rate": price, "date": now_ve.date(), "ts": now_ve.timestamp()})
            return price
        return c["rate"]
    except Exception as e:
        logger.error(f"Error Binance: {e}")
        return c["rate"]


# ── Suscripción ──
async def check_suscripcion(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CANAL, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except: return False

async def pedir_suscripcion(update):
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Suscribirme", url=CANAL_URL)], [InlineKeyboardButton("✅ Ya me suscribí", callback_data="check_sub")]])
    await update.message.reply_text("⚠️ Suscríbete al canal para usar el bot.", reply_markup=keyboard)


# ── Comandos Públicos (Sin cambios en lógica) ──
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    await update.message.reply_text("Bot iniciado. Usa /bcv, /calcular o /convertir.")

async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    rates, binance = fetch_bcv_rates(), fetch_binance_rate()
    if not rates:
        await update.message.reply_text("❌ Error al obtener tasas."); return
    msg = f"*TASAS DEL DÍA*\n📅 {get_date_str()}\n\n🇪🇺 Euro: Bs. {format_number(rates.get('EUR', 0))}\n🇺🇸 Dólar: Bs. {format_number(rates.get('USD', 0))}\n💲 Binance: Bs. {format_number(binance)}"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def calcular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    args = context.args
    if not args:
        await update.message.reply_text("Ejemplo: /calcular 20"); return
    try:
        cantidad = float(args[0].replace(",", "."))
        moneda = args[1].lower() if len(args) > 1 else "usd"
        if moneda in ("eur", "euro", "€"):
            tasa = fetch_bcv_rates().get("EUR")
        elif moneda in ("usdt", "binance"):
            tasa = fetch_binance_rate()
        else:
            tasa = fetch_bcv_rates().get("USD")
        await update.message.reply_text(f"Resultado: Bs. {format_number(cantidad * tasa)}")
    except:
        await update.message.reply_text("❌ Error en cálculo.")

async def convertir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    args = context.args
    if not args:
        await update.message.reply_text("Ejemplo: /convertir 1000"); return
    try:
        cantidad = float(args[0].replace(",", "."))
        moneda = args[1].lower() if len(args) > 1 else "usd"
        if moneda in ("eur", "euro", "€"):
            tasa = fetch_bcv_rates().get("EUR")
        elif moneda in ("usdt", "binance"):
            tasa = fetch_binance_rate()
        else:
            tasa = fetch_bcv_rates().get("USD")
        await update.message.reply_text(f"Resultado: {format_number(cantidad / tasa)}")
    except:
        await update.message.reply_text("❌ Error en conversión.")

async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await check_suscripcion(query.from_user.id, context):
        await query.edit_message_text("✅ Verificado. Ya puedes usar los comandos.")
    else:
        await query.answer("❌ Aún no estás suscrito.", show_alert=True)


# ── Panel Admin ──
async def gfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    await update.message.reply_text("🔐 Panel Admin", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📣 Global", callback_data="admin_global"), InlineKeyboardButton("✉️ Usuario", callback_data="admin_usuario")]]))
    return ADMIN_MENU

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "admin_global": return ESPERANDO_MSG_GLOBAL
    if query.data == "admin_usuario": return ESPERANDO_USUARIO
    return ConversationHandler.END

async def recibir_msg_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for uid in context.bot_data.get("usuarios", []):
        try: await context.bot.send_message(chat_id=uid, text=update.message.text)
        except: pass
    await update.message.reply_text("✅ Enviado.")
    return ConversationHandler.END

async def recibir_usuario_destino(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["admin_destino"] = update.message.text.strip()
    await update.message.reply_text("Escribe el mensaje:")
    return ESPERANDO_MSG_USUARIO

async def recibir_msg_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    destino = context.user_data.get("admin_destino")
    try:
        chat_id = int(destino) if destino.lstrip("-").isdigit() else destino
        await context.bot.send_message(chat_id=chat_id, text=update.message.text)
        await update.message.reply_text("✅ Enviado.")
    except: await update.message.reply_text("❌ Error.")
    return ConversationHandler.END

async def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        if "usuarios" not in context.bot_data: context.bot_data["usuarios"] = set()
        context.bot_data["usuarios"].add(update.effective_user.id)

async def settasa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    try:
        usd, eur = float(context.args[0]), float(context.args[1])
        _cache["bcv"].update({"rates": {"USD": usd, "EUR": eur}, "date": get_effective_date()})
        save_cache()
        await update.message.reply_text("✅ Tasas manuales fijadas.")
    except: await update.message.reply_text("Uso: /settasa USD EUR")


# ── Main ──
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    threading.Thread(target=run_http_server, daemon=True).start()
    load_cache()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bcv", bcv))
    app.add_handler(CommandHandler("calcular", calcular))
    app.add_handler(CommandHandler("convertir", convertir))
    app.add_handler(CommandHandler("settasa", settasa))
    app.add_handler(CommandHandler("banear", banear))
    app.add_handler(CommandHandler("desbanear", desbanear))
    app.add_handler(CommandHandler("baneados", lista_baneados))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("gfa", gfa)],
        states={
            ADMIN_MENU: [CallbackQueryHandler(admin_callback, pattern="^admin_")],
            ESPERANDO_MSG_GLOBAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_msg_global)],
            ESPERANDO_USUARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_usuario_destino)],
            ESPERANDO_MSG_USUARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_msg_usuario)],
        },
        fallbacks=[CommandHandler("cancelar", lambda u,c: ConversationHandler.END)]
    ))
    app.add_handler(MessageHandler(filters.ALL, registrar_usuario), group=1)
    app.run_polling(poll_interval=2.0)

if __name__ == "__main__":
    load_cache()
    
    # Hilo para el servidor HTTP (Evita que Render mate el proceso)
    threading.Thread(target=run_http_server, daemon=True).start()
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No se encontró TELEGRAM_BOT_TOKEN")
        exit(1)

    # Construcción de la aplicación
    app = Application.builder().token(token).build()

    # Comandos públicos
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("settasa",   settasa))
    app.add_handler(CommandHandler("bcv",       bcv))
    app.add_handler(CommandHandler("calcular",  calcular))
    app.add_handler(CommandHandler("convertir", convertir))
    app.add_handler(CommandHandler("banear",    banear))
    app.add_handler(CommandHandler("desbanear", desbanear))
    app.add_handler(CommandHandler("baneados",  lista_baneados))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))

    # Panel Admin
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("gfa", gfa)],
        states={
            ADMIN_MENU: [CallbackQueryHandler(admin_callback, pattern="^admin_")],
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

    # Ejecución estable
    logger.info("Bot en marcha...")
    app.run_polling(drop_pending_updates=True)
