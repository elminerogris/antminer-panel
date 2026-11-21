from flask import Blueprint, redirect, request, session, jsonify, Response
from werkzeug.security import generate_password_hash
import psycopg2.extras, json, time, calendar, csv, io
from datetime import datetime

from db import get_db
from tunel import enviar_orden_tunel
from utils import (
    login_required, es_super_admin, es_cuenta_pro, generar_api_key,
    es_hash_seguro, verificar_password, a_int_seguro, a_float_seguro,
    clave_granja_minero, nombre_inteligente_minero, calcular_segundos_desconectado,
    ordenar_ip_key, obtener_datos_red, enviar_telegram, render_seguro
)

panel_bp = Blueprint('panel_bp', __name__)

def despachar_comando(cur, owner, ip, comando, fecha=None):
    """Envía el comando al instante por el túnel WebSocket; si no hay túnel activo, lo encola en BD."""
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
    tarifa = max(0.0, a_float_seguro(data.get('tarifa_kwh'), 0.0))
    con = get_db(); con.autocommit = True; cur = con.cursor()
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

    agent_id = str(data.get('agent_id') or '').strip()
    subred = str(data.get('subred') or '').strip()
    con = get_db(); con.autocommit = True; cur = con.cursor()

    if agent_id and subred and subred.endswith('.x'):
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

# --- Vistas Principales ---
@panel_bp.route('/')
@panel_bp.route('/resumen')
@login_required
def index():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT tarifa_kwh, plan, saldo FROM usuarios WHERE username=%s", (usr_actual,))
    u_row = cur.fetchone() or {}
    tarifa_kwh = a_float_seguro(u_row.get('tarifa_kwh'), 0.0)
    session['plan'] = u_row.get('plan', session.get('plan', 'Gratis'))
    session['saldo'] = u_row.get('saldo', session.get('saldo', 0.0))

    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    mineros = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    datos_red = obtener_datos_red()
    registros, en_linea, offline = [], 0, 0
    ht, kt, gt, cw, tm, ig, gan_neta_total = 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0

    for m in mineros:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        algo = m.get('algoritmo') or ('Equihash' if 'z15' in str(mod).lower() else 'SHA-256')
        mon = m.get('moneda') or ('ZEC' if algo == 'Equihash' else 'BTC')
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre_custom = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
        hr = a_float_seguro(m.get('hashrate'), 0.0)
        t = a_int_seguro(m.get('temp_max'), 0)
        c = a_float_seguro(m.get('consumo_w'), 0.0)

        segs_desconectado = calcular_segundos_desconectado(m.get('ultima_act'))
        eficiencia_val = round(c / hr, 2) if hr > 0 else 0
        if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)

        if segs_desconectado < 300:
            en_linea += 1
            if unidad == 'TH/s' and algo == 'SHA-256': ht += hr
            elif unidad == 'kSol/s': kt += hr
            elif unidad == 'GH/s': gt += hr

            cw += c; tm = max(tm, t)
            if unidad == 'TH/s' and algo == 'SHA-256' and datos_red['dificultad_t'] > 0:
                b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296)
                ib = b * datos_red['precio']
                costo_elec = (c / 1000.0) * 24.0 * tarifa_kwh
                neto = ib - costo_elec
                ig += ib
                gan_neta_total += neto
                str_gan = f"-USD {abs(neto):.2f}" if neto < 0 else f"{neto:.2f} USD"
            else:
                str_gan = f"Pool {mon}"

            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'algoritmo': algo, 'moneda': mon,
                'unidad': unidad, 'unidad_efi': unidad_efi,
                'estado': '<span style="color:var(--accent-green);">● En línea</span>',
                'tiempo_act': f'hace {int(segs_desconectado)}s', 'hashrate': hr,
                'hashrate_30m': a_float_seguro(m.get('hashrate_30m'), hr),
                'temp_num': t, 'consumo_kw': f"{round(c/1000, 2)} kW",
                'eficiencia': eficiencia_val, 'ganancia_neta': str_gan
            })
        else:
            offline += 1
            registros.append({
                'ip': ip, 'nombre': nombre_custom, 'modelo': mod, 'algoritmo': algo, 'moneda': mon,
                'unidad': unidad, 'unidad_efi': unidad_efi,
                'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>',
                'tiempo_act': f'hace {int(segs_desconectado // 60)}m', 'hashrate': 0, 'hashrate_30m': 0, 'temp_num': 0,
                'consumo_kw': '0.0 kW', 'eficiencia': 0, 'ganancia_neta': "USD 0.00"
            })

    flota = {
        'hashrate_total': round(ht, 1),
        'ksol_total': round(kt, 1),
        'ghs_total': round(gt, 1),
        'consumo_w_total': round(cw, 0),
        'consumo_kw': round(cw/1000, 1), 'temp_max': tm,
        'tarifa_kwh': tarifa_kwh,
        'precio_btc_raw': datos_red['precio'],
        'precio_btc': f"{datos_red['precio']:,.2f}",
        'dificultad_t': round(datos_red['dificultad_t'], 2),
        'recompensa_bloque': datos_red['recompensa'],
        'red_ehs': datos_red['red_ehs'],
        'ingreso_dia': f"{ig:.2f}",
        'ganancia_dia': f"-USD {abs(gan_neta_total):.2f}" if gan_neta_total < 0 else f"{gan_neta_total:.2f} USD"
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

    mod_str = str(m.get('modelo') or 'Antminer')
    algoritmo_val = str(m.get('algoritmo') or ('Equihash' if 'z15' in mod_str.lower() else 'SHA-256'))
    moneda_val = str(m.get('moneda') or ('ZEC' if algoritmo_val == 'Equihash' else 'BTC'))
    unidad_hr = str(m.get('unidad') or ('kSol/s' if 'z15' in mod_str.lower() else 'TH/s'))
    unidad_efi = "J/kSol" if unidad_hr == "kSol/s" else ("J/GH" if unidad_hr == "GH/s" else "J/TH")

    nombre_minero = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
    hr_val = a_float_seguro(m.get('hashrate'), 0.0)
    hr_30m_val = a_float_seguro(m.get('hashrate_30m'), hr_val)
    t_val = a_int_seguro(m.get('temp_max'), 0)
    t_amb_val = a_int_seguro(m.get('temp_amb'), 0)
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
            tp_g.append(a_int_seguro(h.get('temp_max'), 0))

            val_amb = a_int_seguro(h.get('temp_amb'), 0)
            tamb_g.append(val_amb if val_amb > 0 else None)

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
    extra = {
        'worker1': worker1, 'pool1': pool1, 'pass1': pass1, 'status1': status1, 'acc1': acc1, 'rej1': rej1,
        'worker2': worker2, 'pool2': pool2, 'pass2': pass2, 'status2': status2, 'acc2': acc2, 'rej2': rej2,
        'worker3': worker3, 'pool3': pool3, 'pass3': pass3, 'status3': status3, 'acc3': acc3, 'rej3': rej3,
        'mac': mac, 'serial': serial, 'firmware': fw, 'cadenas': cadenas,
        'hostname': hostname, 'net_proto': net_proto, 'netmask': netmask, 'gateway': gateway, 'dns': dns
    }

    eficiencia_val = round(c_val / hr_val, 2) if hr_val > 0 else 0
    if eficiencia_val > 1000: eficiencia_val = round(eficiencia_val / 1000, 2)
    disp = {'eficiencia': eficiencia_val}

    return render_seguro(
        'minero.html', active_page='mineros', ip=ip, nombre=nombre_minero, modelo=mod_str,
        ultimo=dt, extra=extra, dispositivo=disp, offline=offline,
        graf_fechas_raw=json.dumps(f_raw), graf_hashrate=json.dumps(hr_g),
        graf_temps=json.dumps(tp_g), graf_temp_amb=json.dumps(tamb_g),
        graf_errores=json.dumps(err_g), cadenas_json=json.dumps(cadenas),
        graf_cadenas_hist=json.dumps(cad_g), ips_ocupadas=json.dumps(ips_ocupadas)
    )

@panel_bp.route('/mineros')
@login_required
def mineros():
    usr_actual = session.get('usuario', 'Admin')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    lista_mineros = []
    contadores = {'total': len(filas), 'online': 0, 'offline': 0, 'alerta_temp': 0, 'alerta_hw': 0}

    for m in filas:
        ip = m['ip']
        mod = m.get('modelo') or 'Antminer'
        unidad = m.get('unidad') or ('kSol/s' if 'z15' in str(mod).lower() else 'TH/s')
        unidad_efi = "J/kSol" if unidad == "kSol/s" else ("J/GH" if unidad == "GH/s" else "J/TH")
        nombre = nombre_inteligente_minero(ip, nombres_dict, m.get('worker1'))
        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_online = segs < 300

        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_online else 0.0
        hr_30m = a_float_seguro(m.get('hashrate_30m'), hr) if es_online else 0.0
        t_max = a_int_seguro(m.get('temp_max'), 0) if es_online else 0
        t_amb = a_int_seguro(m.get('temp_amb'), 0) if es_online else 0
        cons_w = a_float_seguro(m.get('consumo_w'), 0.0) if es_online else 0.0
        err_hw = a_int_seguro(m.get('errores'), 0) if es_online else 0

        efi = round(cons_w / hr, 2) if hr > 0 else 0.0
        if efi > 1000: efi = round(efi / 1000, 2)

        try: cads = json.loads(m['cadenas']) if m.get('cadenas') else []
        except: cads = []
        placas_activas = len(cads) if es_online else 0
        chips_totales = sum(a_int_seguro(c.get('chips'), 0) for c in cads) if es_online else 0

        acc1 = a_int_seguro(m.get('acc1'), 0)
        rej1 = a_int_seguro(m.get('rej1'), 0)
        tot_shares = acc1 + rej1
        pct_rej = round((rej1 / tot_shares) * 100, 2) if tot_shares > 0 else 0.0

        if es_online:
            contadores['online'] += 1
            if t_max >= 75: contadores['alerta_temp'] += 1
            if err_hw > 50 or pct_rej > 1.5: contadores['alerta_hw'] += 1
        else:
            contadores['offline'] += 1

        lista_mineros.append({
            'ip': ip, 'ultimo_octeto': ip.split('.')[-1], 'nombre': nombre, 'modelo': mod,
            'algoritmo': m.get('algoritmo') or 'SHA-256', 'moneda': m.get('moneda') or 'BTC',
            'mac': m.get('mac') or '-', 'firmware': m.get('firmware') or '-', 'online': es_online,
            'uptime': (m.get('uptime') or 'En línea') if es_online else f'Offline ({int(segs // 60)}m)',
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
    cur.execute("SELECT * FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)

    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {row['ip']: row['nombre'] for row in cur.fetchall()}

    grupos = {}
    for m in filas:
        ip = m['ip']
        ciudad = str(m.get('farm_city') or 'Santa Cruz de la Sierra')
        subred = ".".join(ip.split('.')[:3]) + ".x" if '.' in ip else "LAN"
        clave = clave_granja_minero(ip, ciudad)
        gw_reportado = str(m.get('gateway') or '').strip()
        gw_fallback = (".".join(ip.split('.')[:3]) + ".1") if '.' in ip else "192.168.0.1"
        gw_ip = gw_reportado if (gw_reportado and gw_reportado != ip) else gw_fallback
        ag_id = str(m.get('agent_id') or 'default').strip() or 'default'

        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_online = segs < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_online else 0.0
        cw = a_float_seguro(m.get('consumo_w'), 0.0) if es_online else 0.0
        tm = a_int_seguro(m.get('temp_max'), 0) if es_online else 0

        if clave not in grupos:
            grupos[clave] = {
                'id': clave.lower().replace(' ', '_').replace('(', '').replace(')', '').replace('.', '_'),
                'clave': clave,
                'nombre': f"Granja {ciudad}", 'ciudad': ciudad, 'subred': subred,
                'gateway': gw_ip, 'agent_id': ag_id,
                'total': 0, 'online': 0, 'offline': 0,
                'hashrate': 0.0, 'consumo_kw': 0.0, 'temp_max': 0, 'mineros': []
            }

        g = grupos[clave]
        if gw_reportado and gw_reportado != ip:
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
            'ip': ip, 'nombre': nombre_inteligente_minero(ip, nombres_dict, m.get('worker1')),
            'modelo': m.get('modelo') or 'Antminer', 'online': es_online,
            'hashrate': hr, 'unidad': m.get('unidad') or 'TH/s', 'temp_max': tm
        })

    return render_seguro('granjas.html', active_page='granjas', granjas=list(grupos.values()))

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
        SELECT tg_token, tg_chat_id, umbral_temp, umbral_hr, alertas_activas,
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

    cur.execute("SELECT ip, modelo, worker1, farm_city, watchdog_activo, wd_cuarentena, wd_reinicios_seguidos, wd_motivo_fallo, prot_sleep_desde, prot_motivo FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas_m = sorted(cur.fetchall(), key=ordenar_ip_key)
    cur.execute("SELECT ip, nombre FROM nombres_mineros WHERE owner=%s", (usr_actual,))
    nombres_dict = {r['ip']: r['nombre'] for r in cur.fetchall()}

    granjas_map = {}
    mineros_disp = []
    en_cuarentena = []
    for fm in filas_m:
        ip_m = fm['ip']
        clave_g = clave_granja_minero(ip_m, fm.get('farm_city'))
        granjas_map[clave_g] = granjas_map.get(clave_g, 0) + 1
        es_q = a_int_seguro(fm.get('wd_cuarentena'), 0) == 1
        es_psleep = bool(str(fm.get('prot_sleep_desde') or '').strip())
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

    cur.execute("SELECT * FROM registro_alertas WHERE owner=%s ORDER BY id DESC LIMIT 100", (usr_actual,))
    historial = cur.fetchall()
    return render_seguro(
        'alertas.html', active_page='alertas', cfg=cfg, wd=wd,
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
            cur.execute("UPDATE usuarios SET subnet=%s, tarifa_kwh=%s WHERE username=%s", (subnet, tarifa, usr_actual))
            msg = "Preferencias de red y tarifa guardadas."
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
                        ('mineros_activos', 'owner'), ('nombres_mineros', 'owner'),
                        ('logs_mineros', 'owner'), ('historial_nube', 'owner'),
                        ('cola_comandos', 'owner'), ('registro_alertas', 'owner'),
                        ('movimientos_saldo', 'username')
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

    cur.execute("SELECT * FROM usuarios WHERE username=%s", (usr_actual,))
    u_data = cur.fetchone() or {}
    if not u_data.get('api_key'):
        nueva_k = generar_api_key()
        cur.execute("UPDATE usuarios SET api_key=%s WHERE username=%s", (nueva_k, usr_actual))
        u_data['api_key'] = nueva_k

    user_info = {
        'username': usr_actual, 'plan': u_data.get('plan', 'Gratis'),
        'api_key': u_data.get('api_key', ''), 'subnet': u_data.get('subnet') or '192.168.0',
        'tarifa_kwh': u_data.get('tarifa_kwh') or 0.0
    }
    return render_seguro('ajustes.html', active_page='ajustes', user=user_info, msg=msg)

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

# --- Autenticación ---
@panel_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
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
            session.pop('admin_real', None)
            session['usuario'] = usr['username']
            session['plan'] = usr.get('plan', 'Gratis')
            session['saldo'] = usr.get('saldo', 0.0)
            session['last_seen_ts'] = time.time()
            return redirect('/')
        return render_seguro('login.html', error='Credenciales incorrectas', mode='login')
    return render_seguro('login.html', mode='login')

@panel_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        em = (request.form.get('email') or '').strip().lower()
        p = request.form.get('password') or ''
        if not u or not p:
            return render_seguro('login.html', error='Completa usuario y contraseña', mode='register')
        if len(u) < 3 or len(u) > 30 or ' ' in u:
            return render_seguro('login.html', error='El usuario debe tener entre 3 y 30 caracteres sin espacios', mode='register')
        try:
            con = get_db(); con.autocommit = True; cur = con.cursor()
            cur.execute("SELECT id FROM usuarios WHERE LOWER(username)=LOWER(%s)", (u,))
            if cur.fetchone():
                return render_seguro('login.html', error='El nombre de usuario ya está en uso', mode='register')
            hoy_str = datetime.utcnow().strftime("%Y-%m-%d")
            cur.execute("INSERT INTO usuarios (username, email, fecha_registro, password, plan, saldo, api_key) VALUES (%s, %s, %s, %s, 'Gratis', 0.0, %s)",
                        (u, em, hoy_str, generate_password_hash(p), generar_api_key()))
            return render_seguro('login.html', success='Cuenta creada (Plan Gratis: hasta 3 mineros). Ahora inicia sesión.', mode='login')
        except:
            return render_seguro('login.html', error='El nombre de usuario ya está en uso', mode='register')
    return render_seguro('login.html', mode='register')

@panel_bp.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- Comandos, Logs, Exportación y Live ---
@panel_bp.route('/guardar_nombre', methods=['POST'])
@login_required
def guardar_nombre():
    usr_actual = session.get('usuario', 'Admin')
    ip = request.form.get('ip')
    nombre = request.form.get('nombre')
    if ip and nombre:
        con = get_db(); con.autocommit = True; cur = con.cursor()
        cur.execute("INSERT INTO nombres_mineros (owner, ip, nombre) VALUES (%s, %s, %s) ON CONFLICT (owner, ip) DO UPDATE SET nombre=EXCLUDED.nombre", (usr_actual, ip, nombre))
    return redirect(f'/minero/{ip}')

@panel_bp.route('/cmd_bulk', methods=['POST'])
@login_required
def cmd_bulk():
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

@panel_bp.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    usr_actual = session.get('usuario', 'Admin')
    cmd_str = ""
    if accion in ['restart', 'reset', 'sleep', 'normal', 'blink', 'scan', 'get_log', 'clog', 'hlog', 'dlog'] or accion.startswith('profile_'):
        cmd_str = accion.replace('_', '|') if accion.startswith('profile_') else accion
    elif accion == 'set_network':
        host = request.args.get('host', 'Antminer')
        proto = request.args.get('proto', '2')
        nip = request.args.get('nip', ip)
        mask = request.args.get('mask', '255.255.255.0')
        gw = request.args.get('gw', '192.168.0.1')
        dns = request.args.get('dns', '8.8.8.8')
        cmd_str = f"set_network|{host}|{proto}|{nip}|{mask}|{gw}|{dns}"
    elif accion == 'guardar_pools':
        u1 = request.args.get('u1', ''); w1 = request.args.get('w1', ''); p1 = request.args.get('p1', '123')
        u2 = request.args.get('u2', ''); w2 = request.args.get('w2', ''); p2 = request.args.get('p2', '123')
        u3 = request.args.get('u3', ''); w3 = request.args.get('w3', ''); p3 = request.args.get('p3', '123')
        cmd_str = f"guardar_pools|{u1}|{w1}|{p1}|{u2}|{w2}|{p2}|{u3}|{w3}|{p3}"

    if cmd_str:
        con = get_db(); con.autocommit = True; cur = con.cursor()
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
    cur.execute("SELECT ip, modelo, algoritmo, moneda, hashrate, temp_max, consumo_w, estado FROM mineros_activos WHERE owner=%s", (usr_actual,))
    filas = sorted(cur.fetchall(), key=ordenar_ip_key)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['IP', 'Modelo', 'Algoritmo', 'Moneda', 'Hashrate', 'Temp Max (C)', 'Consumo (W)', 'Estado'])
    for f in filas: writer.writerow([f['ip'], f['modelo'], f.get('algoritmo', 'SHA-256'), f.get('moneda', 'BTC'), f['hashrate'], f['temp_max'], f['consumo_w'], f['estado']])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=Reporte_El_Minero_Gris.csv"})

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
        t_amb = a_int_seguro(h.get('temp_amb'), 0)
        puntos.append({
            "ts": ts,
            "hr": a_float_seguro(h.get('hashrate'), 0.0),
            "temp": a_int_seguro(h.get('temp_max'), 0),
            "amb": t_amb if t_amb > 0 else None,
            "err": diff_err,
            "cadenas": cads
        })

    return jsonify({"ok": True, "puntos": puntos})
