from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify, Response
from functools import wraps
import psycopg2, psycopg2.extras, json, urllib.request, time, calendar, csv, io, os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

DB_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    if not DB_URL: raise Exception("Falta DATABASE_URL en Render")
    return psycopg2.connect(DB_URL)

def init_db():
    if not DB_URL: return
    try:
        con = get_db()
        con.autocommit = True
        c = con.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT, errores INTEGER, pool1 TEXT, worker1 TEXT, pool2 TEXT, worker2 TEXT, pool3 TEXT, worker3 TEXT, mac TEXT, serial TEXT, firmware TEXT, fans TEXT, cadenas TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, plan TEXT DEFAULT 'Gratis', saldo REAL DEFAULT 0.0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS cola_comandos (id SERIAL PRIMARY KEY, ip TEXT, comando TEXT, estado TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS historial_nube (id SERIAL PRIMARY KEY, ip TEXT, hashrate REAL, temp_max INTEGER, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nombres_mineros (ip TEXT UNIQUE, nombre TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs_mineros (ip TEXT UNIQUE, contenido TEXT, fecha TEXT)''')
        
        try: c.execute("ALTER TABLE usuarios ADD COLUMN plan TEXT DEFAULT 'Gratis'")
        except: pass
        try: c.execute("ALTER TABLE usuarios ADD COLUMN saldo REAL DEFAULT 0.0")
        except: pass

        c.execute("SELECT * FROM usuarios WHERE username='giovanni'")
        if not c.fetchone(): c.execute("INSERT INTO usuarios (username, password) VALUES ('giovanni', 'admin123')")
        con.close()
    except Exception as e:
        print(f"Error BD: {e}")

try: init_db()
except: pass

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

cache_red = {'datos': {'precio': 64809.0, 'dificultad_t': 134.82, 'recompensa': 3.125, 'red_ehs': 965.11}, 'ultima_act': 0}
def obtener_datos_red():
    global cache_red
    ahora = time.time()
    if ahora - cache_red['ultima_act'] < 60: return cache_red['datos']
    datos = cache_red['datos'].copy()
    try:
        req_p = urllib.request.Request("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers={'User-Agent': 'Mozilla/5.0'})
        datos['precio'] = float(json.loads(urllib.request.urlopen(req_p, timeout=2).read().decode('utf-8'))['price'])
        req_d = urllib.request.Request("https://mempool.space/api/v1/difficulty-adjustment", headers={'User-Agent': 'Mozilla/5.0'})
        datos['dificultad_t'] = float(json.loads(urllib.request.urlopen(req_d, timeout=2).read().decode('utf-8'))['difficulty']) / 1_000_000_000_000
    except: pass
    cache_red['datos'] = datos; cache_red['ultima_act'] = ahora
    return datos

# --- PLANTILLAS HTML CON CSS CORREGIDO PARA CELULARES ---

HTML_AUTH = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>El Minero Gris - Acceso</title>
<style>
    :root { --bg-body: #14151f; --bg-card: #1b1d29; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --accent-red: #ff5252; --accent-green: #00e676; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }
    .login-card { background: var(--bg-card); padding: 40px 30px; border-radius: 8px; border: 1px solid var(--border-color); width: 100%; max-width: 350px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    .logo-container { font-weight: bold; font-size: 1.5em; display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 20px; }
    .logo-img { height: 45px; width: auto; object-fit: contain; }
    .logo-text span { color: var(--text-muted); font-weight: normal; }
    .tabs { display: flex; margin-bottom: 25px; border-bottom: 1px solid var(--border-color); }
    .tab { flex: 1; text-align: center; padding: 10px; color: var(--text-muted); cursor: pointer; text-decoration: none; font-weight: bold; transition: 0.2s; font-size:0.9em; }
    .tab:hover { color: var(--text-main); }
    .tab.active { color: var(--accent-blue); border-bottom: 2px solid var(--accent-blue); }
    .form-group { margin-bottom: 20px; }
    .form-group label { display: block; font-size: 0.85em; color: var(--text-muted); margin-bottom: 8px; font-weight: bold; }
    .form-group input { width: 100%; background: var(--bg-body); border: 1px solid var(--border-color); color: var(--text-main); padding: 12px; border-radius: 4px; font-size: 1em; box-sizing: border-box; }
    .btn-login { width: 100%; background: var(--accent-blue); color: white; border: none; padding: 12px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.2s; margin-top: 10px; }
    .btn-login:hover { background: #254ee6; }
    .btn-register { background: var(--accent-green); color: #000; }
    .btn-register:hover { background: #00c263; }
</style></head>
<body>
    <div class="login-card">
        <div class="logo-container">
            <img src="/static/logo.png" alt="Logo" class="logo-img" onerror="this.outerHTML='<span style=\\'font-size:1.2em;\\'>⛏️</span>'">
            <div class="logo-text">El Minero <span>Gris</span></div>
        </div>
        <div class="tabs">
            <a href="/login" class="tab {{ 'active' if mode == 'login' else '' }}">Iniciar Sesión</a>
            <a href="/register" class="tab {{ 'active' if mode == 'register' else '' }}">Crear Cuenta</a>
        </div>
        {% if error %}<div style="color:var(--accent-red); margin-bottom:15px; text-align:center; font-size:0.9em; font-weight:bold;">{{ error }}</div>{% endif %}
        {% if success %}<div style="color:var(--accent-green); margin-bottom:15px; text-align:center; font-size:0.9em; font-weight:bold;">{{ success }}</div>{% endif %}
        <form method="POST">
            <div class="form-group"><label>Nombre de Usuario</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Contraseña</label><input type="password" name="password" required></div>
            {% if mode == 'register' %}
                <button type="submit" class="btn-login btn-register">Registrar Nueva Cuenta</button>
            {% else %}
                <button type="submit" class="btn-login">Ingresar al Panel</button>
            {% endif %}
        </form>
    </div>
</body></html>
"""

HTML_LAYOUT = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>El Minero Gris</title>
<script type="text/javascript" src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    :root[data-theme="dark"] { --bg-body: #14151f; --bg-sidebar: #191a24; --bg-card: #1b1d29; --bg-input: #14151f; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --text-sub: #8b8d9b; --accent-blue: #3366ff; --accent-green: #00e676; --accent-red: #ff5252; --hover-bg: #202230; --active-bg: #232533; }
    :root[data-theme="light"] { --bg-body: #f4f5f7; --bg-sidebar: #ffffff; --bg-card: #ffffff; --bg-input: #f4f5f7; --border-color: #e2e8f0; --text-main: #1e293b; --text-muted: #64748b; --text-sub: #94a3b8; --accent-blue: #2563eb; --accent-green: #10b981; --accent-red: #ef4444; --hover-bg: #f8fafc; --active-bg: #f1f5f9; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; display: flex; height: 100vh; overflow: hidden; top: 0 !important; }
    .skiptranslate iframe { display: none !important; } body { top: 0px !important; } #google_translate_element { display: none; }
    a { text-decoration: none; color: inherit; }
    
    .sidebar { width: 220px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; z-index: 1000; transition: transform 0.3s ease; }
    .logo-container { padding: 20px; font-weight: bold; font-size: 1.1em; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border-color); }
    .logo-img { height: 35px; width: auto; object-fit: contain; }
    .logo-text span { color: var(--text-muted); font-weight: normal; }
    .menu { list-style: none; padding: 15px 0; margin: 0; flex: 1; overflow-y: auto; }
    .menu a { display: flex; align-items: center; gap: 12px; padding: 12px 20px; font-size: 0.9em; color: var(--text-muted); cursor: pointer; transition: 0.2s; }
    .menu a:hover { background: var(--hover-bg); color: var(--text-main); }
    .menu a.active { background: var(--active-bg); color: var(--text-main); border-left: 3px solid var(--accent-blue); }
    .sidebar-bottom { border-top: 1px solid var(--border-color); padding: 15px 20px; font-size: 0.85em; display: flex; flex-direction: column; gap: 10px; }
    
    .right-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; width: 100%; }
    .top-bar { height: 60px; background: var(--bg-body); border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; padding: 0 20px; }
    .top-bar-left { display: flex; align-items: center; gap: 15px; }
    .top-bar-right { display: flex; align-items: center; gap: 10px; }
    
    .top-btn, .top-lang-select { background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-main); height: 36px; border-radius: 6px; cursor: pointer; transition: 0.2s; font-size: 0.85em; font-weight: bold; display: flex; align-items: center; justify-content: center; }
    .top-btn { width: 36px; font-size: 1.1em; color: var(--text-muted); }
    .btn-sync { width: auto; padding: 0 15px; color: var(--text-main); }
    .btn-sync:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
    .top-lang-select { padding: 0 10px; outline: none; }
    .top-status { display: flex; align-items: center; gap: 15px; font-size: 0.85em; color: var(--text-muted); font-weight: bold; margin-left: 10px; }
    .status-envivo { color: var(--accent-green); display: flex; align-items: center; gap: 6px; }
    .dot-pulse { width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0.7); } 70% { box-shadow: 0 0 0 6px rgba(0, 230, 118, 0); } 100% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0); } }
    .main-content { flex: 1; padding: 20px; overflow-y: auto; }
    
    .btn-menu { background: transparent; border: 1px solid var(--border-color); color: var(--text-main); font-size: 1.2em; cursor: pointer; border-radius: 6px; height: 36px; padding: 0 12px; display: none; align-items: center; justify-content: center; transition: 0.2s; }
    .btn-menu:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
    .logo-top { display: none; align-items: center; gap: 8px; font-weight: bold; font-size: 1.1em; }
    .logo-top img { height: 25px; width: auto; }
    .overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); z-index: 999; backdrop-filter: blur(2px); }
    .overlay.active { display: block; }

    @media (max-width: 768px) {
        .sidebar { position: fixed; left: 0; top: 0; height: 100%; transform: translateX(-100%); }
        .sidebar.active { transform: translateX(0); }
        .btn-menu { display: flex; }
        .logo-top { display: flex; }
        .top-lang-select, .top-status { display: none; }
        .btn-sync span { display: none; }
        .main-content { padding: 15px 10px; }
    }
</style>
</head>
<body>
    <div id="google_translate_element"></div>
    <div class="overlay" id="sidebarOverlay" onclick="toggleMenu()"></div>
    
    <div class="sidebar">
        <div class="logo-container">
            <img src="/static/logo.png" alt="Logo" class="logo-img" onerror="this.outerHTML='<span style=\\'font-size:1.5em;\\'>⛏️</span>'">
            <div class="logo-text" translate="no">El Minero <span>Gris</span></div>
        </div>
        <div class="menu">
            <a href="/" class="{{ 'active' if active_page == 'resumen' else '' }}">📊 Resumen</a>
            <a href="/plan" class="{{ 'active' if active_page == 'plan' else '' }}">💳 Billetera y Planes</a>
            <a href="/granjas" class="{{ 'active' if active_page == 'granjas' else '' }}">🏭 Granjas</a>
            <a href="/mineros" class="{{ 'active' if active_page == 'mineros' else '' }}">⛏️ Mineros</a>
            <a href="/alertas" class="{{ 'active' if active_page == 'alertas' else '' }}">🔔 Alertas</a>
            <a href="/ajustes" class="{{ 'active' if active_page == 'ajustes' else '' }}">⚙️ Ajustes</a>
        </div>
        <div class="sidebar-bottom">
            <div style="color:var(--text-main);font-weight:bold;display:flex;align-items:center;gap:8px;">👤 {{ session['usuario'] }}</div>
            <div style="color:var(--accent-green); font-size:1em; font-weight:bold;">$ {{ "%.2f"|format(session.get('saldo', 0.0)) }} USDT</div>
            <a href="/logout" style="color:var(--accent-red);display:flex;align-items:center;gap:8px;">🚪 Cerrar Sesión</a>
        </div>
    </div>
    
    <div class="right-container">
        <div class="top-bar">
            <div class="top-bar-left">
                <button class="btn-menu" onclick="toggleMenu()">☰</button>
                <div class="logo-top">
                    <img src="/static/logo.png" alt="Logo" onerror="this.outerHTML='<span style=\\'font-size:1.2em;\\'>⛏️</span>'">
                    <span translate="no">Minero<span style="color:var(--text-muted); font-weight:normal;">Gris</span></span>
                </div>
                <button class="top-btn btn-sync" onclick="escanearRed()" id="btnSync">🔄 <span>Escanear Red</span></button>
            </div>
            
            <div class="top-bar-right">
                <button class="top-btn" onclick="toggleTheme()" id="themeIcon">☀️</button>
                <select class="top-lang-select" id="langSelector" onchange="cambiarIdioma(this.value)">
                    <option value="es">🌐 Español</option><option value="en">🌐 English</option>
                </select>
                <div class="top-status">
                    <span id="relojTop" translate="no">00:00</span>
                    <span class="status-envivo"><div class="dot-pulse"></div> En vivo</span>
                </div>
            </div>
        </div>
        <div class="main-content">{{ content | safe }}</div>
    </div>
    
    <script>
        function toggleMenu() { document.querySelector('.sidebar').classList.toggle('active'); document.getElementById('sidebarOverlay').classList.toggle('active'); }
        function escanearRed() { let b = document.getElementById('btnSync'); b.innerHTML = '⏳ <span>Escaneando...</span>'; b.style.pointerEvents = 'none'; fetch('/cmd/all/scan').then(() => { setTimeout(() => { window.location.reload(); }, 1500); }); }
        function googleTranslateElementInit() { new google.translate.TranslateElement({pageLanguage: 'es', autoDisplay: false}, 'google_translate_element'); }
        function cambiarIdioma(lang) { let s = document.querySelector(".goog-te-combo"); if(s){ s.value = lang; s.dispatchEvent(new Event("change")); } }
        function actualizarReloj() { const d = new Date(); document.getElementById('relojTop').innerText = d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0'); }
        setInterval(actualizarReloj, 1000); actualizarReloj();
        function toggleTheme() { const h = document.documentElement, i = document.getElementById('themeIcon'); if (h.getAttribute('data-theme') === 'dark') { h.setAttribute('data-theme', 'light'); i.innerText = '🌙'; } else { h.setAttribute('data-theme', 'dark'); i.innerText = '☀️'; } }
    </script>
</body></html>
"""

HTML_RESUMEN = """
<style>
    .cabecera { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
    .cabecera h1 { margin: 0 0 5px 0; font-size: 1.8em; }
    .cabecera .sub { font-size: 0.85em; color: var(--text-sub); }
    /* Grid adaptativo para celulares (cajas se acomodan solas) */
    .top-stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 25px; }
    .stat-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px 10px; display: flex; flex-direction: column; justify-content: center; }
    .stat-card .lbl { font-size: 0.65em; color: var(--text-muted); text-transform: uppercase; font-weight: bold; margin-bottom: 6px; display: flex; justify-content: space-between; }
    .stat-card .val { font-size: 1.1em; font-weight: bold; color: var(--text-main); }
    .val-blue { color: var(--accent-blue) !important; } .val-green { color: var(--accent-green) !important; } .val-red { color: var(--accent-red) !important; }
    .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; } .dot-green { background: var(--accent-green); } .dot-red { background: var(--accent-red); }
    
    .calc-panel { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 20px; margin-bottom: 25px; }
    .calc-title { font-size: 1.1em; font-weight: bold; margin-bottom: 15px; display: flex; align-items: center; gap: 8px; }
    /* Flex-wrap soluciona el desbordamiento de la calculadora en iPhone */
    .calc-top-boxes { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px; }
    .box-mini { flex: 1; min-width: 110px; border: 1px solid var(--accent-blue); background: var(--bg-input); border-radius: 6px; padding: 12px 10px; text-align: center; }
    .box-mini .v { color: var(--accent-blue); font-weight: bold; font-size: 1.1em; margin-bottom: 6px; }
    .box-mini .l { color: var(--text-sub); font-size: 0.65em; text-transform: uppercase; font-weight: bold; }
    
    .calc-inputs { display: flex; flex-wrap: wrap; gap: 15px; align-items: flex-end; margin-bottom: 20px; }
    .calc-input-group { flex: 1; min-width: 130px; }
    .calc-input-group label { display: block; font-size: 0.75em; color: var(--text-muted); margin-bottom: 8px; font-weight:bold;}
    .calc-input-group input { width: 100%; background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-main); padding: 10px; border-radius: 4px; font-size: 1em; box-sizing: border-box; }
    .btn-aplicar { height: 41px; background: rgba(51,102,255,0.1); color: var(--accent-blue); border: 1px solid var(--accent-blue); padding: 0 25px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.2s; white-space: nowrap; margin-bottom: 1px; }
    .btn-aplicar:hover { background: var(--accent-blue); color: white; }
    
    /* Grid adaptativo para los resultados de la calculadora */
    .calc-resultados { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; margin-bottom: 20px; }
    .res-box { background: var(--bg-input); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px 8px; text-align: center; }
    .res-box .val { font-size: 1em; font-weight: bold; margin-bottom: 4px; }
    .res-box .lbl { font-size: 0.6em; color: var(--text-muted); text-transform: uppercase; font-weight:bold; }
    
    .calc-text-ribbon { font-size: 0.85em; color: var(--text-muted); margin-bottom: 5px; padding: 5px 0; border-top: 1px solid var(--border-color); padding-top: 15px;}
    .calc-text-ribbon span.hl { color: var(--text-main); font-weight: bold; }
    .calc-text-ribbon .sep { margin: 0 10px; color: var(--border-color); }
    
    .bulk-bar { margin-bottom: 10px; display: flex; gap: 10px; flex-wrap: wrap; }
    .btn-bulk { background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-main); padding: 8px 15px; border-radius: 4px; cursor: pointer; font-size: 0.85em; font-weight: bold; transition: 0.2s; }
    .btn-bulk:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
    .btn-excel { background: rgba(0, 230, 118, 0.1); border: 1px solid var(--accent-green); color: var(--accent-green); }
    .btn-excel:hover { background: var(--accent-green); color: white; }
