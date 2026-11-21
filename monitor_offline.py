import json
import threading
import time
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras

from db import DB_URL
from tunel import enviar_orden_tunel
from utils import (
    a_float_seguro,
    a_int_seguro,
    alerta_en_enfriamiento,
    calcular_segundos_desconectado,
    clave_granja_minero,
    enviar_telegram,
)

_monitor_iniciado = False
_sedes_caidas = set()  # Guarda (owner, agent_id) en caída para avisar cuando vuelvan en línea


def _hhmm_a_minutos(hhmm_str, defecto_min):
    try:
        partes = str(hhmm_str or "").strip().split(":")
        h = int(partes[0])
        m = int(partes[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return defecto_min


def _en_rango_horario(min_actual, min_ini, min_fin):
    if min_ini == min_fin:
        return False
    if min_ini < min_fin:
        return min_ini <= min_actual < min_fin
    # Rango nocturno que cruza medianoche (ej. 22:00 a 06:00)
    return min_actual >= min_ini or min_actual < min_fin


def _procesar_curtailment(cur, ahora_utc_str, ahora_bo):
    """Aplica reglas automáticas de Horario de Tarifa Pico (Curtailment) en hora de Bolivia (UTC-4)."""
    cur.execute("""
        SELECT username, tg_token, tg_chat_id, alertas_activas,
               wd_alcance, wd_granjas, wd_mineros,
               curt_inicio, curt_fin, curt_accion, curt_estado_actual
        FROM usuarios
        WHERE COALESCE(curt_activo, 0) = 1
          AND COALESCE(suspendido, 0) = 0
    """)
    usuarios_curt = cur.fetchall()
    min_actual = ahora_bo.hour * 60 + ahora_bo.minute

    for u in usuarios_curt:
        owner = u["username"]
        ini_str = str(u.get("curt_inicio") or "18:00").strip()
        fin_str = str(u.get("curt_fin") or "22:00").strip()
        accion = str(u.get("curt_accion") or "profile|low").strip()
        if accion not in ("profile|low", "sleep"):
            accion = "profile|low"
        estado_prev = str(u.get("curt_estado_actual") or "normal").strip().lower()

        min_ini = _hhmm_a_minutos(ini_str, 18 * 60)
        min_fin = _hhmm_a_minutos(fin_str, 22 * 60)
        en_pico = _en_rango_horario(min_actual, min_ini, min_fin)

        nuevo_estado = "pico" if en_pico else "normal"
        if nuevo_estado == estado_prev:
            continue

        alcance = u.get("wd_alcance") or "all"
        try:
            granjas_sel = json.loads(u.get("wd_granjas") or "[]")
        except Exception:
            granjas_sel = []
        try:
            mineros_sel = json.loads(u.get("wd_mineros") or "[]")
        except Exception:
            mineros_sel = []

        cur.execute(
            "SELECT ip, agent_id, farm_city FROM mineros_activos WHERE owner=%s",
            (owner,),
        )
        filas = cur.fetchall()
        cmd_a_enviar = accion if en_pico else "normal"
        afectados = 0

        for fm in filas:
            ip_m = fm["ip"]
            ag_m = str(fm.get("agent_id") or "default").strip() or "default"
            clave_g = clave_granja_minero(ip_m, fm.get("farm_city"))
            aplica = (
                alcance == "all"
                or (alcance == "farms" and clave_g in granjas_sel)
                or (alcance == "miners" and ip_m in mineros_sel)
            )
            if not aplica:
                continue

            enviado = enviar_orden_tunel(owner, ip_m, cmd_a_enviar, agent_id=ag_m)
            est_cmd = "enviado" if enviado else "pendiente"
            cur.execute(
                "INSERT INTO cola_comandos (owner, agent_id, ip, comando, estado, fecha) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (owner, ag_m, ip_m, cmd_a_enviar, est_cmd, ahora_utc_str),
            )
            afectados += 1

        cur.execute(
            "UPDATE usuarios SET curt_estado_actual=%s WHERE username=%s",
            (nuevo_estado, owner),
        )

        alertas_on = bool(a_int_seguro(u.get("alertas_activas"), 0) == 1)
        tg_tok = (u.get("tg_token") or "").strip()
        tg_cid = (u.get("tg_chat_id") or "").strip()
        etiqueta_acc = "LOW POWER" if accion == "profile|low" else "SLEEP (REPOSO)"

        if en_pico:
            msg_db = f"⚡ TARIFA PICO ({ini_str}-{fin_str}): {afectados} equipo(s) pasados a {etiqueta_acc}."
            cur.execute(
                "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) "
                "VALUES (%s, 'TARIFA_PICO', 'HORARIO_PICO', %s, %s)",
                (owner, msg_db, ahora_utc_str),
            )
            if alertas_on and tg_tok and tg_cid:
                enviar_telegram(
                    tg_tok,
                    tg_cid,
                    f"⚡ <b>HORARIO DE TARIFA PICO ACTIVADO</b>\n"
                    f"Horario: <b>{ini_str} a {fin_str}</b> (Hora Bolivia)\n"
                    f"Acción aplicada: <b>{etiqueta_acc}</b>\n"
                    f"Equipos actualizados: <b>{afectados}</b>",
                )
        else:
            msg_db = f"🟢 FIN TARIFA PICO ({fin_str}): {afectados} equipo(s) devueltos a NORMAL."
            cur.execute(
                "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) "
                "VALUES (%s, 'TARIFA_PICO', 'HORARIO_NORMAL', %s, %s)",
                (owner, msg_db, ahora_utc_str),
            )
            if alertas_on and tg_tok and tg_cid:
                enviar_telegram(
                    tg_tok,
                    tg_cid,
                    f"🟢 <b>FIN DE TARIFA PICO — MODO NORMAL</b>\n"
                    f"Equipos restaurados a <b>NORMAL</b>: <b>{afectados}</b>",
                )


def _enviar_resumen_diario(cur, owner, tg_tok, tg_cid, umbral_t, ahora_utc_str, ahora_bo):
    """Envía el resumen ejecutivo diario a las 8:00 AM hora de Bolivia (UTC-4)."""
    if alerta_en_enfriamiento(cur, owner, "RESUMEN_DIARIO", "RESUMEN_DIARIO", 72000):
        return

    cur.execute("""
        SELECT ip, modelo, hashrate, unidad, temp_max, consumo_w,
               ultima_act, wd_cuarentena, wd_motivo_fallo, prot_motivo
        FROM mineros_activos
        WHERE owner = %s
    """, (owner,))
    mineros = cur.fetchall()
    if not mineros:
        return

    total = len(mineros)
    online = 0
    offline = 0
    consumo_w_total = 0.0
    temp_max_granja = 0
    hr_por_unidad = {}
    en_alerta = []

    for m in mineros:
        segs = calcular_segundos_desconectado(m.get("ultima_act"))
        ip_m = m.get("ip", "?")
        mod_m = str(m.get("modelo") or "ASIC").strip()
        hr_m = a_float_seguro(m.get("hashrate"), 0.0)
        unid = str(m.get("unidad") or "TH/s").strip()
        temp_m = a_int_seguro(m.get("temp_max"), 0)
        cons_m = a_float_seguro(m.get("consumo_w"), 0.0)
        cuar = a_int_seguro(m.get("wd_cuarentena"), 0)
        mot_wd = str(m.get("wd_motivo_fallo") or "").strip()
        mot_pr = str(m.get("prot_motivo") or "").strip()

        if segs < 300:
            online += 1
            consumo_w_total += cons_m
            if temp_m > temp_max_granja:
                temp_max_granja = temp_m
            hr_por_unidad[unid] = hr_por_unidad.get(unid, 0.0) + hr_m

            if cuar == 1:
                en_alerta.append(f"• <b>{ip_m}</b> ({mod_m}): 🔒 Cuarentena ({mot_wd or 'fallo repetido'})")
            elif mot_pr:
                en_alerta.append(f"• <b>{ip_m}</b> ({mod_m}): 🛡️ {mot_pr}")
            elif temp_m >= umbral_t:
                en_alerta.append(f"• <b>{ip_m}</b> ({mod_m}): 🔥 {temp_m}°C")
            elif hr_m <= 0.01:
                en_alerta.append(f"• <b>{ip_m}</b> ({mod_m}): ⚠️ 0 {unid}")
        else:
            offline += 1
            en_alerta.append(f"• <b>{ip_m}</b> ({mod_m}): 🔴 Desconectado")

    hr_texto = " | ".join(f"<b>{round(val, 2)} {u}</b>" for u, val in hr_por_unidad.items()) or "<b>0 TH/s</b>"
    kw_actual = consumo_w_total / 1000.0
    kwh_dia = kw_actual * 24.0
    fecha_bo_str = ahora_bo.strftime("%d/%m/%Y %H:%M")

    lineas = [
        f"☀️ <b>RESUMEN DIARIO — EL MINERO GRIS</b>",
        f"📅 {fecha_bo_str} (Hora Bolivia)",
        "",
        f"🖥️ Equipos: <b>{online} en línea</b> / {total} totales" + (f" ({offline} offline)" if offline > 0 else ""),
        f"⚡ Hashrate total: {hr_texto}",
        f"🔌 Consumo actual: <b>{kw_actual:.2f} kW</b> (~{kwh_dia:.1f} kWh/día)",
        f"🌡️ Temp. máxima: <b>{temp_max_granja}°C</b>",
    ]

    if en_alerta:
        lineas.append("")
        lineas.append(f"⚠️ <b>Equipos con atención ({len(en_alerta)}):</b>")
        lineas.extend(en_alerta[:12])
        if len(en_alerta) > 12:
            lineas.append(f"<i>... y {len(en_alerta) - 12} equipo(s) más.</i>")
    else:
        lineas.append("")
        lineas.append("✅ <b>Toda la granja opera sin alertas.</b>")

    mensaje_tg = "\n".join(lineas)
    enviar_telegram(tg_tok, tg_cid, mensaje_tg)

    cur.execute(
        "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) "
        "VALUES (%s, 'RESUMEN_DIARIO', 'RESUMEN_DIARIO', %s, %s)",
        (owner, f"Resumen diario enviado ({online}/{total} online, {kw_actual:.2f} kW).", ahora_utc_str),
    )


def _ciclo_vigilancia():
    # Espera inicial de 60s al arrancar Render para evitar falsas alarmas tras un deploy
    time.sleep(60)
    while True:
        con = None
        try:
            if not DB_URL:
                time.sleep(180)
                continue

            con = psycopg2.connect(DB_URL)
            con.autocommit = True
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            ahora_utc = datetime.utcnow()
            ahora_str = ahora_utc.strftime("%Y-%m-%d %H:%M:%S")
            ahora_bo = ahora_utc - timedelta(hours=4)  # Bolivia (UTC-4)
            es_hora_resumen = (ahora_bo.hour == 8 and ahora_bo.minute < 30)

            # 0. Horarios de Energía (Tarifa Pico / Curtailment)
            try:
                _procesar_curtailment(cur, ahora_str, ahora_bo)
            except Exception as err_curt:
                print(f"[Curtailment] Error en ciclo: {err_curt}")

            cur.execute("""
                SELECT username, tg_token, tg_chat_id, umbral_temp
                FROM usuarios
                WHERE alertas_activas = 1
                  AND COALESCE(suspendido, 0) = 0
                  AND tg_token IS NOT NULL AND tg_token != ''
                  AND tg_chat_id IS NOT NULL AND tg_chat_id != ''
            """)
            usuarios = cur.fetchall()

            for u in usuarios:
                owner = u["username"]
                tg_tok = u["tg_token"]
                tg_cid = u["tg_chat_id"]
                umbral_t = a_int_seguro(u.get("umbral_temp"), 80)

                # 1. Resumen diario automático a las 8:00 AM (Hora Bolivia)
                if es_hora_resumen:
                    try:
                        _enviar_resumen_diario(cur, owner, tg_tok, tg_cid, umbral_t, ahora_str, ahora_bo)
                    except Exception as err_res:
                        print(f"[Resumen Diario] Error para {owner}: {err_res}")

                # 2. Vigilancia de cortes de luz o internet por sede
                cur.execute("""
                    SELECT
                        COALESCE(NULLIF(agent_id, ''), 'default') AS sede,
                        MAX(ultima_act) AS ultimo_reporte,
                        COUNT(*) AS total_mineros,
                        MAX(farm_city) AS ciudad
                    FROM mineros_activos
                    WHERE owner = %s
                    GROUP BY COALESCE(NULLIF(agent_id, ''), 'default')
                """, (owner,))
                sedes = cur.fetchall()

                for s in sedes:
                    sede = s["sede"]
                    ultimo_rep = s.get("ultimo_reporte")
                    total_m = a_int_seguro(s.get("total_mineros"), 0)
                    ciudad = str(s.get("ciudad") or "Granja Local").strip()
                    if not ultimo_rep or total_m <= 0:
                        continue

                    segs = calcular_segundos_desconectado(ultimo_rep)
                    clave = (owner, sede)

                    # Caída detectada: entre 5 min (300s) y 24 horas (86400s) sin reportes
                    if 300 <= segs <= 86400:
                        if not alerta_en_enfriamiento(cur, owner, sede, "GRANJA_CAIDA", 3600):
                            mins = int(segs // 60)
                            msg_db = (
                                f"🚨 GRANJA SIN CONEXIÓN: Sede {sede} ({ciudad}) lleva {mins} min "
                                f"sin reportar ({total_m} equipo(s)). Posible corte de luz o internet."
                            )
                            cur.execute(
                                "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) "
                                "VALUES (%s, %s, 'GRANJA_CAIDA', %s, %s)",
                                (owner, sede, msg_db, ahora_str),
                            )
                            enviar_telegram(
                                tg_tok,
                                tg_cid,
                                f"🚨 <b>ALERTA: GRANJA SIN CONEXIÓN</b>\n"
                                f"Sede: <b>{sede}</b> ({ciudad})\n"
                                f"Equipos afectados: <b>{total_m}</b>\n"
                                f"Último reporte: <b>hace {mins} min</b>\n"
                                f"Posible corte de luz, caída de internet o celular apagado.",
                            )
                            _sedes_caidas.add(clave)

                    # Recuperación detectada: volvió a reportar en los últimos 3 minutos
                    elif segs < 180 and clave in _sedes_caidas:
                        _sedes_caidas.discard(clave)
                        msg_ok = f"🟢 GRANJA RECONECTADA: Sede {sede} ({ciudad}) volvió en línea ({total_m} equipo(s))."
                        cur.execute(
                            "INSERT INTO registro_alertas (owner, ip, tipo, mensaje, fecha) "
                            "VALUES (%s, %s, 'GRANJA_ONLINE', %s, %s)",
                            (owner, sede, msg_ok, ahora_str),
                        )
                        enviar_telegram(
                            tg_tok,
                            tg_cid,
                            f"🟢 <b>GRANJA RECONECTADA</b>\n"
                            f"Sede: <b>{sede}</b> ({ciudad})\n"
                            f"Equipos reportando: <b>{total_m}</b>",
                        )

        except Exception as e:
            print(f"[Monitor Offline] Error en ciclo: {e}")
        finally:
            if con:
                try:
                    con.close()
                except Exception:
                    pass

        time.sleep(180)  # Revisar cada 3 minutos


def iniciar_monitor_offline():
    global _monitor_iniciado
    if _monitor_iniciado:
        return
    _monitor_iniciado = True
    hilo = threading.Thread(target=_ciclo_vigilancia, name="MonitorGranjaCaida", daemon=True)
    hilo.start()
