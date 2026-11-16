from flask import Flask, render_template, redirect, request, session, url_for, jsonify, Response
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
        if not c.fetchone(): 
            c.execute("INSERT INTO usuarios (username, password) VALUES ('giovanni', 'admin123')")
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
    if ahora - cache_red['ultima_act'] < 60: 
        return cache_red['datos']
    
    datos = cache_red['datos'].copy()
    try:
        req_p = urllib.request.Request("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers={'User-Agent': 'Mozilla/5.0'})
        datos['precio'] = float(json.loads(urllib.request.urlopen(req_p, timeout=2).read().decode('utf-8'))['price'])
        req_d = urllib.request.Request("https://mempool.space/api/v1/difficulty-adjustment", headers={'User-Agent': 'Mozilla/5.0'})
        datos['dificultad_t'] = float(json.loads(urllib.request.urlopen(req_d, timeout=2).read().decode('utf-8'))['difficulty']) / 1_000_000_000_000
    except: 
        pass
    
    cache_red['datos'] = datos
    cache_red['ultima_act'] = ahora
    return datos

# --- RUTAS DE LA APLICACIÓN ---

@app.route('/')
@login_required
def index():
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos")
    mineros = cur.fetchall()
    con.close()
    
    datos_red = obtener_datos_red()
    registros = []
    en_linea = 0
    offline = 0
    ht = 0.0
    cw = 0.0
    tm = 0
    ig = 0.0
    
    for m in mineros:
        ip = m['ip']
        mod = m['modelo']
        ult = m['ultima_act'] if m['ultima_act'] else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        hr = float(m['hashrate']) if m['hashrate'] else 0.0
        t = int(m['temp_max']) if m['temp_max'] else 0
        c = float(m['consumo_w']) if m['consumo_w'] else 0.0
        
        segs_desconectado = (datetime.utcnow() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds()
        
        if segs_desconectado < 180:
            en_linea += 1
            ht += hr
            cw += c
            tm = max(tm, t)
            b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296) if datos_red['dificultad_t'] > 0 else 0
            ib = b * datos_red['precio']
            ig += ib
            str_gan = f"-USD {abs(ib):.2f}" if ib < 0 else f"{ib:.2f} USD"
            registros.append({'ip': ip, 'modelo': mod, 'estado': '<span style="color:var(--accent-green);">● En línea</span>', 'tiempo_act': f'hace {int(segs_desconectado)}s', 'hashrate': hr, 'temp_num': t, 'consumo_kw': round(c/1000, 1), 'eficiencia': round(c/hr, 2) if hr>0 else 0, 'ganancia_neta': str_gan})
        else:
            offline += 1
            registros.append({'ip': ip, 'modelo': mod, 'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>', 'tiempo_act': 'Offline', 'hashrate': 0, 'temp_num': 0, 'consumo_w': 0, 'consumo_kw': 0, 'eficiencia': 0, 'ganancia_neta': "USD 0.00"})
            
    flota = {'hashrate_total': round(ht, 1), 'consumo_w_total': round(cw, 0), 'consumo_kw': round(cw/1000, 1), 'temp_max': tm, 'precio_btc_raw': datos_red['precio'], 'precio_btc': f"{datos_red['precio']:,.2f}", 'dificultad_t': round(datos_red['dificultad_t'], 2), 'recompensa_bloque': datos_red['recompensa'], 'red_ehs': datos_red['red_ehs'], 'ingreso_dia': f"{ig:.2f}", 'ganancia_dia': f"{ig:.2f} USD"}
    
    return render_template('resumen.html', active_page='resumen', registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=flota)

@app.route('/minero/<ip>')
@login_required
def detalle_minero(ip):
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos WHERE ip=%s", (ip,))
    m = cur.fetchone()
    cur.execute("SELECT fecha, hashrate, temp_max FROM historial_nube WHERE ip=%s ORDER BY id DESC LIMIT 5000", (ip,))
    hist = cur.fetchall()
    con.close()
    
    if not m: return redirect('/')
    
    hr_val = float(m['hashrate']) if m['hashrate'] else 0.0
    t_val = int(m['temp_max']) if m['temp_max'] else 0
    c_val = float(m['consumo_w']) if m['consumo_w'] else 0.0
    ult = m['ultima_act'] if m['ultima_act'] else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    offline = (datetime.utcnow() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds() > 180
    
    pool1 = str(m['pool1']) if m['pool1'] else ""
    worker1 = str(m['worker1']) if m['worker1'] else ""
    pool2 = str(m['pool2']) if m['pool2'] else ""
    worker2 = str(m['worker2']) if m['worker2'] else ""
    pool3 = str(m['pool3']) if m['pool3'] else ""
    worker3 = str(m['worker3']) if m['worker3'] else ""
    
    mac = str(m['mac']) if m['mac'] else "-"
    serial = str(m['serial']) if m['serial'] else "-"
    fw = str(m['firmware']) if m['firmware'] else "Desconocido"
    fans = str(m['fans']) if m['fans'] else "0 / 0"
    
    cadenas_str = m['cadenas'] if m['cadenas'] else "[]"
    try: 
        cadenas = json.loads(cadenas_str)
    except: 
        cadenas = []
        
    if not cadenas: 
        cadenas = [{"id": 0, "temp_c": t_val, "temp_p
