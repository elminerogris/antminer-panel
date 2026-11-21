import threading
import time
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras

from db import DB_URL
from utils import (
    a_float_seguro,
    a_int_seguro,
    alerta_en_enfriamiento,
    calcular_segundos_desconectado,
    enviar_telegram,
)

_monitor_iniciado = False
_sedes_caidas = set()  # Guarda (owner, agent_id) en caída para avisar cuando vuelvan en línea


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

            cur.execute("""
                SELECT username, tg_token, tg_chat_id, umbral_temp
                FROM usuarios
                WHERE alertas_activas = 1
                  AND COALESCE(suspendido, 0) = 0
                  AND tg_token IS NOT NULL AND tg_token != ''
                  AND tg_chat_id IS NOT NULL AND tg_chat_id != ''
            """)
            usuarios = cur.fetchall()

            ahora_utc = datetime.utcnow()
            ahora_str = ahora_utc.strftime("%Y-%m-%d %H:%M:%S")
            ahora_bo = ahora_utc - timedelta(hours=4)  # Bolivia (UTC-4)
            es_hora_resumen = (ahora_bo.hour == 8 and ahora_bo.minute < 30)

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
