from flask import Flask, request, session
import psycopg2.extras, os, time, traceback
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
app.secret_key = os.environ.get("SECRET_KEY", "super_secreta_antminer_tools")

# Sesiones persistentes por 30 días (se renuevan automáticamente al usar el panel)
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(
    SESSION_REFRESH_EACH_REQUEST=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER") or os.environ.get("DATABASE_URL")),
    SESSION_COOKIE_SAMESITE='Lax'
)

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
    if es_super_admin():
        html_error = f"""
        <div style="background:#0f111a;color:#fff;font-family:monospace;padding:30px;min-height:100vh;">
            <h2 style="color:#ff5252;">⚠️ Diagnóstico Técnico de El Minero Gris (Admin)</h2>
            <p style="color:#a5a7b5;">Detalle técnico detectado en el servidor:</p>
            <pre style="background:#000;color:#00e676;padding:20px;border-radius:8px;border:1px solid #333;overflow-x:auto;">{tb}</pre>
            <a href="/" style="display:inline-block;margin-top:15px;background:#3366ff;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-family:sans-serif;font-weight:bold;">🔄 Reintentar / Ir al Inicio</a>
        </div>
        """
    else:
        html_error = """
        <div style="background:#0f111a;color:#fff;font-family:sans-serif;padding:40px;min-height:100vh;text-align:center;">
            <h2 style="color:#ff5252;">⚠️ Ocurrió un inconveniente temporal</h2>
            <p style="color:#a5a7b5;">Estamos trabajando para restablecer el servicio. Intenta recargar en unos segundos.</p>
            <a href="/" style="display:inline-block;margin-top:15px;background:#3366ff;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold;">🔄 Volver al Inicio</a>
        </div>
        """
    return html_error, 500

@app.before_request
def asegurar_bd():
    asegurar_bd_inicializada()
    if 'usuario' in session:
        session.permanent = True
        if DB_URL and not request.path.startswith('/static/'):
            ahora_ts = time.time()
            if ahora_ts - a_float_seguro(session.get('last_seen_ts'), 0) > 300:
                session['last_seen_ts'] = ahora_ts
                try:
                    con = get_db()
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
