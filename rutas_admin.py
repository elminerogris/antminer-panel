from flask import Blueprint, redirect, request, session
from werkzeug.security import generate_password_hash
import psycopg2.extras, json
from datetime import datetime, timedelta

from db import get_db
from utils import (
    login_required, es_super_admin, a_int_seguro, a_float_seguro,
    evaluar_plan_usuario, obtener_datos_red, calcular_segundos_desconectado,
    render_seguro
)

admin_bp = Blueprint('admin_bp', __name__)

@admin_bp.route('/admin/soporte/<int:uid>')
@login_required
def admin_entrar_soporte(uid):
    if not es_super_admin():
        return redirect('/')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username, plan, saldo FROM usuarios WHERE id=%s", (uid,))
    target = cur.fetchone()
    if not target:
        return redirect('/admin')
    session['admin_real'] = 'Admin'
    session['usuario'] = target['username']
    session['plan'] = target.get('plan', 'Gratis')
    session['saldo'] = target.get('saldo', 0.0)
    return redirect('/')

@admin_bp.route('/admin/salir_soporte')
@login_required
def admin_salir_soporte():
    if str(session.get('admin_real') or '').lower() != 'admin':
        return redirect('/')
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT plan, saldo FROM usuarios WHERE LOWER(username)='admin'")
    adm = cur.fetchone() or {}
    session.pop('admin_real', None)
    session['usuario'] = 'Admin'
    session['plan'] = adm.get('plan', 'Pro')
    session['saldo'] = adm.get('saldo', 0.0)
    return redirect('/admin')

@admin_bp.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_panel():
    if not es_super_admin():
        return redirect('/')
    msg = None
    con = get_db()
    con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    ahora_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'guardar_anuncio':
            txt_an = (request.form.get('anuncio_global') or '').strip()
            cur.execute("INSERT INTO config_global (clave, valor) VALUES ('anuncio_global', %s) ON CONFLICT(clave) DO UPDATE SET valor=EXCLUDED.valor", (txt_an,))
            msg = "Anuncio global actualizado." if txt_an else "Anuncio global desactivado."
        elif accion == 'limpiar_bd_manual':
            dias = max(1, min(30, a_int_seguro(request.form.get('dias_retencion'), 30)))
            fecha_lim = (datetime.utcnow() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
            fecha_lim_48h = (datetime.utcnow() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("DELETE FROM historial_nube WHERE fecha < %s", (fecha_lim,))
            borrados_h = cur.rowcount
            cur.execute("UPDATE historial_nube SET cadenas=NULL WHERE fecha < %s AND cadenas IS NOT NULL", (fecha_lim_48h,))
            cur.execute("DELETE FROM registro_alertas WHERE fecha < %s", (fecha_lim,))
            cur.execute("DELETE FROM cola_comandos WHERE estado='enviado' AND fecha < %s", (fecha_lim,))
            cur.execute("DELETE FROM mineros_activos WHERE owner NOT IN (SELECT username FROM usuarios)")
            huerfanos = cur.rowcount
            for tbl in ('nombres_mineros', 'logs_mineros', 'historial_nube', 'cola_comandos', 'registro_alertas'):
                cur.execute(f"DELETE FROM {tbl} WHERE owner NOT IN (SELECT username FROM usuarios)")
            msg = f"Limpieza completada: {borrados_h:,} filas antiguas (> {dias}d) y {huerfanos} minero(s) huérfano(s) eliminados."
        elif accion == 'editar_usuario_completo':
            uid = a_int_seguro(request.form.get('user_id'), 0)
            cur.execute("SELECT * FROM usuarios WHERE id=%s", (uid,))
            target = cur.fetchone()
            if target:
                old_user = target['username']
                es_target_admin = (old_user.lower() == 'admin')
                new_user = 'Admin' if es_target_admin else (request.form.get('username') or old_user).strip()
                new_email = (request.form.get('email') or '').strip().lower()
                new_pass = (request.form.get('nueva_password') or '').strip()
                new_plan = 'Pro' if es_target_admin else (request.form.get('plan') or 'Gratis')
                new_susp = 0 if es_target_admin else (1 if request.form.get('suspendido') == '1' else 0)
                new_notas = (request.form.get('notas_admin') or '').strip()

                try:
                    if new_user != old_user and new_user:
                        cur.execute("SELECT id FROM usuarios WHERE LOWER(username)=LOWER(%s) AND id != %s", (new_user, uid))
                        if cur.fetchone():
                            raise Exception("Ese nombre de usuario ya está en uso.")
                        cur.execute("UPDATE usuarios SET username=%s WHERE id=%s", (new_user, uid))
                        for tbl, col in [('mineros_activos', 'owner'), ('nombres_mineros', 'owner'),
                                         ('logs_mineros', 'owner'), ('historial_nube', 'owner'),
                                         ('cola_comandos', 'owner'), ('registro_alertas', 'owner'),
                                         ('movimientos_saldo', 'username')]:
                            cur.execute(f"UPDATE {tbl} SET {col}=%s WHERE {col}=%s", (new_user, old_user))

                    extra_plan_sql = ""
                    if new_plan == 'Gratis':
                        extra_plan_sql = ", pro_hasta='', limite_mineros=0"

                    if new_pass:
                        cur.execute(f"UPDATE usuarios SET email=%s, plan=%s, suspendido=%s, notas_admin=%s, password=%s{extra_plan_sql} WHERE id=%s",
                                    (new_email, new_plan, new_susp, new_notas, generate_password_hash(new_pass), uid))
                    else:
                        cur.execute(f"UPDATE usuarios SET email=%s, plan=%s, suspendido=%s, notas_admin=%s{extra_plan_sql} WHERE id=%s",
                                    (new_email, new_plan, new_susp, new_notas, uid))
                    msg = f"Cuenta '{new_user}' actualizada correctamente."
                except Exception as e_upd:
                    msg = f"No se pudo cambiar el nombre (posiblemente ya existe): {e_upd}"
        elif accion == 'regalar_pro':
            uid = a_int_seguro(request.form.get('user_id'), 0)
            dias = max(1, min(3650, a_int_seguro(request.form.get('dias_pro'), 15)))
            lim_m = max(0, min(5000, a_int_seguro(request.form.get('limite_mineros'), 0)))
            nota = (request.form.get('nota') or '').strip()
            cur.execute("SELECT username, pro_hasta FROM usuarios WHERE id=%s", (uid,))
            target = cur.fetchone()
            if target:
                base_dt = datetime.utcnow()
                ph_prev = str(target.get('pro_hasta') or '').strip()
                if ph_prev:
                    try:
                        dt_p = datetime.strptime(ph_prev, "%Y-%m-%d %H:%M:%S")
                        if dt_p > base_dt:
                            base_dt = dt_p
                    except:
                        pass
                nueva_exp = (base_dt + timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("UPDATE usuarios SET plan='Pro', pro_hasta=%s, limite_mineros=%s WHERE id=%s",
                            (nueva_exp, lim_m, uid))
                lim_txt = f"hasta {lim_m} máquinas" if lim_m > 0 else "máquinas ilimitadas"
                nota_reg = f"Regalo Pro: +{dias} días ({lim_txt})" + (f" · {nota}" if nota else "")
                cur.execute("INSERT INTO movimientos_saldo (username, tipo, monto, nota, fecha) VALUES (%s, 'regalo_pro', 0, %s, %s)",
                            (target['username'], nota_reg, ahora_str))
                msg = f"🎁 Regalo aplicado a '{target['username']}': +{dias} días Pro ({lim_txt}), vence el {nueva_exp[:10]}."
        elif accion == 'movimiento_saldo':
            uid = a_int_seguro(request.form.get('user_id'), 0)
            tipo_mov = request.form.get('tipo_mov', 'deposito')
            monto = abs(a_float_seguro(request.form.get('monto'), 0.0))
            nota = (request.form.get('nota') or '').strip()
            cur.execute("SELECT username, saldo, saldo_bonus FROM usuarios WHERE id=%s", (uid,))
            target = cur.fetchone()
            if target and monto > 0:
                if tipo_mov in ('bonus', 'quitar_bonus'):
                    delta = -monto if tipo_mov == 'quitar_bonus' else monto
                    nuevo_bonus = max(0.0, round(a_float_seguro(target.get('saldo_bonus'), 0.0) + delta, 2))
                    cur.execute("UPDATE usuarios SET saldo_bonus=%s WHERE id=%s", (nuevo_bonus, uid))
                    cur.execute("INSERT INTO movimientos_saldo (username, tipo, monto, nota, fecha) VALUES (%s, %s, %s, %s, %s)",
                                (target['username'], 'credito_promo' if delta >= 0 else 'ajuste_promo', delta, nota or 'Crédito Promocional (No reembolsable)', ahora_str))
                    signo = "+" if delta >= 0 else "-"
                    msg = f"🎁 Crédito Promocional de '{target['username']}' actualizado ({signo}${abs(delta):.2f} USDT). Nuevo Bonus: ${nuevo_bonus:.2f} USDT."
                else:
                    delta = -monto if tipo_mov in ('gasto', 'retiro') else monto
                    nuevo_saldo = round(a_float_seguro(target.get('saldo'), 0.0) + delta, 2)
                    cur.execute("UPDATE usuarios SET saldo=%s WHERE id=%s", (nuevo_saldo, uid))
                    cur.execute("INSERT INTO movimientos_saldo (username, tipo, monto, nota, fecha) VALUES (%s, %s, %s, %s, %s)",
                                (target['username'], tipo_mov, delta, nota, ahora_str))
                    if target['username'] == session.get('usuario'):
                        session['saldo'] = nuevo_saldo
                    signo = "+" if delta >= 0 else "-"
                    msg = f"Saldo de '{target['username']}' actualizado ({signo}${abs(delta):.2f} USDT). Nuevo saldo: ${nuevo_saldo:.2f} USDT."
        elif accion == 'eliminar_usuario':
            uid = a_int_seguro(request.form.get('user_id'), 0)
            cur.execute("SELECT username FROM usuarios WHERE id=%s", (uid,))
            target = cur.fetchone()
            if target and target['username'].lower() != 'admin':
                uname_del = target['username']
                for tbl, col in [
                    ('mineros_activos', 'owner'), ('nombres_mineros', 'owner'),
                    ('logs_mineros', 'owner'), ('historial_nube', 'owner'),
                    ('cola_comandos', 'owner'), ('registro_alertas', 'owner'),
                    ('movimientos_saldo', 'username')
                ]:
                    cur.execute(f"DELETE FROM {tbl} WHERE {col}=%s", (uname_del,))
                cur.execute("DELETE FROM usuarios WHERE id=%s", (uid,))
                msg = f"Cuenta '{uname_del}' y todos sus datos asociados eliminados."

    # 1. Todos los mineros de usuarios existentes para estadísticas globales y mapa
    cur.execute("""
        SELECT m.owner, m.ip, m.modelo, m.hashrate, m.temp_max, m.unidad, m.algoritmo,
               m.ultima_act, m.farm_lat, m.farm_lon, m.farm_city, m.farm_country, m.wd_cuarentena
        FROM mineros_activos m
        INNER JOIN usuarios u ON u.username = m.owner
    """)
    todos_mineros = cur.fetchall()
    datos_red = obtener_datos_red()

    mapa_granjas_dict = {}
    stats_por_usuario = {}
    mineros_online_global = 0
    mineros_offline_global = 0
    hashrate_global = 0.0

    for m in todos_mineros:
        ow = m.get('owner') or 'Admin'
        if ow not in stats_por_usuario:
            stats_por_usuario[ow] = {'total': 0, 'online': 0, 'hr_btc': 0.0, 'gen_dia': 0.0}
        stats_por_usuario[ow]['total'] += 1

        segs = calcular_segundos_desconectado(m.get('ultima_act'))
        es_on = segs < 300
        hr = a_float_seguro(m.get('hashrate'), 0.0) if es_on else 0.0
        tm = a_int_seguro(m.get('temp_max'), 0) if es_on else 0
        unid = m.get('unidad') or 'TH/s'
        algo = m.get('algoritmo') or 'SHA-256'

        if es_on:
            mineros_online_global += 1
            stats_por_usuario[ow]['online'] += 1
            if unid == 'TH/s' and algo == 'SHA-256':
                hashrate_global += hr
                stats_por_usuario[ow]['hr_btc'] += hr
                if datos_red['dificultad_t'] > 0:
                    b_dia = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296)
                    stats_por_usuario[ow]['gen_dia'] += b_dia * datos_red['precio']
        else:
            mineros_offline_global += 1

        ciudad = str(m.get('farm_city') or 'Santa Cruz de la Sierra').strip()
        subred = ".".join(str(m.get('ip') or '').split('.')[:3]) + ".x"
        g_key = f"{ow}|{ciudad}|{subred}"
        if g_key not in mapa_granjas_dict:
            mapa_granjas_dict[g_key] = {
                'owner': ow, 'ciudad': ciudad, 'pais': str(m.get('farm_country') or ''),
                'subred': subred, 'lat': a_float_seguro(m.get('farm_lat'), -17.7833),
                'lon': a_float_seguro(m.get('farm_lon'), -63.1821),
                'total': 0, 'online': 0, 'offline': 0, 'hashrate': 0.0, 'temp_max': 0, 'cuarentena': 0,
                'asics': []
            }
        gd = mapa_granjas_dict[g_key]
        gd['total'] += 1
        if es_on:
            gd['online'] += 1
            gd['hashrate'] = round(gd['hashrate'] + hr, 2)
            gd['temp_max'] = max(gd['temp_max'], tm)
        else:
            gd['offline'] += 1
        if a_int_seguro(m.get('wd_cuarentena'), 0) == 1:
            gd['cuarentena'] += 1
        gd['asics'].append({
            'ip': m.get('ip'),
            'modelo': m.get('modelo') or 'Antminer',
            'online': es_on,
            'hr': round(hr, 1),
            'unidad': unid,
            'temp': tm
        })

    mapa_granjas = []
    for gd in mapa_granjas_dict.values():
        if gd['offline'] > 0 or gd['temp_max'] >= 85 or gd['cuarentena'] > 0:
            gd['color'] = '#ff5252'
            gd['estado_txt'] = 'Atención / Offline'
        elif gd['temp_max'] >= 75:
            gd['color'] = '#ffb300'
            gd['estado_txt'] = 'Temperatura Alta'
        else:
            gd['color'] = '#00e676'
            gd['estado_txt'] = 'Óptima'
        mapa_granjas.append(gd)

    # 2. Movimientos de saldo
    mes_actual = datetime.utcnow().strftime("%Y-%m")
    cur.execute("SELECT * FROM movimientos_saldo ORDER BY id DESC LIMIT 60")
    movimientos = cur.fetchall()

    cur.execute("""
        SELECT username,
               COALESCE(SUM(CASE WHEN monto > 0 AND tipo NOT IN ('credito_promo', 'ajuste_promo') THEN monto ELSE 0 END), 0) AS total_dep,
               COALESCE(SUM(CASE WHEN monto < 0 AND fecha LIKE %s THEN ABS(monto) ELSE 0 END), 0) AS gasto_mes
        FROM movimientos_saldo
        GROUP BY username
    """, (mes_actual + '%',))
    fin_por_usuario = {r['username']: r for r in cur.fetchall()}

    # 3. Lista de usuarios enriquecida
    cur.execute("SELECT id, username, email, fecha_registro, suspendido, plan, saldo, saldo_bonus, pro_hasta, limite_mineros, notas_admin, ultima_conexion, subnet FROM usuarios ORDER BY id ASC")
    usuarios_raw = cur.fetchall()
    usuarios = []
    por_vencer = []
    for u in usuarios_raw:
        un = u['username']
        es_pro_u, lim_ef, dias_rest = evaluar_plan_usuario(cur, u)
        u['plan'] = 'Pro' if es_pro_u else 'Gratis'
        u['pro_dias'] = dias_rest
        u['limite_efectivo'] = lim_ef
        u['saldo_bonus'] = round(a_float_seguro(u.get('saldo_bonus'), 0.0), 2)
        if dias_rest is not None and 0 < dias_rest <= 3:
            por_vencer.append({'username': un, 'dias': dias_rest, 'fecha': str(u.get('pro_hasta') or '')[:10]})

        sm = stats_por_usuario.get(un, {'total': 0, 'online': 0, 'hr_btc': 0.0, 'gen_dia': 0.0})
        fm = fin_por_usuario.get(un, {'total_dep': 0.0, 'gasto_mes': 0.0})
        u['total_mineros'] = sm['total']
        u['online_mineros'] = sm['online']
        u['hashrate_btc'] = round(sm['hr_btc'], 1)
        u['gen_dia'] = round(sm['gen_dia'], 2)
        u['gen_mes'] = round(sm['gen_dia'] * 30.0, 2)
        u['total_dep'] = round(a_float_seguro(fm.get('total_dep'), 0.0), 2)
        u['gasto_mes'] = round(a_float_seguro(fm.get('gasto_mes'), 0.0), 2)
        usuarios.append(u)

    # 4. Métricas PostgreSQL
    db_mb, hist_filas = 0.0, 0
    try:
        cur.execute("SELECT pg_database_size(current_database()) AS db_bytes")
        row_sz = cur.fetchone()
        if row_sz and row_sz.get('db_bytes'):
            db_mb = round(float(row_sz['db_bytes']) / (1024.0 * 1024.0), 2)
        cur.execute("SELECT COUNT(*) AS cnt FROM historial_nube")
        row_cnt = cur.fetchone()
        if row_cnt:
            hist_filas = int(row_cnt.get('cnt') or 0)
    except Exception as e:
        print(f"Error leyendo métricas BD: {e}")

    cur.execute("SELECT valor FROM config_global WHERE clave='anuncio_global'")
    row_an = cur.fetchone()
    anuncio_actual = (row_an.get('valor') or '') if row_an else ''

    stats = {
        'usuarios': len(usuarios),
        'pro': sum(1 for u in usuarios if u['plan'] == 'Pro'),
        'granjas': len(mapa_granjas),
        'mineros': len(todos_mineros),
        'mineros_online': mineros_online_global,
        'mineros_offline': mineros_offline_global,
        'hashrate_global': round(hashrate_global, 1),
        'saldo_total': sum(a_float_seguro(u['saldo'], 0.0) for u in usuarios),
        'bonus_total': sum(a_float_seguro(u['saldo_bonus'], 0.0) for u in usuarios),
        'db_mb': db_mb,
        'db_pct': min(100.0, round((db_mb / 1024.0) * 100.0, 1)),
        'hist_filas': hist_filas
    }
    return render_seguro(
        'admin.html', active_page='admin', usuarios=usuarios, stats=stats,
        movimientos=movimientos, mapa_granjas_json=json.dumps(mapa_granjas),
        anuncio_actual=anuncio_actual, por_vencer=por_vencer, msg=msg
    )
