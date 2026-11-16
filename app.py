from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify
from functools import wraps
import sqlite3, json, urllib.request, time, os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

def init_db():
    conexion = sqlite3.connect('historial_minero.db')
    cursor = conexion.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
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

cache_red = {'datos': {'precio': 68792.0, 'dificultad_t': 148.36, 'recompensa': 3.125}, 'ultima_act': 0}
def obtener_datos_red():
    global cache_red
    ahora = time.time()
    if ahora - cache_red['ultima_act'] < 300: return cache_red['datos']
    datos = cache_red['datos']
    try:
        req_p = urllib.request.Request("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers={'User-Agent': 'Mozilla/5.0'})
        datos['precio'] = float(json.loads(urllib.request.urlopen(req_p, timeout=2).read().decode('utf-8'))['price'])
        req_d = urllib.request.Request("https://mempool.space/api/v1/difficulty-adjustment", headers={'User-Agent': 'Mozilla/5.0'})
        datos['dificultad_t'] = float(json.loads(urllib.request.urlopen(req_d, timeout=2).read().decode('utf-8'))['difficulty']) / 1_000_000_000_000
    except: pass
    cache_red['datos'] = datos; cache_red['ultima_act'] = ahora
    return datos

HTML_LOGIN = """
<!DOCTYPE html>
<html lang="es" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>Antminer Tools - Acceso</title>
    <style>
        :root { --bg-body: #14151f; --bg-card: #1b1d29; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --accent-red: #ff5252; }
        body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-card { background: var(--bg-card); padding: 40px 30px; border-radius: 8px; border: 1px solid var(--border-color); width: 100%; max-width: 320px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        .logo-container { font-weight: bold; font-size: 1.4em; display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 30px; }
        .logo-icon { background: var(--accent-blue); color: white; width: 32px; height: 32px; border-radius: 6px; display: flex; justify-content: center; align-items: center; font-size: 0.9em; }
        .logo-text span { color: var(--accent-blue); font-weight: normal; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; font-size: 0.85em; color: var(--text-muted); margin-bottom: 8px; }
        .form-group input { width: 100%; background: var(--bg-body); border: 1px solid var(--border-color); color: var(--text-main); padding: 12px; border-radius: 4px; font-size: 1em; box-sizing: border-box; transition: 0.3s; }
        .form-group input:focus { border-color: var(--accent-blue); outline: none; }
        .btn-login { width: 100%; background: var(--accent-blue); color: white; border: none; padding: 12px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.2s; font-size: 1em; margin-top: 10px; }
        .btn-login:hover { background: #254bce; }
        .error-msg { background: rgba(255, 82, 82, 0.1); border: 1px solid var(--accent-red); color: var(--accent-red); padding: 10px; border-radius: 4px; font-size: 0.85em; text-align: center; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo-container"><div class="logo-icon">A</div><div class="logo-text">Antminer <span>SaaS</span></div></div>
        {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
        <form method="POST">
            <div class="form-group"><label>Usuario</label><input type="text" name="username" required autocomplete="off"></div>
            <div class="form-group"><label>Contraseña</label><input type="password" name="password" required></div>
            <button type="submit" class="btn-login">Ingresar al Panel</button>
        </form>
    </div>
</body>
</html>
"""

HTML_PRINCIPAL = """
<!DOCTYPE html>
<html lang="es" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>Antminer SaaS - Resumen</title>
    <script type="text/javascript" src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
    <style>
        :root[data-theme="dark"] { --bg-body: #14151f; --bg-sidebar: #191a24; --bg-card: #1b1d29; --bg-input: #14151f; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --text-sub: #8b8d9b; --accent-blue: #3366ff; --accent-green: #00e676; --accent-red: #ff5252; --hover-bg: #202230; --active-bg: #232533; }
        :root[data-theme="light"] { --bg-body: #f4f5f7; --bg-sidebar: #ffffff; --bg-card: #ffffff; --bg-input: #f4f5f7; --border-color: #e2e8f0; --text-main: #1e293b; --text-muted: #64748b; --text-sub: #94a3b8; --accent-blue: #2563eb; --accent-green: #10b981; --accent-red: #ef4444; --hover-bg: #f8fafc; --active-bg: #f1f5f9; }
        body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; display: flex; height: 100vh; overflow: hidden; transition: 0.3s; top: 0 !important; }
        .skiptranslate iframe { display: none !important; }
        body { top: 0px !important; }
        #google_translate_element { display: none; }
        a { text-decoration: none; color: inherit; }
        
        .sidebar { width: 220px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding-top: 15px; z-index: 10; transition: 0.3s; }
        .logo-container { padding: 10px 20px 20px; font-weight: bold; font-size: 1.1em; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border-color); }
        .logo-icon { background: var(--accent-blue); color: white; width: 24px; height: 24px; border-radius: 4px; display: flex; justify-content: center; align-items: center; font-size: 0.8em; }
        .logo-text span { color: var(--accent-blue); font-weight: normal; }
        .menu { list-style: none; padding: 15px 0; margin: 0; flex: 1; }
        .menu li { padding: 12px 20px; font-size: 0.9em; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; gap: 12px; transition: 0.2s; }
        .menu li:hover { background: var(--hover-bg); color: var(--text-main); }
        .menu li.active { background: var(--active-bg); color: var(--text-main); border-left: 3px solid var(--accent-blue); }
        
        .sidebar-bottom { border-top: 1px solid var(--border-color); padding: 15px 20px; font-size: 0.85em; display: flex; flex-direction: column; gap: 10px; }
        .user-info { color: var(--text-main); font-weight: bold; display: flex; align-items: center; gap: 8px; }
        .btn-logout { color: var(--accent-red); display: flex; align-items: center; gap: 8px; cursor: pointer; transition: 0.2s; }
        
        .right-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .top-bar { height: 60px; background: var(--bg-body); border-bottom: 1px solid var(--border-color); display: flex; justify-content: flex-end; align-items: center; padding: 0 30px; gap: 15px; transition: 0.3s; }
        .top-btn, .top-lang-select { background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-main); height: 36px; border-radius: 6px; cursor: pointer; transition: 0.2s; font-size: 0.85em; font-weight: bold; display: flex; align-items: center; justify-content: center; }
        .top-btn { width: 36px; font-size: 1.1em; color: var(--text-muted); }
        .top-lang-select { padding: 0 10px; outline: none; }
        .top-status { display: flex; align-items: center; gap: 15px; font-size: 0.85em; color: var(--text-muted); font-weight: bold; margin-left: 10px; }
        .status-envivo { color: var(--accent-green); display: flex; align-items: center; gap: 6px; }
        .dot-pulse { width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0.7); } 70% { box-shadow: 0 0 0 6px rgba(0, 230, 118, 0); } 100% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0); } }

        .main-content { flex: 1; padding: 20px 30px; overflow-y: auto; }
        .cabecera h1 { margin: 0 0 5px 0; font-size: 1.8em; }
        .cabecera .sub { font-size: 0.85em; color: var(--text-sub); margin-bottom: 20px;}
        .cabecera .sub span { color: var(--accent-green); }
        
        .top-stats-grid { display: grid; grid-template-columns: repeat(11, 1fr); gap: 10px; margin-bottom: 25px; }
        .stat-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px 10px; display: flex; flex-direction: column; justify-content: center; }
        .stat-card .lbl { font-size: 0.65em; color: var(--text-muted); text-transform: uppercase; font-weight: bold; margin-bottom: 6px; display: flex; justify-content: space-between; }
        .stat-card .val { font-size: 1.2em; font-weight: bold; color: var(--text-main); }
        .val-blue { color: var(--accent-blue) !important; }
        .val-green { color: var(--accent-green) !important; }
        .val-red { color: var(--accent-red) !important; }
        .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
        .dot-green { background: var(--accent-green); }
        .dot-red { background: var(--accent-red); }

        .tabla-container { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px 20px; text-align: left; border-bottom: 1px solid var(--border-color); font-size: 0.85em; }
        th { color: var(--text-muted); text-transform: uppercase; background: var(--bg-card); }
        .nombre-link { color: var(--text-main); font-size: 1.1em; font-weight: bold; }
    </style>
</head>
<body>
    <div id="google_translate_element"></div>

    <div class="sidebar">
        <div class="logo-container"><div class="logo-icon">A</div><div class="logo-text" translate="no">Antminer <span>SaaS</span></div></div>
        <ul class="menu">
            <li class="active">📊 Resumen</li><li>🏭 Granjas</li><li>⛏️ Mineros</li>
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
                <span class="status-envivo"><div class="dot-pulse"></div> En vivo (Nube)</span>
            </div>
        </div>

        <div class="main-content">
            <div class="cabecera">
                <h1>Resumen de Operaciones</h1>
                <div class="sub">Estado de la flota · <span id="textoActualizado">esperando datos...</span></div>
            </div>
            
            <div class="top-stats-grid">
                <div class="stat-card"><div class="lbl">MINEROS ⛏️</div><div class="val val-blue" translate="no">{{ totales }}</div></div>
                <div class="stat-card"><div class="lbl">EN LÍNEA <span class="dot dot-green"></span></div><div class="val val-green" translate="no">{{ en_linea }}</div></div>
                <div class="stat-card"><div class="lbl">SIN CONEXIÓN <span class="dot dot-red"></span></div><div class="val val-red" translate="no">{{ offline }}</div></div>
                <div class="stat-card"><div class="lbl">GRANJAS 🏭</div><div class="val val-blue" translate="no">1</div></div>
                <div class="stat-card"><div class="lbl">TASA DE HASH ⚡</div><div class="val val-blue" translate="no">{{ flota.hashrate_total }} TH/s</div></div>
                <div class="stat-card"><div class="lbl">CONSUMO 🔌</div><div class="val val-blue" translate="no">{{ flota.consumo_kw }} kW</div></div>
                <div class="stat-card"><div class="lbl">PRECIO BTC ₿</div><div class="val" translate="no">USD {{ flota.precio_btc }}</div></div>
                <div class="stat-card"><div class="lbl">INGRESOS / DÍA 💰</div><div class="val val-green" translate="no">{{ flota.ingreso_dia }} USD</div></div>
            </div>

            <div class="tabla-container">
                <table>
                    <tr><th>MINERO (IP)</th><th>ESTADO</th><th>TASA DE HASH</th><th>TEMP</th><th>CONSUMO</th><th>EFICIENCIA</th></tr>
                    {% for f in registros %}
                    <tr>
                        <td><span class="nombre-link" translate="no">{{ f.ip }}</span><br><span style="font-size:0.7em; color:var(--text-sub);" translate="no">{{ f.modelo }}</span></td>
                        <td>{{ f.estado | safe }}</td>
                        <td translate="no">{{ f.hashrate }} TH/s</td>
                        <td style="color: {{ 'var(--accent-red)' if f.temp_num > 80 else 'var(--accent-blue)' }};" translate="no">{{ f.temp_num }} °C</td>
                        <td translate="no">{{ f.consumo_kw }} kW</td>
                        <td translate="no">{{ f.eficiencia }} J/TH</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>
    </div>

    <script>
        function googleTranslateElementInit() { new google.translate.TranslateElement({pageLanguage: 'es', autoDisplay: false}, 'google_translate_element'); }
        function cambiarIdioma(lang) {
            let selectField = document.querySelector(".goog-te-combo");
            if (selectField) { selectField.value = lang; selectField.dispatchEvent(new Event("change")); }
        }
        function actualizarReloj() {
            const ahora = new Date();
            document.getElementById('relojTop').innerText = ahora.getHours().toString().padStart(2, '0') + ':' + ahora.getMinutes().toString().padStart(2, '0');
        }
        setInterval(actualizarReloj, 1000); actualizarReloj();

        function toggleTheme() {
            const html = document.documentElement, icon = document.getElementById('themeIcon');
            if (html.getAttribute('data-theme') === 'dark') { html.setAttribute('data-theme', 'light'); icon.innerText = '🌙'; } 
            else { html.setAttribute('data-theme', 'dark'); icon.innerText = '☀️'; }
        }
        
        // Auto refresco cada 60s para leer datos nuevos de la nube
        setTimeout(() => window.location.reload(), 60000);
    </script>
</body>
</html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        u = request.form.get('username'); p = request.form.get('password')
        con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
        user_db = cur.execute("SELECT id, username FROM usuarios WHERE username=? AND password=?", (u, p)).fetchone()
        con.close()
        if user_db:
            session['usuario'] = user_db[1]; return redirect('/')
        else: error = 'Usuario o contraseña incorrectos'
    return render_template_string(HTML_LOGIN, error=error)

@app.route('/logout')
def logout():
    session.clear(); return redirect('/login')

@app.route('/')
@login_required
def index():
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    mineros = cur.execute("SELECT * FROM mineros_activos").fetchall()
    con.close()
    
    datos_red = obtener_datos_red()
    registros, en_linea, offline, hashrate_flota, consumo_w_flota, temp_max_flota, ingreso_dia = [], 0, 0, 0.0, 0.0, 0, 0.0
    
    for m in mineros:
        ip, modelo, hr, tm, cw, estado, ultima_act = m
        segundos_desde_act = (datetime.now() - datetime.strptime(ultima_act, "%Y-%m-%d %H:%M:%S")).total_seconds()
        
        if segundos_desde_act < 180:
            en_linea += 1; hashrate_flota += hr; consumo_w_flota += cw
            if tm > temp_max_flota: temp_max_flota = tm
            b_dia = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296) if datos_red['dificultad_t'] > 0 else 0
            i_bruto = b_dia * datos_red['precio']; ingreso_dia += i_bruto
            str_gan = f"-USD {abs(i_bruto):.2f}" if i_bruto < 0 else f"{i_bruto:.2f} USD"
            registros.append({'ip': ip, 'modelo': modelo, 'estado': '<span style="color:var(--accent-green);">● En línea</span>', 'hashrate': hr, 'temp_num': tm, 'consumo_kw': round(cw/1000, 1), 'eficiencia': round(cw/hr, 2) if hr>0 else 0})
        else:
            offline += 1
            registros.append({'ip': ip, 'modelo': modelo, 'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>', 'hashrate': 0, 'temp_num': 0, 'consumo_kw': 0, 'eficiencia': 0})
            
    datos_flota = {
        'hashrate_total': round(hashrate_flota, 1), 'consumo_kw': round(consumo_w_flota/1000, 1), 'precio_btc': f"{datos_red['precio']:,.2f}".replace(',', '.'), 'ingreso_dia': f"{ingreso_dia:.2f}"
    }
    return render_template_string(HTML_PRINCIPAL, registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=datos_flota)

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json
    if not datos: return "Error", 400
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for minero in datos.get('mineros', []):
        cur.execute('''INSERT INTO mineros_activos (ip, modelo, hashrate, temp_max, consumo_w, estado, ultima_act) 
                       VALUES (?, ?, ?, ?, ?, 'online', ?) 
                       ON CONFLICT(ip) DO UPDATE SET 
                       hashrate=excluded.hashrate, temp_max=excluded.temp_max, consumo_w=excluded.consumo_w, ultima_act=excluded.ultima_act''', 
                    (minero['ip'], minero['modelo'], minero['hashrate'], minero['temp'], minero['consumo'], ahora))
    con.commit(); con.close()
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
