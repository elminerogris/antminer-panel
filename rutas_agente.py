from flask import Blueprint, redirect, request, session, jsonify, Response
import psycopg2.extras, json, time, io, hashlib, zipfile, re, urllib.request, threading
from datetime import datetime, timedelta

from db import get_db
from tunel import enviar_orden_tunel
from utils import (
    APK_VERSION_CODE, APK_VERSION_NAME, login_required, generar_api_key,
    verificar_password, a_int_seguro, a_float_seguro, evaluar_plan_usuario,
    clave_granja_minero, calcular_segundos_desconectado, alerta_en_enfriamiento,
    obtener_ubicacion_ip_agente, obtener_clima_coordenadas, ventilador_en_cero,
    enviar_telegram, obtener_codigo_agente_configurado, determinar_credenciales_defecto,
    sanitizar_nombre_minero
)

agente_bp = Blueprint('agente_bp', __name__)
ultima_limpieza_bd = 0
_cache_apk_version = {}
_ag_rate_lock = threading.Lock()
_ag_rate_buckets = {}
_col_app_ver_ok = False
_col_pub_ip_ok = False

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
    if len(s) != 12 or s in ('000000000000', 'FFFFFFFFFFFF', '020000000000'):
        return ""
    return s

def formatear_mac(mac_norm):
    if not mac_norm or len(mac_norm) != 12:
        return ""
    return ":".join(mac_norm[i:i+2] for i in range(0, 12, 2))

def normalizar_sn(sn_str):
    s = str(sn_str or '').strip().upper()
    if not s or s in ('-', 'NONE', 'NULL', 'UNKNOWN', 'N/A'):
        return ""
    if s.startswith('SN-') or s.startswith('WM-'):
        return ""
    if len(s) < 6:
        return ""
    return s

def _extraer_nombre_desde_worker(worker_str, ip_str):
    w = str(worker_str or '').strip()
    if w and w not in ('-', 'None', 'null'):
        if '.' in w:
            sufijo = w.split('.')[-1].strip()
            if sufijo and sufijo not in ('-', 'None'):
                return sufijo[:40]
        return w[:40]
    octeto = ip_str.split('.')[-1] if '.' in str(ip_str or '') else str(ip_str or 'ASIC')
    return octeto

def _siguiente_nombre_granja(cur, owner):
    cur.execute("SELECT nombre FROM granjas_sedes WHERE owner=%s", (owner,))
    rows = cur.fetchall() or []
    max_num = len(rows)
    for r in rows:
        nom = str(r.get('nombre') or '').strip()
        m = re.match(r'^granja\s+(\d+)$', nom, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if n > max_num:
                max_num = n
    return f"Granja {max_num + 1:03d}"

def _formatear_nombre_agente(row):
    brand = str(row.get('brand') or '').strip()
    model = str(row.get('model') or '').strip()
    if model and brand and model.lower().startswith(brand.lower()):
        nombre = model
    else:
        nombre = f"{brand} {model}".strip()
    if not nombre:
        nombre = str(row.get('device_type') or '').strip() or str(row.get('agent_id') or 'Agente')
    return nombre

def _formatear_hace_tiempo(segs):
    if segs < 60:
        return "hace unos segundos"
    mins = int(segs // 60)
    if mins < 60:
        return f"hace {mins}m"
    hrs = int(mins // 60)
    if hrs < 24:
        return f"hace {hrs}h"
    dias = int(hrs // 24)
    return f"hace {dias}d"

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
    if isinstance(_cache_apk_version.get("prod"), dict):
        entry = _cache_apk_version["prod"]
        if (ahora - entry.get("ts", 0)) < 300:
            return jsonify(entry.get("data")), 200

    version_code = APK_VERSION_CODE
    version_name = APK_VERSION_NAME
    apk_url = "https://github.com/elminerogris/el-minero-gris-android/releases/download/latest/ElMineroGris.apk"
    version_json_url = "https://github.com/elminerogris/el-minero-gris-android/releases/download/latest/version.json"

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
    _cache_apk_version["prod"] = {"ts": ahora, "data": resultado}
    return jsonify(resultado), 200

@agente_bp.route('/api/agente/mi_sesion')
def api_agente_mi_sesion():
    usr_actual = session.get('usuario')
    if not usr_actual:
        return jsonify({"ok": False, "msg": "No autenticado"}), 401
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM usuarios WHERE LOWER(username)=LOWER(%s)", (usr_actual,))
    usr = cur.fetchone()
    if not usr:
        return jsonify({"ok": False, "msg": "Usuario no encontrado"}), 404
    api_k = usr.get('api_key')
    if not api_k:
        api_k = generar_api_key()
        cur.execute("UPDATE usuarios SET api_key=%s WHERE id=%s", (api_k, usr['id']))
        con.commit()
    return jsonify({
        "ok": True,
        "username": usr['username'],
        "api_key": api_k,
        "subnet": usr.get('subnet') or '192.168.0',
        "miner_user": usr.get('miner_user') or 'root',
        "miner_pass": usr.get('miner_pass') or ''
    }), 200

@agente_bp.route('/api/agente/mis_mineros')
def api_agente_mis_mineros():
    usr_actual = session.get('usuario')
    api_k = (request.args.get('api_key') or request.headers.get('X-API-KEY') or '').strip()
    owner = None
    if usr_actual:
        owner = usr_actual
    elif api_k:
        con = get_db()
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT username FROM usuarios WHERE api_key=%s", (api_k,))
        u = cur.fetchone()
        if u:
            owner = u['username']
    if not owner:
        return jsonify({"ok": False, "msg": "No autorizado", "mineros": []}), 401

    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT ip, modelo, algoritmo, moneda, unidad, hashrate, temp_max, consumo_w, estado, ultima_act, fans, cadenas, worker1, serial FROM mineros_activos WHERE owner=%s ORDER BY ip", (owner,))
    rows = cur.fetchall()
    mineros = []
    for r in rows:
        mineros.append({
            "ip": r['ip'],
            "modelo": r['modelo'] or 'Antminer',
            "algoritmo": r.get('algoritmo') or 'SHA-256d',
            "moneda": r.get('moneda') or 'BTC',
            "unidad": r.get('unidad') or 'TH/s',
            "hashrate": float(r['hashrate'] or 0.0),
            "temp_max": int(r['temp_max'] or 0),
            "consumo_w": float(r['consumo_w'] or 0.0),
            "estado": r['estado'] or 'En línea',
            "fans": r.get('fans') or '',
            "worker": r.get('worker1') or '',
            "serial": r.get('serial') or '',
            "ultima_act": str(r.get('ultima_act') or '')
        })
    return jsonify({"ok": True, "count": len(mineros), "mineros": mineros}), 200

@agente_bp.route('/api/agente/login', methods=['POST'])
def api_agente_login():
    cf = request.headers.get('CF-Connecting-IP', '')
    xff = request.headers.get('X-Forwarded-For', '')
    ip_cli = cf.strip() if cf else (xff.split(',')[0].strip() if xff else (request.remote_addr or 'unknown'))
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

@agente_bp.route('/instalar_pi')
@agente_bp.route('/instalar.sh')
@agente_bp.route('/install.sh')
def script_instalador_pi():
    api_k = (request.args.get('key') or request.args.get('api_key') or '').strip()
    if not api_k and 'usuario' in session:
        usr_actual = session.get('usuario')
        try:
            con = get_db()
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT api_key FROM usuarios WHERE username=%s", (usr_actual,))
            row = cur.fetchone()
            if row and row.get('api_key'):
                api_k = row.get('api_key')
        except Exception:
            pass

    base_url = request.url_root.rstrip('/')
    if base_url.startswith('http://') and not ('localhost' in base_url or '127.0.0.1' in base_url):
        base_url = 'https://' + base_url[7:]

    script_sh = f"""#!/bin/bash
# ====================================================================
#   ⛏️  EL MINERO GRIS - INSTALADOR OFICIAL PARA RASPBERRY PI Y LINUX
# ====================================================================
#   Monitoreo y control 24/7 sin abrir puertos ni configurar VPNs.
# ====================================================================

set -e

VERDE='\\033[0;32m'
AZUL='\\033[0;34m'
AMARILLO='\\033[1;33m'
ROJO='\\033[0;31m'
NC='\\033[0m'

echo ""
echo -e "${{AZUL}}==========================================================${{NC}}"
echo -e "${{VERDE}}   ⛏️   EL MINERO GRIS - AGENTE RASPBERRY PI / LINUX 24/7  ${{NC}}"
echo -e "${{AZUL}}==========================================================${{NC}}"
echo ""

if [ "$EUID" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi

API_KEY="{api_k}"
if [ -z "$API_KEY" ] || [ "$API_KEY" = "TU_API_KEY_AQUI" ]; then
    echo -e "${{AMARILLO}}🔑 No se detectó API Key en el comando.${{NC}}"
    read -p "👉 Ingresa tu API Key de El Minero Gris: " API_KEY
    API_KEY=$(echo "$API_KEY" | tr -d '[:space:]')
fi

if [ -z "$API_KEY" ]; then
    echo -e "${{ROJO}}❌ Error: Se requiere una API Key para conectar con tu cuenta.${{NC}}"
    echo "Puedes verla ingresando en tu panel: {base_url}/resumen"
    exit 1
fi

echo -e "[1/4] 📦 Instalando dependencias necesarias (Python 3, requests, curl)..."
$SUDO apt-get update -y >/dev/null 2>&1 || true
$SUDO apt-get install -y python3 python3-requests curl >/dev/null 2>&1 || {{
    $SUDO apt-get install -y python3 python3-pip curl >/dev/null 2>&1 || true
    python3 -m pip install requests --break-system-packages >/dev/null 2>&1 || python3 -m pip install requests >/dev/null 2>&1 || true
}}

PYTHON_BIN=$(which python3 || echo "/usr/bin/python3")

echo -e "[2/4] 📥 Descargando agente configurado para tu cuenta..."
INSTALL_DIR="/opt/elminerogris"
$SUDO mkdir -p "$INSTALL_DIR"

$SUDO curl -sSL "{base_url}/api/agente/descargar_update?api_key=$API_KEY" -o "$INSTALL_DIR/agente.py"

if [ ! -s "$INSTALL_DIR/agente.py" ]; then
    echo -e "${{ROJO}}❌ Error al descargar el agente. Verifica tu conexión a internet o tu API Key.${{NC}}"
    exit 1
fi

echo -e "[3/4] ⚙️  Configurando servicio automático 24/7 (systemd)..."
cat << EOF | $SUDO tee /etc/systemd/system/elminerogris.service > /dev/null
[Unit]
Description=El Minero Gris - Agente de Mineria 24/7
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON_BIN $INSTALL_DIR/agente.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo -e "[4/4] 🚀 Iniciando servicio El Minero Gris..."
$SUDO systemctl daemon-reload
$SUDO systemctl enable elminerogris.service
$SUDO systemctl restart elminerogris.service

echo ""
echo -e "${{VERDE}}==========================================================${{NC}}"
echo -e "${{VERDE}}  ✅ ¡INSTALACIÓN COMPLETADA CON ÉXITO!${{NC}}"
echo -e "${{VERDE}}==========================================================${{NC}}"
echo -e "  • Tu Raspberry Pi ya está buscando mineros en tu red local."
echo -e "  • El agente corre 24/7 en segundo plano."
echo -e "  • Si la Raspberry Pi se reinicia o se corta la luz, arrancará sola."
echo ""
echo -e "  Comandos útiles de mantenimiento:"
echo -e "    ${{AZUL}}Ver estado:${{NC}}   sudo systemctl status elminerogris"
echo -e "    ${{AZUL}}Ver en vivo:${{NC}}  sudo journalctl -u elminerogris -f"
echo -e "    ${{AZUL}}Reiniciar:${{NC}}    sudo systemctl restart elminerogris"
echo ""
echo -e "  👉 ${{VERDE}}Entra a tu panel para ver tus mineros aparecer en vivo:${{NC}}"
echo -e "     ${{AZUL}}{base_url}/resumen${{NC}}"
echo -e "${{VERDE}}==========================================================${{NC}}"
echo ""
"""
    return Response(script_sh, mimetype="text/x-shellscript")

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

# ==================== APIs DE ZERO-TOUCH Y CAMBIO MASIVO DE CLAVE ====================
@agente_bp.route('/api/granjas/zerotouch', methods=['POST'])
@login_required
def api_guardar_zerotouch():
    owner = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    farm_id = str(data.get('farm_id') or '').strip()
    if not farm_id:
        return jsonify({"status": "error", "msg": "farm_id requerido"}), 400

    zt_activo = 1 if a_int_seguro(data.get('zt_activo'), 0) == 1 else 0
    zt_pool1 = str(data.get('zt_pool1') or '').strip()
    zt_worker_prefix = str(data.get('zt_worker_prefix') or '').strip()
    zt_pass1 = str(data.get('zt_pass1') or '123').strip()
    zt_pool2 = str(data.get('zt_pool2') or '').strip()
    zt_pool3 = str(data.get('zt_pool3') or '').strip()

    con = get_db(); con.autocommit = True; cur = con.cursor()
    cur.execute("""
        UPDATE granjas_sedes
        SET zt_activo=%s, zt_pool1=%s, zt_worker_prefix=%s, zt_pass1=%s, zt_pool2=%s, zt_pool3=%s
        WHERE owner=%s AND farm_id=%s
    """, (zt_activo, zt_pool1, zt_worker_prefix, zt_pass1, zt_pool2, zt_pool3, owner, farm_id))
    return jsonify({"status": "ok", "zt_activo": zt_activo})

@agente_bp.route('/api/mineros/cambiar_clave_masiva', methods=['POST'])
@login_required
def api_cambiar_clave_masiva():
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return jsonify({"status": "error", "msg": "Permiso denegado para el rol Técnico."}), 403

    owner = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    ips = data.get('ips', [])
    old_pass = str(data.get('old_pass') or 'root').strip()
    new_pass = str(data.get('new_pass') or '').strip()

    if not ips or not isinstance(ips, list):
        return jsonify({"status": "error", "msg": "Selecciona al menos un minero"}), 400
    if len(new_pass) < 4 or '|' in new_pass:
        return jsonify({"status": "error", "msg": "La nueva contraseña debe tener al menos 4 caracteres y no usar '|'"}), 400

    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cmd_str = f"change_pass|{old_pass}|{new_pass}"
    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    enviados = 0
    for ip in ips:
        ip_c = str(ip or '').strip()
        if not ip_c:
            continue
        cur.execute("SELECT agent_id FROM mineros_activos WHERE owner=%s AND ip=%s LIMIT 1", (owner, ip_c))
        row_ag = cur.fetchone()
        ag_id = str((row_ag or {}).get('agent_id') or 'default').strip()
        ok_ws = enviar_orden_tunel(owner, ip_c, cmd_str, agent_id=ag_id)
        estado = 'enviado' if ok_ws else 'pendiente'
        cur.execute(
            "INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, %s, %s, %s, %s)",
            (owner, ag_id, ip_c, cmd_str, estado, ahora)
        )
        enviados += 1

    cur.execute("UPDATE usuarios SET miner_pass=%s WHERE username=%s", (new_pass, owner))
    return jsonify({"status": "ok", "enviados": enviados})

@agente_bp.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    global ultima_limpieza_bd, _col_app_ver_ok, _col_pub_ip_ok
    datos = request.get_json(silent=True)
    if not datos: return jsonify({"status": "error", "msg": "JSON invalido"}), 400

    api_key_recibida = str(datos.get('api_key') or '').strip()
    if not api_key_recibida or api_key_recibida == "TU_API_KEY_AQUI":
        return jsonify({"status": "error", "msg": "API Key requerida. Configura tu API Key en agente.py"}), 401

    agent_id = str(datos.get('agent_id') or 'default').strip()[:40] or 'default'

    cf = request.headers.get('CF-Connecting-IP', '')
    xff = request.headers.get('X-Forwarded-For', '')
    ip_publica_agente = cf.strip() if cf else (xff.split(',')[0].strip() if xff else (request.remote_addr or ''))
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

    if not _col_app_ver_ok:
        try:
            cur.execute("ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS app_version TEXT DEFAULT ''")
            _col_app_ver_ok = True
        except Exception:
            pass
    if not _col_pub_ip_ok:
        try:
            cur.execute("ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS public_ip TEXT DEFAULT ''")
            _col_pub_ip_ok = True
        except Exception:
            pass

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

    dev_info = datos.get('device') if isinstance(datos.get('device'), dict) else {}
    phone_info = datos.get('phone') if isinstance(datos.get('phone'), dict) else {}
    src_dev = dev_info if dev_info else phone_info

    dev_type = str(src_dev.get('device_type') or '').strip()
    run_mode = str(src_dev.get('run_mode') or '').strip()
    if not dev_type:
        dev_type = 'Celular' if (agent_id.startswith('AND-') or agent_id.startswith('TERMUX-')) else 'PC / Servidor'
    if not run_mode:
        run_mode = 'APK Directo' if agent_id.startswith('AND-') else ('Termux' if agent_id.startswith('TERMUX-') else 'Python PC')

    dev_brand = str(src_dev.get('brand') or '').strip()[:40]
    dev_model = str(src_dev.get('model') or '').strip()[:80]
    dev_local_ip = str(src_dev.get('local_ip') or '').strip()[:45]
    dev_os = str(src_dev.get('os_info') or (f"Android {src_dev.get('android')}" if src_dev.get('android') else '')).strip()[:60]
    dev_app_ver = str(
        datos.get('app_version')
        or src_dev.get('app_version')
        or src_dev.get('version')
        or ''
    ).strip()[:30]
    batt_pct = a_int_seguro(src_dev.get('battery_pct'), -1)
    has_batt = 1 if (src_dev.get('has_battery') is True or batt_pct >= 0) else 0
    batt_charging = 1 if bool(src_dev.get('charging')) else 0
    batt_temp = a_float_seguro(src_dev.get('battery_temp_c'), 0.0)

    router_mac_raw = (
        datos.get('router_mac')
        or src_dev.get('router_mac')
        or src_dev.get('bssid')
        or ''
    )
    router_mac_norm = normalizar_mac(router_mac_raw)
    router_mac_fmt = formatear_mac(router_mac_norm)

    mineros_payload = datos.get('mineros', [])
    subred_detectada = ""
    gw_detectado = ""
    macs_payload = []
    for mp in mineros_payload:
        if not isinstance(mp, dict):
            continue
        ip_p = str(mp.get('ip') or '').strip()
        if '.' in ip_p and not subred_detectada:
            subred_detectada = ".".join(ip_p.split('.')[:3]) + ".x"
        gw_p = str(mp.get('gateway') or '').strip()
        if gw_p and gw_p != ip_p and not gw_detectado:
            gw_detectado = gw_p
        m_norm_p = normalizar_mac(mp.get('mac'))
        if m_norm_p:
            macs_payload.append(m_norm_p)

    if not subred_detectada and '.' in dev_local_ip:
        subred_detectada = ".".join(dev_local_ip.split('.')[:3]) + ".x"
    if not subred_detectada:
        subred_detectada = (u_cfg.get('subnet') or '192.168.0') + ".x"
    if not gw_detectado:
        gw_detectado = str(datos.get('gateway') or src_dev.get('gateway') or '').strip()
    if not gw_detectado:
        gw_detectado = subred_detectada.replace('.x', '.1') if subred_detectada.endswith('.x') else '192.168.0.1'

    # ==================== RESOLUCIÓN ANTI-DUPLICADOS DE GRANJA (10 CELULARES EN 1 ROUTER = 1 GRANJA) ====================
    farm_id_efectivo = None
    try:
        # Capa 1: Coincidencia por MAC del router (cubre 2.4GHz y 5GHz)
        if router_mac_fmt:
            mac_prefijo_5 = router_mac_fmt[:14]
            cur.execute("""
                SELECT farm_id FROM granjas_sedes
                WHERE owner=%s
                  AND (router_mac=%s OR (router_mac LIKE %s AND gateway=%s))
                ORDER BY id ASC LIMIT 1
            """, (owner_user, router_mac_fmt, mac_prefijo_5 + '%', gw_detectado))
            row_rtr = cur.fetchone()
            if row_rtr and row_rtr.get('farm_id'):
                farm_id_efectivo = str(row_rtr['farm_id']).strip()

        # Capa 2: Coincidencia por misma IP pública + misma subred + mismo gateway (si Android ocultó la MAC)
        if not farm_id_efectivo and ip_publica_agente and subred_detectada and gw_detectado:
            cur.execute("""
                SELECT farm_id FROM granjas_sedes
                WHERE owner=%s AND public_ip=%s AND subred=%s AND gateway=%s
                ORDER BY id ASC LIMIT 1
            """, (owner_user, ip_publica_agente, subred_detectada, gw_detectado))
            row_pub = cur.fetchone()
            if row_pub and row_pub.get('farm_id'):
                farm_id_efectivo = str(row_pub['farm_id']).strip()

        # Capa 3: Si algún minero reportado (por MAC) ya pertenece a una granja de este usuario
        if not farm_id_efectivo and macs_payload:
            cur.execute("""
                SELECT farm_id FROM mineros_activos
                WHERE owner=%s AND farm_id != '' AND UPPER(REPLACE(mac, ':', '')) = ANY(%s)
                LIMIT 1
            """, (owner_user, macs_payload))
            row_mm = cur.fetchone()
            if row_mm and row_mm.get('farm_id'):
                farm_id_efectivo = str(row_mm['farm_id']).strip()

        # Capa 4: Si este agente ya tenía su propia granja asignada previamente
        if not farm_id_efectivo:
            cur.execute(
                "SELECT farm_id FROM granjas_sedes WHERE owner=%s AND (farm_id=%s OR agent_id=%s) ORDER BY id ASC LIMIT 1",
                (owner_user, agent_id, agent_id)
            )
            row_ag_exist = cur.fetchone()
            if row_ag_exist and row_ag_exist.get('farm_id'):
                farm_id_efectivo = str(row_ag_exist['farm_id']).strip()

        # Si es realmente una granja nueva: generar ID limpio
        if not farm_id_efectivo:
            farm_id_efectivo = f"RTR-{router_mac_norm[-6:]}" if router_mac_norm else agent_id
    except Exception as err_rtr:
        print(f"Error resolviendo granja ({agent_id}): {err_rtr}")
        farm_id_efectivo = agent_id

    nombre_defecto_granja = _siguiente_nombre_granja(cur, owner_user)
    tarifa_defecto = a_float_seguro(u_cfg.get('tarifa_kwh'), 0.06)

    try:
        cur.execute("""
            INSERT INTO granjas_sedes (
                owner, farm_id, agent_id, nombre, ciudad, subred, gateway, router_mac, public_ip, cap_kw, tarifa_kwh,
                device_type, run_mode, brand, model, local_ip, has_battery, battery_pct, charging,
                battery_temp_c, os_info, app_version, ultima_act
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 50.0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (owner, farm_id) DO UPDATE SET
                agent_id=EXCLUDED.agent_id,
                ciudad=CASE WHEN granjas_sedes.ciudad IS NULL OR granjas_sedes.ciudad='' THEN EXCLUDED.ciudad ELSE granjas_sedes.ciudad END,
                subred=EXCLUDED.subred,
                gateway=CASE WHEN EXCLUDED.gateway != '' THEN EXCLUDED.gateway ELSE granjas_sedes.gateway END,
                router_mac=CASE WHEN EXCLUDED.router_mac != '' THEN EXCLUDED.router_mac ELSE granjas_sedes.router_mac END,
                public_ip=CASE WHEN EXCLUDED.public_ip != '' THEN EXCLUDED.public_ip ELSE granjas_sedes.public_ip END,
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
                app_version=CASE WHEN EXCLUDED.app_version != '' THEN EXCLUDED.app_version ELSE granjas_sedes.app_version END,
                ultima_act=EXCLUDED.ultima_act
        """, (
            owner_user, farm_id_efectivo, agent_id, nombre_defecto_granja, f_city, subred_detectada, gw_detectado,
            router_mac_fmt, ip_publica_agente, tarifa_defecto, dev_type, run_mode, dev_brand, dev_model, dev_local_ip, has_batt,
            batt_pct, batt_charging, batt_temp, dev_os, dev_app_ver, ahora
        ))
    except Exception as err_gs:
        print(f"Error actualizando granjas_sedes ({farm_id_efectivo}): {err_gs}")

    try:
        cur.execute("""
            INSERT INTO agentes_conectados (
                owner, agent_id, farm_id, device_type, run_mode, brand, model, local_ip, subred, app_version, ultima_act
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (owner, agent_id) DO UPDATE SET
                farm_id=EXCLUDED.farm_id,
                device_type=EXCLUDED.device_type,
                run_mode=EXCLUDED.run_mode,
                brand=CASE WHEN EXCLUDED.brand != '' THEN EXCLUDED.brand ELSE agentes_conectados.brand END,
                model=CASE WHEN EXCLUDED.model != '' THEN EXCLUDED.model ELSE agentes_conectados.model END,
                local_ip=CASE WHEN EXCLUDED.local_ip != '' THEN EXCLUDED.local_ip ELSE agentes_conectados.local_ip END,
                subred=CASE WHEN EXCLUDED.subred != '' THEN EXCLUDED.subred ELSE agentes_conectados.subred END,
                app_version=CASE WHEN EXCLUDED.app_version != '' THEN EXCLUDED.app_version ELSE agentes_conectados.app_version END,
                ultima_act=EXCLUDED.ultima_act
        """, (
            owner_user, agent_id, farm_id_efectivo, dev_type, run_mode, dev_brand, dev_model,
            dev_local_ip, subred_detectada, dev_app_ver, ahora
        ))
    except Exception as err_ag_reg:
        print(f"Error actualizando agentes_conectados ({agent_id}): {err_ag_reg}")

    cur.execute(
        "SELECT nombre, ciudad, zt_activo, zt_pool1, zt_worker_prefix, zt_pass1, zt_pool2, zt_pool3 FROM granjas_sedes WHERE owner=%s AND farm_id=%s LIMIT 1",
        (owner_user, farm_id_efectivo)
    )
    row_sede_info = cur.fetchone() or {}
    nombre_sede_act = str(row_sede_info.get('nombre') or farm_id_efectivo).strip() or farm_id_efectivo
    ciudad_sede_act = str(row_sede_info.get('ciudad') or f_city).strip() or f_city
    zt_cfg = row_sede_info
    zt_on = (a_int_seguro(zt_cfg.get('zt_activo'), 0) == 1) and bool(str(zt_cfg.get('zt_pool1') or '').strip())

    cur.execute("""
        SELECT agent_id, farm_id, ip, miner_uid, mac, serial, corte_activo, corte_temp, watchdog_activo,
               wd_fallo_desde, wd_motivo_fallo, wd_ultimo_reinicio, wd_reinicios_seguidos,
               wd_cuarentena, placas_normales, prot_sleep_desde, prot_motivo,
               farm_lat, farm_lon, farm_city, gps_manual, ultima_act,
               COALESCE(alerta_hr_disparada, 0) AS alerta_hr_disparada,
               COALESCE(alerta_temp_disparada, 0) AS alerta_temp_disparada
        FROM mineros_activos WHERE owner=%s
    """, (owner_user,))
    rows_exist = cur.fetchall()

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (owner_user,))
    nombres_map = {r['ip']: str(r['nombre']).strip() for r in (cur.fetchall() or []) if r and r.get('nombre')}

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

    batch_caida_hr = []
    batch_recup_hr = []
    batch_temp_alta = []
    batch_temp_ok = []
    batch_watchdog = []

    # Detección temprana de apagón / corte de luz en la sede (si el agente reporta pero 0 mineros responden)
    if not mineros_payload:
        mineros_sede_previos = [
            r for r in rows_exist
            if (r.get('farm_id') == farm_id_efectivo or r.get('agent_id') == agent_id)
            and calcular_segundos_desconectado(r.get('ultima_act')) < 240
        ]
        if len(mineros_sede_previos) >= 2:
            if not alerta_en_enfriamiento(cur, owner_user, farm_id_efectivo, 'APAGON_SEDE', 300):
                cant = len(mineros_sede_previos)
                if alertas_on and tg_tok and tg_cid:
                    msg_tg = (
                        f"⚡ <b>ALERTA: CORTE DE LUZ / APAGÓN DETECTADO ({cant} equipos)</b>\n"
                        f"🏭 Sede: <b>{nombre_sede_act}</b> ({ciudad_sede_act})\n"
                        f"Estado: Los <b>{cant} equipos</b> de la granja dejaron de responder simultáneamente.\n"
                        f"💡 <i>Diagnóstico: Corte de suministro eléctrico o breaker principal disparado en la sede.</i>"
                    )
                    enviar_telegram(tg_tok, tg_cid, msg_tg)
                cur.execute(
                    "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'APAGON_SEDE', %s, %s)",
                    (owner_user, farm_id_efectivo, f"Corte de energía detectado: {cant} equipos apagados en {nombre_sede_act}.", ahora)
                )

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

            es_minero_nuevo = (prev_m is None)

            marca_m = str(m.get('marca') or '').strip()
            mod_m_desc = str(m.get('modelo') or 'Antminer').strip()
            cred_calc = determinar_credenciales_defecto(marca_m, mod_m_desc)
            if not marca_m:
                marca_m = cred_calc['marca']
            cred_def_m = str(m.get('cred_defecto') or f"{cred_calc['user']} / {cred_calc['pass']}").strip()

            try:
                cur.execute("""
                    INSERT INTO mineros_descubiertos (
                        owner, agent_id, farm_id, ip, mac, serial, marca, modelo, algoritmo, moneda,
                        unidad, hashrate, temp_max, consumo_w, worker1, pool1, cred_defecto, ultima_act
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (owner, agent_id, ip) DO UPDATE SET
                        farm_id=EXCLUDED.farm_id,
                        mac=CASE WHEN EXCLUDED.mac != '-' AND EXCLUDED.mac != '' THEN EXCLUDED.mac ELSE mineros_descubiertos.mac END,
                        serial=CASE WHEN EXCLUDED.serial != '-' AND EXCLUDED.serial != '' THEN EXCLUDED.serial ELSE mineros_descubiertos.serial END,
                        marca=EXCLUDED.marca, modelo=EXCLUDED.modelo, algoritmo=EXCLUDED.algoritmo, moneda=EXCLUDED.moneda,
                        unidad=EXCLUDED.unidad, hashrate=EXCLUDED.hashrate, temp_max=EXCLUDED.temp_max,
                        consumo_w=EXCLUDED.consumo_w, worker1=EXCLUDED.worker1, pool1=EXCLUDED.pool1,
                        cred_defecto=EXCLUDED.cred_defecto, ultima_act=EXCLUDED.ultima_act
                """, (
                    owner_user, agent_id, farm_id_efectivo, ip_m, mc, sr,
                    marca_m, mod_m_desc, str(m.get('algoritmo') or 'SHA-256'),
                    str(m.get('moneda') or 'BTC'), str(m.get('unidad') or 'TH/s'),
                    a_float_seguro(m.get('hashrate'), 0.0), a_int_seguro(m.get('temp'), 0),
                    a_float_seguro(m.get('consumo'), 0.0), str(m.get('worker1') or ''),
                    str(m.get('pool1') or ''), cred_def_m, ahora
                ))
            except Exception as err_desc:
                print(f"Error guardando descubierto {ip_m}: {err_desc}")

            if es_minero_nuevo and limite_efectivo > 0 and total_existentes >= limite_efectivo:
                omitidos_por_plan += 1
                continue

            if mac_norm:
                miner_uid = f"MAC:{mac_norm}"
            elif sn_norm:
                miner_uid = f"SN:{sn_norm}"
            else:
                miner_uid = (prev_m.get('miner_uid') if prev_m else '') or f"IP:{agent_id}:{ip_m}"

            farm_id_asignada = (prev_m.get('farm_id') if prev_m and prev_m.get('farm_id') else '') or farm_id_efectivo

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
            nom_m = nombres_map.get(ip_m) or f"Minero {ip_m.split('.')[-1]}"
            algo_m = str(m.get('algoritmo') or 'SHA-256')
            mon_m = str(m.get('moneda') or 'BTC')
            hr_m = a_float_seguro(m.get('hashrate'), 0.0)
            hr_30 = a_float_seguro(m.get('hashrate_30m'), hr_m)
            temp_m = a_int_seguro(m.get('temp'), 0)
            if temp_m >= 150 or temp_m < 0:
                temp_m = 0
            t_amb = a_int_seguro(m.get('temp_amb'), 0)
            if t_amb >= 120 or t_amb < -40:
                t_amb = 0
            cons_m = a_float_seguro(m.get('consumo'), 0.0)
            err = a_int_seguro(m.get('errores'), 0)
            unid = str(m.get('unidad') or 'TH/s')

            fw = str(m.get('firmware') or 'Desconocido')
            fw_tipo = str(m.get('fw_tipo') or 'Stock')[:30]
            perfil_oc = str(m.get('perfil_oc') or '')[:50]
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
            for ch in cadenas_list:
                if isinstance(ch, dict):
                    tc = a_int_seguro(ch.get('temp_c'), 0)
                    tp = a_int_seguro(ch.get('temp_p'), 0)
                    if tc >= 150 or tc < 0: ch['temp_c'] = 0
                    if tp >= 150 or tp < 0: ch['temp_p'] = 0
            cadenas_json = json.dumps(cadenas_list)

            cur.execute('''INSERT INTO mineros_activos (ip, owner, agent_id, farm_id, miner_uid, marca, modelo, algoritmo, moneda, unidad, hashrate, hashrate_30m, temp_max, temp_amb, consumo_w, estado, ultima_act, errores, pool1, worker1, pass1, pool2, worker2, pass2, pool3, worker3, pass3, mac, serial, firmware, fw_tipo, perfil_oc, fans, cadenas, uptime, status1, acc1, rej1, status2, acc2, rej2, status3, acc3, rej3, hostname, net_proto, netmask, gateway, dns, cred_defecto, farm_lat, farm_lon, farm_city, farm_country, real_lat, real_lon, real_source) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'online', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(owner, agent_id, ip) DO UPDATE SET 
                            farm_id=CASE WHEN mineros_activos.farm_id IS NOT NULL AND mineros_activos.farm_id != '' THEN mineros_activos.farm_id ELSE EXCLUDED.farm_id END,
                            miner_uid=EXCLUDED.miner_uid, marca=EXCLUDED.marca, modelo=EXCLUDED.modelo, algoritmo=EXCLUDED.algoritmo, moneda=EXCLUDED.moneda, unidad=EXCLUDED.unidad, hashrate=EXCLUDED.hashrate, hashrate_30m=EXCLUDED.hashrate_30m, temp_max=EXCLUDED.temp_max, temp_amb=EXCLUDED.temp_amb, consumo_w=EXCLUDED.consumo_w, estado='online', ultima_act=EXCLUDED.ultima_act, errores=EXCLUDED.errores, pool1=EXCLUDED.pool1, worker1=EXCLUDED.worker1, pass1=EXCLUDED.pass1, pool2=EXCLUDED.pool2, worker2=EXCLUDED.worker2, pass2=EXCLUDED.pass2, pool3=EXCLUDED.pool3, worker3=EXCLUDED.worker3, pass3=EXCLUDED.pass3, mac=CASE WHEN EXCLUDED.mac != '-' AND EXCLUDED.mac != '' THEN EXCLUDED.mac ELSE mineros_activos.mac END, serial=CASE WHEN EXCLUDED.serial != '-' AND EXCLUDED.serial != '' THEN EXCLUDED.serial ELSE mineros_activos.serial END, firmware=EXCLUDED.firmware, fw_tipo=EXCLUDED.fw_tipo, perfil_oc=EXCLUDED.perfil_oc, fans=EXCLUDED.fans, cadenas=EXCLUDED.cadenas, uptime=EXCLUDED.uptime, status1=EXCLUDED.status1, acc1=EXCLUDED.acc1, rej1=EXCLUDED.rej1, status2=EXCLUDED.status2, acc2=EXCLUDED.acc2, rej2=EXCLUDED.rej2, status3=EXCLUDED.status3, acc3=EXCLUDED.acc3, rej3=EXCLUDED.rej3, hostname=EXCLUDED.hostname, net_proto=EXCLUDED.net_proto, netmask=EXCLUDED.netmask, gateway=EXCLUDED.gateway, dns=EXCLUDED.dns, cred_defecto=EXCLUDED.cred_defecto,
                            farm_lat=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_lat ELSE EXCLUDED.farm_lat END,
                            farm_lon=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_lon ELSE EXCLUDED.farm_lon END,
                            farm_city=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_city ELSE EXCLUDED.farm_city END,
                            farm_country=CASE WHEN mineros_activos.gps_manual = 1 THEN mineros_activos.farm_country ELSE EXCLUDED.farm_country END,
                            real_lat=EXCLUDED.real_lat,
                            real_lon=EXCLUDED.real_lon,
                            real_source=EXCLUDED.real_source''',
                        (ip_m, owner_user, agent_id, farm_id_asignada, miner_uid, marca_m, mod_m, algo_m, mon_m, unid, hr_m, hr_30, temp_m, t_amb, cons_m, ahora, err, p1, w1, pa1, p2, w2, pa2, p3, w3, pa3, mc, sr, fw, fw_tipo, perfil_oc, fs, cadenas_json, up, st1, ac1, rj1, st2, ac2, rj2, st3, ac3, rj3, host, nproto, nmask, ngw, ndns, cred_def_m, f_lat, f_lon, f_city, f_country, f_lat, f_lon, f_source))

            if es_minero_nuevo and zt_on:
                zt_p1 = str(zt_cfg.get('zt_pool1') or '').strip()
                zt_p2 = str(zt_cfg.get('zt_pool2') or '').strip()
                zt_p3 = str(zt_cfg.get('zt_pool3') or '').strip()
                zt_pref = str(zt_cfg.get('zt_worker_prefix') or owner_user).strip().rstrip('.')
                zt_pas = str(zt_cfg.get('zt_pass1') or '123').strip()
                octeto = ip_m.split('.')[-1] if '.' in ip_m else '1'
                worker_auto = f"{zt_pref}.{octeto}"
                cmd_zt = f"guardar_pools|{zt_p1}|{worker_auto}|{zt_pas}|{zt_p2}|{worker_auto}|{zt_pas}|{zt_p3}|{worker_auto}|{zt_pas}"
                ordenes.append({"ip": ip_m, "comando": cmd_zt})
                msg_zt = f"🤖 ZERO-TOUCH: Nuevo ASIC {mod_m} ({ip_m}) auto-configurado con worker {worker_auto}."
                cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'ZERO_TOUCH', %s, %s)", (owner_user, ip_m, msg_zt, ahora))
                if alertas_on:
                    enviar_telegram(tg_tok, tg_cid, f"🤖 <b>ZERO-TOUCH ONBOARDING</b>\nNuevo equipo: <b>{mod_m}</b> ({ip_m})\nWorker asignado: <code>{worker_auto}</code>\nPool 1: <code>{zt_p1}</code>")

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
                                msg_q = f"🔒 CUARENTENA: {nom_m} ({ip_m}) falló tras {wd_reinicios} reinicios ({fallo_wd}). Puesto en SLEEP."
                                cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'WATCHDOG_CUARENTENA', %s, %s)",
                                            (owner_user, ip_m, msg_q, ahora))
                                if alertas_on:
                                    batch_watchdog.append({
                                        'ip': ip_m, 'nombre': nom_m, 'modelo': mod_m,
                                        'tipo': 'cuarentena', 'motivo': fallo_wd,
                                        'intento': wd_reinicios, 'max': wd_max_reinicios
                                    })
                            else:
                                nuevos_reb = wd_reinicios + 1
                                ordenes.append({"ip": ip_m, "comando": "restart"})
                                cur.execute("""
                                    UPDATE mineros_activos
                                    SET wd_ultimo_reinicio=%s, wd_reinicios_seguidos=%s, wd_fallo_desde='', wd_motivo_fallo=%s
                                    WHERE owner=%s AND agent_id=%s AND ip=%s
                                """, (ahora, nuevos_reb, fallo_wd, owner_user, agent_id, ip_m))
                                msg_r = f"🔄 AUTO-REINICIO ({nuevos_reb}/{wd_max_reinicios}): {nom_m} ({ip_m}) por {fallo_wd}."
                                cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'WATCHDOG_REBOOT', %s, %s)",
                                            (owner_user, ip_m, msg_r, ahora))
                                if alertas_on:
                                    batch_watchdog.append({
                                        'ip': ip_m, 'nombre': nom_m, 'modelo': mod_m,
                                        'tipo': 'reinicio', 'motivo': fallo_wd,
                                        'intento': nuevos_reb, 'max': wd_max_reinicios
                                    })

            # --- Gestión Inteligente de Alertas (Anti-Spam, Agrupación y Anti-Flapping de 120s) ---
            prev_alerta_hr = a_int_seguro(prev_m.get('alerta_hr_disparada'), 0)
            prev_alerta_temp = a_int_seguro(prev_m.get('alerta_temp_disparada'), 0)

            # 1. Alerta por Caída de Hashrate (Anti-Flapping: requiere al menos 120s tras la última recuperación)
            if umbral_h > 0 and hr_m < umbral_h and temp_m > 5:
                if prev_alerta_hr == 0:
                    if not alerta_en_enfriamiento(cur, owner_user, ip_m, 'HASHRATE_OK', 120):
                        cur.execute(
                            "UPDATE mineros_activos SET alerta_hr_disparada=1 WHERE owner=%s AND agent_id=%s AND ip=%s",
                            (owner_user, agent_id, ip_m)
                        )
                        msg_hr = f"El equipo {nom_m} ({ip_m}) cayó a {hr_m} {unid} (Mínimo: {umbral_h} {unid})."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'HASHRATE', %s, %s)",
                                    (owner_user, ip_m, msg_hr, ahora))
                        if alertas_on:
                            batch_caida_hr.append({
                                'ip': ip_m, 'nombre': nom_m, 'modelo': mod_m,
                                'hr': hr_m, 'unid': unid, 'umbral': umbral_h, 'temp': temp_m
                            })
            elif umbral_h > 0 and hr_m >= umbral_h:
                if prev_alerta_hr == 1:
                    if not alerta_en_enfriamiento(cur, owner_user, ip_m, 'HASHRATE', 120):
                        cur.execute(
                            "UPDATE mineros_activos SET alerta_hr_disparada=0 WHERE owner=%s AND agent_id=%s AND ip=%s",
                            (owner_user, agent_id, ip_m)
                        )
                        msg_ok = f"El equipo {nom_m} ({ip_m}) recuperó su hashrate: {hr_m} {unid}."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'HASHRATE_OK', %s, %s)",
                                    (owner_user, ip_m, msg_ok, ahora))
                        if alertas_on:
                            batch_recup_hr.append({
                                'ip': ip_m, 'nombre': nom_m, 'modelo': mod_m,
                                'hr': hr_m, 'unid': unid, 'temp': temp_m
                            })

            # 2. Alerta por Exceso de Temperatura (Histéresis de 5°C y Anti-Flapping de 120s)
            if temp_m >= umbral_t:
                if prev_alerta_temp == 0:
                    if not alerta_en_enfriamiento(cur, owner_user, ip_m, 'TEMP_OK', 120):
                        cur.execute(
                            "UPDATE mineros_activos SET alerta_temp_disparada=1 WHERE owner=%s AND agent_id=%s AND ip=%s",
                            (owner_user, agent_id, ip_m)
                        )
                        msg_al = f"El equipo {nom_m} ({ip_m}) alcanzó {temp_m}°C (Límite: {umbral_t}°C)."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'TEMP', %s, %s)",
                                    (owner_user, ip_m, msg_al, ahora))
                        if alertas_on:
                            batch_temp_alta.append({
                                'ip': ip_m, 'nombre': nom_m, 'modelo': mod_m,
                                'temp': temp_m, 'umbral': umbral_t, 'hr': hr_m, 'unid': unid
                            })
            elif temp_m > 0 and temp_m <= (umbral_t - 5):
                if prev_alerta_temp == 1:
                    if not alerta_en_enfriamiento(cur, owner_user, ip_m, 'TEMP', 120):
                        cur.execute(
                            "UPDATE mineros_activos SET alerta_temp_disparada=0 WHERE owner=%s AND agent_id=%s AND ip=%s",
                            (owner_user, agent_id, ip_m)
                        )
                        msg_temp_ok = f"El equipo {nom_m} ({ip_m}) estabilizó su temperatura a {temp_m}°C."
                        cur.execute("INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'TEMP_OK', %s, %s)",
                                    (owner_user, ip_m, msg_temp_ok, ahora))
                        if alertas_on:
                            batch_temp_ok.append({
                                'ip': ip_m, 'nombre': nom_m, 'modelo': mod_m,
                                'temp': temp_m, 'hr': hr_m, 'unid': unid
                            })
        except Exception as err_m:
            print(f"Error guardando minero {m.get('ip')}: {err_m}")

    # ==================== DESPACHO AGRUPADO E INTELIGENTE A TELEGRAM (SIN SPAM) ====================
    if alertas_on and tg_tok and tg_cid:
        # 1. Caídas de Hashrate (Si caen 2 o más: 1 solo mensaje agrupado)
        if len(batch_caida_hr) == 1:
            it = batch_caida_hr[0]
            enviar_telegram(
                tg_tok, tg_cid,
                f"⚠️ <b>CAÍDA DE HASHRATE</b>\n"
                f"Minero: <b>{it['nombre']}</b> (<code>{it['ip']}</code>)\n"
                f"Modelo: {it['modelo']}\n"
                f"Hashrate actual: <b>{it['hr']} {it['unid']}</b> (Mínimo: {it['umbral']} {it['unid']})\n"
                f"Temperatura: <b>{it['temp']}°C</b>"
            )
        elif len(batch_caida_hr) >= 2:
            cant = len(batch_caida_hr)
            lineas = [
                f"⚠️ <b>CAÍDA DE HASHRATE COLECTIVA ({cant} equipos)</b>",
                f"🏭 Sede: <b>{nombre_sede_act}</b> ({ciudad_sede_act})",
                ""
            ]
            for it in batch_caida_hr[:15]:
                lineas.append(f"• <b>{it['nombre']}</b> (<code>{it['ip']}</code>): <b>{it['hr']} {it['unid']}</b> ({it['temp']}°C)")
            if cant > 15:
                lineas.append(f"<i>...y {cant - 15} equipo(s) más afectados.</i>")
            lineas.append("")
            lineas.append("⚡ <i>Diagnóstico: Posible corte de luz, breaker del rack disparado o fallo de switch/red.</i>")
            enviar_telegram(tg_tok, tg_cid, "\n".join(lineas))
            cur.execute(
                "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'HASHRATE_MASIVO', %s, %s)",
                (owner_user, farm_id_efectivo, f"Caída masiva de hashrate en {cant} equipos de sede {nombre_sede_act}.", ahora)
            )

        # 2. Hashrate Recuperado (Si se recuperan 2 o más: 1 solo mensaje agrupado)
        if len(batch_recup_hr) == 1:
            it = batch_recup_hr[0]
            enviar_telegram(
                tg_tok, tg_cid,
                f"🟢 <b>HASHRATE RECUPERADO</b>\n"
                f"Minero: <b>{it['nombre']}</b> (<code>{it['ip']}</code>)\n"
                f"Modelo: {it['modelo']}\n"
                f"Hashrate: <b>{it['hr']} {it['unid']}</b> (Normalizado)\n"
                f"Temperatura: <b>{it['temp']}°C</b>"
            )
        elif len(batch_recup_hr) >= 2:
            cant = len(batch_recup_hr)
            lineas = [
                f"🟢 <b>HASHRATE RECUPERADO ({cant} equipos restablecidos)</b>",
                f"🏭 Sede: <b>{nombre_sede_act}</b> ({ciudad_sede_act})",
                ""
            ]
            for it in batch_recup_hr[:15]:
                lineas.append(f"• <b>{it['nombre']}</b> (<code>{it['ip']}</code>): <b>{it['hr']} {it['unid']}</b> (Normalizado)")
            if cant > 15:
                lineas.append(f"<i>...y {cant - 15} equipo(s) más restablecidos.</i>")
            lineas.append("")
            lineas.append("✨ <i>Energía y hashrate normalizados en todos los equipos de la sede.</i>")
            enviar_telegram(tg_tok, tg_cid, "\n".join(lineas))
            cur.execute(
                "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'HASHRATE_OK_MASIVO', %s, %s)",
                (owner_user, farm_id_efectivo, f"Hashrate normalizado en {cant} equipos de sede {nombre_sede_act}.", ahora)
            )

        # 3. Alertas de Temperatura (Si sobrecalientan 2 o más: 1 solo mensaje agrupado)
        if len(batch_temp_alta) == 1:
            it = batch_temp_alta[0]
            enviar_telegram(
                tg_tok, tg_cid,
                f"🔥 <b>ALERTA DE TEMPERATURA</b>\n"
                f"Minero: <b>{it['nombre']}</b> (<code>{it['ip']}</code>)\n"
                f"Modelo: {it['modelo']}\n"
                f"Temperatura: <b>{it['temp']}°C</b> (Límite: {it['umbral']}°C)\n"
                f"Hashrate: {it['hr']} {it['unid']}"
            )
        elif len(batch_temp_alta) >= 2:
            cant = len(batch_temp_alta)
            lineas = [
                f"🔥 <b>ALERTA DE TEMPERATURA COLECTIVA ({cant} equipos)</b>",
                f"🏭 Sede: <b>{nombre_sede_act}</b> ({ciudad_sede_act})",
                ""
            ]
            for it in batch_temp_alta[:15]:
                lineas.append(f"• <b>{it['nombre']}</b> (<code>{it['ip']}</code>): <b>{it['temp']}°C</b> (Límite: {it['umbral']}°C)")
            if cant > 15:
                lineas.append(f"<i>...y {cant - 15} equipo(s) más sobrecalentados.</i>")
            lineas.append("")
            lineas.append("🚨 <i>Revisa ventilación general, extractores o climatización de la sala inmediatamente.</i>")
            enviar_telegram(tg_tok, tg_cid, "\n".join(lineas))
            cur.execute(
                "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) VALUES (%s, %s, 'TEMP_MASIVA', %s, %s)",
                (owner_user, farm_id_efectivo, f"Sobrecalentamiento masivo en {cant} equipos de sede {nombre_sede_act}.", ahora)
            )

        # 4. Temperatura Normalizada (Si enfrían 2 o más: 1 solo mensaje agrupado)
        if len(batch_temp_ok) == 1:
            it = batch_temp_ok[0]
            enviar_telegram(
                tg_tok, tg_cid,
                f"🟢 <b>TEMPERATURA NORMALIZADA</b>\n"
                f"Minero: <b>{it['nombre']}</b> (<code>{it['ip']}</code>)\n"
                f"Temperatura: <b>{it['temp']}°C</b> (Enfriado)\n"
                f"Hashrate: {it['hr']} {it['unid']}"
            )
        elif len(batch_temp_ok) >= 2:
            cant = len(batch_temp_ok)
            lineas = [
                f"🟢 <b>TEMPERATURA NORMALIZADA ({cant} equipos enfriados)</b>",
                f"🏭 Sede: <b>{nombre_sede_act}</b> ({ciudad_sede_act})",
                ""
            ]
            for it in batch_temp_ok[:15]:
                lineas.append(f"• <b>{it['nombre']}</b> (<code>{it['ip']}</code>): <b>{it['temp']}°C</b> (Enfriado)")
            if cant > 15:
                lineas.append(f"<i>...y {cant - 15} equipo(s) más normalizados.</i>")
            enviar_telegram(tg_tok, tg_cid, "\n".join(lineas))

        # 5. Watchdog Colectivo
        if len(batch_watchdog) == 1:
            it = batch_watchdog[0]
            if it['tipo'] == 'cuarentena':
                enviar_telegram(
                    tg_tok, tg_cid,
                    f"🔒 <b>WATCHDOG: MINERO EN CUARENTENA</b>\n"
                    f"Minero: <b>{it['nombre']}</b> (<code>{it['ip']}</code>)\n"
                    f"Motivo: <b>{it['motivo']}</b>\n"
                    f"Reinicios fallidos: {it['intento']}/{it['max']}\n"
                    f"Acción: Puesto en <b>REPOSO (Sleep)</b>."
                )
            else:
                enviar_telegram(
                    tg_tok, tg_cid,
                    f"🔄 <b>WATCHDOG: AUTO-REINICIO</b>\n"
                    f"Minero: <b>{it['nombre']}</b> (<code>{it['ip']}</code>)\n"
                    f"Motivo: <b>{it['motivo']}</b>\n"
                    f"Intento: {it['intento']} de {it['max']}"
                )
        elif len(batch_watchdog) >= 2:
            cant = len(batch_watchdog)
            lineas = [
                f"🔄 <b>WATCHDOG: ACCIÓN COLECTIVA ({cant} equipos)</b>",
                f"🏭 Sede: <b>{nombre_sede_act}</b> ({ciudad_sede_act})",
                ""
            ]
            for it in batch_watchdog[:15]:
                lineas.append(f"• <b>{it['nombre']}</b> (<code>{it['ip']}</code>): {'🔒 Cuarentena' if it['tipo']=='cuarentena' else '🔄 Reinicio'} ({it['motivo']})")
            if cant > 15:
                lineas.append(f"<i>...y {cant - 15} equipo(s) más.</i>")
            enviar_telegram(tg_tok, tg_cid, "\n".join(lineas))

    try:
        cur.execute("UPDATE usuarios SET mineros_omitidos=%s WHERE username=%s", (0 if (es_pro and limite_efectivo == 0) else omitidos_por_plan, owner_user))
    except:
        pass

    if ahora_ts - ultima_limpieza_bd > 3600:
        ultima_limpieza_bd = ahora_ts
        try:
            # Retención optimizada para alta escala (500 clientes, 5,000+ ASICs):
            # - Incidentes / Auditoría (registro_alertas): 30 días (~10 MB, muy liviano)
            # - Telemetría continua (historial_nube): 30 días completos (rango mensual del gráfico)
            # - Arrays pesados de cadenas/chips en telemetría: limpiar tras 24h para mantener la BD ligera
            # - Cola de órdenes ejecutadas: 2 días
            fecha_limite_30d = (ahora_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            fecha_limite_24h = (ahora_dt - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            fecha_limite_cmd = (ahora_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("DELETE FROM historial_nube WHERE fecha < %s", (fecha_limite_30d,))
            cur.execute("UPDATE historial_nube SET cadenas=NULL WHERE fecha < %s AND cadenas IS NOT NULL", (fecha_limite_24h,))
            cur.execute("DELETE FROM registro_alertas WHERE fecha < %s", (fecha_limite_30d,))
            cur.execute("DELETE FROM cola_comandos WHERE estado='enviado' AND fecha < %s", (fecha_limite_cmd,))
        except Exception as err_clean:
            print(f"Error en limpieza automática BD: {err_clean}")

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

# ==================== ESCÁNER INTELIGENTE GLOBAL ====================
def _obtener_agentes_usuario(cur, owner):
    cur.execute("""
        SELECT agent_id, farm_id, device_type, run_mode, brand, model, local_ip, subred, app_version, ultima_act
        FROM agentes_conectados
        WHERE owner=%s
        ORDER BY ultima_act DESC
    """, (owner,))
    rows = cur.fetchall() or []

    if not rows:
        cur.execute("""
            SELECT COALESCE(NULLIF(agent_id, ''), farm_id) AS agent_id, farm_id,
                   device_type, run_mode, brand, model, local_ip, subred, app_version, ultima_act
            FROM granjas_sedes
            WHERE owner=%s
            ORDER BY ultima_act DESC
        """, (owner,))
        rows = cur.fetchall() or []

    lista = []
    for r in rows:
        segs = calcular_segundos_desconectado(r.get('ultima_act') or '')
        online = (segs <= 180)
        lista.append({
            "agent_id": r.get('agent_id') or 'default',
            "farm_id": r.get('farm_id') or r.get('agent_id') or 'default',
            "nombre": _formatear_nombre_agente(r),
            "device_type": r.get('device_type') or 'Celular',
            "local_ip": r.get('local_ip') or '',
            "subred": r.get('subred') or '',
            "app_version": r.get('app_version') or '',
            "online": online,
            "hace": _formatear_hace_tiempo(segs),
            "ultima_act": r.get('ultima_act') or ''
        })
    return lista


@agente_bp.route('/api/escaner/iniciar', methods=['POST'])
@login_required
def api_escaner_iniciar():
    owner = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    agent_id_filtro = str(data.get('agent_id') or '').strip()
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    con = get_db()
    con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    agentes = _obtener_agentes_usuario(cur, owner)
    if agent_id_filtro:
        agentes = [a for a in agentes if a['agent_id'] == agent_id_filtro]

    online_ids = []
    routers_online = set()
    for ag in agentes:
        if not ag['online']:
            continue
        ok_ws = enviar_orden_tunel(owner, 'SCAN_NET', 'scan_now', agent_id=ag['agent_id'])
        estado = 'enviado' if ok_ws else 'pendiente'
        cur.execute(
            "INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, 'SCAN_NET', 'scan_now', %s, %s)",
            (owner, ag['agent_id'], estado, ahora)
        )
        online_ids.append(ag['agent_id'])
        routers_online.add(ag['farm_id'])

    return jsonify({
        "ok": True,
        "agentes_online": online_ids,
        "routers_online": len(routers_online),
        "agentes": agentes
    }), 200


@agente_bp.route('/api/escaner/resultados', methods=['GET'])
@login_required
def api_escaner_resultados():
    owner = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM usuarios WHERE username=%s", (owner,))
    u_cfg = cur.fetchone() or {}
    es_pro, limite_efectivo, _ = evaluar_plan_usuario(cur, u_cfg)

    cur.execute("SELECT ip, mac, farm_id FROM mineros_activos WHERE owner=%s", (owner,))
    activos = cur.fetchall() or []
    activos_por_ip = {r['ip']: r for r in activos}
    macs_activas = {normalizar_mac(r.get('mac')) for r in activos if normalizar_mac(r.get('mac'))}

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (owner,))
    nombres_map = {r['ip']: r['nombre'] for r in (cur.fetchall() or []) if r.get('nombre')}

    cur.execute("""
        SELECT farm_id, nombre, ciudad, subred, agent_id
        FROM granjas_sedes
        WHERE owner=%s
        ORDER BY id ASC
    """, (owner,))
    granjas = cur.fetchall() or []
    granjas_map = {g['farm_id']: g for g in granjas}

    agentes = _obtener_agentes_usuario(cur, owner)

    hace_7d = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT agent_id, farm_id, ip, mac, serial, marca, modelo, algoritmo, moneda,
               unidad, hashrate, temp_max, consumo_w, worker1, pool1, cred_defecto, ultima_act
        FROM mineros_descubiertos
        WHERE owner=%s AND ultima_act >= %s
        ORDER BY ultima_act DESC, ip ASC
    """, (owner, hace_7d))
    descubiertos_raw = cur.fetchall() or []

    grupos_dict = {}
    for g in granjas:
        fid = g['farm_id']
        grupos_dict[fid] = {
            "farm_id": fid,
            "farm_nombre": g.get('nombre') or fid,
            "ciudad": g.get('ciudad') or '',
            "subred": g.get('subred') or '',
            "agentes": [],
            "mineros": []
        }

    for ag in agentes:
        fid = ag['farm_id']
        if fid not in grupos_dict:
            grupos_dict[fid] = {
                "farm_id": fid,
                "farm_nombre": f"Granja ({fid})",
                "ciudad": "",
                "subred": ag.get('subred') or '',
                "agentes": [],
                "mineros": []
            }
        grupos_dict[fid]["agentes"].append(ag)

    vistos_global = set()
    vistos_por_granja = set()
    lista_final = []

    for d in descubiertos_raw:
        ip_d = d['ip']
        mac_d = normalizar_mac(d.get('mac'))
        activo_row = activos_por_ip.get(ip_d)
        ya_agregado = (activo_row is not None) or (bool(mac_d) and mac_d in macs_activas)
        modelo_d = d.get('modelo') or 'Antminer'
        marca_d = d.get('marca') or ''
        cred_d = d.get('cred_defecto') or ''

        cred_info = determinar_credenciales_defecto(marca_d, modelo_d)
        if not marca_d:
            marca_d = cred_info['marca']
        if not cred_d:
            cred_d = f"{cred_info['user']} / {cred_info['pass']}"

        # Nombre: 1) guardado en sistema, 2) sufijo de worker1 (ej. giovanni.008 -> 008), 3) octeto IP
        nombre_defecto = nombres_map.get(ip_d) or _extraer_nombre_desde_worker(d.get('worker1'), ip_d)

        router_fid = str(d.get('farm_id') or d.get('agent_id') or 'default')
        farm_asignada = (activo_row.get('farm_id') if activo_row and activo_row.get('farm_id') else '') or router_fid

        if router_fid not in grupos_dict:
            g_info = granjas_map.get(router_fid) or {}
            grupos_dict[router_fid] = {
                "farm_id": router_fid,
                "farm_nombre": g_info.get('nombre') or f"Granja ({router_fid})",
                "ciudad": g_info.get('ciudad') or '',
                "subred": g_info.get('subred') or '',
                "agentes": [],
                "mineros": []
            }

        clave_g = (router_fid, mac_d if mac_d else ip_d)
        if clave_g in vistos_por_granja:
            continue
        vistos_por_granja.add(clave_g)

        item = {
            "ip": ip_d,
            "agent_id": d.get('agent_id') or 'default',
            "farm_id": farm_asignada,
            "router_farm_id": router_fid,
            "marca": marca_d,
            "modelo": modelo_d,
            "algoritmo": d.get('algoritmo') or 'SHA-256',
            "moneda": d.get('moneda') or 'BTC',
            "cred_defecto": cred_d,
            "cred_user": cred_info.get('user', 'root'),
            "cred_pass": cred_info.get('pass', 'root'),
            "cred_alt": cred_info.get('alt', ''),
            "cred_nota": cred_info.get('nota', ''),
            "nombre": nombre_defecto,
            "ultima_act": d.get('ultima_act') or '',
            "ya_agregado": ya_agregado
        }
        grupos_dict[router_fid]["mineros"].append(item)

        if ip_d not in vistos_global:
            vistos_global.add(ip_d)
            lista_final.append(item)

    grupos_lista = []
    for g in grupos_dict.values():
        g["mineros"].sort(key=lambda x: (0 if not x['ya_agregado'] else 1, x['ip']))
        g["agentes"].sort(key=lambda a: (0 if a['online'] else 1, a['nombre']))
        g["online_count"] = sum(1 for a in g["agentes"] if a["online"])
        if g["agentes"] or g["mineros"]:
            grupos_lista.append(g)

    grupos_lista.sort(key=lambda g: (0 if g["online_count"] > 0 else 1, -len(g["mineros"]), g["farm_nombre"]))
    lista_final.sort(key=lambda x: (0 if not x['ya_agregado'] else 1, x['ip']))

    return jsonify({
        "ok": True,
        "es_pro": bool(es_pro),
        "limite": limite_efectivo,
        "activos_count": len(activos),
        "routers_count": len(grupos_lista),
        "granjas": granjas,
        "agentes": agentes,
        "grupos": grupos_lista,
        "descubiertos": lista_final
    }), 200


@agente_bp.route('/api/escaner/blink', methods=['POST'])
@login_required
def api_escaner_blink():
    owner = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}
    ip = str(data.get('ip') or '').strip()
    agent_id = str(data.get('agent_id') or 'default').strip() or 'default'
    if not ip:
        return jsonify({"ok": False, "msg": "IP requerida"}), 400

    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    con = get_db()
    con.autocommit = True
    cur = con.cursor()
    ok_ws = enviar_orden_tunel(owner, ip, 'blink', agent_id=agent_id)
    estado = 'enviado' if ok_ws else 'pendiente'
    cur.execute(
        "INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) VALUES (%s, %s, %s, 'blink', %s, %s)",
        (owner, agent_id, ip, estado, ahora)
    )
    return jsonify({"ok": True, "ip": ip}), 200


@agente_bp.route('/api/escaner/agregar', methods=['POST'])
@login_required
def api_escaner_agregar():
    owner = session.get('usuario', 'Admin')
    data = request.get_json(silent=True) or {}

    items = data.get('items', [])
    eliminar_ips = data.get('eliminar_ips', [])

    if not items and isinstance(data.get('ips'), list):
        f_def = str(data.get('farm_id') or '').strip()
        items = [{"ip": ip_x, "farm_id": f_def, "nombre": ""} for ip_x in data.get('ips')]

    if not items and not eliminar_ips:
        return jsonify({"ok": False, "msg": "No hay cambios para aplicar."}), 400

    con = get_db()
    con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM usuarios WHERE username=%s", (owner,))
    u_cfg = cur.fetchone() or {}
    es_pro, limite_efectivo, _ = evaluar_plan_usuario(cur, u_cfg)

    eliminados = 0
    for ip_del in (eliminar_ips or []):
        ip_c = str(ip_del or '').strip()
        if not ip_c:
            continue
        cur.execute("DELETE FROM mineros_activos WHERE owner=%s AND ip=%s", (owner, ip_c))
        if cur.rowcount > 0:
            eliminados += 1

    cur.execute("SELECT ip FROM mineros_activos WHERE owner=%s", (owner,))
    ips_existentes = {r['ip'] for r in (cur.fetchall() or [])}
    total_actual = len(ips_existentes)

    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    agregados = 0
    actualizados = 0
    bloqueados = 0

    for it in (items or []):
        ip_c = str(it.get('ip') or '').strip()
        if not ip_c:
            continue
        nombre_custom = sanitizar_nombre_minero(it.get('nombre'), ip_defecto=ip_c)
        farm_dest = str(it.get('farm_id') or '').strip()

        ya_existe = ip_c in ips_existentes

        if not ya_existe:
            if limite_efectivo > 0 and (total_actual + agregados) >= limite_efectivo:
                bloqueados += 1
                continue

            cur.execute("""
                SELECT * FROM mineros_descubiertos
                WHERE owner=%s AND ip=%s
                ORDER BY ultima_act DESC LIMIT 1
            """, (owner, ip_c))
            d = cur.fetchone()
            if not d:
                continue

            ag_id = str(d.get('agent_id') or 'default').strip() or 'default'
            f_id = farm_dest or str(d.get('farm_id') or ag_id)
            mac_n = normalizar_mac(d.get('mac'))
            sn_n = normalizar_sn(d.get('serial'))
            miner_uid = f"MAC:{mac_n}" if mac_n else (f"SN:{sn_n}" if sn_n else f"IP:{ag_id}:{ip_c}")

            cur.execute("""
                INSERT INTO mineros_activos (
                    ip, owner, agent_id, farm_id, miner_uid, modelo, algoritmo, moneda, unidad,
                    hashrate, hashrate_30m, temp_max, consumo_w, estado, ultima_act, errores,
                    pool1, worker1, pass1, mac, serial, firmware, fans, uptime
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, 'online', %s, 0,
                    %s, %s, '123', %s, %s, 'Stock', '0 / 0 RPM', 'Recién agregado'
                )
                ON CONFLICT (owner, agent_id, ip) DO UPDATE SET
                    farm_id=EXCLUDED.farm_id, estado='online', ultima_act=EXCLUDED.ultima_act
            """, (
                ip_c, owner, ag_id, f_id, miner_uid,
                d.get('modelo') or 'Antminer', d.get('algoritmo') or 'SHA-256',
                d.get('moneda') or 'BTC', d.get('unidad') or 'TH/s',
                a_float_seguro(d.get('hashrate'), 0.0), a_float_seguro(d.get('hashrate'), 0.0),
                a_int_seguro(d.get('temp_max'), 0), a_float_seguro(d.get('consumo_w'), 0.0),
                ahora, d.get('pool1') or '', d.get('worker1') or '',
                d.get('mac') or '-', d.get('serial') or '-'
            ))
            agregados += 1
        else:
            if farm_dest:
                cur.execute("UPDATE mineros_activos SET farm_id=%s WHERE owner=%s AND ip=%s", (farm_dest, owner, ip_c))
            actualizados += 1

        if nombre_custom:
            cur.execute("""
                INSERT INTO nombres_mineros (owner, ip, nombre) VALUES (%s, %s, %s)
                ON CONFLICT (owner, ip) DO UPDATE SET nombre=EXCLUDED.nombre
            """, (owner, ip_c, nombre_custom))

    if agregados == 0 and eliminados == 0 and actualizados == 0 and bloqueados > 0:
        return jsonify({
            "ok": False,
            "limite_alcanzado": True,
            "limite": limite_efectivo,
            "msg": f"Tu plan Gratis permite {limite_efectivo} mineros. Mejora tu plan para añadir más.",
            "bloqueados": bloqueados
        }), 403

    return jsonify({
        "ok": True,
        "agregados": agregados,
        "eliminados": eliminados,
        "actualizados": actualizados,
        "bloqueados": bloqueados
    }), 200
