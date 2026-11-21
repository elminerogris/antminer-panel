from flask import Blueprint, redirect, request, session, jsonify, Response
import psycopg2.extras, json, time, io, hashlib, zipfile, re, urllib.request, threading
from datetime import datetime, timedelta

from db import get_db
from utils import (
    APK_VERSION_CODE, APK_VERSION_NAME, login_required, generar_api_key,
    verificar_password, a_int_seguro, a_float_seguro, evaluar_plan_usuario,
    clave_granja_minero, calcular_segundos_desconectado, alerta_en_enfriamiento,
    obtener_ubicacion_ip_agente, obtener_clima_coordenadas, ventilador_en_cero,
    enviar_telegram, obtener_codigo_agente_configurado
)

agente_bp = Blueprint('agente_bp', __name__)
ultima_limpieza_bd = 0
_cache_apk_version = {"ts": 0, "data": None}
_ag_rate_lock = threading.Lock()
_ag_rate_buckets = {}

def _verificar_rate_limit_agente(clave, max_intentos=10, ventana_seg=300):
    ahora = time.time()
    with _ag_rate_lock:
        hist = [ts for ts in _ag_rate_buckets.get(clave, []) if ahora - ts < ventana_seg]
        if len(hist) >= max_intentos:
            _ag_rate_buckets[clave] = hist
            return True
        hist.append(ahora)
        _ag_rate_buckets[clave] = hist
        return False

def normalizar_mac(mac_str):
    s = re.sub(r'[^0-9A-Fa-f]', '', str(mac_str or '')).upper()
    if len(s) != 12 or s in ('000000000000', 'FFFFFFFFFFFF'):
        return ""
    return s

def normalizar_sn(sn_str):
    s = str(sn_str or '').strip().upper()
    if not s or s in ('-', 'NONE', 'NULL', 'UNKNOWN', 'N/A'):
        return ""
    if s.startswith('SN-') or s.startswith('WM-'):
        return ""
    if len(s) < 6:
        return ""
    return s

def migrar_ip_en_tablas(cur, owner, ip_vieja, ip_nueva):
    if not ip_vieja or not ip_nueva or ip_vieja == ip_nueva:
        return
    try:
        cur.execute("UPDATE historial_nube SET ip=%s WHERE owner=%s AND ip=%s", (ip_nueva, owner, ip_vieja))
        cur.execute("UPDATE registro_alertas SET ip=%s WHERE owner=%s AND ip=%s", (ip_nueva, owner, ip_vieja))
        cur.execute("SELECT nombre FROM nombres_mineros WHERE owner=%s AND ip=%s", (owner, ip_vieja))
        row_n = cur.fetchone()
        if row_n and row_n.get('nombre'):
            cur.execute(
                "INSERT INTO nombres_mineros (owner, ip, nombre) VALUES (%s, %s, %s) ON CONFLICT(owner, ip) DO UPDATE SET nombre=EXCLUDED.nombre",
                (owner, ip_nueva, row_n['nombre'])
            )
            cur.execute("DELETE FROM nombres_mineros WHERE owner=%s AND ip=%s", (owner, ip_vieja))
        cur.execute("DELETE FROM logs_mineros WHERE owner=%s AND ip=%s", (owner, ip_vieja))
    except Exception as e:
        print(f"Error migrando IP {ip_vieja} -> {ip_nueva}: {e}")

@agente_bp.route('/api/agente/apk_version')
def api_agente_apk_version():
    ahora = time.time()
    if _cache_apk_version["data"] and (ahora - _cache_apk_version["ts"]) < 300:
        return jsonify(_cache_apk_version["data"]), 200

    version_code = APK_VERSION_CODE
    version_name = APK_VERSION_NAME
    apk_url = "https://github.com/elminerogris/el-minero-gris-android/releases/latest/download/ElMineroGris-Agente.apk"
    version_json_url = "https://github.com/elminerogris/el-minero-gris-android/releases/latest/download/version.json"

    try:
        req = urllib.request.Request(
            version_json_url,
            headers={"User-Agent": "ElMineroGris-Server/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                info = json.loads(resp.read().decode('utf-8'))
                if info.get("version_code"):
                    version_code = int(info["version_code"])
                if info.get("version_name"):
                    version_name = str(info["version_name"])
    except Exception:
        pass

    resultado = {
        "version_code": version_code,
        "version_name": version_name,
        "apk_url": apk_url
    }
    _cache_apk_version["ts"] = ahora
    _cache_apk_version["data"] = resultado
    return jsonify(resultado), 200

@agente_bp.route('/api/agente/login', methods=['POST'])
def api_agente_login():
    xff = request.headers.get('X-Forwarded-For', '')
    ip_cli = xff.split(',')[0].strip() if xff else (request.remote_addr or 'unknown')
    if _verificar_rate_limit_agente(f"ag_login:{ip_cli}", max_intentos=10, ventana_seg=300):
        return jsonify({"ok": False, "msg": "Demasiados intentos. Espera 5 minutos."}), 429

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

    ahora_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("UPDATE usuarios SET ultima_conexion=%s WHERE id=%s", (ahora_str, usr['id']))

    api_k = usr.get('api_key')
    if not api_k:
        api_k = generar_api_key()
        cur.execute("UPDATE usuarios SET api_key=%s WHERE id=%s", (api_k, usr['id']))

    return jsonify({
        "ok": True,
        "username": usr['username'],
        "api_key": api_k,
        "subnet": usr.get('subnet') or '192.168.0',
        "miner_user": usr.get('miner_user') or 'root',
        "miner_pass": usr.get('miner_pass') or ''
    }), 200

@agente_bp.route('/api/agente/version')
def api_agente_version():
    try:
        with open('agente.py', 'r', encoding='utf-8') as f:
            version_hash = hashlib.sha256(f.read().encode('utf-8')).hexdigest()[:12]
        return jsonify({"version": version_hash}), 200
    except:
        return jsonify({"version": "error"}), 500

@agente_bp.route('/api/agente/descargar_update')
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

@agente_bp.route('/descargar_agente')
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

@agente_bp.route('/api/recibir_logs', methods=['POST'])
def recibir_logs():
    data = request.get_json(silent=True)
    if not data or 'ip' not in data or 'log' not in data:
        return jsonify({"status": "error", "msg": "Datos incompletos"}), 400
    api_key_recibida = str(data.get('api_key') or '').strip()
    if not api_key_recibida or api_key_recibida == "TU_API_KEY_AQUI":
        return jsonify({"status": "error", "msg": "API Key requerida"}), 401

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username FROM usuarios WHERE api_key=%s", (api_key_recibida,))
    u_row = cur.fetchone()
    if not u_row:
        return jsonify({"status": "error", "msg": "API Key inválida"}), 401

    cur.execute(
        "INSERT INTO logs_mineros (owner, ip, contenido, fecha) VALUES (%s, %s, %s, %s) ON CONFLICT(owner, ip) DO UPDATE SET contenido=EXCLUDED.contenido, fecha=EXCLUDED.fecha",
        (u_row['username'], data['ip'], data['log'], datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    )
    return "OK", 200

@agente_bp.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    global ultima_limpieza_bd
    datos = request.get_json(silent=True)
    if not datos: return jsonify({"status": "error", "msg": "JSON invalido"}), 400

    api_key_recibida = str(datos.get('api_key') or '').strip()
    if not api_key_recibida or api_key_recibida == "TU_API_KEY_AQUI":
        return jsonify({"status": "error", "msg": "API Key requerida. Configura tu API Key en agente.py"}), 401

    agent_id = str(datos.get('agent_id') or 'default').strip()[:40] or 'default'

    xff = request.headers.get('X-Forwarded-For', '')
    ip_publica_agente = xff.split(',')[0].strip() if xff else (request.remote_addr or '')
    geo_granja = obtener_ubicacion_ip_agente(ip_publica_agente)

    lat_agente = a_float_seguro(datos.get('lat') if datos.get('lat') is not None else datos.get('farm_lat'), 0.0)
    lon_agente = a_float_seguro(datos.get('lon') if datos.get('lon') is not None else datos.get('farm_lon'), 0.0)
    if (-90.0 <= lat_agente <= 90.0) and (-180.0 <= lon_agente <= 180.0) and (abs(lat_agente) > 0.001 or abs(lon_agente) > 0.001):
        f_lat = round(lat_agente, 6)
        f_lon = round(lon_agente, 6)
        f_source = str(datos.get('geo_source') or 'GPS Agente').strip()[:30]
    else:
        f_lat = geo_granja['lat']
        f_lon = geo_granja['lon']
        f_source = 'IP Pública'

    f_city = str(datos.get('ciudad') or geo_granja['ciudad'] or 'Santa Cruz de la Sierra').strip()
    f_country = str(datos.get('pais') or geo_granja.get('pais') or 'Bolivia').strip()

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM usuarios WHERE api_key=%s", (api_key_recibida,))
    u_cfg = cur.fetchone()
    if not u_cfg:
        return jsonify({"status": "error", "msg": "API Key inválida"}), 401
    if a_int_seguro(u_cfg.get('suspendido'), 0) == 1:
        return jsonify({"status": "error", "msg": "Cuenta suspendida"}), 403

    owner_user = u_cfg['username']
    es_pro, limite_efectivo, _ = evaluar_plan_usuario(cur, u_cfg)

    ahora_dt = datetime.utcnow()
    ahora = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    ahora_minuto = ahora_dt.strftime("%Y-%m-%d %H:%M:00")
    ahora_ts = time.time()

    # ==================== CREACIÓN / ACTUALIZACIÓN AUTOMÁTICA DE LA GRANJA DEL AGENTE ====================
    dev_info = datos.get('device') if isinstance(datos.get('device'), dict) else {}
    phone_info = datos.get('phone') if isinstance(datos.get('phone'), dict) else {}
    src_dev = dev_info if dev_info else phone_info

    dev_type = str(src_dev.get('device_type') or '').strip()
    run_mode = str(src_dev.get('run_mode') or '').strip()
    if not dev_type:
        if agent_id.startswith('AND-') or agent_id.startswith('TERMUX-'):
            dev_type = 'Celular'
        else:
            dev_type = 'PC / Servidor'
    if not run_mode:
        if agent_id.startswith('AND-'):
            run_mode = 'APK Directo'
        elif agent_id.startswith('TERMUX-'):
            run_mode = 'Termux'
        else:
            run_mode = 'Python PC'

    dev_brand = str(src_dev.get('brand') or '').strip()[:40]
    dev_model = str(src_dev.get('model') or '').strip()[:80]
    dev_local_ip = str(src_dev.get('local_ip') or '').strip()[:45]
    dev_os = str(src_dev.get('os_info') or (f"Android {src_dev.get('android')}" if src_dev.get('android') else '')).strip()[:60]
    batt_pct = a_int_seguro(src_dev.get('battery_pct'), -1)
    has_batt = 1 if (src_dev.get('has_battery') is True or batt_pct >= 0) else 0
    batt_charging = 1 if bool(src_dev.get('charging')) else 0
    batt_temp = a_float_seguro(src_dev.get('battery_temp_c'), 0.0)

    mineros_payload = datos.get('mineros', [])
    subred_detectada = ""
    gw_detectado = ""
    for mp in mineros_payload:
        if not isinstance(mp, dict):
            continue
        ip_p = str(mp.get('ip') or '').strip()
        if '.' in ip_p and not subred_detectada:
            subred_detectada = ".".join(ip_p.split('.')[:3]) + ".x"
        gw_p = str(mp.get('gateway') or '').strip()
        if gw_p and gw_p != ip_p and not gw_detectado:
            gw_detectado = gw_p
        if subred_detectada and gw_detectado:
            break

    if not subred_detectada and '.' in dev_local_ip:
        subred_detectada = ".".join(dev_local_ip.split('.')[:3]) + ".x"
    if not subred_detectada:
        subred_detectada = (u_cfg.get('subnet') or '192.168.0') + ".x"
    if not gw_detectado:
        gw_detectado = subred_detectada.replace('.x', '.1') if subred_detectada.endswith('.x') else '192.168.0.1'

    nombre_defecto_granja = f"Granja {f_city} ({agent_id})"
    tarifa_defecto = a_float_seguro(u_cfg.get('tarifa_kwh'), 0.06)

    try:
        cur.execute("""
            INSERT INTO granjas_sedes (
                owner, farm_id, agent_id, nombre, ciudad, subred, gateway, cap_kw, tarifa_kwh,
                device_type, run_mode, brand, model, local_ip, has_battery, battery_pct, charging,
                battery_temp_c, os_info, ultima_act
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 50.0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (owner, farm_id) DO UPDATE SET
                agent_id=EXCLUDED.agent_id,
                ciudad=CASE WHEN granjas_sedes.ciudad IS NULL OR granjas_sedes.ciudad='' THEN EXCLUDED.ciudad ELSE granjas_sedes.ciudad END,
                subred=EXCLUDED.subred,
                gateway=CASE WHEN EXCLUDED.gateway != '' THEN EXCLUDED.gateway ELSE granjas_sedes.gateway END,
                device_type=EXCLUDED.device_type,
                run_mode=EXCLUDED.run_mode,
                brand=CASE WHEN EXCLUDED.brand != '' THEN EXCLUDED.brand ELSE granjas_sedes.brand END,
                model=CASE WHEN EXCLUDED.model != '' THEN EXCLUDED.model ELSE granjas_sedes.model END,
                local_ip=CASE WHEN EXCLUDED.local_ip != '' THEN EXCLUDED.local_ip ELSE granjas_sedes.local_ip END,
                has_battery=EXCLUDED.has_battery,
                battery_pct=EXCLUDED.battery_pct,
                charging=EXCLUDED.charging,
                battery_temp_c=EXCLUDED.battery_temp_c,
                os_info=CASE WHEN EXCLUDED.os_info != '' THEN EXCLUDED.os_info ELSE granjas_sedes.os_info END,
                ultima_act=EXCLUDED.ultima_act
        """, (
            owner_user, agent_id, agent_id, nombre_defecto_granja, f_city, subred_detectada, gw_detectado,
            tarifa_defecto, dev_type, run_mode, dev_brand, dev_model, dev_local_ip, has_batt,
            batt_pct, batt_charging, batt_temp, dev_os, ahora
        ))
    except Exception as err_gs:
        print(f"Error actualizando granjas_sedes ({agent_id}): {err_gs}")

    cur.execute("""
        SELECT agent_id, farm_id, ip, miner_uid, mac, serial, corte_activo, corte_temp, watchdog_activo,
               wd_fallo_desde, wd_motivo_fallo, wd_ultimo_reinicio, wd_reinicios_seguidos,
               wd_cuarentena, placas_normales, prot_sleep_desde, prot_motivo,
               farm_lat, farm_lon, farm_city, gps_manual, ultima_act
        FROM mineros_activos WHERE owner=%s
    """, (owner_user,))
    rows_exist = cur.fetchall()

    por_mac = {}
    por_sn = {}
    por_ip_agent = {}
    por_ip_solo = {}
    for row in rows_exist:
        r_mac = normalizar_mac(row.get('mac'))
        r_sn = normalizar_sn(row.get('serial'))
        r_ip = row['ip']
        r_ag = str(row.get('agent_id') or 'default')
        if r_mac:
            if r_mac not in por_mac or str(row.get('ultima_act') or '') > str(por_mac[r_mac].get('ultima_act') or ''):
                por_mac[r_mac] = row
        if r_sn:
            if r_sn not in por_sn or str(row.get('ultima_act') or '') > str(por_sn[r_sn].get('ultima_act') or ''):
                por_sn[r_sn] = row
        por_ip_agent[(r_ag, r_ip)] = row
        if r_ip not in por_ip_solo:
            por_ip_solo[r_ip] = row

    total_existentes = len(rows_exist)

    if owner_user.lower() == 'admin':
        cur.execute("""
            SELECT id, ip, comando FROM cola_comandos
            WHERE estado='pendiente'
              AND (LOWER(owner)='admin' OR owner IS NULL)
              AND (agent_id=%s OR agent_id='default' OR agent_id IS NULL)
        """, (agent_id,))
    else:
        cur.execute("""
            SELECT id, ip, comando FROM cola_comandos
            WHERE estado='pendiente'
              AND owner=%s
              AND (agent_id=%s OR agent_id='default' OR agent_id IS NULL)
        """, (owner_user, agent_id))
    pend = cur.fetchall()
    ordenes = [{"ip": c['ip'], "comando": c['comando']} for c in pend]
    for c in pend: cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=%s", (c['id'],))

    umbral_t = a_int_seguro(u_cfg.get('umbral_temp'), 80)
    umbral_h = a_float_seguro(u_cfg.get('umbral_hr'), 50.0)
    alertas_on = bool(a_int_seguro(u_cfg.get('alertas_activas'), 0) == 1)
    tg_tok = (u_cfg.get('tg_token') or '')
    tg_cid = (u_cfg.get('tg_chat_id') or '')

    if has_batt == 1:
        try:
            nombre_dev = f"{dev_brand} {dev_model}".strip() or dev_type
            if 0 <= batt_pct < 20 and batt_charging == 0:
                if not alerta_en_enfriamiento(cur, owner_user, agent_id, 'CELULAR_BATERIA', 1800):
                    msg_bat = f"🔋 Batería baja en {nombre_dev} ({agent_id}): {batt_pct}% y NO está cargando."
                    cur.execute(
                        "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'CELULAR_BATERIA', %s, %s)",
                        (owner_user, agent_id, msg_bat, ahora)
                    )
                    if alertas_on:
                        enviar_telegram(
                            tg_tok, tg_cid,
                            f"🔋 <b>ALERTA: BATERÍA BAJA EN AGENTE ({dev_type.upper()})</b>\nSede: <b>{agent_id}</b> ({nombre_dev})\nBatería: <b>{batt_pct}%</b> (Desconectado del cargador)\nRevisa el cargador o posible corte de energía."
                        )

            if batt_temp >= 45.0:
                if not alerta_en_enfriamiento(cur, owner_user, agent_id, 'CELULAR_TEMP', 1800):
                    msg_pt = f"🌡️ Sobrecalentamiento en {nombre_dev} ({agent_id}): batería a {batt_temp:.1f}°C ({batt_pct}%)."
                    cur.execute(
                        "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'CELULAR_TEMP', %s, %s)",
                        (owner_user, agent_id, msg_pt, ahora)
                    )
                    if alertas_on:
                        enviar_telegram(
                            tg_tok, tg_cid,
                            f"🌡️ <b>ALERTA: DISPOSITIVO AGENTE SOBRECALENTADO</b>\nSede: <b>{agent_id}</b> ({nombre_dev})\nTemperatura batería: <b>{batt_temp:.1f}°C</b>\nBatería: {batt_pct}% ({'Cargando' if batt_charging else 'Sin cargar'})"
                        )
        except Exception as err_ph:
            print(f"Error procesando telemetría agente {agent_id}: {err_ph}")

    wd_activo = bool(a_int_seguro(u_cfg.get('wd_activo'), 0) == 1)
    wd_alcance = (u_cfg.get('wd_alcance') or 'all')
    try: wd_granjas_list = json.loads((u_cfg.get('wd_granjas') or '[]'))
    except: wd_granjas_list = []
    try: wd_mineros_list = json.loads((u_cfg.get('wd_mineros') or '[]'))
    except: wd_mineros_list = []

    wd_zero_hr = bool(a_int_seguro(u_cfg.get('wd_zero_hr'), 0) == 1)
    wd_cold_on = bool(a_int_seguro(u_cfg.get('wd_cold_on'), 0) == 1)
    wd_cold_temp = a_int_seguro(u_cfg.get('wd_cold_temp'), 31)
    wd_amb_cold_on = bool(a_int_seguro(u_cfg.get('wd_amb_cold_on'), 0) == 1)
    wd_amb_cold_temp = a_int_seguro(u_cfg.get('wd_amb_cold_temp'), 18)
    wd_dead_board = bool(a_int_seguro(u_cfg.get('wd_dead_board'), 0) == 1)
    wd_gracia_seg = max(60, a_int_seguro(u_cfg.get('wd_gracia_min'), 5) * 60)
    wd_cooldown_seg = max(300, a_int_seguro(u_cfg.get('wd_cooldown_min'), 30) * 60)
    wd_cuarentena_on = bool(a_int_seguro(u_cfg.get('wd_cuarentena_on'), 0) == 1)
    wd_max_reinicios = max(1, a_int_seguro(u_cfg.get('wd_max_reinicios'), 3))

    prot_activo = bool(a_int_seguro(u_cfg.get('prot_activo'), 0) == 1)
    prot_temp_on = bool(a_int_seguro(u_cfg.get('prot_temp_on'), 0) == 1)
    prot_temp_val = a_int_seguro(u_cfg.get('prot_temp_val'), 85)
    prot_temp_accion = (u_cfg.get('prot_temp_accion') or 'sleep')
    prot_clima_on = bool(a_int_seguro(u_cfg.get('prot_clima_on'), 0) == 1)
    prot_clima_val = a_int_seguro(u_cfg.get('prot_clima_val'), 38)
    prot_clima_accion = (u_cfg.get('prot_clima_accion') or 'sleep')
    prot_net_on = bool(a_int_seguro(u_cfg.get('prot_net_on'), 0) == 1)
    prot_net_rej_pct = a_float_seguro(u_cfg.get('prot_net_rej_pct'), 5.0)
    prot_net_accion = (u_cfg.get('prot_net_accion') or 'sleep')
    prot_fan_on = bool(a_int_seguro(u_cfg.get('prot_fan_on'), 0) == 1)
    prot_fan_accion = (u_cfg.get('prot_fan_accion') or 'sleep')
    prot_wakeup_on = bool(a_int_seguro(u_cfg.get('prot_wakeup_on'), 0) == 1)
    prot_wakeup_seg = max(300, a_int_seguro(u_cfg.get('prot_wakeup_min'), 30) * 60)

    guardados_ok = 0
    omitidos_por_plan = 0

    for m in mineros_payload:
        try:
            ip_m = str(m.get('ip') or '').strip()
            if not ip_m: continue

            mc = str(m.get('mac') or '-').strip()
            sr = str(m.get('serial') or '-').strip()
            mac_norm = normalizar_mac(mc)
            sn_norm = normalizar_sn(sr)

            prev_m = None
            if mac_norm and mac_norm in por_mac:
                prev_m = por_mac[mac_norm]
            elif sn_norm and sn_norm in por_sn:
                prev_m = por_sn[sn_norm]
            elif (agent_id, ip_m) in por_ip_agent:
                prev_m = por_ip_agent[(agent_id, ip_m)]
            elif ip_m in por_ip_solo:
                cand = por_ip_solo[ip_m]
                cand_mac = normalizar_mac(cand.get('mac'))
                cand_sn = normalizar_sn(cand.get('serial'))
                if (not mac_norm or not cand_mac or mac_norm == cand_mac) and (not sn_norm or not cand_sn or sn_norm == cand_sn):
                    prev_m = cand

            if prev_m is None and limite_efectivo > 0 and total_existentes >= limite_efectivo:
                omitidos_por_plan += 1
                continue

            if mac_norm:
                miner_uid = f"MAC:{mac_norm}"
            elif sn_norm:
                miner_uid = f"SN:{sn_norm}"
            else:
                miner_uid = (prev_m.get('miner_uid') if prev_m else '') or f"IP:{agent_id}:{ip_m}"

            farm_id_asignada = (prev_m.get('farm_id') if prev_m and prev_m.get('farm_id') else '') or agent_id

            if prev_m is not None:
                old_ip = prev_m['ip']
                old_ag = str(prev_m.get('agent_id') or 'default')
                if old_ip != ip_m or old_ag != agent_id:
                    cur.execute("DELETE FROM mineros_activos WHERE owner=%s AND agent_id=%s AND ip=%s", (owner_user, agent_id, ip_m))
                    cur.execute(
                        "UPDATE mineros_activos SET ip=%s, agent_id=%s, miner_uid=%s WHERE owner=%s AND agent_id=%s AND ip=%s",
                        (ip_m, agent_id, miner_uid, owner_user, old_ag, old_ip)
                    )
                    if old_ip != ip_m:
                        migrar_ip_en_tablas(cur, owner_user, old_ip, ip_m)
                if mac_norm:
                    cur.execute(
                        "DELETE FROM mineros_activos WHERE owner=%s AND UPPER(REPLACE(mac, ':', ''))=%s AND NOT (agent_id=%s AND ip=%s)",
                        (owner_user, mac_norm, agent_id, ip_m)
                    )
                cur.execute(
                    "DELETE FROM mineros_activos WHERE owner=%s AND ip=%s AND agent_id != %s AND (mac IS NULL OR mac='' OR mac='-' OR UPPER(REPLACE(mac, ':', ''))=%s)",
                    (owner_user, ip_m, agent_id, mac_norm)
                )
            else:
                total_existentes += 1

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

            cur.execute('''INSERT INTO mineros_activos (ip, owner, agent_id, farm_id, miner_uid, modelo, algoritmo, moneda, unidad, hashrate, hashrate_30m, temp_max, temp_amb, consumo_w, estado, ultima_act, errores, pool1, worker1, pass1, pool2, worker2, pass2, pool3, worker3, pass3, mac, serial, firmware, fans, cadenas, uptime, status1, acc1, rej1, status2, acc2, rej2, status3, acc3, rej3, hostname, net_proto, netmask, gateway, dns, farm_lat, farm_lon, farm_city, farm_country, real_lat, real_lon, real_source) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'online', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(owner, agent_id, ip) DO UPDATE SET 
                            farm_id=CASE WHEN mineros_activos.farm_id IS NOT NULL AND mineros_activos.farm_id != '' THEN mineros_activos.farm_id ELSE EXCLUDED.farm_id END,
                            miner_uid=EXCLUDED.miner_uid, modelo=EXCLUDED.modelo, algoritmo=EXCLUDED.algoritmo, moneda=EXCLUDED.moneda, unidad=EXCLUDED.unidad, hashrate=EXCLUDED.hashrate, hashrate_30m=EXCLUDED.hashrate_30m, temp_max=EXCLUDED.temp_max, temp_amb=EXCLUDED.temp_amb, consumo_w=EXCLUDED.consumo_w, estado='online', ultima_act=EXCLUDED.ultima_act, errores=EXCLUDED.errores, pool1=EXCLUDED.pool1, worker1=EXCLUDED.worker1, pass1=EXCLUDED.pass1, pool2=EXCLUDED.pool2, worker2=EXCLUDED.worker2, pass2=EXCLUDED.pass2, pool3=EXCLUDED.pool3, worker3=EXCLUDED.worker3, pass3=EXCLUDED.pass3, mac=CASE WHEN EXCLUDED.mac != '-' AND EXCLUDED.mac != '' THEN EXCLUDED.mac ELSE mineros_activos.mac END, serial=CASE WHEN EXCLUDED.serial != '-' AND EXCLUDED.serial != '' THEN EXCLUDED.serial ELSE mineros_activos.serial END, firmware=EXCLUDED.firmware, fans=EXCLUDED.fans, cadenas=EXCLUDED.cadenas, uptime=EXCLUDED.uptime, status1=EXCLUDED.status1, acc1=EXCLUDED.acc1, rej1=EXCLUDED.rej1, status2=EXCLUDED.status2, acc2=EXCLUDED.acc2, rej2=EXCLUDED.rej2, status3=EXCLUDED.status3, acc3=EXCLUDED.acc3, rej3=EXCLUDED.rej3, hostname=EXCLUDED.hostname, net_proto=EXCLUDED.net_proto, netmask=EXCLUDED.netmask, gateway=EXCLUDED.gateway, dns=EXCLUDED.dns,
                            farm_lat=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_lat ELSE EXCLUDED.farm_lat END,
                            farm_lon=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_lon ELSE EXCLUDED.farm_lon END,
                            farm_city=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_city ELSE EXCLUDED.farm_city END,
                            farm_country=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_country ELSE EXCLUDED.farm_country END,
                            real_lat=EXCLUDED.real_lat,
                            real_lon=EXCLUDED.real_lon,
                            real_source=EXCLUDED.real_source''',
                        (ip_m, owner_user, agent_id, farm_id_asignada, miner_uid, mod_m, algo_m, mon_m, unid, hr_m, hr_30, temp_m, t_amb, cons_m, ahora, err, p1, w1, pa1, p2, w2, pa2, p3, w3, pa3, mc, sr, fw, fs, cadenas_json, up, st1, ac1, rj1, st2, ac2, rj2, st3, ac3, rj3, host, nproto, nmask, ngw, ndns, f_lat, f_lon, f_city, f_country, f_lat, f_lon, f_source))

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

            prev_m = prev_m or {}
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
                cur.execute("UPDATE mineros_activos SET prot_sleep_desde=%s, prot_motivo=%s WHERE owner=%s AND agent_id=%s AND ip=%s",
                            (nuevo_sleep_ts, motivo_proteccion, owner_user, agent_id, ip_m))
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
                        cur.execute("UPDATE mineros_activos SET prot_sleep_desde='', prot_motivo='' WHERE owner=%s AND agent_id=%s AND ip=%s",
                                    (owner_user, agent_id, ip_m))
                        msg_wk = f"🌅 AUTO-DESPERTAR: {mod_m} ({ip_m}) vuelto a NORMAL tras {int(segs_dormido // 60)}m de reposo."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'CORTE_TERMICO', %s, %s)", (owner_user, ip_m, msg_wk, ahora))
                        if alertas_on:
                            enviar_telegram(tg_tok, tg_cid, f"🌅 <b>AUTO-DESPERTAR TÉRMICO</b>\nMinero: <b>{ip_m}</b> ({mod_m}) vuelto a <b>NORMAL</b> tras enfriar.")

            placas_con_hr = sum(1 for ch in cadenas_list if isinstance(ch, dict) and a_float_seguro(ch.get('hr'), 0.0) > 0.0)
            placas_normales = a_int_seguro(prev_m.get('placas_normales'), 0)
            if hr_m > 0 and placas_con_hr > placas_normales:
                placas_normales = placas_con_hr
                cur.execute("UPDATE mineros_activos SET placas_normales=%s WHERE owner=%s AND agent_id=%s AND ip=%s", (placas_normales, owner_user, agent_id, ip_m))

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
                        WHERE owner=%s AND agent_id=%s AND ip=%s
                    """, (owner_user, agent_id, ip_m))
            elif wd_activo and wd_permitido_minero and aplica_alcance and not corte_disparado and wd_cuarentena == 0:
                if not wd_fallo_desde:
                    cur.execute("UPDATE mineros_activos SET wd_fallo_desde=%s, wd_motivo_fallo=%s WHERE owner=%s AND agent_id=%s AND ip=%s",
                                (ahora, fallo_wd, owner_user, agent_id, ip_m))
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
                                    WHERE owner=%s AND agent_id=%s AND ip=%s
                                """, (ahora, fallo_wd, owner_user, agent_id, ip_m))
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
                                    WHERE owner=%s AND agent_id=%s AND ip=%s
                                """, (ahora, nuevos_reb, fallo_wd, owner_user, agent_id, ip_m))
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
        cur.execute("UPDATE usuarios SET mineros_omitidos=%s WHERE username=%s", (0 if (es_pro and limite_efectivo == 0) else omitidos_por_plan, owner_user))
    except:
        pass

    if ahora_ts - ultima_limpieza_bd > 3600:
        ultima_limpieza_bd = ahora_ts
        try:
            fecha_limite_30d = (ahora_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            fecha_limite_48h = (ahora_dt - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
            fecha_limite_cmd = (ahora_dt - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("DELETE FROM historial_nube WHERE fecha < %s", (fecha_limite_30d,))
            cur.execute("UPDATE historial_nube SET cadenas=NULL WHERE fecha < %s AND cadenas IS NOT NULL", (fecha_limite_48h,))
            cur.execute("DELETE FROM registro_alertas WHERE fecha < %s", (fecha_limite_30d,))
            cur.execute("DELETE FROM cola_comandos WHERE estado='enviado' AND fecha < %s", (fecha_limite_cmd,))
        except Exception as err_clean:
            print(f"Error en limpieza automática 30d: {err_clean}")

    return jsonify({
        "status": "ok",
        "guardados": guardados_ok,
        "omitidos_por_plan": omitidos_por_plan,
        "ordenes": ordenes,
        "miner_user": u_cfg.get('miner_user') or 'root',
        "miner_pass": u_cfg.get('miner_pass') or ''
    }), 200

# ==================== DIAGNÓSTICO DE RED (PING A POOLS) DESDE EL PANEL ====================
@agente_bp.route('/api/panel/sedes', methods=['GET'])
@login_required
def api_panel_sedes():
    owner = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT COALESCE(NULLIF(agent_id, ''), 'default') AS agent_id,
               MAX(farm_city) AS ciudad,
               COUNT(*) AS total
        FROM mineros_activos
        WHERE owner=%s
        GROUP BY COALESCE(NULLIF(agent_id, ''), 'default')
        ORDER BY MAX(ultima_act) DESC
    """, (owner,))
    sedes = cur.fetchall() or []
    return jsonify({"ok": True, "sedes": sedes}), 200

@agente_bp.route('/api/panel/ping_pools', methods=['POST'])
@login_required
def api_panel_ping_pools():
    owner = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    agent_id = str(data.get('agent_id') or 'default').strip()[:40] or 'default'
    clave_log = f"PING-{agent_id}"
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    con = get_db()
    con.autocommit = True
    cur = con.cursor()
    cur.execute("DELETE FROM logs_mineros WHERE owner=%s AND ip=%s", (owner, clave_log))
    cur.execute(
        "INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, %s, 'ping_pools', 'pendiente', %s)",
        (owner, agent_id, clave_log, ahora)
    )
    return jsonify({"ok": True, "agent_id": agent_id, "clave_log": clave_log}), 200

@agente_bp.route('/api/panel/ping_pools_resultado', methods=['GET'])
@login_required
def api_panel_ping_pools_resultado():
    owner = session.get('usuario', 'Admin')
    agent_id = str(request.args.get('agent_id') or 'default').strip()[:40] or 'default'
    clave_log = f"PING-{agent_id}"

    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT contenido, fecha FROM logs_mineros WHERE owner=%s AND ip=%s LIMIT 1",
        (owner, clave_log)
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"ok": False, "pendiente": True}), 200
    return jsonify({"ok": True, "contenido": row['contenido'], "fecha": row['fecha']}), 200
