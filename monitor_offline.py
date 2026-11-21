import threading
import time
from datetime import datetime
import psycopg2
import psycopg2.extras

from db import DB_URL
from utils import (
    a_int_seguro,
    alerta_en_enfriamiento,
    calcular_segundos_desconectado,
    enviar_telegram,
)

_monitor_iniciado = False
_sedes_caidas = set()  # Guarda (owner, agent_id) en caída para avisar cuando vuelvan en línea


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
                SELECT username, tg_token, tg_chat_id
                FROM usuarios
                WHERE alertas_activas = 1
                  AND COALESCE(suspendido, 0) = 0
                  AND tg_token IS NOT NULL AND tg_token != ''
                  AND tg_chat_id IS NOT NULL AND tg_chat_id != ''
            """)
            usuarios = cur.fetchall()
            ahora_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            for u in usuarios:
                owner = u["username"]
                tg_tok = u["tg_token"]
                tg_cid = u["tg_chat_id"]

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
