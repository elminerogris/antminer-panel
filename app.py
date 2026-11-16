from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify
from functools import wraps
import sqlite3, json, urllib.request, time, os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

# --- 1. BASE DE DATOS (AHORA CON COLA DE COMANDOS) ---
def init_db():
    conexion = sqlite3.connect('historial_minero.db')
    cursor = conexion.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS cola_comandos (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, comando TEXT, estado TEXT, fecha TEXT)')
    
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

# (Tus funciones de caché de Binance van aquí igual que antes)
cache_red = {'datos': {'precio': 68792.0, 'dificultad_t': 148.36, 'recompensa': 3.125}, 'ultima_act': 0}
def obtener_datos_red():
    global cache_red
    ahora = time.time()
    if ahora - cache_red['ultima_act'] < 300: return cache_red['datos']
    datos = cache_red['datos']
    try:
        req_p = urllib.request.Request("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers={'User-Agent': 'Mozilla/5.0'})
        datos['precio'] = float(json.loads(urllib.request.urlopen(req_p, timeout=2).read().decode('utf-8'))['price'])
    except: pass
    cache_red['datos'] = datos; cache_red['ultima_act'] = ahora
    return datos

# =====================================================================
# AQUÍ VAN TUS VARIABLES HTML_LOGIN, HTML_PRINCIPAL, HTML_DETALLE
# =====================================================================

# Plantilla genérica para las subpáginas en construcción
HTML_SUBPAGINA = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>Antminer SaaS - {{ titulo }}</title>
<style>
    :root { --bg-body: #14151f; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --bg-card: #1b1d29; --border-color: #292a38; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); padding: 40px; display: flex; flex-direction: column; align-items: center; }
    .card { background: var(--bg-card); border: 1px solid var(--border-color); padding: 40px; border-radius: 8px; text-align: center; max-width: 500px; width: 100%; }
    .btn { background: var(--accent-blue); color: white; text-decoration: none; padding: 10px 20px; border-radius: 4px; display: inline-block; margin-top: 20px; font-weight: bold; }
</style></head>
<body>
    <div class="card">
        <h1 style="margin-top:0;">{{ titulo }}</h1>
        <p style="color: var(--text-muted);">Esta sección está en desarrollo para la Fase 3 del SaaS. Aquí podrás gestionar configuraciones avanzadas, usuarios y alertas automáticas por Telegram.</p>
        <a href="/" class="btn">← Volver al Panel Principal</a>
    </div>
</body></html>
"""

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
    registros, en_linea, offline, hashrate_flota, consumo_w_flota = [], 0, 0, 0.0, 0.0
    
    for m in mineros:
        ip, modelo, hr, tm, cw, estado, ultima_act = m
        if (datetime.now() - datetime.strptime(ultima_act, "%Y-%m-%d %H:%M:%S")).total_seconds() < 180:
            en_linea += 1; hashrate_flota += hr; consumo_w_flota += cw
            registros.append({'ip': ip, 'modelo': modelo, 'estado': '<span style="color:#00e676;">● En línea</span>', 'hashrate': hr, 'temp_num': tm, 'consumo_kw': round(cw/1000, 1), 'eficiencia': round(cw/hr, 2) if hr>0 else 0})
        else:
            offline += 1
            registros.append({'ip': ip, 'modelo': modelo, 'estado': '<span style="color:#ff5252;">● Sin conexión</span>', 'hashrate': 0, 'temp_num': 0, 'consumo_kw': 0, 'eficiencia': 0})
            
    return render_template_string(HTML_PRINCIPAL, registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota={'hashrate_total': round(hashrate_flota, 1), 'consumo_kw': round(consumo_w_flota/1000, 1), 'precio_btc': f"{datos_red['precio']:,.2f}", 'ingreso_dia': "0.00"})

# --- 2. RUTAS PARA LAS SUBPÁGINAS DEL MENÚ ---
@app.route('/granjas')
@login_required
def granjas(): return render_template_string(HTML_SUBPAGINA, titulo="🏭 Gestión de Granjas")

@app.route('/mineros')
@login_required
def mineros(): return redirect('/') # El resumen ya actúa como vista de mineros

@app.route('/alertas')
@login_required
def alertas(): return render_template_string(HTML_SUBPAGINA, titulo="🔔 Centro de Alertas")

@app.route('/plan')
@login_required
def plan(): return render_template_string(HTML_SUBPAGINA, titulo="📋 Planes y Facturación")

@app.route('/ajustes')
@login_required
def ajustes(): return render_template_string(HTML_SUBPAGINA, titulo="⚙️ Ajustes del Sistema")

# --- 3. ALMACENAR COMANDOS EN LA NUBE ---
@app.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    comando_real = "restart" if accion == 'restart' else "pause" if accion == 'sleep' else "resume" if accion == 'normal' else None
    if comando_real:
        con = sqlite3.connect('historial_minero.db')
        con.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (?, ?, 'pendiente', ?)", 
                    (ip, comando_real, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit(); con.close()
    # Volver a la página principal tras enviar el comando
    return redirect('/')

# --- 4. LA PUERTA API (AHORA ENTREGA ÓRDENES AL AGENTE) ---
@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json
    if not datos: return "Error", 400
    con = sqlite3.connect('historial_minero.db'); cur = con.cursor()
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # A. Guardar los datos frescos que llegaron del Android
    for minero in datos.get('mineros', []):
        cur.execute('''INSERT INTO mineros_activos (ip, modelo, hashrate, temp_max, consumo_w, estado, ultima_act) 
                       VALUES (?, ?, ?, ?, ?, 'online', ?) 
                       ON CONFLICT(ip) DO UPDATE SET 
                       hashrate=excluded.hashrate, temp_max=excluded.temp_max, consumo_w=excluded.consumo_w, ultima_act=excluded.ultima_act''', 
                    (minero['ip'], minero['modelo'], minero['hashrate'], minero['temp'], minero['consumo'], ahora))
    
    # B. Revisar si el dueño apretó algún botón en su iPhone recientemente
    comandos_pendientes = cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente'").fetchall()
    lista_ordenes = []
    for cmd in comandos_pendientes:
        lista_ordenes.append({"ip": cmd[1], "comando": cmd[2]})
        cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=?", (cmd[0],))
    
    con.commit(); con.close()
    
    # C. Responderle al Android dándole las órdenes que debe ejecutar
    return jsonify({"status": "ok", "ordenes": lista_ordenes}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
