"""
==============================================================================
 EL MINERO GRIS — Actualización Masiva de Firmware (OTA)
==============================================================================
Permite subir archivos de firmware y enviarlos a múltiples ASICs simultáneamente.
El agente recibe la URL de descarga + las IPs destino y ejecuta el flasheo
vía SSH/HTTP según el tipo de ASIC (Antminer/WhatsMiner).
"""

from flask import Blueprint, redirect, request, session, jsonify, Response
from werkzeug.utils import secure_filename
import psycopg2.extras, json, hashlib, os, io
from datetime import datetime

from db import get_db
from utils import (
    login_required, a_int_seguro, a_float_seguro,
    nombre_inteligente_minero, calcular_segundos_desconectado,
    ordenar_ip_key, render_seguro
)

firmware_bp = Blueprint('firmware_bp', __name__)

# Directorio para almacenar firmwares subidos
FIRMWARE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'firmware_uploads')
os.makedirs(FIRMWARE_DIR, exist_ok=True)

EXTENSIONES_PERMITIDAS = {'.tar.gz', '.img', '.bin', '.swu', '.zip', '.tar'}
MAX_FIRMWARE_SIZE = 300 * 1024 * 1024  # 300 MB


def extension_valida(nombre_archivo):
    n = nombre_archivo.lower()
    for ext in EXTENSIONES_PERMITIDAS:
        if n.endswith(ext):
            return True
    return False


def calcular_sha256(data_bytes):
    return hashlib.sha256(data_bytes).hexdigest()


# ==================== VISTA PRINCIPAL: GESTIÓN DE FIRMWARES ====================

@firmware_bp.route('/firmware', methods=['GET'])
@login_required
def vista_firmware():
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return redirect('/resumen')

    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Firmwares subidos
    cur.execute("SELECT * FROM firmware_ota WHERE owner=%s ORDER BY id DESC", (usr_actual,))
    firmwares = cur.fetchall()

    # Jobs recientes
    cur.execute("""
        SELECT j.*, f.nombre AS fw_nombre, f.version AS fw_version
        FROM firmware_ota_jobs j
        LEFT JOIN firmware_ota f ON f.id = j.firmware_id AND f.owner = j.owner
        WHERE j.owner=%s
        ORDER BY j.id DESC LIMIT 100
    """, (usr_actual,))
    jobs = cur.fetchall()

    # Mineros disponibles para flashear
    cur.execute("SELECT ip, modelo, firmware, fw_tipo, agent_id FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas_m = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    mineros_disp = [{
        'ip': m['ip'],
        'nombre': nombre_inteligente_minero(m['ip'], nombres_dict, ''),
        'modelo': m.get('modelo') or 'Antminer',
        'firmware': m.get('firmware') or 'Desconocido',
        'fw_tipo': m.get('fw_tipo') or 'Stock',
        'agent_id': m.get('agent_id') or 'default'
    } for m in filas_m]

    # Estadísticas de jobs
    stats = {'pendiente': 0, 'en_progreso': 0, 'completado': 0, 'error': 0}
    for j in jobs:
        est = j.get('estado', 'pendiente')
        if est in stats:
            stats[est] += 1

    return render_seguro('firmware.html', active_page='firmware',
                         firmwares=firmwares, jobs=jobs, mineros_disp=mineros_disp, stats=stats)


# ==================== SUBIR FIRMWARE ====================

@firmware_bp.route('/api/firmware/subir', methods=['POST'])
@login_required
def api_firmware_subir():
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return jsonify({"status": "error", "msg": "Permiso denegado"}), 403

    usr_actual = session.get('usuario', 'Admin')

    if 'archivo' not in request.files:
        return jsonify({"status": "error", "msg": "No se seleccionó ningún archivo"}), 400

    archivo = request.files['archivo']
    if not archivo.filename:
        return jsonify({"status": "error", "msg": "Nombre de archivo vacío"}), 400

    if not extension_valida(archivo.filename):
        return jsonify({"status": "error", "msg": f"Tipo no permitido. Usa: {', '.join(EXTENSIONES_PERMITIDAS)}"}), 400

    nombre_fw = (request.form.get('nombre') or '').strip()[:80]
    modelo = (request.form.get('modelo_compatible') or '').strip()[:60]
    version = (request.form.get('version') or '').strip()[:30]
    notas = (request.form.get('notas') or '').strip()[:300]

    if not nombre_fw:
        nombre_fw = archivo.filename

    # Leer contenido
    data = archivo.read()
    if len(data) > MAX_FIRMWARE_SIZE:
        return jsonify({"status": "error", "msg": f"Archivo demasiado grande. Máximo: {MAX_FIRMWARE_SIZE // (1024*1024)} MB"}), 400

    tamano_mb = round(len(data) / (1024 * 1024), 2)
    checksum = calcular_sha256(data)
    nombre_seguro = secure_filename(archivo.filename)
    nombre_almacen = f"{usr_actual}_{checksum[:12]}_{nombre_seguro}"

    # Guardar archivo en disco
    ruta_archivo = os.path.join(FIRMWARE_DIR, nombre_almacen)
    with open(ruta_archivo, 'wb') as f:
        f.write(data)

    # Guardar metadata en BD
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        INSERT INTO firmware_ota (owner, nombre, nombre_archivo, modelo_compatible, version, tamano_mb, checksum_sha256, notas, fecha_subida)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (usr_actual, nombre_fw, nombre_almacen, modelo, version, tamano_mb, checksum, notas, ahora))
    nuevo_id = cur.fetchone()['id']

    return jsonify({"status": "ok", "id": nuevo_id, "nombre": nombre_fw, "checksum": checksum, "tamano_mb": tamano_mb})


# ==================== ELIMINAR FIRMWARE ====================

@firmware_bp.route('/api/firmware/eliminar', methods=['POST'])
@login_required
def api_firmware_eliminar():
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return jsonify({"status": "error", "msg": "Permiso denegado"}), 403

    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    fw_id = a_int_seguro(data.get('firmware_id'), 0)
    if fw_id <= 0:
        return jsonify({"status": "error", "msg": "ID inválido"}), 400

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT nombre_archivo FROM firmware_ota WHERE id=%s AND owner=%s", (fw_id, usr_actual))
    row = cur.fetchone()
    if not row:
        return jsonify({"status": "error", "msg": "Firmware no encontrado"}), 404

    # Eliminar archivo del disco
    ruta = os.path.join(FIRMWARE_DIR, row['nombre_archivo'])
    if os.path.exists(ruta):
        try:
            os.remove(ruta)
        except:
            pass

    # Eliminar de BD + jobs relacionados
    cur.execute("DELETE FROM firmware_ota_jobs WHERE firmware_id=%s AND owner=%s", (fw_id, usr_actual))
    cur.execute("DELETE FROM firmware_ota WHERE id=%s AND owner=%s", (fw_id, usr_actual))

    return jsonify({"status": "ok"})


# ==================== DESCARGAR FIRMWARE (PARA EL AGENTE) ====================

@firmware_bp.route('/api/firmware/descargar/<int:fw_id>')
def api_firmware_descargar(fw_id):
    api_key = (request.args.get('api_key') or '').strip()
    if not api_key:
        return "API Key requerida", 401

    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username FROM usuarios WHERE api_key=%s", (api_key,))
    u = cur.fetchone()
    if not u:
        return "API Key inválida", 401

    cur.execute("SELECT nombre_archivo, checksum_sha256 FROM firmware_ota WHERE id=%s AND owner=%s", (fw_id, u['username']))
    fw = cur.fetchone()
    if not fw:
        return "Firmware no encontrado", 404

    ruta = os.path.join(FIRMWARE_DIR, fw['nombre_archivo'])
    if not os.path.exists(ruta):
        return "Archivo no disponible en el servidor", 404

    with open(ruta, 'rb') as f:
        data = f.read()

    return Response(data, mimetype='application/octet-stream',
                    headers={
                        'Content-Disposition': f'attachment; filename="{fw["nombre_archivo"]}"',
                        'X-Checksum-SHA256': fw['checksum_sha256']
                    })


# ==================== INICIAR FLASHEO MASIVO (OTA) ====================

@firmware_bp.route('/api/firmware/flashear', methods=['POST'])
@login_required
def api_firmware_flashear():
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return jsonify({"status": "error", "msg": "Permiso denegado"}), 403

    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    fw_id = a_int_seguro(data.get('firmware_id'), 0)
    ips = data.get('ips', [])

    if fw_id <= 0:
        return jsonify({"status": "error", "msg": "Selecciona un firmware"}), 400
    if not ips or not isinstance(ips, list):
        return jsonify({"status": "error", "msg": "Selecciona al menos un equipo"}), 400

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Verificar que el firmware existe
    cur.execute("SELECT id, nombre, nombre_archivo, checksum_sha256 FROM firmware_ota WHERE id=%s AND owner=%s", (fw_id, usr_actual))
    fw = cur.fetchone()
    if not fw:
        return jsonify({"status": "error", "msg": "Firmware no encontrado"}), 404

    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    # Obtener API key del usuario para la URL de descarga
    cur.execute("SELECT api_key FROM usuarios WHERE username=%s", (usr_actual,))
    u_row = cur.fetchone()
    api_key = (u_row or {}).get('api_key') or ''

    jobs_creados = 0
    from tunel import enviar_orden_tunel

    for ip in ips:
        ip_c = str(ip or '').strip()
        if not ip_c:
            continue

        cur.execute("SELECT agent_id FROM mineros_activos WHERE owner=%s AND ip=%s LIMIT 1", (usr_actual, ip_c))
        row_ag = cur.fetchone()
        ag_id = str((row_ag or {}).get('agent_id') or 'default').strip()

        # Crear job en BD
        cur.execute("""
            INSERT INTO firmware_ota_jobs (owner, firmware_id, agent_id, ip, estado, progreso, mensaje, fecha_creacion)
            VALUES (%s, %s, %s, %s, 'pendiente', 0, 'En cola de actualización', %s)
            RETURNING id
        """, (usr_actual, fw_id, ag_id, ip_c, ahora))
        job_id = cur.fetchone()['id']

        # Enviar comando de flash al agente via WebSocket o cola
        cmd_str = f"flash_firmware|{fw_id}|{fw['checksum_sha256']}|{api_key}"
        ok_ws = enviar_orden_tunel(usr_actual, ip_c, cmd_str, agent_id=ag_id)
        estado = 'enviado' if ok_ws else 'pendiente'
        cur.execute("INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, %s, %s, %s, %s)",
                    (usr_actual, ag_id, ip_c, cmd_str, estado, ahora))
        jobs_creados += 1

    return jsonify({"status": "ok", "jobs_creados": jobs_creados, "firmware": fw['nombre']})


# ==================== REPORTAR PROGRESO DE FLASHEO (DESDE EL AGENTE) ====================

@firmware_bp.route('/api/firmware/progreso', methods=['POST'])
def api_firmware_progreso():
    data = request.get_json(silent=True) or {}
    api_key = str(data.get('api_key') or '').strip()
    if not api_key:
        return jsonify({"status": "error"}), 401

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username FROM usuarios WHERE api_key=%s", (api_key,))
    u = cur.fetchone()
    if not u:
        return jsonify({"status": "error"}), 401

    ip = str(data.get('ip') or '').strip()
    fw_id = a_int_seguro(data.get('firmware_id'), 0)
    estado = str(data.get('estado') or 'en_progreso').strip()
    progreso = min(100, max(0, a_int_seguro(data.get('progreso'), 0)))
    mensaje = str(data.get('mensaje') or '').strip()[:200]

    if estado in ('completado', 'error'):
        ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            UPDATE firmware_ota_jobs SET estado=%s, progreso=%s, mensaje=%s, fecha_fin=%s
            WHERE owner=%s AND firmware_id=%s AND ip=%s AND estado != 'completado'
        """, (estado, progreso, mensaje, ahora, u['username'], fw_id, ip))
    else:
        cur.execute("""
            UPDATE firmware_ota_jobs SET estado=%s, progreso=%s, mensaje=%s
            WHERE owner=%s AND firmware_id=%s AND ip=%s AND estado NOT IN ('completado', 'error')
        """, (estado, progreso, mensaje, u['username'], fw_id, ip))

    return jsonify({"status": "ok"})


# ==================== ESTADO DE JOBS (PARA POLLING FRONTEND) ====================

@firmware_bp.route('/api/firmware/jobs')
@login_required
def api_firmware_jobs():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT j.id, j.ip, j.estado, j.progreso, j.mensaje, j.fecha_creacion, j.fecha_fin,
               f.nombre AS fw_nombre, f.version AS fw_version
        FROM firmware_ota_jobs j
        LEFT JOIN firmware_ota f ON f.id = j.firmware_id AND f.owner = j.owner
        WHERE j.owner=%s
        ORDER BY j.id DESC LIMIT 50
    """, (usr_actual,))
    jobs = cur.fetchall()
    return jsonify({"ok": True, "jobs": jobs})
