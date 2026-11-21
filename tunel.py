from flask_sock import Sock
from flask import request, session, Response, redirect
import base64, ipaddress, json, re, threading, uuid, psycopg2, psycopg2.extras
from db import DB_URL
from utils import login_required

sock = Sock()
_lock = threading.Lock()
# Guarda conexiones activas: { (owner_lower, agent_id): ws }
agentes_conectados = {}
# Peticiones HTTP en vuelo por el túnel: { req_id: {"event": Event, "resp": dict|None} }
_proxy_pendientes = {}

def tunel_esta_activo(owner, agent_id='default', estricto=False):
    """Retorna True si el agente tiene el túnel WebSocket activo en este momento."""
    owner_k = str(owner or 'Admin').strip().lower()
    ag_k = str(agent_id or 'default').strip()
    with _lock:
        if ag_k and ag_k != 'default':
            if (owner_k, ag_k) in agentes_conectados:
                return True
            if estricto:
                return False
        for k in agentes_conectados.keys():
            if k[0] == owner_k:
                return True
    return False

def _es_ip_local_privada(ip_str):
    try:
        ip_obj = ipaddress.ip_address(str(ip_str or '').strip())
        return ip_obj.version == 4 and ip_obj.is_private and not ip_obj.is_loopback and not ip_obj.is_link_local
    except Exception:
        return False

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

def _resolver_agent_id_para_ip(owner, ip):
    if not DB_URL:
        return 'default'
    con = None
    try:
        con = psycopg2.connect(DB_URL, connect_timeout=5)
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT agent_id FROM mineros_activos WHERE owner=%s AND (ip=%s OR gateway=%s) ORDER BY ultima_act DESC LIMIT 1",
            (owner, ip, ip)
        )
        row = cur.fetchone()
        if row and row.get('agent_id'):
            return str(row['agent_id']).strip()
    except Exception:
        pass
    finally:
        if con is not None and con.closed == 0:
            try: con.close()
            except: pass
    return 'default'

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

def enviar_peticion_proxy(owner, ip, method, path, headers=None, body_b64="", scheme="http", agent_id="default", timeout=18.0):
    """Envía una petición HTTP local a través del túnel WebSocket y espera la respuesta del agente."""
    if not _es_ip_local_privada(ip):
        return {"error": "IP no permitida (solo red local privada)."}

    owner_k = str(owner or 'Admin').strip().lower()
    ag_k = str(agent_id or 'default').strip()

    ws_destino = None
    clave_destino = None
    with _lock:
        if ag_k != 'default' and (owner_k, ag_k) in agentes_conectados:
            clave_destino = (owner_k, ag_k)
            ws_destino = agentes_conectados[clave_destino]
        else:
            for k, ws in list(agentes_conectados.items()):
                if k[0] == owner_k:
                    clave_destino = k
                    ws_destino = ws
                    break

    if ws_destino is None:
        return {"error": "No hay ningún Agente Android conectado al túnel en tiempo real para esta cuenta."}

    req_id = uuid.uuid4().hex
    ev = threading.Event()
    with _lock:
        _proxy_pendientes[req_id] = {"event": ev, "resp": None}

    payload = json.dumps({
        "tipo": "http_req",
        "req_id": req_id,
        "ip": ip,
        "scheme": "https" if scheme == "https" else "http",
        "method": method,
        "path": path,
        "headers": headers or {},
        "body_b64": body_b64 or ""
    })

    try:
        ws_destino.send(payload)
    except Exception:
        with _lock:
            _proxy_pendientes.pop(req_id, None)
            if clave_destino:
                agentes_conectados.pop(clave_destino, None)
        return {"error": "Se perdió la conexión con el túnel del Agente."}

    recibido = ev.wait(timeout)
    with _lock:
        entry = _proxy_pendientes.pop(req_id, None)

    if not recibido or not entry or not entry.get("resp"):
        return {"error": "Tiempo de espera agotado esperando respuesta del equipo local."}

    return entry["resp"]

def _construir_interceptor_js(prefix):
    return f"""<script>
(function(){{
  var P = "{prefix}";
  function fixUrl(u){{
    if (typeof u !== "string") return u;
    if (u.startsWith("/") && !u.startsWith("//") && !u.startsWith(P + "/") && u !== P) {{
      return P + u;
    }}
    return u;
  }}
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url){{
    var args = Array.prototype.slice.call(arguments);
    args[1] = fixUrl(url);
    return origOpen.apply(this, args);
  }};
  if (window.fetch) {{
    var origFetch = window.fetch;
    window.fetch = function(input, init){{
      if (typeof input === "string") {{
        input = fixUrl(input);
      }} else if (input && typeof input.url === "string") {{
        var fixed = fixUrl(input.url);
        if (fixed !== input.url) {{
          input = new Request(fixed, input);
        }}
      }}
      return origFetch.call(this, input, init);
    }};
  }}
}})();
</script>"""

def _reescribir_contenido_proxy(body_bytes, content_type, prefix):
    ct = (content_type or "").lower()
    if not any(t in ct for t in ("text/html", "text/css", "javascript", "application/json")):
        return body_bytes
    try:
        txt = body_bytes.decode("utf-8", errors="ignore")
        if "text/html" in ct:
            txt = re.sub(r'(?i)(href|src|action)=([\'"])/(?!/)', rf'\1=\2{prefix}/', txt)
            txt = re.sub(r'(?i)url\(([\'"]?)/(?!/)', rf'url(\1{prefix}/', txt)
            txt = re.sub(r'(?i)(content=[\'"][^\'"]*url=)/(?!/)', rf'\1{prefix}/', txt)
            for ruta in ('/cgi-bin/', '/webpages/', '/data/'):
                txt = txt.replace(f'"{ruta}', f'"{prefix}{ruta}').replace(f"'{ruta}", f"'{prefix}{ruta}")
            interceptor = _construir_interceptor_js(prefix)
            if re.search(r'(?i)<head[^>]*>', txt):
                txt = re.sub(r'(?i)(<head[^>]*>)', rf'\1{interceptor}', txt, count=1)
            else:
                txt = interceptor + txt
        elif "text/css" in ct:
            txt = re.sub(r'(?i)url\(([\'"]?)/(?!/)', rf'url(\1{prefix}/', txt)
        elif "javascript" in ct:
            for ruta in ('/cgi-bin/', '/webpages/', '/data/'):
                txt = txt.replace(f'"{ruta}', f'"{prefix}{ruta}').replace(f"'{ruta}", f"'{prefix}{ruta}")
        return txt.encode("utf-8")
    except Exception:
        return body_bytes

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
                try:
                    data = json.loads(msg)
                    if isinstance(data, dict) and data.get("tipo") == "http_resp":
                        req_id = data.get("req_id")
                        with _lock:
                            entry = _proxy_pendientes.get(req_id)
                        if entry:
                            entry["resp"] = data
                            entry["event"].set()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            with _lock:
                if agentes_conectados.get(clave) is ws:
                    agentes_conectados.pop(clave, None)
            print(f"[WS] Agente desconectado: {owner} ({agent_id})")

    @app.route('/proxy/<ip>', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    @app.route('/proxy/<ip>/', defaults={'subpath': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    @app.route('/proxy/<ip>/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    @login_required
    def proxy_web_local(ip, subpath):
        if request.path == f'/proxy/{ip}':
            qs = request.query_string.decode('utf-8', errors='ignore')
            return redirect(f'/proxy/{ip}/' + (f'?{qs}' if qs else ''))

        owner = session.get('usuario', 'Admin')
        if not _es_ip_local_privada(ip):
            return "IP no permitida. Solo se permiten direcciones privadas de red local.", 403

        scheme = (request.args.get('scheme') or request.cookies.get('emg_proxy_scheme') or 'http').strip().lower()
        if scheme not in ('http', 'https'):
            scheme = 'http'

        agent_id = (request.args.get('agent_id') or '').strip() or _resolver_agent_id_para_ip(owner, ip)

        qs_pairs = [(k, v) for k, v in request.args.items(multi=True) if k not in ('scheme', 'agent_id')]
        qs_clean = "&".join(f"{k}={v}" for k, v in qs_pairs)
        target_path = "/" + subpath + (f"?{qs_clean}" if qs_clean else "")

        fwd_headers = {}
        for h_name in ('Accept', 'Content-Type', 'Authorization', 'X-Requested-With', 'Cookie'):
            val = request.headers.get(h_name)
            if val:
                fwd_headers[h_name] = val
        fwd_headers['User-Agent'] = request.headers.get('User-Agent', 'Mozilla/5.0')
        fwd_headers['Referer'] = f'{scheme}://{ip}/'

        raw_body = request.get_data() or b""
        body_b64 = base64.b64encode(raw_body).decode('ascii') if raw_body else ""

        resp = enviar_peticion_proxy(
            owner=owner,
            ip=ip,
            method=request.method,
            path=target_path,
            headers=fwd_headers,
            body_b64=body_b64,
            scheme=scheme,
            agent_id=agent_id,
            timeout=18.0
        )

        if resp.get("error"):
            html_err = f"""
            <div style="background:#0d0f17;color:#fff;font-family:sans-serif;padding:36px;min-height:100vh;">
                <h2 style="color:#ff5252;">⚠️ Proxy Web Local — {ip}</h2>
                <p style="color:#9499b0;">{resp['error']}</p>
                <p style="font-size:0.9em;color:#cbd5e1;">Si es un router que usa HTTPS, prueba cambiar el protocolo:</p>
                <a href="/proxy/{ip}/?scheme={'http' if scheme=='https' else 'https'}" style="display:inline-block;background:#3366ff;color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:bold;margin-right:8px;">
                    🔄 Probar con {'HTTP' if scheme=='https' else 'HTTPS'}
                </a>
                <a href="/" style="display:inline-block;background:#25283a;color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none;">
                    ↩ Volver al Panel
                </a>
            </div>
            """
            return Response(html_err, status=502, mimetype="text/html")

        status_code = int(resp.get("status") or 200)
        resp_headers = resp.get("headers") or {}
        try:
            body_bytes = base64.b64decode(resp.get("body_b64") or "")
        except Exception:
            body_bytes = b""

        prefix = f"/proxy/{ip}"
        content_type = ""
        for k, v in resp_headers.items():
            if str(k).lower() == "content-type":
                content_type = str(v)
                break

        body_bytes = _reescribir_contenido_proxy(body_bytes, content_type, prefix)
        flask_resp = Response(body_bytes, status=status_code)

        if content_type:
            flask_resp.headers["Content-Type"] = content_type

        for k, v in resp_headers.items():
            kl = str(k).lower()
            if kl in ("content-type", "content-length", "transfer-encoding", "connection", "content-encoding"):
                continue
            if kl == "location":
                loc = str(v)
                if loc.startswith(f"http://{ip}"):
                    loc = prefix + loc[len(f"http://{ip}"):]
                elif loc.startswith(f"https://{ip}"):
                    loc = prefix + loc[len(f"https://{ip}"):] + ("&scheme=https" if "?" in loc else "?scheme=https")
                elif loc.startswith("/"):
                    loc = prefix + loc
                flask_resp.headers["Location"] = loc
            elif kl == "set-cookie":
                cookies_list = v if isinstance(v, list) else [v]
                for ck in cookies_list:
                    ck_clean = re.sub(r'(?i);\s*Domain=[^;]+', '', str(ck))
                    ck_clean = re.sub(r'(?i);\s*Secure\b', '', ck_clean)
                    if re.search(r'(?i);\s*Path=', ck_clean):
                        ck_clean = re.sub(r'(?i);\s*Path=[^;]+', f'; Path={prefix}/', ck_clean)
                    else:
                        ck_clean = f"{ck_clean}; Path={prefix}/"
                    flask_resp.headers.add("Set-Cookie", ck_clean)
            elif kl in ("www-authenticate", "cache-control", "etag"):
                flask_resp.headers[k] = str(v)

        if request.args.get('scheme'):
            flask_resp.set_cookie('emg_proxy_scheme', scheme, path=f'{prefix}/', max_age=3600)

        return flask_resp
