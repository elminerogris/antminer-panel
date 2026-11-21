from flask_sock import Sock
from flask import request
import json, threading, psycopg2, psycopg2.extras
from db import DB_URL

sock = Sock()
_lock = threading.Lock()
# Guarda conexiones activas: { (owner_lower, agent_id): ws }
agentes_conectados = {}

def _verificar_api_key(api_key):
    if not api_key or not DB_URL:
        return None
    con = None
    try:
        con = psycopg2.connect(DB_URL, connect_timeout=6)
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT username, suspendido FROM usuarios WHERE api_key=%s", (api_key,))
        row = cur.fetchone()
        if row and int(row.get('suspendido') or 0) == 0:
            return row['username']
    except Exception as e:
        print(f"Error auth WS: {e}")
    finally:
        if con is not None and con.closed == 0:
            try: con.close()
            except: pass
    return None

def enviar_orden_tunel(owner, ip, comando, agent_id='default'):
    """Envía una orden instantánea (< 1s) si el agente está conectado al túnel."""
    owner_k = str(owner or 'Admin').strip().lower()
    ag_k = str(agent_id or 'default').strip()
    payload = json.dumps({"ordenes": [{"ip": ip, "comando": comando}]})
    enviado = False

    with _lock:
        destinos = []
        if ag_k != 'default' and (owner_k, ag_k) in agentes_conectados:
            destinos.append(((owner_k, ag_k), agentes_conectados[(owner_k, ag_k)]))
        else:
            for k, ws in list(agentes_conectados.items()):
                if k[0] == owner_k:
                    destinos.append((k, ws))

    for k, ws in destinos:
        try:
            ws.send(payload)
            enviado = True
        except Exception:
            with _lock:
                agentes_conectados.pop(k, None)
    return enviado

def init_tunel(app):
    sock.init_app(app)

    @sock.route('/ws/agente')
    def ws_agente(ws):
        api_key = (request.args.get('api_key') or '').strip()
        agent_id = (request.args.get('agent_id') or 'default').strip()[:40] or 'default'

        owner = _verificar_api_key(api_key)
        if not owner:
            ws.close(1008, "API Key invalida")
            return

        clave = (owner.lower(), agent_id)
        with _lock:
            agentes_conectados[clave] = ws
        print(f"[WS] Agente conectado: {owner} ({agent_id})")

        try:
            while True:
                msg = ws.receive()
                if msg is None:
                    break
                # Si el APK envía hello o ack, simplemente mantenemos vivo el canal
        except Exception:
            pass
        finally:
            with _lock:
                if agentes_conectados.get(clave) is ws:
                    agentes_conectados.pop(clave, None)
            print(f"[WS] Agente desconectado: {owner} ({agent_id})")
