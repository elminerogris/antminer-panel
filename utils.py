from flask import render_template, render_template_string, redirect, session
from werkzeug.security import check_password_hash
from jinja2 import TemplateNotFound
from functools import wraps
import json, urllib.request, urllib.parse, time, os, secrets, hashlib, re, threading, base64, struct, hmac
from datetime import datetime

LIMITE_MINEROS_GRATIS = 3
APK_VERSION_CODE = 4
APK_VERSION_NAME = "1.3"

cache_geo_granja = {}
cache_clima_coord = {}
cache_red = {
    'datos': {'precio': 96500.0, 'dificultad_t': 134.82, 'recompensa': 3.125, 'red_ehs': 965.11},
    'ultima_act': time.time(),
    'actualizando': False
}

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

def es_super_admin():
    return str(session.get('usuario') or '').lower() == 'admin' or str(session.get('admin_real') or '').lower() == 'admin'

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

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

def es_cuenta_pro(username, plan_str, pro_hasta_str=""):
    if str(username or '').lower() == 'admin':
        return True
    ph = str(pro_hasta_str or '').strip()
    if ph:
        try:
            dt_exp = datetime.strptime(ph, "%Y-%m-%d %H:%M:%S")
            if dt_exp > datetime.utcnow():
                return True
            return False
        except:
            pass
    return str(plan_str or '').strip().lower() in ('pro', 'unlimited', 'enterprise')

def calcular_dias_pro_restantes(pro_hasta_str):
    ph = str(pro_hasta_str or '').strip()
    if not ph:
        return None
    try:
        dt_exp = datetime.strptime(ph, "%Y-%m-%d %H:%M:%S")
        diff = (dt_exp - datetime.utcnow()).total_seconds()
        if diff <= 0:
            return 0
        return max(1, int((diff + 86399) // 86400))
    except:
        return None

def evaluar_plan_usuario(cur, u_row):
    """Verifica vencimiento de Pro temporal y devuelve (es_pro, limite_efectivo, dias_restantes)."""
    if not u_row:
        return False, LIMITE_MINEROS_GRATIS, None
    uname = u_row.get('username', '')
    if str(uname).lower() == 'admin':
        return True, 0, None

    plan_str = str(u_row.get('plan') or 'Gratis')
    pro_hasta = str(u_row.get('pro_hasta') or '').strip()
    lim_cfg = a_int_seguro(u_row.get('limite_mineros'), 0)

    if pro_hasta:
        dias_rest = calcular_dias_pro_restantes(pro_hasta)
        if dias_rest is not None and dias_rest > 0:
            return True, lim_cfg, dias_rest
        try:
            cur.execute("UPDATE usuarios SET plan='Gratis', pro_hasta='', limite_mineros=0 WHERE username=%s", (uname,))
        except:
            pass
        return False, LIMITE_MINEROS_GRATIS, 0

    if plan_str.strip().lower() in ('pro', 'unlimited', 'enterprise'):
        return True, lim_cfg, None
    return False, LIMITE_MINEROS_GRATIS, None

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

def calcular_segundos_desconectado(ult_str):
    if not ult_str: return 999999
    try:
        segs = (datetime.utcnow() - datetime.strptime(str(ult_str).strip(), "%Y-%m-%d %H:%M:%S")).total_seconds()
        return max(0, segs)
    except:
        return 0

def alerta_en_enfriamiento(cur, owner, ip, tipo, segundos=1800):
    cur.execute(
        "SELECT fecha FROM registro_alertas WHERE owner=%s AND ip=%s AND tipo=%s ORDER BY id DESC LIMIT 1",
        (owner, ip, tipo)
    )
    row = cur.fetchone()
    if not row or not row.get('fecha'):
        return False
    return calcular_segundos_desconectado(row['fecha']) < segundos

def ordenar_ip_key(item):
    ip_str = str(item.get('ip') or '')
    partes = ip_str.split('.')
    if len(partes) == 4 and all(p.isdigit() for p in partes):
        return tuple(int(p) for p in partes)
    return (999, 999, 999, 999)

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
    if any(w in ml for w in ("hyd", "hydro", "water", "m33", "m53", "m63", "1566ha")):
        return False
    fs = str(fans_str or "").strip()
    if not fs or "hydro" in fs.lower():
        return False
    nums = [int(x) for x in re.findall(r'\d+', fs)]
    if not nums:
        return False
    return all(n == 0 for n in nums)

def determinar_credenciales_defecto(marca="", modelo=""):
    """
    Retorna la marca normalizada, usuario y contraseña por defecto para el acceso
    web / SSH de cualquier marca de ASIC (Avalon, WhatsMiner, Antminer, Fluminer, IceRiver, etc.)
    """
    txt = f"{marca} {modelo}".lower()
    if any(k in txt for k in ("avalon", "canaan", "q90", "a10", "a11", "a12", "a13", "a14", "a15", "nano")):
        return {
            "marca": "Canaan Avalon",
            "user": "root",
            "pass": "root",
            "alt": "admin / (vacía) o admin / admin",
            "nota": "AvalonMiner · Canaan"
        }
    if any(k in txt for k in ("whatsminer", "microbt", "btminer", "m20", "m21", "m30", "m31", "m32", "m33", "m50", "m53", "m60", "m63")):
        return {
            "marca": "MicroBT WhatsMiner",
            "user": "admin",
            "pass": "admin",
            "alt": "",
            "nota": "BTMiner · WhatsMiner"
        }
    if any(k in txt for k in ("iceriver", "ks0", "ks1", "ks2", "ks3", "ks5")):
        return {
            "marca": "IceRiver",
            "user": "admin",
            "pass": "12345678",
            "alt": "",
            "nota": "IceRiver Kaspa"
        }
    if any(k in txt for k in ("goldshell", "box")):
        return {
            "marca": "Goldshell",
            "user": "admin",
            "pass": "12345678",
            "alt": "admin / admin",
            "nota": "Goldshell Miner"
        }
    if any(k in txt for k in ("fluminer", "flux")):
        return {
            "marca": "Fluminer",
            "user": "root",
            "pass": "root",
            "alt": "admin / admin",
            "nota": "Fluminer Altcoin"
        }
    if any(k in txt for k in ("jasminer", "x4", "x16")):
        return {
            "marca": "Jasminer",
            "user": "root",
            "pass": "root",
            "alt": "admin / admin",
            "nota": "Jasminer Etchash"
        }
    if any(k in txt for k in ("innosilicon", "t2t", "t3", "a10", "a11")):
        return {
            "marca": "Innosilicon",
            "user": "admin",
            "pass": "admin",
            "alt": "root / root",
            "nota": "Innosilicon A10/T2T"
        }
    if any(k in txt for k in ("antminer", "bitmain", "bmminer")):
        return {
            "marca": "Bitmain Antminer",
            "user": "root",
            "pass": "root",
            "alt": "root / admin (antiguos) o Braiins: root / root",
            "nota": "Bitmain Antminer"
        }
    return {
        "marca": "ASIC Estándar",
        "user": "root",
        "pass": "root",
        "alt": "admin / admin",
        "nota": "Acceso de fábrica"
    }

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

def generar_totp_secret(length=32):
    """Genera una clave secreta base32 para 2FA compatible con Google Authenticator y Authy."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(secrets.choice(chars) for _ in range(length))

def verificar_totp(secret, codigo, ventana=1):
    """Valida un código TOTP RFC 6238 de 6 dígitos con ventana de tolerancia."""
    if not secret or not codigo:
        return False
    codigo_str = str(codigo).strip()
    if not codigo_str.isdigit() or len(codigo_str) != 6:
        return False
    try:
        secret_clean = secret.upper().replace(" ", "").replace("=", "")
        pad = (8 - len(secret_clean) % 8) % 8
        key = base64.b32decode(secret_clean + "=" * pad)
        ahora = int(time.time() // 30)
        for offset in range(-ventana, ventana + 1):
            counter = ahora + offset
            msg = struct.pack('!Q', counter)
            h = hmac.new(key, msg, hashlib.sha1).digest()
            o = h[19] & 15
            calc = (struct.unpack('!I', h[o:o+4])[0] & 0x7fffffff) % 1000000
            if f"{calc:06d}" == codigo_str:
                return True
    except Exception:
        pass
    return False

def notificar_alerta_seguridad(titulo, detalle, ip=""):
    """Envía una alerta de seguridad silenciosa e inmediata a Telegram sin bloquear peticiones."""
    def _enviar():
        try:
            from db import get_db
            tg_tok = os.environ.get("TELEGRAM_ADMIN_TOKEN")
            tg_cid = os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
            if not tg_tok or not tg_cid:
                con = get_db()
                cur = con.cursor()
                cur.execute("SELECT tg_token, tg_chat_id FROM usuarios WHERE LOWER(username)='admin' LIMIT 1")
                row = cur.fetchone()
                if row and row[0] and row[1]:
                    tg_tok, tg_cid = row[0], row[1]
            if tg_tok and tg_cid:
                ahora_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                msg = (
                    f"🚨 <b>ALERTA DE SEGURIDAD — El Minero Gris</b>\n"
                    f"<b>Evento:</b> {titulo}\n"
                    f"<b>Detalle:</b> {detalle}\n"
                    f"<b>IP:</b> <code>{ip or 'Desconocida'}</code>\n"
                    f"<b>Fecha UTC:</b> {ahora_str}"
                )
                enviar_telegram(tg_tok, tg_cid, msg)
        except Exception:
            pass
    t = threading.Thread(target=_enviar, daemon=True)
    t.start()

def _obtener_precio_btc():
    """Intenta obtener el precio de BTC con 3 APIs de respaldo."""
    # 1. Binance
    try:
        req = urllib.request.Request(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        precio = float(json.loads(urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8'))['price'])
        if precio > 1000:
            return precio
    except Exception:
        pass

    # 2. Coinbase
    try:
        req = urllib.request.Request(
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        j = json.loads(urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8'))
        precio = float(j['data']['amount'])
        if precio > 1000:
            return precio
    except Exception:
        pass

    # 3. CoinGecko
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        j = json.loads(urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8'))
        precio = float(j['bitcoin']['usd'])
        if precio > 1000:
            return precio
    except Exception:
        pass

    return None

def _obtener_dificultad_btc():
    """Intenta obtener dificultad (T) y hashrate global (EH/s) de la red Bitcoin."""
    # 1. Mempool.space
    try:
        req = urllib.request.Request(
            "https://mempool.space/api/v1/mining/hashrate/3d",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        j = json.loads(urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8'))
        diff = float(j.get('currentDifficulty') or 0)
        hr_red = float(j.get('currentHashrate') or 0)
        if diff > 0:
            dif_t = diff / 1_000_000_000_000
            ehs = round(hr_red / 1e18, 2) if hr_red > 0 else round(dif_t * 7.1583, 2)
            return dif_t, ehs
    except Exception:
        pass

    # 2. Blockchain.info como respaldo
    try:
        req = urllib.request.Request(
            "https://blockchain.info/q/getdifficulty",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        diff = float(urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8').strip())
        if diff > 0:
            dif_t = diff / 1_000_000_000_000
            return dif_t, round(dif_t * 7.1583, 2)
    except Exception:
        pass

    return None, None

def _obtener_recompensa_btc():
    """Calcula la recompensa por bloque según la altura actual (se ajusta sola en futuros Halvings)."""
    try:
        req = urllib.request.Request(
            "https://mempool.space/api/blocks/tip/height",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        altura = int(urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8').strip())
        if altura > 0:
            halvings = altura // 210000
            return 50.0 / (2 ** halvings)
    except Exception:
        pass
    return None

def _actualizar_datos_red_worker():
    global cache_red
    try:
        datos = cache_red['datos'].copy()
        precio = _obtener_precio_btc()
        if precio is not None:
            datos['precio'] = precio

        dif_t, red_ehs = _obtener_dificultad_btc()
        if dif_t is not None:
            datos['dificultad_t'] = dif_t
            datos['red_ehs'] = red_ehs

        recomp = _obtener_recompensa_btc()
        if recomp is not None:
            datos['recompensa'] = recomp

        cache_red['datos'] = datos
    except Exception as e:
        print(f"[Red BTC] Error worker fondo: {e}")
    finally:
        cache_red['ultima_act'] = time.time()
        cache_red['actualizando'] = False

def obtener_datos_red():
    global cache_red
    ahora = time.time()
    # Si pasaron más de 5 minutos y no hay un hilo activo, actualizar en segundo plano sin frenar la web
    if ahora - cache_red.get('ultima_act', 0) > 300 and not cache_red.get('actualizando', False):
        cache_red['actualizando'] = True
        try:
            hilo = threading.Thread(target=_actualizar_datos_red_worker, daemon=True)
            hilo.start()
        except Exception:
            cache_red['actualizando'] = False

    # Retorna INSTANTÁNEAMENTE el último valor conocido sin frenar la petición web
    return cache_red['datos']

def obtener_codigo_agente_configurado(api_k, sub):
    with open('agente.py', 'r', encoding='utf-8') as f:
        codigo_base = f.read()
    version_hash = hashlib.sha256(codigo_base.encode('utf-8')).hexdigest()[:12]
    codigo = codigo_base.replace('API_KEY = "TU_API_KEY_AQUI"', f'API_KEY = "{api_k}"')
    codigo = codigo.replace('SUBNET = "192.168.0"', f'SUBNET = "{sub}"')
    codigo = codigo.replace('SUBNET_DEFECTO = "192.168.0"', f'SUBNET_DEFECTO = "{sub}"')
    codigo = codigo.replace('VERSION_AGENTE = "LOCAL"', f'VERSION_AGENTE = "{version_hash}"')
    return codigo, version_hash

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

def parsear_dispositivo(ua_string):
    """Genera un nombre amigable de navegador/sistema a partir del User-Agent."""
    ua = str(ua_string or "").lower()
    os_name = "Dispositivo"
    if "windows" in ua: os_name = "Windows"
    elif "android" in ua: os_name = "Android"
    elif "iphone" in ua or "ipad" in ua: os_name = "iPhone/iPad"
    elif "macintosh" in ua or "mac os" in ua: os_name = "macOS"
    elif "linux" in ua: os_name = "Linux"

    browser = "Navegador"
    if "edg/" in ua: browser = "Edge"
    elif "chrome/" in ua and "safari/" in ua: browser = "Chrome"
    elif "firefox/" in ua: browser = "Firefox"
    elif "safari/" in ua and "chrome/" not in ua: browser = "Safari"
    elif "opera/" in ua or "opr/" in ua: browser = "Opera"

    return f"{browser} en {os_name}"

def fijar_cookie_dispositivo(resp, raw_token):
    """Establece la cookie persistente para Dispositivos de Confianza con alcance de dominio seguro."""
    from flask import request
    import os
    host_c = (request.host or '').split(':')[0].lower()
    cookie_domain = '.elminerogris.com' if 'elminerogris.com' in host_c else None
    resp.set_cookie(
        'emg_trusted_device', raw_token,
        max_age=30 * 86400,
        httponly=True,
        secure=bool(os.environ.get("RENDER") or os.environ.get("DATABASE_URL")),
        samesite='Lax',
        path='/',
        domain=cookie_domain
    )
    return resp

