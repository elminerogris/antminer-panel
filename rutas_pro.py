from flask import Blueprint, redirect, request, session, jsonify
from werkzeug.security import generate_password_hash
import psycopg2.extras, json, secrets
from datetime import datetime

from db import get_db
from utils import (
    login_required, a_int_seguro, a_float_seguro, generar_api_key,
    nombre_inteligente_minero, calcular_segundos_desconectado,
    ordenar_ip_key, render_seguro, es_super_admin
)

pro_bp = Blueprint('pro_bp', __name__)

# ==================== CONTROL DE ROL TÉCNICO (SIN TOCAR RUTAS_PANEL.PY) ====================
RUTAS_BLOQUEADAS_TECNICO = (
    '/plan', '/ajustes', '/hosting', '/tecnicos', '/admin',
    '/api/eliminar_mineros', '/api/granjas/eliminar', '/api/guardar_tarifa',
    '/hosting/cuentas', '/firmware', '/api/firmware'
)

@pro_bp.before_app_request
def controlar_acceso_tecnico():
    if 'usuario' not in session:
        return None

    # Resolver rol y cuenta padre la primera vez tras el login
    if 'rol' not in session:
        try:
            con = get_db()
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT username, parent_owner, rol FROM usuarios WHERE username=%s", (session['usuario'],))
            u = cur.fetchone()
            if u and str(u.get('rol') or 'owner').lower() == 'tecnico' and u.get('parent_owner'):
                session['usuario_real'] = u['username']
                session['rol'] = 'tecnico'
                session['usuario'] = u['parent_owner']
            else:
                session['rol'] = 'owner'
        except Exception:
            session['rol'] = 'owner'

    if str(session.get('rol') or 'owner').lower() != 'tecnico':
        return None

    path = request.path or '/'
    if any(path.startswith(r) for r in RUTAS_BLOQUEADAS_TECNICO):
        if request.is_json or path.startswith('/api/'):
            return jsonify({"status": "error", "msg": "Permiso denegado para el rol Técnico."}), 403
        return redirect('/resumen')

    # Bloquear cambio de pools, factory reset o cambio de IP al técnico
    if path.startswith('/cmd/') and any(x in path for x in ('/guardar_pools', '/reset', '/set_network')):
        return "Permiso denegado para el rol Técnico", 403

    return None


# ==================== 1. MAPA VISUAL DE RACKS ====================
@pro_bp.route('/racks')
@login_required
def vista_racks():
    if not es_super_admin():
        return redirect('/resumen')
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM racks_sedes WHERE owner=%s ORDER BY id ASC", (usr_actual,))
    racks_db = cur.fetchall()

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas_m = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    mineros_info = []
    mapa_celdas = {}  # (rack_nombre, fila, col) -> minero_dict

    for m in filas_m:
        ip = m['ip']
        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_on = segs < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_on else 0.0
        tm = a_int_seguro(m.get('temp_max'), 0) if es_on else 0
        rack_n = str(m.get('rack_nombre') or '').strip()
        rack_f = a_int_seguro(m.get('rack_fila'), 0)
        rack_c = a_int_seguro(m.get('rack_col'), 0)

        if not es_on:
            color_cls = 'slot-off'
        elif tm >= 85 or hr <= 0.01:
            color_cls = 'slot-crit'
        elif tm >= 75:
            color_cls = 'slot-warn'
        else:
            color_cls = 'slot-ok'

        mod_raw = str(m.get('modelo') or 'Antminer').strip()
        # Generar modelo corto legible para la chapa frontal del ASIC
        mod_corto = mod_raw
        for pref in ('Antminer ', 'WhatsMiner ', 'Innosilicon ', 'Avalon '):
            if mod_corto.lower().startswith(pref.lower()):
                mod_corto = mod_corto[len(pref):]
                break
        mod_corto = mod_corto[:10].strip() or 'ASIC'

        # Determinar familia del ASIC para ilustración SVG precisa
        mod_l = mod_raw.lower()
        if 's21' in mod_l or 't21' in mod_l:
            tipo_familia = 's21'
        elif 'whatsminer' in mod_l or 'm30' in mod_l or 'm50' in mod_l or 'm20' in mod_l or 'm31' in mod_l or 'm60' in mod_l:
            tipo_familia = 'whatsminer'
        elif 'l7' in mod_l or 'd7' in mod_l or 'ka3' in mod_l or 'k7' in mod_l or 'e9' in mod_l:
            tipo_familia = 'l7'
        elif 'avalon' in mod_l or 'a12' in mod_l or 'a13' in mod_l or 'a11' in mod_l or 'a14' in mod_l:
            tipo_familia = 'avalon'
        elif 's19' in mod_l or 't19' in mod_l or 'antminer' in mod_l or 's17' in mod_l or 't17' in mod_l:
            tipo_familia = 's19'
        else:
            tipo_familia = 'generic'

        item = {
            'ip': ip,
            'nombre': nombre_inteligente_minero(ip, nombres_dict, m.get('worker1')),
            'modelo': mod_raw,
            'modelo_corto': mod_corto,
            'tipo_familia': tipo_familia,
            'online': es_on,
            'hashrate': hr,
            'unidad': m.get('unidad') or 'TH/s',
            'temp': tm,
            'consumo_w': a_float_seguro(m.get('consumo_w'), 0.0),
            'color_cls': color_cls,
            'rack_nombre': rack_n,
            'rack_fila': rack_f,
            'rack_col': rack_c
        }
        mineros_info.append(item)
        if rack_n and rack_f > 0 and rack_c > 0:
            mapa_celdas[(rack_n, rack_f, rack_c)] = item

    racks_render = []
    total_hr_global_racks = 0.0
    for rk in racks_db:
        r_nom = rk['nombre']
        n_filas = max(1, min(12, a_int_seguro(rk.get('filas'), 4)))
        n_cols = max(1, min(16, a_int_seguro(rk.get('columnas'), 6)))
        matriz = []
        ocupados = 0
        online_cnt = 0
        offline_cnt = 0
        hr_rack = 0.0
        temps_rack = []
        consumo_rack = 0.0

        for f in range(1, n_filas + 1):
            fila_celdas = []
            for c in range(1, n_cols + 1):
                m_cell = mapa_celdas.get((r_nom, f, c))
                if m_cell:
                    ocupados += 1
                    if m_cell['online']:
                        online_cnt += 1
                        hr_rack += m_cell['hashrate']
                        consumo_rack += m_cell.get('consumo_w', 0.0)
                        if m_cell['temp'] > 0:
                            temps_rack.append(m_cell['temp'])
                    else:
                        offline_cnt += 1
                fila_celdas.append({'fila': f, 'col': c, 'minero': m_cell})
            matriz.append(fila_celdas)

        temp_prom = round(sum(temps_rack) / len(temps_rack), 1) if temps_rack else 0
        total_hr_global_racks += hr_rack
        capacidad = n_filas * n_cols
        pct_ocupado = round((ocupados / capacidad) * 100, 1) if capacidad > 0 else 0

        racks_render.append({
            'id': rk['id'],
            'nombre': r_nom,
            'filas': n_filas,
            'columnas': n_cols,
            'ocupados': ocupados,
            'capacidad': capacidad,
            'pct_ocupado': pct_ocupado,
            'online_cnt': online_cnt,
            'offline_cnt': offline_cnt,
            'hr_rack': round(hr_rack, 1),
            'temp_prom': temp_prom,
            'consumo_kw': round(consumo_rack / 1000.0, 2),
            'matriz': matriz
        })

    sin_asignar = [m for m in mineros_info if not m['rack_nombre']]
    return render_seguro(
        'racks.html', active_page='racks',
        racks=racks_render, mineros=mineros_info, sin_asignar=sin_asignar,
        total_hr_global_racks=round(total_hr_global_racks, 1)
    )

@pro_bp.route('/api/racks/crear', methods=['POST'])
@login_required
def api_racks_crear():
    if not es_super_admin():
        return jsonify({"status": "error", "msg": "Acceso restringido a administradores"}), 403
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    nombre = str(data.get('nombre') or '').strip()[:40]
    filas = max(1, min(12, a_int_seguro(data.get('filas'), 4)))
    columnas = max(1, min(16, a_int_seguro(data.get('columnas'), 6)))
    if not nombre:
        return jsonify({"status": "error", "msg": "Ingresa un nombre para el estante"}), 400

    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("""
        INSERT INTO racks_sedes (owner, nombre, filas, columnas)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (owner, nombre) DO UPDATE SET filas=EXCLUDED.filas, columnas=EXCLUDED.columnas
    """, (usr_actual, nombre, filas, columnas))
    return jsonify({"status": "ok"})

@pro_bp.route('/api/racks/editar', methods=['POST'])
@login_required
def api_racks_editar():
    if not es_super_admin():
        return jsonify({"status": "error", "msg": "Acceso restringido a administradores"}), 403
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    rack_id = a_int_seguro(data.get('id'), 0)
    nuevo_nombre = str(data.get('nombre') or '').strip()[:40]
    filas = max(1, min(12, a_int_seguro(data.get('filas'), 4)))
    columnas = max(1, min(16, a_int_seguro(data.get('columnas'), 6)))

    if not nuevo_nombre or rack_id <= 0:
        return jsonify({"status": "error", "msg": "Datos inválidos para editar estante"}), 400

    con = get_db(); con.autocommit = True; cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT nombre FROM racks_sedes WHERE owner=%s AND id=%s", (usr_actual, rack_id))
    row = cur.fetchone()
    if not row:
        return jsonify({"status": "error", "msg": "Estante no encontrado"}), 404

    nombre_ant = row['nombre']

    # Actualizar estante
    cur.execute("""
        UPDATE racks_sedes
        SET nombre=%s, filas=%s, columnas=%s
        WHERE owner=%s AND id=%s
    """, (nuevo_nombre, filas, columnas, usr_actual, rack_id))

    # Si cambió de nombre, sincronizar los mineros ya asignados
    if nuevo_nombre != nombre_ant:
        cur.execute("""
            UPDATE mineros_activos
            SET rack_nombre=%s
            WHERE owner=%s AND rack_nombre=%s
        """, (nuevo_nombre, usr_actual, nombre_ant))

    # Si se redujeron filas o columnas, liberar celdas que queden por fuera
    cur.execute("""
        UPDATE mineros_activos
        SET rack_nombre='', rack_fila=0, rack_col=0
        WHERE owner=%s AND rack_nombre=%s AND (rack_fila > %s OR rack_col > %s)
    """, (usr_actual, nuevo_nombre, filas, columnas))

    return jsonify({"status": "ok"})

@pro_bp.route('/api/racks/eliminar', methods=['POST'])
@login_required
def api_racks_eliminar():
    if not es_super_admin():
        return jsonify({"status": "error", "msg": "Acceso restringido a administradores"}), 403
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    nombre = str(data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({"status": "error"}), 400
    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("UPDATE mineros_activos SET rack_nombre='', rack_fila=0, rack_col=0 WHERE owner=%s AND rack_nombre=%s", (usr_actual, nombre))
    cur.execute("DELETE FROM racks_sedes WHERE owner=%s AND nombre=%s", (usr_actual, nombre))
    return jsonify({"status": "ok"})

@pro_bp.route('/api/racks/asignar', methods=['POST'])
@login_required
def api_racks_asignar():
    if not es_super_admin():
        return jsonify({"status": "error", "msg": "Acceso restringido a administradores"}), 403
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    ip = str(data.get('ip') or '').strip()
    rack_nombre = str(data.get('rack_nombre') or '').strip()
    fila = a_int_seguro(data.get('fila'), 0)
    col = a_int_seguro(data.get('col'), 0)

    con = get_db(); con.autocommit = True; cur = con.cursor()
    # Liberar la celda destino si había otro equipo ahí
    if rack_nombre and fila > 0 and col > 0:
        cur.execute(
            "UPDATE mineros_activos SET rack_nombre='', rack_fila=0, rack_col=0 WHERE owner=%s AND rack_nombre=%s AND rack_fila=%s AND rack_col=%s",
            (usr_actual, rack_nombre, fila, col)
        )
    if ip:
        cur.execute(
            "UPDATE mineros_activos SET rack_nombre=%s, rack_fila=%s, rack_col=%s WHERE owner=%s AND ip=%s",
            (rack_nombre, fila, col, usr_actual, ip)
        )
    return jsonify({"status": "ok"})


# ==================== 2. ENLACES DE SOLO LECTURA (MODO HOSTING) ====================
@pro_bp.route('/hosting', methods=['GET', 'POST'])
@login_required
def gestion_hosting():
    usr_actual = session.get('usuario', 'Admin')
    msg = None
    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'crear_enlace':
            titulo = (request.form.get('titulo') or 'Cliente Hosting').strip()[:60]
            ips_sel = request.form.getlist('ips')
            if not ips_sel:
                msg = "Selecciona al menos un minero para crear el enlace."
            else:
                token = secrets.token_urlsafe(12)
                ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    "INSERT INTO enlaces_hosting (owner, token, titulo, ips_json, activo, fecha_creacion) VALUES (%s, %s, %s, %s, 1, %s)",
                    (usr_actual, token, titulo, json.dumps(ips_sel), ahora)
                )
                msg = f"Enlace de solo lectura '{titulo}' creado correctamente."
        elif accion == 'eliminar_enlace':
            eid = a_int_seguro(request.form.get('enlace_id'), 0)
            cur.execute("DELETE FROM enlaces_hosting WHERE id=%s AND owner=%s", (eid, usr_actual))
            msg = "Enlace revocado y eliminado."

    cur.execute("SELECT * FROM enlaces_hosting WHERE owner=%s ORDER BY id DESC", (usr_actual,))
    enlaces = cur.fetchall()
    for e in enlaces:
        try:
            e['ips_list'] = json.loads(e.get('ips_json') or '[]')
        except Exception:
            e['ips_list'] = []

    cur.execute("SELECT ip, modelo, worker1 FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas_m = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    mineros_disp = [
        {'ip': m['ip'], 'nombre': nombre_inteligente_minero(m['ip'], nombres_dict, m.get('worker1')), 'modelo': m.get('modelo') or 'Antminer'}
        for m in filas_m
    ]

    return render_seguro('hosting.html', active_page='hosting', enlaces=enlaces, mineros_disp=mineros_disp, msg=msg)

@pro_bp.route('/h/<token>')
def vista_publica_hosting(token):
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM enlaces_hosting WHERE token=%s AND activo=1 LIMIT 1", (token,))
    enlace = cur.fetchone()
    if not enlace:
        return "Enlace de monitoreo no válido o revocado.", 404

    owner = enlace['owner']
    try:
        ips_permitidas = json.loads(enlace.get('ips_json') or '[]')
    except Exception:
        ips_permitidas = []

    if not ips_permitidas:
        return render_seguro('hosting_publico.html', titulo=enlace['titulo'], mineros=[], totales=0, online=0, hashrate_total=0.0, consumo_kw=0.0)

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

        mineros_pub.append({
            'nombre': nombre_inteligente_minero(ip, nombres_dict, m.get('worker1')),
            'modelo': m.get('modelo') or 'Antminer',
            'online': es_on,
            'hashrate': hr,
            'unidad': m.get('unidad') or 'TH/s',
            'temp': tm,
            'consumo_kw': round(cw / 1000.0, 2),
            'uptime': (m.get('uptime') or 'En línea') if es_on else 'Sin conexión'
        })

    return render_seguro(
        'hosting_publico.html',
        titulo=enlace['titulo'],
        mineros=mineros_pub,
        totales=len(mineros_pub),
        online=online_cnt,
        hashrate_total=round(hr_total, 2),
        consumo_kw=round(cw_total / 1000.0, 2)
    )


# ==================== 3. SUB-CUENTAS CON ROL TÉCNICO ====================
@pro_bp.route('/tecnicos', methods=['GET', 'POST'])
@login_required
def gestion_tecnicos():
    usr_actual = session.get('usuario', 'Admin')
    msg = None
    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'crear_tecnico':
            u_tec = (request.form.get('username') or '').strip()
            p_tec = request.form.get('password') or ''
            if len(u_tec) < 3 or len(u_tec) > 30 or ' ' in u_tec:
                msg = "El usuario del técnico debe tener entre 3 y 30 caracteres sin espacios."
            elif len(p_tec) < 6:
                msg = "La contraseña debe tener al menos 6 caracteres."
            else:
                cur.execute("SELECT id FROM usuarios WHERE LOWER(username)=LOWER(%s)", (u_tec,))
                if cur.fetchone():
                    msg = "Ese nombre de usuario ya está en uso."
                else:
                    hoy = datetime.utcnow().strftime("%Y-%m-%d")
                    cur.execute(
                        "INSERT INTO usuarios (username, password, fecha_registro, plan, api_key, parent_owner, rol) VALUES (%s, %s, %s, 'Pro', %s, %s, 'tecnico')",
                        (u_tec, generate_password_hash(p_tec), hoy, generar_api_key(), usr_actual)
                    )
                    msg = f"Sub-cuenta Técnico '{u_tec}' creada correctamente."
        elif accion == 'eliminar_tecnico':
            tid = a_int_seguro(request.form.get('tec_id'), 0)
            cur.execute("DELETE FROM usuarios WHERE id=%s AND parent_owner=%s AND rol='tecnico'", (tid, usr_actual))
            msg = "Cuenta de técnico eliminada."

    cur.execute(
        "SELECT id, username, fecha_registro, ultima_conexion FROM usuarios WHERE parent_owner=%s AND rol='tecnico' ORDER BY id DESC",
        (usr_actual,)
    )
    tecnicos = cur.fetchall()
    return render_seguro('tecnicos.html', active_page='tecnicos', tecnicos=tecnicos, msg=msg)
