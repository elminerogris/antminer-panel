from flask import Flask, request, session, redirect, url_for
import psycopg2.extras, os, time, traceback, secrets, hashlib
from datetime import datetime, timedelta

from db import DB_URL, get_db, cerrar_conexion_bd, init_db, asegurar_bd_inicializada
from utils import (
    LIMITE_MINEROS_GRATIS, es_super_admin, a_int_seguro, a_float_seguro, evaluar_plan_usuario
)
from rutas_admin import admin_bp
from rutas_panel import panel_bp
from rutas_agente import agente_bp
from tunel import init_tunel
from monitor_offline import iniciar_monitor_offline

app = Flask(__name__, template_folder='templates')
# Blindar SECRET_KEY: usa variable de entorno de Render o genera clave criptográfica aleatoria en memoria
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Sesiones persistentes por 30 días con cookies blindadas contra XSS y CSRF
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(
    SESSION_REFRESH_EACH_REQUEST=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER") or os.environ.get("DATABASE_URL")),
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=10 * 1024 * 1024  # Máximo 10 MB de payload (Anti-DoS / Buffer Exhaustion)
)

@app.errorhandler(413)
def request_entity_too_large(error):
    return "Payload demasiado grande (máximo 10 MB permitido)", 413

# Cabeceras de Seguridad HTTP (Anti-Clickjacking, Anti-MIME sniffing, CSP y HTTPS forzado)
@app.after_request
def agregar_cabeceras_seguridad(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com https://translate.google.com https://translate.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://tile.openstreetmap.org https://*.google.com https://mt0.google.com https://mt1.google.com https://mt2.google.com https://mt3.google.com https://api.qrserver.com https://unpkg.com https://translate.google.com https://www.gstatic.com; "
        "connect-src 'self' wss: ws: https://api.qrserver.com https://api.binance.com https://api.coinbase.com https://api.coingecko.com https://mempool.space https://api.open-meteo.com https://geocoding-api.open-meteo.com https://api.bigdatacloud.net https://ipapi.co https://translate.googleapis.com; "
        "object-src 'none'; "
        "frame-ancestors 'none';"
    )
    if os.environ.get("RENDER") or os.environ.get("DATABASE_URL"):
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

# Registrar los módulos principales (Blueprints)
app.register_blueprint(admin_bp)
app.register_blueprint(panel_bp)
app.register_blueprint(agente_bp)

# Registrar módulos Pro (Racks, Hosting, Técnicos) y Pagos USDT si están presentes
try:
    from rutas_pro import pro_bp
    app.register_blueprint(pro_bp)
except ImportError:
    pass

try:
    from rutas_pagos import pagos_bp
    app.register_blueprint(pagos_bp)
except ImportError:
    pass

try:
    from rutas_hosting import hosting_bp
    app.register_blueprint(hosting_bp)
except ImportError:
    pass

try:
    from rutas_firmware import firmware_bp
    app.register_blueprint(firmware_bp)
except ImportError:
    pass

# Inicializar túnel WebSocket para comandos en tiempo real
init_tunel(app)

# Inicializar BD al arrancar
try:
    init_db()
except Exception as e:
    print(f"Error init_db: {e}")

# Iniciar vigilante en segundo plano para detectar cortes de luz o internet por sede
iniciar_monitor_offline()

@app.teardown_appcontext
def teardown_db(exception=None):
    cerrar_conexion_bd(exception)

@app.errorhandler(Exception)
def manejar_error_global(e):
    tb = traceback.format_exc()
    print(f"[ERROR 500]: {tb}")
    try:
        from utils import notificar_alerta_seguridad
        notificar_alerta_seguridad("Error 500 Interno", f"Ruta: {request.path}\nDetalle: {str(e)[:200]}", request.remote_addr or "")
    except Exception:
        pass

    if es_super_admin():
        import html
        tb_escapado = html.escape(tb)
        html_error = f"""
        <div style="background:#0f111a;color:#fff;font-family:monospace;padding:30px;min-height:100vh;">
            <h2 style="color:#ff5252;">⚠️ Diagnóstico Técnico de El Minero Gris (Admin)</h2>
            <p style="color:#a5a7b5;">Detalle técnico detectado en el servidor:</p>
            <pre style="background:#000;color:#00e676;padding:20px;border-radius:8px;border:1px solid #333;overflow-x:auto;">{tb_escapado}</pre>
            <div style="margin-top:20px;display:flex;gap:12px;">
                <a href="/resumen" style="background:#3366ff;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-family:sans-serif;font-weight:bold;">📊 Ir a Resumen</a>
                <a href="/login" style="background:#4b5563;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-family:sans-serif;font-weight:bold;">🔑 Iniciar Sesión</a>
            </div>
        </div>
        """
        return html_error, 500

    html_error = """
    <div style="background:#0f111a;color:#fff;font-family:sans-serif;padding:40px;min-height:100vh;text-align:center;">
        <h2 style="color:#ff5252;">⚠️ Ocurrió un inconveniente temporal</h2>
        <p style="color:#a5a7b5;">El sistema ha registrado el evento y nuestro equipo técnico ha sido notificado.</p>
        <div style="margin-top:20px;display:flex;gap:12px;justify-content:center;">
            <a href="/" style="background:#3366ff;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold;">🔄 Reintentar</a>
            <a href="/login" style="background:#4b5563;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold;">🔑 Iniciar Sesión</a>
        </div>
    </div>
    """
    return html_error, 500

@app.before_request
def asegurar_bd():
    asegurar_bd_inicializada()

    # 1. Recuperar sesión automáticamente si el dispositivo fue recordado previamente (Dispositivo de Confianza)
    if 'usuario' not in session and DB_URL and not request.path.startswith('/static/'):
        disp_token = request.cookies.get('emg_trusted_device')
        if disp_token:
            try:
                con_t = get_db()
                cur_t = con_t.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                tok_h = hashlib.sha256(disp_token.encode('utf-8')).hexdigest()
                ahora_s = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur_t.execute(
                    "SELECT username FROM dispositivos_confianza WHERE token_hash=%s AND expira_en > %s LIMIT 1",
                    (tok_h, ahora_s)
                )
                r_disp = cur_t.fetchone()
                if r_disp:
                    usr_trusted = r_disp['username']
                    cur_t.execute("SELECT * FROM usuarios WHERE LOWER(username)=LOWER(%s) AND suspendido=0", (usr_trusted,))
                    usr_obj = cur_t.fetchone()
                    if usr_obj:
                        session.permanent = True
                        session['usuario'] = usr_obj['username']
                        session['trusted_device_hash'] = tok_h
                        session['plan'] = usr_obj.get('plan', 'Gratis')
                        session['saldo'] = usr_obj.get('saldo', 0.0)
                        session['last_seen_ts'] = time.time()
                        session['admin_last_activity'] = time.time()
            except Exception as e_disp:
                print(f"[TRUSTED DEVICE AUTO-LOGIN]: {e_disp}")

    # 2. Manejo de sesión activa y verificación de revocación remota
    if 'usuario' in session:
        # VALIDACIÓN DE REVOCACIÓN REMOTA:
        # Si la petición trae cookie de dispositivo o la sesión está asociada a un dispositivo de confianza,
        # verificar en la base de datos que dicho dispositivo no haya sido eliminado (ej. revocado desde otra PC).
        if DB_URL and not request.path.startswith('/static/') and request.path != '/logout':
            disp_token_act = request.cookies.get('emg_trusted_device')
            tok_check = hashlib.sha256(disp_token_act.encode('utf-8')).hexdigest() if disp_token_act else session.get('trusted_device_hash')
            if tok_check:
                try:
                    con_chk_rev = get_db()
                    cur_chk_rev = con_chk_rev.cursor()
                    ahora_s_rev = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    cur_chk_rev.execute(
                        "SELECT id FROM dispositivos_confianza WHERE LOWER(username)=LOWER(%s) AND token_hash=%s AND expira_en > %s LIMIT 1",
                        (session['usuario'], tok_check, ahora_s_rev)
                    )
                    if not cur_chk_rev.fetchone():
                        # ¡Dispositivo eliminado/revocado! Destruir sesión y cookies de inmediato
                        session.clear()
                        resp = redirect('/login')
                        host_c = (request.host or '').split(':')[0].lower()
                        cookie_domain = '.elminerogris.com' if 'elminerogris.com' in host_c else None
                        resp.delete_cookie('emg_trusted_device', path='/', domain=cookie_domain)
                        resp.delete_cookie('emg_trusted_device', path='/')
                        resp.delete_cookie('session', path='/', domain=cookie_domain)
                        resp.delete_cookie('session', path='/')
                        return resp
                except Exception as e_rev:
                    print(f"[REVOKE CHECK ERROR]: {e_rev}")

        session.permanent = True

        # Para el Super Admin: verificar si el dispositivo actual es de confianza
        if es_super_admin():
            es_dispositivo_confianza = False
            disp_token = request.cookies.get('emg_trusted_device')
            if disp_token:
                try:
                    tok_h = hashlib.sha256(disp_token.encode('utf-8')).hexdigest()
                    ahora_s = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    con_chk = get_db()
                    cur_chk = con_chk.cursor()
                    cur_chk.execute(
                        "SELECT id FROM dispositivos_confianza WHERE LOWER(username)='admin' AND token_hash=%s AND expira_en > %s LIMIT 1",
                        (tok_h, ahora_s)
                    )
                    if cur_chk.fetchone():
                        es_dispositivo_confianza = True
                except:
                    pass

            if es_dispositivo_confianza:
                # Dispositivo de confianza: mantener sesión activa sin cerrar
                session['admin_last_activity'] = time.time()
            else:
                # Dispositivo público/no recordado: auto-cerrar tras 2 horas de inactividad
                ult_act = a_float_seguro(session.get('admin_last_activity'), 0)
                ahora_act = time.time()
                if ult_act > 0 and (ahora_act - ult_act) > 7200:
                    session.clear()
                    return redirect('/login')
                session['admin_last_activity'] = ahora_act

        if DB_URL and not request.path.startswith('/static/'):
            ahora_ts = time.time()
            if ahora_ts - a_float_seguro(session.get('last_seen_ts'), 0) > 300:
                session['last_seen_ts'] = ahora_ts
                try:
                    con = get_db()
                    if not con.autocommit:
                        con.rollback()
                        con.autocommit = True
                    cur = con.cursor()
                    usr_real = session.get('admin_real') or session.get('usuario_real') or session.get('usuario')
                    cur.execute(
                        "UPDATE usuarios SET ultima_conexion=%s WHERE username=%s",
                        (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), usr_real)
                    )
                except:
                    pass

@app.context_processor
def inyectar_globales():
    anuncio = ""
    omitidos_u = 0
    es_pro_u = False
    saldo_bonus_u = 0.0
    pro_dias_u = None
    limite_efectivo_u = LIMITE_MINEROS_GRATIS
    es_tecnico_u = (str(session.get('rol') or 'owner').lower() == 'tecnico')
    try:
        if 'usuario' in session and DB_URL:
            con = get_db()
            if not con.autocommit:
                con.rollback()
                con.autocommit = True
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT valor FROM config_global WHERE clave='anuncio_global'")
            row = cur.fetchone()
            if row and row.get('valor'):
                anuncio = str(row['valor']).strip()
            cur.execute(
                "SELECT username, plan, saldo, saldo_bonus, pro_hasta, limite_mineros, mineros_omitidos FROM usuarios WHERE username=%s",
                (session['usuario'],)
            )
            urow = cur.fetchone()
            if urow:
                es_pro_u, limite_efectivo_u, pro_dias_u = evaluar_plan_usuario(cur, urow)
                saldo_bonus_u = round(a_float_seguro(urow.get('saldo_bonus'), 0.0), 2)
                session['saldo'] = round(a_float_seguro(urow.get('saldo'), 0.0), 2)
                session['plan'] = 'Pro' if es_pro_u else 'Gratis'
                omitidos_u = a_int_seguro(urow.get('mineros_omitidos'), 0)
                if es_pro_u and limite_efectivo_u == 0:
                    omitidos_u = 0
    except:
        pass
    return {
        'anuncio_global': anuncio,
        'en_modo_soporte': bool(str(session.get('admin_real') or '').lower() == 'admin'),
        'es_super_admin': es_super_admin(),
        'es_tecnico': es_tecnico_u,
        'mineros_omitidos': omitidos_u,
        'es_pro_actual': es_pro_u,
        'limite_gratis': LIMITE_MINEROS_GRATIS,
        'limite_efectivo': limite_efectivo_u,
        'saldo_bonus_actual': saldo_bonus_u,
        'pro_dias_restantes': pro_dias_u
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
