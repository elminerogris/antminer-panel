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
        cadenas = [{"id": 0, "temp_c": t_val, "temp_p": max(0, t_val-5)}, {"id": 1, "temp_c": max(0, t_val-1), "temp_p": max(0, t_val-6)}, {"id": 2, "temp_c": max(0, t_val-2), "temp_p": max(0, t_val-7)}]

    hist.reverse()
    f_raw = []
    hr_g = []
    tp_g = []
    
    if hist:
        for idx, h in enumerate(hist):
            dt_actual = datetime.strptime(h['fecha'], "%Y-%m-%d %H:%M:%S")
            ts = calendar.timegm(dt_actual.timetuple())
            f_raw.append(ts) 
            
            val_h = float(h['hashrate']) if h['hashrate'] else 0
            if idx > 0:
                dt_prev = datetime.strptime(hist[idx-1]['fecha'], "%Y-%m-%d %H:%M:%S")
                if (dt_actual - dt_prev).total_seconds() > 300: 
                    f_raw.insert(-1, calendar.timegm(dt_prev.timetuple()))
                    hr_g.insert(-1, 0)
                    tp_g.insert(-1, 0)
            hr_g.append(val_h)
            tp_g.append(int(h['temp_max']) if h['temp_max'] else 0)
    else:
        f_raw = [calendar.timegm(datetime.utcnow().timetuple())]
        hr_g = [hr_val]
        tp_g = [t_val]
        
    dt = {'hashrate': hr_val, 'temp_max': t_val, 'errores': m['errores'], 'consumo': round(c_val/1000, 1), 'fans': fans, 'uptime': '0d 13h' if not offline else 'Desconectado', 'hashrate_c0': round(hr_val/len(cadenas), 2) if cadenas else 0}
    extra = {'worker1': worker1, 'pool1': pool1, 'worker2': worker2, 'pool2': pool2, 'worker3': worker3, 'pool3': pool3, 'mac': mac, 'serial': serial, 'firmware': fw, 'cadenas': cadenas}
    disp = {'eficiencia': round(c_val/hr_val, 2) if hr_val > 0 else 0}
    
    return render_template('detalle.html', active_page='mineros', ip=ip, modelo=m['modelo'], ultimo=dt, extra=extra, dispositivo=disp, offline=offline, graf_fechas_raw=json.dumps(f_raw), graf_hashrate=json.dumps(hr_g), graf_temps=json.dumps(tp_g))

@app.route('/plan')
@login_required
def plan():
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT plan, saldo FROM usuarios WHERE username=%s", (session['usuario'],))
    usr = cur.fetchone()
    con.close()
    
    if usr:
        session['plan'] = usr.get('plan', 'Gratis')
        session['saldo'] = usr.get('saldo', 0.0)
        
    return render_template('plan.html', active_page='plan', current_plan=session.get('plan', 'Gratis'), saldo=session.get('saldo', 0.0))

@app.route('/granjas')
@login_required
def granjas(): 
    return render_template('subpaginas.html', active_page='granjas', titulo="🏭 Gestión de Granjas")

@app.route('/mineros')
@login_required
def mineros(): 
    return redirect('/')

@app.route('/alertas')
@login_required
def alertas(): 
    return render_template('subpaginas.html', active_page='alertas', titulo="🔔 Centro de Alertas")

@app.route('/ajustes')
@login_required
def ajustes(): 
    return render_template('subpaginas.html', active_page='ajustes', titulo="⚙️ Ajustes del Sistema")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        con = get_db()
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM usuarios WHERE username=%s AND password=%s", (u, p))
        usr = cur.fetchone()
        con.close()
        
        if usr:
            session['usuario'] = u
            session['plan'] = usr.get('plan', 'Gratis')
            session['saldo'] = usr.get('saldo', 0.0)
            return redirect('/')
            
        return render_template('login.html', error='Credenciales incorrectas', mode='login')
    return render_template('login.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        try:
            con = get_db()
            con.autocommit = True
            cur = con.cursor()
            cur.execute("INSERT INTO usuarios (username, password, plan, saldo) VALUES (%s, %s, 'Gratis', 0.0)", (u, p))
            con.close()
            return render_template('login.html', success='Cuenta creada. Ahora inicia sesión.', mode='login')
        except Exception as e:
            return render_template('login.html', error='El nombre de usuario ya está en uso', mode='register')
            
    return render_template('login.html', mode='register')

@app.route('/logout')
def logout(): 
    session.clear()
    return redirect('/login')

@app.route('/exportar_csv')
@login_required
def exportar_csv():
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT ip, modelo, hashrate, temp_max, consumo_w, estado FROM mineros_activos")
    filas = cur.fetchall()
    con.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['IP', 'Modelo', 'Hashrate (TH/s)', 'Temp Max (C)', 'Consumo (W)', 'Estado'])
    
    for f in filas: 
        writer.writerow([f['ip'], f['modelo'], f['hashrate'], f['temp_max'], f['consumo_w'], f['estado']])
        
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=Reporte_El_Minero_Gris.csv"})

@app.route('/cmd_bulk', methods=['POST'])
@login_required
def cmd_bulk():
    data = request.json
    accion = data.get('accion')
    ips = data.get('ips', [])
    
    if accion and ips:
        con = get_db()
        con.autocommit = True
        cur = con.cursor()
        for ip in ips: 
            cur.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (%s, %s, 'pendiente', %s)", (ip, accion, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        con.close()
        
    return "OK"

@app.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    cmd_str = ""
    if accion in ['restart', 'sleep', 'normal', 'blink', 'scan', 'get_log'] or accion.startswith('profile_'): 
        cmd_str = accion.replace('_', '|') if accion.startswith('profile_') else accion
    elif accion == 'setpool': 
        cmd_str = f"setpool|{request.args.get('url', '')}|{request.args.get('user', '')}"
        
    if cmd_str:
        con = get_db()
        con.autocommit = True
        cur = con.cursor()
        cur.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (%s, %s, 'pendiente', %s)", (ip, cmd_str, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        con.close()
        
    return "OK"

@app.route('/api/ver_log/<ip>')
def ver_log(ip):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT contenido FROM logs_mineros WHERE ip=%s", (ip,))
    log = cur.fetchone()
    con.close()
    return log[0] if log else "Esperando respuesta..."

@app.route('/api/recibir_logs', methods=['POST'])
def recibir_logs():
    data = request.json
    if data and 'ip' in data and 'log' in data:
        con = get_db()
        con.autocommit = True
        cur = con.cursor()
        cur.execute("INSERT INTO logs_mineros (ip, contenido, fecha) VALUES (%s, %s, %s) ON CONFLICT(ip) DO UPDATE SET contenido=EXCLUDED.contenido, fecha=EXCLUDED.fecha", (data['ip'], data['log'], datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        con.close()
    return "OK", 200

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json
    if not datos: 
        return "Error", 400
        
    con = get_db()
    con.autocommit = True
    cur = con.cursor()
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    for m in datos.get('mineros', []):
        err = m.get('errores', 0)
        mc = m.get('mac', '')
        sr = m.get('serial', '')
        fw = m.get('firmware', '')
        fs = m.get('fans', '')
        p1 = m.get('pool1', '')
        w1 = m.get('worker1', '')
        p2 = m.get('pool2', '')
        w2 = m.get('worker2', '')
        p3 = m.get('pool3', '')
        w3 = m.get('worker3', '')
        cadenas_json = json.dumps(m.get('cadenas', []))
        
        cur.execute('''INSERT INTO mineros_activos (ip, modelo, hashrate, temp_max, consumo_w, estado, ultima_act, errores, pool1, worker1, pool2, worker2, pool3, worker3, mac, serial, firmware, fans, cadenas) 
                       VALUES (%s, %s, %s, %s, %s, 'online', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(ip) DO UPDATE SET 
                       modelo=EXCLUDED.modelo, hashrate=EXCLUDED.hashrate, temp_max=EXCLUDED.temp_max, consumo_w=EXCLUDED.consumo_w, ultima_act=EXCLUDED.ultima_act, errores=EXCLUDED.errores, pool1=EXCLUDED.pool1, worker1=EXCLUDED.worker1, pool2=EXCLUDED.pool2, worker2=EXCLUDED.worker2, pool3=EXCLUDED.pool3, worker3=EXCLUDED.worker3, mac=EXCLUDED.mac, serial=EXCLUDED.serial, firmware=EXCLUDED.firmware, fans=EXCLUDED.fans, cadenas=EXCLUDED.cadenas''', 
                    (m['ip'], m['modelo'], m['hashrate'], m['temp'], m['consumo'], ahora, err, p1, w1, p2, w2, p3, w3, mc, sr, fw, fs, cadenas_json))
                    
        cur.execute("INSERT INTO historial_nube (ip, hashrate, temp_max, fecha) VALUES (%s, %s, %s, %s)", (m['ip'], m['hashrate'], m['temp'], ahora))
    
    cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente'")
    pend = cur.fetchall()
    ordenes = [{"ip": c[1], "comando": c[2]} for c in pend]
    
    for c in pend: 
        cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=%s", (c[0],))
        
    con.close()
    return jsonify({"status": "ok", "ordenes": ordenes}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
