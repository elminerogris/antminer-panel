from flask import Blueprint, redirect, request, session, jsonify, Response
from werkzeug.security import generate_password_hash
import psycopg2.extras, json, time, calendar, csv, io, uuid, secrets, threading, hashlib, re
from datetime import datetime, timedelta

from db import get_db
from tunel import enviar_orden_tunel, tunel_esta_activo
from resolver_modelos import resolver_nombre_dispositivo
from utils import (
    login_required, es_super_admin, es_cuenta_pro, generar_api_key,
    es_hash_seguro, verificar_password, a_int_seguro, a_float_seguro,
    clave_granja_minero, nombre_inteligente_minero, calcular_segundos_desconectado,
    ordenar_ip_key, obtener_datos_red, enviar_telegram, render_seguro,
    generar_totp_secret, verificar_totp, notificar_alerta_seguridad,
    parsear_dispositivo, fijar_cookie_dispositivo, determinar_credenciales_defecto,
    sanitizar_nombre_minero, sanitizar_nombre_granja, sanitizar_texto_seguro,
    enmascarar_email, enviar_email_codigo
)

panel_bp = Blueprint('panel_bp', __name__)

_rate_lock = threading.Lock()
_rate_buckets = {}
_reset_codes = {}

def _ip_cliente():
    cf = request.headers.get('CF-Connecting-IP', '')
    if cf:
        return cf.strip()
    xff = request.headers.get('X-Forwarded-For', '')
    return xff.split(',')[0].strip() if xff else (request.remote_addr or 'unknown')

def verificar_rate_limit(clave, max_intentos=8, ventana_seg=300):
    ahora = time.time()
    with _rate_lock:
        hist = [ts for ts in _rate_buckets.get(clave, []) if ahora - ts < ventana_seg]
        if len(hist) >= max_intentos:
            _rate_buckets[clave] = hist
            return True
        hist.append(ahora)
        _rate_buckets[clave] = hist
        return False

def _validar_origen_csrf():
    """Valida que peticiones críticas de control provengan del mismo host (Anti-CSRF)."""
    host = request.host
    ref = request.headers.get('Referer', '')
    orig = request.headers.get('Origin', '')
    if orig:
        clean_orig = orig.split('://')[-1].split('/')[0]
        if clean_orig != host:
            notificar_alerta_seguridad("Violación Anti-CSRF", f"Origin '{orig}' no coincide con host '{host}' en ruta {request.path}", _ip_cliente())
            return False
    elif ref:
        clean_ref = ref.split('://')[-1].split('/')[0]
        if clean_ref != host:
            notificar_alerta_seguridad("Violación Anti-CSRF", f"Referer '{ref}' no coincide con host '{host}' en ruta {request.path}", _ip_cliente())
            return False
    return True

def despachar_comando(cur, owner, ip, comando, fecha=None):
    ahora = fecha or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    agent_id = 'default'
    try:
        cur.execute("SELECT agent_id FROM mineros_activos WHERE owner=%s AND ip=%s LIMIT 1", (owner, ip))
        row_ag = cur.fetchone()
        if row_ag:
            ag_val = row_ag['agent_id'] if isinstance(row_ag, dict) else row_ag[0]
            if ag_val:
                agent_id = str(ag_val).strip()
    except Exception:
        pass

    enviado = enviar_orden_tunel(owner, ip, comando, agent_id=agent_id)
    estado = 'enviado' if enviado else 'pendiente'
    cur.execute(
        "INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, %s, %s, %s, %s)",
        (owner, agent_id, ip, comando, estado, ahora)
    )
    return enviado

def _calcular_horas_rango(ini_str, fin_str):
    try:
        hi, mi = [int(x) for x in str(ini_str or '18:00').split(':')[:2]]
        hf, mf = [int(x) for x in str(fin_str or '22:00').split(':')[:2]]
        m1 = hi * 60 + mi
        m2 = hf * 60 + mf
        diff = (m2 - m1) if m2 > m1 else ((1440 - m1) + m2)
        return max(0.0, round(diff / 60.0, 2))
    except Exception:
        return 4.0

# --- Portada Pública, Legales y SEO ---
@panel_bp.route('/')
def landing_o_resumen():
    if 'usuario' in session:
        return redirect('/resumen')
    return render_seguro('landing.html')

@panel_bp.route('/compatibilidad')
def pagina_compatibilidad():
    return render_seguro('compatibilidad.html')

@panel_bp.route('/guia')
def pagina_guia():
    return render_seguro('guia.html')

@panel_bp.route('/privacidad')
def pagina_privacidad():
    return render_seguro('privacidad.html')

@panel_bp.route('/terminos')
def pagina_terminos():
    return render_seguro('terminos.html')

@panel_bp.route('/robots.txt')
def robots_txt():
    contenido = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /proxy/\n"
        "Disallow: /cmd/\n"
        "Sitemap: https://elminerogris.com/sitemap.xml\n"
    )
    return Response(contenido, mimetype="text/plain")

@panel_bp.route('/sitemap.xml')
def sitemap_xml():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://elminerogris.com/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://elminerogris.com/compatibilidad</loc>
    <changefreq>monthly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://elminerogris.com/guia</loc>
    <changefreq>monthly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://elminerogris.com/register</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://elminerogris.com/login</loc>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>
  <url>
    <loc>https://elminerogris.com/privacidad</loc>
    <changefreq>yearly</changefreq>
    <priority>0.4</priority>
  </url>
  <url>
    <loc>https://elminerogris.com/terminos</loc>
    <changefreq>yearly</changefreq>
    <priority>0.4</priority>
  </url>
</urlset>"""
    return Response(xml, mimetype="application/xml")

# --- APIs de Backup y Restauración (JSON) ---
@panel_bp.route('/api/ajustes/exportar_backup')
@login_required
def api_exportar_backup():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT subnet, tarifa_kwh, comision_pool, umbral_temp, umbral_hr, alertas_activas,
               wd_activo, wd_alcance, wd_granjas, wd_mineros, wd_zero_hr, wd_cold_on,
               wd_cold_temp, wd_amb_cold_on, wd_amb_cold_temp, wd_dead_board,
               wd_gracia_min, wd_cooldown_min, wd_cuarentena_on, wd_max_reinicios,
               prot_activo, prot_temp_on, prot_temp_val, prot_temp_accion,
               prot_clima_on, prot_clima_val, prot_clima_accion,
               prot_net_on, prot_net_rej_pct, prot_net_accion,
               prot_fan_on, prot_fan_accion, prot_wakeup_on, prot_wakeup_min,
               curt_activo, curt_inicio, curt_fin, curt_accion
        FROM usuarios WHERE username=%s
    """, (usr_actual,))
    prefs = dict(cur.fetchone() or {})

    cur.execute("""
        SELECT farm_id, nombre, ciudad, gateway, cap_kw, tarifa_kwh, notas
        FROM granjas_sedes WHERE owner=%s ORDER BY id ASC
    """, (usr_actual,))
    granjas = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s ORDER BY ip ASC", (usr_actual,))
    nombres = [dict(r) for r in cur.fetchall()]

    backup = {
        "app": "El Minero Gris",
        "version": 1,
        "fecha_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "owner": usr_actual,
        "preferencias": prefs,
        "granjas": granjas,
        "nombres_mineros": nombres
    }
    fecha_archivo = datetime.utcnow().strftime("%Y%m%d")
    contenido = json.dumps(backup, indent=2, ensure_ascii=False)
    return Response(
        contenido,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment;filename=Backup_ElMineroGris_{usr_actual}_{fecha_archivo}.json"}
    )

@panel_bp.route('/api/ajustes/importar_backup', methods=['POST'])
@login_required
def api_importar_backup():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("app") != "El Minero Gris":
        return jsonify({"status": "error", "msg": "El archivo no es un respaldo válido de El Minero Gris."}), 400

    con = get_db(); con.autocommit = True; cur = con.cursor()
    restaurados_nombres = 0
    restauradas_granjas = 0
    restauradas_prefs = False

    nombres = data.get("nombres_mineros")
    if isinstance(nombres, list):
        for item in nombres:
            if not isinstance(item, dict):
                continue
            ip = str(item.get("ip") or "").strip()
            nom = sanitizar_nombre_minero(item.get("nombre"), ip_defecto=ip)
            if ip and nom:
                cur.execute(
                    "INSERT INTO nombres_mineros (owner, ip, nombre) VALUES (%s, %s, %s) ON CONFLICT (owner, ip) DO UPDATE SET nombre=EXCLUDED.nombre",
                    (usr_actual, ip, nom)
                )
                restaurados_nombres += 1

    granjas = data.get("granjas")
    if isinstance(granjas, list):
        for g in granjas:
            if not isinstance(g, dict):
                continue
            fid = str(g.get("farm_id") or "").strip()
            nom = sanitizar_nombre_granja(g.get("nombre"), defecto="Granja 001")
            if not fid or not nom:
                continue
            ciudad = sanitizar_texto_seguro(g.get("ciudad"), max_len=60, defecto="Santa Cruz de la Sierra")
            gw = str(g.get("gateway") or "").strip()
            cap_kw = max(1.0, a_float_seguro(g.get("cap_kw"), 50.0))
            tarifa_kwh = max(0.0, a_float_seguro(g.get("tarifa_kwh"), 0.06))
            notas = sanitizar_texto_seguro(g.get("notas"), max_len=200)
            cur.execute("""
                INSERT INTO granjas_sedes (owner, farm_id, nombre, ciudad, gateway, cap_kw, tarifa_kwh, notas)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner, farm_id) DO UPDATE SET
                    nombre=EXCLUDED.nombre,
                    ciudad=EXCLUDED.ciudad,
                    gateway=CASE WHEN EXCLUDED.gateway != '' THEN EXCLUDED.gateway ELSE granjas_sedes.gateway END,
                    cap_kw=EXCLUDED.cap_kw,
                    tarifa_kwh=EXCLUDED.tarifa_kwh,
                    notas=EXCLUDED.notas
            """, (usr_actual, fid, nom, ciudad, gw, cap_kw, tarifa_kwh, notas))
            restauradas_granjas += 1

    prefs = data.get("preferencias")
    if isinstance(prefs, dict) and prefs:
        cur.execute("""
            UPDATE usuarios SET
                subnet=%s, tarifa_kwh=%s, comision_pool=%s, umbral_temp=%s, umbral_hr=%s, alertas_activas=%s,
                wd_activo=%s, wd_alcance=%s, wd_granjas=%s, wd_mineros=%s,
                wd_zero_hr=%s, wd_cold_on=%s, wd_cold_temp=%s,
                wd_amb_cold_on=%s, wd_amb_cold_temp=%s, wd_dead_board=%s,
                wd_gracia_min=%s, wd_cooldown_min=%s, wd_cuarentena_on=%s, wd_max_reinicios=%s,
                prot_activo=%s, prot_temp_on=%s, prot_temp_val=%s, prot_temp_accion=%s,
                prot_clima_on=%s, prot_clima_val=%s, prot_clima_accion=%s,
                prot_net_on=%s, prot_net_rej_pct=%s, prot_net_accion=%s,
                prot_fan_on=%s, prot_fan_accion=%s, prot_wakeup_on=%s, prot_wakeup_min=%s,
                curt_activo=%s, curt_inicio=%s, curt_fin=%s, curt_accion=%s
            WHERE username=%s
        """, (
            str(prefs.get('subnet') or '192.168.0').strip(),
            max(0.0, a_float_seguro(prefs.get('tarifa_kwh'), 0.0)),
            min(100.0, max(0.0, a_float_seguro(prefs.get('comision_pool'), 0.0))),
            a_int_seguro(prefs.get('umbral_temp'), 80),
            a_float_seguro(prefs.get('umbral_hr'), 50.0),
            1 if a_int_seguro(prefs.get('alertas_activas'), 0) == 1 else 0,
            1 if a_int_seguro(prefs.get('wd_activo'), 0) == 1 else 0,
            str(prefs.get('wd_alcance') or 'all'),
            str(prefs.get('wd_granjas') or '[]'),
            str(prefs.get('wd_mineros') or '[]'),
            1 if a_int_seguro(prefs.get('wd_zero_hr'), 0) == 1 else 0,
            1 if a_int_seguro(prefs.get('wd_cold_on'), 0) == 1 else 0,
            a_int_seguro(prefs.get('wd_cold_temp'), 31),
            1 if a_int_seguro(prefs.get('wd_amb_cold_on'), 0) == 1 else 0,
            a_int_seguro(prefs.get('wd_amb_cold_temp'), 18),
            1 if a_int_seguro(prefs.get('wd_dead_board'), 0) == 1 else 0,
            a_int_seguro(prefs.get('wd_gracia_min'), 5),
            a_int_seguro(prefs.get('wd_cooldown_min'), 30),
            1 if a_int_seguro(prefs.get('wd_cuarentena_on'), 0) == 1 else 0,
            a_int_seguro(prefs.get('wd_max_reinicios'), 3),
            1 if a_int_seguro(prefs.get('prot_activo'), 0) == 1 else 0,
            1 if a_int_seguro(prefs.get('prot_temp_on'), 0) == 1 else 0,
            a_int_seguro(prefs.get('prot_temp_val'), 85),
            str(prefs.get('prot_temp_accion') or 'sleep'),
            1 if a_int_seguro(prefs.get('prot_clima_on'), 0) == 1 else 0,
            a_int_seguro(prefs.get('prot_clima_val'), 38),
            str(prefs.get('prot_clima_accion') or 'sleep'),
            1 if a_int_seguro(prefs.get('prot_net_on'), 0) == 1 else 0,
            a_float_seguro(prefs.get('prot_net_rej_pct'), 5.0),
            str(prefs.get('prot_net_accion') or 'sleep'),
            1 if a_int_seguro(prefs.get('prot_fan_on'), 0) == 1 else 0,
            str(prefs.get('prot_fan_accion') or 'sleep'),
            1 if a_int_seguro(prefs.get('prot_wakeup_on'), 0) == 1 else 0,
            a_int_seguro(prefs.get('prot_wakeup_min'), 30),
            1 if a_int_seguro(prefs.get('curt_activo'), 0) == 1 else 0,
            str(prefs.get('curt_inicio') or '18:00')[:5],
            str(prefs.get('curt_fin') or '22:00')[:5],
            str(prefs.get('curt_accion') or 'profile|low'),
            usr_actual
        ))
        restauradas_prefs = True

    return jsonify({
        "status": "ok",
        "nombres": restaurados_nombres,
        "granjas": restauradas_granjas,
        "prefs": restauradas_prefs
    })

# --- APIs de Granjas y Sedes ---
@panel_bp.route('/api/granjas/actualizar_agente_remoto', methods=['POST'])
@login_required
def api_actualizar_agente_remoto():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    agent_id = str(data.get('agent_id') or '').strip()
    if not agent_id:
        return jsonify({"status": "error", "msg": "agent_id requerido"}), 400

    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    con = get_db(); con.autocommit = True; cur = con.cursor()
    enviado = enviar_orden_tunel(usr_actual, "AGENTE-HOST", "update_apk", agent_id=agent_id)
    estado = 'enviado' if enviado else 'pendiente'
    cur.execute(
        "INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, %s, %s, %s, %s)",
        (usr_actual, agent_id, "AGENTE-HOST", "update_apk", estado, ahora)
    )
    return jsonify({"status": "ok", "enviado_tunel": enviado, "agent_id": agent_id})

@panel_bp.route('/api/granjas/guardar', methods=['POST'])
@login_required
def api_granjas_guardar():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    farm_id = str(data.get('farm_id') or '').strip()
    nombre = sanitizar_nombre_granja(data.get('nombre'), defecto="")
    ciudad = sanitizar_texto_seguro(data.get('ciudad'), max_len=60, defecto='Santa Cruz de la Sierra')
    cap_kw = max(1.0, a_float_seguro(data.get('cap_kw'), 50.0))
    tarifa_kwh = max(0.0, a_float_seguro(data.get('tarifa_kwh'), 0.06))
    gateway = str(data.get('gateway') or '').strip()[:60]
    notas = sanitizar_texto_seguro(data.get('notas'), max_len=200)

    if not nombre:
        return jsonify({"status": "error", "msg": "El nombre de la granja es obligatorio"}), 400
    if not farm_id:
        farm_id = "SEDE-" + uuid.uuid4().hex[:8].upper()

    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("""
        INSERT INTO granjas_sedes (owner, farm_id, nombre, ciudad, gateway, cap_kw, tarifa_kwh, notas)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (owner, farm_id) DO UPDATE SET
            nombre=EXCLUDED.nombre,
            ciudad=EXCLUDED.ciudad,
            gateway=CASE WHEN EXCLUDED.gateway != '' THEN EXCLUDED.gateway ELSE granjas_sedes.gateway END,
            cap_kw=EXCLUDED.cap_kw,
            tarifa_kwh=EXCLUDED.tarifa_kwh,
            notas=EXCLUDED.notas
    """, (usr_actual, farm_id, nombre, ciudad, gateway, cap_kw, tarifa_kwh, notas))
    return jsonify({"status": "ok", "farm_id": farm_id, "nombre": nombre})

def _borrar_granja_completa(cur, owner, farm_id, eliminar_mineros=True, mover_a_farm_id=""):
    """Elimina una granja/sede por completo (o traslada sus mineros) evitando registros huérfanos."""
    cur.execute(
        "SELECT agent_id FROM granjas_sedes WHERE owner=%s AND farm_id=%s LIMIT 1",
        (owner, farm_id)
    )
    row_sede = cur.fetchone()
    ag_ref = str((row_sede[0] if (row_sede and not isinstance(row_sede, dict)) else (row_sede or {}).get('agent_id')) or farm_id).strip() or farm_id

    cur.execute(
        "SELECT ip FROM mineros_activos WHERE owner=%s AND (farm_id=%s OR ((farm_id IS NULL OR farm_id='') AND agent_id=%s))",
        (owner, farm_id, ag_ref)
    )
    rows_ip = cur.fetchall() or []
    ips_afectadas = [str(r[0] if not isinstance(r, dict) else r['ip']).strip() for r in rows_ip]

    eliminados_m = 0
    movidos_m = 0
    if mover_a_farm_id and mover_a_farm_id != farm_id:
        cur.execute(
            "UPDATE mineros_activos SET farm_id=%s WHERE owner=%s AND (farm_id=%s OR ((farm_id IS NULL OR farm_id='') AND agent_id=%s))",
            (mover_a_farm_id, owner, farm_id, ag_ref)
        )
        movidos_m = cur.rowcount
        cur.execute("UPDATE racks_sedes SET farm_id=%s WHERE owner=%s AND farm_id=%s", (mover_a_farm_id, owner, farm_id))
        cur.execute("UPDATE agentes_conectados SET farm_id=%s WHERE owner=%s AND farm_id=%s", (mover_a_farm_id, owner, farm_id))
        cur.execute("UPDATE mineros_descubiertos SET farm_id=%s WHERE owner=%s AND farm_id=%s", (mover_a_farm_id, owner, farm_id))
    elif eliminar_mineros:
        cur.execute(
            "DELETE FROM mineros_activos WHERE owner=%s AND (farm_id=%s OR ((farm_id IS NULL OR farm_id='') AND agent_id=%s))",
            (owner, farm_id, ag_ref)
        )
        eliminados_m = cur.rowcount
        if ips_afectadas:
            cur.execute("SELECT ip FROM mineros_activos WHERE owner=%s", (owner,))
            ips_restantes = {str(r[0] if not isinstance(r, dict) else r['ip']).strip() for r in (cur.fetchall() or [])}
            for ip_x in ips_afectadas:
                if ip_x and ip_x not in ips_restantes:
                    for tbl in ('nombres_mineros', 'logs_mineros', 'cola_comandos'):
                        cur.execute(f"DELETE FROM {tbl} WHERE owner=%s AND ip=%s", (owner, ip_x))
        cur.execute("DELETE FROM racks_sedes WHERE owner=%s AND farm_id=%s", (owner, farm_id))
        cur.execute("DELETE FROM mineros_descubiertos WHERE owner=%s AND farm_id=%s", (owner, farm_id))
        cur.execute("DELETE FROM agentes_conectados WHERE owner=%s AND farm_id=%s", (owner, farm_id))
        if ag_ref:
            cur.execute(
                "DELETE FROM agentes_conectados WHERE owner=%s AND agent_id=%s AND NOT EXISTS (SELECT 1 FROM granjas_sedes gs WHERE gs.owner=%s AND gs.agent_id=%s AND gs.farm_id != %s)",
                (owner, ag_ref, owner, ag_ref, farm_id)
            )
    else:
        cur.execute(
            "UPDATE mineros_activos SET farm_id=agent_id WHERE owner=%s AND farm_id=%s",
            (owner, farm_id)
        )

    cur.execute("DELETE FROM granjas_sedes WHERE owner=%s AND farm_id=%s", (owner, farm_id))
    return eliminados_m, movidos_m


@panel_bp.route('/api/granjas/eliminar', methods=['POST'])
@login_required
def api_granjas_eliminar():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    farm_id = str(data.get('farm_id') or '').strip()
    if not farm_id:
        return jsonify({"status": "error", "msg": "farm_id requerido"}), 400

    mover_a = str(data.get('mover_a_farm_id') or '').strip()
    eliminar_mineros = bool(data.get('eliminar_mineros', True))

    con = get_db(); con.autocommit = True; cur = con.cursor()
    eliminados_m, movidos_m = _borrar_granja_completa(
        cur, usr_actual, farm_id, eliminar_mineros=eliminar_mineros, mover_a_farm_id=mover_a
    )
    return jsonify({"status": "ok", "eliminados_mineros": eliminados_m, "movidos_mineros": movidos_m})

@panel_bp.route('/api/granjas/editar_minero', methods=['POST'])
@login_required
def api_granjas_editar_minero():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    ip = str(data.get('ip') or '').strip()
    raw_nombre = data.get('nombre')
    nuevo_nombre = sanitizar_nombre_minero(raw_nombre, ip_defecto=ip) if raw_nombre is not None else ''
    nuevo_farm_id = str(data.get('farm_id') or '').strip()

    if not ip:
        return jsonify({"status": "error", "msg": "IP requerida"}), 400

    con = get_db(); con.autocommit = True; cur = con.cursor()
    if nuevo_nombre:
        cur.execute(
            "INSERT INTO nombres_mineros (owner, ip, nombre) VALUES (%s, %s, %s) ON CONFLICT (owner, ip) DO UPDATE SET nombre=EXCLUDED.nombre",
            (usr_actual, ip, nuevo_nombre)
        )
    if nuevo_farm_id:
        cur.execute(
            "UPDATE mineros_activos SET farm_id=%s WHERE owner=%s AND ip=%s",
            (nuevo_farm_id, usr_actual, ip)
        )
    return jsonify({"status": "ok", "ip": ip, "nombre": nuevo_nombre, "farm_id": nuevo_farm_id})

# --- APIs del Panel ---
@panel_bp.route('/api/eliminar_mineros', methods=['POST'])
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

@panel_bp.route('/api/guardar_tarifa', methods=['POST'])
@login_required
def api_guardar_tarifa():
    usr_actual = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    con = get_db(); con.autocommit = True; cur = con.cursor()

    tiene_tarifa = 'tarifa_kwh' in data
    tiene_comision = 'comision_pool' in data
    tarifa = max(0.0, a_float_seguro(data.get('tarifa_kwh'), 0.0)) if tiene_tarifa else None
    comision = min(100.0, max(0.0, a_float_seguro(data.get('comision_pool'), 0.0))) if tiene_comision else None

    if tiene_tarifa and tiene_comision:
        cur.execute(
            "UPDATE usuarios SET tarifa_kwh=%s, comision_pool=%s WHERE username=%s",
            (tarifa, comision, usr_actual)
        )
        return jsonify({"status": "ok", "tarifa_kwh": tarifa, "comision_pool": comision})
    elif tiene_comision:
        cur.execute("UPDATE usuarios SET comision_pool=%s WHERE username=%s", (comision, usr_actual))
        return jsonify({"status": "ok", "comision_pool": comision})
    else:
        tarifa = max(0.0, a_float_seguro(data.get('tarifa_kwh'), 0.0))
        cur.execute("UPDATE usuarios SET tarifa_kwh=%s WHERE username=%s", (tarifa, usr_actual))
        return jsonify({"status": "ok", "tarifa_kwh": tarifa})

@panel_bp.route('/api/guardar_corte_termico', methods=['POST'])
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

@panel_bp.route('/api/guardar_watchdog_minero', methods=['POST'])
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

@panel_bp.route('/api/guardar_gps', methods=['POST'])
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

    owner_target = usr_actual
    if es_super_admin() and data.get('owner'):
        owner_target = str(data.get('owner')).strip()

    farm_id = str(data.get('farm_id') or '').strip()
    agent_id = str(data.get('agent_id') or '').strip()
    subred = str(data.get('subred') or '').strip()
    con = get_db(); con.autocommit = True; cur = con.cursor()

    if farm_id:
        cur.execute(
            "UPDATE mineros_activos SET farm_lat=%s, farm_lon=%s, farm_city=%s, farm_country=%s, gps_manual=1 WHERE owner=%s AND (farm_id=%s OR ((farm_id IS NULL OR farm_id='') AND agent_id=%s))",
            (lat, lon, ciudad, pais, owner_target, farm_id, agent_id or farm_id)
        )
        cur.execute(
            "UPDATE granjas_sedes SET ciudad=%s WHERE owner=%s AND farm_id=%s",
            (ciudad, owner_target, farm_id)
        )
    elif agent_id and subred and subred.endswith('.x'):
        cur.execute(
            "UPDATE mineros_activos SET farm_lat=%s, farm_lon=%s, farm_city=%s, farm_country=%s, gps_manual=1 WHERE owner=%s AND agent_id=%s AND ip LIKE %s",
            (lat, lon, ciudad, pais, owner_target, agent_id, subred[:-1] + '%')
        )
    elif agent_id:
        cur.execute(
            "UPDATE mineros_activos SET farm_lat=%s, farm_lon=%s, farm_city=%s, farm_country=%s, gps_manual=1 WHERE owner=%s AND agent_id=%s",
            (lat, lon, ciudad, pais, owner_target, agent_id)
        )
    elif subred and subred.endswith('.x'):
        cur.execute(
            "UPDATE mineros_activos SET farm_lat=%s, farm_lon=%s, farm_city=%s, farm_country=%s, gps_manual=1 WHERE owner=%s AND ip LIKE %s",
            (lat, lon, ciudad, pais, owner_target, subred[:-1] + '%')
        )
    else:
        cur.execute(
            "UPDATE mineros_activos SET farm_lat=%s, farm_lon=%s, farm_city=%s, farm_country=%s, gps_manual=1 WHERE owner=%s",
            (lat, lon, ciudad, pais, owner_target)
        )
    return jsonify({"status": "ok", "lat": lat, "lon": lon, "ciudad": ciudad, "pais": pais, "owner": owner_target})

def _calcular_rentabilidad_btc_dia(hr_th, consumo_w, datos_red, tarifa_kwh, comision_pool=0.0):
    """Calcula (btc_dia, ingreso_usd, costo_elec_usd, neto_usd) para un minero SHA-256 activo."""
    dif_t = a_float_seguro((datos_red or {}).get('dificultad_t'), 0.0)
    precio = a_float_seguro((datos_red or {}).get('precio'), 0.0)
    recompensa = a_float_seguro((datos_red or {}).get('recompensa'), 3.125)
    if hr_th <= 0 or dif_t <= 0:
        return 0.0, 0.0, 0.0, 0.0
    btc_dia = ((hr_th * recompensa * 86400.0) / (dif_t * 4294967296.0)) * (1.0 - comision_pool / 100.0)
    ingreso_usd = btc_dia * precio
    costo_elec_usd = (max(0.0, consumo_w) / 1000.0) * 24.0 * max(0.0, tarifa_kwh)
    return btc_dia, ingreso_usd, costo_elec_usd, (ingreso_usd - costo_elec_usd)

# --- Vistas Principales ---
@panel_bp.route('/resumen')
@login_required
def index():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT tarifa_kwh, comision_pool, plan, saldo FROM usuarios WHERE username=%s", (usr_actual,))
    u_row = cur.fetchone() or {}
    tarifa_kwh = a_float_seguro(u_row.get('tarifa_kwh'), 0.0)
    comision_pool = min(100.0, max(0.0, a_float_seguro(u_row.get('comision_pool'), 0.0)))
    session['plan'] = u_row.get('plan', session.get('plan', 'Gratis'))
    session['saldo'] = u_row.get('saldo', session.get('saldo', 0.0))

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    mineros = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    datos_red = obtener_datos_red()
    registros, en_linea, offline = [], 0, 0
    ht, kt, gt, cw, cw_btc, tm, ig, gan_neta_total = 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0

    for m in mineros:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        algo = m.get('algoritmo') or ('Equihash' if 'z15' in str(mod).lower() else 'SHA-256')
        mon = m.get('moneda') or ('ZEC' if algo == 'Equihash' else 'BTC')
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre_custom = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))

        segs_desconectado = calcular_segundos_desconectado(m.get('ultima_act'))
        es_online = segs_desconectado < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_online else 0.0
        t = a_int_seguro(m.get('temp_max'), 0) if es_online else 0
        esta_minando = es_online and hr > 0
        c = a_float_seguro(m.get('consumo_w'), 0.0) if esta_minando else 0.0
        eficiencia_val = round(c / hr, 2) if esta_minando else 0
        if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)

        if es_online:
            en_linea += 1
            es_btc = (unidad == 'TH/s' and algo == 'SHA-256')
            if esta_minando:
                if es_btc:
                    ht += hr
                    cw_btc += c
                elif unidad == 'kSol/s': kt += hr
                elif unidad == 'GH/s': gt += hr
                cw += c

            tm = max(tm, t)
            if not esta_minando:
                str_gan = "USD 0.00"
                neto_num = 0.0
            elif es_btc:
                if datos_red['dificultad_t'] > 0:
                    _, ib, _, neto = _calcular_rentabilidad_btc_dia(hr, c, datos_red, tarifa_kwh, comision_pool)
                    ig += ib
                    gan_neta_total += neto
                    str_gan = f"-USD {abs(neto):.2f}" if neto < 0 else f"USD {neto:.2f}"
                    neto_num = round(neto, 4)
                else:
                    str_gan = "USD 0.00"
                    neto_num = 0.0
            else:
                str_gan = f"Pool {mon}"
                neto_num = 0.0

            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'algoritmo': algo, 'moneda': mon,
                'unidad': unidad, 'unidad_efi': unidad_efi,
                'estado': '<span style="color:var(--accent-green);">● En línea</span>',
                'tiempo_act': f'hace {int(segs_desconectado)}s', 'hashrate': hr,
                'hashrate_30m': a_float_seguro(m.get('hashrate_30m'), hr) if esta_minando else 0,
                'temp_num': t, 'consumo_kw': f"{c/1000:.2f} kW",
                'eficiencia': eficiencia_val, 'ganancia_neta': str_gan, 'ganancia_neta_num': neto_num
            })
        else:
            offline += 1
            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'algoritmo': algo, 'moneda': mon,
                'unidad': unidad, 'unidad_efi': unidad_efi,
                'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>',
                'tiempo_act': f'hace {int(segs_desconectado // 60)}m', 'hashrate': 0, 'hashrate_30m': 0, 'temp_num': 0,
                'consumo_kw': '0.00 kW', 'eficiencia': 0, 'ganancia_neta': "USD 0.00", 'ganancia_neta_num': 0.0
            })

    flota = {
        'hashrate_total': round(ht, 1),
        'ksol_total': round(kt, 1),
        'ghs_total': round(gt, 1),
        'consumo_w_total': round(cw, 0),
        'consumo_w_btc': round(cw_btc, 0),
        'consumo_kw': round(cw/1000, 1), 'temp_max': tm,
        'tarifa_kwh': tarifa_kwh,
        'comision_pool': comision_pool,
        'precio_btc_raw': datos_red['precio'],
        'precio_btc': f"{datos_red['precio']:,.2f}",
        'dificultad_t': round(datos_red['dificultad_t'], 2),
        'recompensa_bloque': datos_red['recompensa'],
        'red_ehs': datos_red['red_ehs'],
        'ingreso_dia': f"{ig:.2f}",
        'ganancia_dia_raw': round(gan_neta_total, 2),
        'ganancia_mes_raw': round(gan_neta_total * 30.0, 0),
        'ganancia_dia': f"-USD {abs(gan_neta_total):.2f}" if gan_neta_total < 0 else f"USD {gan_neta_total:.2f}"
    }
    return render_seguro('resumen.html', active_page='resumen', registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=flota)

@panel_bp.route('/minero/<ip>')
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

    cur.execute("""
        SELECT tipo, mensaje, fecha
        FROM registro_alertas
        WHERE owner=%s AND (ip=%s OR ip='TARIFA_PICO')
          AND tipo IN ('WATCHDOG_REBOOT', 'WATCHDOG_CUARENTENA', 'CORTE_TERMICO', 'HORARIO_PICO', 'HORARIO_NORMAL')
        ORDER BY id DESC
        LIMIT 80
    """, (usr_actual, ip))
    eventos_graf = []
    for ev in cur.fetchall():
        try:
            ts_ev = calendar.timegm(datetime.strptime(ev['fecha'], "%Y-%m-%d %H:%M:%S").timetuple())
            eventos_graf.append({
                "ts": ts_ev,
                "tipo": str(ev.get('tipo') or ''),
                "msg": str(ev.get('mensaje') or '')
            })
        except Exception:
            continue

    mod_str = str(m.get('modelo') or 'Antminer')
    algoritmo_val = str(m.get('algoritmo') or ('Equihash' if 'z15' in mod_str.lower() else 'SHA-256'))
    moneda_val = str(m.get('moneda') or ('ZEC' if algoritmo_val == 'Equihash' else 'BTC'))
    unidad_hr = str(m.get('unidad') or ('kSol/s' if 'z15' in mod_str.lower() else 'TH/s'))
    unidad_efi = "J/kSol" if unidad_hr == "kSol/s" else ("J/GH" if unidad_hr == "GH/s" else "J/TH")

    nombre_minero = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
    hr_val = a_float_seguro(m.get('hashrate'), 0.0)
    hr_30m_val = a_float_seguro(m.get('hashrate_30m'), hr_val)
    t_val = a_int_seguro(m.get('temp_max'), 0)
    if t_val >= 150 or t_val < 0: t_val = 0
    t_amb_val = a_int_seguro(m.get('temp_amb'), 0)
    if t_amb_val >= 120 or t_amb_val < -40: t_amb_val = 0
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
            t_raw = a_int_seguro(h.get('temp_max'), 0)
            tp_g.append(t_raw if 0 < t_raw < 150 else 0)

            val_amb = a_int_seguro(h.get('temp_amb'), 0)
            tamb_g.append(val_amb if 0 < val_amb < 120 else None)

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
    cred_calc = determinar_credenciales_defecto(m.get('marca', ''), mod_str)
    marca_val = m.get('marca') or cred_calc['marca']
    cred_val = m.get('cred_defecto') or f"{cred_calc['user']} / {cred_calc['pass']}"

    extra = {
        'marca': marca_val, 'cred_defecto': cred_val,
        'cred_user': cred_calc['user'], 'cred_pass': cred_calc['pass'],
        'cred_alt': cred_calc['alt'], 'cred_nota': cred_calc['nota'],
        'worker1': worker1, 'pool1': pool1, 'pass1': pass1, 'status1': status1, 'acc1': acc1, 'rej1': rej1,
        'worker2': worker2, 'pool2': pool2, 'pass2': pass2, 'status2': status2, 'acc2': acc2, 'rej2': rej2,
        'worker3': worker3, 'pool3': pool3, 'pass3': pass3, 'status3': status3, 'acc3': acc3, 'rej3': rej3,
        'mac': mac, 'serial': serial, 'firmware': fw, 'cadenas': cadenas,
        'hostname': hostname, 'net_proto': net_proto, 'netmask': netmask, 'gateway': gateway, 'dns': dns
    }

    eficiencia_val = round(c_val / hr_val, 2) if hr_val > 0 else 0
    if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)
    disp = {'eficiencia': eficiencia_val}

    ag_minero = str(m.get('agent_id') or 'default').strip() or 'default'
    tunel_ok = tunel_esta_activo(usr_actual, agent_id=ag_minero)

    return render_seguro(
        'minero.html', active_page='mineros', ip=ip, nombre=nombre_minero, modelo=mod_str,
        ultimo=dt, extra=extra, dispositivo=disp, offline=offline, tunel_activo=tunel_ok,
        graf_fechas_raw=json.dumps(f_raw), graf_hashrate=json.dumps(hr_g),
        graf_temps=json.dumps(tp_g), graf_temp_amb=json.dumps(tamb_g),
        graf_errores=json.dumps(err_g), cadenas_json=json.dumps(cadenas),
        graf_cadenas_hist=json.dumps(cad_g), ips_ocupadas=json.dumps(ips_ocupadas),
        eventos_graf_json=json.dumps(eventos_graf)
    )

@panel_bp.route('/mineros')
@login_required
def mineros():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT tarifa_kwh, comision_pool FROM usuarios WHERE username=%s", (usr_actual,))
    u_row = cur.fetchone() or {}
    tarifa_kwh = max(0.0, a_float_seguro(u_row.get('tarifa_kwh'), 0.0))
    comision_pool = min(100.0, max(0.0, a_float_seguro(u_row.get('comision_pool'), 0.0)))

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    cur.execute("SELECT farm_id, nombre, ciudad FROM granjas_sedes WHERE owner=%s", (usr_actual,))
    sedes_dict = {
        str(row['farm_id']).strip(): row
        for row in (cur.fetchall() or [])
        if str(row.get('farm_id') or '').strip()
    }
    granjas_dict = {k: str(v.get('nombre') or '').strip() for k, v in sedes_dict.items() if v.get('nombre')}

    datos_red = obtener_datos_red()
    lista_mineros = []
    contadores = {'total': len(filas), 'online': 0, 'offline': 0, 'alerta_temp': 0, 'alerta_hw': 0}

    for m in filas:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        algo = m.get('algoritmo') or ('Equihash' if 'z15' in str(mod).lower() else 'SHA-256')
        mon = m.get('moneda') or ('ZEC' if algo == 'Equihash' else 'BTC')
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
        ag_id = str(m.get('agent_id') or 'default').strip() or 'default'
        fid = str(m.get('farm_id') or ag_id).strip() or 'default'
        sede_info = sedes_dict.get(fid) or {}
        granja_nombre = str(sede_info.get('nombre') or '').strip()
        if not granja_nombre:
            ciudad_m = str(sede_info.get('ciudad') or m.get('farm_city') or '').strip()
            if ciudad_m:
                granja_nombre = f"Granja {ciudad_m} ({fid})"
            elif fid != 'default':
                granja_nombre = f"Granja ({fid})"
            else:
                granja_nombre = "Granja Principal"

        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_online = segs < 300

        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_online else 0.0
        esta_minando = es_online and hr > 0
        hr_30m = a_float_seguro(m.get('hashrate_30m'), hr) if esta_minando else 0.0
        t_max = a_int_seguro(m.get('temp_max'), 0) if es_online else 0
        t_amb = a_int_seguro(m.get('temp_amb'), 0) if es_online else 0
        cons_w = a_float_seguro(m.get('consumo_w'), 0.0) if esta_minando else 0.0
        err_hw = a_int_seguro(m.get('errores'), 0) if es_online else 0

        efi = round(cons_w / hr, 2) if esta_minando else 0.0
        if efi > 1000: efi = round(efi / 1000, 2)

        try: cads = json.loads(m['cadenas']) if m.get('cadenas') else []
        except: cads = []
        placas_activas = len(cads) if es_online else 0
        chips_totales = sum(a_int_seguro(c.get('chips'), 0) for c in cads) if es_online else 0

        acc1 = a_int_seguro(m.get('acc1'), 0)
        rej1 = a_int_seguro(m.get('rej1'), 0)
        tot_shares = acc1 + rej1
        pct_rej = round((rej1 / tot_shares) * 100, 2) if tot_shares > 0 else 0.0

        gan_usd = 0.0
        gan_cripto = 0.0
        if esta_minando and unidad == 'TH/s' and algo == 'SHA-256' and datos_red.get('dificultad_t', 0) > 0:
            btc_d, _, _, neto_d = _calcular_rentabilidad_btc_dia(hr, cons_w, datos_red, tarifa_kwh, comision_pool)
            gan_usd = round(neto_d, 2)
            gan_cripto = round(btc_d, 8)

        if es_online:
            contadores['online'] += 1
            if t_max >= 75: contadores['alerta_temp'] += 1
            if err_hw > 50 or pct_rej > 1.5: contadores['alerta_hw'] += 1
        else:
            contadores['offline'] += 1

        lista_mineros.append({
            'ip': ip, 'ultimo_octeto': ip.split('.')[-1], 'nombre': nombre, 'modelo': mod,
            'farm_id': fid, 'granja_nombre': granja_nombre,
            'ganancia_dia_usd': gan_usd, 'ganancia_dia_cripto': gan_cripto,
            'algoritmo': algo, 'moneda': mon,
            'mac': m.get('mac') or '-', 'firmware': m.get('firmware') or '-', 'online': es_online,
            'uptime': (m.get('uptime') or 'En línea') if es_online else f'Sin conexión ({int(segs // 60)}m)',
            'hashrate': hr, 'hashrate_30m': hr_30m, 'unidad': unidad, 'unidad_efi': unidad_efi,
            'eficiencia': efi, 'consumo_kw': round(cons_w / 1000.0, 2), 'temp_max': t_max, 'temp_amb': t_amb,
            'errores': err_hw, 'fans': m.get('fans') or '0 / 0 RPM', 'placas_activas': placas_activas,
            'chips_totales': chips_totales, 'pool1': m.get('pool1') or '-', 'worker1': m.get('worker1') or '-',
            'pass1': m.get('pass1') or '123', 'pool2': m.get('pool2') or '', 'worker2': m.get('worker2') or '',
            'pass2': m.get('pass2') or '123', 'pool3': m.get('pool3') or '', 'worker3': m.get('worker3') or '',
            'pass3': m.get('pass3') or '123', 'acc1': acc1, 'rej1': rej1, 'pct_rej': pct_rej
        })

    return render_seguro('mineros.html', active_page='mineros', mineros=lista_mineros, stats=contadores)

@panel_bp.route('/granjas')
@login_required
def granjas():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM granjas_sedes WHERE owner=%s ORDER BY id ASC", (usr_actual,))
    sedes_db = cur.fetchall()

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    grupos = {}
    for s in sedes_db:
        fid = str(s.get('farm_id') or '').strip()
        if not fid:
            continue
        segs_ag = calcular_segundos_desconectado(s.get('ultima_act')) if s.get('ultima_act') else 999999
        dev_type = str(s.get('device_type') or 'Desconocido')
        dev_brand = str(s.get('brand') or '')
        dev_model = str(s.get('model') or '')
        dev_comercial, dev_code = resolver_nombre_dispositivo(dev_brand, dev_model, dev_type)
        ag_sede = str(s.get('agent_id') or fid)

        grupos[fid] = {
            'farm_id': fid,
            'agent_id': ag_sede,
            'nombre': str(s.get('nombre') or f"Sede {fid}"),
            'ciudad': str(s.get('ciudad') or 'Santa Cruz de la Sierra'),
            'subred': str(s.get('subred') or '192.168.0.x'),
            'gateway': str(s.get('gateway') or '192.168.0.1'),
            'router_mac': str(s.get('router_mac') or ''),
            'cap_kw': a_float_seguro(s.get('cap_kw'), 50.0),
            'tarifa_kwh': a_float_seguro(s.get('tarifa_kwh'), 0.06),
            'notas': str(s.get('notas') or ''),
            'device_type': dev_type,
            'device_commercial': dev_comercial,
            'device_code': dev_code,
            'run_mode': str(s.get('run_mode') or 'Agente'),
            'brand': dev_brand,
            'model': dev_model,
            'local_ip': str(s.get('local_ip') or ''),
            'has_battery': bool(a_int_seguro(s.get('has_battery'), 0) == 1),
            'battery_pct': a_int_seguro(s.get('battery_pct'), -1),
            'charging': bool(a_int_seguro(s.get('charging'), 0) == 1),
            'battery_temp_c': a_float_seguro(s.get('battery_temp_c'), 0.0),
            'os_info': str(s.get('os_info') or ''),
            'app_version': str(s.get('app_version') or ''),
            'agent_online': segs_ag < 300,
            'tunel_activo': tunel_esta_activo(usr_actual, agent_id=ag_sede, estricto=True),
            'es_manual': not bool(s.get('agent_id')),
            'total': 0, 'online': 0, 'offline': 0,
            'hashrate': 0.0, 'consumo_kw': 0.0, 'temp_max': 0, 'mineros': []
        }

    for m in filas:
        ip = m['ip']
        ciudad = str(m.get('farm_city') or 'Santa Cruz de la Sierra')
        subred = ".".join(ip.split('.')[:3]) + ".x" if '.' in ip else "LAN"
        ag_id = str(m.get('agent_id') or 'default').strip() or 'default'
        fid = str(m.get('farm_id') or ag_id).strip() or 'default'

        gw_reportado = str(m.get('gateway') or '').strip()
        gw_fallback = (".".join(ip.split('.')[:3]) + ".1") if '.' in ip else "192.168.0.1"
        gw_ip = gw_reportado if (gw_reportado and gw_reportado != ip) else gw_fallback

        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_online = segs < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_online else 0.0
        cw = a_float_seguro(m.get('consumo_w'), 0.0) if es_online else 0.0
        tm = a_int_seguro(m.get('temp_max'), 0) if es_online else 0

        if fid not in grupos:
            dev_type = 'Celular' if ag_id.startswith('AND-') else 'PC / Servidor'
            grupos[fid] = {
                'farm_id': fid,
                'agent_id': ag_id,
                'nombre': f"Granja {ciudad} ({fid})",
                'ciudad': ciudad, 'subred': subred, 'gateway': gw_ip, 'router_mac': '',
                'cap_kw': 50.0, 'tarifa_kwh': 0.06, 'notas': '',
                'device_type': dev_type,
                'device_commercial': dev_type,
                'device_code': '',
                'run_mode': 'APK Directo' if ag_id.startswith('AND-') else ('Termux' if ag_id.startswith('TERMUX-') else 'Python PC'),
                'brand': '', 'model': '', 'local_ip': '',
                'has_battery': False, 'battery_pct': -1, 'charging': False, 'battery_temp_c': 0.0, 'os_info': '',
                'app_version': '',
                'agent_online': es_online,
                'tunel_activo': tunel_esta_activo(usr_actual, agent_id=ag_id, estricto=True),
                'es_manual': False,
                'total': 0, 'online': 0, 'offline': 0,
                'hashrate': 0.0, 'consumo_kw': 0.0, 'temp_max': 0, 'mineros': []
            }

        g = grupos[fid]
        if gw_reportado and gw_reportado != ip and (not g['gateway'] or g['gateway'] == '192.168.0.1'):
            g['gateway'] = gw_reportado
        g['total'] += 1
        if es_online:
            g['online'] += 1
            g['hashrate'] = round(g['hashrate'] + hr, 2)
            g['consumo_kw'] = round(g['consumo_kw'] + (cw / 1000.0), 2)
            g['temp_max'] = max(g['temp_max'], tm)
        else:
            g['offline'] += 1

        g['mineros'].append({
            'ip': ip,
            'nombre': nombre_inteligente_minero(ip, nombres_dict, m.get('worker1')),
            'modelo': m.get('modelo') or 'Antminer',
            'online': es_online,
            'hashrate': hr,
            'unidad': m.get('unidad') or 'TH/s',
            'temp_max': tm,
            'farm_id': fid
        })

    lista_granjas = list(grupos.values())
    for g in lista_granjas:
        cap = max(1.0, g['cap_kw'])
        g['pct_carga'] = min(100, int(round((g['consumo_kw'] / cap) * 100)))
        g['costo_dia'] = round(g['consumo_kw'] * 24.0 * g['tarifa_kwh'], 2)

    return render_seguro('granjas.html', active_page='granjas', granjas=lista_granjas)

@panel_bp.route('/alertas', methods=['GET', 'POST'])
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

            curt_activo = 1 if request.form.get('curt_activo') == '1' else 0
            curt_inicio = (request.form.get('curt_inicio') or '18:00').strip()[:5]
            curt_fin = (request.form.get('curt_fin') or '22:00').strip()[:5]
            curt_accion = request.form.get('curt_accion', 'profile|low')
            if curt_accion not in ('profile|low', 'sleep'):
                curt_accion = 'profile|low'

            cur.execute("""
                UPDATE usuarios
                SET wd_activo=%s, wd_alcance=%s, wd_granjas=%s, wd_mineros=%s,
                    wd_zero_hr=%s, wd_cold_on=%s, wd_cold_temp=%s,
                    wd_amb_cold_on=%s, wd_amb_cold_temp=%s, wd_dead_board=%s,
                    wd_gracia_min=%s, wd_cooldown_min=%s, wd_cuarentena_on=%s, wd_max_reinicios=%s,
                    prot_activo=%s, prot_temp_on=%s, prot_temp_val=%s, prot_temp_accion=%s,
                    prot_clima_on=%s, prot_clima_val=%s, prot_clima_accion=%s,
                    prot_net_on=%s, prot_net_rej_pct=%s, prot_net_accion=%s,
                    prot_fan_on=%s, prot_fan_accion=%s, prot_wakeup_on=%s, prot_wakeup_min=%s,
                    curt_activo=%s, curt_inicio=%s, curt_fin=%s, curt_accion=%s,
                    curt_estado_actual=CASE WHEN %s = 0 THEN 'normal' ELSE curt_estado_actual END
                WHERE username=%s
            """, (wd_activo, wd_alcance, wd_granjas, wd_mineros, wd_zero_hr, wd_cold_on, wd_cold_temp,
                  wd_amb_cold_on, wd_amb_cold_temp, wd_dead_board, wd_gracia_min, wd_cooldown_min,
                  wd_cuarentena_on, wd_max_reinicios, prot_activo, prot_temp_on, prot_temp_val, prot_temp_accion,
                  prot_clima_on, prot_clima_val, prot_clima_accion, prot_net_on, prot_net_rej_pct, prot_net_accion,
                  prot_fan_on, prot_fan_accion, prot_wakeup_on, prot_wakeup_min,
                  curt_activo, curt_inicio, curt_fin, curt_accion, curt_activo, usr_actual))
            msg = "Reglas de Protección, Watchdog y Horarios de Energía guardadas correctamente."
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
                despachar_comando(cur, usr_actual, ip_lib, 'normal', ahora)
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
        SELECT tarifa_kwh, tg_token, tg_chat_id, umbral_temp, umbral_hr, alertas_activas,
               wd_activo, wd_alcance, wd_granjas, wd_mineros, wd_zero_hr, wd_cold_on,
               wd_cold_temp, wd_amb_cold_on, wd_amb_cold_temp, wd_dead_board,
               wd_gracia_min, wd_cooldown_min, wd_cuarentena_on, wd_max_reinicios,
               prot_activo, prot_temp_on, prot_temp_val, prot_temp_accion,
               prot_clima_on, prot_clima_val, prot_clima_accion,
               prot_net_on, prot_net_rej_pct, prot_net_accion,
               prot_fan_on, prot_fan_accion, prot_wakeup_on, prot_wakeup_min,
               curt_activo, curt_inicio, curt_fin, curt_accion, curt_estado_actual
        FROM usuarios WHERE username=%s
    """, (usr_actual,))
    row_u = cur.fetchone() or {}
    tarifa_u = a_float_seguro(row_u.get('tarifa_kwh'), 0.06)
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
        'prot_wakeup_min': a_int_seguro(row_u.get('prot_wakeup_min'), 30),
        'curt_activo': bool(a_int_seguro(row_u.get('curt_activo'), 0) == 1),
        'curt_inicio': row_u.get('curt_inicio') or '18:00',
        'curt_fin': row_u.get('curt_fin') or '22:00',
        'curt_accion': row_u.get('curt_accion') or 'profile|low',
        'curt_estado_actual': row_u.get('curt_estado_actual') or 'normal'
    }

    cur.execute("SELECT ip, modelo, worker1, farm_city, consumo_w, watchdog_activo, wd_cuarentena, wd_reinicios_seguidos, wd_motivo_fallo, prot_sleep_desde, prot_motivo FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas_m = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    granjas_map = {}
    mineros_disp = []
    en_cuarentena = []
    kw_flota_curt = 0.0
    for fm in filas_m:
        ip_m = fm['ip']
        clave_g = clave_granja_minero(ip_m, fm.get('farm_city'))
        granjas_map[clave_g] = granjas_map.get(clave_g, 0) + 1
        es_q = a_int_seguro(fm.get('wd_cuarentena'), 0) == 1
        es_psleep = bool(str(fm.get('prot_sleep_desde') or '').strip())
        aplica_curt = (
            wd['alcance'] == 'all'
            or (wd['alcance'] == 'farms' and clave_g in wd['granjas_sel'])
            or (wd['alcance'] == 'miners' and ip_m in wd['mineros_sel'])
        )
        if aplica_curt:
            kw_flota_curt += a_float_seguro(fm.get('consumo_w'), 0.0) / 1000.0
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

    horas_pico = _calcular_horas_rango(wd['curt_inicio'], wd['curt_fin'])
    factor_ahorro = 0.95 if wd['curt_accion'] == 'sleep' else 0.30
    kwh_dia_ahorrados = round(kw_flota_curt * horas_pico * factor_ahorro, 1)
    usd_dia_ahorrados = round(kwh_dia_ahorrados * tarifa_u, 2)
    usd_mes_ahorrados = round(usd_dia_ahorrados * 30.0, 2)

    fecha_30d = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM registro_alertas WHERE owner=%s AND tipo='HORARIO_PICO' AND fecha >= %s",
        (usr_actual, fecha_30d)
    )
    row_cnt = cur.fetchone() or {}
    ejecuciones_30d = a_int_seguro(row_cnt.get('cnt'), 0)

    ahorro_curt = {
        'horas_pico': horas_pico,
        'kw_objetivo': round(kw_flota_curt, 2),
        'tarifa_kwh': tarifa_u,
        'kwh_dia': kwh_dia_ahorrados,
        'kwh_mes': round(kwh_dia_ahorrados * 30.0, 0),
        'usd_dia': usd_dia_ahorrados,
        'usd_mes': usd_mes_ahorrados,
        'ejecuciones_30d': ejecuciones_30d,
        'usd_real_30d': round(usd_dia_ahorrados * ejecuciones_30d, 2)
    }

    cur.execute("SELECT * FROM registro_alertas WHERE owner=%s ORDER BY id DESC LIMIT 100", (usr_actual,))
    historial = cur.fetchall()
    return render_seguro(
        'alertas.html', active_page='alertas', cfg=cfg, wd=wd,
        ahorro_curt=ahorro_curt,
        granjas_disp=granjas_disp, mineros_disp=mineros_disp,
        en_cuarentena=en_cuarentena, historial=historial, msg=msg
    )

@panel_bp.route('/ajustes', methods=['GET', 'POST'])
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
            miner_user = (request.form.get('miner_user') or 'root').strip()[:40] or 'root'
            miner_pass = (request.form.get('miner_pass') or '').strip()[:80]
            cur.execute(
                "UPDATE usuarios SET subnet=%s, tarifa_kwh=%s, miner_user=%s, miner_pass=%s WHERE username=%s",
                (subnet, tarifa, miner_user, miner_pass, usr_actual)
            )
            msg = "Preferencias de red, tarifa y credenciales ASIC guardadas."
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
                        ('mineros_activos', 'owner'), ('granjas_sedes', 'owner'),
                        ('nombres_mineros', 'owner'), ('logs_mineros', 'owner'),
                        ('historial_nube', 'owner'), ('cola_comandos', 'owner'),
                        ('registro_alertas', 'owner'), ('movimientos_saldo', 'username')
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
        elif accion == 'guardar_telegram':
            tg_tok = (request.form.get('tg_token') or '').strip()
            tg_cid = (request.form.get('tg_chat_id') or '').strip()
            cur.execute("UPDATE usuarios SET tg_token=%s, tg_chat_id=%s WHERE username=%s", (tg_tok, tg_cid, usr_actual))
            msg = "Configuración de Telegram guardada correctamente."
        elif accion == 'probar_telegram':
            cur.execute("SELECT tg_token, tg_chat_id FROM usuarios WHERE username=%s", (usr_actual,))
            cfg_tg = cur.fetchone() or {}
            tok = cfg_tg.get('tg_token') or ''
            cid = cfg_tg.get('tg_chat_id') or ''
            if tok and cid:
                ok = enviar_telegram(tok, cid, f"🟢 <b>El Minero Gris — Prueba Exitosa</b>\n¡Bot de Telegram vinculado correctamente a la cuenta <b>{usr_actual}</b>!")
                msg = "Mensaje de prueba enviado con éxito a tu Telegram." if ok else "No se pudo enviar. Revisa el Token, Chat ID y asegúrate de haber pulsado 'Iniciar' en tu bot de Telegram."
            else:
                msg = "Primero debes ingresar y guardar tu Token y Chat ID de Telegram."

    cur.execute("SELECT * FROM usuarios WHERE username=%s", (usr_actual,))
    u_data = cur.fetchone() or {}
    if not u_data.get('api_key'):
        nueva_k = generar_api_key()
        cur.execute("UPDATE usuarios SET api_key=%s WHERE username=%s", (nueva_k, usr_actual))
        u_data['api_key'] = nueva_k

    # 2FA y Dispositivos de Confianza
    totp_activo = bool(a_int_seguro(u_data.get('totp_activo'), 0) == 1)
    ahora_dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "SELECT id, dispositivo, ip, fecha_creacion, expira_en, token_hash FROM dispositivos_confianza WHERE LOWER(username)=LOWER(%s) AND expira_en > %s ORDER BY id DESC",
        (usr_actual, ahora_dt)
    )
    dispositivos_db = cur.fetchall() or []

    token_actual = request.cookies.get('emg_trusted_device')
    hash_actual = hashlib.sha256(token_actual.encode('utf-8')).hexdigest() if token_actual else None
    lista_dispositivos = []
    for d in dispositivos_db:
        es_act = bool(hash_actual and d.get('token_hash') == hash_actual)
        disp_nom = d.get('dispositivo') or 'Navegador Web'
        dl = disp_nom.lower()
        if 'android' in dl or 'iphone' in dl or 'ipad' in dl:
            ico = '📱'
        elif 'windows' in dl:
            ico = '💻'
        elif 'mac' in dl:
            ico = '🖥️'
        elif 'linux' in dl:
            ico = '🐧'
        else:
            ico = '🌐'

        lista_dispositivos.append({
            'id': d['id'],
            'dispositivo': disp_nom,
            'icono': ico,
            'ip': d.get('ip') or 'Desconocida',
            'fecha_creacion': d.get('fecha_creacion') or '',
            'expira_en': d.get('expira_en') or '',
            'es_actual': es_act
        })

    user_info = {
        'username': usr_actual, 'plan': u_data.get('plan', 'Gratis'),
        'api_key': u_data.get('api_key', ''), 'subnet': u_data.get('subnet') or '192.168.0',
        'tarifa_kwh': u_data.get('tarifa_kwh') or 0.0,
        'miner_user': u_data.get('miner_user') or 'root',
        'miner_pass': u_data.get('miner_pass') or '',
        'tg_token': u_data.get('tg_token') or '',
        'tg_chat_id': u_data.get('tg_chat_id') or '',
        'totp_activo': totp_activo,
        'dispositivos': lista_dispositivos
    }
    return render_seguro('ajustes.html', active_page='ajustes', user=user_info, msg=msg)

# ==================== 2FA Y DISPOSITIVOS SEGUROS (CLIENTES Y ADMIN) ====================
@panel_bp.route('/api/2fa/configurar', methods=['POST'])
@login_required
def api_2fa_configurar():
    usr_actual = session.get('usuario')
    secret = generar_totp_secret()
    session['temp_totp_secret'] = secret
    uri = f"otpauth://totp/ElMineroGris:{usr_actual}?secret={secret}&issuer=ElMineroGris"
    return jsonify({"status": "ok", "secret": secret, "uri": uri})

@panel_bp.route('/api/2fa/confirmar', methods=['POST'])
@login_required
def api_2fa_confirmar():
    usr_actual = session.get('usuario')
    data = request.get_json(silent=True) or {}
    codigo = str(data.get('codigo') or '').strip().replace(' ', '')
    secret = session.get('temp_totp_secret')
    if not secret:
        return jsonify({"status": "error", "msg": "Sesión de configuración 2FA expirada. Vuelve a iniciar el proceso."}), 400
    if not verificar_totp(secret, codigo):
        return jsonify({"status": "error", "msg": "Código 2FA incorrecto. Verifica que la hora de tu teléfono sea automática e intenta de nuevo."}), 400

    con = get_db(); cur = con.cursor()
    cur.execute("UPDATE usuarios SET totp_secret=%s, totp_activo=1 WHERE LOWER(username)=LOWER(%s)", (secret, usr_actual))
    session.pop('temp_totp_secret', None)
    notificar_alerta_seguridad("2FA Activado", f"El usuario {usr_actual} activó exitosamente 2FA en su cuenta.", _ip_cliente())
    return jsonify({"status": "ok", "msg": "¡Autenticación 2FA activada con éxito en tu cuenta!"})

@panel_bp.route('/api/2fa/ver_clave', methods=['GET', 'POST'])
@login_required
def api_2fa_ver_clave():
    usr_actual = session.get('usuario')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT totp_secret, totp_activo FROM usuarios WHERE LOWER(username)=LOWER(%s)", (usr_actual,))
    row = cur.fetchone() or {}
    secret = (row.get('totp_secret') or '').strip()
    if not secret:
        return jsonify({"status": "error", "msg": "No hay clave 2FA activa configurada."}), 400
    uri = f"otpauth://totp/ElMineroGris:{usr_actual}?secret={secret}&issuer=ElMineroGris"
    return jsonify({"status": "ok", "secret": secret, "uri": uri})

@panel_bp.route('/api/2fa/desactivar', methods=['POST'])
@login_required
def api_2fa_desactivar():
    usr_actual = session.get('usuario')
    data = request.get_json(silent=True) or {}
    codigo = str(data.get('codigo') or '').strip().replace(' ', '')
    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT totp_secret, totp_activo FROM usuarios WHERE LOWER(username)=LOWER(%s)", (usr_actual,))
    row = cur.fetchone()
    if not row or a_int_seguro(row.get('totp_activo'), 0) != 1:
        return jsonify({"status": "error", "msg": "El 2FA no está activo en esta cuenta."}), 400
    if not verificar_totp(row.get('totp_secret'), codigo):
        return jsonify({"status": "error", "msg": "Código 2FA incorrecto para desactivar."}), 400

    cur.execute("UPDATE usuarios SET totp_activo=0, totp_secret='' WHERE LOWER(username)=LOWER(%s)", (usr_actual,))
    notificar_alerta_seguridad("2FA Desactivado", f"El usuario {usr_actual} desactivó la protección 2FA de su cuenta.", _ip_cliente())
    return jsonify({"status": "ok", "msg": "2FA desactivado correctamente."})

@panel_bp.route('/api/dispositivos/eliminar', methods=['POST'])
@login_required
def api_dispositivo_eliminar():
    usr_actual = session.get('usuario')
    data = request.get_json(silent=True) or {}
    disp_id = a_int_seguro(data.get('id'), 0)
    if disp_id <= 0:
        return jsonify({"status": "error", "msg": "ID de dispositivo no válido."}), 400

    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT token_hash, dispositivo FROM dispositivos_confianza WHERE id=%s AND LOWER(username)=LOWER(%s)", (disp_id, usr_actual))
    row = cur.fetchone()
    if not row:
        return jsonify({"status": "error", "msg": "Dispositivo no encontrado o ya eliminado."}), 404

    cur.execute("DELETE FROM dispositivos_confianza WHERE id=%s AND LOWER(username)=LOWER(%s)", (disp_id, usr_actual))
    resp = jsonify({"status": "ok", "msg": f"Dispositivo '{row.get('dispositivo')}' olvidado correctamente."})

    # Si era el dispositivo actual, limpiar la cookie local
    token_actual = request.cookies.get('emg_trusted_device')
    if token_actual:
        hash_act = hashlib.sha256(token_actual.encode('utf-8')).hexdigest()
        if hash_act == row.get('token_hash'):
            host_c = (request.host or '').split(':')[0].lower()
            cookie_domain = '.elminerogris.com' if 'elminerogris.com' in host_c else None
            resp.delete_cookie('emg_trusted_device', path='/', domain=cookie_domain)
            resp.delete_cookie('emg_trusted_device', path='/')
    return resp

@panel_bp.route('/api/dispositivos/revocar_todos', methods=['POST'])
@login_required
def api_dispositivos_revocar_todos():
    usr_actual = session.get('usuario')
    con = get_db(); cur = con.cursor()
    cur.execute("DELETE FROM dispositivos_confianza WHERE LOWER(username)=LOWER(%s)", (usr_actual,))
    resp = jsonify({"status": "ok", "msg": "Todos los dispositivos de confianza fueron olvidados con éxito."})
    host_c = (request.host or '').split(':')[0].lower()
    cookie_domain = '.elminerogris.com' if 'elminerogris.com' in host_c else None
    resp.delete_cookie('emg_trusted_device', path='/', domain=cookie_domain)
    resp.delete_cookie('emg_trusted_device', path='/')
    return resp

@panel_bp.route('/plan')
@login_required
def plan():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT plan, saldo, saldo_bonus, pro_hasta, limite_mineros FROM usuarios WHERE username=%s", (usr_actual,))
    u = cur.fetchone() or {}
    session['plan'] = u.get('plan', session.get('plan', 'Gratis'))
    session['saldo'] = u.get('saldo', session.get('saldo', 0.0))
    return render_seguro(
        'plan.html', active_page='plan', current_plan=session['plan'],
        saldo=session['saldo'], saldo_bonus=round(a_float_seguro(u.get('saldo_bonus'), 0.0), 2)
    )

# --- Autenticación y Recuperación (con Rate Limiting) ---
@panel_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # 1. Paso 2: Verificación de Código 2FA (Google Authenticator / Authy)
        cod_2fa = (request.form.get('codigo_2fa') or '').strip()
        pending_user = session.get('2fa_pending_user')
        if pending_user and cod_2fa:
            con = get_db(); con.autocommit = True
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM usuarios WHERE LOWER(username)=LOWER(%s)", (pending_user,))
            usr = cur.fetchone()
            if not usr or not verificar_totp(usr.get('totp_secret'), cod_2fa):
                notificar_alerta_seguridad("Fallo de Código 2FA", f"Código 2FA incorrecto para usuario: {pending_user}", _ip_cliente())
                return render_seguro('login.html', error='Código 2FA incorrecto o expirado. Intenta nuevamente.', mode='2fa')

            # 2FA aprobado -> Sesión limpia y autenticada
            rec_pref = (request.form.get('recordar_dispositivo') == '1') or (session.get('recordar_dispositivo_pref') == '1')
            session.clear()
            session.permanent = True
            session['usuario'] = usr['username']
            session['plan'] = usr.get('plan', 'Gratis')
            session['saldo'] = usr.get('saldo', 0.0)
            session['last_seen_ts'] = time.time()
            session['admin_last_activity'] = time.time()

            resp = redirect('/resumen')
            if rec_pref:
                raw_token = secrets.token_hex(32)
                tok_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
                ahora_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                exp_dt = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                ua_str = parsear_dispositivo(request.headers.get('User-Agent', ''))
                cur.execute(
                    "INSERT INTO dispositivos_confianza (username, token_hash, dispositivo, ip, fecha_creacion, expira_en) VALUES (%s, %s, %s, %s, %s, %s)",
                    (usr['username'], tok_hash, ua_str, _ip_cliente(), ahora_str, exp_dt)
                )
                fijar_cookie_dispositivo(resp, raw_token)
            return resp

        # 2. Paso 1: Verificación de Usuario y Contraseña
        ip_c = _ip_cliente()
        if verificar_rate_limit(f"login:{ip_c}", max_intentos=8, ventana_seg=300):
            notificar_alerta_seguridad("Rate Limit Superado en Login", f"IP bloqueada temporalmente: {ip_c}", ip_c)
            return render_seguro('login.html', error='Demasiados intentos fallidos. Espera 5 minutos.', mode='login'), 429

        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
        rec_disp = (request.form.get('recordar_dispositivo') == '1')

        if u and verificar_rate_limit(f"login_acc:{u.lower()}", max_intentos=5, ventana_seg=300):
            notificar_alerta_seguridad("Ataque de Fuerza Bruta a Cuenta", f"Múltiples intentos fallidos hacia la cuenta: {u}", ip_c)
            return render_seguro('login.html', error='Demasiados intentos hacia esta cuenta. Espera 5 minutos.', mode='login'), 429

        con = get_db(); con.autocommit = True
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM usuarios WHERE LOWER(username)=LOWER(%s) OR (email != '' AND LOWER(email)=LOWER(%s))", (u, u))
        usr = cur.fetchone()
        if usr and verificar_password(usr.get('password'), p):
            if a_int_seguro(usr.get('suspendido'), 0) == 1:
                return render_seguro('login.html', error='Tu cuenta se encuentra suspendida. Contacta soporte.', mode='login')

            ahora_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            if not es_hash_seguro(usr.get('password')):
                cur.execute("UPDATE usuarios SET password=%s, ultima_conexion=%s WHERE id=%s", (generate_password_hash(p), ahora_str, usr['id']))
            else:
                cur.execute("UPDATE usuarios SET ultima_conexion=%s WHERE id=%s", (ahora_str, usr['id']))

            # Si el usuario tiene 2FA activado, comprobar si este dispositivo ya fue recordado (Dispositivo de Confianza)
            disp_valido = False
            if a_int_seguro(usr.get('totp_activo'), 0) == 1 and usr.get('totp_secret'):
                disp_token = request.cookies.get('emg_trusted_device')
                if disp_token:
                    tok_hash = hashlib.sha256(disp_token.encode('utf-8')).hexdigest()
                    cur.execute(
                        "SELECT id FROM dispositivos_confianza WHERE LOWER(username)=LOWER(%s) AND token_hash=%s AND expira_en > %s LIMIT 1",
                        (usr['username'], tok_hash, ahora_str)
                    )
                    if cur.fetchone():
                        disp_valido = True

                if not disp_valido:
                    session.clear()
                    session['2fa_pending_user'] = usr['username']
                    if rec_disp:
                        session['recordar_dispositivo_pref'] = '1'
                    return render_seguro('login.html', mode='2fa')

            # Anti-Session Fixation: limpiar identificador anterior antes de poblar la sesión
            session.clear()
            session.permanent = True
            session['usuario'] = usr['username']
            session['plan'] = usr.get('plan', 'Gratis')
            session['saldo'] = usr.get('saldo', 0.0)
            session['last_seen_ts'] = time.time()
            session['admin_last_activity'] = time.time()

            resp = redirect('/resumen')
            if rec_disp:
                raw_token = secrets.token_hex(32)
                tok_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
                exp_dt = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
                ua_str = parsear_dispositivo(request.headers.get('User-Agent', ''))
                cur.execute(
                    "INSERT INTO dispositivos_confianza (username, token_hash, dispositivo, ip, fecha_creacion, expira_en) VALUES (%s, %s, %s, %s, %s, %s)",
                    (usr['username'], tok_hash, ua_str, _ip_cliente(), ahora_str, exp_dt)
                )
                fijar_cookie_dispositivo(resp, raw_token)
            return resp

        if u.lower() in ('admin', 'administrator', 'root', 'soporte'):
            notificar_alerta_seguridad("Intento Fallido de Acceso Admin", f"Contraseña inválida intentada para usuario: {u}", ip_c)

        return render_seguro('login.html', error='Credenciales incorrectas', mode='login')
    return render_seguro('login.html', mode='login')

@panel_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 1. Anti-Bot Honeypot (campo invisible para humanos)
        if request.form.get('b_website'):
            notificar_alerta_seguridad("Bot detectado en registro", f"Trampa honeypot activada desde IP: {_ip_cliente()}", _ip_cliente())
            return render_seguro('login.html', error='Solicitud inválida.', mode='register'), 400

        if verificar_rate_limit(f"register:{_ip_cliente()}", max_intentos=5, ventana_seg=600):
            return render_seguro('login.html', error='Demasiados registros recientes desde esta IP. Intenta más tarde.', mode='register'), 429

        u = (request.form.get('username') or '').strip()
        em = (request.form.get('email') or '').strip().lower()
        p = request.form.get('password') or ''
        if not u or not p or not em:
            return render_seguro('login.html', error='Completa usuario, correo electrónico y contraseña.', mode='register')
        if len(u) < 3 or len(u) > 30 or ' ' in u:
            return render_seguro('login.html', error='El usuario debe tener entre 3 y 30 caracteres sin espacios.', mode='register')
        RESERVADOS = {'admin', 'administrator', 'root', 'soporte', 'system', 'elminerogris', 'moderador', 'staff', 'owner', 'miner'}
        if u.lower() in RESERVADOS:
            return render_seguro('login.html', error='Nombre de usuario reservado o no permitido.', mode='register')
        if len(p) < 6:
            return render_seguro('login.html', error='La contraseña debe tener al menos 6 caracteres.', mode='register')
        if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', em) or len(em) > 120:
            return render_seguro('login.html', error='Ingresa un correo electrónico válido.', mode='register')

        try:
            con = get_db(); con.autocommit = True
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT id FROM usuarios WHERE LOWER(username)=LOWER(%s)", (u,))
            if cur.fetchone():
                return render_seguro('login.html', error='El nombre de usuario ya está en uso.', mode='register')

            cur.execute("SELECT id FROM usuarios WHERE LOWER(email)=LOWER(%s)", (em,))
            if cur.fetchone():
                return render_seguro('login.html', error='Este correo electrónico ya está registrado en otra cuenta.', mode='register')

            cur.execute("""
                INSERT INTO usuarios (username, email, fecha_registro, password, plan, saldo, api_key, email_verificado, email_token, email_token_exp)
                VALUES (%s, %s, %s, %s, 'Gratis', 0.0, %s, 1, '', '')
            """, (u, em, hoy_str, generate_password_hash(p), generar_api_key()))

            return render_seguro('login.html', success='Cuenta creada exitosamente (Plan Gratis: hasta 3 mineros). Ahora inicia sesión.', mode='login')
        except Exception as e:
            return render_seguro('login.html', error='Ocurrió un error al crear la cuenta. Intenta de nuevo.', mode='register')
    return render_seguro('login.html', mode='register')

@panel_bp.route('/verificar_email', methods=['GET', 'POST'])
def verificar_email():
    return redirect('/resumen' if session.get('usuario') else '/login')

@panel_bp.route('/reenviar_codigo_email', methods=['POST'])
def reenviar_codigo_email():
    pending_u = session.get('pending_verify_user') or session.get('usuario')
    if not pending_u:
        return jsonify({"status": "error", "msg": "Sesión inválida o expirada."}), 401

    if verificar_rate_limit(f"resend_otp:{pending_u.lower()}", max_intentos=1, ventana_seg=60):
        return jsonify({"status": "error", "msg": "Espera 60 segundos antes de solicitar otro código."}), 429

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM usuarios WHERE LOWER(username)=LOWER(%s)", (pending_u,))
    usr = cur.fetchone()
    if not usr or not usr.get('email'):
        return jsonify({"status": "error", "msg": "No se encontró el correo del usuario."}), 400

    codigo_otp = f"{secrets.randbelow(900000) + 100000}"
    exp_otp = (datetime.utcnow() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("UPDATE usuarios SET email_token=%s, email_token_exp=%s WHERE id=%s", (codigo_otp, exp_otp, usr['id']))
    enviar_email_codigo(usr['email'], codigo_otp, usr['username'])

    return jsonify({"status": "ok", "msg": f"Nuevo código enviado a {enmascarar_email(usr['email'])}"})

@panel_bp.route('/recuperar', methods=['GET', 'POST'])
def recuperar_password():
    if request.method == 'POST':
        if verificar_rate_limit(f"recuperar:{_ip_cliente()}", max_intentos=6, ventana_seg=600):
            return render_seguro('recuperar.html', paso='solicitar', error='Demasiados intentos. Espera 10 minutos.'), 429

        paso = (request.form.get('paso') or 'solicitar').strip()
        u_in = (request.form.get('username') or '').strip()
        if not u_in:
            return render_seguro('recuperar.html', paso='solicitar', error='Ingresa tu usuario o correo electrónico.')

        con = get_db(); con.autocommit = True
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, username, tg_token, tg_chat_id FROM usuarios WHERE LOWER(username)=LOWER(%s) OR (email != '' AND LOWER(email)=LOWER(%s))", (u_in, u_in))
        usr = cur.fetchone()

        if paso == 'solicitar':
            if not usr or not usr.get('tg_token') or not usr.get('tg_chat_id'):
                return render_seguro(
                    'recuperar.html', paso='solicitar',
                    error='No encontramos una cuenta con Telegram vinculado. Si no vinculaste Telegram, contacta a soporte para restablecer tu acceso.'
                )
            codigo = f"{secrets.randbelow(900000) + 100000}"
            clave_u = usr['username'].lower()
            with _rate_lock:
                _reset_codes[clave_u] = {"code": codigo, "exp": time.time() + 600, "attempts": 0}
            ok = enviar_telegram(
                usr['tg_token'], usr['tg_chat_id'],
                f"🔑 <b>El Minero Gris — Código de Recuperación</b>\nUsuario: <b>{usr['username']}</b>\nCódigo: <code>{codigo}</code>\nVálido por 10 minutos."
            )
            if not ok:
                return render_seguro('recuperar.html', paso='solicitar', error='No se pudo enviar el código a tu Telegram. Verifica tu bot o contacta soporte.')
            return render_seguro(
                'recuperar.html', paso='verificar', username=usr['username'],
                success='Código enviado a tu Telegram. Ingrésalo abajo junto con tu nueva contraseña.'
            )

        elif paso == 'verificar':
            codigo_in = (request.form.get('codigo') or '').strip()
            p1 = request.form.get('nueva_pass') or ''
            p2 = request.form.get('confirm_pass') or ''
            if not usr:
                return render_seguro('recuperar.html', paso='solicitar', error='Usuario no encontrado.')
            if len(p1) < 6 or p1 != p2:
                return render_seguro('recuperar.html', paso='verificar', username=usr['username'], error='Las contraseñas no coinciden o tienen menos de 6 caracteres.')

            clave_u = usr['username'].lower()
            with _rate_lock:
                rec = _reset_codes.get(clave_u)
                if not rec or time.time() > rec['exp']:
                    _reset_codes.pop(clave_u, None)
                    return render_seguro('recuperar.html', paso='solicitar', error='El código expiró. Solicita uno nuevo.')
                rec['attempts'] += 1
                if rec['attempts'] > 5:
                    _reset_codes.pop(clave_u, None)
                    return render_seguro('recuperar.html', paso='solicitar', error='Demasiados intentos incorrectos. Solicita un código nuevo.')
                if not secrets.compare_digest(rec['code'], codigo_in):
                    return render_seguro('recuperar.html', paso='verificar', username=usr['username'], error='Código incorrecto. Revisa tu Telegram.')
                _reset_codes.pop(clave_u, None)

            cur.execute("UPDATE usuarios SET password=%s WHERE id=%s", (generate_password_hash(p1), usr['id']))
            return render_seguro('login.html', success='Contraseña actualizada correctamente. Ya puedes iniciar sesión.', mode='login')

    return render_seguro('recuperar.html', paso='solicitar')

@panel_bp.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# --- Comandos, Logs, Exportación y Live ---
@panel_bp.route('/guardar_nombre', methods=['POST'])
@login_required
def guardar_nombre():
    usr_actual = session.get('usuario', 'Admin')
    ip = str(request.form.get('ip') or '').strip()
    raw_nombre = request.form.get('nombre')
    nombre = sanitizar_nombre_minero(raw_nombre, ip_defecto=ip) if raw_nombre is not None else ''
    if ip and nombre:
        con = get_db(); con.autocommit = True; cur = con.cursor()
        cur.execute("INSERT INTO nombres_mineros (owner, ip, nombre) VALUES (%s, %s, %s) ON CONFLICT (owner, ip) DO UPDATE SET nombre=EXCLUDED.nombre", (usr_actual, ip, nombre))
    return redirect(f'/minero/{ip}')

@panel_bp.route('/cmd_bulk', methods=['POST'])
@login_required
def cmd_bulk():
    if not _validar_origen_csrf():
        return jsonify({"status": "error", "msg": "Origen no permitido (CSRF)"}), 403
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
            despachar_comando(cur, usr_actual, item['ip'], item['comando'], ahora)
    elif accion and ips:
        for ip in ips:
            despachar_comando(cur, usr_actual, ip, accion, ahora)
    return jsonify({"status": "ok"})

@panel_bp.route('/cmd/<ip>/<accion>', methods=['GET', 'POST'])
@login_required
def comando_minero(ip, accion):
    if not _validar_origen_csrf():
        return jsonify({"status": "error", "msg": "Origen no permitido (CSRF)"}), 403

    usr_actual = session.get('usuario', 'Admin')

    # Mitigación IDOR: Verificar que el minero pertenezca al usuario en sesión
    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("SELECT id FROM mineros_activos WHERE owner=%s AND ip=%s LIMIT 1", (usr_actual, ip))
    if not cur.fetchone() and not es_super_admin():
        return jsonify({"status": "error", "msg": "Minero no autorizado"}), 403

    cmd_str = ""
    if accion in ['restart', 'reset', 'sleep', 'normal', 'blink', 'scan', 'get_log', 'clog', 'hlog', 'dlog'] or accion.startswith('profile_'):
        cmd_str = accion.replace('_', '|') if accion.startswith('profile_') else accion
    elif accion == 'set_network':
        host = str(request.args.get('host', 'Antminer')).replace('|', '').strip()
        proto = str(request.args.get('proto', '2')).replace('|', '').strip()
        nip = str(request.args.get('nip', ip)).replace('|', '').strip()
        mask = str(request.args.get('mask', '255.255.255.0')).replace('|', '').strip()
        gw = str(request.args.get('gw', '192.168.0.1')).replace('|', '').strip()
        dns = str(request.args.get('dns', '8.8.8.8')).replace('|', '').strip()
        cmd_str = f"set_network|{host}|{proto}|{nip}|{mask}|{gw}|{dns}"
    elif accion == 'guardar_pools':
        u1 = str(request.args.get('u1', '')).replace('|', '').strip()
        w1 = str(request.args.get('w1', '')).replace('|', '').strip()
        p1 = str(request.args.get('p1', '123')).replace('|', '').strip()
        u2 = str(request.args.get('u2', '')).replace('|', '').strip()
        w2 = str(request.args.get('w2', '')).replace('|', '').strip()
        p2 = str(request.args.get('p2', '123')).replace('|', '').strip()
        u3 = str(request.args.get('u3', '')).replace('|', '').strip()
        w3 = str(request.args.get('w3', '')).replace('|', '').strip()
        p3 = str(request.args.get('p3', '123')).replace('|', '').strip()
        cmd_str = f"guardar_pools|{u1}|{w1}|{p1}|{u2}|{w2}|{p2}|{u3}|{w3}|{p3}"

    if cmd_str:
        despachar_comando(cur, usr_actual, ip, cmd_str)
    return "OK"

@panel_bp.route('/exportar_csv')
@login_required
def exportar_csv():
    usr_actual = session.get('usuario', 'Admin')
    plan_actual = session.get('plan', 'Gratis')
    if not es_cuenta_pro(usr_actual, plan_actual) and not es_super_admin():
        return redirect('/plan')

    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT ip, modelo, algoritmo, moneda, hashrate, temp_max, consumo_w, estado, ultima_act FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['IP', 'Modelo', 'Algoritmo', 'Moneda', 'Hashrate', 'Temp Max (C)', 'Consumo (W)', 'Estado'])
    for f in filas:
        online = calcular_segundos_desconectado(f.get('ultima_act')) < 300
        hr_f = a_float_seguro(f.get('hashrate'), 0.0) if online else 0.0
        temp_f = a_int_seguro(f.get('temp_max'), 0) if online else 0
        cons_f = round(a_float_seguro(f.get('consumo_w'), 0.0), 0) if (online and hr_f > 0) else 0
        estado_f = (f.get('estado') or 'En línea') if online else 'Sin conexión'
        writer.writerow([f['ip'], f['modelo'], f.get('algoritmo', 'SHA-256'), f.get('moneda', 'BTC'), hr_f, temp_f, cons_f, estado_f])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=Reporte_El_Minero_Gris.csv"})

@panel_bp.route('/exportar_reporte_mensual')
@login_required
def exportar_reporte_mensual():
    usr_actual = session.get('usuario', 'Admin')
    plan_actual = session.get('plan', 'Gratis')
    if not es_cuenta_pro(usr_actual, plan_actual) and not es_super_admin():
        return redirect('/plan')

    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT tarifa_kwh, comision_pool FROM usuarios WHERE username=%s", (usr_actual,))
    u_row = cur.fetchone() or {}
    tarifa = max(0.0, a_float_seguro(u_row.get('tarifa_kwh'), 0.0))
    comision_pool = min(100.0, max(0.0, a_float_seguro(u_row.get('comision_pool'), 0.0)))

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    datos_red = obtener_datos_red()
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow([
        'IP', 'Nombre', 'Modelo', 'Estado', 'Hashrate', 'Unidad',
        'Consumo (kW)', 'kWh/Mes Est.', f'Costo Luz/Mes (USD @ {tarifa}/kWh)',
        'Ingreso BTC/Mes Est. (USD)', 'Ganancia Neta/Mes Est. (USD)'
    ])

    tot_kw = 0.0
    tot_kwh_mes = 0.0
    tot_costo_mes = 0.0
    tot_ing_mes = 0.0
    tot_neto_mes = 0.0

    for m in filas:
        ip = m['ip']
        nom = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
        mod = m.get('modelo') or 'Antminer'
        algo = m.get('algoritmo') or 'SHA-256'
        unid = m.get('unidad') or 'TH/s'
        online = calcular_segundos_desconectado(m.get('ultima_act')) < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if online else 0.0
        esta_minando = online and hr > 0
        cons_w = a_float_seguro(m.get('consumo_w'), 0.0) if esta_minando else 0.0
        kw = round(cons_w / 1000.0, 2)
        kwh_mes = round(kw * 24.0 * 30.0, 1)

        es_btc = (unid == 'TH/s' and algo == 'SHA-256')
        if esta_minando and es_btc and datos_red['dificultad_t'] > 0:
            _, ing_dia, _, _ = _calcular_rentabilidad_btc_dia(hr, kw * 1000.0, datos_red, tarifa, comision_pool)
            costo_mes = round(kwh_mes * tarifa, 2)
            ing_mes = round(ing_dia * 30.0, 2)
            neto_mes = round(ing_mes - costo_mes, 2)
        elif esta_minando:
            costo_mes = round(kwh_mes * tarifa, 2)
            ing_mes = 0.0
            neto_mes = 0.0
        else:
            costo_mes = 0.0
            ing_mes = 0.0
            neto_mes = 0.0

        tot_kw += kw
        tot_kwh_mes += kwh_mes
        tot_costo_mes += costo_mes
        tot_ing_mes += ing_mes
        tot_neto_mes += neto_mes

        writer.writerow([
            ip, nom, mod, 'En linea' if online else 'Offline',
            hr, unid, kw, kwh_mes, costo_mes, ing_mes, neto_mes
        ])

    writer.writerow([])
    writer.writerow([
        'TOTALES', f'{len(filas)} equipos', '', '', '', '',
        round(tot_kw, 2), round(tot_kwh_mes, 1), round(tot_costo_mes, 2),
        round(tot_ing_mes, 2), round(tot_neto_mes, 2)
    ])

    mes_str = datetime.utcnow().strftime("%Y-%m")
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment;filename=Reporte_Mensual_ElMineroGris_{mes_str}.csv"}
    )

@panel_bp.route('/api/ver_log/<ip>')
@login_required
def ver_log(ip):
    usr_actual = session.get('usuario', 'Admin')
    con = get_db(); cur = con.cursor()
    cur.execute("SELECT contenido FROM logs_mineros WHERE owner=%s AND ip=%s", (usr_actual, ip))
    log = cur.fetchone()
    return log[0] if log else "Esperando respuesta..."

@panel_bp.route('/api/live/minero/<ip>')
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
        t_raw = a_int_seguro(h.get('temp_max'), 0)
        t_amb = a_int_seguro(h.get('temp_amb'), 0)
        puntos.append({
            "ts": ts,
            "hr": a_float_seguro(h.get('hashrate'), 0.0),
            "temp": t_raw if 0 < t_raw < 150 else 0,
            "amb": t_amb if 0 < t_amb < 120 else None,
            "err": diff_err,
            "cadenas": cads
        })

    return jsonify({"ok": True, "puntos": puntos})
