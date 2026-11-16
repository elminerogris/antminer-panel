from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify
from functools import wraps
import sqlite3, json, urllib.request, time, os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

# --- INICIALIZACIÓN DE BD ---
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

# =====================================================================
# PEGA AQUÍ TUS 3 VARIABLES GIGANTES DE HTML QUE YA TIENES:
# HTML_LOGIN = """..."""
# HTML_PRINCIPAL = """..."""
# HTML_DETALLE = """..."""
# (No las copio aquí para que el mensaje no se corte, usa las del paso anterior)
# =====================================================================

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
    # En la nube, leemos los mineros que el agente Android nos haya enviado
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
            registros.append({'ip': ip, 'nombre': ip[-3:], 'modelo': modelo, 'estado': '<span style="color:var(--accent-green);">● En línea</span>', 'hashrate': hr, 'temp_num': tm, 'consumo_kw': round(cw/1000, 1), 'eficiencia': round(cw/hr, 2) if hr>0 else 0, 'ganancia_neta': str_gan})
        else:
            offline += 1
            registros.append({'ip': ip, 'nombre': ip[-3:], 'modelo': modelo, 'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>', 'hashrate': 0, 'temp_num': 0, 'consumo_kw': 0, 'eficiencia': 0, 'ganancia_neta': "0.00 USD"})
            
    gn_flota = ingreso_dia - 0.0
    datos_flota = {
        'hashrate_total': round(hashrate_flota, 1), 'consumo_w_total': round(consumo_w_flota, 0), 'consumo_kw': round(consumo_w_flota/1000, 1),
        'temp_max': temp_max_flota, 'precio_btc_raw': datos_red['precio'], 'precio_btc': f"{datos_red['precio']:,.2f}".replace(',', '.'), 
        'dificultad_t': round(datos_red['dificultad_t'], 2), 'recompensa_bloque': datos_red['recompensa'],
        'ingreso_dia': f"{ingreso_dia:.2f}", 'ganancia_dia': f"-USD {abs(gn_flota):.2f}" if gn_flota < 0 else f"{gn_flota:.2f} USD"
    }
    return render_template_string(HTML_PRINCIPAL, registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=datos_flota)

# --- ESTA ES LA NUEVA PUERTA (API) PARA EL AGENTE ANDROID ---
@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json
    if not datos: return "Error", 400
    
    con = sqlite3.connect('historial_minero.db')
    cur = con.cursor()
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