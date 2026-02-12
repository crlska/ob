import json
import os
import logging
import urllib.request
import urllib.parse
from datetime import datetime, time
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
import google.generativeai as genai

# --- Config ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))
WARDROBE_FILE = Path(__file__).parent / "wardrobe.json"
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "7"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", "-6"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outfit-bot")

ALL_CATEGORIES = [
    "underwear", "socks", "calzado", "pantalones", "tops", "capas",
    "gorras", "smartwatch_bands", "relojes", "anillos", "cadenas",
    "pulseras", "earplugs", "lentes", "extras"
]

# --- Weather ---
def get_weather(city: str) -> str:
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "outfit-bot"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        current = data["current_condition"][0]
        temp = current["temp_C"]
        feels = current["FeelsLikeC"]
        desc = current["lang_es"][0]["value"] if "lang_es" in current and current["lang_es"] else current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        forecast_today = data["weather"][0]
        max_t = forecast_today["maxtempC"]
        min_t = forecast_today["mintempC"]
        rain_chance = forecast_today["hourly"][4].get("chanceofrain", "0") if len(forecast_today["hourly"]) > 4 else "0"
        return (
            f"Clima en {city}: {desc}, {temp}°C (sensación {feels}°C), "
            f"min {min_t}°C / max {max_t}°C, humedad {humidity}%, "
            f"probabilidad de lluvia {rain_chance}%"
        )
    except Exception as e:
        logger.warning(f"Weather error for {city}: {e}")
        return f"No pude obtener el clima de {city}"


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
                "city": "Saltillo, Coahuila",
                "body": {
                    "age": 36, "height_cm": 162, "weight_kg": 75,
                    "target_weight_kg": 60,
                    "skin_tone": "moreno claro / light medium",
                    "undertone": "cálido-neutral, más dorado que rosado",
                    "hair": "al hombro"
                },
                "identity": "Mujer queer, prefiere vestir masculino/andrógino",
                "style_notes": "Casual urbano, un poco alternativo, ligeramente edgy pero simple. Colores oscuros y neutros. Nada formal, nada influencer, nada overdressed.",
                "preferences": "Comodidad con intención. Combinaciones clean y put together. Tech worker, estilo casual funcional.",
                "goal": "Step up un poco, mejor match entre prendas, más segura sin cambiar su esencia.",
                "daily_enabled": False
            },
            "categories": {cat: [] for cat in ALL_CATEGORIES},
            "items": {},
            "history": [],
            "feedback": [],
            "packing_lists": {
                "basicos": {
                    "description": "Lo que siempre traigo conmigo (bolsa/pockets/mochila pequeña)",
                    "items": [
                        "Audifonos/earplugs",
                        "Cargador USB-C",
                        "Batería portátil",
                        "Cable lightning",
                        "Cartera",
                        "Llaves",
                        "Lip balm"
                    ]
                },
                "festival": {
                    "description": "Equipo para trabajo en festivales (foto/video)",
                    "items": [
                        "Cámara",
                        "Baterías extra cámara x3",
                        "Cargador baterías",
                        "Memorias SD x4",
                        "Strap de hombro",
                        "Monopod",
                        "Lens cleaning kit",
                        "Rain cover cámara",
                        "Laptop + cargador",
                        "HDD externo"
                    ]
                },
                "viaje": {
                    "description": "Esenciales para cualquier viaje",
                    "items": [
                        "Underwear (días + 1)",
                        "Socks (días + 1)",
                        "Flip flops",
                        "Toalla",
                        "Cepillo de dientes",
                        "Pasta dental",
                        "Desodorante",
                        "Shampoo travel size",
                        "Cargadores",
                        "Batería portátil",
                        "Medicinas básicas",
                        "Pijama"
                    ]
                }
            }
        }

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_item(self, category: str, name: str, details: dict = None):
        item_id = f"{category}_{len(self.data['items'])+1}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        item = {
            "id": item_id, "name": name, "category": category,
            "status": "clean", "details": details or {},
            "added": datetime.now().isoformat(),
            "times_worn": 0, "last_worn": None
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

    def record_outfit(self, outfit: dict):
        entry = {"date": datetime.now().isoformat(), "outfit": outfit}
        self.data["history"].append(entry)
        for item_id in outfit.values():
            if item_id in self.data["items"]:
                self.data["items"][item_id]["times_worn"] += 1
                self.data["items"][item_id]["last_worn"] = datetime.now().isoformat()
        self.save()

    def add_feedback(self, feedback: str):
        self.data["feedback"].append({"date": datetime.now().isoformat(), "feedback": feedback})
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
                    emoji = {"clean": "✅", "dirty": "🧺", "lost": "❓", "damaged": "⚠️"}.get(item["status"], "❔")
                    details_str = ""
                    if item.get("details"):
                        details_str = " | " + ", ".join(f"{k}: {v}" for k, v in item["details"].items())
                    lines.append(f"  {emoji} {item['name']}{details_str} (#{iid[-4:]})")
        return "\n".join(lines) if lines else "Tu guardarropa está vacío. Usa /add para agregar prendas."

    def get_context_for_ai(self):
        available = self.get_available()
        dirty = {k: v for k, v in self.data["items"].items() if v["status"] == "dirty"}
        recent = self.data["history"][-7:] if self.data["history"] else []
        context = {
            "profile": self.data["profile"],
            "available_items": {
                k: {"name": v["name"], "category": v["category"], "details": v.get("details", {})}
                for k, v in available.items()
            },
            "dirty_items": [f"{v['name']} ({v['category']})" for v in dirty.values()],
            "recent_outfits": recent,
            "feedback_history": self.data["feedback"][-10:] if self.data["feedback"] else [],
            "packing_lists": self.data.get("packing_lists", {})
        }
        return json.dumps(context, ensure_ascii=False, indent=2)

    def get_city(self):
        return self.data["profile"].get("city", "Saltillo, Coahuila")

    # --- Packing Lists ---
    def get_list(self, name: str):
        return self.data.get("packing_lists", {}).get(name)

    def get_all_lists(self):
        return self.data.get("packing_lists", {})

    def add_list_item(self, list_name: str, item: str):
        lists = self.data.setdefault("packing_lists", {})
        if list_name not in lists:
            lists[list_name] = {"description": "", "items": []}
        lists[list_name]["items"].append(item)
        self.save()

    def remove_list_item(self, list_name: str, index: int):
        lists = self.data.get("packing_lists", {})
        if list_name in lists and 0 <= index < len(lists[list_name]["items"]):
            removed = lists[list_name]["items"].pop(index)
            self.save()
            return removed
        return None

    def create_list(self, name: str, description: str = ""):
        lists = self.data.setdefault("packing_lists", {})
        if name not in lists:
            lists[name] = {"description": description, "items": []}
            self.save()
            return True
        return False

    def delete_list(self, name: str):
        lists = self.data.get("packing_lists", {})
        if name in lists:
            del lists[name]
            self.save()
            return True
        return False


# --- AI Outfit Engine ---
SYSTEM_PROMPT = """Eres un stylist personal de Los Angeles. Tu clienta es una mujer queer de 36 años que prefiere vestir masculino/andrógino. Tu vibe es edgy pero accesible — piensa East LA meets Silverlake, no West Hollywood.

SOBRE ELLA:
- Trabaja en tech/data/automation, día a día casual y funcional
- No quiere verse flashy, influencer, ni overdressed
- Prefiere upgrades sutiles, no cambios drásticos
- Colores oscuros y neutros, evita lo formal
- Valora comodidad pero quiere verse más intencional y atractiva
- Su meta: verse más confident, clean y put together sin cambiar quién es

REGLAS:
1. SOLO sugiere prendas DISPONIBLES (status: clean) en su guardarropa
2. Incluye: underwear, calcetines, pantalón, top, calzado. Capa solo si el clima lo requiere
3. Sugiere reloj O smartwatch+banda según el outfit (tiene ambos). No siempre smartwatch — un reloj análogo puede elevar más el look
4. Sugiere color de earplugs que combine (siempre los trae)
5. Si sugiere gorra, menciona modelo y forma específica
6. Si algo importante está sucio, dile que lo lave con humor
7. Considera el CLIMA (se te dará info del clima actual) y la ocasión
8. Considera últimos outfits para no repetir
9. Sé directo, breve, con personalidad. Como un stylist amigo edgy de LA
10. Responde en español casual mexicano (con anglicismos naturales de moda)
11. Toma en cuenta tipo de cuerpo, tono de piel y undertone del perfil
12. Sugiere prendas que favorezcan su figura actual sin hacerla sentir mal
13. El fit importa: sugiere cómo debería quedar cada prenda
14. Usa marca, modelo y color cuando estén disponibles
15. Joyería: no mezclar metales, max 2-3 anillos, sugiere mano/dedo. Complementar sin saturar
16. Para VIAJES: minimiza items, maximiza combinaciones. Repetir calzado está bien. Prioriza prendas versátiles que sirvan para múltiples outfits
17. Para viajes de varios días, sugiere outfits que compartan piezas (ej: mismo jean, diferente top)

FORMATO para outfit de un día:
🔥 [Nombre creativo del outfit]

🩲 Underwear: [prenda]
🧦 Calcetines: [prenda]
👖 Pantalón: [prenda]
👕 Top: [prenda]
👟 Calzado: [prenda]
🧥 Capa (si aplica): [prenda]
🧢 Gorra (si aplica): [modelo]
⌚ Reloj/Smartwatch: [cuál y por qué]
🎧 Earplugs: [color que combine]
💍 Joyería: [anillos, cadenas, pulseras]
🎒 Extras: [otros]

💡 [Por qué funciona - 1-2 líneas]
⚠️ [Alertas si hay]

FORMATO para viaje de varios días:
🧳 PACKING LIST — [destino] ([días] días)

📦 LO QUE LLEVAS:
[lista de todas las prendas únicas que necesita empacar]

Luego cada día:
📅 DÍA X — [ocasión]
[outfit del día en formato normal]

💡 NOTAS DE VIAJE:
[tips de combinación, qué se repite, etc.]
"""

async def get_ai_suggestion(wardrobe: Wardrobe, user_message: str, city_override: str = None) -> str:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-preview-05-20",
        system_instruction=SYSTEM_PROMPT
    )
    wardrobe_context = wardrobe.get_context_for_ai()
    city = city_override or wardrobe.get_city()
    weather = get_weather(city)
    today = datetime.now()
    day_info = f"Hoy es {today.strftime('%A %d de %B %Y')}, hora: {today.strftime('%H:%M')}"

    response = model.generate_content(
        f"""CONTEXTO DEL GUARDARROPA:
{wardrobe_context}

CLIMA ACTUAL:
{weather}

FECHA: {day_info}
CIUDAD: {city}

SOLICITUD: {user_message}"""
    )
    return response.text


# --- Telegram Handlers ---
wardrobe = Wardrobe(WARDROBE_FILE)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👔 Outfit Bot — tu stylist personal\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💬 PEDIR OUTFIT:\n"
        "Escríbeme directo, como si hablaras con un amigo:\n"
        "• 'voy a un bar con amigos'\n"
        "• 'junta de zoom pero quiero verme bien'\n"
        "• 'me voy a CDMX 3 días, concierto de rock'\n"
        "• 'outfit para hoy, hace frío'\n"
        "O usa /outfit [ocasión] si prefieres\n\n"
        "👕 GUARDARROPA:\n"
        "/add [cat] [nombre] — Agregar prenda rápido\n"
        "/addpro — Agregar con detalles (marca, color, modelo...)\n"
        "/bulk — Agregar muchas prendas de golpe\n"
        "/closet — Ver todo tu guardarropa\n"
        "/available — Solo lo que está limpio\n\n"
        "🧺 STATUS DE PRENDAS:\n"
        "/dirty [#id] [razón] — Marcar sucia\n"
        "/clean [#id] — Marcar limpia\n"
        "/lost [#id] [dónde] — Marcar perdida\n\n"
        "👤 PERFIL:\n"
        "/profile — Ver tu perfil\n"
        "/profile peso 70 — Actualizar dato\n"
        "/city — Ver ciudad + clima actual\n"
        "/city CDMX — Cambiar ciudad default\n\n"
        "📋 PACKING LISTS (sin AI):\n"
        "/lists — Ver todas tus listas\n"
        "/list basicos — Ver una lista\n"
        "/listadd basicos Kindle — Agregar item\n"
        "/listdel basicos 3 — Quitar item #3\n"
        "/listnew nombre descripción — Crear lista\n"
        "/listremove nombre — Eliminar lista\n\n"
        "⚙️ CONFIG:\n"
        "/daily on — Outfit automático cada mañana\n"
        "/daily off — Desactivar\n"
        "/feedback [texto] — Dar feedback al bot\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Ciudad: {wardrobe.get_city()}\n"
        f"⏰ Outfit diario: {'ON' if wardrobe.data['profile'].get('daily_enabled') else 'OFF'}\n\n"
        f"Categorías válidas para /add:\n{', '.join(ALL_CATEGORIES)}"
    )

async def cmd_outfit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    occasion = " ".join(context.args) if context.args else "día normal, ir al trabajo"
    await update.message.reply_text("🤔 Checando tu clóset y el clima...")
    try:
        suggestion = await get_ai_suggestion(wardrobe, occasion)
        await update.message.reply_text(suggestion)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("❌ Error. Intenta de nuevo.")

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            f"Uso: /add [categoría] [nombre]\n\n"
            f"Categorías:\n{', '.join(ALL_CATEGORIES)}\n\n"
            f"Ej:\n"
            f"/add calzado Dr Martens 1460 negras\n"
            f"/add relojes Casio A168 plateado\n"
            f"/add earplugs dorado shiny\n\n"
            f"Para más detalle: /addpro"
        )
        return
    category = context.args[0].lower()
    name = " ".join(context.args[1:])
    if category not in ALL_CATEGORIES:
        await update.message.reply_text(f"❌ Categoría '{category}' no existe.\nVálidas: {', '.join(ALL_CATEGORIES)}")
        return
    item_id = wardrobe.add_item(category, name)
    await update.message.reply_text(f"✅ {name} → {category} (#{item_id[-4:]})")

async def cmd_addpro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Formato:\ncategoría: nombre | marca: X | color: X | modelo: X | fit: X | notas: X\n\n"
        "Ej:\n"
        "calzado: boots 1460 | marca: Dr Martens | color: negro mate\n"
        "relojes: A168 retro | marca: Casio | color: plateado | notas: digital vintage\n"
        "earplugs: loops | color: dorado shiny | notas: silicón\n\n"
        "Solo 'categoría: nombre' es obligatorio."
    )
    context.user_data["awaiting_addpro"] = True

async def cmd_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Una prenda por línea:\n\n"
        "categoría: nombre\n"
        "O con detalle:\n"
        "categoría: nombre | marca: X | color: X\n\n"
        f"Categorías: {', '.join(ALL_CATEGORIES)}"
    )
    context.user_data["awaiting_bulk"] = True

async def cmd_status_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text.split()[0].replace("/", "")
    status_map = {"dirty": "dirty", "clean": "clean", "lost": "lost"}
    new_status = status_map.get(command, "clean")
    if not context.args:
        await update.message.reply_text(f"Uso: /{command} [#id] [razón opcional]")
        return
    search = context.args[0].replace("#", "")
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
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
        await update.message.reply_text(f"❌ No encontré '{search}'. Usa /closet para ver IDs.")

async def cmd_closet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = wardrobe.get_inventory_summary()
    # Telegram max message length is 4096
    if len(summary) > 4000:
        parts = [summary[i:i+4000] for i in range(0, len(summary), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(f"👔 TU GUARDARROPA:\n{summary}")

async def cmd_available(update: Update, context: ContextTypes.DEFAULT_TYPE):
    available = wardrobe.get_available()
    if not available:
        await update.message.reply_text("😬 No tienes nada limpio. ¡A lavar!")
        return
    lines = ["✅ DISPONIBLE:\n"]
    by_cat = {}
    for item in available.values():
        cat = item["category"]
        by_cat.setdefault(cat, []).append(item["name"])
    for cat, items in by_cat.items():
        lines.append(f"📦 {cat.upper()}")
        for name in items:
            lines.append(f"  • {name}")
    await update.message.reply_text("\n".join(lines))

async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /feedback me gustó el outfit de hoy")
        return
    wardrobe.add_feedback(" ".join(context.args))
    await update.message.reply_text("📝 Feedback guardado 💪")

async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Uso: /daily on o /daily off")
        return
    on = context.args[0].lower() == "on"
    wardrobe.data["profile"]["daily_enabled"] = on
    wardrobe.save()
    if on:
        await update.message.reply_text(f"⏰ Outfit diario ON → {DAILY_HOUR}:{DAILY_MINUTE:02d} cada mañana")
    else:
        await update.message.reply_text("⏰ Outfit diario OFF")

async def cmd_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        current = wardrobe.get_city()
        weather = get_weather(current)
        await update.message.reply_text(f"📍 Ciudad actual: {current}\n🌤️ {weather}\n\nCambiar: /city Monterrey")
        return
    new_city = " ".join(context.args)
    wardrobe.data["profile"]["city"] = new_city
    wardrobe.save()
    weather = get_weather(new_city)
    await update.message.reply_text(f"📍 Ciudad actualizada → {new_city}\n🌤️ {weather}")

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        body = wardrobe.data["profile"].get("body", {})
        city = wardrobe.get_city()
        lines = [
            "👤 TU PERFIL:\n",
            f"📍 Ciudad: {city}",
            f"🎂 Edad: {body.get('age', '?')}",
            f"📏 Estatura: {body.get('height_cm', '?')} cm",
            f"⚖️ Peso: {body.get('weight_kg', '?')} kg",
            f"🎯 Meta: {body.get('target_weight_kg', '?')} kg",
            f"🎨 Tono: {body.get('skin_tone', '?')}",
            f"✨ Subtono: {body.get('undertone', '?')}",
            f"💇 Cabello: {body.get('hair', '?')}",
            "\nActualizar: /profile [campo] [valor]",
            "Campos: peso, meta, edad, pelo, tono, subtono, estatura",
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
            await update.message.reply_text(f"✅ {key} → {body[key]}")
        except ValueError:
            await update.message.reply_text(f"❌ Valor inválido")
    else:
        await update.message.reply_text(f"❌ Campo '{field}' no reconocido.\nVálidos: peso, meta, edad, pelo, tono, subtono, estatura")

# --- Packing Lists Commands ---
async def cmd_lists(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_lists = wardrobe.get_all_lists()
    if not all_lists:
        await update.message.reply_text("📋 No tienes listas. Crea una con /listnew [nombre] [descripción]")
        return
    lines = ["📋 TUS LISTAS:\n"]
    for name, data in all_lists.items():
        desc = data.get("description", "")
        count = len(data.get("items", []))
        lines.append(f"  📌 {name} ({count} items){' — ' + desc if desc else ''}")
    lines.append("\nVer una: /list [nombre]")
    await update.message.reply_text("\n".join(lines))

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /list [nombre]\nEj: /list viaje, /list basicos, /list festival")
        return
    name = context.args[0].lower()
    lst = wardrobe.get_list(name)
    if not lst:
        available = ", ".join(wardrobe.get_all_lists().keys())
        await update.message.reply_text(f"❌ Lista '{name}' no existe.\nDisponibles: {available}")
        return
    lines = [f"📋 {name.upper()}", f"📝 {lst.get('description', '')}\n"]
    for i, item in enumerate(lst.get("items", [])):
        lines.append(f"  {i+1}. {item}")
    lines.append(f"\nAgregar: /listadd {name} [item]")
    lines.append(f"Quitar: /listdel {name} [#num]")
    await update.message.reply_text("\n".join(lines))

async def cmd_listadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /listadd [lista] [item]\nEj: /listadd basicos Kindle Paperwhite")
        return
    name = context.args[0].lower()
    item = " ".join(context.args[1:])
    if name not in wardrobe.get_all_lists():
        await update.message.reply_text(f"❌ Lista '{name}' no existe. Créala con /listnew {name}")
        return
    wardrobe.add_list_item(name, item)
    await update.message.reply_text(f"✅ '{item}' agregado a {name}")

async def cmd_listdel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /listdel [lista] [#num]\nEj: /listdel viaje 3")
        return
    name = context.args[0].lower()
    try:
        index = int(context.args[1].replace("#", "")) - 1
    except ValueError:
        await update.message.reply_text("❌ El número debe ser... un número")
        return
    removed = wardrobe.remove_list_item(name, index)
    if removed:
        await update.message.reply_text(f"🗑️ '{removed}' eliminado de {name}")
    else:
        await update.message.reply_text("❌ No encontré ese item. Usa /list [nombre] para ver números.")

async def cmd_listnew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /listnew [nombre] [descripción opcional]\nEj: /listnew camping Equipo para acampar")
        return
    name = context.args[0].lower()
    desc = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    if wardrobe.create_list(name, desc):
        await update.message.reply_text(f"✅ Lista '{name}' creada")
    else:
        await update.message.reply_text(f"⚠️ Lista '{name}' ya existe")

async def cmd_listremove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /listremove [nombre]\n⚠️ Esto elimina la lista completa")
        return
    name = context.args[0].lower()
    if wardrobe.delete_list(name):
        await update.message.reply_text(f"🗑️ Lista '{name}' eliminada")
    else:
        await update.message.reply_text(f"❌ Lista '{name}' no existe")

# --- Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if context.user_data.get("awaiting_addpro"):
        context.user_data["awaiting_addpro"] = False
        results = _parse_detailed_lines([text.strip()])
        if results:
            await update.message.reply_text(f"✅ {results[0]}")
        else:
            await update.message.reply_text("❌ No pude parsear. Revisa formato con /addpro")
        return

    if context.user_data.get("awaiting_bulk"):
        context.user_data["awaiting_bulk"] = False
        lines = text.strip().split("\n")
        results = _parse_detailed_lines(lines)
        added = len(results)
        if added:
            await update.message.reply_text(f"✅ {added} prendas agregadas.")
        else:
            await update.message.reply_text("❌ No pude agregar nada. Revisa el formato.")
        return

    await update.message.reply_text("🤔 Checando clóset y clima...")
    try:
        suggestion = await get_ai_suggestion(wardrobe, text)
        await update.message.reply_text(suggestion)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("❌ Error. Intenta de nuevo.")

def _parse_detailed_lines(lines):
    results = []
    for line in lines:
        line = line.strip()
        if not line or ":" not in line:
            continue
        first_split = line.split("|")
        cat_parts = first_split[0].split(":", 1)
        category = cat_parts[0].strip().lower()
        name = cat_parts[1].strip() if len(cat_parts) > 1 else ""
        if not name or category not in ALL_CATEGORIES:
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
    if not wardrobe.data["profile"].get("daily_enabled") or OWNER_CHAT_ID == 0:
        return
    try:
        suggestion = await get_ai_suggestion(wardrobe, "outfit para ir al trabajo hoy, casual pero presentable")
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=f"☀️ Buenos días! Tu outfit:\n\n{suggestion}")
    except Exception as e:
        logger.error(f"Daily outfit error: {e}")

# --- Main ---
def main():
    if not TELEGRAM_TOKEN:
        print("❌ Falta TELEGRAM_TOKEN")
        return
    if not GEMINI_API_KEY:
        print("❌ Falta GEMINI_API_KEY")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

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
    app.add_handler(CommandHandler("city", cmd_city))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("lists", cmd_lists))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("listadd", cmd_listadd))
    app.add_handler(CommandHandler("listdel", cmd_listdel))
    app.add_handler(CommandHandler("listnew", cmd_listnew))
    app.add_handler(CommandHandler("listremove", cmd_listremove))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
    job_time = time(hour=DAILY_HOUR, minute=DAILY_MINUTE, tzinfo=tz)
    app.job_queue.run_daily(send_daily_outfit, time=job_time)

    print("🤖 Outfit Bot corriendo...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
