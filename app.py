from flask import Flask, render_template, render_template_string, redirect, request, session, url_for, jsonify, Response, g
from werkzeug.security import generate_password_hash, check_password_hash
from jinja2 import TemplateNotFound
from functools import wraps
import psycopg2, psycopg2.extras, json, urllib.request, urllib.parse, time, calendar, csv, io, os, secrets, traceback, hashlib, zipfile, re
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get("SECRET_KEY", "super_secreta_antminer_tools")

try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

DB_URL = os.environ.get("DATABASE_URL", "")
LIMITE_MINEROS_GRATIS = 3
APK_VERSION_CODE = 1
APK_VERSION_NAME = "1.0"
ultima_limpieza_bd = 0
cache_geo_granja = {}
cache_clima_coord = {}
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

def es_hash_seguro(pass_str):
    s = str(pass_str or "")
    return s.startswith(("scrypt:", "pbkdf2:"))

def verificar_password(stored_pass, input_pass):
    if not stored_pass or not input_pass:
        return False
    if es_hash_seguro(stored_pass):
        return check_password_hash(stored_pass, input_pass)
    return stored_pass == input_pass

def es_cuenta_pro(username, plan_str):
    if str(username or '').lower() == 'admin':
        return True
    return str(plan_str or '').strip().lower() in ('pro', 'unlimited', 'enterprise')

def es_super_admin():
    return str(session.get('usuario') or '').lower() == 'admin' or str(session.get('admin_real') or '').lower() == 'admin'

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

def clave_granja_minero(ip, ciudad):
    c = str(ciudad or 'Santa Cruz de la Sierra').strip()
    sub = ".".join(str(ip).split('.')[:3]) + ".x" if '.' in str(ip) else "LAN"
    return f"{c} ({sub})"

def nombre_inteligente_minero(ip, nombres_dict, worker1=""):
    if ip in nombres_dict and nombres_dict[ip]:
        return nombres_dict[ip]
    w = str(worker1 or "").strip()
    if w and w != "-":
        return w.split('.')[-1] if '.' in w else w
    return ip.split('.')[-1]

def alerta_en_enfriamiento(cur, owner, ip, tipo, segundos=1800):
    cur.execute(
        "SELECT fecha FROM registro_alertas WHERE owner=%s AND ip=%s AND tipo=%s ORDER BY id DESC LIMIT 1",
        (owner, ip, tipo)
    )
    row = cur.fetchone()
    if not row or not row.get('fecha'):
        return False
    return calcular_segundos_desconectado(row['fecha']) < segundos

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

def obtener_clima_coordenadas(lat, lon):
    global cache_clima_coord
    key = f"{round(a_float_seguro(lat, -17.7833), 2)},{round(a_float_seguro(lon, -63.1821), 2)}"
    ahora = time.time()
    if key in cache_clima_coord and (ahora - cache_clima_coord[key]['ts'] < 900):
        return cache_clima_coord[key]['temp']
    try:
        lat_v, lon_v = key.split(',')
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat_v}&longitude={lon_v}&current=temperature_2m"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        raw = urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8')
        j = json.loads(raw)
        t_ext = j.get('current', {}).get('temperature_2m')
        if t_ext is not None:
            temp_c = round(float(t_ext), 1)
            cache_clima_coord[key] = {'temp': temp_c, 'ts': ahora}
            return temp_c
    except:
        pass
    return cache_clima_coord.get(key, {}).get('temp')

def ventilador_en_cero(fans_str, modelo_str):
    ml = str(modelo_str or "").lower()
    if any(w in ml for w in ("hyd", "hydro", "water", "m33", "m53", "m63")):
        return False
    fs = str(fans_str or "").strip()
    if not fs or "hydro" in fs.lower():
        return False
    nums = [int(x) for x in re.findall(r'\d+', fs)]
    if not nums:
        return False
    return all(n == 0 for n in nums)

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
        c.execute('''CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT, owner TEXT DEFAULT 'Admin', modelo TEXT, algoritmo TEXT DEFAULT 'SHA-256', moneda TEXT DEFAULT 'BTC', unidad TEXT DEFAULT 'TH/s', hashrate REAL, hashrate_30m REAL, temp_max INTEGER, temp_amb INTEGER DEFAULT 0, consumo_w REAL, estado TEXT, ultima_act TEXT, errores INTEGER, pool1 TEXT, worker1 TEXT, pass1 TEXT, pool2 TEXT, worker2 TEXT, pass2 TEXT, pool3 TEXT, worker3 TEXT, pass3 TEXT, mac TEXT, serial TEXT, firmware TEXT, fans TEXT, cadenas TEXT, uptime TEXT DEFAULT '0h 0m', status1 TEXT DEFAULT 'Alive', acc1 INTEGER DEFAULT 0, rej1 INTEGER DEFAULT 0, status2 TEXT DEFAULT 'Alive', acc2 INTEGER DEFAULT 0, rej2 INTEGER DEFAULT 0, status3 TEXT DEFAULT 'Dead', acc3 INTEGER DEFAULT 0, rej3 INTEGER DEFAULT 0, hostname TEXT DEFAULT 'Antminer', net_proto TEXT DEFAULT 'Static', netmask TEXT DEFAULT '255.255.255.0', gateway TEXT DEFAULT '192.168.0.1', dns TEXT DEFAULT '8.8.8.8', farm_lat REAL DEFAULT -17.7833, farm_lon REAL DEFAULT -63.1821, farm_city TEXT DEFAULT 'Santa Cruz de la Sierra', farm_country TEXT DEFAULT 'Bolivia', gps_manual INTEGER DEFAULT 0, corte_activo INTEGER DEFAULT 0, corte_temp INTEGER DEFAULT 85, watchdog_activo INTEGER DEFAULT 1, wd_fallo_desde TEXT DEFAULT '', wd_motivo_fallo TEXT DEFAULT '', wd_ultimo_reinicio TEXT DEFAULT '', wd_reinicios_seguidos INTEGER DEFAULT 0, wd_cuarentena INTEGER DEFAULT 0, placas_normales INTEGER DEFAULT 0, prot_sleep_desde TEXT DEFAULT '', prot_motivo TEXT DEFAULT '')''')
        c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, email TEXT DEFAULT '', fecha_registro TEXT DEFAULT '', suspendido INTEGER DEFAULT 0, plan TEXT DEFAULT 'Gratis', saldo REAL DEFAULT 0.0, api_key TEXT, subnet TEXT DEFAULT '192.168.0', tarifa_kwh REAL DEFAULT 0.0, tg_token TEXT DEFAULT '', tg_chat_id TEXT DEFAULT '', umbral_temp INTEGER DEFAULT 80, umbral_hr REAL DEFAULT 50.0, alertas_activas INTEGER DEFAULT 0, wd_activo INTEGER DEFAULT 0, wd_alcance TEXT DEFAULT 'all', wd_granjas TEXT DEFAULT '[]', wd_mineros TEXT DEFAULT '[]', wd_zero_hr INTEGER DEFAULT 0, wd_cold_on INTEGER DEFAULT 0, wd_cold_temp INTEGER DEFAULT 31, wd_amb_cold_on INTEGER DEFAULT 0, wd_amb_cold_temp INTEGER DEFAULT 18, wd_dead_board INTEGER DEFAULT 0, wd_gracia_min INTEGER DEFAULT 5, wd_cooldown_min INTEGER DEFAULT 30, wd_cuarentena_on INTEGER DEFAULT 0, wd_max_reinicios INTEGER DEFAULT 3, prot_activo INTEGER DEFAULT 0, prot_temp_on INTEGER DEFAULT 0, prot_temp_val INTEGER DEFAULT 85, prot_temp_accion TEXT DEFAULT 'sleep', prot_clima_on INTEGER DEFAULT 0, prot_clima_val INTEGER DEFAULT 38, prot_clima_accion TEXT DEFAULT 'sleep', prot_net_on INTEGER DEFAULT 0, prot_net_rej_pct REAL DEFAULT 5.0, prot_net_accion TEXT DEFAULT 'sleep', prot_fan_on INTEGER DEFAULT 0, prot_fan_accion TEXT DEFAULT 'sleep', prot_wakeup_on INTEGER DEFAULT 0, prot_wakeup_min INTEGER DEFAULT 30, mineros_omitidos INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS cola_comandos (id SERIAL PRIMARY KEY, owner TEXT DEFAULT 'Admin', ip TEXT, comando TEXT, estado TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS historial_nube (id SERIAL PRIMARY KEY, owner TEXT DEFAULT 'Admin', ip TEXT, hashrate REAL, temp_max INTEGER, temp_amb INTEGER DEFAULT 0, errores INTEGER DEFAULT 0, cadenas TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nombres_mineros (ip TEXT, owner TEXT DEFAULT 'Admin', nombre TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs_mineros (ip TEXT, owner TEXT DEFAULT 'Admin', contenido TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS registro_alertas (id SERIAL PRIMARY KEY, owner TEXT, ip TEXT, tipo TEXT, mensaje TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS movimientos_saldo (id SERIAL PRIMARY KEY, username TEXT, tipo TEXT, monto REAL, nota TEXT DEFAULT '', fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS config_global (clave TEXT PRIMARY KEY, valor TEXT DEFAULT '')''')

        columnas_nuevas = [
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS algoritmo TEXT DEFAULT 'SHA-256'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS moneda TEXT DEFAULT 'BTC'",
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
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS gps_manual INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS corte_activo INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS corte_temp INTEGER DEFAULT 85",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS watchdog_activo INTEGER DEFAULT 1",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_fallo_desde TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_motivo_fallo TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_ultimo_reinicio TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_reinicios_seguidos INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_cuarentena INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS placas_normales INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS prot_sleep_desde TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS prot_motivo TEXT DEFAULT ''",
            "ALTER TABLE cola_comandos ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS errores INTEGER DEFAULT 0",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS temp_amb INTEGER DEFAULT 0",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS cadenas TEXT",
            "ALTER TABLE nombres_mineros ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE logs_mineros ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS email TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS fecha_registro TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS suspendido INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS api_key TEXT",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS subnet TEXT DEFAULT '192.168.0'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tarifa_kwh REAL DEFAULT 0.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tg_token TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tg_chat_id TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS umbral_temp INTEGER DEFAULT 80",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS umbral_hr REAL DEFAULT 50.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS alertas_activas INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_activo INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_alcance TEXT DEFAULT 'all'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_granjas TEXT DEFAULT '[]'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_mineros TEXT DEFAULT '[]'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_zero_hr INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cold_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cold_temp INTEGER DEFAULT 31",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_amb_cold_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_amb_cold_temp INTEGER DEFAULT 18",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_dead_board INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_gracia_min INTEGER DEFAULT 5",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cooldown_min INTEGER DEFAULT 30",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cuarentena_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_max_reinicios INTEGER DEFAULT 3",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_activo INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_temp_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_temp_val INTEGER DEFAULT 85",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_temp_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_clima_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_clima_val INTEGER DEFAULT 38",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_clima_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_net_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_net_rej_pct REAL DEFAULT 5.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_net_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_fan_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_fan_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_wakeup_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_wakeup_min INTEGER DEFAULT 30",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS mineros_omitidos INTEGER DEFAULT 0"
        ]
        for col_sql in columnas_nuevas:
            try: c.execute(col_sql)
            except: pass

        c.execute("UPDATE mineros_activos SET owner='Admin' WHERE owner IS NULL OR owner=''")
        c.execute("UPDATE nombres_mineros SET owner='Admin' WHERE owner IS NULL OR owner=''")
        c.execute("UPDATE logs_mineros SET owner='Admin' WHERE owner IS NULL OR owner=''")
        c.execute("UPDATE historial_nube SET owner='Admin' WHERE owner IS NULL OR owner=''")

        # Migración automática y segura de 'giovanni' -> 'Admin'
        c.execute("SELECT id FROM usuarios WHERE LOWER(username)='giovanni'")
        row_old_adm = c.fetchone()
        c.execute("SELECT id FROM usuarios WHERE LOWER(username)='admin'")
        row_new_adm = c.fetchone()
        if row_old_adm and not row_new_adm:
            c.execute("UPDATE usuarios SET username='Admin', plan='Pro', suspendido=0 WHERE id=%s", (row_old_adm[0],))
        for tbl, col in [
            ('mineros_activos', 'owner'), ('nombres_mineros', 'owner'),
            ('logs_mineros', 'owner'), ('historial_nube', 'owner'),
            ('cola_comandos', 'owner'), ('registro_alertas', 'owner'),
            ('movimientos_saldo', 'username')
        ]:
            c.execute(f"UPDATE {tbl} SET {col}='Admin' WHERE LOWER({col})='giovanni'")

        hoy_str = datetime.utcnow().strftime("%Y-%m-%d")
        c.execute("UPDATE usuarios SET fecha_registro=%s WHERE fecha_registro IS NULL OR fecha_registro=''", (hoy_str,))

        migraciones_unique = [
            "ALTER TABLE mineros_activos DROP CONSTRAINT IF EXISTS mineros_activos_ip_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mineros_owner_ip ON mineros_activos (owner, ip)",
            "ALTER TABLE nombres_mineros DROP CONSTRAINT IF EXISTS nombres_mineros_ip_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_nombres_owner_ip ON nombres_mineros (owner, ip)",
            "ALTER TABLE logs_mineros DROP CONSTRAINT IF EXISTS logs_mineros_ip_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_owner_ip ON logs_mineros (owner, ip)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_username_lower ON usuarios (LOWER(username))"
        ]
        for m_sql in migraciones_unique:
            try: c.execute(m_sql)
            except: pass

        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_owner_ip_id ON historial_nube (owner, ip, id DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_owner_ip_fecha ON historial_nube (owner, ip, fecha)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_fecha ON historial_nube (fecha)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cola_estado_owner ON cola_comandos (estado, owner)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_alertas_owner_ip_tipo ON registro_alertas (owner, ip, tipo, id DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mov_saldo_user ON movimientos_saldo (username, id DESC)")

        c.execute("SELECT id FROM usuarios WHERE LOWER(username)='admin'")
        if not c.fetchone():
            c.execute("INSERT INTO usuarios (username, password, email, fecha_registro, plan, api_key) VALUES ('Admin', %s, '', %s, 'Pro', %s)",
                      (generate_password_hash('admin123'), hoy_str, generar_api_key()))
        else:
            c.execute("UPDATE usuarios SET username='Admin', plan='Pro', suspendido=0 WHERE LOWER(username)='admin'")

        c.execute("SELECT id, username FROM usuarios WHERE api_key IS NULL OR api_key = ''")
        for u_row in c.fetchall():
            c.execute("UPDATE usuarios SET api_key=%s WHERE id=%s", (generar_api_key(), u_row[0]))

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

@app.context_processor
def inyectar_globales():
    anuncio = ""
    omitidos_u = 0
    es_pro_u = False
    try:
        if 'usuario' in session and DB_URL:
            con = get_db()
            cur = con.cursor()
            cur.execute("SELECT valor FROM config_global WHERE clave='anuncio_global'")
            row = cur.fetchone()
            if row and row[0]:
                anuncio = str(row[0]).strip()
            cur.execute("SELECT plan, mineros_omitidos FROM usuarios WHERE username=%s", (session['usuario'],))
            urow = cur.fetchone()
            if urow:
                es_pro_u = es_cuenta_pro(session['usuario'], urow[0])
                omitidos_u = 0 if es_pro_u else a_int_seguro(urow[1], 0)
    except:
        pass
    return {
        'anuncio_global': anuncio,
        'en_modo_soporte': bool(str(session.get('admin_real') or '').lower() == 'admin'),
        'es_super_admin': es_super_admin(),
        'mineros_omitidos': omitidos_u,
        'es_pro_actual': es_pro_u,
        'limite_gratis': LIMITE_MINEROS_GRATIS
    }

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
        datos['red_ehs'] = round(datos['dificultad_t'] * 7.1583, 2)
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

def obtener_codigo_agente_configurado(api_k, sub):
    with open('agente.py', 'r', encoding='utf-8') as f:
        codigo_base = f.read()
    version_hash = hashlib.sha256(codigo_base.encode('utf-8')).hexdigest()[:12]
    codigo = codigo_base.replace('API_KEY = "TU_API_KEY_AQUI"', f'API_KEY = "{api_k}"')
    codigo = codigo.replace('SUBNET = "192.168.0"', f'SUBNET = "{sub}"')
    codigo = codigo.replace('VERSION_AGENTE = "LOCAL"', f'VERSION_AGENTE = "{version_hash}"')
    return codigo, version_hash

# --- MODO SOPORTE (ENTRAR COMO CLIENTE Y VOLVER A ADMIN) ---
@app.route('/admin/soporte/<int:uid>')
@login_required
def admin_entrar_soporte(uid):
    if not es_super_admin():
        return redirect('/')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username, plan, saldo FROM usuarios WHERE id=%s", (uid,))
    target = cur.fetchone()
    if not target:
        return redirect('/admin')
    session['admin_real'] = 'Admin'
    session['usuario'] = target['username']
    session['plan'] = target.get('plan', 'Gratis')
    session['saldo'] = target.get('saldo', 0.0)
    return redirect('/')

@app.route('/admin/salir_soporte')
@login_required
def admin_salir_soporte():
    if str(session.get('admin_real') or '').lower() != 'admin':
        return redirect('/')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT plan, saldo FROM usuarios WHERE LOWER(username)='admin'")
    adm = cur.fetchone() or {}
    session.pop('admin_real', None)
    session['usuario'] = 'Admin'
    session['plan'] = adm.get('plan', 'Pro')
    session['saldo'] = adm.get('saldo', 0.0)
    return redirect('/admin')

# --- VISTA: /admin (Centro de Control Super-Admin para Admin) ---
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_panel():
    if not es_super_admin():
        return redirect('/')
    msg = None
    con = get_db()
    con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    ahora_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'guardar_anuncio':
            txt_an = (request.form.get('anuncio_global') or '').strip()
            cur.execute("INSERT INTO config_global (clave, valor) VALUES ('anuncio_global', %s) ON CONFLICT(clave) DO UPDATE SET valor=EXCLUDED.valor", (txt_an,))
            msg = "Anuncio global actualizado." if txt_an else "Anuncio global desactivado."
        elif accion == 'limpiar_bd_manual':
            dias = max(1, min(30, a_int_seguro(request.form.get('dias_retencion'), 30)))
            fecha_lim = (datetime.utcnow() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("DELETE FROM historial_nube WHERE fecha < %s", (fecha_lim,))
            borrados_h = cur.rowcount
            cur.execute("DELETE FROM registro_alertas WHERE fecha < %s", (fecha_lim,))
            cur.execute("DELETE FROM cola_comandos WHERE estado='enviado' AND fecha < %s", (fecha_lim,))
            msg = f"Limpieza completada: {borrados_h:,} registros antiguos (> {dias} días) eliminados de historial_nube."
        elif accion == 'editar_usuario_completo':
            uid = a_int_seguro(request.form.get('user_id'), 0)
            cur.execute("SELECT * FROM usuarios WHERE id=%s", (uid,))
            target = cur.fetchone()
            if target:
                old_user = target['username']
                es_target_admin = (old_user.lower() == 'admin')
                new_user = 'Admin' if es_target_admin else (request.form.get('username') or old_user).strip()
                new_email = (request.form.get('email') or '').strip().lower()
                new_pass = (request.form.get('nueva_password') or '').strip()
                new_plan = 'Pro' if es_target_admin else (request.form.get('plan') or 'Gratis')
                new_susp = 0 if es_target_admin else (1 if request.form.get('suspendido') == '1' else 0)

                try:
                    if new_user != old_user and new_user:
                        cur.execute("SELECT id FROM usuarios WHERE LOWER(username)=LOWER(%s) AND id != %s", (new_user, uid))
                        if cur.fetchone():
                            raise Exception("Ese nombre de usuario ya está en uso.")
                        cur.execute("UPDATE usuarios SET username=%s WHERE id=%s", (new_user, uid))
                        for tbl, col in [('mineros_activos', 'owner'), ('nombres_mineros', 'owner'),
                                         ('logs_mineros', 'owner'), ('historial_nube', 'owner'),
                                         ('cola_comandos', 'owner'), ('registro_alertas', 'owner'),
                                         ('movimientos_saldo', 'username')]:
                            cur.execute(f"UPDATE {tbl} SET {col}=%s WHERE {col}=%s", (new_user, old_user))

                    if new_pass:
                        cur.execute("UPDATE usuarios SET email=%s, plan=%s, suspendido=%s, password=%s WHERE id=%s",
                                    (new_email, new_plan, new_susp, generate_password_hash(new_pass), uid))
                    else:
                        cur.execute("UPDATE usuarios SET email=%s, plan=%s, suspendido=%s WHERE id=%s",
                                    (new_email, new_plan, new_susp, uid))
                    msg = f"Cuenta '{new_user}' actualizada correctamente."
                except Exception as e_upd:
                    msg = f"No se pudo cambiar el nombre (posiblemente ya existe): {e_upd}"
        elif accion == 'movimiento_saldo':
            uid = a_int_seguro(request.form.get('user_id'), 0)
            tipo_mov = request.form.get('tipo_mov', 'deposito')
            monto = abs(a_float_seguro(request.form.get('monto'), 0.0))
            nota = (request.form.get('nota') or '').strip()
            cur.execute("SELECT username, saldo FROM usuarios WHERE id=%s", (uid,))
            target = cur.fetchone()
            if target and monto > 0:
                delta = -monto if tipo_mov in ('gasto', 'retiro') else monto
                nuevo_saldo = round(a_float_seguro(target.get('saldo'), 0.0) + delta, 2)
                cur.execute("UPDATE usuarios SET saldo=%s WHERE id=%s", (nuevo_saldo, uid))
                cur.execute("INSERT INTO movimientos_saldo (username, tipo, monto, nota, fecha) VALUES (%s, %s, %s, %s, %s)",
                            (target['username'], tipo_mov, delta, nota, ahora_str))
                if target['username'] == session.get('usuario'):
                    session['saldo'] = nuevo_saldo
                signo = "+" if delta >= 0 else "-"
                msg = f"Saldo de '{target['username']}' actualizado ({signo}${abs(delta):.2f} USDT). Nuevo saldo: ${nuevo_saldo:.2f} USDT."
        elif accion == 'eliminar_usuario':
            uid = a_int_seguro(request.form.get('user_id'), 0)
            cur.execute("SELECT username FROM usuarios WHERE id=%s", (uid,))
            target = cur.fetchone()
            if target and target['username'].lower() != 'admin':
                cur.execute("DELETE FROM usuarios WHERE id=%s", (uid,))
                msg = f"Cuenta '{target['username']}' eliminada."

    # 1. Todos los mineros para estadísticas globales, mapa mundial y minado por cliente
    cur.execute("SELECT owner, ip, hashrate, temp_max, unidad, algoritmo, ultima_act, farm_lat, farm_lon, farm_city, farm_country, wd_cuarentena FROM mineros_activos")
    todos_mineros = cur.fetchall()
    datos_red = obtener_datos_red()

    mapa_granjas_dict = {}
    stats_por_usuario = {}
    mineros_online_global = 0
    mineros_offline_global = 0
    hashrate_global = 0.0

    for m in todos_mineros:
        ow = m.get('owner') or 'Admin'
        if ow not in stats_por_usuario:
            stats_por_usuario[ow] = {'total': 0, 'online': 0, 'hr_btc': 0.0, 'gen_dia': 0.0}
        stats_por_usuario[ow]['total'] += 1

        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_on = segs < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_on else 0.0
        tm = a_int_seguro(m.get('temp_max'), 0) if es_on else 0
        unid = m.get('unidad') or 'TH/s'
        algo = m.get('algoritmo') or 'SHA-256'

        if es_on:
            mineros_online_global += 1
            stats_por_usuario[ow]['online'] += 1
            if unid == 'TH/s' and algo == 'SHA-256':
                hashrate_global += hr
                stats_por_usuario[ow]['hr_btc'] += hr
                if datos_red['dificultad_t'] > 0:
                    b_dia = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296)
                    stats_por_usuario[ow]['gen_dia'] += b_dia * datos_red['precio']
        else:
            mineros_offline_global += 1

        ciudad = str(m.get('farm_city') or 'Santa Cruz de la Sierra').strip()
        subred = ".".join(str(m.get('ip') or '').split('.')[:3]) + ".x"
        g_key = f"{ow}|{ciudad}|{subred}"
        if g_key not in mapa_granjas_dict:
            mapa_granjas_dict[g_key] = {
                'owner': ow, 'ciudad': ciudad, 'pais': str(m.get('farm_country') or ''),
                'subred': subred, 'lat': a_float_seguro(m.get('farm_lat'), -17.7833),
                'lon': a_float_seguro(m.get('farm_lon'), -63.1821),
                'total': 0, 'online': 0, 'offline': 0, 'hashrate': 0.0, 'temp_max': 0, 'cuarentena': 0
            }
        gd = mapa_granjas_dict[g_key]
        gd['total'] += 1
        if es_on:
            gd['online'] += 1
            gd['hashrate'] = round(gd['hashrate'] + hr, 2)
            gd['temp_max'] = max(gd['temp_max'], tm)
        else:
            gd['offline'] += 1
        if a_int_seguro(m.get('wd_cuarentena'), 0) == 1:
            gd['cuarentena'] += 1

    mapa_granjas = []
    for gd in mapa_granjas_dict.values():
        if gd['offline'] > 0 or gd['temp_max'] >= 85 or gd['cuarentena'] > 0:
            gd['color'] = '#ff5252'
            gd['estado_txt'] = 'Atención / Offline'
        elif gd['temp_max'] >= 75:
            gd['color'] = '#ffb300'
            gd['estado_txt'] = 'Temperatura Alta'
        else:
            gd['color'] = '#00e676'
            gd['estado_txt'] = 'Óptima'
        mapa_granjas.append(gd)

    # 2. Movimientos de saldo (depósitos totales y gasto del mes actual)
    mes_actual = datetime.utcnow().strftime("%Y-%m")
    cur.execute("SELECT * FROM movimientos_saldo ORDER BY id DESC LIMIT 60")
    movimientos = cur.fetchall()

    cur.execute("""
        SELECT username,
               COALESCE(SUM(CASE WHEN monto > 0 THEN monto ELSE 0 END), 0) AS total_dep,
               COALESCE(SUM(CASE WHEN monto < 0 AND fecha LIKE %s THEN ABS(monto) ELSE 0 END), 0) AS gasto_mes
        FROM movimientos_saldo
        GROUP BY username
    """, (mes_actual + '%',))
    fin_por_usuario = {r['username']: r for r in cur.fetchall()}

    # 3. Lista de usuarios enriquecida
    cur.execute("SELECT id, username, email, fecha_registro, suspendido, plan, saldo, subnet FROM usuarios ORDER BY id ASC")
    usuarios_raw = cur.fetchall()
    usuarios = []
    for u in usuarios_raw:
        un = u['username']
        sm = stats_por_usuario.get(un, {'total': 0, 'online': 0, 'hr_btc': 0.0, 'gen_dia': 0.0})
        fm = fin_por_usuario.get(un, {'total_dep': 0.0, 'gasto_mes': 0.0})
        u['total_mineros'] = sm['total']
        u['online_mineros'] = sm['online']
        u['hashrate_btc'] = round(sm['hr_btc'], 1)
        u['gen_dia'] = round(sm['gen_dia'], 2)
        u['gen_mes'] = round(sm['gen_dia'] * 30.0, 2)
        u['total_dep'] = round(a_float_seguro(fm.get('total_dep'), 0.0), 2)
        u['gasto_mes'] = round(a_float_seguro(fm.get('gasto_mes'), 0.0), 2)
        usuarios.append(u)

    # 4. Tamaño de BD y filas de historial_nube
    db_mb, hist_filas = 0.0, 0
    try:
        cur.execute("SELECT pg_database_size(current_database()) AS db_bytes")
        row_sz = cur.fetchone()
        if row_sz and row_sz.get('db_bytes'):
            db_mb = round(float(row_sz['db_bytes']) / (1024.0 * 1024.0), 2)
        cur.execute("SELECT COUNT(*) AS cnt FROM historial_nube")
        row_cnt = cur.fetchone()
        if row_cnt:
            hist_filas = int(row_cnt.get('cnt') or 0)
    except Exception as e:
        print(f"Error leyendo métricas BD: {e}")

    cur.execute("SELECT valor FROM config_global WHERE clave='anuncio_global'")
    row_an = cur.fetchone()
    anuncio_actual = (row_an.get('valor') or '') if row_an else ''

    stats = {
        'usuarios': len(usuarios),
        'pro': sum(1 for u in usuarios if es_cuenta_pro(u['username'], u['plan'])),
        'granjas': len(mapa_granjas),
        'mineros': len(todos_mineros),
        'mineros_online': mineros_online_global,
        'mineros_offline': mineros_offline_global,
        'hashrate_global': round(hashrate_global, 1),
        'saldo_total': sum(a_float_seguro(u['saldo'], 0.0) for u in usuarios),
        'db_mb': db_mb,
        'db_pct': min(100.0, round((db_mb / 1024.0) * 100.0, 1)),
        'hist_filas': hist_filas
    }
    return render_seguro(
        'admin.html', active_page='admin', usuarios=usuarios, stats=stats,
        movimientos=movimientos, mapa_granjas_json=json.dumps(mapa_granjas),
        anuncio_actual=anuncio_actual, msg=msg
    )

# --- API: Eliminar mineros (liberar cupo o borrar equipos offline/fantasma) ---
@app.route('/api/eliminar_mineros', methods=['POST'])
@login_required
def api_eliminar_mineros():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    ips = data.get('ips', [])
    if not ips or not isinstance(ips, list):
        return jsonify({"status": "error", "msg": "No se recibieron IPs"}), 400
    con = get_db(); con.autocommit = True; cur = con.cursor()
    eliminados = 0
    for ip in ips:
        ip_clean = str(ip or '').strip()
        if not ip_clean:
            continue
        cur.execute("DELETE FROM mineros_activos WHERE owner=%s AND ip=%s", (usr_actual, ip_clean))
        eliminados += cur.rowcount
        cur.execute("DELETE FROM nombres_mineros WHERE owner=%s AND ip=%s", (usr_actual, ip_clean))
        cur.execute("DELETE FROM logs_mineros WHERE owner=%s AND ip=%s", (usr_actual, ip_clean))
        cur.execute("DELETE FROM cola_comandos WHERE owner=%s AND ip=%s", (usr_actual, ip_clean))
    return jsonify({"status": "ok", "eliminados": eliminados})

# --- API: Sincronizar tarifa eléctrica desde cualquier página ---
@app.route('/api/guardar_tarifa', methods=['POST'])
@login_required
def api_guardar_tarifa():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    tarifa = max(0.0, a_float_seguro(data.get('tarifa_kwh'), 0.0))
    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("UPDATE usuarios SET tarifa_kwh=%s WHERE username=%s", (tarifa, usr_actual))
    return jsonify({"status": "ok", "tarifa_kwh": tarifa})

# --- API: Guardar Corte Térmico individual por minero (OFF por defecto) ---
@app.route('/api/guardar_corte_termico', methods=['POST'])
@login_required
def api_guardar_corte_termico():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    ip = str(data.get('ip') or '').strip()
    activo = 1 if a_int_seguro(data.get('activo'), 0) == 1 else 0
    temp = max(60, min(105, a_int_seguro(data.get('temp'), 85)))
    if not ip:
        return jsonify({"status": "error"}), 400
    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("UPDATE mineros_activos SET corte_activo=%s, corte_temp=%s WHERE owner=%s AND ip=%s", (activo, temp, usr_actual, ip))
    return jsonify({"status": "ok", "activo": activo, "temp": temp})

# --- API: Activar / Excluir Watchdog individual en un minero ---
@app.route('/api/guardar_watchdog_minero', methods=['POST'])
@login_required
def api_guardar_watchdog_minero():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    ip = str(data.get('ip') or '').strip()
    activo = 1 if a_int_seguro(data.get('activo'), 1) == 1 else 0
    if not ip:
        return jsonify({"status": "error"}), 400
    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("UPDATE mineros_activos SET watchdog_activo=%s, wd_fallo_desde='' WHERE owner=%s AND ip=%s", (activo, usr_actual, ip))
    return jsonify({"status": "ok", "activo": activo})

# --- API: Guardar GPS manual de la granja sin que se sobrescriba ---
@app.route('/api/guardar_gps', methods=['POST'])
@login_required
def api_guardar_gps():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    lat = a_float_seguro(data.get('lat'), None)
    lon = a_float_seguro(data.get('lon'), None)
    ciudad = str(data.get('ciudad') or '').strip() or 'Granja Local'
    pais = str(data.get('pais') or '').strip() or 'Bolivia'
    if lat is None or lon is None:
        return jsonify({"status": "error", "msg": "Coordenadas inválidas"}), 400
    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute(
        "UPDATE mineros_activos SET farm_lat=%s, farm_lon=%s, farm_city=%s, farm_country=%s, gps_manual=1 WHERE owner=%s",
        (lat, lon, ciudad, pais, usr_actual)
    )
    return jsonify({"status": "ok", "lat": lat, "lon": lon, "ciudad": ciudad, "pais": pais})

# --- VISTA: / y /resumen -> templates/resumen.html ---
@app.route('/')
@app.route('/resumen')
@login_required
def index():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT tarifa_kwh, plan, saldo FROM usuarios WHERE username=%s", (usr_actual,))
    u_row = cur.fetchone() or {}
    tarifa_kwh = a_float_seguro(u_row.get('tarifa_kwh'), 0.0)
    session['plan'] = u_row.get('plan', session.get('plan', 'Gratis'))
    session['saldo'] = u_row.get('saldo', session.get('saldo', 0.0))

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    mineros = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    datos_red = obtener_datos_red()
    registros, en_linea, offline = [], 0, 0
    ht, kt, gt, cw, tm, ig, gan_neta_total = 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0

    for m in mineros:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        algo = m.get('algoritmo') or ('Equihash' if 'z15' in str(mod).lower() else 'SHA-256')
        mon = m.get('moneda') or ('ZEC' if algo == 'Equihash' else 'BTC')
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre_custom = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
        hr = a_float_seguro(m.get('hashrate'), 0.0)
        t = a_int_seguro(m.get('temp_max'), 0)
        c = a_float_seguro(m.get('consumo_w'), 0.0)

        segs_desconectado = calcular_segundos_desconectado(m.get('ultima_act'))
        eficiencia_val = round(c / hr, 2) if hr > 0 else 0
        if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)

        if segs_desconectado < 300:
            en_linea += 1
            if unidad == 'TH/s' and algo == 'SHA-256': ht += hr
            elif unidad == 'kSol/s': kt += hr
            elif unidad == 'GH/s': gt += hr

            cw += c; tm = max(tm, t)
            if unidad == 'TH/s' and algo == 'SHA-256' and datos_red['dificultad_t'] > 0:
                b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296)
                ib = b * datos_red['precio']
                costo_elec = (c / 1000.0) * 24.0 * tarifa_kwh
                neto = ib - costo_elec
                ig += ib
                gan_neta_total += neto
                str_gan = f"-USD {abs(neto):.2f}" if neto < 0 else f"{neto:.2f} USD"
            else:
                str_gan = f"Pool {mon}"

            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'algoritmo': algo, 'moneda': mon,
                'unidad': unidad, 'unidad_efi': unidad_efi,
                'estado': '<span style="color:var(--accent-green);">● En línea</span>',
                'tiempo_act': f'hace {int(segs_desconectado)}s', 'hashrate': hr,
                'hashrate_30m': a_float_seguro(m.get('hashrate_30m'), hr),
                'temp_num': t, 'consumo_kw': f"{round(c/1000, 2)} kW",
                'eficiencia': eficiencia_val, 'ganancia_neta': str_gan
            })
        else:
            offline += 1
            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'algoritmo': algo, 'moneda': mon,
                'unidad': unidad, 'unidad_efi': unidad_efi,
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
        'tarifa_kwh': tarifa_kwh,
        'precio_btc_raw': datos_red['precio'],
        'precio_btc': f"{datos_red['precio']:,.2f}",
        'dificultad_t': round(datos_red['dificultad_t'], 2),
        'recompensa_bloque': datos_red['recompensa'],
        'red_ehs': datos_red['red_ehs'],
        'ingreso_dia': f"{ig:.2f}",
        'ganancia_dia': f"-USD {abs(gan_neta_total):.2f}" if gan_neta_total < 0 else f"{gan_neta_total:.2f} USD"
    }
    return render_seguro('resumen.html', active_page='resumen', registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=flota)

# --- VISTA: /minero/<ip> -> templates/minero.html ---
@app.route('/minero/<ip>')
@login_required
def detalle_minero(ip):
    usr_actual = session.get('usuario', 'Admin')
    plan_actual = session.get('plan', 'Gratis')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s AND ip=%s", (usr_actual, ip))
    m = cur.fetchone()
    if not m: return redirect('/')

    cur.execute("SELECT nombre FROM nombres_mineros WHERE owner=%s AND ip=%s", (usr_actual, ip))
    n_row = cur.fetchone()
    nombres_dict = {ip: n_row['nombre']} if n_row and n_row.get('nombre') else {}

    cur.execute("SELECT ip FROM mineros_activos WHERE owner=%s AND ip != %s", (usr_actual, ip))
    ips_ocupadas = [row['ip'] for row in cur.fetchall()]

    limite_hist = 44000 if es_cuenta_pro(usr_actual, plan_actual) else 1440
    cur.execute("""
        SELECT fecha, hashrate, temp_max, temp_amb, errores, cadenas 
        FROM (
            SELECT id, fecha, hashrate, temp_max, temp_amb, errores, cadenas 
            FROM historial_nube 
            WHERE owner=%s AND ip=%s 
            ORDER BY id DESC 
            LIMIT %s
        ) sub 
        ORDER BY id ASC
    """, (usr_actual, ip, limite_hist))
    hist = cur.fetchall()

    mod_str = str(m.get('modelo') or 'Antminer')
    algoritmo_val = str(m.get('algoritmo') or ('Equihash' if 'z15' in mod_str.lower() else 'SHA-256'))
    moneda_val = str(m.get('moneda') or ('ZEC' if algoritmo_val == 'Equihash' else 'BTC'))
    unidad_hr = str(m.get('unidad') or ('kSol/s' if 'z15' in mod_str.lower() else 'TH/s'))
    unidad_efi = "J/kSol" if unidad_hr == "kSol/s" else ("J/GH" if unidad_hr == "GH/s" else "J/TH")

    nombre_minero = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
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

    farm_lat = a_float_seguro(m.get('farm_lat'), -17.7833)
    farm_lon = a_float_seguro(m.get('farm_lon'), -63.1821)
    farm_city = str(m.get('farm_city') or 'Santa Cruz de la Sierra')
    farm_country = str(m.get('farm_country') or 'Bolivia')

    corte_activo = bool(a_int_seguro(m.get('corte_activo'), 0) == 1)
    corte_temp = a_int_seguro(m.get('corte_temp'), 85)
    watchdog_activo = bool(a_int_seguro(m.get('watchdog_activo'), 1) == 1)
    wd_cuarentena = bool(a_int_seguro(m.get('wd_cuarentena'), 0) == 1)

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
        for h in hist:
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
        'algoritmo': algoritmo_val, 'moneda': moneda_val,
        'temp_max': t_val, 'temp_amb': t_amb_val,
        'corte_activo': corte_activo, 'corte_temp': corte_temp,
        'watchdog_activo': watchdog_activo, 'wd_cuarentena': wd_cuarentena,
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

    return render_seguro(
        'minero.html', active_page='mineros', ip=ip, nombre=nombre_minero, modelo=mod_str,
        ultimo=dt, extra=extra, dispositivo=disp, offline=offline,
        graf_fechas_raw=json.dumps(f_raw), graf_hashrate=json.dumps(hr_g),
        graf_temps=json.dumps(tp_g), graf_temp_amb=json.dumps(tamb_g),
        graf_errores=json.dumps(err_g), cadenas_json=json.dumps(cadenas),
        graf_cadenas_hist=json.dumps(cad_g), ips_ocupadas=json.dumps(ips_ocupadas)
    )

# --- VISTA: /mineros -> templates/mineros.html ---
@app.route('/mineros')
@login_required
def mineros():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    lista_mineros = []
    contadores = {'total': len(filas), 'online': 0, 'offline': 0, 'alerta_temp': 0, 'alerta_hw': 0}

    for m in filas:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
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
            'ip': ip, 'ultimo_octeto': ip.split('.')[-1], 'nombre': nombre, 'modelo': mod,
            'algoritmo': m.get('algoritmo') or 'SHA-256', 'moneda': m.get('moneda') or 'BTC',
            'mac': m.get('mac') or '-', 'firmware': m.get('firmware') or '-', 'online': es_online,
            'uptime': (m.get('uptime') or 'En línea') if es_online else f'Offline ({int(segs // 60)}m)',
            'hashrate': hr, 'hashrate_30m': hr_30m, 'unidad': unidad, 'unidad_efi': unidad_efi,
            'eficiencia': efi, 'consumo_kw': round(cons_w / 1000.0, 2), 'temp_max': t_max, 'temp_amb': t_amb,
            'errores': err_hw, 'fans': m.get('fans') or '0 / 0 RPM', 'placas_activas': placas_activas,
            'chips_totales': chips_totales, 'pool1': m.get('pool1') or '-', 'worker1': m.get('worker1') or '-',
            'pass1': m.get('pass1') or '123', 'pool2': m.get('pool2') or '', 'worker2': m.get('worker2') or '',
            'pass2': m.get('pass2') or '123', 'pool3': m.get('pool3') or '', 'worker3': m.get('worker3') or '',
            'pass3': m.get('pass3') or '123', 'acc1': acc1, 'rej1': rej1, 'pct_rej': pct_rej
        })

    return render_seguro('mineros.html', active_page='mineros', mineros=lista_mineros, stats=contadores)

# --- VISTA: /granjas -> templates/granjas.html ---
@app.route('/granjas')
@login_required
def granjas():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    grupos = {}
    for m in filas:
        ip = m['ip']
        ciudad = str(m.get('farm_city') or 'Santa Cruz de la Sierra')
        subred = ".".join(ip.split('.')[:3]) + ".x" if '.' in ip else "LAN"
        clave = clave_granja_minero(ip, ciudad)

        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_online = segs < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_online else 0.0
        cw = a_float_seguro(m.get('consumo_w'), 0.0) if es_online else 0.0
        tm = a_int_seguro(m.get('temp_max'), 0) if es_online else 0

        if clave not in grupos:
            grupos[clave] = {
                'id': clave.lower().replace(' ', '_').replace('(', '').replace(')', '').replace('.', '_'),
                'clave': clave,
                'nombre': f"Granja {ciudad}", 'ciudad': ciudad, 'subred': subred,
                'total': 0, 'online': 0, 'offline': 0,
                'hashrate': 0.0, 'consumo_kw': 0.0, 'temp_max': 0, 'mineros': []
            }

        g = grupos[clave]
        g['total'] += 1
        if es_online:
            g['online'] += 1
            g['hashrate'] = round(g['hashrate'] + hr, 2)
            g['consumo_kw'] = round(g['consumo_kw'] + (cw / 1000.0), 2)
            g['temp_max'] = max(g['temp_max'], tm)
        else:
            g['offline'] += 1

        g['mineros'].append({
            'ip': ip, 'nombre': nombre_inteligente_minero(ip, nombres_dict, m.get('worker1')),
            'modelo': m.get('modelo') or 'Antminer', 'online': es_online,
            'hashrate': hr, 'unidad': m.get('unidad') or 'TH/s', 'temp_max': tm
        })

    return render_seguro('granjas.html', active_page='granjas', granjas=list(grupos.values()))

# --- VISTA: /alertas -> templates/alertas.html ---
@app.route('/alertas', methods=['GET', 'POST'])
@login_required
def alertas():
    usr_actual = session.get('usuario', 'Admin')
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
            msg = "Configuración de Telegram y umbrales de alerta guardada."
        elif accion == 'guardar_automatizacion':
            wd_activo = 1 if request.form.get('wd_activo') == '1' else 0
            wd_alcance = request.form.get('wd_alcance', 'all')
            if wd_alcance not in ('all', 'farms', 'miners'):
                wd_alcance = 'all'
            wd_granjas = json.dumps(request.form.getlist('wd_granjas'))
            wd_mineros = json.dumps(request.form.getlist('wd_mineros'))

            wd_zero_hr = 1 if request.form.get('wd_zero_hr') == '1' else 0
            wd_cold_on = 1 if request.form.get('wd_cold_on') == '1' else 0
            wd_cold_temp = max(15, min(60, a_int_seguro(request.form.get('wd_cold_temp'), 31)))
            wd_amb_cold_on = 1 if request.form.get('wd_amb_cold_on') == '1' else 0
            wd_amb_cold_temp = max(5, min(45, a_int_seguro(request.form.get('wd_amb_cold_temp'), 18)))
            wd_dead_board = 1 if request.form.get('wd_dead_board') == '1' else 0
            wd_gracia_min = max(1, min(60, a_int_seguro(request.form.get('wd_gracia_min'), 5)))
            wd_cooldown_min = max(5, min(180, a_int_seguro(request.form.get('wd_cooldown_min'), 30)))
            wd_cuarentena_on = 1 if request.form.get('wd_cuarentena_on') == '1' else 0
            wd_max_reinicios = max(1, min(10, a_int_seguro(request.form.get('wd_max_reinicios'), 3)))

            prot_activo = 1 if request.form.get('prot_activo') == '1' else 0
            prot_temp_on = 1 if request.form.get('prot_temp_on') == '1' else 0
            prot_temp_val = max(60, min(105, a_int_seguro(request.form.get('prot_temp_val'), 85)))
            prot_temp_accion = request.form.get('prot_temp_accion', 'sleep')
            if prot_temp_accion not in ('sleep', 'profile_low'): prot_temp_accion = 'sleep'

            prot_clima_on = 1 if request.form.get('prot_clima_on') == '1' else 0
            prot_clima_val = max(20, min(55, a_int_seguro(request.form.get('prot_clima_val'), 38)))
            prot_clima_accion = request.form.get('prot_clima_accion', 'sleep')
            if prot_clima_accion not in ('sleep', 'profile_low'): prot_clima_accion = 'sleep'

            prot_net_on = 1 if request.form.get('prot_net_on') == '1' else 0
            prot_net_rej_pct = max(0.5, min(50.0, a_float_seguro(request.form.get('prot_net_rej_pct'), 5.0)))
            prot_net_accion = request.form.get('prot_net_accion', 'sleep')
            if prot_net_accion not in ('sleep', 'restart'): prot_net_accion = 'sleep'

            prot_fan_on = 1 if request.form.get('prot_fan_on') == '1' else 0
            prot_fan_accion = request.form.get('prot_fan_accion', 'sleep')
            if prot_fan_accion not in ('sleep', 'restart'): prot_fan_accion = 'sleep'

            prot_wakeup_on = 1 if request.form.get('prot_wakeup_on') == '1' else 0
            prot_wakeup_min = max(5, min(360, a_int_seguro(request.form.get('prot_wakeup_min'), 30)))

            cur.execute("""
                UPDATE usuarios
                SET wd_activo=%s, wd_alcance=%s, wd_granjas=%s, wd_mineros=%s,
                    wd_zero_hr=%s, wd_cold_on=%s, wd_cold_temp=%s,
                    wd_amb_cold_on=%s, wd_amb_cold_temp=%s, wd_dead_board=%s,
                    wd_gracia_min=%s, wd_cooldown_min=%s, wd_cuarentena_on=%s, wd_max_reinicios=%s,
                    prot_activo=%s, prot_temp_on=%s, prot_temp_val=%s, prot_temp_accion=%s,
                    prot_clima_on=%s, prot_clima_val=%s, prot_clima_accion=%s,
                    prot_net_on=%s, prot_net_rej_pct=%s, prot_net_accion=%s,
                    prot_fan_on=%s, prot_fan_accion=%s, prot_wakeup_on=%s, prot_wakeup_min=%s
                WHERE username=%s
            """, (wd_activo, wd_alcance, wd_granjas, wd_mineros, wd_zero_hr, wd_cold_on, wd_cold_temp,
                  wd_amb_cold_on, wd_amb_cold_temp, wd_dead_board, wd_gracia_min, wd_cooldown_min,
                  wd_cuarentena_on, wd_max_reinicios, prot_activo, prot_temp_on, prot_temp_val, prot_temp_accion,
                  prot_clima_on, prot_clima_val, prot_clima_accion, prot_net_on, prot_net_rej_pct, prot_net_accion,
                  prot_fan_on, prot_fan_accion, prot_wakeup_on, prot_wakeup_min, usr_actual))
            msg = "Reglas de Protección y Watchdog guardadas correctamente."
        elif accion == 'liberar_cuarentena':
            ip_lib = (request.form.get('ip') or '').strip()
            if ip_lib:
                ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("""
                    UPDATE mineros_activos
                    SET wd_cuarentena=0, wd_reinicios_seguidos=0, wd_fallo_desde='', wd_motivo_fallo='',
                        prot_sleep_desde='', prot_motivo=''
                    WHERE owner=%s AND ip=%s
                """, (usr_actual, ip_lib))
                cur.execute("INSERT INTO cola_comandos (owner, ip, comando, estado, fecha) VALUES (%s, %s, 'normal', 'pendiente', %s)",
                            (usr_actual, ip_lib, ahora))
                msg = f"Minero {ip_lib} liberado y puesto en modo Normal."
        elif accion == 'probar_telegram':
            cur.execute("SELECT tg_token, tg_chat_id FROM usuarios WHERE username=%s", (usr_actual,))
            u_cfg = cur.fetchone()
            if u_cfg and u_cfg.get('tg_token') and u_cfg.get('tg_chat_id'):
                ok = enviar_telegram(u_cfg['tg_token'], u_cfg['tg_chat_id'], f"🟢 <b>El Minero Gris — Conexión Exitosa</b>\nLas alertas y automatizaciones para <b>{usr_actual}</b> están listas.")
                msg = "Mensaje de prueba enviado a tu Telegram." if ok else "Error al conectar con Telegram. Verifica tu Token y Chat ID."
            else:
                msg = "Primero guarda tu Bot Token y Chat ID arriba."
        elif accion == 'limpiar_historial':
            cur.execute("DELETE FROM registro_alertas WHERE owner=%s", (usr_actual,))
            msg = "Historial de incidentes limpiado."

    cur.execute("""
        SELECT tg_token, tg_chat_id, umbral_temp, umbral_hr, alertas_activas,
               wd_activo, wd_alcance, wd_granjas, wd_mineros, wd_zero_hr, wd_cold_on,
               wd_cold_temp, wd_amb_cold_on, wd_amb_cold_temp, wd_dead_board,
               wd_gracia_min, wd_cooldown_min, wd_cuarentena_on, wd_max_reinicios,
               prot_activo, prot_temp_on, prot_temp_val, prot_temp_accion,
               prot_clima_on, prot_clima_val, prot_clima_accion,
               prot_net_on, prot_net_rej_pct, prot_net_accion,
               prot_fan_on, prot_fan_accion, prot_wakeup_on, prot_wakeup_min
        FROM usuarios WHERE username=%s
    """, (usr_actual,))
    row_u = cur.fetchone() or {}
    cfg = {
        'tg_token': row_u.get('tg_token') or '', 'tg_chat_id': row_u.get('tg_chat_id') or '',
        'umbral_temp': a_int_seguro(row_u.get('umbral_temp'), 80),
        'umbral_hr': a_float_seguro(row_u.get('umbral_hr'), 50.0),
        'activas': bool(a_int_seguro(row_u.get('alertas_activas'), 0) == 1)
    }

    try: wd_granjas_sel = json.loads(row_u.get('wd_granjas') or '[]')
    except: wd_granjas_sel = []
    try: wd_mineros_sel = json.loads(row_u.get('wd_mineros') or '[]')
    except: wd_mineros_sel = []

    wd = {
        'activo': bool(a_int_seguro(row_u.get('wd_activo'), 0) == 1),
        'alcance': row_u.get('wd_alcance') or 'all',
        'granjas_sel': wd_granjas_sel,
        'mineros_sel': wd_mineros_sel,
        'zero_hr': bool(a_int_seguro(row_u.get('wd_zero_hr'), 0) == 1),
        'cold_on': bool(a_int_seguro(row_u.get('wd_cold_on'), 0) == 1),
        'cold_temp': a_int_seguro(row_u.get('wd_cold_temp'), 31),
        'amb_cold_on': bool(a_int_seguro(row_u.get('wd_amb_cold_on'), 0) == 1),
        'amb_cold_temp': a_int_seguro(row_u.get('wd_amb_cold_temp'), 18),
        'dead_board': bool(a_int_seguro(row_u.get('wd_dead_board'), 0) == 1),
        'gracia_min': a_int_seguro(row_u.get('wd_gracia_min'), 5),
        'cooldown_min': a_int_seguro(row_u.get('wd_cooldown_min'), 30),
        'cuarentena_on': bool(a_int_seguro(row_u.get('wd_cuarentena_on'), 0) == 1),
        'max_reinicios': a_int_seguro(row_u.get('wd_max_reinicios'), 3),
        'prot_activo': bool(a_int_seguro(row_u.get('prot_activo'), 0) == 1),
        'prot_temp_on': bool(a_int_seguro(row_u.get('prot_temp_on'), 0) == 1),
        'prot_temp_val': a_int_seguro(row_u.get('prot_temp_val'), 85),
        'prot_temp_accion': row_u.get('prot_temp_accion') or 'sleep',
        'prot_clima_on': bool(a_int_seguro(row_u.get('prot_clima_on'), 0) == 1),
        'prot_clima_val': a_int_seguro(row_u.get('prot_clima_val'), 38),
        'prot_clima_accion': row_u.get('prot_clima_accion') or 'sleep',
        'prot_net_on': bool(a_int_seguro(row_u.get('prot_net_on'), 0) == 1),
        'prot_net_rej_pct': a_float_seguro(row_u.get('prot_net_rej_pct'), 5.0),
        'prot_net_accion': row_u.get('prot_net_accion') or 'sleep',
        'prot_fan_on': bool(a_int_seguro(row_u.get('prot_fan_on'), 0) == 1),
        'prot_fan_accion': row_u.get('prot_fan_accion') or 'sleep',
        'prot_wakeup_on': bool(a_int_seguro(row_u.get('prot_wakeup_on'), 0) == 1),
        'prot_wakeup_min': a_int_seguro(row_u.get('prot_wakeup_min'), 30)
    }

    cur.execute("SELECT ip, modelo, worker1, farm_city, watchdog_activo, wd_cuarentena, wd_reinicios_seguidos, wd_motivo_fallo, prot_sleep_desde, prot_motivo FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas_m = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    granjas_map = {}
    mineros_disp = []
    en_cuarentena = []
    for fm in filas_m:
        ip_m = fm['ip']
        clave_g = clave_granja_minero(ip_m, fm.get('farm_city'))
        granjas_map[clave_g] = granjas_map.get(clave_g, 0) + 1
        es_q = a_int_seguro(fm.get('wd_cuarentena'), 0) == 1
        es_psleep = bool(str(fm.get('prot_sleep_desde') or '').strip())
        item_m = {
            'ip': ip_m,
            'nombre': nombre_inteligente_minero(ip_m, nombres_dict, fm.get('worker1')),
            'modelo': fm.get('modelo') or 'Antminer',
            'granja': clave_g,
            'watchdog_activo': bool(a_int_seguro(fm.get('watchdog_activo'), 1) == 1),
            'cuarentena': es_q,
            'prot_sleep': es_psleep,
            'reinicios': a_int_seguro(fm.get('wd_reinicios_seguidos'), 0),
            'motivo': (fm.get('prot_motivo') if es_psleep else '') or (fm.get('wd_motivo_fallo') or '')
        }
        mineros_disp.append(item_m)
        if es_q or es_psleep:
            en_cuarentena.append(item_m)

    granjas_disp = [{'clave': k, 'total': v} for k, v in granjas_map.items()]

    cur.execute("SELECT * FROM registro_alertas WHERE owner=%s ORDER BY id DESC LIMIT 100", (usr_actual,))
    historial = cur.fetchall()
    return render_seguro(
        'alertas.html', active_page='alertas', cfg=cfg, wd=wd,
        granjas_disp=granjas_disp, mineros_disp=mineros_disp,
        en_cuarentena=en_cuarentena, historial=historial, msg=msg
    )

# --- VISTA: /ajustes -> templates/ajustes.html ---
@app.route('/ajustes', methods=['GET', 'POST'])
@login_required
def ajustes():
    usr_actual = session.get('usuario', 'Admin')
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
        elif accion == 'cambiar_username':
            nuevo_u = (request.form.get('nuevo_username') or '').strip()
            if usr_actual.lower() == 'admin':
                msg = "La cuenta principal Admin no puede cambiar de nombre."
            elif len(nuevo_u) < 3 or len(nuevo_u) > 30 or ' ' in nuevo_u:
                msg = "El usuario debe tener entre 3 y 30 caracteres sin espacios."
            elif nuevo_u == usr_actual:
                msg = "Tu nombre de usuario no tuvo cambios."
            else:
                cur.execute("SELECT id FROM usuarios WHERE LOWER(username)=LOWER(%s) AND LOWER(username) != LOWER(%s)", (nuevo_u, usr_actual))
                if cur.fetchone():
                    msg = "Ese nombre de usuario ya está en uso por otra cuenta."
                else:
                    cur.execute("UPDATE usuarios SET username=%s WHERE username=%s", (nuevo_u, usr_actual))
                    for tbl, col in [
                        ('mineros_activos', 'owner'), ('nombres_mineros', 'owner'),
                        ('logs_mineros', 'owner'), ('historial_nube', 'owner'),
                        ('cola_comandos', 'owner'), ('registro_alertas', 'owner'),
                        ('movimientos_saldo', 'username')
                    ]:
                        cur.execute(f"UPDATE {tbl} SET {col}=%s WHERE {col}=%s", (nuevo_u, usr_actual))
                    session['usuario'] = nuevo_u
                    usr_actual = nuevo_u
                    msg = f"Nombre de usuario actualizado a '{nuevo_u}' correctamente."
        elif accion == 'cambiar_password':
            p1 = request.form.get('nueva_pass', '')
            p2 = request.form.get('confirm_pass', '')
            if p1 and p1 == p2:
                cur.execute("UPDATE usuarios SET password=%s WHERE username=%s", (generate_password_hash(p1), usr_actual))
                msg = "Contraseña actualizada y cifrada correctamente."
            else:
                msg = "Las contraseñas no coinciden."

    cur.execute("SELECT * FROM usuarios WHERE username=%s", (usr_actual,))
    u_data = cur.fetchone() or {}
    if not u_data.get('api_key'):
        nueva_k = generar_api_key()
        cur.execute("UPDATE usuarios SET api_key=%s WHERE username=%s", (nueva_k, usr_actual))
        u_data['api_key'] = nueva_k

    user_info = {
        'username': usr_actual, 'plan': u_data.get('plan', 'Gratis'),
        'api_key': u_data.get('api_key', ''), 'subnet': u_data.get('subnet') or '192.168.0',
        'tarifa_kwh': u_data.get('tarifa_kwh') or 0.0
    }
    return render_seguro('ajustes.html', active_page='ajustes', user=user_info, msg=msg)

# --- LOGIN Y VERIFICADOR DE ACTUALIZACIONES PARA EL APK ANDROID ---
@app.route('/api/agente/apk_version')
def api_agente_apk_version():
    return jsonify({
        "version_code": APK_VERSION_CODE,
        "version_name": APK_VERSION_NAME,
        "apk_url": "https://github.com/elminerogris/el-minero-gris-android/releases/latest/download/ElMineroGris-Agente.apk"
    }), 200

@app.route('/api/agente/login', methods=['POST'])
def api_agente_login():
    data = request.get_json(silent=True) or {}
    u = (data.get('username') or '').strip()
    p = data.get('password') or ''
    if not u or not p:
        return jsonify({"ok": False, "msg": "Usuario y contraseña requeridos"}), 400

    con = get_db()
    con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM usuarios WHERE LOWER(username)=LOWER(%s) OR (email != '' AND LOWER(email)=LOWER(%s))", (u, u))
    usr = cur.fetchone()
    if not usr or not verificar_password(usr.get('password'), p):
        return jsonify({"ok": False, "msg": "Credenciales incorrectas"}), 401
    if a_int_seguro(usr.get('suspendido'), 0) == 1:
        return jsonify({"ok": False, "msg": "Cuenta suspendida. Contacta soporte."}), 403

    api_k = usr.get('api_key')
    if not api_k:
        api_k = generar_api_key()
        cur.execute("UPDATE usuarios SET api_key=%s WHERE id=%s", (api_k, usr['id']))

    return jsonify({
        "ok": True,
        "username": usr['username'],
        "api_key": api_k,
        "subnet": usr.get('subnet') or '192.168.0'
    }), 200

@app.route('/api/agente/version')
def api_agente_version():
    try:
        with open('agente.py', 'r', encoding='utf-8') as f:
            version_hash = hashlib.sha256(f.read().encode('utf-8')).hexdigest()[:12]
        return jsonify({"version": version_hash}), 200
    except:
        return jsonify({"version": "error"}), 500

@app.route('/api/agente/descargar_update')
def api_agente_update():
    api_k = (request.args.get('api_key') or '').strip()
    sub = (request.args.get('subnet') or '192.168.0').strip()
    if not api_k or api_k == "TU_API_KEY_AQUI":
        return "API Key requerida", 401

    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username, subnet FROM usuarios WHERE api_key=%s", (api_k,))
    u = cur.fetchone()
    if not u:
        return "API Key invalida", 401

    sub_final = u.get('subnet') or sub
    try:
        codigo, _ = obtener_codigo_agente_configurado(api_k, sub_final)
        return Response(codigo, mimetype="text/x-python")
    except:
        return "Error leyendo agente", 500

@app.route('/descargar_agente')
@login_required
def descargar_agente():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT api_key, subnet FROM usuarios WHERE username=%s", (usr_actual,))
    u = cur.fetchone() or {}
    api_k = u.get('api_key') or generar_api_key()
    sub = u.get('subnet') or '192.168.0'

    try:
        codigo_agente, _ = obtener_codigo_agente_configurado(api_k, sub)
        bat_script = f"""@echo off
title El Minero Gris - Agente Local ({usr_actual})
cd /d "%~dp0"
set EMG_MANAGED=1

echo ==================================================
echo   EL MINERO GRIS - INSTALADOR Y MONITOR 24/7
echo ==================================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python no encontrado. Intentando instalar automaticamente...
    winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    echo [!] Si termino de instalar, cierra esta ventana y abre START_AGENT.bat de nuevo.
    pause
    exit /b
)

echo [*] Verificando librerias necesarias...
python -m pip install --quiet --upgrade requests

:: Crear acceso directo en Inicio de Windows para arrancar solo al encender la PC
set "STARTUP_LNK=%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\ElMineroGris_Agente.lnk"
if not exist "%STARTUP_LNK%" (
    echo [*] Configurando arranque automatico con Windows...
    powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%STARTUP_LNK%');$s.TargetPath='%~f0';$s.WorkingDirectory='%~dp0';$s.Save()"
)

:LOOP
echo.
echo [OK] Iniciando Agente Universal El Minero Gris...
python agente.py
echo [!] El agente se reiniciara en 4 segundos (actualizacion o reconexion)...
timeout /t 4 /nobreak >nul
goto LOOP
"""
        mem_zip = io.BytesIO()
        with zipfile.ZipFile(mem_zip, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('agente.py', codigo_agente)
            zf.writestr('START_AGENT.bat', bat_script.replace('\n', '\r\n'))
        mem_zip.seek(0)
        return Response(
            mem_zip.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": f"attachment;filename=ElMineroGris_Agente_{usr_actual}.zip"}
        )
    except Exception as e:
        print(f"Error generando zip agente: {e}")
        return redirect('/ajustes')

@app.route('/plan')
@login_required
def plan():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT plan, saldo FROM usuarios WHERE username=%s", (usr_actual,))
    u = cur.fetchone() or {}
    session['plan'] = u.get('plan', session.get('plan', 'Gratis'))
    session['saldo'] = u.get('saldo', session.get('saldo', 0.0))
    return render_seguro('plan.html', active_page='plan', current_plan=session['plan'], saldo=session['saldo'])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
        con = get_db(); con.autocommit = True
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM usuarios WHERE LOWER(username)=LOWER(%s) OR (email != '' AND LOWER(email)=LOWER(%s))", (u, u))
        usr = cur.fetchone()
        if usr and verificar_password(usr.get('password'), p):
            if a_int_seguro(usr.get('suspendido'), 0) == 1:
                return render_seguro('login.html', error='Tu cuenta se encuentra suspendida. Contacta soporte.', mode='login')
            if not es_hash_seguro(usr.get('password')):
                cur.execute("UPDATE usuarios SET password=%s WHERE id=%s", (generate_password_hash(p), usr['id']))
            session.pop('admin_real', None)
            session['usuario'] = usr['username']
            session['plan'] = usr.get('plan', 'Gratis')
            session['saldo'] = usr.get('saldo', 0.0)
            return redirect('/')
        return render_seguro('login.html', error='Credenciales incorrectas', mode='login')
    return render_seguro('login.html', mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        em = (request.form.get('email') or '').strip().lower()
        p = request.form.get('password') or ''
        if not u or not p:
            return render_seguro('login.html', error='Completa usuario y contraseña', mode='register')
        if len(u) < 3 or len(u) > 30 or ' ' in u:
            return render_seguro('login.html', error='El usuario debe tener entre 3 y 30 caracteres sin espacios', mode='register')
        try:
            con = get_db(); con.autocommit = True; cur = con.cursor()
            cur.execute("SELECT id FROM usuarios WHERE LOWER(username)=LOWER(%s)", (u,))
            if cur.fetchone():
                return render_seguro('login.html', error='El nombre de usuario ya está en uso', mode='register')
            hoy_str = datetime.utcnow().strftime("%Y-%m-%d")
            cur.execute("INSERT INTO usuarios (username, email, fecha_registro, password, plan, saldo, api_key) VALUES (%s, %s, %s, %s, 'Gratis', 0.0, %s)",
                        (u, em, hoy_str, generate_password_hash(p), generar_api_key()))
            return render_seguro('login.html', success='Cuenta creada (Plan Gratis: hasta 3 mineros). Ahora inicia sesión.', mode='login')
        except:
            return render_seguro('login.html', error='El nombre de usuario ya está en uso', mode='register')
    return render_seguro('login.html', mode='register')

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/guardar_nombre', methods=['POST'])
@login_required
def guardar_nombre():
    usr_actual = session.get('usuario', 'Admin')
    ip = request.form.get('ip')
    nombre = request.form.get('nombre')
    if ip and nombre:
        con = get_db(); con.autocommit = True; cur = con.cursor()
        cur.execute("INSERT INTO nombres_mineros (owner, ip, nombre) VALUES (%s, %s, %s) ON CONFLICT (owner, ip) DO UPDATE SET nombre=EXCLUDED.nombre", (usr_actual, ip, nombre))
    return redirect(f'/minero/{ip}')

@app.route('/cmd_bulk', methods=['POST'])
@login_required
def cmd_bulk():
    usr_actual = session.get('usuario', 'Admin')
    plan_actual = session.get('plan', 'Gratis')
    data = request.get_json(silent=True) or {}
    accion = data.get('accion')
    ips = data.get('ips', [])
    ordenes_custom = data.get('ordenes_custom', [])

    total_objetivos = len(ordenes_custom) if ordenes_custom else len(ips)
    if total_objetivos > 1 and not es_cuenta_pro(usr_actual, plan_actual) and not es_super_admin():
        return jsonify({"status": "error", "msg": "Las Acciones Masivas requieren Plan Pro."}), 403

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
    usr_actual = session.get('usuario', 'Admin')
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
    usr_actual = session.get('usuario', 'Admin')
    plan_actual = session.get('plan', 'Gratis')
    if not es_cuenta_pro(usr_actual, plan_actual) and not es_super_admin():
        return redirect('/plan')

    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT ip, modelo, algoritmo, moneda, hashrate, temp_max, consumo_w, estado FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['IP', 'Modelo', 'Algoritmo', 'Moneda', 'Hashrate', 'Temp Max (C)', 'Consumo (W)', 'Estado'])
    for f in filas: writer.writerow([f['ip'], f['modelo'], f.get('algoritmo', 'SHA-256'), f.get('moneda', 'BTC'), f['hashrate'], f['temp_max'], f['consumo_w'], f['estado']])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=Reporte_El_Minero_Gris.csv"})

@app.route('/api/ver_log/<ip>')
@login_required
def ver_log(ip):
    usr_actual = session.get('usuario', 'Admin')
    con = get_db(); cur = con.cursor()
    cur.execute("SELECT contenido FROM logs_mineros WHERE owner=%s AND ip=%s", (usr_actual, ip))
    log = cur.fetchone()
    return log[0] if log else "Esperando respuesta..."

@app.route('/api/recibir_logs', methods=['POST'])
def recibir_logs():
    data = request.get_json(silent=True)
    if data and 'ip' in data and 'log' in data:
        con = get_db(); con.autocommit = True
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        api_key_recibida = str(data.get('api_key') or '').strip()
        owner_user = 'Admin'
        if api_key_recibida and api_key_recibida != "TU_API_KEY_AQUI":
            cur.execute("SELECT username FROM usuarios WHERE api_key=%s", (api_key_recibida,))
            u_row = cur.fetchone()
            if not u_row:
                return jsonify({"status": "error", "msg": "API Key inválida"}), 401
            owner_user = u_row['username']
        cur.execute(
            "INSERT INTO logs_mineros (owner, ip, contenido, fecha) VALUES (%s, %s, %s, %s) ON CONFLICT(owner, ip) DO UPDATE SET contenido=EXCLUDED.contenido, fecha=EXCLUDED.fecha",
            (owner_user, data['ip'], data['log'], datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        )
    return "OK", 200

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    global ultima_limpieza_bd
    datos = request.get_json(silent=True)
    if not datos: return jsonify({"status": "error", "msg": "JSON invalido"}), 400

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
    owner_user = 'Admin'
    u_cfg = None
    if api_key_recibida and api_key_recibida != "TU_API_KEY_AQUI":
        cur.execute("SELECT * FROM usuarios WHERE api_key=%s", (api_key_recibida,))
        u_cfg = cur.fetchone()
        if not u_cfg:
            return jsonify({"status": "error", "msg": "API Key inválida"}), 401
        owner_user = u_cfg['username']
    else:
        cur.execute("SELECT * FROM usuarios WHERE LOWER(username)='admin'")
        u_cfg = cur.fetchone()

    plan_owner = (u_cfg.get('plan') or 'Gratis') if u_cfg else 'Gratis'
    es_pro = es_cuenta_pro(owner_user, plan_owner)

    cur.execute("""
        SELECT ip, corte_activo, corte_temp, watchdog_activo, wd_fallo_desde, wd_motivo_fallo,
               wd_ultimo_reinicio, wd_reinicios_seguidos, wd_cuarentena, placas_normales,
               prot_sleep_desde, prot_motivo, farm_lat, farm_lon, farm_city, gps_manual
        FROM mineros_activos WHERE owner=%s
    """, (owner_user,))
    rows_exist = cur.fetchall()
    ips_existentes = {row['ip'] for row in rows_exist}
    estado_prev_dict = {row['ip']: row for row in rows_exist}

    if owner_user.lower() == 'admin':
        cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente' AND (LOWER(owner)='admin' OR owner IS NULL)")
    else:
        cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente' AND owner=%s", (owner_user,))
    pend = cur.fetchall()
    ordenes = [{"ip": c['ip'], "comando": c['comando']} for c in pend]
    for c in pend: cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=%s", (c['id'],))

    ahora_dt = datetime.utcnow()
    ahora = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    ahora_minuto = ahora_dt.strftime("%Y-%m-%d %H:%M:00")
    ahora_ts = time.time()

    umbral_t = a_int_seguro(u_cfg.get('umbral_temp'), 80) if u_cfg else 80
    umbral_h = a_float_seguro(u_cfg.get('umbral_hr'), 50.0) if u_cfg else 50.0
    alertas_on = bool(a_int_seguro(u_cfg.get('alertas_activas'), 0) == 1) if u_cfg else False
    tg_tok = (u_cfg.get('tg_token') or '') if u_cfg else ''
    tg_cid = (u_cfg.get('tg_chat_id') or '') if u_cfg else ''

    wd_activo = bool(a_int_seguro(u_cfg.get('wd_activo'), 0) == 1) if u_cfg else False
    wd_alcance = (u_cfg.get('wd_alcance') or 'all') if u_cfg else 'all'
    try: wd_granjas_list = json.loads((u_cfg.get('wd_granjas') or '[]')) if u_cfg else []
    except: wd_granjas_list = []
    try: wd_mineros_list = json.loads((u_cfg.get('wd_mineros') or '[]')) if u_cfg else []
    except: wd_mineros_list = []

    wd_zero_hr = bool(a_int_seguro(u_cfg.get('wd_zero_hr'), 0) == 1) if u_cfg else False
    wd_cold_on = bool(a_int_seguro(u_cfg.get('wd_cold_on'), 0) == 1) if u_cfg else False
    wd_cold_temp = a_int_seguro(u_cfg.get('wd_cold_temp'), 31) if u_cfg else 31
    wd_amb_cold_on = bool(a_int_seguro(u_cfg.get('wd_amb_cold_on'), 0) == 1) if u_cfg else False
    wd_amb_cold_temp = a_int_seguro(u_cfg.get('wd_amb_cold_temp'), 18) if u_cfg else 18
    wd_dead_board = bool(a_int_seguro(u_cfg.get('wd_dead_board'), 0) == 1) if u_cfg else False
    wd_gracia_seg = max(60, a_int_seguro(u_cfg.get('wd_gracia_min'), 5) * 60) if u_cfg else 300
    wd_cooldown_seg = max(300, a_int_seguro(u_cfg.get('wd_cooldown_min'), 30) * 60) if u_cfg else 1800
    wd_cuarentena_on = bool(a_int_seguro(u_cfg.get('wd_cuarentena_on'), 0) == 1) if u_cfg else False
    wd_max_reinicios = max(1, a_int_seguro(u_cfg.get('wd_max_reinicios'), 3)) if u_cfg else 3

    prot_activo = bool(a_int_seguro(u_cfg.get('prot_activo'), 0) == 1) if u_cfg else False
    prot_temp_on = bool(a_int_seguro(u_cfg.get('prot_temp_on'), 0) == 1) if u_cfg else False
    prot_temp_val = a_int_seguro(u_cfg.get('prot_temp_val'), 85) if u_cfg else 85
    prot_temp_accion = (u_cfg.get('prot_temp_accion') or 'sleep') if u_cfg else 'sleep'
    prot_clima_on = bool(a_int_seguro(u_cfg.get('prot_clima_on'), 0) == 1) if u_cfg else False
    prot_clima_val = a_int_seguro(u_cfg.get('prot_clima_val'), 38) if u_cfg else 38
    prot_clima_accion = (u_cfg.get('prot_clima_accion') or 'sleep') if u_cfg else 'sleep'
    prot_net_on = bool(a_int_seguro(u_cfg.get('prot_net_on'), 0) == 1) if u_cfg else False
    prot_net_rej_pct = a_float_seguro(u_cfg.get('prot_net_rej_pct'), 5.0) if u_cfg else 5.0
    prot_net_accion = (u_cfg.get('prot_net_accion') or 'sleep') if u_cfg else 'sleep'
    prot_fan_on = bool(a_int_seguro(u_cfg.get('prot_fan_on'), 0) == 1) if u_cfg else False
    prot_fan_accion = (u_cfg.get('prot_fan_accion') or 'sleep') if u_cfg else 'sleep'
    prot_wakeup_on = bool(a_int_seguro(u_cfg.get('prot_wakeup_on'), 0) == 1) if u_cfg else False
    prot_wakeup_seg = max(300, a_int_seguro(u_cfg.get('prot_wakeup_min'), 30) * 60) if u_cfg else 1800

    guardados_ok = 0
    omitidos_por_plan = 0

    for m in datos.get('mineros', []):
        try:
            ip_m = str(m.get('ip') or '').strip()
            if not ip_m: continue

            if not es_pro and ip_m not in ips_existentes:
                if len(ips_existentes) >= LIMITE_MINEROS_GRATIS:
                    omitidos_por_plan += 1
                    continue
                ips_existentes.add(ip_m)

            mod_m = str(m.get('modelo') or 'Antminer')
            algo_m = str(m.get('algoritmo') or 'SHA-256')
            mon_m = str(m.get('moneda') or 'BTC')
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
            if not isinstance(cadenas_list, list):
                cadenas_list = []
            cadenas_json = json.dumps(cadenas_list)

            cur.execute('''INSERT INTO mineros_activos (ip, owner, modelo, algoritmo, moneda, unidad, hashrate, hashrate_30m, temp_max, temp_amb, consumo_w, estado, ultima_act, errores, pool1, worker1, pass1, pool2, worker2, pass2, pool3, worker3, pass3, mac, serial, firmware, fans, cadenas, uptime, status1, acc1, rej1, status2, acc2, rej2, status3, acc3, rej3, hostname, net_proto, netmask, gateway, dns, farm_lat, farm_lon, farm_city, farm_country) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'online', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(owner, ip) DO UPDATE SET 
                            modelo=EXCLUDED.modelo, algoritmo=EXCLUDED.algoritmo, moneda=EXCLUDED.moneda, unidad=EXCLUDED.unidad, hashrate=EXCLUDED.hashrate, hashrate_30m=EXCLUDED.hashrate_30m, temp_max=EXCLUDED.temp_max, temp_amb=EXCLUDED.temp_amb, consumo_w=EXCLUDED.consumo_w, estado='online', ultima_act=EXCLUDED.ultima_act, errores=EXCLUDED.errores, pool1=EXCLUDED.pool1, worker1=EXCLUDED.worker1, pass1=EXCLUDED.pass1, pool2=EXCLUDED.pool2, worker2=EXCLUDED.worker2, pass2=EXCLUDED.pass2, pool3=EXCLUDED.pool3, worker3=EXCLUDED.worker3, pass3=EXCLUDED.pass3, mac=EXCLUDED.mac, serial=EXCLUDED.serial, firmware=EXCLUDED.firmware, fans=EXCLUDED.fans, cadenas=EXCLUDED.cadenas, uptime=EXCLUDED.uptime, status1=EXCLUDED.status1, acc1=EXCLUDED.acc1, rej1=EXCLUDED.rej1, status2=EXCLUDED.status2, acc2=EXCLUDED.acc2, rej2=EXCLUDED.rej2, status3=EXCLUDED.status3, acc3=EXCLUDED.acc3, rej3=EXCLUDED.rej3, hostname=EXCLUDED.hostname, net_proto=EXCLUDED.net_proto, netmask=EXCLUDED.netmask, gateway=EXCLUDED.gateway, dns=EXCLUDED.dns,
                            farm_lat=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_lat ELSE EXCLUDED.farm_lat END,
                            farm_lon=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_lon ELSE EXCLUDED.farm_lon END,
                            farm_city=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_city ELSE EXCLUDED.farm_city END,
                            farm_country=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_country ELSE EXCLUDED.farm_country END''',
                        (ip_m, owner_user, mod_m, algo_m, mon_m, unid, hr_m, hr_30, temp_m, t_amb, cons_m, ahora, err, p1, w1, pa1, p2, w2, pa2, p3, w3, pa3, mc, sr, fw, fs, cadenas_json, up, st1, ac1, rj1, st2, ac2, rj2, st3, ac3, rj3, host, nproto, nmask, ngw, ndns, f_lat, f_lon, f_city, f_country))

            cur.execute("SELECT id, hashrate, temp_max FROM historial_nube WHERE owner=%s AND ip=%s AND fecha=%s LIMIT 1", (owner_user, ip_m, ahora_minuto))
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

            prev_m = estado_prev_dict.get(ip_m) or {}
            es_manual_gps = a_int_seguro(prev_m.get('gps_manual'), 0) == 1
            ciudad_efectiva = prev_m.get('farm_city') if es_manual_gps else f_city
            lat_efectiva = prev_m.get('farm_lat') if es_manual_gps else f_lat
            lon_efectiva = prev_m.get('farm_lon') if es_manual_gps else f_lon
            clave_g_m = clave_granja_minero(ip_m, ciudad_efectiva)

            aplica_alcance = (
                wd_alcance == 'all'
                or (wd_alcance == 'farms' and clave_g_m in wd_granjas_list)
                or (wd_alcance == 'miners' and ip_m in wd_mineros_list)
            )
            wd_permitido_minero = a_int_seguro(prev_m.get('watchdog_activo'), 1) == 1

            corte_on_m = a_int_seguro(prev_m.get('corte_activo'), 0)
            corte_temp_m = a_int_seguro(prev_m.get('corte_temp'), 85)
            prot_sleep_desde = str(prev_m.get('prot_sleep_desde') or '').strip()
            accion_proteccion = None
            motivo_proteccion = ""

            if prot_activo and aplica_alcance:
                if prot_temp_on and temp_m >= prot_temp_val and hr_m > 0:
                    accion_proteccion = prot_temp_accion
                    motivo_proteccion = f"Temp. ASIC {temp_m}°C ≥ {prot_temp_val}°C"
                elif prot_clima_on and hr_m > 0:
                    t_ext_actual = obtener_clima_coordenadas(lat_efectiva, lon_efectiva)
                    if t_ext_actual is not None and t_ext_actual >= prot_clima_val:
                        accion_proteccion = prot_clima_accion
                        motivo_proteccion = f"Clima exterior {t_ext_actual}°C ≥ {prot_clima_val}°C ({ciudad_efectiva})"
                if not accion_proteccion and prot_fan_on and hr_m > 0 and ventilador_en_cero(fs, mod_m):
                    accion_proteccion = prot_fan_accion
                    motivo_proteccion = f"Ventilador en 0 RPM ({fs})"
                if not accion_proteccion and prot_net_on and hr_m > 0:
                    tot_sh = ac1 + rj1
                    pct_r = (rj1 / tot_sh * 100.0) if tot_sh > 0 else 0.0
                    todos_dead = (st1.lower() != 'alive' and st2.lower() != 'alive' and st3.lower() != 'alive')
                    if pct_r >= prot_net_rej_pct:
                        accion_proteccion = prot_net_accion
                        motivo_proteccion = f"Rechazados {pct_r:.2f}% ≥ {prot_net_rej_pct:.1f}%"
                    elif todos_dead:
                        accion_proteccion = prot_net_accion
                        motivo_proteccion = "Todos los pools en estado Dead"

            if not accion_proteccion and corte_on_m == 1 and temp_m >= corte_temp_m and hr_m > 0:
                accion_proteccion = "sleep"
                motivo_proteccion = f"Corte térmico {temp_m}°C ≥ {corte_temp_m}°C"

            corte_disparado = False
            if accion_proteccion and not alerta_en_enfriamiento(cur, owner_user, ip_m, 'CORTE_TERMICO', 1800):
                corte_disparado = True
                ordenes.append({"ip": ip_m, "comando": accion_proteccion})
                nuevo_sleep_ts = prot_sleep_desde if (accion_proteccion == 'sleep' and prot_sleep_desde) else (ahora if accion_proteccion == 'sleep' else '')
                cur.execute("UPDATE mineros_activos SET prot_sleep_desde=%s, prot_motivo=%s WHERE owner=%s AND ip=%s",
                            (nuevo_sleep_ts, motivo_proteccion, owner_user, ip_m))
                msg_em = f"🛡️ PROTECCIÓN ({accion_proteccion.upper()}): {mod_m} ({ip_m}) — {motivo_proteccion}."
                cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'CORTE_TERMICO', %s, %s)", (owner_user, ip_m, msg_em, ahora))
                if alertas_on:
                    enviar_telegram(tg_tok, tg_cid, f"🛡️ <b>PROTECCIÓN ACTIVADA ({accion_proteccion.upper()})</b>\nMinero: <b>{ip_m}</b> ({mod_m})\nMotivo: <b>{motivo_proteccion}</b>")

            if prot_activo and prot_wakeup_on and prot_sleep_desde and hr_m <= 0.01:
                segs_dormido = calcular_segundos_desconectado(prot_sleep_desde)
                clima_ok = True
                if prot_clima_on:
                    t_ext_chk = obtener_clima_coordenadas(lat_efectiva, lon_efectiva)
                    if t_ext_chk is not None and t_ext_chk >= prot_clima_val:
                        clima_ok = False
                if segs_dormido >= prot_wakeup_seg and clima_ok:
                    if not alerta_en_enfriamiento(cur, owner_user, ip_m, 'CORTE_TERMICO', 900):
                        ordenes.append({"ip": ip_m, "comando": "normal"})
                        cur.execute("UPDATE mineros_activos SET prot_sleep_desde='', prot_motivo='' WHERE owner=%s AND ip=%s",
                                    (owner_user, ip_m))
                        msg_wk = f"🌅 AUTO-DESPERTAR: {mod_m} ({ip_m}) vuelto a NORMAL tras {int(segs_dormido // 60)}m de reposo."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'CORTE_TERMICO', %s, %s)", (owner_user, ip_m, msg_wk, ahora))
                        if alertas_on:
                            enviar_telegram(tg_tok, tg_cid, f"🌅 <b>AUTO-DESPERTAR TÉRMICO</b>\nMinero: <b>{ip_m}</b> ({mod_m}) vuelto a <b>NORMAL</b> tras enfriar.")

            placas_con_hr = sum(1 for ch in cadenas_list if isinstance(ch, dict) and a_float_seguro(ch.get('hr'), 0.0) > 0.0)
            placas_normales = a_int_seguro(prev_m.get('placas_normales'), 0)
            if hr_m > 0 and placas_con_hr > placas_normales:
                placas_normales = placas_con_hr
                cur.execute("UPDATE mineros_activos SET placas_normales=%s WHERE owner=%s AND ip=%s", (placas_normales, owner_user, ip_m))

            fallo_wd = None
            if wd_zero_hr and hr_m <= 0.01 and not prot_sleep_desde:
                fallo_wd = f"0 {unid} (Hashrate en cero)"
            elif wd_cold_on and temp_m > 0 and temp_m <= wd_cold_temp and not prot_sleep_desde:
                fallo_wd = f"Minero frío ({temp_m}°C ≤ {wd_cold_temp}°C)"
            elif wd_amb_cold_on and t_amb > 0 and t_amb <= wd_amb_cold_temp and not prot_sleep_desde:
                fallo_wd = f"Temp. Ambiente baja ({t_amb}°C ≤ {wd_amb_cold_temp}°C)"
            elif wd_dead_board and placas_normales >= 2 and hr_m > 0 and placas_con_hr < placas_normales:
                fallo_wd = f"Placa caída ({placas_con_hr}/{placas_normales} placas activas)"

            wd_fallo_desde = str(prev_m.get('wd_fallo_desde') or '').strip()
            wd_ultimo_reinicio = str(prev_m.get('wd_ultimo_reinicio') or '').strip()
            wd_reinicios = a_int_seguro(prev_m.get('wd_reinicios_seguidos'), 0)
            wd_cuarentena = a_int_seguro(prev_m.get('wd_cuarentena'), 0)

            if fallo_wd is None:
                if wd_fallo_desde or wd_reinicios > 0 or wd_cuarentena == 1 or (prot_sleep_desde and hr_m > 0):
                    cur.execute("""
                        UPDATE mineros_activos
                        SET wd_fallo_desde='', wd_motivo_fallo='', wd_reinicios_seguidos=0, wd_cuarentena=0,
                            prot_sleep_desde='', prot_motivo=''
                        WHERE owner=%s AND ip=%s
                    """, (owner_user, ip_m))
            elif wd_activo and wd_permitido_minero and aplica_alcance and not corte_disparado and wd_cuarentena == 0:
                if not wd_fallo_desde:
                    cur.execute("UPDATE mineros_activos SET wd_fallo_desde=%s, wd_motivo_fallo=%s WHERE owner=%s AND ip=%s",
                                (ahora, fallo_wd, owner_user, ip_m))
                else:
                    segs_fallo = calcular_segundos_desconectado(wd_fallo_desde)
                    if segs_fallo >= wd_gracia_seg:
                        segs_desde_reb = calcular_segundos_desconectado(wd_ultimo_reinicio) if wd_ultimo_reinicio else 999999
                        if segs_desde_reb >= wd_cooldown_seg:
                            if wd_cuarentena_on and wd_reinicios >= wd_max_reinicios:
                                ordenes.append({"ip": ip_m, "comando": "sleep"})
                                cur.execute("""
                                    UPDATE mineros_activos
                                    SET wd_cuarentena=1, wd_ultimo_reinicio=%s, wd_motivo_fallo=%s
                                    WHERE owner=%s AND ip=%s
                                """, (ahora, fallo_wd, owner_user, ip_m))
                                msg_q = f"🔒 CUARENTENA: {mod_m} ({ip_m}) falló tras {wd_reinicios} reinicios ({fallo_wd}). Puesto en SLEEP."
                                cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'WATCHDOG_CUARENTENA', %s, %s)",
                                            (owner_user, ip_m, msg_q, ahora))
                                if alertas_on:
                                    enviar_telegram(tg_tok, tg_cid, f"🔒 <b>WATCHDOG: MINERO EN CUARENTENA</b>\nMinero: <b>{ip_m}</b> ({mod_m})\nMotivo: <b>{fallo_wd}</b>\nReinicios fallidos: {wd_reinicios}/{wd_max_reinicios}\nAcción: Puesto en <b>REPOSO (Sleep)</b>.")
                            else:
                                nuevos_reb = wd_reinicios + 1
                                ordenes.append({"ip": ip_m, "comando": "restart"})
                                cur.execute("""
                                    UPDATE mineros_activos
                                    SET wd_ultimo_reinicio=%s, wd_reinicios_seguidos=%s, wd_fallo_desde='', wd_motivo_fallo=%s
                                    WHERE owner=%s AND ip=%s
                                """, (ahora, nuevos_reb, fallo_wd, owner_user, ip_m))
                                msg_r = f"🔄 AUTO-REINICIO ({nuevos_reb}/{wd_max_reinicios}): {mod_m} ({ip_m}) por {fallo_wd}."
                                cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'WATCHDOG_REBOOT', %s, %s)",
                                            (owner_user, ip_m, msg_r, ahora))
                                if alertas_on:
                                    enviar_telegram(tg_tok, tg_cid, f"🔄 <b>WATCHDOG: AUTO-REINICIO</b>\nMinero: <b>{ip_m}</b> ({mod_m})\nMotivo: <b>{fallo_wd}</b>\nIntento: {nuevos_reb} de {wd_max_reinicios}")

            if alertas_on:
                if temp_m >= umbral_t:
                    if not alerta_en_enfriamiento(cur, owner_user, ip_m, 'TEMP', 1800):
                        msg_al = f"El equipo {mod_m} ({ip_m}) alcanzó {temp_m}°C (Límite: {umbral_t}°C)."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'TEMP', %s, %s)", (owner_user, ip_m, msg_al, ahora))
                        enviar_telegram(tg_tok, tg_cid, f"🔥 <b>ALERTA DE TEMPERATURA</b>\nMinero: <b>{ip_m}</b> ({mod_m})\nTemperatura: <b>{temp_m}°C</b>\nHashrate: {hr_m} {unid}")

                if umbral_h > 0 and hr_m < umbral_h and temp_m > 5:
                    if not alerta_en_enfriamiento(cur, owner_user, ip_m, 'HASHRATE', 1800):
                        msg_hr = f"El equipo {mod_m} ({ip_m}) cayó a {hr_m} {unid} (Mínimo configurado: {umbral_h} {unid})."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'HASHRATE', %s, %s)", (owner_user, ip_m, msg_hr, ahora))
                        enviar_telegram(tg_tok, tg_cid, f"⚠️ <b>CAÍDA DE HASHRATE</b>\nMinero: <b>{ip_m}</b> ({mod_m})\nHashrate actual: <b>{hr_m} {unid}</b> (Umbral: {umbral_h} {unid})\nTemperatura: {temp_m}°C")
        except Exception as err_m:
            print(f"Error guardando minero {m.get('ip')}: {err_m}")

    try:
        cur.execute("UPDATE usuarios SET mineros_omitidos=%s WHERE username=%s", (0 if es_pro else omitidos_por_plan, owner_user))
    except:
        pass

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

    return jsonify({"status": "ok", "guardados": guardados_ok, "omitidos_por_plan": omitidos_por_plan, "ordenes": ordenes}), 200

@app.route('/api/live/minero/<ip>')
@login_required
def api_live_minero(ip):
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT hashrate, temp_max, temp_amb, errores, cadenas FROM mineros_activos WHERE owner=%s AND ip=%s", (usr_actual, ip))
    m = cur.fetchone()
    if not m:
        return jsonify({"ok": False}), 404

    cur.execute("""
        SELECT fecha, hashrate, temp_max, temp_amb, errores, cadenas
        FROM (
            SELECT id, fecha, hashrate, temp_max, temp_amb, errores, cadenas
            FROM historial_nube
            WHERE owner=%s AND ip=%s
            ORDER BY id DESC
            LIMIT 7
        ) sub
        ORDER BY id ASC
    """, (usr_actual, ip))
    rows = cur.fetchall()

    puntos = []
    prev_err = None
    for idx, h in enumerate(rows):
        tot_err = a_int_seguro(h.get('errores'), 0)
        if idx == 0 and len(rows) > 1:
            prev_err = tot_err
            continue
        try:
            ts = calendar.timegm(datetime.strptime(h['fecha'], "%Y-%m-%d %H:%M:%S").timetuple())
        except:
            continue
        diff_err = 0 if prev_err is None else max(0, tot_err - prev_err if tot_err >= prev_err else tot_err)
        prev_err = tot_err
        try:
            cads = json.loads(h['cadenas']) if h.get('cadenas') else []
        except:
            cads = []
        t_amb = a_int_seguro(h.get('temp_amb'), 0)
        puntos.append({
            "ts": ts,
            "hr": a_float_seguro(h.get('hashrate'), 0.0),
            "temp": a_int_seguro(h.get('temp_max'), 0),
            "amb": t_amb if t_amb > 0 else None,
            "err": diff_err,
            "cadenas": cads
        })

    return jsonify({"ok": True, "puntos": puntos})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
