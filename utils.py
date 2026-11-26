from flask import render_template, render_template_string, redirect, session
from werkzeug.security import check_password_hash
from jinja2 import TemplateNotFound
from functools import wraps
import json, urllib.request, urllib.parse, time, os, secrets, hashlib, re, threading, base64, struct, hmac, math, html, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

LIMITE_MINEROS_GRATIS = 3
APK_VERSION_CODE = 4
APK_VERSION_NAME = "1.3"

cache_geo_granja = {}
cache_clima_coord = {}
cache_red = {
    'datos': {'precio': 60500.0, 'dificultad_t': 134.82, 'recompensa': 3.125, 'red_ehs': 1050.0},
    'ultima_act': 0.0,
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

def es_super_admin(usr=None):
    u = str(usr if usr is not None else (session.get('usuario') or '')).lower()
    return u == 'admin' or str(session.get('admin_real') or '').lower() == 'admin'

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
        f = float(val)
        if math.isnan(f) or math.isinf(f): return defecto
        return int(f)
    except:
        return defecto

def a_float_seguro(val, defecto=0.0):
    try:
        if val is None or val == "": return defecto
        f = float(val)
        if math.isnan(f) or math.isinf(f): return defecto
        return f
    except:
        return defecto

def sanitizar_nombre_minero(val, ip_defecto=""):
    """
    Sanitiza nombres de minero para evitar desbordes de UI, XSS, colisiones de delimitadores (|)
    e inyecciones. Límite estricto: 40 caracteres.
    """
    if val is None:
        s = ""
    else:
        s = str(val).strip()
    # Eliminar bytes nulos y caracteres de control no imprimibles
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', s)
    # Reemplazar delimitador pipe (|) que rompería comandos de socket o protocolos
    s = s.replace('|', '-')
    # Quitar cualquier etiqueta HTML inyectada
    s = re.sub(r'<[^>]*>', '', s)
    # Conservar caracteres válidos: alfanuméricos, espacios, guiones, puntos, #, (), /
    s = re.sub(r'[^\w\s\-\.\#\(\)\/]', '', s, flags=re.UNICODE)
    # Colapsar espacios consecutivos
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > 40:
        s = s[:40].strip()
    if not s:
        if ip_defecto:
            ultimo = str(ip_defecto).split('.')[-1]
            return f"Minero {ultimo}" if ultimo.isdigit() else f"Minero {ip_defecto}"
        return "Minero"
    return s

def sanitizar_nombre_granja(val, defecto="Granja 001"):
    """
    Sanitiza nombres de granja / sede. Límite estricto: 50 caracteres.
    """
    if val is None:
        s = ""
    else:
        s = str(val).strip()
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', s)
    s = s.replace('|', '-')
    s = re.sub(r'<[^>]*>', '', s)
    s = re.sub(r'[^\w\s\-\.\#\(\)\,\/]', '', s, flags=re.UNICODE)
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > 50:
        s = s[:50].strip()
    return s if s else defecto

def sanitizar_texto_seguro(val, max_len=250, defecto=""):
    """
    Sanitiza notas, descripciones y campos libres para evitar XSS o bytes nulos.
    """
    if val is None:
        s = ""
    else:
        s = str(val).strip()
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', s)
    s = s.replace('|', '-')
    s = re.sub(r'<[^>]*>', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) > max_len:
        s = s[:max_len].strip()
    return s if s else defecto

def es_cuenta_pro(username, plan_str="", pro_hasta_str=""):
    """Todas las funciones Pro (racks, hosting, técnicos, acciones masivas, telemetría 30d, reportes CSV)
    son 100% gratuitas para todos los usuarios. El cobro rige únicamente por capacidad de máquinas."""
    return True

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
    """Verifica límites de máquinas y cupones promocionales de capacidad.
    Todas las funciones avanzadas son 100% gratuitas; el límite rige únicamente la cantidad de mineros permitidos.
    """
    if not u_row:
        return True, LIMITE_MINEROS_GRATIS, None
    uname = u_row.get('username', '')
    if str(uname).lower() == 'admin':
        return True, 0, None  # 0 = sin límite de máquinas para Admin

    pro_hasta = str(u_row.get('pro_hasta') or '').strip()
    lim_cfg = a_int_seguro(u_row.get('limite_mineros'), 0)

    # Si tiene vigencia temporal activa (cupón promocional o suscripción por capacidad)
    if pro_hasta:
        dias_rest = calcular_dias_pro_restantes(pro_hasta)
        if dias_rest is not None and dias_rest > 0:
            # Si tiene límite fijado (ej: 10 máquinas con cupón), se respeta; si es 0, ilimitado
            return True, (lim_cfg if lim_cfg > 0 else 0), dias_rest
        try:
            # Al vencer el cupón, vuelve al límite gratuito base de 3 mineros
            cur.execute("UPDATE usuarios SET pro_hasta='', limite_mineros=0 WHERE username=%s", (uname,))
        except:
            pass
        return True, LIMITE_MINEROS_GRATIS, 0

    # Si tiene límite configurado manualmente (ej. asignado por Admin)
    if lim_cfg > 0:
        return True, lim_cfg, None

    # Plan base: hasta 3 mineros gratis con todas las funciones
    return True, LIMITE_MINEROS_GRATIS, None

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
    if not token or not chat_id or not texto: return False
    txt = str(texto)
    # Límite de mensaje en Telegram: 4096 caracteres. Truncamos a 4000 para evitar rechazos HTTP 400
    if len(txt) > 4000:
        txt = txt[:3985] + "\n...(cortado por longitud)"
    url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"

    # Intento 1: Envío formateado en HTML
    try:
        datos = urllib.parse.urlencode({'chat_id': str(chat_id).strip(), 'text': txt, 'parse_mode': 'HTML'}).encode('utf-8')
        req = urllib.request.Request(url, data=datos, headers={'User-Agent': 'Mozilla/5.0'})
        urllib.request.urlopen(req, timeout=4)
        return True
    except Exception as e_html:
        # Si Telegram rechaza el HTML (ej: entidades no balanceadas), enviar texto plano sin formato
        try:
            txt_plano = re.sub(r'<[^>]*>', '', txt)
            datos_plano = urllib.parse.urlencode({'chat_id': str(chat_id).strip(), 'text': txt_plano}).encode('utf-8')
            req_plano = urllib.request.Request(url, data=datos_plano, headers={'User-Agent': 'Mozilla/5.0'})
            urllib.request.urlopen(req_plano, timeout=4)
            return True
        except Exception as e_plano:
            print(f"Error Telegram fallback plano: {e_plano} (original: {e_html})")
            return False

def generar_totp_secret(length=32):
    """Genera una clave secreta base32 para 2FA compatible con Google Authenticator y Authy."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(secrets.choice(chars) for _ in range(length))

def generar_qr_base64(data_str, box_size=5, border=2):
    """Genera un código QR como Data URL PNG Base64 localmente sin depender de servicios externos."""
    if not data_str:
        return ""
    try:
        import qrcode, io
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box_size,
            border=border
        )
        qr.add_data(data_str)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        print(f"Error generando QR base64: {e}")
        return ""

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
    """Consulta múltiples orígenes globales con timeout ultrarrápido (2.5s) para blindar el precio contra caídas."""
    endpoints = [
        # 1. Coinbase (Spot BTC-USD) - Alta disponibilidad, sin restricciones de IP en EE.UU.
        (
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            lambda j: float(j['data']['amount'])
        ),
        # 2. Kraken (Ticker XBTUSD) - Alta liquidez y respuesta inmediata
        (
            "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
            lambda j: float(list(j['result'].values())[0]['c'][0])
        ),
        # 3. Mempool.space - Datos directos de minería y precio USD oficial
        (
            "https://mempool.space/api/v1/prices",
            lambda j: float(j['USD'])
        ),
        # 4. Blockchain.info - API clásica de máxima estabilidad
        (
            "https://blockchain.info/ticker",
            lambda j: float(j['USD']['last'])
        ),
        # 5. Binance - Par BTCUSDT global
        (
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            lambda j: float(j['price'])
        ),
        # 6. CoinGecko - Respaldo complementario
        (
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            lambda j: float(j['bitcoin']['usd'])
        )
    ]

    for url, parser in endpoints:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            resp = urllib.request.urlopen(req, timeout=2.5).read().decode('utf-8')
            precio = parser(json.loads(resp))
            if precio and precio > 1000.0:
                return round(precio, 2)
        except Exception:
            continue

    return None

def _obtener_dificultad_btc():
    """Intenta obtener dificultad (T) y hashrate global (EH/s) de la red Bitcoin con respaldo múltiple."""
    # 1. Mempool.space
    try:
        req = urllib.request.Request(
            "https://mempool.space/api/v1/mining/hashrate/3d",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        j = json.loads(urllib.request.urlopen(req, timeout=3.0).read().decode('utf-8'))
        diff = float(j.get('currentDifficulty') or 0)
        hr_red = float(j.get('currentHashrate') or 0)
        if diff > 0:
            dif_t = diff / 1_000_000_000_000
            ehs = round(hr_red / 1e18, 2) if hr_red > 0 else round(dif_t * 7.1583, 2)
            return round(dif_t, 2), ehs
    except Exception:
        pass

    # 2. Blockchain.info como respaldo
    try:
        req = urllib.request.Request(
            "https://blockchain.info/q/getdifficulty",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        diff = float(urllib.request.urlopen(req, timeout=3.0).read().decode('utf-8').strip())
        if diff > 0:
            dif_t = diff / 1_000_000_000_000
            return round(dif_t, 2), round(dif_t * 7.1583, 2)
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
    # Si es el primer arranque o aún no hay datos frescos, actualizar de inmediato
    if cache_red.get('ultima_act', 0) == 0.0:
        _actualizar_datos_red_worker()
        return cache_red['datos']

    # Si pasaron más de 3 minutos (180s) y no hay un hilo activo, actualizar en segundo plano sin frenar la web
    if ahora - cache_red.get('ultima_act', 0) > 180 and not cache_red.get('actualizando', False):
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

_geo_cache = {}

def obtener_ubicacion_ip(ip_str, req=None):
    """Retorna 'Ciudad, País' a partir de Cloudflare o consulta IP Geolocation rápida con caché."""
    if not ip_str or ip_str in ('127.0.0.1', 'localhost', '::1', 'unknown'):
        return 'Red Local / Privada'
    
    # 1. Cabeceras directas de Cloudflare (solo si contiene ciudad y país)
    if req:
        cf_city = (req.headers.get('CF-IPCity') or '').strip()
        cf_country = (req.headers.get('CF-IPCountry') or '').strip()
        if cf_city and cf_country:
            return f"{cf_city}, {cf_country}"

    # 2. Caché en memoria
    if ip_str in _geo_cache:
        return _geo_cache[ip_str]
        
    # 3. Consulta de geolocalización IP (ip-api.com) con timeout estricto de 1.5s
    try:
        url = f"http://ip-api.com/json/{ip_str}?fields=status,country,city,countryCode"
        req_obj = urllib.request.Request(url, headers={'User-Agent': 'ElMineroGris/2.0'})
        with urllib.request.urlopen(req_obj, timeout=1.5) as r:
            data = json.loads(r.read().decode('utf-8'))
            if data.get('status') == 'success':
                ciudad = (data.get('city') or '').strip()
                pais = (data.get('country') or data.get('countryCode') or '').strip()
                loc = f"{ciudad}, {pais}".strip(', ') if (ciudad or pais) else 'Ubicación Desconocida'
                _geo_cache[ip_str] = loc
                return loc
    except Exception:
        pass

    # 4. Fallback si solo tenemos país de Cloudflare
    if req:
        cf_country = (req.headers.get('CF-IPCountry') or '').strip()
        if cf_country:
            _geo_cache[ip_str] = cf_country
            return cf_country
        
    return 'Ubicación Desconocida'

# Mapeo exhaustivo de códigos técnicos de modelos (ej. SM-A107M) a nombres comerciales amigables (ej. Samsung Galaxy A10s)
SAMSUNG_MODELOS = {
    # Serie Galaxy A
    'SM-A015': 'Samsung Galaxy A01',
    'SM-A022': 'Samsung Galaxy A02',
    'SM-A025': 'Samsung Galaxy A02s',
    'SM-A032': 'Samsung Galaxy A03 Core',
    'SM-A035': 'Samsung Galaxy A03',
    'SM-A037': 'Samsung Galaxy A03s',
    'SM-A042': 'Samsung Galaxy A04e',
    'SM-A045': 'Samsung Galaxy A04',
    'SM-A047': 'Samsung Galaxy A04s',
    'SM-A055': 'Samsung Galaxy A05',
    'SM-A057': 'Samsung Galaxy A05s',
    'SM-A105': 'Samsung Galaxy A10',
    'SM-A107': 'Samsung Galaxy A10s',
    'SM-A115': 'Samsung Galaxy A11',
    'SM-A125': 'Samsung Galaxy A12',
    'SM-A127': 'Samsung Galaxy A12 Nacho',
    'SM-A135': 'Samsung Galaxy A13',
    'SM-A136': 'Samsung Galaxy A13 5G',
    'SM-A137': 'Samsung Galaxy A13',
    'SM-A145': 'Samsung Galaxy A14',
    'SM-A146': 'Samsung Galaxy A14 5G',
    'SM-A155': 'Samsung Galaxy A15',
    'SM-A156': 'Samsung Galaxy A15 5G',
    'SM-A205': 'Samsung Galaxy A20',
    'SM-A207': 'Samsung Galaxy A20s',
    'SM-A217': 'Samsung Galaxy A21s',
    'SM-A225': 'Samsung Galaxy A22',
    'SM-A226': 'Samsung Galaxy A22 5G',
    'SM-A235': 'Samsung Galaxy A23',
    'SM-A236': 'Samsung Galaxy A23 5G',
    'SM-A245': 'Samsung Galaxy A24',
    'SM-A256': 'Samsung Galaxy A25 5G',
    'SM-A305': 'Samsung Galaxy A30',
    'SM-A307': 'Samsung Galaxy A30s',
    'SM-A315': 'Samsung Galaxy A31',
    'SM-A325': 'Samsung Galaxy A32',
    'SM-A326': 'Samsung Galaxy A32 5G',
    'SM-A336': 'Samsung Galaxy A33 5G',
    'SM-A346': 'Samsung Galaxy A34 5G',
    'SM-A356': 'Samsung Galaxy A35 5G',
    'SM-A505': 'Samsung Galaxy A50',
    'SM-A507': 'Samsung Galaxy A50s',
    'SM-A515': 'Samsung Galaxy A51',
    'SM-A516': 'Samsung Galaxy A51 5G',
    'SM-A525': 'Samsung Galaxy A52',
    'SM-A526': 'Samsung Galaxy A52 5G',
    'SM-A528': 'Samsung Galaxy A52s 5G',
    'SM-A536': 'Samsung Galaxy A53 5G',
    'SM-A546': 'Samsung Galaxy A54 5G',
    'SM-A556': 'Samsung Galaxy A55 5G',
    'SM-A705': 'Samsung Galaxy A70',
    'SM-A715': 'Samsung Galaxy A71',
    'SM-A725': 'Samsung Galaxy A72',
    'SM-A736': 'Samsung Galaxy A73 5G',

    # Serie Galaxy S
    'SM-G970': 'Samsung Galaxy S10e',
    'SM-G973': 'Samsung Galaxy S10',
    'SM-G975': 'Samsung Galaxy S10+',
    'SM-G977': 'Samsung Galaxy S10 5G',
    'SM-G980': 'Samsung Galaxy S20',
    'SM-G981': 'Samsung Galaxy S20 5G',
    'SM-G985': 'Samsung Galaxy S20+',
    'SM-G986': 'Samsung Galaxy S20+ 5G',
    'SM-G988': 'Samsung Galaxy S20 Ultra',
    'SM-G990': 'Samsung Galaxy S21 FE',
    'SM-G991': 'Samsung Galaxy S21',
    'SM-G996': 'Samsung Galaxy S21+',
    'SM-G998': 'Samsung Galaxy S21 Ultra',
    'SM-S901': 'Samsung Galaxy S22',
    'SM-S906': 'Samsung Galaxy S22+',
    'SM-S908': 'Samsung Galaxy S22 Ultra',
    'SM-S911': 'Samsung Galaxy S23',
    'SM-S916': 'Samsung Galaxy S23+',
    'SM-S918': 'Samsung Galaxy S23 Ultra',
    'SM-S921': 'Samsung Galaxy S24',
    'SM-S926': 'Samsung Galaxy S24+',
    'SM-S928': 'Samsung Galaxy S24 Ultra',
    'SM-S711': 'Samsung Galaxy S23 FE',
    'SM-S721': 'Samsung Galaxy S24 FE',

    # Serie Galaxy Note
    'SM-N970': 'Samsung Galaxy Note 10',
    'SM-N975': 'Samsung Galaxy Note 10+',
    'SM-N980': 'Samsung Galaxy Note 20',
    'SM-N981': 'Samsung Galaxy Note 20 5G',
    'SM-N985': 'Samsung Galaxy Note 20 Ultra',
    'SM-N986': 'Samsung Galaxy Note 20 Ultra 5G',

    # Serie Galaxy Z (Flip & Fold)
    'SM-F700': 'Samsung Galaxy Z Flip',
    'SM-F707': 'Samsung Galaxy Z Flip 5G',
    'SM-F711': 'Samsung Galaxy Z Flip 3',
    'SM-F721': 'Samsung Galaxy Z Flip 4',
    'SM-F731': 'Samsung Galaxy Z Flip 5',
    'SM-F741': 'Samsung Galaxy Z Flip 6',
    'SM-F900': 'Samsung Galaxy Fold',
    'SM-F916': 'Samsung Galaxy Z Fold 2',
    'SM-F926': 'Samsung Galaxy Z Fold 3',
    'SM-F936': 'Samsung Galaxy Z Fold 4',
    'SM-F946': 'Samsung Galaxy Z Fold 5',
    'SM-F956': 'Samsung Galaxy Z Fold 6',
}

# Modelos Xiaomi / Redmi / POCO
XIAOMI_MODELOS = {
    '2201117': 'Xiaomi Redmi Note 11',
    '2312DRA50': 'Xiaomi Redmi Note 13 Pro',
    '23076RN4BI': 'Xiaomi Redmi 12',
    'M2003J15SC': 'Xiaomi Redmi Note 9',
    'M2101K6G': 'Xiaomi Redmi Note 10 Pro',
    '220333QAG': 'Xiaomi Redmi 10C',
    '2109119DG': 'Xiaomi 11 Lite 5G NE',
    '2210132G': 'Xiaomi 12T Pro',
    '23049PCD8G': 'POCO F5',
}

def resolver_modelo_comercial(raw_text):
    """
    Traduce códigos técnicos de hardware (ej. SM-A107M, 2201117PG) o cadenas de SO
    al nombre de modelo comercial limpio (ej. Samsung Galaxy A10s, iPhone 16 Pro Max, PC con Windows).
    """
    if not raw_text:
        return 'Dispositivo Web'
    
    s = str(raw_text).strip()
    s_l = s.lower()
    
    # 1. Búsqueda de códigos Samsung (ej: SM-A107M, SM-G981U, etc.)
    m_sm = re.search(r'\b(SM-[A-Z0-9]{4,6})\b', s, re.IGNORECASE)
    if m_sm:
        code = m_sm.group(1).upper()
        for k, v in SAMSUNG_MODELOS.items():
            if code.startswith(k):
                return v
        return f"Samsung Galaxy ({code})"
        
    # 2. Búsqueda de modelos Xiaomi / Redmi
    for k, v in XIAOMI_MODELOS.items():
        if k.lower() in s_l:
            return v
            
    # 3. Modelos Apple iPhone / iPad específicos o genéricos
    if 'iphone 16 pro max' in s_l:
        return 'iPhone 16 Pro Max'
    elif 'iphone 16 pro' in s_l:
        return 'iPhone 16 Pro'
    elif 'iphone 16 plus' in s_l:
        return 'iPhone 16 Plus'
    elif 'iphone 16' in s_l:
        return 'iPhone 16'
    elif 'iphone 15 pro max' in s_l:
        return 'iPhone 15 Pro Max'
    elif 'iphone 15 pro' in s_l:
        return 'iPhone 15 Pro'
    elif 'iphone 15' in s_l:
        return 'iPhone 15'
    elif 'iphone 14 pro max' in s_l:
        return 'iPhone 14 Pro Max'
    elif 'iphone 14 pro' in s_l:
        return 'iPhone 14 Pro'
    elif 'iphone 14 plus' in s_l:
        return 'iPhone 14 Plus'
    elif 'iphone 14' in s_l:
        return 'iPhone 14'
    elif 'iphone 13 pro max' in s_l:
        return 'iPhone 13 Pro Max'
    elif 'iphone 13 pro' in s_l:
        return 'iPhone 13 Pro'
    elif 'iphone 13' in s_l:
        return 'iPhone 13'
    elif 'iphone 12' in s_l:
        return 'iPhone 12'
    elif 'iphone 11' in s_l:
        return 'iPhone 11'
    elif 'ipad pro' in s_l:
        return 'iPad Pro'
    elif 'ipad air' in s_l:
        return 'iPad Air'
    elif 'ipad mini' in s_l:
        return 'iPad mini'
    elif 'ipad' in s_l:
        return 'Apple iPad'
    elif 'iphone' in s_l:
        m_ip = re.search(r'(iphone[^\;\)]*)', s, re.IGNORECASE)
        return m_ip.group(1).strip() if m_ip else 'Apple iPhone'
        
    # 4. Sistemas de escritorio
    if 'windows 10/11' in s_l or 'windows nt 10.0' in s_l or 'windows 11' in s_l or 'windows 10' in s_l:
        return 'PC con Windows'
    elif 'windows' in s_l:
        return 'PC con Windows'
    elif 'macintosh' in s_l or 'mac os' in s_l or 'mac (macos)' in s_l:
        return 'Apple Mac (macOS)'
    elif 'linux' in s_l:
        return 'PC con Linux'
        
    # 5. Android genérico
    if 'android' in s_l:
        m_par = re.search(r'android\s*\(([^\)]+)\)', s, re.IGNORECASE)
        if m_par:
            interior = m_par.group(1).strip()
            res_int = resolver_modelo_comercial(interior)
            if res_int != 'Dispositivo Web' and 'android' not in res_int.lower():
                return res_int
            return f"Android ({interior})"
        return 'Teléfono Android'
        
    return s

def desglosar_dispositivo_info(disp_str):
    """
    Desglosa una cadena de dispositivo (ej: 'Google Chrome en Android (SM-A107M)' o 'Chrome en Windows')
    en sus componentes limpios y profesionales:
    - modelo: 'Samsung Galaxy A10s' (título principal en negrita)
    - navegador: 'Google Chrome' (subtítulo en letras pequeñas)
    - icono: '📱' / '💻' / '📟'
    """
    raw = str(disp_str or '').strip()
    if not raw:
        return {'modelo': 'Dispositivo Seguro', 'navegador': 'Navegador Web', 'icono': '🌐'}
        
    raw_l = raw.lower()
    
    # 1. Detección del navegador
    nav = "Navegador Web"
    if "app el minero gris" in raw_l or "elminerogris-app" in raw_l:
        nav = "App El Minero Gris"
    elif "telegram" in raw_l:
        nav = "Telegram In-App"
    elif "instagram" in raw_l:
        nav = "Instagram In-App"
    elif "facebook" in raw_l or "fbav" in raw_l:
        nav = "Facebook In-App"
    elif "samsung internet" in raw_l or "samsungbrowser" in raw_l:
        nav = "Samsung Internet"
    elif "google chrome" in raw_l or "chrome" in raw_l or "crios" in raw_l:
        nav = "Google Chrome"
    elif "microsoft edge" in raw_l or "edg" in raw_l:
        nav = "Microsoft Edge"
    elif "opera" in raw_l or "opr" in raw_l:
        nav = "Opera"
    elif "firefox" in raw_l or "fxios" in raw_l:
        nav = "Mozilla Firefox"
    elif "safari" in raw_l:
        nav = "Apple Safari"
        
    # 2. Detección del modelo de hardware
    parte_hw = raw
    if " en " in raw:
        parte_hw = raw.split(" en ", 1)[1].strip()
    elif " · " in raw:
        parte_hw = raw.split(" · ", 1)[0].strip()
        
    modelo = resolver_modelo_comercial(parte_hw)
    if modelo in ('Dispositivo', 'Dispositivo Web'):
        modelo = resolver_modelo_comercial(raw)
        
    # 3. Asignación de ícono
    mod_l = modelo.lower()
    if 'ipad' in mod_l or 'tablet' in mod_l:
        ico = '📟'
    elif 'iphone' in mod_l or 'samsung' in mod_l or 'galaxy' in mod_l or 'xiaomi' in mod_l or 'redmi' in mod_l or 'android' in mod_l or 'pixel' in mod_l or 'motorola' in mod_l or 'teléfono' in mod_l:
        ico = '📱'
    elif 'windows' in mod_l or 'pc' in mod_l:
        ico = '💻'
    elif 'mac' in mod_l:
        ico = '🖥️'
    elif 'linux' in mod_l:
        ico = '🐧'
    else:
        ico = '🌐'
        
    return {
        'modelo': modelo,
        'navegador': nav,
        'icono': ico
    }

def parsear_dispositivo(ua_string, client_hint=None):
    """
    Genera un nombre detallado y profesional de dispositivo, navegador y sistema operativo.
    Soporta discriminación exacta de iPhone, iPad, Samsung Galaxy (resuelve SM-XXXX a nombre comercial),
    Android, Chrome, Safari, Firefox, Edge, etc.
    """
    ua = str(ua_string or "")
    ua_l = ua.lower()
    
    # 1. Si el cliente envió un indicio de hardware específico (detectado vía JS)
    hint_clean = str(client_hint or "").strip()
    
    # 2. Detección de Sistema y Dispositivo
    os_name = "Dispositivo"
    
    if hint_clean and ("iphone" in hint_clean.lower() or "ipad" in hint_clean.lower() or "galaxy" in hint_clean.lower() or "pixel" in hint_clean.lower() or "sm-" in hint_clean.lower()):
        os_name = resolver_modelo_comercial(hint_clean)
    elif "ipad" in ua_l or ("macintosh" in ua_l and "mobile" in ua_l):
        os_name = "Apple iPad"
    elif "iphone" in ua_l:
        os_name = "Apple iPhone"
    elif "android" in ua_l:
        m = re.search(r'android\s+[\d\.]+;\s*([^;\)]+)', ua, re.IGNORECASE)
        if m and m.group(1):
            modelo_raw = m.group(1).strip()
            if "build" in modelo_raw.lower():
                modelo_raw = modelo_raw.split("Build")[0].strip()
            os_name = resolver_modelo_comercial(modelo_raw)
        else:
            os_name = "Teléfono Android"
    elif "windows nt 10.0" in ua_l or "windows 11" in ua_l:
        os_name = "PC con Windows"
    elif "windows" in ua_l:
        os_name = "PC con Windows"
    elif "macintosh" in ua_l or "mac os x" in ua_l:
        os_name = "Apple Mac (macOS)"
    elif "linux" in ua_l:
        os_name = "PC con Linux"

    # 3. Detección precisa de Navegador o App
    browser = "Navegador Web"
    if "elminerogris-app" in ua_l:
        browser = "App El Minero Gris"
    elif "telegram" in ua_l:
        browser = "Telegram In-App"
    elif "instagram" in ua_l:
        browser = "Instagram In-App"
    elif "fbav" in ua_l or "fban" in ua_l:
        browser = "Facebook In-App"
    elif "crios/" in ua_l:
        browser = "Google Chrome"
    elif "fxios/" in ua_l:
        browser = "Mozilla Firefox"
    elif "edgios/" in ua_l or "edg/" in ua_l:
        browser = "Microsoft Edge"
    elif "opios/" in ua_l or "opr/" in ua_l or "opera" in ua_l:
        browser = "Opera"
    elif "samsungbrowser/" in ua_l:
        browser = "Samsung Internet"
    elif "chrome/" in ua_l and "safari/" in ua_l:
        browser = "Google Chrome"
    elif "safari/" in ua_l and "chrome" not in ua_l:
        browser = "Apple Safari"
    elif "firefox/" in ua_l:
        browser = "Mozilla Firefox"

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

def enmascarar_email(email_str):
    """Enmascara un correo para visualización segura (ej: c***o@gmail.com)."""
    s = str(email_str or "").strip()
    if "@" not in s:
        return s
    usuario, dominio = s.split("@", 1)
    if len(usuario) <= 2:
        u_mask = usuario[0] + "*"
    else:
        u_mask = usuario[0] + "*" * (len(usuario) - 2) + usuario[-1]
    return f"{u_mask}@{dominio}"

def enviar_email_codigo(email_dest, codigo, username=""):
    """
    Envía un código OTP de 6 dígitos por correo usando SMTP configurado
    (Gmail, Brevo, SendGrid, Amazon SES o SMTP personalizado).
    Si no hay credenciales SMTP configuradas en variables de entorno, registra el código
    en los logs para permitir pruebas en desarrollo sin bloquear al usuario.
    """
    if not email_dest or not codigo:
        return False

    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_port = a_int_seguro(os.environ.get("SMTP_PORT", "587"), 587)
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    smtp_from = os.environ.get("SMTP_FROM", "").strip() or f"El Minero Gris <{smtp_user or 'no-reply@elminerogris.com'}>"

    asunto = f"🔐 Código de Verificación: {codigo} — El Minero Gris"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="UTF-8"></head>
    <body style="margin:0; padding:20px; font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color:#0b1320; color:#e2e8f0;">
      <div style="max-width:520px; margin:0 auto; background:#111b2b; border-radius:12px; border:1px solid #1e293b; overflow:hidden; box-shadow:0 10px 25px rgba(0,0,0,0.5);">
        <div style="background:linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding:25px; text-align:center; border-bottom:1px solid #334155;">
          <h1 style="margin:0; font-size:22px; color:#38bdf8; letter-spacing:1px;">⚡ EL MINERO GRIS</h1>
          <p style="margin:6px 0 0 0; font-size:13px; color:#94a3b8;">Monitoreo Profesional de Minería ASIC</p>
        </div>
        <div style="padding:30px 25px;">
          <p style="margin-top:0; font-size:15px; color:#f1f5f9;">¡Hola, <b>{username or 'Minero'}</b>!</p>
          <p style="font-size:14px; line-height:1.6; color:#cbd5e1;">Gracias por registrarte en <b>El Minero Gris</b>. Para activar tu cuenta y proteger el acceso a tus mineros, ingresa el siguiente código de verificación:</p>
          <div style="text-align:center; margin:30px 0;">
            <div style="display:inline-block; background:#0f172a; border:2px solid #38bdf8; border-radius:10px; padding:15px 35px; font-size:32px; font-weight:bold; letter-spacing:8px; color:#38bdf8; font-family:monospace; box-shadow:0 0 20px rgba(56,189,248,0.25);">
              {codigo}
            </div>
            <p style="margin:10px 0 0 0; font-size:12px; color:#94a3b8;">⏱️ Válido durante 15 minutos</p>
          </div>
          <p style="font-size:13px; color:#94a3b8; line-height:1.5;">Si tú no solicitaste este registro, puedes ignorar este mensaje de forma segura; la cuenta no podrá activarse sin este código.</p>
        </div>
        <div style="background:#0b1320; padding:15px 25px; text-align:center; border-top:1px solid #1e293b; font-size:11px; color:#64748b;">
          El Minero Gris · Plataforma de Control & Telemetría ASIC · Santa Cruz, Bolivia
        </div>
      </div>
    </body>
    </html>
    """

    texto_plano = (
        f"El Minero Gris — Código de Verificación\n\n"
        f"Hola {username or 'Minero'},\n\n"
        f"Tu código de verificación para activar tu cuenta es: {codigo}\n\n"
        f"Este código expira en 15 minutos.\n\n"
        f"Si no solicitaste este código, ignora este mensaje."
    )

    if not smtp_host or not smtp_user or not smtp_pass:
        print(f"⚠️ [DEV MODE - SMTP NO CONFIGURADO] Código para '{username}' ({email_dest}): {codigo}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = smtp_from
        msg["To"] = email_dest
        msg.attach(MIMEText(texto_plano, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=8) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_from, [email_dest], msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=8) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_from, [email_dest], msg.as_string())
        return True
    except Exception as e:
        print(f"❌ Error enviando email a {email_dest}: {e}")
        return False

