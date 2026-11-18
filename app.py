from flask import Flask, render_template, render_template_string, redirect, request, session, url_for, jsonify, Response, g
from jinja2 import TemplateNotFound
from functools import wraps
import psycopg2, psycopg2.extras, json, urllib.request, urllib.parse, time, calendar, csv, io, os, secrets, traceback
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get("SECRET_KEY", "super_secreta_antminer_tools")

DB_URL = os.environ.get("DATABASE_URL", "")
ultimo_envio_alerta = {}
ultima_limpieza_bd = 0
cache_geo_granja = {}  # {ip_publica: {'lat': ..., 'lon': ..., 'ciudad': ..., 'pais': ..., 'ts': ...}}
db_inicializada = False

@app.errorhandler(Exception)
def manejar_error_global(e):
    tb = traceback.format_exc()
    html_error = f"""
    <div style="background:#0f111a;color:#fff;font-family:monospace;padding:30px;min-height:100vh;">
        <h2 style="color:#ff5252;">⚠️ Diagnóstico Técnico de El Minero Gris</h2>
        <p style="color:#a5a7b5;">Detalle técnico detectado en el servidor:</p>
        <pre style="background:#000;color:#00e676;padding:20px;border-radius:8px;border:1px solid #333;overflow-x:auto;">{tb}</pre>
        <a href="/" style="display:inline-block;margin-top:15px;background:#3366ff;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-family:sans-serif;font-weight:bold;">🔄 Reintentar / Ir al Inicio</a>
    </div>
    """
    return html_error, 500

def get_db():
    if not DB_URL: raise Exception("Falta DATABASE_URL en Render")
    if 'db_con' not in g or g.db_con.closed != 0:
        g.db_con = psycopg2.connect(DB_URL, connect_timeout=8)
    return g.db_con

@app.teardown_appcontext
def cerrar_conexion_bd(exception=None):
    con = g.pop('db_con', None)
    if con is not None and con.closed == 0:
        try: con.close()
        except: pass

def generar_api_key():
    return "EMG-" + secrets.token_hex(8).upper()

def a_int_seguro(val, defecto=0):
    try:
        if val is None or val == "": return defecto
        return int(float(val))
    except:
        return defecto

def a_float_seguro(val, defecto=0.0):
    try:
        if val is None or val == "": return defecto
        return float(val)
    except:
        return defecto

# Detecta la ubicación física de la GRANJA (Ciudad y País) a partir de la IP pública desde donde transmite agente.py
def obtener_ubicacion_ip_agente(ip_publica):
    global cache_geo_granja
    ahora = time.time()
    if ip_publica in cache_geo_granja and (ahora - cache_geo_granja[ip_publica]['ts'] < 86400):
        return cache_geo_granja[ip_publica]

    if not ip_publica or ip_publica.startswith(("127.", "192.168.", "10.", "172.")):
        ip_query = ""
    else:
        ip_query = ip_publica.strip()

    urls = [
        f"http://ip-api.com/json/{ip_query}?fields=status,country,regionName,city,lat,lon",
        f"https://ipwho.is/{ip_query}"
    ]
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
            raw = urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8')
            j = json.loads(raw)
            if j.get('status') == 'success' or j.get('success') is True:
                lat = float(j.get('lat') or j.get('latitude'))
                lon = float(j.get('lon') or j.get('longitude'))
                ciudad = str(j.get('city') or j.get('regionName') or j.get('region') or 'Granja Local').strip()
                pais = str(j.get('country') or 'Bolivia').strip()
                res = {'lat': lat, 'lon': lon, 'ciudad': ciudad, 'pais': pais, 'ts': ahora}
                cache_geo_granja[ip_publica] = res
                return res
        except:
            pass
    return {'lat': -17.7833, 'lon': -63.1821, 'ciudad': 'Santa Cruz de la Sierra', 'pais': 'Bolivia', 'ts': ahora}

def render_seguro(nombre_archivo, **contexto):
    try:
        return render_template(nombre_archivo, **contexto)
    except TemplateNotFound:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        rutas_posibles = [
            os.path.join(base_dir, 'templates', nombre_archivo),
            os.path.join(base_dir, nombre_archivo),
            os.path.join(base_dir, 'templates', 'detalle.html'),
            os.path.join(base_dir, 'detalle.html')
        ]
        for ruta in rutas_posibles:
            if os.path.exists(ruta):
                with open(ruta, 'r', encoding='utf-8') as f:
                    return render_template_string(f.read(), **contexto)
        html_fallback = """
        {% extends "layout.html" %}
        {% block content %}
        <div style="background:var(--bg-card);padding:30px;border-radius:8px;border:1px solid var(--border-color);text-align:center;">
            <h2>{{ titulo | default('Sección activa') }}</h2>
            <p style="color:var(--text-muted);">El archivo <b>{{ archivo }}</b> no se encontró dentro de la carpeta <code>templates/</code> en GitHub.</p>
        </div>
        {% endblock %}
        """
        return render_template_string(html_fallback, archivo=nombre_archivo, **contexto)

def init_db():
    global db_inicializada
    if not DB_URL: return
    con = None
    try:
        con = psycopg2.connect(DB_URL, connect_timeout=8)
        con.autocommit = True
        c = con.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, owner TEXT DEFAULT 'giovanni', modelo TEXT, unidad TEXT DEFAULT 'TH/s', hashrate REAL, hashrate_30m REAL, temp_max INTEGER, temp_amb INTEGER DEFAULT 0, consumo_w REAL, estado TEXT, ultima_act TEXT, errores INTEGER, pool1 TEXT, worker1 TEXT, pass1 TEXT, pool2 TEXT, worker2 TEXT, pass2 TEXT, pool3 TEXT, worker3 TEXT, pass3 TEXT, mac TEXT, serial TEXT, firmware TEXT, fans TEXT, cadenas TEXT, uptime TEXT DEFAULT '0h 0m', status1 TEXT DEFAULT 'Alive', acc1 INTEGER DEFAULT 0, rej1 INTEGER DEFAULT 0, status2 TEXT DEFAULT 'Alive', acc2 INTEGER DEFAULT 0, rej2 INTEGER DEFAULT 0, status3 TEXT DEFAULT 'Dead', acc3 INTEGER DEFAULT 0, rej3 INTEGER DEFAULT 0, hostname TEXT DEFAULT 'Antminer', net_proto TEXT DEFAULT 'Static', netmask TEXT DEFAULT '255.255.255.0', gateway TEXT DEFAULT '192.168.0.1', dns TEXT DEFAULT '8.8.8.8', farm_lat REAL DEFAULT -17.7833, farm_lon REAL DEFAULT -63.1821, farm_city TEXT DEFAULT 'Santa Cruz de la Sierra', farm_country TEXT DEFAULT 'Bolivia')''')
        c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, plan TEXT DEFAULT 'Gratis', saldo REAL DEFAULT 0.0, api_key TEXT, subnet TEXT DEFAULT '192.168.0', tarifa_kwh REAL DEFAULT 0.0, tg_token TEXT DEFAULT '', tg_chat_id TEXT DEFAULT '', umbral_temp INTEGER DEFAULT 80, umbral_hr REAL DEFAULT 50.0, alertas_activas INTEGER DEFAULT 1)''')
        c.execute('''CREATE TABLE IF NOT EXISTS cola_comandos (id SERIAL PRIMARY KEY, owner TEXT DEFAULT 'giovanni', ip TEXT, comando TEXT, estado TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS historial_nube (id SERIAL PRIMARY KEY, owner TEXT DEFAULT 'giovanni', ip TEXT, hashrate REAL, temp_max INTEGER, temp_amb INTEGER DEFAULT 0, errores INTEGER DEFAULT 0, cadenas TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nombres_mineros (ip TEXT UNIQUE, owner TEXT DEFAULT 'giovanni', nombre TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs_mineros (ip TEXT UNIQUE, contenido TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS registro_alertas (id SERIAL PRIMARY KEY, owner TEXT, ip TEXT, tipo TEXT, mensaje TEXT, fecha TEXT)''')
        
        columnas_nuevas = [
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'giovanni'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS unidad TEXT DEFAULT 'TH/s'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS hashrate_30m REAL DEFAULT 0.0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS temp_amb INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS pass1 TEXT DEFAULT '123'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS pass2 TEXT DEFAULT '123'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS pass3 TEXT DEFAULT '123'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS cadenas TEXT",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS uptime TEXT DEFAULT '0h 0m'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS status1 TEXT DEFAULT 'Alive'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS acc1 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rej1 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS status2 TEXT DEFAULT 'Alive'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS acc2 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rej2 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS status3 TEXT DEFAULT 'Dead'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS acc3 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rej3 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS hostname TEXT DEFAULT 'Antminer'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS net_proto TEXT DEFAULT 'Static'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS netmask TEXT DEFAULT '255.255.255.0'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS gateway TEXT DEFAULT '192.168.0.1'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS dns TEXT DEFAULT '8.8.8.8'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_lat REAL DEFAULT -17.7833",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_lon REAL DEFAULT -63.1821",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_city TEXT DEFAULT 'Santa Cruz de la Sierra'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_country TEXT DEFAULT 'Bolivia'",
            "ALTER TABLE cola_comandos ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'giovanni'",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'giovanni'",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS errores INTEGER DEFAULT 0",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS temp_amb INTEGER DEFAULT 0",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS cadenas TEXT",
            "ALTER TABLE nombres_mineros ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'giovanni'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS api_key TEXT",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS subnet TEXT DEFAULT '192.168.0'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tarifa_kwh REAL DEFAULT 0.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tg_token TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tg_chat_id TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS umbral_temp INTEGER DEFAULT 80",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS umbral_hr REAL DEFAULT 50.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS alertas_activas INTEGER DEFAULT 1"
        ]
        for col_sql in columnas_nuevas:
            try: c.execute(col_sql)
            except: pass

        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_ip_id ON historial_nube (ip, id DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_ip_fecha ON historial_nube (ip, fecha)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_fecha ON historial_nube (fecha)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cola_estado_owner ON cola_comandos (estado, owner)")

        c.execute("SELECT * FROM usuarios WHERE username='giovanni'")
        if not c.fetchone():
            c.execute("INSERT INTO usuarios (username, password, api_key) VALUES ('giovanni', 'admin123', %s)", (generar_api_key(),))
        
        c.execute("SELECT id, username FROM usuarios WHERE api_key IS NULL OR api_key = ''")
        for u_row in c.fetchall():
            c.execute("UPDATE usuarios SET api_key=%s WHERE id=%s", (generar_api_key(), u_row[0]))

        c.execute("UPDATE mineros_activos SET owner='giovanni' WHERE owner IS NULL OR owner=''")
        con.close()
        db_inicializada = True
    except Exception as e:
        print(f"Error BD Init: {e}")
        if con is not None and con.closed == 0:
            try: con.close()
            except: pass

try: init_db()
except Exception as e: print(f"Error init_db: {e}")

@app.before_request
def asegurar_bd():
    if not db_inicializada:
        init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def enviar_telegram(token, chat_id, texto):
    if not token or not chat_id: return False
    try:
        url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
        datos = urllib.parse.urlencode({'chat_id': chat_id.strip(), 'text': texto, 'parse_mode': 'HTML'}).encode('utf-8')
        req = urllib.request.Request(url, data=datos, headers={'User-Agent': 'Mozilla/5.0'})
        urllib.request.urlopen(req, timeout=4)
        return True
    except Exception as e:
        print(f"Error Telegram: {e}")
        return False

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

def calcular_segundos_desconectado(ult_str):
    if not ult_str: return 999999
    try:
        segs = (datetime.utcnow() - datetime.strptime(str(ult_str).strip(), "%Y-%m-%d %H:%M:%S")).total_seconds()
        return max(0, segs)
    except:
        return 0

def ordenar_ip_key(item):
    ip_str = str(item.get('ip') or '')
    partes = ip_str.split('.')
    if len(partes) == 4 and all(p.isdigit() for p in partes):
        return tuple(int(p) for p in partes)
    return (999, 999, 999, 999)

# --- VISTA: / y /resumen -> templates/resumen.html ---
@app.route('/')
@app.route('/resumen')
@login_required
def index():
    usr_actual = session.get('usuario', 'giovanni')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if usr_actual == 'giovanni':
        cur.execute("SELECT * FROM mineros_activos")
    else:
        cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    mineros = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros")
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}
    
    datos_red = obtener_datos_red()
    registros, en_linea, offline = [], 0, 0
    ht, kt, gt, cw, tm, ig = 0.0, 0.0, 0.0, 0.0, 0, 0.0
    
    for m in mineros:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre_custom = nombres_dict.get(ip, ip.split('.')[-1])
        hr = a_float_seguro(m.get('hashrate'), 0.0)
        t = a_int_seguro(m.get('temp_max'), 0)
        c = a_float_seguro(m.get('consumo_w'), 0.0)
        
        segs_desconectado = calcular_segundos_desconectado(m.get('ultima_act'))
        eficiencia_val = round(c / hr, 2) if hr > 0 else 0
        if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)

        if segs_desconectado < 300:
            en_linea += 1
            if unidad == 'TH/s': ht += hr
            elif unidad == 'kSol/s': kt += hr
            elif unidad == 'GH/s': gt += hr
            
            cw += c; tm = max(tm, t)
            if unidad == 'TH/s' and datos_red['dificultad_t'] > 0:
                b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296)
                ib = b * datos_red['precio']; ig += ib
                str_gan = f"-USD {abs(ib):.2f}" if ib < 0 else f"{ib:.2f} USD"
            else:
                str_gan = "Pool Externa"
                
            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'unidad': unidad, 'unidad_efi': unidad_efi,
                'estado': '<span style="color:var(--accent-green);">● En línea</span>', 
                'tiempo_act': f'hace {int(segs_desconectado)}s', 'hashrate': hr, 
                'hashrate_30m': a_float_seguro(m.get('hashrate_30m'), hr), 
                'temp_num': t, 'consumo_kw': f"{round(c/1000, 2)} kW", 
                'eficiencia': eficiencia_val, 'ganancia_neta': str_gan
            })
        else:
            offline += 1
            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'unidad': unidad, 'unidad_efi': unidad_efi,
                'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>', 
                'tiempo_act': f'hace {int(segs_desconectado // 60)}m', 'hashrate': 0, 'hashrate_30m': 0, 'temp_num': 0, 
                'consumo_kw': '0.0 kW', 'eficiencia': 0, 'ganancia_neta': "USD 0.00"
            })
            
    flota = {
        'hashrate_total': round(ht, 1),
        'ksol_total': round(kt, 1),
        'ghs_total': round(gt, 1),
        'consumo_w_total': round(cw, 0), 
        'consumo_kw': round(cw/1000, 1), 'temp_max': tm, 
        'precio_btc_raw': datos_red['precio'],
        'precio_btc': f"{datos_red['precio']:,.2f}", 
        'dificultad_t': round(datos_red['dificultad_t'], 2),
        'recompensa_bloque': datos_red['recompensa'],
        'red_ehs': datos_red['red_ehs'],
        'ingreso_dia': f"{ig:.2f}", 'ganancia_dia': f"{ig:.2f} USD"
    }
    return render_seguro('resumen.html', active_page='resumen', registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=flota)

# --- VISTA: /minero/<ip> -> templates/minero.html ---
@app.route('/minero/<ip>')
@login_required
def detalle_minero(ip):
    usr_actual = session.get('usuario', 'giovanni')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if usr_actual == 'giovanni':
        cur.execute("SELECT * FROM mineros_activos WHERE ip=%s", (ip,))
    else:
        cur.execute("SELECT * FROM mineros_activos WHERE ip=%s AND owner=%s", (ip, usr_actual))
    m = cur.fetchone()
    if not m: return redirect('/')

    cur.execute("SELECT nombre FROM nombres_mineros WHERE ip=%s", (ip,))
    n_row = cur.fetchone()

    # 44000 registros de 1 minuto cubren los 30 días completos (30d * 1440m = 43200 registros)
    cur.execute("""
        SELECT fecha, hashrate, temp_max, temp_amb, errores, cadenas 
        FROM (
            SELECT id, fecha, hashrate, temp_max, temp_amb, errores, cadenas 
            FROM historial_nube 
            WHERE ip=%s 
            ORDER BY id DESC 
            LIMIT 44000
        ) sub 
        ORDER BY id ASC
    """, (ip,))
    hist = cur.fetchall()
    
    mod_str = str(m.get('modelo') or 'Antminer')
    unidad_hr = str(m.get('unidad') or ('kSol/s' if 'z15' in mod_str.lower() else 'TH/s'))
    unidad_efi = "J/kSol" if unidad_hr == "kSol/s" else ("J/GH" if unidad_hr == "GH/s" else "J/TH")
    
    nombre_minero = n_row['nombre'] if n_row and n_row.get('nombre') else ip.split('.')[-1]
    hr_val = a_float_seguro(m.get('hashrate'), 0.0)
    hr_30m_val = a_float_seguro(m.get('hashrate_30m'), hr_val)
    t_val = a_int_seguro(m.get('temp_max'), 0)
    t_amb_val = a_int_seguro(m.get('temp_amb'), 0)
    c_val = a_float_seguro(m.get('consumo_w'), 0.0)
    offline = calcular_segundos_desconectado(m.get('ultima_act')) > 300
    
    pool1 = str(m.get('pool1') or ""); worker1 = str(m.get('worker1') or ""); pass1 = str(m.get('pass1') or "123")
    pool2 = str(m.get('pool2') or ""); worker2 = str(m.get('worker2') or ""); pass2 = str(m.get('pass2') or "123")
    pool3 = str(m.get('pool3') or ""); worker3 = str(m.get('worker3') or ""); pass3 = str(m.get('pass3') or "123")
    
    status1 = str(m.get('status1') or ("Alive" if pool1 else "Dead"))
    acc1 = a_int_seguro(m.get('acc1'), 0); rej1 = a_int_seguro(m.get('rej1'), 0)
    status2 = str(m.get('status2') or ("Alive" if pool2 else "Dead"))
    acc2 = a_int_seguro(m.get('acc2'), 0); rej2 = a_int_seguro(m.get('rej2'), 0)
    status3 = str(m.get('status3') or ("Alive" if pool3 else "Dead"))
    acc3 = a_int_seguro(m.get('acc3'), 0); rej3 = a_int_seguro(m.get('rej3'), 0)

    mac = str(m.get('mac') or "-"); serial = str(m.get('serial') or "-")
    fw = str(m.get('firmware') or "Desconocido"); fans = str(m.get('fans') or "0 / 0 RPM")
    
    default_gw = ".".join(ip.split('.')[:3]) + ".1" if '.' in ip else "192.168.0.1"
    hostname = str(m.get('hostname') or f"Antminer{ip.split('.')[-1]}")
    net_proto = str(m.get('net_proto') or "Static")
    netmask = str(m.get('netmask') or "255.255.255.0")
    gateway = str(m.get('gateway') or default_gw)
    dns = str(m.get('dns') or "8.8.8.8")

    # Coordenadas físicas de la granja donde está el Antminer (detectadas por la IP del agente.py)
    farm_lat = a_float_seguro(m.get('farm_lat'), -17.7833)
    farm_lon = a_float_seguro(m.get('farm_lon'), -63.1821)
    farm_city = str(m.get('farm_city') or 'Santa Cruz de la Sierra')
    farm_country = str(m.get('farm_country') or 'Bolivia')
    
    raw_uptime = str(m.get('uptime') or "")
    uptime_val = raw_uptime if (raw_uptime and raw_uptime != "0h 0m") else "En línea"
    if offline: uptime_val = "Desconectado"
    
    cadenas_str = m.get('cadenas') or "[]"
    try: cadenas = json.loads(cadenas_str)
    except: cadenas = []
    if not cadenas: 
        cadenas = [
            {"id": 0, "temp_c": t_val, "temp_p": max(0, t_val-5), "chips": 114, "hw": 0, "freq": 0, "hr": round(hr_val/3, 2)}, 
            {"id": 1, "temp_c": max(0, t_val-1), "temp_p": max(0, t_val-6), "chips": 114, "hw": 0, "freq": 0, "hr": round(hr_val/3, 2)}, 
            {"id": 2, "temp_c": max(0, t_val-2), "temp_p": max(0, t_val-7), "chips": 114, "hw": 0, "freq": 0, "hr": round(hr_val/3, 2)}
        ]

    f_raw, hr_g, tp_g, tamb_g, err_g, cad_g = [], [], [], [], [], []
    prev_err = None
    if hist:
        for idx, h in enumerate(hist):
            try: dt_actual = datetime.strptime(h['fecha'], "%Y-%m-%d %H:%M:%S")
            except: continue
            ts = calendar.timegm(dt_actual.timetuple())
            f_raw.append(ts) 
            val_h = a_float_seguro(h.get('hashrate'), 0.0)
            
            total_err_actual = a_int_seguro(h.get('errores'), 0)
            if prev_err is None:
                diff_err = 0
            else:
                diff_err = total_err_actual - prev_err
                if diff_err < 0: diff_err = total_err_actual
            err_g.append(max(0, diff_err))
            prev_err = total_err_actual

            hr_g.append(val_h)
            tp_g.append(a_int_seguro(h.get('temp_max'), 0))
            
            val_amb = a_int_seguro(h.get('temp_amb'), 0)
            tamb_g.append(val_amb if val_amb > 0 else None)
            
            c_hist_item = []
            if h.get('cadenas'):
                try: c_hist_item = json.loads(h['cadenas'])
                except: pass
            cad_g.append(c_hist_item if c_hist_item else cadenas)
    else:
        f_raw = [calendar.timegm(datetime.utcnow().timetuple())]
        hr_g = [hr_val]; tp_g = [t_val]
        tamb_g = [t_amb_val if t_amb_val > 0 else None]
        err_g = [0]; cad_g = [cadenas]
        
    dt = {
        'hashrate': hr_val, 'hashrate_30m': hr_30m_val, 'unidad': unidad_hr, 'unidad_efi': unidad_efi,
        'temp_max': t_val, 'temp_amb': t_amb_val,
        'errores': a_int_seguro(m.get('errores'), 0), 'consumo': f"{round(c_val/1000, 2)} kW", 
        'fans': fans, 'uptime': uptime_val, 
        'hashrate_c0': round(hr_val/len(cadenas), 2) if cadenas else 0,
        'farm_lat': farm_lat, 'farm_lon': farm_lon, 'farm_city': farm_city, 'farm_country': farm_country
    }
    extra = {
        'worker1': worker1, 'pool1': pool1, 'pass1': pass1, 'status1': status1, 'acc1': acc1, 'rej1': rej1,
        'worker2': worker2, 'pool2': pool2, 'pass2': pass2, 'status2': status2, 'acc2': acc2, 'rej2': rej2,
        'worker3': worker3, 'pool3': pool3, 'pass3': pass3, 'status3': status3, 'acc3': acc3, 'rej3': rej3,
        'mac': mac, 'serial': serial, 'firmware': fw, 'cadenas': cadenas,
        'hostname': hostname, 'net_proto': net_proto, 'netmask': netmask, 'gateway': gateway, 'dns': dns
    }
    
    eficiencia_val = round(c_val / hr_val, 2) if hr_val > 0 else 0
    if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)
    disp = {'eficiencia': eficiencia_val}
    
    return render_seguro('minero.html', active_page='mineros', ip=ip, nombre=nombre_minero, modelo=mod_str, ultimo=dt, extra=extra, dispositivo=disp, offline=offline, graf_fechas_raw=json.dumps(f_raw), graf_hashrate=json.dumps(hr_g), graf_temps=json.dumps(tp_g), graf_temp_amb=json.dumps(tamb_g), graf_errores=json.dumps(err_g), cadenas_json=json.dumps(cadenas), graf_cadenas_hist=json.dumps(cad_g))

# --- VISTA: /mineros -> templates/mineros.html ---
@app.route('/mineros')
@login_required
def mineros():
    usr_actual = session.get('usuario', 'giovanni')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if usr_actual == 'giovanni':
        cur.execute("SELECT * FROM mineros_activos")
    else:
        cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros")
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    lista_mineros = []
    contadores = {'total': len(filas), 'online': 0, 'offline': 0, 'alerta_temp': 0, 'alerta_hw': 0}

    for m in filas:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre = nombres_dict.get(ip, ip.split('.')[-1])
        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_online = segs < 300

        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_online else 0.0
        hr_30m = a_float_seguro(m.get('hashrate_30m'), hr) if es_online else 0.0
        t_max = a_int_seguro(m.get('temp_max'), 0) if es_online else 0
        t_amb = a_int_seguro(m.get('temp_amb'), 0) if es_online else 0
        cons_w = a_float_seguro(m.get('consumo_w'), 0.0) if es_online else 0.0
        err_hw = a_int_seguro(m.get('errores'), 0) if es_online else 0

        efi = round(cons_w / hr, 2) if hr > 0 else 0.0
        if efi > 1000: efi = round(efi / 1000, 2)

        try: cads = json.loads(m['cadenas']) if m.get('cadenas') else []
        except: cads = []
        placas_activas = len(cads) if es_online else 0
        chips_totales = sum(a_int_seguro(c.get('chips'), 0) for c in cads) if es_online else 0

        acc1 = a_int_seguro(m.get('acc1'), 0)
        rej1 = a_int_seguro(m.get('rej1'), 0)
        tot_shares = acc1 + rej1
        pct_rej = round((rej1 / tot_shares) * 100, 2) if tot_shares > 0 else 0.0

        if es_online:
            contadores['online'] += 1
            if t_max >= 75: contadores['alerta_temp'] += 1
            if err_hw > 50 or pct_rej > 1.5: contadores['alerta_hw'] += 1
        else:
            contadores['offline'] += 1

        lista_mineros.append({
            'ip': ip,
            'ultimo_octeto': ip.split('.')[-1],
            'nombre': nombre,
            'modelo': mod,
            'mac': m.get('mac') or '-',
            'firmware': m.get('firmware') or '-',
            'online': es_online,
            'uptime': (m.get('uptime') or 'En línea') if es_online else f'Offline ({int(segs // 60)}m)',
            'hashrate': hr,
            'hashrate_30m': hr_30m,
            'unidad': unidad,
            'unidad_efi': unidad_efi,
            'eficiencia': efi,
            'consumo_kw': round(cons_w / 1000.0, 2),
            'temp_max': t_max,
            'temp_amb': t_amb,
            'errores': err_hw,
            'fans': m.get('fans') or '0 / 0 RPM',
            'placas_activas': placas_activas,
            'chips_totales': chips_totales,
            'pool1': m.get('pool1') or '-',
            'worker1': m.get('worker1') or '-',
            'pass1': m.get('pass1') or '123',
            'pool2': m.get('pool2') or '',
            'worker2': m.get('worker2') or '',
            'pass2': m.get('pass2') or '123',
            'pool3': m.get('pool3') or '',
            'worker3': m.get('worker3') or '',
            'pass3': m.get('pass3') or '123',
            'acc1': acc1,
            'rej1': rej1,
            'pct_rej': pct_rej
        })

    return render_seguro('mineros.html', active_page='mineros', mineros=lista_mineros, stats=contadores)

# --- VISTA: /alertas -> templates/alertas.html ---
@app.route('/alertas', methods=['GET', 'POST'])
@login_required
def alertas():
    usr_actual = session.get('usuario', 'giovanni')
    msg = None
    con = get_db()
    con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'guardar_config':
            tg_token = request.form.get('tg_token', '').strip()
            tg_chat_id = request.form.get('tg_chat_id', '').strip()
            u_temp = a_int_seguro(request.form.get('umbral_temp'), 80)
            u_hr = a_float_seguro(request.form.get('umbral_hr'), 50.0)
            activas = 1 if request.form.get('alertas_activas') == '1' else 0
            cur.execute("UPDATE usuarios SET tg_token=%s, tg_chat_id=%s, umbral_temp=%s, umbral_hr=%s, alertas_activas=%s WHERE username=%s",
                        (tg_token, tg_chat_id, u_temp, u_hr, activas, usr_actual))
            msg = "Configuración de alertas guardada correctamente."
        elif accion == 'probar_telegram':
            cur.execute("SELECT tg_token, tg_chat_id FROM usuarios WHERE username=%s", (usr_actual,))
            u_cfg = cur.fetchone()
            if u_cfg and u_cfg.get('tg_token') and u_cfg.get('tg_chat_id'):
                ok = enviar_telegram(u_cfg['tg_token'], u_cfg['tg_chat_id'], f"🟢 <b>El Minero Gris — Conexión Exitosa</b>\nLas alertas en tiempo real para la cuenta <b>{usr_actual}</b> están activas.")
                msg = "Mensaje de prueba enviado a tu Telegram." if ok else "Error al conectar con Telegram. Verifica tu Token y Chat ID."
            else:
                msg = "Primero guarda tu Bot Token y Chat ID arriba."
        elif accion == 'limpiar_historial':
            cur.execute("DELETE FROM registro_alertas WHERE owner=%s", (usr_actual,))
            msg = "Historial de incidentes limpiado."

    cur.execute("SELECT tg_token, tg_chat_id, umbral_temp, umbral_hr, alertas_activas FROM usuarios WHERE username=%s", (usr_actual,))
    row_u = cur.fetchone() or {}
    cfg = {
        'tg_token': row_u.get('tg_token') or '',
        'tg_chat_id': row_u.get('tg_chat_id') or '',
        'umbral_temp': row_u.get('umbral_temp') or 80,
        'umbral_hr': row_u.get('umbral_hr') or 50.0,
        'activas': bool(row_u.get('alertas_activas', 1))
    }

    cur.execute("SELECT * FROM registro_alertas WHERE owner=%s ORDER BY id DESC LIMIT 100", (usr_actual,))
    historial = cur.fetchall()
    return render_seguro('alertas.html', active_page='alertas', cfg=cfg, historial=historial, msg=msg)

# --- VISTA: /ajustes -> templates/ajustes.html ---
@app.route('/ajustes', methods=['GET', 'POST'])
@login_required
def ajustes():
    usr_actual = session.get('usuario', 'giovanni')
    msg = None
    con = get_db()
    con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'regenerar_key':
            nueva_k = generar_api_key()
            cur.execute("UPDATE usuarios SET api_key=%s WHERE username=%s", (nueva_k, usr_actual))
            msg = "Tu API Key ha sido regenerada con éxito."
        elif accion == 'guardar_red_agente':
            subnet = request.form.get('subnet', '192.168.0').strip()
            tarifa = a_float_seguro(request.form.get('tarifa_kwh'), 0.0)
            cur.execute("UPDATE usuarios SET subnet=%s, tarifa_kwh=%s WHERE username=%s", (subnet, tarifa, usr_actual))
            msg = "Preferencias de red y tarifa guardadas."
        elif accion == 'cambiar_password':
            p1 = request.form.get('nueva_pass', '')
            p2 = request.form.get('confirm_pass', '')
            if p1 and p1 == p2:
                cur.execute("UPDATE usuarios SET password=%s WHERE username=%s", (p1, usr_actual))
                msg = "Contraseña actualizada correctamente."
            else:
                msg = "Las contraseñas no coinciden."

    cur.execute("SELECT * FROM usuarios WHERE username=%s", (usr_actual,))
    u_data = cur.fetchone() or {}
    if not u_data.get('api_key'):
        nueva_k = generar_api_key()
        cur.execute("UPDATE usuarios SET api_key=%s WHERE username=%s", (nueva_k, usr_actual))
        u_data['api_key'] = nueva_k

    user_info = {
        'username': usr_actual,
        'plan': u_data.get('plan', 'Gratis'),
        'api_key': u_data.get('api_key', ''),
        'subnet': u_data.get('subnet') or '192.168.0',
        'tarifa_kwh': u_data.get('tarifa_kwh') or 0.0
    }
    return render_seguro('ajustes.html', active_page='ajustes', user=user_info, msg=msg)

@app.route('/descargar_agente')
@login_required
def descargar_agente():
    usr_actual = session.get('usuario', 'giovanni')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT api_key, subnet FROM usuarios WHERE username=%s", (usr_actual,))
    u = cur.fetchone() or {}
    api_k = u.get('api_key') or generar_api_key()
    sub = u.get('subnet') or '192.168.0'

    try:
        with open('agente.py', 'r', encoding='utf-8') as f:
            codigo = f.read()
        codigo = codigo.replace('API_KEY = "TU_API_KEY_AQUI"', f'API_KEY = "{api_k}"')
        codigo = codigo.replace('SUBNET = "192.168.0"', f'SUBNET = "{sub}"')
        return Response(codigo, mimetype="text/x-python", headers={"Content-Disposition": f"attachment;filename=agente_{usr_actual}.py"})
    except:
        return redirect('/ajustes')

@app.route('/plan')
@login_required
def plan():
    return render_seguro('plan.html', active_page='plan', current_plan=session.get('plan', 'Gratis'), saldo=session.get('saldo', 0.0))

@app.route('/granjas')
@login_required
def granjas():
    return render_seguro('granjas.html', active_page='granjas', titulo="🏭 Gestión de Granjas")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username'); p = request.form.get('password')
        con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM usuarios WHERE username=%s AND password=%s", (u, p))
        usr = cur.fetchone()
        if usr:
            session['usuario'] = u; session['plan'] = usr.get('plan', 'Gratis'); session['saldo'] = usr.get('saldo', 0.0)
            return redirect('/')
        return render_seguro('login.html', error='Credenciales incorrectas', mode='login')
    return render_seguro('login.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = request.form.get('username'); p = request.form.get('password')
        try:
            con = get_db(); con.autocommit = True; cur = con.cursor()
            cur.execute("INSERT INTO usuarios (username, password, plan, saldo, api_key) VALUES (%s, %s, 'Gratis', 0.0, %s)", (u, p, generar_api_key()))
            return render_seguro('login.html', success='Cuenta creada con API Key propia. Ahora inicia sesión.', mode='login')
        except: return render_seguro('login.html', error='El nombre de usuario ya está en uso', mode='register')
    return render_seguro('login.html', mode='register')

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/guardar_nombre', methods=['POST'])
@login_required
def guardar_nombre():
    usr_actual = session.get('usuario', 'giovanni')
    ip = request.form.get('ip')
    nombre = request.form.get('nombre')
    if ip and nombre:
        con = get_db(); con.autocommit = True; cur = con.cursor()
        cur.execute("INSERT INTO nombres_mineros (ip, owner, nombre) VALUES (%s, %s, %s) ON CONFLICT (ip) DO UPDATE SET nombre=EXCLUDED.nombre, owner=EXCLUDED.owner", (ip, usr_actual, nombre))
    return redirect(f'/minero/{ip}')

@app.route('/cmd_bulk', methods=['POST'])
@login_required
def cmd_bulk():
    usr_actual = session.get('usuario', 'giovanni')
    data = request.get_json(silent=True) or {}
    accion = data.get('accion')
    ips = data.get('ips', [])
    ordenes_custom = data.get('ordenes_custom', [])
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    con = get_db(); con.autocommit = True; cur = con.cursor()
    if ordenes_custom:
        for item in ordenes_custom:
            cur.execute("INSERT INTO cola_comandos (owner, ip, comando, estado, fecha) VALUES (%s, %s, %s, 'pendiente', %s)", (usr_actual, item['ip'], item['comando'], ahora))
    elif accion and ips:
        for ip in ips:
            cur.execute("INSERT INTO cola_comandos (owner, ip, comando, estado, fecha) VALUES (%s, %s, %s, 'pendiente', %s)", (usr_actual, ip, accion, ahora))
    return jsonify({"status": "ok"})

@app.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    usr_actual = session.get('usuario', 'giovanni')
    cmd_str = ""
    if accion in ['restart', 'reset', 'sleep', 'normal', 'blink', 'scan', 'get_log', 'clog', 'hlog', 'dlog'] or accion.startswith('profile_'): 
        cmd_str = accion.replace('_', '|') if accion.startswith('profile_') else accion
    elif accion == 'set_network':
        host = request.args.get('host', 'Antminer')
        proto = request.args.get('proto', '2')
        nip = request.args.get('nip', ip)
        mask = request.args.get('mask', '255.255.255.0')
        gw = request.args.get('gw', '192.168.0.1')
        dns = request.args.get('dns', '8.8.8.8')
        cmd_str = f"set_network|{host}|{proto}|{nip}|{mask}|{gw}|{dns}"
    elif accion == 'guardar_pools': 
        u1 = request.args.get('u1', ''); w1 = request.args.get('w1', ''); p1 = request.args.get('p1', '123')
        u2 = request.args.get('u2', ''); w2 = request.args.get('w2', ''); p2 = request.args.get('p2', '123')
        u3 = request.args.get('u3', ''); w3 = request.args.get('w3', ''); p3 = request.args.get('p3', '123')
        cmd_str = f"guardar_pools|{u1}|{w1}|{p1}|{u2}|{w2}|{p2}|{u3}|{w3}|{p3}"
        
    if cmd_str:
        con = get_db(); con.autocommit = True; cur = con.cursor()
        cur.execute("INSERT INTO cola_comandos (owner, ip, comando, estado, fecha) VALUES (%s, %s, %s, 'pendiente', %s)", (usr_actual, ip, cmd_str, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    return "OK"

@app.route('/exportar_csv')
@login_required
def exportar_csv():
    usr_actual = session.get('usuario', 'giovanni')
    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if usr_actual == 'giovanni':
        cur.execute("SELECT ip, modelo, hashrate, temp_max, consumo_w, estado FROM mineros_activos")
    else:
        cur.execute("SELECT ip, modelo, hashrate, temp_max, consumo_w, estado FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['IP', 'Modelo', 'Hashrate', 'Temp Max (C)', 'Consumo (W)', 'Estado'])
    for f in filas: writer.writerow([f['ip'], f['modelo'], f['hashrate'], f['temp_max'], f['consumo_w'], f['estado']])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=Reporte_El_Minero_Gris.csv"})

@app.route('/api/ver_log/<ip>')
def ver_log(ip):
    con = get_db(); cur = con.cursor()
    cur.execute("SELECT contenido FROM logs_mineros WHERE ip=%s", (ip,))
    log = cur.fetchone()
    return log[0] if log else "Esperando respuesta..."

@app.route('/api/recibir_logs', methods=['POST'])
def recibir_logs():
    data = request.get_json(silent=True)
    if data and 'ip' in data and 'log' in data:
        con = get_db(); con.autocommit = True; cur = con.cursor()
        cur.execute("INSERT INTO logs_mineros (ip, contenido, fecha) VALUES (%s, %s, %s) ON CONFLICT(ip) DO UPDATE SET contenido=EXCLUDED.contenido, fecha=EXCLUDED.fecha", (data['ip'], data['log'], datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    return "OK", 200

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    global ultimo_envio_alerta, ultima_limpieza_bd
    datos = request.get_json(silent=True)
    if not datos: return jsonify({"status": "error", "msg": "JSON invalido"}), 400
    
    # Detectar la IP pública de la granja desde donde se conecta el agente.py
    xff = request.headers.get('X-Forwarded-For', '')
    ip_publica_agente = xff.split(',')[0].strip() if xff else (request.remote_addr or '')
    geo_granja = obtener_ubicacion_ip_agente(ip_publica_agente)
    f_lat = geo_granja['lat']
    f_lon = geo_granja['lon']
    f_city = geo_granja['ciudad']
    f_country = geo_granja.get('pais', 'Bolivia')

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    api_key_recibida = str(datos.get('api_key') or '').strip()
    owner_user = 'giovanni'
    u_cfg = None
    if api_key_recibida and api_key_recibida != "TU_API_KEY_AQUI":
        cur.execute("SELECT * FROM usuarios WHERE api_key=%s", (api_key_recibida,))
        u_cfg = cur.fetchone()
        if u_cfg: owner_user = u_cfg['username']
    if not u_cfg:
        cur.execute("SELECT * FROM usuarios WHERE username='giovanni'")
        u_cfg = cur.fetchone()

    # Entregar comandos pendientes correspondientes a este propietario
    if owner_user == 'giovanni':
        cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente' AND (owner='giovanni' OR owner IS NULL)")
    else:
        cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente' AND owner=%s", (owner_user,))
    pend = cur.fetchall()
    ordenes = [{"ip": c['ip'], "comando": c['comando']} for c in pend]
    for c in pend: cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=%s", (c['id'],))

    ahora_dt = datetime.utcnow()
    ahora = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    # Redondeado al minuto exacto (:00) para guardar solo 1 punto por minuto en historial_nube
    ahora_minuto = ahora_dt.strftime("%Y-%m-%d %H:%M:00")
    ahora_ts = time.time()

    umbral_t = a_int_seguro(u_cfg.get('umbral_temp'), 80) if u_cfg else 80
    umbral_h = a_float_seguro(u_cfg.get('umbral_hr'), 50.0) if u_cfg else 50.0
    alertas_on = bool(u_cfg.get('alertas_activas', 1)) if u_cfg else True
    tg_tok = (u_cfg.get('tg_token') or '') if u_cfg else ''
    tg_cid = (u_cfg.get('tg_chat_id') or '') if u_cfg else ''

    guardados_ok = 0
    for m in datos.get('mineros', []):
        try:
            ip_m = str(m.get('ip') or '').strip()
            if not ip_m: continue
            mod_m = str(m.get('modelo') or 'Antminer')
            hr_m = a_float_seguro(m.get('hashrate'), 0.0)
            hr_30 = a_float_seguro(m.get('hashrate_30m'), hr_m)
            temp_m = a_int_seguro(m.get('temp'), 0)
            t_amb = a_int_seguro(m.get('temp_amb'), 0)
            cons_m = a_float_seguro(m.get('consumo'), 0.0)
            err = a_int_seguro(m.get('errores'), 0)
            unid = str(m.get('unidad') or 'TH/s')
            
            mc = str(m.get('mac') or '-')
            sr = str(m.get('serial') or '-')
            fw = str(m.get('firmware') or 'Desconocido')
            fs = str(m.get('fans') or '0 / 0 RPM')
            up = str(m.get('uptime') or 'En línea')
            
            p1 = str(m.get('pool1') or ''); w1 = str(m.get('worker1') or ''); pa1 = str(m.get('pass1') or '123')
            st1 = str(m.get('status1') or 'Alive'); ac1 = a_int_seguro(m.get('acc1'), 0); rj1 = a_int_seguro(m.get('rej1'), 0)
            p2 = str(m.get('pool2') or ''); w2 = str(m.get('worker2') or ''); pa2 = str(m.get('pass2') or '123')
            st2 = str(m.get('status2') or 'Alive'); ac2 = a_int_seguro(m.get('acc2'), 0); rj2 = a_int_seguro(m.get('rej2'), 0)
            p3 = str(m.get('pool3') or ''); w3 = str(m.get('worker3') or ''); pa3 = str(m.get('pass3') or '123')
            st3 = str(m.get('status3') or 'Dead'); ac3 = a_int_seguro(m.get('acc3'), 0); rj3 = a_int_seguro(m.get('rej3'), 0)
            
            host = str(m.get('hostname') or f"Antminer{ip_m.split('.')[-1]}")
            nproto = str(m.get('net_proto') or 'Static')
            nmask = str(m.get('netmask') or '255.255.255.0')
            ngw = str(m.get('gateway') or '192.168.0.1')
            ndns = str(m.get('dns') or '8.8.8.8')
            
            cadenas_list = m.get('cadenas', [])
            cadenas_json = json.dumps(cadenas_list)
            
            cur.execute('''INSERT INTO mineros_activos (ip, owner, modelo, unidad, hashrate, hashrate_30m, temp_max, temp_amb, consumo_w, estado, ultima_act, errores, pool1, worker1, pass1, pool2, worker2, pass2, pool3, worker3, pass3, mac, serial, firmware, fans, cadenas, uptime, status1, acc1, rej1, status2, acc2, rej2, status3, acc3, rej3, hostname, net_proto, netmask, gateway, dns, farm_lat, farm_lon, farm_city, farm_country) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'online', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(ip) DO UPDATE SET 
                            owner=EXCLUDED.owner, modelo=EXCLUDED.modelo, unidad=EXCLUDED.unidad, hashrate=EXCLUDED.hashrate, hashrate_30m=EXCLUDED.hashrate_30m, temp_max=EXCLUDED.temp_max, temp_amb=EXCLUDED.temp_amb, consumo_w=EXCLUDED.consumo_w, estado='online', ultima_act=EXCLUDED.ultima_act, errores=EXCLUDED.errores, pool1=EXCLUDED.pool1, worker1=EXCLUDED.worker1, pass1=EXCLUDED.pass1, pool2=EXCLUDED.pool2, worker2=EXCLUDED.worker2, pass2=EXCLUDED.pass2, pool3=EXCLUDED.pool3, worker3=EXCLUDED.worker3, pass3=EXCLUDED.pass3, mac=EXCLUDED.mac, serial=EXCLUDED.serial, firmware=EXCLUDED.firmware, fans=EXCLUDED.fans, cadenas=EXCLUDED.cadenas, uptime=EXCLUDED.uptime, status1=EXCLUDED.status1, acc1=EXCLUDED.acc1, rej1=EXCLUDED.rej1, status2=EXCLUDED.status2, acc2=EXCLUDED.acc2, rej2=EXCLUDED.rej2, status3=EXCLUDED.status3, acc3=EXCLUDED.acc3, rej3=EXCLUDED.rej3, hostname=EXCLUDED.hostname, net_proto=EXCLUDED.net_proto, netmask=EXCLUDED.netmask, gateway=EXCLUDED.gateway, dns=EXCLUDED.dns, farm_lat=EXCLUDED.farm_lat, farm_lon=EXCLUDED.farm_lon, farm_city=EXCLUDED.farm_city, farm_country=EXCLUDED.farm_country''', 
                        (ip_m, owner_user, mod_m, unid, hr_m, hr_30, temp_m, t_amb, cons_m, ahora, err, p1, w1, pa1, p2, w2, pa2, p3, w3, pa3, mc, sr, fw, fs, cadenas_json, up, st1, ac1, rj1, st2, ac2, rj2, st3, ac3, rj3, host, nproto, nmask, ngw, ndns, f_lat, f_lon, f_city, f_country))
            
            # Garantizar solo 1 registro por minuto en historial_nube
            cur.execute("SELECT id, hashrate, temp_max FROM historial_nube WHERE ip=%s AND fecha=%s LIMIT 1", (ip_m, ahora_minuto))
            row_min = cur.fetchone()
            if not row_min:
                cur.execute("INSERT INTO historial_nube (owner, ip, hashrate, temp_max, temp_amb, errores, cadenas, fecha) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                            (owner_user, ip_m, hr_m, temp_m, t_amb, err, cadenas_json, ahora_minuto))
            else:
                hr_prev = a_float_seguro(row_min.get('hashrate'), hr_m)
                hr_prom = round((hr_prev + hr_m) / 2.0, 2) if (hr_prev > 0 and hr_m > 0) else max(hr_prev, hr_m)
                t_max_min = max(a_int_seguro(row_min.get('temp_max'), 0), temp_m)
                cur.execute("UPDATE historial_nube SET hashrate=%s, temp_max=%s, temp_amb=%s, errores=%s, cadenas=%s WHERE id=%s",
                            (hr_prom, t_max_min, t_amb, err, cadenas_json, row_min['id']))

            guardados_ok += 1

            # Sistema de Alertas Automáticas (Temperatura Alta + Caída de Hashrate)
            if alertas_on:
                if temp_m >= umbral_t:
                    k_al = f"{owner_user}_{ip_m}_TEMP"
                    if ahora_ts - ultimo_envio_alerta.get(k_al, 0) > 1800:
                        ultimo_envio_alerta[k_al] = ahora_ts
                        msg_al = f"El equipo {mod_m} ({ip_m}) alcanzó {temp_m}°C (Límite: {umbral_t}°C)."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'TEMP', %s, %s)", (owner_user, ip_m, msg_al, ahora))
                        enviar_telegram(tg_tok, tg_cid, f"🔥 <b>ALERTA DE TEMPERATURA</b>\nMinero: <b>{ip_m}</b> ({mod_m})\nTemperatura: <b>{temp_m}°C</b>\nHashrate: {hr_m} {unid}")

                if umbral_h > 0 and hr_m < umbral_h and temp_m > 5:
                    k_hr = f"{owner_user}_{ip_m}_LOWHR"
                    if ahora_ts - ultimo_envio_alerta.get(k_hr, 0) > 1800:
                        ultimo_envio_alerta[k_hr] = ahora_ts
                        msg_hr = f"El equipo {mod_m} ({ip_m}) cayó a {hr_m} {unid} (Mínimo configurado: {umbral_h} {unid})."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'HASHRATE', %s, %s)", (owner_user, ip_m, msg_hr, ahora))
                        enviar_telegram(tg_tok, tg_cid, f"⚠️ <b>CAÍDA DE HASHRATE</b>\nMinero: <b>{ip_m}</b> ({mod_m})\nHashrate actual: <b>{hr_m} {unid}</b> (Umbral: {umbral_h} {unid})\nTemperatura: {temp_m}°C")
        except Exception as err_m:
            print(f"Error guardando minero {m.get('ip')}: {err_m}")

    # Limpieza automática de registros con más de 30 días y comandos viejos (1 vez por hora)
    if ahora_ts - ultima_limpieza_bd > 3600:
        ultima_limpieza_bd = ahora_ts
        try:
            fecha_limite_30d = (ahora_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            fecha_limite_cmd = (ahora_dt - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("DELETE FROM historial_nube WHERE fecha < %s", (fecha_limite_30d,))
            cur.execute("DELETE FROM registro_alertas WHERE fecha < %s", (fecha_limite_30d,))
            cur.execute("DELETE FROM cola_comandos WHERE estado='enviado' AND fecha < %s", (fecha_limite_cmd,))
        except Exception as err_clean:
            print(f"Error en limpieza automática 30d: {err_clean}")
    
    return jsonify({"status": "ok", "guardados": guardados_ok, "ordenes": ordenes}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
