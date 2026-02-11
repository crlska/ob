import json
import os
import logging
from datetime import datetime, time
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import google.generativeai as genai

# --- Config ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))
WARDROBE_FILE = Path(__file__).parent / "wardrobe.json"
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "7"))  # hora local para outfit diario
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", "-6"))  # CST Mexico

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outfit-bot")

# --- Wardrobe Manager ---
class Wardrobe:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        return self._default()

    def _default(self):
        return {
            "profile": {
                "name": "",
                "style_notes": "Estilo casual con toques urbanos. No le gusta complicarse. Quiere verse bien sin esfuerzo.",
                "preferences": "Prefiere outfits simples pero con un detalle que destaque."
            },
            "categories": {
                "underwear": [],
                "socks": [],
                "calzado": [],
                "pantalones": [],
                "tops": [],
                "capas": [],
                "extras": []
            },
            "items": {},
            "history": [],
            "feedback": []
        }

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_item(self, category: str, name: str, details: dict = None):
        item_id = f"{category}_{len(self.data['items'])+1}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        item = {
            "id": item_id,
            "name": name,
            "category": category,
            "status": "clean",  # clean, dirty, lost, damaged
            "details": details or {},
            "added": datetime.now().isoformat(),
            "times_worn": 0,
            "last_worn": None
        }
        self.data["items"][item_id] = item
        if category not in self.data["categories"]:
            self.data["categories"][category] = []
        self.data["categories"][category].append(item_id)
        self.save()
        return item_id

    def set_status(self, item_id: str, status: str, reason: str = ""):
        if item_id in self.data["items"]:
            self.data["items"][item_id]["status"] = status
            if reason:
                self.data["items"][item_id]["status_reason"] = reason
            self.save()
            return True
        return False

    def get_available(self, category: str = None):
        items = self.data["items"]
        available = {k: v for k, v in items.items() if v["status"] == "clean"}
        if category:
            available = {k: v for k, v in available.items() if v["category"] == category}
        return available

    def get_all_items(self):
        return self.data["items"]

    def record_outfit(self, outfit: dict):
        entry = {
            "date": datetime.now().isoformat(),
            "outfit": outfit,
        }
        self.data["history"].append(entry)
        for item_id in outfit.values():
            if item_id in self.data["items"]:
                self.data["items"][item_id]["times_worn"] += 1
                self.data["items"][item_id]["last_worn"] = datetime.now().isoformat()
        self.save()

    def add_feedback(self, feedback: str):
        self.data["feedback"].append({
            "date": datetime.now().isoformat(),
            "feedback": feedback
        })
        self.save()

    def get_inventory_summary(self):
        lines = []
        for cat, item_ids in self.data["categories"].items():
            if not item_ids:
                continue
            lines.append(f"\n📦 {cat.upper()}")
            for iid in item_ids:
                item = self.data["items"].get(iid)
                if item:
                    status_emoji = {"clean": "✅", "dirty": "🧺", "lost": "❓", "damaged": "⚠️"}.get(item["status"], "❔")
                    lines.append(f"  {status_emoji} {item['name']} (#{iid[-4:]})")
        return "\n".join(lines) if lines else "Tu guardarropa está vacío. Usa /add para agregar prendas."

    def get_context_for_ai(self):
        available = self.get_available()
        dirty = {k: v for k, v in self.data["items"].items() if v["status"] == "dirty"}
        recent = self.data["history"][-7:] if self.data["history"] else []

        context = {
            "profile": self.data["profile"],
            "available_items": {k: {"name": v["name"], "category": v["category"], "details": v.get("details", {})} for k, v in available.items()},
            "dirty_items": [v["name"] for v in dirty.values()],
            "recent_outfits": recent,
            "feedback_history": self.data["feedback"][-10:] if self.data["feedback"] else []
        }
        return json.dumps(context, ensure_ascii=False, indent=2)


# --- AI Outfit Engine ---
SYSTEM_PROMPT = """Eres un asistente personal de moda para una mujer queer de 36 años que prefiere vestir masculino/andrógino. Estilo casual urbano, un poco alternativo, ligeramente edgy pero simple.

SOBRE ELLA:
- Trabaja en tech, su día a día es casual y funcional
- No quiere verse flashy, influencer, ni overdressed
- Prefiere upgrades sutiles, no cambios drásticos
- Valora la comodidad pero quiere verse más intencional y atractiva
- Le gustan colores oscuros y neutros, evita lo formal
- Su meta: verse más confident, clean y put together sin cambiar quién es

REGLAS:
1. Solo sugiere prendas que están DISPONIBLES (status: clean) en su guardarropa
2. Incluye TODO: underwear, calcetines, pantalón, top, calzado, y extras si aplican
3. Incluye banda de smartwatch que combine con el outfit (ella siempre lo usa)
4. Si sugiere gorra, menciona modelo y forma específica
5. Si algo importante está sucio, dile que lo lave con humor
6. Considera el clima, la ocasión, y los últimos outfits para no repetir
7. Sé directo, breve, con personalidad. Como un amigo que sabe de moda
8. Si le dices que se ponga algo, dile POR QUÉ funciona (1 línea max)
9. Responde siempre en español casual mexicano
10. Toma en cuenta su tipo de cuerpo, tono de piel y undertone para las combinaciones (los datos están en el perfil)
11. Si está en proceso de bajar de peso, sugiere prendas que favorezcan su figura actual sin hacerla sentir mal
12. El fit importa: sugiere cómo debería quedarle cada prenda (holgado, justo, etc.)
13. Usa los detalles de marca, modelo y color cuando estén disponibles para ser específico

FORMATO DE RESPUESTA para outfits:
🔥 [Nombre creativo del outfit]

🩲 Underwear: [prenda]
🧦 Calcetines: [prenda]
👖 Pantalón: [prenda]
👕 Top: [prenda]
👟 Calzado: [prenda]
🧥 Capa (si aplica): [prenda]
🧢 Gorra (si aplica): [modelo específico]
⌚ Banda smartwatch: [color/tipo]
🎒 Extras: [accesorios]

💡 [Por qué funciona - 1-2 líneas max]
⚠️ [Alertas: ropa sucia que debería lavar, etc.]
"""

async def get_ai_suggestion(wardrobe: Wardrobe, user_message: str) -> str:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=SYSTEM_PROMPT
    )
    wardrobe_context = wardrobe.get_context_for_ai()

    today = datetime.now()
    day_info = f"Hoy es {today.strftime('%A %d de %B %Y')}, hora: {today.strftime('%H:%M')}"

    response = model.generate_content(
        f"""CONTEXTO DEL GUARDARROPA:
{wardrobe_context}

FECHA: {day_info}

SOLICITUD DEL USUARIO: {user_message}"""
    )
    return response.text


# --- Telegram Handlers ---
wardrobe = Wardrobe(WARDROBE_FILE)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👔 ¡Outfit Bot activado!\n\n"
        "Comandos:\n"
        "/outfit [ocasión] — Pide un outfit\n"
        "/add [categoría] [nombre] — Agrega prenda\n"
        "/dirty [#id] [razón] — Marcar como sucia\n"
        "/clean [#id] — Marcar como limpia\n"
        "/lost [#id] — Marcar como perdida\n"
        "/closet — Ver tu guardarropa\n"
        "/available — Ver solo lo disponible\n"
        "/feedback [texto] — Dar feedback\n"
        "/daily on/off — Outfit diario automático\n"
        "/bulk — Agregar varias prendas de golpe\n"
        "/profile — Ver/editar tu perfil (peso, pelo, etc.)\n\n"
        "O simplemente escríbeme como: 'voy a un bar con amigos' y te armo el outfit 🔥"
    )

async def cmd_outfit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    occasion = " ".join(context.args) if context.args else "día normal, ir al trabajo"
    await update.message.reply_text("🤔 Déjame ver tu clóset...")
    try:
        suggestion = await get_ai_suggestion(wardrobe, occasion)
        await update.message.reply_text(suggestion)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("❌ Error consultando el cerebro fashionista. Intenta de nuevo.")

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        categories = list(wardrobe.data["categories"].keys())
        await update.message.reply_text(
            f"Uso: /add [categoría] [nombre]\n\n"
            f"Categorías: {', '.join(categories)}\n\n"
            f"Ejemplos:\n"
            f"/add calzado Dr Martens 1460 negras\n"
            f"/add gorras New Era 9FORTY negra curva\n"
            f"/add smartwatch_bands banda sport negra\n\n"
            f"Para más detalle usa /addpro"
        )
        return
    category = context.args[0].lower()
    name = " ".join(context.args[1:])
    item_id = wardrobe.add_item(category, name)
    short_id = item_id[-4:]
    await update.message.reply_text(f"✅ Agregado: {name} → {category} (#{short_id})")

async def cmd_addpro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Manda la prenda con detalles en este formato:\n\n"
        "categoría: nombre | marca: X | color: X | modelo: X | fit: X | notas: X\n\n"
        "Ejemplos:\n"
        "calzado: boots negras | marca: Dr Martens | modelo: 1460 | color: negro mate\n"
        "gorras: gorra negra | marca: New Era | modelo: 9FORTY | color: negro | notas: curva ajustable\n"
        "smartwatch_bands: banda sport | color: negro | notas: silicón para Apple Watch\n"
        "tops: playera cuello V | marca: H&M | color: negro | fit: slim\n\n"
        "Solo 'categoría: nombre' es obligatorio, lo demás es opcional."
    )
    context.user_data["awaiting_addpro"] = True

async def cmd_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Manda tu inventario en este formato (una prenda por línea):\n\n"
        "categoría: nombre de prenda\n\n"
        "Ejemplo:\n"
        "calzado: Dr Martens negras\n"
        "calzado: Nike Air Force blancas\n"
        "pantalones: jean azul oscuro recto\n"
        "tops: playera negra básica\n"
        "underwear: boxer negro Calvin Klein\n"
        "socks: calcetines negros lisos\n\n"
        "Categorías válidas: underwear, socks, calzado, pantalones, tops, capas, extras"
    )
    context.user_data["awaiting_bulk"] = True

async def cmd_status_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text.split()[0].replace("/", "")
    status_map = {"dirty": "dirty", "clean": "clean", "lost": "lost"}
    new_status = status_map.get(command, "clean")

    if not context.args:
        await update.message.reply_text(f"Uso: /{command} [#id] [razón opcional]\nEjemplo: /{command} a3f1 lo dejé en casa de Juan")
        return

    search = context.args[0].replace("#", "")
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""

    # find item by partial id match
    found = None
    for item_id in wardrobe.data["items"]:
        if item_id.endswith(search) or search in item_id:
            found = item_id
            break

    if found:
        wardrobe.set_status(found, new_status, reason)
        item_name = wardrobe.data["items"][found]["name"]
        emoji = {"clean": "✅", "dirty": "🧺", "lost": "❓"}.get(new_status, "📌")
        msg = f"{emoji} {item_name} → {new_status}"
        if reason:
            msg += f" ({reason})"
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text(f"❌ No encontré prenda con ID que contenga '{search}'. Usa /closet para ver IDs.")

async def cmd_closet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = wardrobe.get_inventory_summary()
    await update.message.reply_text(f"👔 TU GUARDARROPA:\n{summary}")

async def cmd_available(update: Update, context: ContextTypes.DEFAULT_TYPE):
    available = wardrobe.get_available()
    if not available:
        await update.message.reply_text("😬 No tienes nada limpio. ¡A lavar!")
        return
    lines = ["✅ DISPONIBLE AHORA:\n"]
    by_cat = {}
    for item in available.values():
        cat = item["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(item["name"])
    for cat, items in by_cat.items():
        lines.append(f"📦 {cat.upper()}")
        for name in items:
            lines.append(f"  • {name}")
    await update.message.reply_text("\n".join(lines))

async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /feedback me gustó el outfit de hoy")
        return
    fb = " ".join(context.args)
    wardrobe.add_feedback(fb)
    await update.message.reply_text("📝 Feedback guardado. Voy aprendiendo tu estilo 💪")

async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Uso: /daily on o /daily off")
        return

    if context.args[0].lower() == "on":
        wardrobe.data["profile"]["daily_enabled"] = True
        wardrobe.save()
        await update.message.reply_text(f"⏰ Outfit diario activado. Te mando outfit a las {DAILY_HOUR}:{DAILY_MINUTE:02d} todos los días.")
    else:
        wardrobe.data["profile"]["daily_enabled"] = False
        wardrobe.save()
        await update.message.reply_text("⏰ Outfit diario desactivado.")

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        body = wardrobe.data["profile"].get("body", {})
        lines = [
            "👤 TU PERFIL:\n",
            f"🎂 Edad: {body.get('age', '?')}",
            f"📏 Estatura: {body.get('height_cm', '?')} cm",
            f"⚖️ Peso actual: {body.get('weight_kg', '?')} kg",
            f"🎯 Peso meta: {body.get('target_weight_kg', '?')} kg",
            f"🎨 Tono de piel: {body.get('skin_tone', '?')}",
            f"✨ Subtono: {body.get('undertone', '?')}",
            f"💇 Cabello: {body.get('hair', '?')}",
            "\nPara actualizar usa:",
            "/profile peso 70",
            "/profile pelo corto pixie",
            "/profile edad 37",
            "/profile tono moreno medio",
            "/profile meta 58",
        ]
        await update.message.reply_text("\n".join(lines))
        return

    field = context.args[0].lower()
    value = " ".join(context.args[1:])
    if not value:
        await update.message.reply_text("Falta el valor. Ej: /profile peso 70")
        return

    body = wardrobe.data["profile"].setdefault("body", {})
    field_map = {
        "peso": ("weight_kg", float), "weight": ("weight_kg", float),
        "meta": ("target_weight_kg", float), "target": ("target_weight_kg", float),
        "edad": ("age", int), "age": ("age", int),
        "pelo": ("hair", str), "hair": ("hair", str), "cabello": ("hair", str),
        "tono": ("skin_tone", str), "skin": ("skin_tone", str),
        "subtono": ("undertone", str), "undertone": ("undertone", str),
        "estatura": ("height_cm", float), "height": ("height_cm", float),
    }

    if field in field_map:
        key, cast = field_map[field]
        try:
            body[key] = cast(value) if cast != str else value
            wardrobe.save()
            await update.message.reply_text(f"✅ {key} actualizado → {body[key]}")
        except ValueError:
            await update.message.reply_text(f"❌ Valor inválido para {field}")
    else:
        await update.message.reply_text(f"❌ Campo '{field}' no reconocido. Usa: peso, meta, edad, pelo, tono, subtono, estatura")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # Handle addpro (single detailed item)
    if context.user_data.get("awaiting_addpro"):
        context.user_data["awaiting_addpro"] = False
        items_added = _parse_detailed_lines([text.strip()])
        if items_added:
            await update.message.reply_text(f"✅ {items_added[0]}")
        else:
            await update.message.reply_text("❌ No pude parsear eso. Revisa el formato con /addpro")
        return

    # Handle bulk add (supports both simple and detailed format)
    if context.user_data.get("awaiting_bulk"):
        context.user_data["awaiting_bulk"] = False
        lines = text.strip().split("\n")
        results = _parse_detailed_lines(lines)
        added = len(results)
        msg = f"✅ {added} prendas agregadas."
        if added == 0:
            msg = "❌ No pude agregar nada. Revisa el formato."
        await update.message.reply_text(msg)
        return

    # Default: treat as outfit request
    await update.message.reply_text("🤔 Analizando...")
    try:
        suggestion = await get_ai_suggestion(wardrobe, text)
        await update.message.reply_text(suggestion)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("❌ Error. Intenta de nuevo.")

def _parse_detailed_lines(lines):
    results = []
    valid_cats = ["underwear", "socks", "calzado", "pantalones", "tops", "capas", "gorras", "smartwatch_bands", "extras"]
    for line in lines:
        line = line.strip()
        if not line or ":" not in line:
            continue
        # Split first : for category:name, then parse | for details
        first_split = line.split("|")
        cat_name = first_split[0]
        cat_parts = cat_name.split(":", 1)
        category = cat_parts[0].strip().lower()
        name = cat_parts[1].strip() if len(cat_parts) > 1 else ""
        if not name or category not in valid_cats:
            continue
        details = {}
        for part in first_split[1:]:
            if ":" in part:
                key, val = part.split(":", 1)
                details[key.strip().lower()] = val.strip()
        item_id = wardrobe.add_item(category, name, details)
        results.append(f"{name} → {category} (#{item_id[-4:]})")
    return results

async def send_daily_outfit(context: ContextTypes.DEFAULT_TYPE):
    if not wardrobe.data["profile"].get("daily_enabled"):
        return
    if OWNER_CHAT_ID == 0:
        return
    try:
        suggestion = await get_ai_suggestion(wardrobe, "outfit para ir al trabajo hoy, algo casual pero presentable")
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=f"☀️ Buenos días! Tu outfit de hoy:\n\n{suggestion}")
    except Exception as e:
        logger.error(f"Daily outfit error: {e}")

# --- Main ---
def main():
    if not TELEGRAM_TOKEN:
        print("❌ Falta TELEGRAM_TOKEN en variables de entorno")
        return
    if not GEMINI_API_KEY:
        print("❌ Falta GEMINI_API_KEY en variables de entorno")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("outfit", cmd_outfit))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("addpro", cmd_addpro))
    app.add_handler(CommandHandler("bulk", cmd_bulk))
    app.add_handler(CommandHandler("dirty", cmd_status_change))
    app.add_handler(CommandHandler("clean", cmd_status_change))
    app.add_handler(CommandHandler("lost", cmd_status_change))
    app.add_handler(CommandHandler("closet", cmd_closet))
    app.add_handler(CommandHandler("available", cmd_available))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Daily job
    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
    job_time = time(hour=DAILY_HOUR, minute=DAILY_MINUTE, tzinfo=tz)
    app.job_queue.run_daily(send_daily_outfit, time=job_time)

    print("🤖 Outfit Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()
