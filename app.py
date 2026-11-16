from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify
from functools import wraps
import sqlite3, json, urllib.request, time
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

# --- 1. BASE DE DATOS COMPLETA EN LA NUBE ---
def init_db():
    conexion = sqlite3.connect('historial_minero.db')
    cursor = conexion.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS cola_comandos (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, comando TEXT, estado TEXT, fecha TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS historial_nube (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, hashrate REAL, temp_max INTEGER, fecha TEXT)')
    
    if not cursor.execute("SELECT * FROM usuarios WHERE username='giovanni'").fetchone():
        cursor.execute("INSERT INTO usuarios (username, password) VALUES ('giovanni', 'admin123')")
    conexion.commit(); conexion.close()

init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- 2. MOTOR DE PRECIOS Y DIFICULTAD (BINANCE / MEMPOOL) ---
cache_red = {'datos': {'precio': 68792.0, 'dificultad_t': 148.36, 'recompensa': 3.125}, 'ultima_act': 0}
def obtener_datos_red():
    global cache_red
    ahora = time.time()
    if ahora - cache_red['ultima_act'] < 300: return cache_red['datos']
    datos = {'precio': cache_red['datos']['precio'], 'dificultad_t': cache_red['datos']['dificultad_t'], 'recompensa': 3.125}
    try:
        req_p = urllib.request.Request("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers={'User-Agent': 'Mozilla/5.0'})
        datos['precio'] = float(json.loads(urllib.request.urlopen(req_p, timeout=2).read().decode('utf-8'))['price'])
        req_d = urllib.request.Request("https://mempool.space/api/v1/difficulty-adjustment", headers={'User-Agent': 'Mozilla/5.0'})
        datos['dificultad_t'] = float(json.loads(urllib.request.urlopen(req_d, timeout=2).read().decode('utf-8'))['difficulty']) / 1_000_000_000_000
    except: pass
    cache_red['datos'] = datos; cache_red['ultima_act'] = ahora
    return datos

# --- 3. DISEÑOS HTML PREMIUM ---
HTML_LOGIN = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>Antminer Tools - Acceso</title>
<style>
    :root { --bg-body: #14151f; --bg-card: #1b1d29; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --accent-red: #ff5252; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
    .login-card { background: var(--bg-card); padding: 40px 30px; border-radius: 8px; border: 1px solid var(--border-color); width: 100%; max-width: 320px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    .logo-container { font-weight: bold; font-size: 1.4em; display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 30px; }
    .logo-icon { background: var(--accent-blue); color: white; width: 32px; height: 32px; border-radius: 6px; display: flex; justify-content: center; align-items: center; font-size: 0.9em; }
    .logo-text span { color: var(--accent-blue); font-weight: normal; }
    .form-group { margin-bottom: 20px; }
    .form-group label { display: block; font-size: 0.85em; color: var(--text-muted); margin-bottom: 8px; }
    .form-group input { width: 100%; background: var(--bg-body); border: 1px solid var(--border-color); color: var(--text-main); padding: 12px; border-radius: 4px; font-size: 1em; box-sizing: border-box; }
    .form-group input:focus { border-color: var(--accent-blue); outline: none; }
    .btn-login { width: 100%; background: var(--accent-blue); color: white; border: none; padding: 12px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.2s; font-size: 1em; margin-top: 10px; }
    .error-msg { background: rgba(255, 82, 82, 0.1); border: 1px solid var(--accent-red); color: var(--accent-red); padding: 10px; border-radius: 4px; font-size: 0.85em; text-align: center; margin-bottom: 20px; }
</style></head>
<body>
    <div class="login-card">
        <div class="logo-container"><div class="logo-icon">A</div><div class="logo-text">Antminer <span>Tools</span></div></div>
        {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
        <form method="POST">
            <div class="form-group"><label>Usuario</label><input type="text" name="username" required autocomplete="off"></div>
            <div class="form-group"><label>Contraseña</label><input type="password" name="password" required></div>
            <button type="submit" class="btn-login">Ingresar al Panel</button>
        </form>
    </div>
</body></html>
"""

HTML_PRINCIPAL = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>Antminer Tools - Dashboard</title>
<script type="text/javascript" src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
<style>
    :root[data-theme="dark"] { --bg-body: #14151f; --bg-sidebar: #191a24; --bg-card: #1b1d29; --bg-input: #14151f; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --text-sub: #8b8d9b; --accent-blue: #3366ff; --accent-green: #00e676; --accent-red: #ff5252; --hover-bg: #202230; --active-bg: #232533; }
    :root[data-theme="light"] { --bg-body: #f4f5f7; --bg-sidebar: #ffffff; --bg-card: #ffffff; --bg-input: #f4f5f7; --border-color: #e2e8f0; --text-main: #1e293b; --text-muted: #64748b; --text-sub: #94a3b8; --accent-blue: #2563eb; --accent-green: #10b981; --accent-red: #ef4444; --hover-bg: #f8fafc; --active-bg: #f1f5f9; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; display: flex; height: 100vh; overflow: hidden; }
    .skiptranslate iframe { display: none !important; } body { top: 0px !important; } #google_translate_element { display: none; }
    a { text-decoration: none; color: inherit; }
    
    .sidebar { width: 220px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding-top: 15px; }
    .logo-container { padding: 10px 20px 20px; font-weight: bold; font-size: 1.1em; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border-color); }
    .logo-icon { background: var(--accent-blue); color: white; width: 24px; height: 24px; border-radius: 4px; display: flex; justify-content: center; align-items: center; font-size: 0.8em; }
    .logo-text span { color: var(--accent-blue); font-weight: normal; }
    .menu { list-style: none; padding: 15px 0; margin: 0; flex: 1; }
    .menu li { padding: 12px 20px; font-size: 0.9em; color: var(--text-muted); cursor: pointer; transition: 0.2s; }
    .menu li:hover { background: var(--hover-bg); color: var(--text-main); }
    .menu li.active { background: var(--active-bg); color: var(--text-main); border-left: 3px solid var(--accent-blue); }
    .menu a { display: flex; width: 100%; align-items: center; gap: 12px; }
    
    .sidebar-bottom { border-top: 1px solid var(--border-color); padding: 15px 20px; font-size: 0.85em; display: flex; flex-direction: column; gap: 10px; }
    .user-info { color: var(--text-main); font-weight: bold; display: flex; align-items: center; gap: 8px; }
    .btn-logout { color: var(--accent-red); display: flex; align-items: center; gap: 8px; cursor: pointer; }
    
    .right-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
    .top-bar { height: 60px; background: var(--bg-body); border-bottom: 1px solid var(--border-color); display: flex; justify-content: flex-end; align-items: center; padding: 0 30px; gap: 15px; }
    .top-btn, .top-lang-select { background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-main); height: 36px; border-radius: 6px; cursor: pointer; font-weight: bold; display: flex; align-items: center; justify-content: center; }
    .top-btn { width: 36px; font-size: 1.1em; color: var(--text-muted); }
    .top-lang-select { padding: 0 10px; outline: none; }
    .top-status { display: flex; align-items: center; gap: 15px; font-size: 0.85em; color: var(--text-muted); font-weight: bold; }
    .status-envivo { color: var(--accent-green); display: flex; align-items: center; gap: 6px; }
    .dot-pulse { width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0.7); } 70% { box-shadow: 0 0 0 6px rgba(0, 230, 118, 0); } 100% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0); } }

    .main-content { flex: 1; padding: 20px 30px; overflow-y: auto; }
    .cabecera h1 { margin: 0 0 5px 0; font-size: 1.8em; }
    .cabecera .sub { font-size: 0.85em; color: var(--text-sub); margin-bottom: 20px;}
    
    .top-stats-grid { display: grid; grid-template-columns: repeat(11, 1fr); gap: 10px; margin-bottom: 25px; }
    .stat-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px 10px; display: flex; flex-direction: column; justify-content: center; }
    .stat-card .lbl { font-size: 0.65em; color: var(--text-muted); text-transform: uppercase; font-weight: bold; margin-bottom: 6px; display: flex; justify-content: space-between; }
    .stat-card .val { font-size: 1.2em; font-weight: bold; color: var(--text-main); }
    .val-blue { color: var(--accent-blue) !important; } .val-green { color: var(--accent-green) !important; } .val-red { color: var(--accent-red) !important; }
    .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; } .dot-green { background: var(--accent-green); } .dot-red { background: var(--accent-red); }

    .calc-panel { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 20px; margin-bottom: 25px; }
    .calc-title { font-size: 1em; font-weight: bold; margin-bottom: 15px; display: flex; align-items: center; gap: 8px; }
    .calc-top-boxes { display: flex; gap: 15px; margin-bottom: 20px; }
    .box-mini { border: 1px solid var(--border-color); background: var(--bg-input); border-radius: 6px; padding: 10px 15px; text-align: center; min-width: 100px; }
    .box-mini .v { color: var(--accent-blue); font-weight: bold; font-size: 1em; margin-bottom: 4px; }
    .box-mini .l { color: var(--text-sub); font-size: 0.65em; text-transform: uppercase; }
    
    .calc-inputs { display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 20px; align-items: flex-end; }
    .calc-input-group { flex: 1; min-width: 130px; }
    .calc-input-group label { display: block; font-size: 0.75em; color: var(--text-muted); margin-bottom: 8px; }
    .calc-input-group input { width: 100%; background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-main); padding: 10px; border-radius: 4px; font-size: 1em; box-sizing: border-box; }
    .btn-aplicar { height: 41px; background: var(--accent-blue); color: white; border: none; padding: 0 25px; border-radius: 4px; font-weight: bold; cursor: pointer; }
    
    .calc-resultados { display: grid; grid-template-columns: repeat(8, 1fr); gap: 10px; }
    .res-box { background: var(--bg-input); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px 8px; text-align: center; }
    .res-box .val { font-size: 1.1em; font-weight: bold; margin-bottom: 4px; }
    .res-box .lbl { font-size: 0.65em; color: var(--text-muted); text-transform: uppercase; }
    
    .tabla-container { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px 20px; text-align: left; border-bottom: 1px solid var(--border-color); font-size: 0.85em; }
    th { color: var(--text-muted); text-transform: uppercase; background: var(--bg-card); }
    tr.fila-minero:hover { background: var(--hover-bg); }
    .nombre-link { color: var(--text-main); font-size: 1.1em; font-weight: bold; cursor: pointer; text-decoration: underline; }
    .btn-accion { background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-main); padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 0.9em; transition: 0.2s; }
    .btn-accion:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
    .celda-ganancia { font-weight:bold; }
</style></head>
<body>
    <div id="google_translate_element"></div>
    <div class="sidebar">
        <div class="logo-container"><div class="logo-icon">A</div><div class="logo-text" translate="no">Antminer <span>Tools</span></div></div>
        <ul class="menu">
            <li class="active"><a href="/">📊 Resumen</a></li>
            <li><a href="/granjas">🏭 Granjas</a></li>
            <li><a href="/alertas">🔔 Alertas</a></li>
            <li><a href="/plan">📋 Plan</a></li>
            <li><a href="/ajustes">⚙️ Ajustes</a></li>
        </ul>
        <div class="sidebar-bottom">
            <div class="user-info">👤 {{ session['usuario'] }}</div>
            <a href="/logout" class="btn-logout">🚪 Cerrar Sesión</a>
        </div>
    </div>
    <div class="right-container">
        <div class="top-bar">
            <button class="top-btn" onclick="toggleTheme()" id="themeIcon">☀️</button>
            <select class="top-lang-select" id="langSelector" onchange="cambiarIdioma(this.value)">
                <option value="es">🌐 Español</option><option value="en">🌐 English</option>
            </select>
            <div class="top-status">
                <span id="relojTop" translate="no">00:00</span>
                <span class="status-envivo"><div class="dot-pulse"></div> En vivo (SaaS)</span>
            </div>
        </div>
        <div class="main-content">
            <div class="cabecera">
                <h1>Resumen de Operaciones</h1>
                <div class="sub">Control de Flota y Comandos · <span id="textoActualizado" style="color:var(--accent-green);">Actualizado hace 1s</span></div>
            </div>
            
            <div class="top-stats-grid">
                <div class="stat-card"><div class="lbl">MINEROS ⛏️</div><div class="val val-blue" translate="no">{{ totales }}</div></div>
                <div class="stat-card"><div class="lbl">EN LÍNEA <span class="dot dot-green"></span></div><div class="val val-green" translate="no">{{ en_linea }}</div></div>
                <div class="stat-card"><div class="lbl">SIN CONEXIÓN <span class="dot dot-red"></span></div><div class="val val-red" translate="no">{{ offline }}</div></div>
                <div class="stat-card"><div class="lbl">GRANJAS 🏭</div><div class="val val-blue" translate="no">1</div></div>
                <div class="stat-card"><div class="lbl">ALERTAS 🔔</div><div class="val val-green" translate="no">0</div></div>
                <div class="stat-card"><div class="lbl">TASA DE HASH ⚡</div><div class="val val-blue" translate="no">{{ flota.hashrate_total }} TH/s</div></div>
                <div class="stat-card"><div class="lbl">CONSUMO 🔌</div><div class="val val-blue" translate="no">{{ flota.consumo_kw }} kW</div></div>
                <div class="stat-card"><div class="lbl">TEMP MÁX 🌡️</div><div class="val val-red" translate="no">{{ flota.temp_max }} °C</div></div>
                <div class="stat-card"><div class="lbl">PRECIO BTC ₿</div><div class="val" translate="no">USD {{ flota.precio_btc }}</div></div>
                <div class="stat-card"><div class="lbl">INGRESOS / DÍA 💰</div><div class="val val-green" translate="no">{{ flota.ingreso_dia }} USD</div></div>
                <div class="stat-card"><div class="lbl">GANANCIA / DÍA 💵</div><div class="val" style="color: {{ '#ff5252' if '-' in flota.ganancia_dia else '#00e676' }};" translate="no">{{ flota.ganancia_dia }}</div></div>
            </div>

            <div class="calc-panel">
                <div class="calc-title">₿ Calculadora de rentabilidad (En vivo)</div>
                <div class="calc-top-boxes">
                    <div class="box-mini"><div class="v" id="topBtcDia">0.00</div><div class="l">BTC/DÍA</div></div>
                    <div class="box-mini"><div class="v" id="topBtcSemana">0.00</div><div class="l">BTC/SEM</div></div>
                    <div class="box-mini"><div class="v" id="topBtcMes">0.00</div><div class="l">BTC / MES</div></div>
                </div>
                <div class="calc-inputs">
                    <div class="calc-input-group"><label>Tasa de hash (TH/s)</label><input type="number" id="hashrateInput" value="{{ flota.hashrate_total }}"></div>
                    <div class="calc-input-group"><label>Consumo (W)</label><input type="number" id="consumoInput" value="{{ flota.consumo_w_total }}"></div>
                    <div class="calc-input-group"><label>Electricidad (USD/kWh)</label><input type="number" step="0.01" id="kwhInput" value="0.0"></div>
                    <div class="calc-input-group"><label>Comisión pool (%)</label><input type="number" step="0.1" id="comisionInput" value="0.0"></div>
                    <button class="btn-aplicar" onclick="calcularRentabilidad()">🔄 Actualizar</button>
                </div>
                <div class="calc-resultados">
                    <div class="res-box"><div class="val" id="resIngresoDia">0.00 USD</div><div class="lbl">INGRESOS/DÍA</div></div>
                    <div class="res-box"><div class="val" id="resElecDia" style="color:var(--accent-red);">USD 0.00</div><div class="lbl">LUZ/DÍA</div></div>
                    <div class="res-box"><div class="val" id="resGananciaDia">0.00 USD</div><div class="lbl">GANANCIA/DÍA</div></div>
                    <div class="res-box"><div class="val" id="resGananciaMes">0.00 USD</div><div class="lbl">GANANCIA/MES</div></div>
                </div>
            </div>

            <div class="tabla-container">
                <table>
                    <tr><th>MINERO (IP)</th><th>ESTADO</th><th>TASA DE HASH</th><th>TEMP</th><th>CONSUMO</th><th>GANANCIA / DÍA</th><th>ACCIONES (NUBE)</th></tr>
                    {% for f in registros %}
                    <tr class="fila-minero" data-hashrate="{{ f.hashrate }}" data-consumo="{{ f.consumo_kw * 1000 }}">
                        <td><span class="nombre-link" onclick="window.location.href='/minero/{{ f.ip }}'" translate="no">{{ f.ip }} 🔗</span><br><span style="font-size:0.7em; color:var(--text-sub);" translate="no">{{ f.modelo }}</span></td>
                        <td>{{ f.estado | safe }}</td>
                        <td translate="no">{{ f.hashrate }} TH/s</td>
                        <td style="color: {{ 'var(--accent-red)' if f.temp_num > 80 else 'var(--accent-blue)' }};" translate="no">{{ f.temp_num }} °C</td>
                        <td translate="no">{{ f.consumo_kw }} kW</td>
                        <td class="celda-ganancia" translate="no">{{ f.ganancia_neta }}</td>
                        <td>
                            <button class="btn-accion" onclick="if(confirm('¿Reiniciar IP {{ f.ip }}?')) window.location.href='/cmd/{{ f.ip }}/restart'">🔄 Reiniciar</button>
                            <button class="btn-accion" onclick="if(confirm('¿Pausar IP {{ f.ip }}?')) window.location.href='/cmd/{{ f.ip }}/sleep'">😴 Reposo</button>
                        </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>
    </div>
    <script>
        function googleTranslateElementInit() { new google.translate.TranslateElement({pageLanguage: 'es', autoDisplay: false}, 'google_translate_element'); }
        function cambiarIdioma(lang) { let s = document.querySelector(".goog-te-combo"); if(s){ s.value = lang; s.dispatchEvent(new Event("change")); } }
        function actualizarReloj() { const d = new Date(); document.getElementById('relojTop').innerText = d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0'); }
        setInterval(actualizarReloj, 1000); actualizarReloj();

        function toggleTheme() {
            const h = document.documentElement, i = document.getElementById('themeIcon');
            if (h.getAttribute('data-theme') === 'dark') { h.setAttribute('data-theme', 'light'); i.innerText = '🌙'; } 
            else { h.setAttribute('data-theme', 'dark'); i.innerText = '☀️'; }
        }
        let segs = 1; setInterval(() => { document.getElementById('textoActualizado').innerText = 'Actualizado hace ' + (++segs) + 's'; }, 1000);
        setTimeout(() => window.location.reload(), 60000);

        const PRECIO_BTC = parseFloat("{{ flota.precio_btc_raw }}"), DIFICULTAD = parseFloat("{{ flota.dificultad_t }}");
        function calcularRentabilidad() {
            let hTH = parseFloat(document.getElementById('hashrateInput').value) || 0;
            let cW = parseFloat(document.getElementById('consumoInput').value) || 0;
            let cKwh = parseFloat(document.getElementById('kwhInput').value) || 0;
            let pool = parseFloat(document.getElementById('comisionInput').value) || 0;

            let btcDia = DIFICULTAD > 0 ? (hTH * 3.125 * 86400) / (DIFICULTAD * 4294967296) : 0;
            document.getElementById('topBtcDia').innerText = btcDia.toFixed(8);
            document.getElementById('topBtcSemana').innerText = (btcDia * 7).toFixed(8);
            document.getElementById('topBtcMes').innerText = (btcDia * 30).toFixed(6);

            let inBruto = btcDia * PRECIO_BTC, inNeto = inBruto - (inBruto * (pool / 100)), elecDia = (cW * 24 / 1000) * cKwh, ganDia = inNeto - elecDia;

            document.getElementById('resIngresoDia').innerText = inNeto.toFixed(2) + ' USD';
            document.getElementById('resElecDia').innerText = elecDia.toFixed(2) + ' USD';
            
            let domDia = document.getElementById('resGananciaDia'), domMes = document.getElementById('resGananciaMes');
            domDia.innerText = ganDia.toFixed(2) + ' USD'; domDia.style.color = ganDia < 0 ? 'var(--accent-red)' : 'var(--accent-green)';
            domMes.innerText = (ganDia * 30).toFixed(2) + ' USD'; domMes.style.color = ganDia < 0 ? 'var(--accent-red)' : 'var(--accent-green)';

            document.querySelectorAll('.fila-minero').forEach(f => {
                let fh = parseFloat(f.getAttribute('data-hashrate')) || 0, fc = parseFloat(f.getAttribute('data-consumo')) || 0;
                let fb = DIFICULTAD > 0 ? (fh * 3.125 * 86400) / (DIFICULTAD * 4294967296) : 0;
                let fg = (fb * PRECIO_BTC * (1 - pool/100)) - ((fc * 24 / 1000) * cKwh);
                let c = f.querySelector('.celda-ganancia');
                if (c) { c.innerText = fg < 0 ? `-${Math.abs(fg).toFixed(2)} USD` : `${fg.toFixed(2)} USD`; c.style.color = fg < 0 ? 'var(--accent-red)' : 'var(--accent-green)'; }
            });
        }
        window.onload = function() { calcularRentabilidad(); };
    </script>
</body></html>
"""

HTML_DETALLE = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>Detalle - {{ ip }}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    :root[data-theme="dark"] { --bg-body: #14151f; --bg-card: #1b1d29; --bg-input: #14151f; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --accent-red: #ff5252; --accent-green: #00e676; }
    :root[data-theme="light"] { --bg-body: #f4f5f7; --bg-card: #ffffff; --bg-input: #f4f5f7; --border-color: #e2e8f0; --text-main: #1e293b; --text-muted: #64748b; --accent-blue: #2563eb; --accent-red: #ef4444; --accent-green: #10b981; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; padding: 20px; }
    .contenedor { max-width: 1400px; margin: 0 auto; }
    .card { background: var(--bg-card); padding: 20px; border-radius: 8px; border: 1px solid var(--border-color); margin-bottom: 20px;}
    a { text-decoration: none; color: inherit; }
    .header-detalle { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
    .btn-volver { color: var(--text-muted); font-size: 1.5em; font-weight: bold; }
    .titulo-minero { margin: 0; font-size: 1.5em; display: flex; align-items: center; gap: 10px; }
    .estado-punto { height: 12px; width: 12px; background: var(--accent-green); border-radius: 50%; display: inline-block; }
    
    .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }
    .grid-4 .card { margin-bottom: 0; }
    .grid-4 .card h4 { margin: 0; font-size: 0.8em; color: var(--text-muted); text-transform: uppercase; }
    .grid-4 .card .val { font-size: 1.8em; font-weight: bold; margin-top: 10px; color: var(--accent-blue); }
    
    .panel-control { display: flex; gap: 10px; margin-bottom: 20px; background: var(--bg-card); padding: 15px; border-radius: 8px; border: 1px solid var(--border-color); align-items: center; }
    .btn-accion { background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-main); padding: 8px 15px; border-radius: 4px; cursor: pointer; font-weight: bold; }
    .btn-accion:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
    
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .info-table { width: 100%; border-collapse: collapse; }
    .info-table td { padding: 10px 0; border-bottom: 1px solid var(--border-color); font-size: 0.9em; }
    .info-table td:nth-child(1) { color: var(--text-muted); }
    .info-table td:nth-child(2) { text-align: right; font-weight: bold; }
</style></head>
<body>
    <div class="contenedor">
        <div class="header-detalle">
            <div style="display:flex; align-items:center; gap:15px;">
                <a href="/" class="btn-volver">← Volver</a>
                <div><h2 class="titulo-minero"><span translate="no">{{ nombre }}</span> <span class="estado-punto"></span></h2><div style="color:var(--text-muted); font-size: 0.9em; margin-top: 5px;" translate="no">{{ modelo }} - {{ ip }}</div></div>
            </div>
        </div>

        <div class="grid-4">
            <div class="card"><h4>Tasa de Hash</h4><div class="val" translate="no">{{ ultimo.hashrate }} TH/s</div></div>
            <div class="card"><h4>Errores HW</h4><div class="val" style="color:var(--accent-red);" translate="no">{{ ultimo.errores }}</div></div>
            <div class="card"><h4>Consumo</h4><div class="val" style="color:var(--accent-green);" translate="no">{{ ultimo.consumo }} W</div></div>
            <div class="card"><h4>Temp Máx</h4><div class="val" style="color:var(--accent-red);" translate="no">{{ ultimo.temp_max }} °C</div></div>
        </div>

        <div class="panel-control"><span style="font-weight:bold; margin-right:15px;">⚡ Comandos en la Nube</span>
            <button class="btn-accion" onclick="if(confirm('¿Reiniciar IP {{ ip }}?')) window.location.href='/cmd/{{ ip }}/restart'">🔄 Reiniciar</button>
            <button class="btn-accion" onclick="if(confirm('¿Pausar IP {{ ip }}?')) window.location.href='/cmd/{{ ip }}/sleep'">😴 Reposo</button>
            <button class="btn-accion" onclick="if(confirm('¿Normal IP {{ ip }}?')) window.location.href='/cmd/{{ ip }}/normal'">▶ Normal</button>
        </div>

        <div class="card" style="height: 300px;"><canvas id="hashrateChart"></canvas></div>

        <div class="grid-2">
            <div class="card">
                <h3 style="color:var(--text-muted); margin-top:0;">TEMPERATURAS DE CADENA (Próximamente)</h3>
                <table class="info-table">
                    <tr><td>Cadena 0</td><td translate="no">Chip: <span style="color:var(--accent-blue);">{{ v_pools.t0_c }}°C</span> | PCB: {{ v_pools.t0_p }}°C</td></tr>
                    <tr><td>Cadena 1</td><td translate="no">Chip: <span style="color:var(--accent-blue);">{{ v_pools.t1_c }}°C</span> | PCB: {{ v_pools.t1_p }}°C</td></tr>
                    <tr><td>Cadena 2</td><td translate="no">Chip: <span style="color:var(--accent-blue);">{{ v_pools.t2_c }}°C</span> | PCB: {{ v_pools.t2_p }}°C</td></tr>
                </table>
            </div>
            <div class="card">
                <h3 style="color:var(--text-muted); margin-top:0;">INFORMACIÓN DEL DISPOSITIVO</h3>
                <table class="info-table">
                    <tr><td>Modelo</td><td translate="no">{{ modelo }}</td></tr>
                    <tr><td>Firmware</td><td translate="no">{{ dispositivo.firmware }}</td></tr>
                    <tr><td>IP local</td><td translate="no">{{ ip }}</td></tr>
                    <tr><td>Eficiencia</td><td translate="no">{{ dispositivo.eficiencia }} J/TH</td></tr>
                    <tr><td>Ventiladores</td><td translate="no">{{ ultimo.fans }} RPM</td></tr>
                </table>
            </div>
        </div>
    </div>
    <script>
        const ctx = document.getElementById('hashrateChart').getContext('2d');
        new Chart(ctx, { type: 'line', data: { labels: {{ graf_fechas | safe }}, datasets: [ { label: 'Hashrate (TH/s)', data: {{ graf_hashrate | safe }}, borderColor: '#3366ff', tension: 0.3, pointRadius: 0 }, { label: 'Temp Max (°C)', data: {{ graf_temps | safe }}, borderColor: '#ff5252', tension: 0.3, pointRadius: 0, yAxisID: 'y1' } ]}, options: { responsive: true, maintainAspectRatio: false, scales: { y1: { type: 'linear', position: 'right' } } } });
    </script>
</body></html>
"""

HTML_SUBPAGINA = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>Antminer SaaS - {{ titulo }}</title>
<style>
    :root { --bg-body: #14151f; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --bg-card: #1b1d29; --border-color: #292a38; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); padding: 40px; display: flex; flex-direction: column; align-items: center; }
    .card { background: var(--bg-card); border: 1px solid var(--border-color); padding: 40px; border-radius: 8px; text-align: center; max-width: 500px; width: 100%; box-shadow: 0 4px 15px rgba(0,0,0,0.3); }
    .btn { background: var(--accent-blue); color: white; text-decoration: none; padding: 12px 25px; border-radius: 4px; display: inline-block; margin-top: 25px; font-weight: bold; transition: 0.2s;}
    .btn:hover { opacity: 0.8; }
</style></head>
<body>
    <div class="card">
        <h1 style="margin-top:0;">{{ titulo }}</h1>
        <p style="color: var(--text-muted); line-height: 1.6;">Esta sección está reservada para el desarrollo de la Fase 3 del SaaS. Aquí los clientes podrán gestionar configuraciones avanzadas, facturación y alertas por Telegram.</p>
        <a href="/" class="btn">← Volver al Panel Principal</a>
    </div>
</body></html>
"""

# --- 4. RUTAS DEL SISTEMA ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        u = request.form.get('username'); p = request.form.get('password')
        con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
        if cur.execute("SELECT id FROM usuarios WHERE username=? AND password=?", (u, p)).fetchone():
            session['usuario'] = u; return redirect('/')
        else: error = 'Credenciales incorrectas'
    return render_template_string(HTML_LOGIN, error=error)

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/')
@login_required
def index():
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    mineros = cur.execute("SELECT * FROM mineros_activos").fetchall(); con.close()
    
    datos_red = obtener_datos_red()
    registros, en_linea, offline, hashrate_flota, consumo_w_flota, temp_max_flota, ingreso_dia = [], 0, 0, 0.0, 0.0, 0, 0.0
    
    for m in mineros:
        ip, modelo, hr, tm, cw, estado, ultima_act = m
        if (datetime.now() - datetime.strptime(ultima_act, "%Y-%m-%d %H:%M:%S")).total_seconds() < 180:
            en_linea += 1; hashrate_flota += hr; consumo_w_flota += cw
            if tm > temp_max_flota: temp_max_flota = tm
            
            b_dia = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296) if datos_red['dificultad_t'] > 0 else 0
            i_bruto = b_dia * datos_red['precio']; ingreso_dia += i_bruto
            str_gan = f"-USD {abs(i_bruto):.2f}" if i_bruto < 0 else f"{i_bruto:.2f} USD"
            
            registros.append({'ip': ip, 'modelo': modelo, 'estado': '<span style="color:#00e676;">● En línea</span>', 'hashrate': hr, 'temp_num': tm, 'consumo_kw': round(cw/1000, 1), 'ganancia_neta': str_gan})
        else:
            offline += 1
            registros.append({'ip': ip, 'modelo': modelo, 'estado': '<span style="color:#ff5252;">● Sin conexión</span>', 'hashrate': 0, 'temp_num': 0, 'consumo_kw': 0, 'ganancia_neta': "0.00 USD"})
            
    datos_flota = {
        'hashrate_total': round(hashrate_flota, 1), 'consumo_w_total': round(consumo_w_flota, 0), 'consumo_kw': round(consumo_w_flota/1000, 1),
        'temp_max': temp_max_flota, 'precio_btc_raw': datos_red['precio'], 'precio_btc': f"{datos_red['precio']:,.2f}".replace(',', '.'), 
        'dificultad_t': round(datos_red['dificultad_t'], 2), 'recompensa_bloque': datos_red['recompensa'],
        'ingreso_dia': f"{ingreso_dia:.2f}", 'ganancia_dia': f"{ingreso_dia:.2f} USD"
    }
    return render_template_string(HTML_PRINCIPAL, registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=datos_flota)

@app.route('/minero/<ip>')
@login_required
def detalle_minero(ip):
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    m = cur.execute("SELECT * FROM mineros_activos WHERE ip=?", (ip,)).fetchone()
    hist = cur.execute("SELECT fecha, hashrate, temp_max FROM historial_nube WHERE ip=? ORDER BY id DESC LIMIT 50", (ip,)).fetchall()
    con.close()
    if not m: return redirect('/')
    
    hist.reverse()
    fechas = [h[0].split(' ')[1][:5] for h in hist] if hist else [datetime.now().strftime("%H:%M")]
    hrs = [h[1] for h in hist] if hist else [m[2]]
    tmps = [h[2] for h in hist] if hist else [m[3]]
    
    dt = {'hashrate': m[2], 'temp_max': m[3], 'errores': '-', 'consumo': m[4], 'fans': '- / - / - / -', 'uptime': '-'}
    disp = {'firmware': 'Antminer (Cloud)', 'eficiencia': round(m[4]/m[2], 2) if m[2] > 0 else 0}
    vp = {'t0_c': '-', 't0_p': '-', 't1_c': '-', 't1_p': '-', 't2_c': '-', 't2_p': '-'}
    
    return render_template_string(HTML_DETALLE, ip=ip, nombre=ip[-3:], modelo=m[1], ultimo=dt, v_pools=vp, dispositivo=disp, graf_fechas=json.dumps(fechas), graf_hashrate=json.dumps(hrs), graf_temps=json.dumps(tmps))

@app.route('/granjas')
@login_required
def granjas(): return render_template_string(HTML_SUBPAGINA, titulo="🏭 Gestión de Granjas")
@app.route('/alertas')
@login_required
def alertas(): return render_template_string(HTML_SUBPAGINA, titulo="🔔 Centro de Alertas")
@app.route('/plan')
@login_required
def plan(): return render_template_string(HTML_SUBPAGINA, titulo="📋 Planes y Facturación")
@app.route('/ajustes')
@login_required
def ajustes(): return render_template_string(HTML_SUBPAGINA, titulo="⚙️ Ajustes del Sistema")

@app.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    comando_real = "restart" if accion == 'restart' else "pause" if accion == 'sleep' else "resume" if accion == 'normal' else None
    if comando_real:
        con = sqlite3.connect('historial_minero.db')
        con.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (?, ?, 'pendiente', ?)", (ip, comando_real, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit(); con.close()
    return redirect('/')

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json
    if not datos: return "Error", 400
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for minero in datos.get('mineros', []):
        cur.execute('''INSERT INTO mineros_activos (ip, modelo, hashrate, temp_max, consumo_w, estado, ultima_act) 
                       VALUES (?, ?, ?, ?, ?, 'online', ?) ON CONFLICT(ip) DO UPDATE SET 
                       hashrate=excluded.hashrate, temp_max=excluded.temp_max, consumo_w=excluded.consumo_w, ultima_act=excluded.ultima_act''', 
                    (minero['ip'], minero['modelo'], minero['hashrate'], minero['temp'], minero['consumo'], ahora))
        # Guardar para los gráficos
        cur.execute("INSERT INTO historial_nube (ip, hashrate, temp_max, fecha) VALUES (?, ?, ?, ?)", (minero['ip'], minero['hashrate'], minero['temp'], ahora))
    
    comandos_pendientes = cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente'").fetchall()
    lista_ordenes = []
    for cmd in comandos_pendientes:
        lista_ordenes.append({"ip": cmd[1], "comando": cmd[2]})
        cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=?", (cmd[0],))
    
    con.commit(); con.close()
    return jsonify({"status": "ok", "ordenes": lista_ordenes}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
