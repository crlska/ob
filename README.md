# 👔 Outfit Bot — Tu Asistente Personal de Moda

Bot de Telegram que te dice qué ponerte cada día sin que tengas que pensar.

## Setup rápido (15 min)

### 1. Crear el bot en Telegram
- Abre Telegram, busca **@BotFather**
- Manda `/newbot`, ponle nombre (ej: "Mi Outfit Bot")
- Copia el **token** que te da

### 2. Obtener tu Chat ID
- Busca **@userinfobot** en Telegram
- Mándale cualquier mensaje
- Te responde con tu **Chat ID** (es un número)

### 3. API Key de Gemini (GRATIS)
- Ve a [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- Crea API key
- **Costo: $0** — Gemini 2.0 Flash tiene tier gratuito (15 RPM, 1M tokens/día)

### 4. Configurar y correr

```bash
cd outfit-bot
pip install -r requirements.txt
cp .env.example .env
# Edita .env con tus datos reales
export $(cat .env | xargs)
python bot.py
```

### 5. Cargar tu ropa

Opción A — Una por una:
```
/add calzado Dr Martens negras
```

Opción B — De golpe con `/bulk`, luego mandas:
```
calzado: Dr Martens negras
calzado: Nike Air Force blancas
calzado: Vans negras
pantalones: jean azul oscuro recto
pantalones: jean negro
pantalones: cargo verde oliva
tops: playera negra básica
tops: playera blanca
tops: camisa mezclilla
tops: hoodie gris
capas: chamarra de piel
extras: gorra negra
extras: reloj plateado
underwear: boxer negro
underwear: boxer gris
socks: calcetines negros lisos
socks: calcetines blancos
```

## Uso diario

| Comando | Qué hace |
|---------|----------|
| Texto libre | "voy a un bar con amigos" → te arma outfit |
| `/outfit trabajo` | Outfit para la ocasión |
| `/daily on` | Outfit automático cada mañana |
| `/dirty #id razón` | Marcar prenda como sucia |
| `/clean #id` | Marcar prenda como limpia |
| `/lost #id en casa de Juan` | Marcar como perdida |
| `/closet` | Ver todo tu guardarropa con status |
| `/available` | Ver solo lo que está limpio |
| `/feedback me gustó mucho` | Dar feedback para que aprenda |

---

## Subir a GitHub (repo privado)

Sí, repos privados son gratis en GitHub sin límite.

### Primera vez

```bash
# 1. Crear .gitignore (MUY IMPORTANTE - protege tus keys y data)
cd outfit-bot
cat > .gitignore << 'EOF'
.env
wardrobe.json
__pycache__/
*.pyc
EOF

# 2. Inicializar repo
git init
git add .
git commit -m "first commit: outfit bot"

# 3a. Con GitHub CLI (más fácil):
gh auth login
gh repo create outfit-bot --private --source=. --push

# 3b. O manual desde github.com/new:
#   - Nombre: outfit-bot
#   - Selecciona: ● Private
#   - NO marques "Add README"
#   - Create repository
#   - Luego:
git remote add origin https://github.com/TU_USUARIO/outfit-bot.git
git branch -M main
git push -u origin main
```

### Actualizar cambios

```bash
git add .
git commit -m "descripción del cambio"
git push
```

### Clonar en tu VPS

```bash
git clone https://github.com/TU_USUARIO/outfit-bot.git
cd outfit-bot
pip install -r requirements.txt
cp .env.example .env
nano .env  # poner tus keys
export $(cat .env | xargs)
python bot.py
```

### IMPORTANTE: Seguridad
El `.gitignore` asegura que **NUNCA** se suba tu `.env` (API keys) ni `wardrobe.json` (data personal). Si por error ya hiciste commit con el `.env`, cambia tus keys de inmediato.

---

## Correr en tu VPS (background)

```bash
# Opción 1: screen (rápido)
screen -S outfitbot
python bot.py
# Ctrl+A luego D para desconectar
# screen -r outfitbot para reconectar

# Opción 2: systemd (se reinicia solo si falla)
sudo tee /etc/systemd/system/outfit-bot.service << EOF
[Unit]
Description=Outfit Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
EnvironmentFile=$(pwd)/.env
ExecStart=$(which python3) bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable outfit-bot
sudo systemctl start outfit-bot
sudo systemctl status outfit-bot

# Ver logs:
journalctl -u outfit-bot -f
```

---

## Costo total

| Concepto | Costo |
|----------|-------|
| Telegram Bot | Gratis |
| Gemini API (free tier) | **Gratis** |
| GitHub privado | Gratis |
| VPS (ya lo tienes) | $0 extra |
| **Total** | **$0/mes** |
