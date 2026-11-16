from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify
from functools import wraps
import sqlite3, json, urllib.request, time
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

# --- 1. BASE DE DATOS SAAS ---
def init_db():
    conexion = sqlite3.connect('historial_minero.db'); c = conexion.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS cola_comandos (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, comando TEXT, estado TEXT, fecha TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS historial_nube (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, hashrate REAL, temp_max INTEGER, fecha TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS nombres_mineros (ip TEXT UNIQUE, nombre TEXT)')
    
    # Auto-parche para nuevas métricas
    for col in ['errores INTEGER', 'worker TEXT', 'pool TEXT', 'mac TEXT', 'serial TEXT', 'firmware TEXT', 'fans TEXT']:
        try: c.execute(f'ALTER TABLE mineros_activos ADD COLUMN {col}')
        except: pass

    if not c.execute("SELECT * FROM usuarios WHERE username='giovanni'").fetchone():
        c.execute("INSERT INTO usuarios (username, password) VALUES ('giovanni', 'admin123')")
    conexion.commit(); conexion.close()
init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

cache_red = {'datos': {'precio': 64809.0, 'dificultad_t': 134.82, 'recompensa': 3.125}, 'ultima_act': 0}
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

# --- 2. PLANTILLAS HTML ADAPTADAS ---

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
    .btn-login { width: 100%; background: var(--accent-blue); color: white; border: none; padding: 12px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.2s; margin-top: 10px; }
</style></head>
<body>
    <div class="login-card">
        <div class="logo-container"><div class="logo-icon">A</div><div class="logo-text">Antminer <span>Tools</span></div></div>
        {% if error %}<div style="color:var(--accent-red); margin-bottom:15px; text-align:center;">{{ error }}</div>{% endif %}
        <form method="POST">
            <div class="form-group"><label>Usuario</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Contraseña</label><input type="password" name="password" required></div>
            <button type="submit" class="btn-login">Ingresar al Panel</button>
        </form>
    </div>
</body></html>
"""

HTML_PRINCIPAL = """
<!DOCTYPE html>
<html lang="es" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>Antminer Tools - Resumen</title>
    <script type="text/javascript" src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
    <style>
        :root[data-theme="dark"] { --bg-body: #14151f; --bg-sidebar: #191a24; --bg-card: #1b1d29; --bg-input: #14151f; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --text-sub: #8b8d9b; --accent-blue: #3366ff; --accent-green: #00e676; --accent-red: #ff5252; --hover-bg: #202230; --active-bg: #232533; }
        :root[data-theme="light"] { --bg-body: #f4f5f7; --bg-sidebar: #ffffff; --bg-card: #ffffff; --bg-input: #f4f5f7; --border-color: #e2e8f0; --text-main: #1e293b; --text-muted: #64748b; --text-sub: #94a3b8; --accent-blue: #2563eb; --accent-green: #10b981; --accent-red: #ef4444; --hover-bg: #f8fafc; --active-bg: #f1f5f9; }
        body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; display: flex; height: 100vh; overflow: hidden; top: 0 !important; }
        .skiptranslate iframe { display: none !important; } body { top: 0px !important; } #google_translate_element { display: none; }
        a { text-decoration: none; color: inherit; }
        
        .sidebar { width: 220px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding-top: 15px; }
        .logo-container { padding: 10px 20px 20px; font-weight: bold; font-size: 1.1em; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border-color); }
        .logo-icon { background: var(--accent-blue); color: white; width: 24px; height: 24px; border-radius: 4px; display: flex; justify-content: center; align-items: center; font-size: 0.8em; }
        .logo-text span { color: var(--accent-blue); font-weight: normal; }
        .menu { list-style: none; padding: 15px 0; margin: 0; flex: 1; }
        .menu a { padding: 12px 20px; font-size: 0.9em; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; gap: 12px; transition: 0.2s; }
        .menu a:hover { background: var(--hover-bg); color: var(--text-main); }
        .menu a.active { background: var(--active-bg); color: var(--text-main); border-left: 3px solid var(--accent-blue); }
        
        .right-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .top-bar { height: 60px; background: var(--bg-body); border-bottom: 1px solid var(--border-color); display: flex; justify-content: flex-end; align-items: center; padding: 0 30px; gap: 15px; }
        .top-btn, .top-scan-btn, .top-lang-select { background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-main); height: 36px; border-radius: 6px; cursor: pointer; transition: 0.2s; font-size: 0.85em; font-weight: bold; display: flex; align-items: center; justify-content: center; }
        .top-btn { width: 36px; font-size: 1.1em; color: var(--text-muted); }
        .top-scan-btn { padding: 0 15px; }
        .top-lang-select { padding: 0 10px; outline: none; }
        .top-status { display: flex; align-items: center; gap: 15px; font-size: 0.85em; color: var(--text-muted); font-weight: bold; margin-left: 10px; }
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
        .calc-resultados { display: grid; grid-template-columns: repeat(8, 1fr); gap: 10px; margin-bottom: 15px; }
        .res-box { background: var(--bg-input); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px 8px; text-align: center; }
        .res-box .val { font-size: 1.1em; font-weight: bold; margin-bottom: 4px; }
        .res-box .lbl { font-size: 0.65em; color: var(--text-muted); text-transform: uppercase; }
        
        .tabla-container { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px 20px; text-align: left; border-bottom: 1px solid var(--border-color); font-size: 0.85em; }
        th { color: var(--text-muted); text-transform: uppercase; background: var(--bg-card); }
        tr.fila-minero { cursor: pointer; transition: 0.15s ease; }
        tr.fila-minero:hover { background: var(--hover-bg); }
        .nombre-link { color: var(--text-main); font-size: 1.1em; font-weight: bold; }
    </style>
</head>
<body>
    <div id="google_translate_element"></div>
    <div class="sidebar">
        <div class="logo-container"><div class="logo-icon">A</div><div class="logo-text" translate="no">Antminer <span>Tools</span></div></div>
        <div class="menu">
            <a href="/" class="active">📊 Resumen</a>
            <a href="#">🏭 Granjas</a>
            <a href="#">⛏️ Mineros</a>
            <a href="#">🔔 Alertas</a>
            <a href="#">📋 Plan</a>
            <a href="#">⚙️ Ajustes</a>
        </div>
    </div>
    <div class="right-container">
        <div class="top-bar">
            <button class="top-scan-btn" onclick="fetch('/cmd/all/scan').then(()=>location.reload())">🔄 Escanear Red</button>
            <button class="top-btn" onclick="toggleTheme()" id="themeIcon">☀️</button>
            <select class="top-lang-select" id="langSelector" onchange="cambiarIdioma(this.value)">
                <option value="es">🌐 Español</option><option value="en">🌐 English</option><option value="pt">🌐 Português</option>
                <option value="fr">🌐 Français</option><option value="de">🌐 Deutsch</option><option value="it">🌐 Italiano</option>
                <option value="ru">🌐 Русский</option><option value="zh-CN">🌐 中文</option><option value="ja">🌐 日本語</option><option value="ko">🌐 한국어</option>
            </select>
            <div class="top-status">
                <span id="relojTop" translate="no">00:00</span>
                <span class="status-envivo"><div class="dot-pulse"></div> En vivo</span>
            </div>
        </div>
        <div class="main-content">
            <div class="cabecera">
                <h1>Resumen</h1>
                <div class="sub">Estado de la flota en 1 granja(s) · <span id="textoActualizado" style="color:var(--accent-green);">actualizado hace 1s</span></div>
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
                <div class="stat-card"><div class="lbl">PRECIO BTC ₿</div><div class="val btc-live-price" translate="no">USD {{ flota.precio_btc }}</div></div>
                <div class="stat-card"><div class="lbl">INGRESOS / DÍA 💰</div><div class="val val-green" translate="no">{{ flota.ingreso_dia }} USD</div></div>
                <div class="stat-card"><div class="lbl">GANANCIA / DÍA 💵</div><div class="val" style="color: {{ '#ff5252' if '-' in flota.ganancia_dia else '#00e676' }};" translate="no">{{ flota.ganancia_dia }}</div></div>
            </div>
            
            <div class="calc-panel">
                <div class="calc-title">₿ Calculadora de rentabilidad (En vivo)</div>
                <div class="calc-top-boxes">
                    <div class="box-mini"><div class="v" id="topBtcDia" translate="no">0.00000000</div><div class="l">BTC/DÍA</div></div>
                    <div class="box-mini"><div class="v" id="topBtcSemana" translate="no">0.00000000</div><div class="l">BTC/SEMANA</div></div>
                    <div class="box-mini"><div class="v" id="topBtcMes" translate="no">0.00000000</div><div class="l">BTC / MES</div></div>
                    <div class="box-mini"><div class="v" id="topBtcAnio" translate="no">0.00000000</div><div class="l">BTC/AÑO</div></div>
                </div>

                <div class="calc-inputs">
                    <div class="calc-input-group"><label>Hashrate (TH/s)</label><input type="number" id="hashrateInput" value="{{ flota.hashrate_total }}" oninput="calcularRentabilidad()"></div>
                    <div class="calc-input-group"><label>Consumo (W)</label><input type="number" id="consumoInput" value="{{ flota.consumo_w_total }}" oninput="calcularRentabilidad()"></div>
                    <div class="calc-input-group"><label>Luz (USD/kWh)</label><input type="number" step="0.01" id="kwhInput" value="0.08" oninput="calcularRentabilidad()"></div>
                    <div class="calc-input-group"><label>Comisión pool (%)</label><input type="number" step="0.1" id="comisionInput" value="2.0" oninput="calcularRentabilidad()"></div>
                </div>

                <div class="calc-resultados">
                    <div class="res-box"><div class="val" id="resBtcDia" translate="no">0.00000000</div><div class="lbl">BTC/DÍA</div></div>
                    <div class="res-box"><div class="val" id="resIngresoDia" translate="no">0.00 USD</div><div class="lbl">INGRESOS / DÍA</div></div>
                    <div class="res-box"><div class="val" id="resElecDia" style="color:var(--accent-red);" translate="no">USD 0.00</div><div class="lbl">ELECTRICIDAD / DÍA</div></div>
                    <div class="res-box"><div class="val" id="resGananciaDia" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA / DÍA</div></div>
                    <div class="res-box"><div class="val" id="resGananciaSemana" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA / SEMANA</div></div>
                    <div class="res-box"><div class="val" id="resGananciaMes" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA / MES</div></div>
                    <div class="res-box"><div class="val" id="resGananciaAnio" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA / AÑO</div></div>
                    <div class="res-box"><div class="val" id="resCosteBtc" translate="no">USD 0.00</div><div class="lbl">COSTE POR BTC</div></div>
                </div>
            </div>

            <div class="tabla-container">
                <table>
                    <tr><th>MINERO</th><th>ESTADO</th><th>HASHRATE</th><th>TEMP</th><th>CONSUMO</th><th>EFICIENCIA</th><th>GANANCIA / DÍA</th></tr>
                    {% for f in registros %}
                    <tr class="fila-minero" onclick="window.location.href='/minero/{{ f.ip }}'" data-hashrate="{{ f.hashrate }}" data-consumo="{{ f.consumo_kw * 1000 }}">
                        <td><span class="nombre-link" translate="no">{{ f.nombre }}</span><br><span style="font-size:0.7em; color:var(--text-sub);" translate="no">{{ f.modelo }}</span></td>
                        <td>{{ f.estado | safe }}</td>
                        <td translate="no">{{ f.hashrate }} TH/s</td>
                        <td style="color: {{ 'var(--accent-red)' if f.temp_num > 80 else 'var(--accent-blue)' }};" translate="no">{{ f.temp_num }} °C</td>
                        <td translate="no">{{ f.consumo_kw }} kW</td>
                        <td translate="no">{{ f.eficiencia }} J/TH</td>
                        <td class="celda-ganancia" style="font-weight:bold; color: {{ '#ff5252' if '-' in f.ganancia_neta else '#00e676' }};" translate="no">{{ f.ganancia_neta }}</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>
    </div>
    <script>
        let PRECIO_BTC = parseFloat("{{ flota.precio_btc_raw }}");
        const DIFICULTAD = parseFloat("{{ flota.dificultad_t }}");
        async function actualizarPrecioEnVivo() {
            try {
                let r = await fetch('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT');
                let d = await r.json(); PRECIO_BTC = parseFloat(d.price);
                document.querySelectorAll('.btc-live-price').forEach(l => l.innerText = 'USD ' + PRECIO_BTC.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}));
                calcularRentabilidad();
            } catch(e) {}
        }
        setInterval(actualizarPrecioEnVivo, 5000); 
        
        function formatVal(val) { return val < 0 ? `<span style="color:var(--accent-red);">-USD ${Math.abs(val).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}</span>` : `<span style="color:var(--accent-green);">USD ${val.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}</span>`; }
        function calcularRentabilidad() {
            let hTH = parseFloat(document.getElementById('hashrateInput').value) || 0, cW = parseFloat(document.getElementById('consumoInput').value) || 0;
            let cKwh = parseFloat(document.getElementById('kwhInput').value) || 0, pool = parseFloat(document.getElementById('comisionInput').value) || 0;
            let btcDia = DIFICULTAD > 0 ? (hTH * 3.125 * 86400) / (DIFICULTAD * 4294967296) : 0;
            document.getElementById('topBtcDia').innerText = btcDia.toFixed(8); document.getElementById('topBtcSemana').innerText = (btcDia * 7).toFixed(8);
            document.getElementById('topBtcMes').innerText = (btcDia * 30).toFixed(6); document.getElementById('topBtcAnio').innerText = (btcDia * 365).toFixed(4);
            document.getElementById('resBtcDia').innerText = btcDia.toFixed(8);
            let inBruto = btcDia * PRECIO_BTC, inNeto = inBruto - (inBruto * (pool / 100)), elecDia = (cW * 24 / 1000) * cKwh, ganDia = inNeto - elecDia;
            document.getElementById('resIngresoDia').innerText = 'USD ' + inNeto.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            document.getElementById('resElecDia').innerText = elecDia <= 0 ? `USD 0.00` : `USD ${elecDia.toFixed(2)}`;
            document.getElementById('resGananciaDia').innerHTML = formatVal(ganDia); document.getElementById('resGananciaSemana').innerHTML = formatVal(ganDia * 7);
            document.getElementById('resGananciaMes').innerHTML = formatVal(ganDia * 30); document.getElementById('resGananciaAnio').innerHTML = formatVal(ganDia * 365);
            let costBtc = btcDia > 0 ? (elecDia / btcDia) : 0;
            document.getElementById('resCosteBtc').innerText = costBtc > 0 ? '$' + costBtc.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '--';
        }
        function googleTranslateElementInit() { new google.translate.TranslateElement({pageLanguage: 'es', autoDisplay: false}, 'google_translate_element'); }
        function cambiarIdioma(lang) { let selectField = document.querySelector(".goog-te-combo"); if (selectField) { selectField.value = lang; selectField.dispatchEvent(new Event("change")); } }
        function actualizarReloj() { const ahora = new Date(); document.getElementById('relojTop').innerText = ahora.getHours().toString().padStart(2, '0') + ':' + ahora.getMinutes().toString().padStart(2, '0'); }
        setInterval(actualizarReloj, 1000); actualizarReloj();
        function toggleTheme() { const html = document.documentElement, icon = document.getElementById('themeIcon'); if (html.getAttribute('data-theme') === 'dark') { html.setAttribute('data-theme', 'light'); icon.innerText = '🌙'; } else { html.setAttribute('data-theme', 'dark'); icon.innerText = '☀️'; } }
        let segs = 1; setInterval(() => { document.getElementById('textoActualizado').innerText = 'actualizado hace ' + (++segs) + 's'; }, 1000);
        window.onload = function() { calcularRentabilidad(); }; setTimeout(() => window.location.reload(), 60000);
    </script>
</body>
</html>
"""

HTML_DETALLE = """
<!DOCTYPE html>
<html lang="es" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>Detalle - {{ nombre }}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root[data-theme="dark"] { --bg-body: #14151f; --bg-card: #1b1d29; --bg-input: #14151f; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --accent-red: #ff5252; --accent-green: #00e676; }
        :root[data-theme="light"] { --bg-body: #f4f5f7; --bg-card: #ffffff; --bg-input: #f4f5f7; --border-color: #e2e8f0; --text-main: #1e293b; --text-muted: #64748b; --accent-blue: #2563eb; --accent-red: #ef4444; --accent-green: #10b981; }
        body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; padding: 20px; transition: 0.3s; }
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
        
        .panel-control { display: flex; gap: 10px; margin-bottom: 20px; background: var(--bg-card); padding: 15px; border-radius: 8px; border: 1px solid var(--border-color); align-items: center; flex-wrap: wrap; }
        .btn-accion { background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-main); padding: 8px 15px; border-radius: 4px; cursor: pointer; font-weight: bold; }
        .btn-accion:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
        
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .info-table { width: 100%; border-collapse: collapse; }
        .info-table td { padding: 10px 0; border-bottom: 1px solid var(--border-color); font-size: 0.9em; }
        .info-table td:nth-child(1) { color: var(--text-muted); }
        .info-table td:nth-child(2) { text-align: right; font-weight: bold; }
        
        .input-pool { background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-main); padding: 6px 10px; border-radius: 4px; width: 90%; }
        .input-nombre { background: transparent; border: none; color: var(--text-main); font-size: 1em; font-weight: bold; width: 150px; border-bottom: 1px dashed var(--border-color); outline:none; }
        .input-nombre:focus { border-bottom-color: var(--accent-blue); }
    </style>
</head>
<body>
    <div class="contenedor">
        <div class="header-detalle">
            <div style="display:flex; align-items:center; gap:15px;">
                <a href="/" class="btn-volver">← Volver</a>
                <div>
                    <h2 class="titulo-minero">
                        <input type="text" id="nombreMinero" class="input-nombre" value="{{ nombre }}" onchange="guardarNombre()" translate="no"> 
                        <span class="estado-punto"></span>
                    </h2>
                    <div style="color:var(--text-muted); font-size: 0.9em; margin-top: 5px;" translate="no">{{ modelo }} - {{ ip }}</div>
                </div>
            </div>
        </div>

        <div class="grid-4">
            <div class="card"><h4>Tasa de Hash</h4><div class="val" translate="no">{{ ultimo.hashrate }} TH/s</div></div>
            <div class="card"><h4>Errores HW</h4><div class="val" style="color:var(--accent-red);" translate="no">{{ ultimo.errores }}</div></div>
            <div class="card"><h4>Consumo</h4><div class="val" style="color:var(--accent-green);" translate="no">{{ ultimo.consumo }} W</div></div>
            <div class="card"><h4>Temp Máx</h4><div class="val" style="color:var(--accent-red);" translate="no">{{ ultimo.temp_max }} °C</div></div>
        </div>

        <div class="panel-control"><span style="font-weight:bold; margin-right:15px;">⚡ Comandos SaaS (Nube)</span>
            <button class="btn-accion" onclick="if(confirm('¿Reiniciar?')) fetch('/cmd/{{ ip }}/restart').then(()=>location.reload())">🔄 Reiniciar</button>
            <button class="btn-accion" onclick="if(confirm('¿Modo Reposo?')) fetch('/cmd/{{ ip }}/sleep').then(()=>location.reload())">😴 Reposo</button>
            <button class="btn-accion" onclick="if(confirm('¿Modo Normal?')) fetch('/cmd/{{ ip }}/normal').then(()=>location.reload())">▶ Normal</button>
        </div>

        <div class="card" style="height: 300px;"><canvas id="hashrateChart"></canvas></div>

        <div class="grid-2">
            <div class="card">
                <h3 style="color:var(--text-muted); margin-top:0;">INFORMACIÓN DE RED Y POOL</h3>
                <table class="info-table">
                    <tr><td>Estado Pool</td><td translate="no"><span style="color:var(--accent-green);">Activo</span></td></tr>
                    <tr><td>Pool URL</td><td translate="no" style="font-size:0.8em; color:var(--accent-blue);">{{ extra.pool }}</td></tr>
                    <tr><td>Worker</td><td translate="no">{{ extra.worker }}</td></tr>
                </table>
            </div>
            <div class="card">
                <h3 style="color:var(--text-muted); margin-top:0;">INFORMACIÓN DEL DISPOSITIVO</h3>
                <table class="info-table">
                    <tr><td>Modelo</td><td translate="no">{{ modelo }}</td></tr>
                    <tr><td>Firmware</td><td translate="no">{{ extra.firmware }}</td></tr>
                    <tr><td>Número de serie</td><td translate="no">{{ extra.serial }}</td></tr>
                    <tr><td>MAC Address</td><td translate="no">{{ extra.mac }}</td></tr>
                    <tr><td>IP Local</td><td translate="no">{{ ip }}</td></tr>
                    <tr><td>Ventiladores</td><td translate="no">{{ ultimo.fans }} RPM</td></tr>
                </table>
            </div>
        </div>

        <div class="card">
            <h3 style="color:var(--text-muted); margin-top:0;">CONFIGURAR POOLS DESDE LA NUBE</h3>
            <table class="info-table" style="text-align: left;">
                <tr style="color:var(--text-muted);"><th>URL de Piscina</th><th>Trabajador</th><th>Acción</th></tr>
                <tr>
                    <td><input type="text" id="url_0" value="{{ extra.pool }}" class="input-pool" translate="no"></td>
                    <td><input type="text" id="user_0" value="{{ extra.worker }}" class="input-pool" translate="no"></td>
                    <td><button class="btn-accion" style="padding:6px 12px; background:var(--accent-blue); color:white; border:none;" onclick="guardarPool()">Guardar en Cola</button></td>
                </tr>
            </table>
            <p style="font-size:0.8em; color:var(--text-muted); margin-top:10px;">Al presionar "Guardar en Cola", la orden se enviará al Agente local en la granja para que la aplique de forma segura.</p>
        </div>
    </div>
    <script>
        function guardarNombre() {
            let n = document.getElementById('nombreMinero').value;
            fetch('/renombrar/{{ ip }}', { method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'nombre='+encodeURIComponent(n) });
        }
        function guardarPool() {
            let u = document.getElementById('url_0').value, us = document.getElementById('user_0').value;
            if(confirm('¿Enviar actualización de Pool a la cola de comandos?')) {
                fetch('/cmd/{{ ip }}/setpool?url='+encodeURIComponent(u)+'&user='+encodeURIComponent(us)).then(()=>alert('Comando encolado. Se aplicará en el próximo ciclo del Agente.'));
            }
        }
        const ctx = document.getElementById('hashrateChart').getContext('2d');
        new Chart(ctx, { type: 'line', data: { labels: {{ graf_fechas | safe }}, datasets: [ { label: 'Hashrate (TH/s)', data: {{ graf_hashrate | safe }}, borderColor: '#3366ff', tension: 0.3, pointRadius: 0 }, { label: 'Temp Max (°C)', data: {{ graf_temps | safe }}, borderColor: '#ff5252', tension: 0.3, pointRadius: 0, yAxisID: 'y1' } ]}, options: { responsive: true, maintainAspectRatio: false, scales:{y1:{position:'right'}} } });
    </script>
</body>
</html>
"""

# --- 3. RUTAS FLASK DEL SAAS ---

@app.route('/')
@login_required
def index():
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    mineros = cur.execute("SELECT * FROM mineros_activos").fetchall()
    dic_nombres = dict(cur.execute("SELECT ip, nombre FROM nombres_mineros").fetchall())
    con.close()
    
    datos_red = obtener_datos_red()
    registros, en_linea, offline, ht, cw, tm, ig = [], 0, 0, 0.0, 0.0, 0, 0.0
    
    for m in mineros:
        ip = m[0]; mod = m[1]; ult = m[6] if len(m)>6 and m[6] else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hr = float(m[2]) if len(m)>2 and m[2] else 0.0
        t = int(m[3]) if len(m)>3 and m[3] else 0
        c = float(m[4]) if len(m)>4 and m[4] else 0.0
        nombre = dic_nombres.get(ip, ip) 
        
        segs_desconectado = (datetime.now() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds()
        
        if segs_desconectado < 180:
            en_linea += 1; ht += hr; cw += c; tm = max(tm, t)
            b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296) if datos_red['dificultad_t'] > 0 else 0
            ib = b * datos_red['precio']; ig += ib
            str_gan = f"-USD {abs(ib):.2f}" if ib < 0 else f"{ib:.2f} USD"
            registros.append({'ip': ip, 'nombre': nombre, 'modelo': mod, 'estado': '<span style="color:var(--accent-green);">● En línea</span>', 'hashrate': hr, 'temp_num': t, 'consumo_kw': round(c/1000, 1), 'eficiencia': round(c/hr, 2) if hr>0 else 0, 'ganancia_neta': str_gan})
        else:
            offline += 1
            registros.append({'ip': ip, 'nombre': nombre, 'modelo': mod, 'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>', 'hashrate': 0, 'temp_num': 0, 'consumo_kw': 0, 'eficiencia': 0, 'ganancia_neta': "USD 0.00"})
            
    flota = {'hashrate_total': round(ht, 1), 'consumo_w_total': round(cw, 0), 'consumo_kw': round(cw/1000, 1), 'temp_max': tm, 'precio_btc_raw': datos_red['precio'], 'precio_btc': f"{datos_red['precio']:,.2f}", 'dificultad_t': round(datos_red['dificultad_t'], 2), 'recompensa_bloque': datos_red['recompensa'], 'ingreso_dia': f"{ig:.2f}", 'ganancia_dia': f"{ig:.2f} USD"}
    
    return render_template_string(HTML_PRINCIPAL, registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=flota)

@app.route('/minero/<ip>')
@login_required
def detalle_minero(ip):
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    m = cur.execute("SELECT * FROM mineros_activos WHERE ip=?", (ip,)).fetchone()
    hist = cur.execute("SELECT fecha, hashrate, temp_max FROM historial_nube WHERE ip=? ORDER BY id DESC LIMIT 50", (ip,)).fetchall()
    nom = cur.execute("SELECT nombre FROM nombres_mineros WHERE ip=?", (ip,)).fetchone()
    con.close()
    if not m: return redirect('/')
    
    nombre_mostrar = nom[0] if nom else ip
    h_val = float(m[2]) if len(m)>2 and m[2] is not None else 0.0
    t_val = int(m[3]) if len(m)>3 and m[3] is not None else 0
    c_val = float(m[4]) if len(m)>4 and m[4] is not None else 0.0
    errores = int(m[7]) if len(m)>7 and m[7] is not None else 0
    worker = str(m[8]) if len(m)>8 and m[8] else "No detectado"
    pool = str(m[9]) if len(m)>9 and m[9] else "No detectado"
    mac = str(m[10]) if len(m)>10 and m[10] else "-"
    serial = str(m[11]) if len(m)>11 and m[11] else "-"
    fw = str(m[12]) if len(m)>12 and m[12] else "Desconocido"
    fans = str(m[13]) if len(m)>13 and m[13] else "0 / 0"
    
    hist.reverse()
    f = [h[0].split(' ')[1][:5] for h in hist] if hist else [datetime.now().strftime("%H:%M")]
    hr = [float(h[1]) if h[1] else 0 for h in hist] if hist else [h_val]
    tp = [int(h[2]) if h[2] else 0 for h in hist] if hist else [t_val]
    
    dt = {'hashrate': h_val, 'temp_max': t_val, 'errores': errores, 'consumo': c_val, 'fans': fans}
    extra = {'worker': worker, 'pool': pool, 'mac': mac, 'serial': serial, 'firmware': fw}
    
    return render_template_string(HTML_DETALLE, ip=ip, nombre=nombre_mostrar, modelo=m[1], ultimo=dt, extra=extra, graf_fechas=json.dumps(f), graf_hashrate=json.dumps(hr), graf_temps=json.dumps(tp))

@app.route('/renombrar/<ip>', methods=['POST'])
@login_required
def renombrar(ip):
    nombre = request.form.get('nombre')
    if nombre:
        con = sqlite3.connect('historial_minero.db')
        con.execute("INSERT INTO nombres_mineros (ip, nombre) VALUES (?, ?) ON CONFLICT(ip) DO UPDATE SET nombre=excluded.nombre", (ip, nombre))
        con.commit(); con.close()
    return "OK"

@app.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    cmd_str = ""
    if accion in ['restart', 'sleep', 'normal']: cmd_str = accion
    elif accion == 'scan': cmd_str = 'scan' # Para escanear red (all/scan)
    elif accion == 'setpool':
        u = request.args.get('url', ''); us = request.args.get('user', '')
        cmd_str = f"setpool|{u}|{us}"
        
    if cmd_str:
        con = sqlite3.connect('historial_minero.db')
        con.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (?, ?, 'pendiente', ?)", (ip, cmd_str, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit(); con.close()
    return "OK"

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

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json
    if not datos: return "Error", 400
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for m in datos.get('mineros', []):
        err = m.get('errores', 0); wk = m.get('worker', ''); pl = m.get('pool', ''); mc = m.get('mac', ''); sr = m.get('serial', ''); fw = m.get('firmware', ''); fs = m.get('fans', '')
        cur.execute('''INSERT INTO mineros_activos (ip, modelo, hashrate, temp_max, consumo_w, estado, ultima_act, errores, worker, pool, mac, serial, firmware, fans) 
                       VALUES (?, ?, ?, ?, ?, 'online', ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ip) DO UPDATE SET 
                       hashrate=excluded.hashrate, temp_max=excluded.temp_max, consumo_w=excluded.consumo_w, ultima_act=excluded.ultima_act, errores=excluded.errores, worker=excluded.worker, pool=excluded.pool, mac=excluded.mac, serial=excluded.serial, firmware=excluded.firmware, fans=excluded.fans''', 
                    (m['ip'], m['modelo'], m['hashrate'], m['temp'], m['consumo'], ahora, err, wk, pl, mc, sr, fw, fs))
        cur.execute("INSERT INTO historial_nube (ip, hashrate, temp_max, fecha) VALUES (?, ?, ?, ?)", (m['ip'], m['hashrate'], m['temp'], ahora))
    
    pend = cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente'").fetchall()
    ordenes = [{"ip": c[1], "comando": c[2]} for c in pend]
    for c in pend: cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=?", (c[0],))
    con.commit(); con.close()
    return jsonify({"status": "ok", "ordenes": ordenes}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
