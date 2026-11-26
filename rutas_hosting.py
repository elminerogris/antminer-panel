"""
==============================================================================
 EL MINERO GRIS — Cuentas de Hosting con Login Propio y Permisos Granulares
==============================================================================
Sistema profesional tipo FarmGod: cada cliente de hosting tiene su propia
cuenta con usuario/contraseña, puede ver SOLO sus equipos asignados, y el
dueño de la granja define qué acciones puede ejecutar cada cliente.
"""

from flask import Blueprint, redirect, request, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2.extras, json, secrets, threading, time
from datetime import datetime

from db import get_db
from utils import (
    login_required, a_int_seguro, a_float_seguro,
    nombre_inteligente_minero, calcular_segundos_desconectado,
    ordenar_ip_key, render_seguro
)

hosting_bp = Blueprint('hosting_bp', __name__)

# Rate limiting para login de clientes hosting
_hc_rate_lock = threading.Lock()
_hc_rate_buckets = {}

def _verificar_rate_limit_hosting(clave, max_intentos=8, ventana_seg=300):
    ahora = time.time()
    with _hc_rate_lock:
        hist = [ts for ts in _hc_rate_buckets.get(clave, []) if ahora - ts < ventana_seg]
        if len(hist) >= max_intentos:
            _hc_rate_buckets[clave] = hist
            return True
        hist.append(ahora)
        _hc_rate_buckets[clave] = hist
        return False

PERMISOS_DEFECTO = {
    "ver_estado": True,
    "ver_logs": False,
    "reiniciar": False,
    "cambiar_pools": False,
    "blink_led": False
}

def parsear_permisos(permisos_json_str):
    try:
        p = json.loads(permisos_json_str or '{}')
        return {k: bool(p.get(k, v)) for k, v in PERMISOS_DEFECTO.items()}
    except Exception:
        return PERMISOS_DEFECTO.copy()


# ==================== GESTIÓN DE CUENTAS DE HOSTING (PARA EL DUEÑO) ====================

@hosting_bp.route('/hosting/cuentas', methods=['GET', 'POST'])
@login_required
def gestion_hosting_cuentas():
    usr_actual = session.get('usuario', 'Admin')
    msg = None
    msg_tipo = 'ok'
    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'crear_cuenta':
            u_cli = (request.form.get('username') or '').strip()
            p_cli = request.form.get('password') or ''
            nombre = (request.form.get('nombre_display') or 'Cliente').strip()[:60]
            email = (request.form.get('email') or '').strip()[:120]
            ips_sel = request.form.getlist('ips')
            notas = (request.form.get('notas') or '').strip()[:200]

            # Parsear permisos del formulario
            permisos = {
                "ver_estado": True,  # Siempre activo
                "ver_logs": bool(request.form.get('perm_ver_logs')),
                "reiniciar": bool(request.form.get('perm_reiniciar')),
                "cambiar_pools": bool(request.form.get('perm_cambiar_pools')),
                "blink_led": bool(request.form.get('perm_blink_led'))
            }

            if len(u_cli) < 3 or len(u_cli) > 30 or ' ' in u_cli:
                msg = "El usuario debe tener entre 3 y 30 caracteres sin espacios."
                msg_tipo = 'error'
            elif len(p_cli) < 6:
                msg = "La contraseña debe tener al menos 6 caracteres."
                msg_tipo = 'error'
            elif not ips_sel:
                msg = "Selecciona al menos un equipo para asignar al cliente."
                msg_tipo = 'error'
            else:
                # Verificar que no existe ya
                cur.execute("SELECT id FROM hosting_cuentas WHERE owner=%s AND LOWER(username)=LOWER(%s)", (usr_actual, u_cli))
                if cur.fetchone():
                    msg = f"Ya existe una cuenta de hosting con usuario '{u_cli}'."
                    msg_tipo = 'error'
                else:
                    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    cur.execute("""
                        INSERT INTO hosting_cuentas (owner, username, password, nombre_display, email, ips_json, permisos_json, activo, fecha_creacion, notas)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                    """, (usr_actual, u_cli, generate_password_hash(p_cli), nombre, email,
                          json.dumps(ips_sel), json.dumps(permisos), ahora, notas))
                    msg = f"✅ Cuenta de hosting '{u_cli}' creada. El cliente puede ingresar desde la página de login de hosting."

        elif accion == 'eliminar_cuenta':
            cid = a_int_seguro(request.form.get('cuenta_id'), 0)
            cur.execute("DELETE FROM hosting_cuentas WHERE id=%s AND owner=%s", (cid, usr_actual))
            msg = "Cuenta de hosting eliminada."

        elif accion == 'toggle_cuenta':
            cid = a_int_seguro(request.form.get('cuenta_id'), 0)
            cur.execute("SELECT activo FROM hosting_cuentas WHERE id=%s AND owner=%s", (cid, usr_actual))
            row = cur.fetchone()
            if row:
                nuevo = 0 if row['activo'] == 1 else 1
                cur.execute("UPDATE hosting_cuentas SET activo=%s WHERE id=%s AND owner=%s", (nuevo, cid, usr_actual))
                msg = f"Cuenta {'activada' if nuevo == 1 else 'suspendida'}."

        elif accion == 'actualizar_cuenta':
            cid = a_int_seguro(request.form.get('cuenta_id'), 0)
            ips_sel = request.form.getlist('ips')
            nombre = (request.form.get('nombre_display') or 'Cliente').strip()[:60]
            notas = (request.form.get('notas') or '').strip()[:200]
            permisos = {
                "ver_estado": True,
                "ver_logs": bool(request.form.get('perm_ver_logs')),
                "reiniciar": bool(request.form.get('perm_reiniciar')),
                "cambiar_pools": bool(request.form.get('perm_cambiar_pools')),
                "blink_led": bool(request.form.get('perm_blink_led'))
            }
            new_pass = (request.form.get('new_password') or '').strip()
            if new_pass and len(new_pass) >= 6:
                cur.execute("UPDATE hosting_cuentas SET nombre_display=%s, ips_json=%s, permisos_json=%s, notas=%s, password=%s WHERE id=%s AND owner=%s",
                            (nombre, json.dumps(ips_sel), json.dumps(permisos), notas, generate_password_hash(new_pass), cid, usr_actual))
            else:
                cur.execute("UPDATE hosting_cuentas SET nombre_display=%s, ips_json=%s, permisos_json=%s, notas=%s WHERE id=%s AND owner=%s",
                            (nombre, json.dumps(ips_sel), json.dumps(permisos), notas, cid, usr_actual))
            msg = "Cuenta actualizada correctamente."

    # Cargar datos para la vista
    cur.execute("SELECT * FROM hosting_cuentas WHERE owner=%s ORDER BY id DESC", (usr_actual,))
    cuentas = cur.fetchall()
    for c in cuentas:
        try:
            c['ips_list'] = json.loads(c.get('ips_json') or '[]')
        except:
            c['ips_list'] = []
        c['permisos'] = parsear_permisos(c.get('permisos_json'))

    cur.execute("SELECT ip, modelo, worker1 FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas_m = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    mineros_disp = [
        {'ip': m['ip'], 'nombre': nombre_inteligente_minero(m['ip'], nombres_dict, m.get('worker1')), 'modelo': m.get('modelo') or 'Antminer'}
        for m in filas_m
    ]

    return render_seguro('hosting_cuentas.html', active_page='hosting_cuentas', active_subnav='hosting_cuentas',
                         cuentas=cuentas, mineros_disp=mineros_disp, msg=msg, msg_tipo=msg_tipo)


# ==================== LOGIN PÚBLICO PARA CLIENTES DE HOSTING ====================

@hosting_bp.route('/hosting/login', methods=['GET', 'POST'])
def hosting_login():
    error = None
    if request.method == 'POST':
        xff = request.headers.get('X-Forwarded-For', '')
        ip_cli = xff.split(',')[0].strip() if xff else (request.remote_addr or 'unknown')
        if _verificar_rate_limit_hosting(f"hc_login:{ip_cli}", max_intentos=8, ventana_seg=300):
            error = "Demasiados intentos. Espera 5 minutos."
            return render_seguro('hosting_login.html', error=error)

        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''

        if not u or not p:
            error = "Ingresa usuario y contraseña."
        else:
            con = get_db(); con.autocommit = True
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM hosting_cuentas WHERE LOWER(username)=LOWER(%s) AND activo=1", (u,))
            cuenta = cur.fetchone()

            if not cuenta or not check_password_hash(cuenta['password'], p):
                error = "Credenciales incorrectas o cuenta suspendida."
            else:
                ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("UPDATE hosting_cuentas SET ultimo_acceso=%s WHERE id=%s", (ahora, cuenta['id']))

                session['hosting_cliente_id'] = cuenta['id']
                session['hosting_owner'] = cuenta['owner']
                session['hosting_username'] = cuenta['username']
                session['hosting_nombre'] = cuenta['nombre_display']
                session['hosting_permisos'] = parsear_permisos(cuenta.get('permisos_json'))
                return redirect('/hosting/dashboard')

    return render_seguro('hosting_login.html', error=error)


@hosting_bp.route('/hosting/logout')
def hosting_logout():
    for k in list(session.keys()):
        if k.startswith('hosting_'):
            session.pop(k, None)
    return redirect('/hosting/login')


def hosting_login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'hosting_cliente_id' not in session:
            return redirect('/hosting/login')
        return f(*args, **kwargs)
    return decorated


# ==================== DASHBOARD DEL CLIENTE DE HOSTING ====================

@hosting_bp.route('/hosting/dashboard')
@hosting_login_required
def hosting_dashboard():
    hc_id = session['hosting_cliente_id']
    owner = session['hosting_owner']
    permisos = session.get('hosting_permisos', PERMISOS_DEFECTO)

    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT ips_json, permisos_json, activo FROM hosting_cuentas WHERE id=%s AND owner=%s LIMIT 1", (hc_id, owner))
    cuenta = cur.fetchone()
    if not cuenta or cuenta['activo'] != 1:
        session.clear()
        return redirect('/hosting/login')

    try:
        ips_permitidas = json.loads(cuenta.get('ips_json') or '[]')
    except:
        ips_permitidas = []

    permisos = parsear_permisos(cuenta.get('permisos_json'))
    session['hosting_permisos'] = permisos

    if not ips_permitidas:
        return render_seguro('hosting_dashboard.html',
                             titulo=session.get('hosting_nombre', 'Mi Panel'),
                             mineros=[], totales=0, online=0, hashrate_total=0.0,
                             consumo_kw=0.0, permisos=permisos, username=session.get('hosting_username'))

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s AND ip = ANY(%s)", (owner, ips_permitidas))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (owner,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    mineros_pub = []
    online_cnt = 0
    hr_total = 0.0
    cw_total = 0.0

    for m in filas:
        ip = m['ip']
        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_on = segs < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_on else 0.0
        tm = a_int_seguro(m.get('temp_max'), 0) if es_on else 0
        cw = a_float_seguro(m.get('consumo_w'), 0.0) if es_on else 0.0
        if es_on:
            online_cnt += 1
            if (m.get('unidad') or 'TH/s') == 'TH/s':
                hr_total += hr
            cw_total += cw

        t_amb = a_int_seguro(m.get('temp_amb'), 0) if es_on else 0
        fw = m.get('firmware') or 'Desconocido'
        fw_tipo = m.get('fw_tipo') or 'Stock'

        mineros_pub.append({
            'ip': ip,
            'nombre': nombre_inteligente_minero(ip, nombres_dict, m.get('worker1')),
            'modelo': m.get('modelo') or 'Antminer',
            'online': es_on,
            'hashrate': hr,
            'unidad': m.get('unidad') or 'TH/s',
            'temp': tm,
            'temp_amb': t_amb,
            'consumo_kw': round(cw / 1000.0, 2),
            'uptime': (m.get('uptime') or 'En línea') if es_on else 'Sin conexión',
            'firmware': fw,
            'fw_tipo': fw_tipo,
            'fans': m.get('fans') or '-',
            'pool1': m.get('pool1') or '-',
            'worker1': m.get('worker1') or '-',
            'errores': a_int_seguro(m.get('errores'), 0),
            'agent_id': m.get('agent_id') or 'default'
        })

    return render_seguro(
        'hosting_dashboard.html',
        titulo=session.get('hosting_nombre', 'Mi Panel'),
        mineros=mineros_pub,
        totales=len(mineros_pub),
        online=online_cnt,
        hashrate_total=round(hr_total, 2),
        consumo_kw=round(cw_total / 1000.0, 2),
        permisos=permisos,
        username=session.get('hosting_username')
    )


# ==================== ACCIONES REMOTAS DEL CLIENTE DE HOSTING ====================

@hosting_bp.route('/hosting/cmd/<ip>/<accion>', methods=['POST'])
@hosting_login_required
def hosting_ejecutar_comando(ip, accion):
    hc_id = session['hosting_cliente_id']
    owner = session['hosting_owner']
    permisos = session.get('hosting_permisos', PERMISOS_DEFECTO)

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Verificar que la IP pertenece a este cliente
    cur.execute("SELECT ips_json FROM hosting_cuentas WHERE id=%s AND owner=%s AND activo=1", (hc_id, owner))
    cuenta = cur.fetchone()
    if not cuenta:
        return jsonify({"status": "error", "msg": "Cuenta no válida"}), 403

    try:
        ips_permitidas = json.loads(cuenta.get('ips_json') or '[]')
    except:
        ips_permitidas = []

    if ip not in ips_permitidas:
        return jsonify({"status": "error", "msg": "No tienes acceso a este equipo"}), 403

    # Verificar permisos para la acción
    acciones_permisos = {
        'restart': 'reiniciar',
        'blink': 'blink_led',
        'guardar_pools': 'cambiar_pools'
    }

    permiso_requerido = acciones_permisos.get(accion)
    if permiso_requerido and not permisos.get(permiso_requerido):
        return jsonify({"status": "error", "msg": f"No tienes permiso para: {permiso_requerido}"}), 403

    # Ejecutar comando
    cur.execute("SELECT agent_id FROM mineros_activos WHERE owner=%s AND ip=%s LIMIT 1", (owner, ip))
    row_ag = cur.fetchone()
    ag_id = str((row_ag or {}).get('agent_id') or 'default').strip()

    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if accion == 'guardar_pools':
        data = request.get_json(silent=True) or {}
        p1 = str(data.get('pool1') or '').strip()
        w1 = str(data.get('worker1') or '').strip()
        pa1 = str(data.get('pass1') or '123').strip()
        cmd_str = f"guardar_pools|{p1}|{w1}|{pa1}|||||||"
    else:
        cmd_str = accion

    from tunel import enviar_orden_tunel
    ok_ws = enviar_orden_tunel(owner, ip, cmd_str, agent_id=ag_id)
    estado = 'enviado' if ok_ws else 'pendiente'
    cur.execute("INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, %s, %s, %s, %s)",
                (owner, ag_id, ip, cmd_str, estado, ahora))

    return jsonify({"status": "ok", "msg": f"Comando '{accion}' enviado a {ip}"})


# ==================== API: VER LOG DE MINERO (HOSTING) ====================

@hosting_bp.route('/hosting/api/ver_log/<ip>')
@hosting_login_required
def hosting_ver_log(ip):
    hc_id = session['hosting_cliente_id']
    owner = session['hosting_owner']
    permisos = session.get('hosting_permisos', PERMISOS_DEFECTO)

    if not permisos.get('ver_logs'):
        return jsonify({"status": "error", "msg": "No tienes permiso para ver logs"}), 403

    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Verificar acceso
    cur.execute("SELECT ips_json FROM hosting_cuentas WHERE id=%s AND owner=%s AND activo=1", (hc_id, owner))
    cuenta = cur.fetchone()
    try:
        ips_permitidas = json.loads((cuenta or {}).get('ips_json') or '[]')
    except:
        ips_permitidas = []

    if ip not in ips_permitidas:
        return jsonify({"status": "error", "msg": "Sin acceso"}), 403

    cur.execute("SELECT contenido, fecha FROM logs_mineros WHERE owner=%s AND ip=%s LIMIT 1", (owner, ip))
    row = cur.fetchone()
    if not row:
        return jsonify({"ok": False, "contenido": "Sin logs disponibles para este equipo."})
    return jsonify({"ok": True, "contenido": row['contenido'], "fecha": row['fecha']})
