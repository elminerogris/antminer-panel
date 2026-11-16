from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify
from functools import wraps
import sqlite3, json, urllib.request, time
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

# --- 1. BASE DE DATOS ---
def init_db():
    conexion = sqlite3.connect('historial_minero.db')
    c = conexion.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS cola_comandos (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, comando TEXT, estado TEXT, fecha TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS historial_nube (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, hashrate REAL, temp_max INTEGER, fecha TEXT)')
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

# --- 2. PLANTILLAS HTML (ARQUITECTURA MAESTRA) ---

# A) LOGIN
HTML_LOGIN = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>Antminer Tools - Login</title>
<style>
    :root { --bg: #14151f; --card: #1b1d29; --border: #292a38; --text: #fff; --text-m: #a5a7b5; --blue: #3366ff; --red: #ff5252; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
    .box { background: var(--card); padding: 40px 30px; border-radius: 8px; border: 1px solid var(--border); width: 100%; max-width: 320px; }
    .logo { font-size: 1.5em; font-weight: bold; text-align: center; margin-bottom: 30px; }
    .logo span { color: var(--blue); }
    input { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 12px; border-radius: 4px; box-sizing: border-box; margin-bottom: 20px;}
    button { width: 100%; background: var(--blue); color: white; border: none; padding: 12px; border-radius: 4px; font-weight: bold; cursor: pointer; }
    .err { background: rgba(255,82,82,0.1); color: var(--red); padding: 10px; border-radius: 4px; margin-bottom: 20px; text-align: center; font-size: 0.9em; }
</style></head>
<body>
    <div class="box"><div class="logo">Antminer <span>Tools</span></div>
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <form method="POST"><label style="font-size:0.8em;color:var(--text-m);">Usuario</label><input type="text" name="username" required>
    <label style="font-size:0.8em;color:var(--text-m);">Contraseña</label><input type="password" name="password" required>
    <button type="submit">Ingresar</button></form></div>
</body></html>
"""

# B) PLANTILLA MAESTRA (ENVUELVE A TODAS LAS PÁGINAS)
HTML_LAYOUT = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>Antminer Tools</title>
<style>
    :root[data-theme="dark"] { --bg: #14151f; --side: #191a24; --card: #1b1d29; --input: #14151f; --border: #292a38; --text: #fff; --text-m: #a5a7b5; --blue: #3366ff; --green: #00e676; --red: #ff5252; --hover: #202230; --act: #232533; }
    :root[data-theme="light"] { --bg: #f4f5f7; --side: #ffffff; --card: #ffffff; --input: #f4f5f7; --border: #e2e8f0; --text: #1e293b; --text-m: #64748b; --blue: #2563eb; --green: #10b981; --red: #ef4444; --hover: #f8fafc; --act: #f1f5f9; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); margin: 0; display: flex; height: 100vh; overflow: hidden; }
    a { text-decoration: none; color: inherit; }
    
    .sidebar { width: 220px; background: var(--side); border-right: 1px solid var(--border); display: flex; flex-direction: column; padding-top: 15px; }
    .logo { padding: 10px 20px 20px; font-weight: bold; font-size: 1.2em; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border); }
    .logo span { color: var(--blue); }
    .menu { list-style: none; padding: 15px 0; margin: 0; flex: 1; }
    .menu a { padding: 12px 20px; display: block; color: var(--text-m); transition: 0.2s; font-size: 0.9em; }
    .menu a:hover { background: var(--hover); color: var(--text); }
    .menu a.active { background: var(--act); color: var(--text); border-left: 3px solid var(--blue); }
    .side-bot { border-top: 1px solid var(--border); padding: 15px 20px; font-size: 0.85em; }
    
    .right { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
    .topbar { height: 60px; background: var(--bg); border-bottom: 1px solid var(--border); display: flex; justify-content: flex-end; align-items: center; padding: 0 30px; gap: 15px; }
    .btn-top { background: var(--card); border: 1px solid var(--border); color: var(--text); height: 36px; padding: 0 15px; border-radius: 6px; cursor: pointer; }
    
    .main { flex: 1; padding: 25px 30px; overflow-y: auto; }
    .title { margin: 0 0 5px 0; font-size: 1.8em; }
    .sub { font-size: 0.85em; color: var(--text-m); margin-bottom: 25px; }
    
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 25px; }
    .grid { display: grid; gap: 15px; }
    .g-11 { grid-template-columns: repeat(11, 1fr); } .g-4 { grid-template-columns: repeat(4, 1fr); } .g-3 { grid-template-columns: repeat(3, 1fr); } .g-2 { grid-template-columns: repeat(2, 1fr); }
    
    .stat { display: flex; flex-direction: column; justify-content: center; padding: 12px 10px; }
    .stat .l { font-size: 0.65em; color: var(--text-m); text-transform: uppercase; font-weight: bold; margin-bottom: 6px; }
    .stat .v { font-size: 1.2em; font-weight: bold; color: var(--text); }
    .c-blue { color: var(--blue)!important; } .c-green { color: var(--green)!important; } .c-red { color: var(--red)!important; }
    
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid var(--border); font-size: 0.85em; }
    th { color: var(--text-m); text-transform: uppercase; }
    tr:hover { background: var(--hover); }
    .link-ip { color: var(--text); font-weight: bold; text-decoration: underline; cursor: pointer; }
    
    .btn { background: var(--blue); color: white; border: none; padding: 10px 20px; border-radius: 4px; font-weight: bold; cursor: pointer; }
    .btn-out { background: transparent; border: 1px solid var(--border); color: var(--text); padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
    .btn-out:hover { border-color: var(--blue); color: var(--blue); }
    
    input[type="text"], input[type="number"], input[type="password"], select { background: var(--input); border: 1px solid var(--border); color: var(--text); padding: 10px; border-radius: 4px; width: 100%; box-sizing: border-box; }
</style></head>
<body>
    <div class="sidebar">
        <div class="logo">Antminer <span>Tools</span></div>
        <div class="menu">
            <a href="/" class="{{ 'active' if active_page == 'resumen' else '' }}">📊 Dashboard</a>
            <a href="/granjas" class="{{ 'active' if active_page == 'granjas' else '' }}">🏭 Granjas</a>
            <a href="/alertas" class="{{ 'active' if active_page == 'alertas' else '' }}">🔔 Alertas</a>
            <a href="/plan" class="{{ 'active' if active_page == 'plan' else '' }}">📋 Planes</a>
            <a href="/ajustes" class="{{ 'active' if active_page == 'ajustes' else '' }}">⚙️ Ajustes</a>
        </div>
        <div class="side-bot">👤 {{ session.get('usuario', 'Admin') }}<br><br><a href="/logout" style="color:var(--red);">🚪 Salir</a></div>
    </div>
    <div class="right">
        <div class="topbar">
            <button class="btn-top" onclick="document.documentElement.setAttribute('data-theme', document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark')">☀️ / 🌙</button>
            <div style="color:var(--green); font-size:0.85em; font-weight:bold;">● SaaS En Línea</div>
        </div>
        <div class="main">{{ content | safe }}</div>
    </div>
</body></html>
"""

# C) CONTENIDO: DASHBOARD (CALCULADORA Y TABLA)
HTML_RESUMEN = """
<h1 class="title">Resumen de Operaciones</h1><div class="sub">Estado Global de la Flota</div>
<div class="card stat grid g-11" style="padding:0; border:none; background:transparent;">
    <div class="card stat"><div class="l">MINEROS</div><div class="v c-blue">{{ totales }}</div></div>
    <div class="card stat"><div class="l">EN LÍNEA</div><div class="v c-green">{{ en_linea }}</div></div>
    <div class="card stat"><div class="l">OFFLINE</div><div class="v c-red">{{ offline }}</div></div>
    <div class="card stat"><div class="l">GRANJAS</div><div class="v c-blue">1</div></div>
    <div class="card stat"><div class="l">ALERTAS</div><div class="v c-green">0</div></div>
    <div class="card stat"><div class="l">HASH TOTAL</div><div class="v c-blue">{{ flota.hashrate_total }} TH/s</div></div>
    <div class="card stat"><div class="l">CONSUMO</div><div class="v c-blue">{{ flota.consumo_kw }} kW</div></div>
    <div class="card stat"><div class="l">TEMP MÁX</div><div class="v c-red">{{ flota.temp_max }} °C</div></div>
    <div class="card stat"><div class="l">PRECIO BTC</div><div class="v">USD {{ flota.precio_btc }}</div></div>
    <div class="card stat"><div class="l">INGRESO/DÍA</div><div class="v c-green">{{ flota.ingreso_dia }} USD</div></div>
    <div class="card stat"><div class="l">GANANCIA/DÍA</div><div class="v c-green">{{ flota.ganancia_dia }}</div></div>
</div>

<div class="card">
    <h3 style="margin-top:0;">₿ Calculadora de Rentabilidad</h3>
    <div class="grid g-4" style="margin-bottom:15px;">
        <div><label class="sub">Tasa de hash (TH/s)</label><input type="number" id="h" value="{{ flota.hashrate_total }}"></div>
        <div><label class="sub">Consumo (W)</label><input type="number" id="c" value="{{ flota.consumo_w_total }}"></div>
        <div><label class="sub">Luz (USD/kWh)</label><input type="number" id="k" step="0.01" value="0.04"></div>
        <div><label class="sub">Pool (%)</label><input type="number" id="p" step="0.1" value="0.0"></div>
    </div>
    <button class="btn" onclick="calc()">Actualizar Proyección</button>
    <div class="grid g-4" style="margin-top:20px; border-top:1px solid var(--border); padding-top:15px;">
        <div class="stat"><div class="l">BTC/DÍA</div><div class="v c-blue" id="r1">0.00</div></div>
        <div class="stat"><div class="l">INGRESO/DÍA</div><div class="v" id="r2">0.00 USD</div></div>
        <div class="stat"><div class="l">COSTE LUZ/DÍA</div><div class="v c-red" id="r3">0.00 USD</div></div>
        <div class="stat"><div class="l">GANANCIA/MES</div><div class="v c-green" id="r4">0.00 USD</div></div>
    </div>
</div>

<div class="card" style="padding:0; overflow:hidden;">
    <table>
        <tr><th>MINERO (IP)</th><th>ESTADO</th><th>HASH</th><th>TEMP</th><th>CONSUMO</th><th>ACCIÓN</th></tr>
        {% for f in registros %}
        <tr class="f-min" data-h="{{ f.hashrate }}" data-c="{{ f.consumo_w }}">
            <td><a href="/minero/{{ f.ip }}" class="link-ip">{{ f.ip }}</a><br><span class="sub">{{ f.modelo }}</span></td>
            <td>{{ f.estado | safe }}</td>
            <td>{{ f.hashrate }} TH/s</td>
            <td class="{{ 'c-red' if f.temp_num > 80 else 'c-blue' }}">{{ f.temp_num }} °C</td>
            <td>{{ f.consumo_kw }} kW</td>
            <td>
                <button class="btn-out" onclick="if(confirm('¿Reiniciar {{ f.ip }}?')) window.location.href='/cmd/{{ f.ip }}/restart'">🔄 Reiniciar</button>
                <button class="btn-out" onclick="window.location.href='/cmd/{{ f.ip }}/sleep'">😴 Reposo</button>
            </td>
        </tr>
        {% endfor %}
    </table>
</div>
<script>
const PB = {{ flota.precio_btc_raw }}, DIF = {{ flota.dificultad_t }};
function calc() {
    let h = parseFloat(document.getElementById('h').value)||0, c = parseFloat(document.getElementById('c').value)||0;
    let k = parseFloat(document.getElementById('k').value)||0, p = parseFloat(document.getElementById('p').value)||0;
    let btcDia = DIF>0 ? (h*3.125*86400)/(DIF*4294967296) : 0;
    let inB = btcDia*PB, inN = inB-(inB*(p/100)), el = (c*24/1000)*k, gan = inN-el;
    document.getElementById('r1').innerText = btcDia.toFixed(6);
    document.getElementById('r2').innerText = inN.toFixed(2) + ' USD';
    document.getElementById('r3').innerText = el.toFixed(2) + ' USD';
    document.getElementById('r4').innerText = (gan*30).toFixed(2) + ' USD';
}
window.onload = calc;
setTimeout(() => window.location.reload(), 60000);
</script>
"""

# D) CONTENIDO: DETALLE DE MINERO CON GRÁFICO
HTML_DETALLE = """
<div style="display:flex; align-items:center; gap:15px; margin-bottom:20px;">
    <a href="/" style="font-size:1.5em; font-weight:bold; color:var(--text-m);">← Volver</a>
    <div><h1 class="title" style="margin:0;">IP: {{ ip }}</h1><div class="sub">{{ modelo }}</div></div>
</div>
<div class="grid g-4">
    <div class="card stat"><div class="l">TASA DE HASH</div><div class="v c-blue">{{ ultimo.hashrate }} TH/s</div></div>
    <div class="card stat"><div class="l">TEMP MÁX</div><div class="v c-red">{{ ultimo.temp_max }} °C</div></div>
    <div class="card stat"><div class="l">CONSUMO</div><div class="v c-green">{{ ultimo.consumo }} W</div></div>
    <div class="card stat"><div class="l">EFICIENCIA</div><div class="v">{{ disp.eficiencia }} J/TH</div></div>
</div>
<div class="card" style="padding:15px; display:flex; gap:10px;">
    <span style="font-weight:bold; margin-right:15px; display:flex; align-items:center;">⚡ Comandos Remotos:</span>
    <button class="btn-out" onclick="window.location.href='/cmd/{{ ip }}/restart'">🔄 Reiniciar</button>
    <button class="btn-out" onclick="window.location.href='/cmd/{{ ip }}/sleep'">😴 Modo Reposo</button>
    <button class="btn-out" onclick="window.location.href='/cmd/{{ ip }}/normal'">▶ Modo Normal</button>
</div>
<div class="card" style="height:300px;"><canvas id="chart"></canvas></div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
    new Chart(document.getElementById('chart'), { type:'line', data:{ labels:{{ fechas|safe }}, datasets:[
        {label:'Hashrate (TH/s)', data:{{ hrs|safe }}, borderColor:'#3366ff', tension:0.3},
        {label:'Temperatura (°C)', data:{{ tmps|safe }}, borderColor:'#ff5252', tension:0.3, yAxisID:'y1'}
    ]}, options:{ responsive:true, maintainAspectRatio:false, scales:{ y1:{ position:'right' } } }});
</script>
"""

# E) CONTENIDO: PÁGINAS ADMINISTRATIVAS
HTML_GRANJAS = """
<h1 class="title">Gestión de Granjas</h1><div class="sub">Organiza tus mineros por ubicación física</div>
<div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center;">
        <div><h3 style="margin:0;">📍 Granja Principal (Santa Cruz)</h3><p class="sub" style="margin-top:5px;">Mineros Asignados: {{ totales }}</p></div>
        <button class="btn-out">Configurar Red</button>
    </div>
    <hr style="border:0; border-top:1px solid var(--border); margin:15px 0;">
    <div class="grid g-3">
        <div class="stat"><div class="l">Tasa de Hash</div><div class="v c-blue">{{ flota.hashrate_total }} TH/s</div></div>
        <div class="stat"><div class="l">Consumo Total</div><div class="v">{{ flota.consumo_kw }} kW</div></div>
        <div class="stat"><div class="l">Estado</div><div class="v c-green">Activa</div></div>
    </div>
</div>
<button class="btn">+ Añadir Nueva Granja</button>
"""

HTML_ALERTAS = """
<h1 class="title">Centro de Alertas</h1><div class="sub">Configura notificaciones automáticas para tu celular</div>
<div class="grid g-2">
    <div class="card">
        <h3 style="margin-top:0;">🤖 Bot de Telegram</h3>
        <p class="sub">Recibe mensajes al instante cuando un minero falle.</p>
        <label class="sub">Telegram Bot Token</label><input type="text" placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"><br><br>
        <label class="sub">Tu Chat ID</label><input type="text" placeholder="987654321"><br><br>
        <button class="btn">Conectar Telegram</button>
    </div>
    <div class="card">
        <h3 style="margin-top:0;">⚠️ Reglas de Alerta</h3>
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:15px;">
            <input type="checkbox" checked style="width:auto;"> <span>Minero desconectado (Offline > 5 min)</span>
        </div>
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:15px;">
            <input type="checkbox" checked style="width:auto;"> <span>Temperatura de chip crítica (> 85°C)</span>
        </div>
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:15px;">
            <input type="checkbox" checked style="width:auto;"> <span>Tasa de hash cae por debajo del 20%</span>
        </div>
        <button class="btn-out">Guardar Reglas</button>
    </div>
</div>
"""

HTML_PLAN = """
<h1 class="title">Suscripciones y Facturación</h1><div class="sub">Amplía la capacidad de tu panel SaaS</div>
<div class="grid g-3">
    <div class="card" style="text-align:center;">
        <h2 style="margin:0;">Starter</h2><div class="v c-blue" style="font-size:2em; margin:10px 0;">$0 <span style="font-size:0.4em;color:var(--text-m);">/mes</span></div>
        <p class="sub">Hasta 5 mineros</p><hr style="border-color:var(--border);"><p class="sub">Soporte Comunitario</p>
        <button class="btn-out" style="width:100%; margin-top:15px;" disabled>Plan Actual</button>
    </div>
    <div class="card" style="text-align:center; border-color:var(--blue);">
        <h2 style="margin:0;">Pro</h2><div class="v c-blue" style="font-size:2em; margin:10px 0;">$29 <span style="font-size:0.4em;color:var(--text-m);">/mes</span></div>
        <p class="sub">Hasta 50 mineros</p><hr style="border-color:var(--border);"><p class="sub">Alertas Telegram & API</p>
        <button class="btn" style="width:100%; margin-top:15px;">Actualizar a Pro</button>
    </div>
    <div class="card" style="text-align:center;">
        <h2 style="margin:0;">Farm</h2><div class="v c-blue" style="font-size:2em; margin:10px 0;">$99 <span style="font-size:0.4em;color:var(--text-m);">/mes</span></div>
        <p class="sub">Mineros Ilimitados</p><hr style="border-color:var(--border);"><p class="sub">Soporte Dedicado 24/7</p>
        <button class="btn-out" style="width:100%; margin-top:15px;">Contactar Ventas</button>
    </div>
</div>
"""

HTML_AJUSTES = """
<h1 class="title">Ajustes del Sistema</h1><div class="sub">Configuración de cuenta y seguridad API</div>
<div class="grid g-2">
    <div class="card">
        <h3 style="margin-top:0;">Seguridad y Contraseña</h3>
        <label class="sub">Usuario Master</label><input type="text" value="{{ session['usuario'] }}" disabled><br><br>
        <label class="sub">Nueva Contraseña</label><input type="password" placeholder="Escribe para cambiar"><br><br>
        <button class="btn">Actualizar Credenciales</button>
    </div>
    <div class="card">
        <h3 style="margin-top:0;">API Key (Para el Agente)</h3>
        <p class="sub">Esta clave es la que debes poner en tu archivo agente.py para que pueda enviar datos de forma segura a tu nube.</p>
        <input type="text" value="SaaS-Key-{{ session['usuario'] }}-9x8Q2" disabled><br><br>
        <button class="btn-out">Generar Nueva Clave</button>
    </div>
</div>
"""

def render_page(active, content_html, **kwargs):
    page_content = render_template_string(content_html, **kwargs)
    return render_template_string(HTML_LAYOUT, active_page=active, content=page_content, **kwargs)

# --- 3. RUTAS FLASK ---
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

def get_flota_data():
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    mineros = cur.execute("SELECT * FROM mineros_activos").fetchall(); con.close()
    datos_red = obtener_datos_red()
    registros, en_linea, offline, ht, cw, tm, ig = [], 0, 0, 0.0, 0.0, 0, 0.0
    for m in mineros:
        ip, mod, hr, t, c, est, ult = m
        if (datetime.now() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds() < 180:
            en_linea += 1; ht += hr; cw += c; tm = max(tm, t)
            b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296) if datos_red['dificultad_t'] > 0 else 0
            ig += b * datos_red['precio']
            registros.append({'ip': ip, 'modelo': mod, 'estado': '<span style="color:var(--green);">● En línea</span>', 'hashrate': hr, 'temp_num': t, 'consumo_w': c, 'consumo_kw': round(c/1000, 1)})
        else:
            offline += 1; registros.append({'ip': ip, 'modelo': mod, 'estado': '<span style="color:var(--red);">● Offline</span>', 'hashrate': 0, 'temp_num': 0, 'consumo_w': 0, 'consumo_kw': 0})
    return registros, len(mineros), en_linea, offline, {'hashrate_total': round(ht, 1), 'consumo_w_total': round(cw, 0), 'consumo_kw': round(cw/1000, 1), 'temp_max': tm, 'precio_btc_raw': datos_red['precio'], 'precio_btc': f"{datos_red['precio']:,.2f}", 'dificultad_t': round(datos_red['dificultad_t'], 2), 'ingreso_dia': f"{ig:.2f}", 'ganancia_dia': f"{ig:.2f} USD"}

@app.route('/')
@login_required
def index():
    reg, tot, onl, off, fl = get_flota_data()
    return render_page('resumen', HTML_RESUMEN, registros=reg, totales=tot, en_linea=onl, offline=off, flota=fl)

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
    dt = {'hashrate': m[2], 'temp_max': m[3], 'consumo': m[4]}
    disp = {'eficiencia': round(m[4]/m[2], 2) if m[2] > 0 else 0}
    return render_page('resumen', HTML_DETALLE, ip=ip, modelo=m[1], ultimo=dt, disp=disp, fechas=json.dumps(fechas), hrs=json.dumps(hrs), tmps=json.dumps(tmps))

@app.route('/granjas')
@login_required
def granjas():
    reg, tot, onl, off, fl = get_flota_data()
    return render_page('granjas', HTML_GRANJAS, totales=tot, flota=fl)

@app.route('/alertas')
@login_required
def alertas(): return render_page('alertas', HTML_ALERTAS)

@app.route('/plan')
@login_required
def plan(): return render_page('plan', HTML_PLAN)

@app.route('/ajustes')
@login_required
def ajustes(): return render_page('ajustes', HTML_AJUSTES)

@app.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    cmd = "restart" if accion == 'restart' else "pause" if accion == 'sleep' else "resume" if accion == 'normal' else None
    if cmd:
        con = sqlite3.connect('historial_minero.db')
        con.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (?, ?, 'pendiente', ?)", (ip, cmd, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit(); con.close()
    return redirect('/')

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json
    if not datos: return "Error", 400
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for m in datos.get('mineros', []):
        cur.execute("INSERT INTO mineros_activos (ip, modelo, hashrate, temp_max, consumo_w, estado, ultima_act) VALUES (?, ?, ?, ?, ?, 'online', ?) ON CONFLICT(ip) DO UPDATE SET hashrate=excluded.hashrate, temp_max=excluded.temp_max, consumo_w=excluded.consumo_w, ultima_act=excluded.ultima_act", (m['ip'], m['modelo'], m['hashrate'], m['temp'], m['consumo'], ahora))
        cur.execute("INSERT INTO historial_nube (ip, hashrate, temp_max, fecha) VALUES (?, ?, ?, ?)", (m['ip'], m['hashrate'], m['temp'], ahora))
    
    pendientes = cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente'").fetchall()
    ordenes = [{"ip": c[1], "comando": c[2]} for c in pendientes]
    for c in pendientes: cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=?", (c[0],))
    
    con.commit(); con.close()
    return jsonify({"status": "ok", "ordenes": ordenes}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
