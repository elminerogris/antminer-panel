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
        c.execute('''CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, hashrate_30m REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT, errores INTEGER, pool1 TEXT, worker1 TEXT, pass1 TEXT, pool2 TEXT, worker2 TEXT, pass2 TEXT, pool3 TEXT, worker3 TEXT, pass3 TEXT, mac TEXT, serial TEXT, firmware TEXT, fans TEXT, cadenas TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, plan TEXT DEFAULT 'Gratis', saldo REAL DEFAULT 0.0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS cola_comandos (id SERIAL PRIMARY KEY, ip TEXT, comando TEXT, estado TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS historial_nube (id SERIAL PRIMARY KEY, ip TEXT, hashrate REAL, temp_max INTEGER, errores INTEGER DEFAULT 0, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nombres_mineros (ip TEXT UNIQUE, nombre TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs_mineros (ip TEXT UNIQUE, contenido TEXT, fecha TEXT)''')
        
        try: c.execute("ALTER TABLE mineros_activos ADD COLUMN hashrate_30m REAL DEFAULT 0.0")
        except: pass
        try: c.execute("ALTER TABLE mineros_activos ADD COLUMN pass1 TEXT DEFAULT '123'")
        except: pass
        try: c.execute("ALTER TABLE mineros_activos ADD COLUMN pass2 TEXT DEFAULT '123'")
        except: pass
        try: c.execute("ALTER TABLE mineros_activos ADD COLUMN pass3 TEXT DEFAULT '123'")
        except: pass
        try: c.execute("ALTER TABLE historial_nube ADD COLUMN errores INTEGER DEFAULT 0")
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
    cache_red['datos'] = datos
    cache_red['ultima_act'] = ahora
    return datos

@app.route('/')
@login_required
def index():
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos")
    mineros = cur.fetchall()
    cur.execute("SELECT * FROM nombres_mineros")
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}
    con.close()
    
    datos_red = obtener_datos_red()
    registros, en_linea, offline, ht, cw, tm, ig = [], 0, 0, 0.0, 0.0, 0, 0.0
    
    for m in mineros:
        ip = m['ip']
        mod = m['modelo']
        nombre_custom = nombres_dict.get(ip, ip.split('.')[-1])
        ult = m['ultima_act'] if m['ultima_act'] else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        hr = float(m['hashrate']) if m['hashrate'] else 0.0
        t = int(m['temp_max']) if m['temp_max'] else 0
        c = float(m['consumo_w']) if m['consumo_w'] else 0.0
        
        segs_desconectado = (datetime.utcnow() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds()
        eficiencia_val = round(c / hr, 2) if hr > 0 else 0
        if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)

        if segs_desconectado < 180:
            en_linea += 1; ht += hr; cw += c; tm = max(tm, t)
            b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296) if datos_red['dificultad_t'] > 0 else 0
            ib = b * datos_red['precio']; ig += ib
            str_gan = f"-USD {abs(ib):.2f}" if ib < 0 else f"{ib:.2f} USD"
            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 
                'estado': '<span style="color:var(--accent-green);">● En línea</span>', 
                'tiempo_act': f'hace {int(segs_desconectado)}s', 'hashrate': hr, 
                'hashrate_30m': float(m.get('hashrate_30m', hr)), 
                'temp_num': t, 'consumo_kw': f"{round(c/1000, 2)} kW", 
                'eficiencia': eficiencia_val, 'ganancia_neta': str_gan
            })
        else:
            offline += 1
            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 
                'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>', 
                'tiempo_act': 'Offline', 'hashrate': 0, 'hashrate_30m': 0, 'temp_num': 0, 
                'consumo_kw': '0.0 kW', 'eficiencia': 0, 'ganancia_neta': "USD 0.00"
            })
            
    flota = {
        'hashrate_total': round(ht, 1), 'consumo_w_total': round(cw, 0), 
        'consumo_kw': round(cw/1000, 1), 'temp_max': tm, 
        'precio_btc': f"{datos_red['precio']:,.2f}", 
        'ingreso_dia': f"{ig:.2f}", 'ganancia_dia': f"{ig:.2f} USD"
    }
    return render_template('resumen.html', active_page='resumen', registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=flota)

@app.route('/minero/<ip>')
@login_required
def detalle_minero(ip):
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos WHERE ip=%s", (ip,))
    m = cur.fetchone()
    cur.execute("SELECT nombre FROM nombres_mineros WHERE ip=%s", (ip,))
    n_row = cur.fetchone()
    cur.execute("SELECT fecha, hashrate, temp_max, errores FROM historial_nube WHERE ip=%s ORDER BY id DESC LIMIT 5000", (ip,))
    hist = cur.fetchall()
    con.close()
    
    if not m: return redirect('/')
    
    nombre_minero = n_row['nombre'] if n_row and n_row['nombre'] else ip.split('.')[-1]
    hr_val = float(m['hashrate']) if m['hashrate'] else 0.0
    hr_30m_val = float(m.get('hashrate_30m', hr_val)) if m.get('hashrate_30m') else hr_val
    t_val = int(m['temp_max']) if m['temp_max'] else 0
    c_val = float(m['consumo_w']) if m['consumo_w'] else 0.0
    ult = m['ultima_act'] if m['ultima_act'] else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    offline = (datetime.utcnow() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds() > 180
    
    pool1 = str(m['pool1']) if m['pool1'] else ""; worker1 = str(m['worker1']) if m['worker1'] else ""; pass1 = str(m['pass1']) if m['pass1'] else "123"
    pool2 = str(m['pool2']) if m['pool2'] else ""; worker2 = str(m['worker2']) if m['worker2'] else ""; pass2 = str(m['pass2']) if m['pass2'] else "123"
    pool3 = str(m['pool3']) if m['pool3'] else ""; worker3 = str(m['worker3']) if m['worker3'] else ""; pass3 = str(m['pass3']) if m['pass3'] else "123"
    
    mac = str(m['mac']) if m['mac'] else "-"; serial = str(m['serial']) if m['serial'] else "-"
    fw = str(m['firmware']) if m['firmware'] else "Desconocido"; fans = str(m['fans']) if m['fans'] else "0 / 0"
    
    cadenas_str = m['cadenas'] if m['cadenas'] else "[]"
    try: cadenas = json.loads(cadenas_str)
    except: cadenas = []
    if not cadenas: cadenas = [{"id": 0, "temp_c": t_val, "temp_p": max(0, t_val-5)}, {"id": 1, "temp_c": max(0, t_val-1), "temp_p": max(0, t_val-6)}, {"id": 2, "temp_c": max(0, t_val-2), "temp_p": max(0, t_val-7)}]

    hist.reverse()
    f_raw, hr_g, tp_g, err_g = [], [], [], []
    prev_err = 0
    if hist:
        for idx, h in enumerate(hist):
            dt_actual = datetime.strptime(h['fecha'], "%Y-%m-%d %H:%M:%S")
            ts = calendar.timegm(dt_actual.timetuple())
            f_raw.append(ts) 
            val_h = float(h['hashrate']) if h['hashrate'] else 0
            if idx > 0:
                dt_prev = datetime.strptime(hist[idx-1]['fecha'], "%Y-%m-%d %H:%M:%S")
                if (dt_actual - dt_prev).total_seconds() > 300: 
                    f_raw.insert(-1, calendar.timegm(dt_prev.timetuple())); hr_g.insert(-1, 0); tp_g.insert(-1, 0); err_g.insert(-1, 0)
            
            # PROBLEMA 3: Cálculo de errores por intervalo (no acumulativo)
            total_err_actual = int(h['errores']) if 'errores' in h and h['errores'] else 0
            diff_err = total_err_actual - prev_err
            if diff_err < 0: diff_err = total_err_actual # si se reinició el minero
            err_g.append(max(0, diff_err))
            prev_err = total_err_actual

            hr_g.append(val_h)
            tp_g.append(int(h['temp_max']) if h['temp_max'] else 0)
    else:
        f_raw = [calendar.timegm(datetime.utcnow().timetuple())]; hr_g = [hr_val]; tp_g = [t_val]; err_g = [0]
        
    dt = {
        'hashrate': hr_val, 'hashrate_30m': hr_30m_val, 'temp_max': t_val, 
        'errores': m.get('errores', 0), 'consumo': f"{round(c_val/1000, 2)} kW", 
        'fans': fans, 'uptime': '0d 13h' if not offline else 'Desconectado', 
        'hashrate_c0': round(hr_val/len(cadenas), 2) if cadenas else 0
    }
    extra = {
        'worker1': worker1, 'pool1': pool1, 'pass1': pass1, 
        'worker2': worker2, 'pool2': pool2, 'pass2': pass2, 
        'worker3': worker3, 'pool3': pool3, 'pass3': pass3, 
        'mac': mac, 'serial': serial, 'firmware': fw, 'cadenas': cadenas
    }
    
    eficiencia_val = round(c_val / hr_val, 2) if hr_val > 0 else 0
    if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)
    disp = {'eficiencia': eficiencia_val}
    
    return render_template('detalle.html', active_page='mineros', ip=ip, nombre=nombre_minero, modelo=m['modelo'], ultimo=dt, extra=extra, dispositivo=disp, offline=offline, graf_fechas_raw=json.dumps(f_raw), graf_hashrate=json.dumps(hr_g), graf_temps=json.dumps(tp_g), graf_errores=json.dumps(err_g))

@app.route('/guardar_nombre', methods=['POST'])
@login_required
def guardar_nombre():
    ip = request.form.get('ip
