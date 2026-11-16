from flask import Flask, render_template_string, redirect, request, session, url_for, jsonify, Response
from functools import wraps
import psycopg2, psycopg2.extras, json, urllib.request, time, calendar, csv, io, os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secreta_antminer_tools'

DB_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    if not DB_URL: raise Exception("Falta DATABASE_URL")
    return psycopg2.connect(DB_URL)

def init_db():
    if not DB_URL: return
    con = get_db(); c = con.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT UNIQUE, modelo TEXT, hashrate REAL, temp_max INTEGER, consumo_w REAL, estado TEXT, ultima_act TEXT, errores INTEGER, pool1 TEXT, worker1 TEXT, pool2 TEXT, worker2 TEXT, pool3 TEXT, worker3 TEXT, mac TEXT, serial TEXT, firmware TEXT, fans TEXT, cadenas TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, plan TEXT DEFAULT 'Gratis', saldo REAL DEFAULT 0.0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transacciones (id SERIAL PRIMARY KEY, username TEXT, monto REAL, metodo TEXT, estado TEXT, fecha TEXT, txid TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cola_comandos (id SERIAL PRIMARY KEY, ip TEXT, comando TEXT, estado TEXT, fecha TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS historial_nube (id SERIAL PRIMARY KEY, ip TEXT, hashrate REAL, temp_max INTEGER, fecha TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS nombres_mineros (ip TEXT UNIQUE, nombre TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs_mineros (ip TEXT UNIQUE, contenido TEXT, fecha TEXT)''')
    
    # Parches automáticos para la tabla usuarios (por si la tabla ya existía sin estas columnas)
    try: c.execute("ALTER TABLE usuarios ADD COLUMN plan TEXT DEFAULT 'Gratis'")
    except: pass
    try: c.execute("ALTER TABLE usuarios ADD COLUMN saldo REAL DEFAULT 0.0")
    except: pass
    
    con.commit(); con.close()

try: init_db()
except: pass

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- PLANTILLAS HTML: Autenticación ---

HTML_AUTH = """
<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>El Minero Gris - Acceso</title>
<style>
    :root { --bg-body: #14151f; --bg-card: #1b1d29; --border-color: #292a38; --text-main: #fff; --text-muted: #a5a7b5; --accent-blue: #3366ff; --accent-red: #ff5252; --accent-green: #00e676; }
    body { font-family: 'Segoe UI', sans-serif; background: var(--bg-body); color: var(--text-main); display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
    .login-card { background: var(--bg-card); padding: 40px 30px; border-radius: 8px; border: 1px solid var(--border-color); width: 100%; max-width: 350px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    .logo-container { font-weight: bold; font-size: 1.5em; display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 30px; }
    .logo-img { height: 45px; width: auto; object-fit: contain; }
    .logo-text span { color: var(--text-muted); font-weight: normal; }
    .form-group { margin-bottom: 20px; }
    .form-group label { display: block; font-size: 0.85em; color: var(--text-muted); margin-bottom: 8px; }
    .form-group input { width: 100%; background: var(--bg-body); border: 1px solid var(--border-color); color: var(--text-main); padding: 12px; border-radius: 4px; font-size: 1em; box-sizing: border-box; }
    .btn-login { width: 100%; background: var(--accent-blue); color: white; border: none; padding: 12px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.2s; margin-top: 10px; }
    .btn-login:hover { background: #254ee6; }
    .link-auth { display: block; text-align: center; margin-top: 20px; font-size: 0.85em; color: var(--accent-blue); text-decoration: none; }
    .link-auth:hover { text-decoration: underline; }
</style></head>
<body>
    <div class="login-card">
        <div class="logo-container"><img src="/static/logo.png" alt="Logo" class="logo-img"><div class="logo-text">El Minero <span>Gris</span></div></div>
        {% if error %}<div style="color:var(--accent-red); margin-bottom:15px; text-align:center; font-size:0.9em; font-weight:bold;">{{ error }}</div>{% endif %}
        {% if success %}<div style="color:var(--accent-green); margin-bottom:15px; text-align:center; font-size:0.9em; font-weight:bold;">{{ success }}</div>{% endif %}
        
        <form method="POST">
            <div class="form-group"><label>Nombre de Usuario</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Contraseña</label><input type="password" name="password" required></div>
            <button type="submit" class="btn-login">{{ 'Registrarse' if mode == 'register' else 'Ingresar al Panel' }}</button>
        </form>
        {% if mode == 'login' %}
            <a href="/register" class="link-auth">¿No tienes cuenta? Regístrate aquí</a>
        {% else %}
            <a href="/login" class="link-auth">¿Ya tienes cuenta? Inicia sesión</a>
        {% endif %}
    </div>
</body></html>
"""

# --- PLANTILLAS HTML: Layout, Resumen y Detalle permanecen igual (Minificadas para espacio) ---
HTML_LAYOUT = """<!DOCTYPE html><html lang="es" data-theme="dark"><head><meta charset="UTF-8"><title>El Minero Gris</title><script type="text/javascript" src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script><script src="https://cdn.jsdelivr.net/npm/chart.js"></script><style>:root[data-theme="dark"]{--bg-body:#14151f;--bg-sidebar:#191a24;--bg-card:#1b1d29;--bg-input:#14151f;--border-color:#292a38;--text-main:#fff;--text-muted:#a5a7b5;--text-sub:#8b8d9b;--accent-blue:#3366ff;--accent-green:#00e676;--accent-red:#ff5252;--hover-bg:#202230;--active-bg:#232533}:root[data-theme="light"]{--bg-body:#f4f5f7;--bg-sidebar:#ffffff;--bg-card:#ffffff;--bg-input:#f4f5f7;--border-color:#e2e8f0;--text-main:#1e293b;--text-muted:#64748b;--text-sub:#94a3b8;--accent-blue:#2563eb;--accent-green:#10b981;--accent-red:#ef4444;--hover-bg:#f8fafc;--active-bg:#f1f5f9}body{font-family:'Segoe UI',sans-serif;background:var(--bg-body);color:var(--text-main);margin:0;display:flex;height:100vh;overflow:hidden;transition:0.3s;top:0 !important}.skiptranslate iframe{display:none !important}body{top:0px !important}#google_translate_element{display:none}a{text-decoration:none;color:inherit}.sidebar{width:220px;background:var(--bg-sidebar);border-right:1px solid var(--border-color);display:flex;flex-direction:column;padding-top:15px;z-index:10}.logo-container{padding:10px 20px 20px;font-weight:bold;font-size:1.1em;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--border-color)}.logo-img{height:35px;width:auto;object-fit:contain}.logo-text span{color:var(--text-muted);font-weight:normal}.menu{list-style:none;padding:15px 0;margin:0;flex:1}.menu a{display:flex;align-items:center;gap:12px;padding:12px 20px;font-size:0.9em;color:var(--text-muted);cursor:pointer;transition:0.2s}.menu a:hover{background:var(--hover-bg);color:var(--text-main)}.menu a.active{background:var(--active-bg);color:var(--text-main);border-left:3px solid var(--accent-blue)}.sidebar-bottom{border-top:1px solid var(--border-color);padding:15px 20px;font-size:0.85em;display:flex;flex-direction:column;gap:10px}.right-container{flex:1;display:flex;flex-direction:column;overflow:hidden}.top-bar{height:60px;background:var(--bg-body);border-bottom:1px solid var(--border-color);display:flex;justify-content:flex-end;align-items:center;padding:0 30px;gap:15px}.top-btn,.top-lang-select{background:var(--bg-card);border:1px solid var(--border-color);color:var(--text-main);height:36px;border-radius:6px;cursor:pointer;transition:0.2s;font-size:0.85em;font-weight:bold;display:flex;align-items:center;justify-content:center}.top-btn{width:36px;font-size:1.1em;color:var(--text-muted)}.btn-sync{width:auto;padding:0 15px;color:var(--text-main)}.btn-sync:hover{border-color:var(--accent-blue);color:var(--accent-blue)}.top-lang-select{padding:0 10px;outline:none}.top-status{display:flex;align-items:center;gap:15px;font-size:0.85em;color:var(--text-muted);font-weight:bold;margin-left:10px}.status-envivo{color:var(--accent-green);display:flex;align-items:center;gap:6px}.dot-pulse{width:8px;height:8px;background:var(--accent-green);border-radius:50%;animation:pulse 1.5s infinite}@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(0,230,118,0.7)}70%{box-shadow:0 0 0 6px rgba(0,230,118,0)}100%{box-shadow:0 0 0 0 rgba(0,230,118,0)}}.main-content{flex:1;padding:20px 30px;overflow-y:auto}</style></head><body><div id="google_translate_element"></div><div class="sidebar"><div class="logo-container"><img src="/static/logo.png" alt="Logo" class="logo-img"><div class="logo-text" translate="no">El Minero <span>Gris</span></div></div><div class="menu"><a href="/" class="{{ 'active' if active_page == 'resumen' else '' }}">📊 Resumen</a><a href="/plan" class="{{ 'active' if active_page == 'plan' else '' }}">💳 Billetera y Planes</a><a href="/granjas" class="{{ 'active' if active_page == 'granjas' else '' }}">🏭 Granjas</a><a href="/alertas" class="{{ 'active' if active_page == 'alertas' else '' }}">🔔 Alertas</a><a href="/ajustes" class="{{ 'active' if active_page == 'ajustes' else '' }}">⚙️ Ajustes</a></div><div class="sidebar-bottom"><div style="color:var(--text-main);font-weight:bold;display:flex;align-items:center;gap:8px;">👤 {{ session['usuario'] }}</div><div style="color:var(--accent-green); font-size:0.9em; font-weight:bold;">$ {{ "%.2f"|format(session.get('saldo', 0.0)) }} USDT</div><a href="/logout" style="color:var(--accent-red);display:flex;align-items:center;gap:8px;">🚪 Cerrar Sesión</a></div></div><div class="right-container"><div class="top-bar"><button class="top-btn btn-sync" onclick="escanearRed()" id="btnSync">🔄 Escanear Red</button><button class="top-btn" onclick="toggleTheme()" id="themeIcon">☀️</button><select class="top-lang-select" id="langSelector" onchange="cambiarIdioma(this.value)"><option value="es">🌐 Español</option><option value="en">🌐 English</option><option value="zh-CN">🌐 中文</option><option value="ru">🌐 Русский</option></select><div class="top-status"><span id="relojTop" translate="no">00:00</span><span class="status-envivo"><div class="dot-pulse"></div> En vivo</span></div></div><div class="main-content">{{ content | safe }}</div></div><script>function escanearRed(){let b=document.getElementById('btnSync');b.innerText='⏳ Escaneando...';b.style.pointerEvents='none';fetch('/cmd/all/scan').then(()=>{setTimeout(()=>{window.location.reload();},1500);});}function googleTranslateElementInit(){new google.translate.TranslateElement({pageLanguage:'es',autoDisplay:false},'google_translate_element');}function cambiarIdioma(lang){let s=document.querySelector(".goog-te-combo");if(s){s.value=lang;s.dispatchEvent(new Event("change"));}}function actualizarReloj(){const d=new Date();document.getElementById('relojTop').innerText=d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0');}setInterval(actualizarReloj,1000);actualizarReloj();function toggleTheme(){const h=document.documentElement,i=document.getElementById('themeIcon');if(h.getAttribute('data-theme')==='dark'){h.setAttribute('data-theme','light');i.innerText='🌙';}else{h.setAttribute('data-theme','dark');i.innerText='☀️';}}</script></body></html>"""

# --- PLANTILLA HTML: Facturación y Planes (NUEVO SISTEMA SAAS) ---
HTML_PLAN = """
<style>
    .plan-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }
    .card-dark { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 30px; }
    .plan-title { font-size: 1.5em; font-weight: bold; margin-bottom: 5px; color: var(--text-main); }
    .plan-price { font-size: 2.5em; font-weight: bold; color: var(--accent-blue); margin: 15px 0; }
    .plan-price span { font-size: 0.4em; color: var(--text-muted); font-weight: normal; }
    .plan-features { list-style: none; padding: 0; margin: 20px 0; color: var(--text-muted); line-height: 2; }
    .plan-features li::before { content: '✔️'; margin-right: 10px; color: var(--accent-green); }
    .btn-upgrade { background: var(--accent-blue); color: white; border: none; padding: 12px; border-radius: 4px; font-weight: bold; width: 100%; cursor: pointer; transition: 0.2s; font-size: 1em; }
    .btn-upgrade:hover { background: #254ee6; }
    
    .wallet-card { background: linear-gradient(135deg, #1b1d29 0%, #14151f 100%); border: 1px solid var(--accent-blue); text-align: center; }
    .wallet-balance { font-size: 3em; font-weight: bold; color: var(--accent-green); margin: 20px 0; }
    .deposit-options { display: flex; flex-direction: column; gap: 15px; margin-top: 30px; text-align: left; }
    .deposit-box { background: var(--bg-body); border: 1px dashed var(--border-color); padding: 15px; border-radius: 6px; }
    .qr-box { display: flex; align-items: center; gap: 20px; }
    .qr-img { width: 100px; height: 100px; background: white; padding: 5px; border-radius: 4px; }
    .copy-text { background: var(--bg-card); padding: 8px; border: 1px solid var(--border-color); border-radius: 4px; font-family: monospace; display: flex; justify-content: space-between; color: var(--accent-blue); margin-top: 10px; cursor: pointer;}
</style>

<div style="margin-bottom: 30px;">
    <h1 style="margin:0;">💳 Billetera y Planes</h1>
    <p style="color: var(--text-muted);">Administra tu suscripción, agrega fondos y expande el límite de tu granja.</p>
</div>

<div class="plan-grid">
    <!-- Planes -->
    <div>
        <div class="card-dark" style="margin-bottom: 20px; {{ 'border-color: var(--accent-green);' if current_plan == 'Gratis' else '' }}">
            <div class="plan-title">Plan Gratis (Hobby)</div>
            <div class="plan-price">$0.00 <span>/ mes</span></div>
            <ul class="plan-features">
                <li>Monitoreo de hasta 3 mineros.</li>
                <li>Retención de historial de 24 horas.</li>
                <li>Soporte comunitario.</li>
            </ul>
            <button class="btn-upgrade" style="background:var(--bg-body); color:var(--text-main);" disabled>Plan Actual</button>
        </div>

        <div class="card-dark" style="{{ 'border-color: var(--accent-green);' if current_plan == 'Pro' else '' }}">
            <div class="plan-title">Plan Profesional</div>
            <div class="plan-price">$1.50 <span>/ minero / mes</span></div>
            <ul class="plan-features">
                <li>Mineros ilimitados.</li>
                <li>Acciones Masivas (Bulk Actions).</li>
                <li>Perfiles de Overclock Automático.</li>
                <li>Exportación CSV e Historial 30 días.</li>
            </ul>
            <button class="btn-upgrade" onclick="alert('Funcionalidad de cambio de plan en desarrollo. Contacte al administrador.')">Mejorar a Pro</button>
        </div>
    </div>

    <!-- Billetera / Wallet -->
    <div class="card-dark wallet-card">
        <h2 style="margin-top:0;">Mi Billetera (Saldo)</h2>
        <div class="wallet-balance">${{ "%.2f"|format(saldo) }} <span style="font-size:0.4em; color:var(--text-main);">USDT</span></div>
        <p style="color: var(--text-muted); font-size: 0.9em;">El saldo se debita automáticamente cada mes según tu cantidad de mineros y tu plan activo.</p>
        
        <div class="deposit-options">
            <h3 style="margin-bottom: 0;">⬇️ Agregar Fondos</h3>
            
            <div class="deposit-box">
                <div style="font-weight: bold; color: #FCD535; margin-bottom: 10px;">🟡 Binance Pay (Recomendado)</div>
                <div class="qr-box">
                    <img src="https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=235489710" alt="QR Binance" class="qr-img">
                    <div>
                        <div style="font-size: 0.85em; color: var(--text-muted);">Escanea el código QR desde tu app de Binance o copia el Pay ID.</div>
                        <div class="copy-text" onclick="navigator.clipboard.writeText('235489710'); alert('Pay ID Copiado')">
                            <span>ID: 235489710</span> <span>📋</span>
                        </div>
                    </div>
                </div>
            </div>

            <div class="deposit-box">
                <div style="font-weight: bold; color: var(--accent-green); margin-bottom: 10px;">🟢 Red Tron (TRC20) USDT</div>
                <div style="font-size: 0.85em; color: var(--text-muted);">Envía USDT únicamente por la red TRC20 a esta dirección:</div>
                <div class="copy-text" onclick="navigator.clipboard.writeText('TWMxYQ...TUgMvR'); alert('Dirección Copiada')">
                    <span>TWMxYQ...TUgMvR</span> <span>📋</span>
                </div>
            </div>
            
            <div style="font-size: 0.8em; color: var(--text-sub); text-align: center; margin-top: 10px;">
                Una vez realizado el pago, el saldo se acreditará en tu cuenta en un lapso de 5 a 15 minutos.
            </div>
        </div>
    </div>
</div>
"""

# HTML_RESUMEN y HTML_DETALLE se mantienen como la versión anterior. Para no exceder límites, usarás los que ya te pasé.
HTML_RESUMEN = """<style>.cabecera{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:20px}.cabecera h1{margin:0 0 5px 0;font-size:1.8em}.cabecera .sub{font-size:0.85em;color:var(--text-sub)}.top-stats-grid{display:grid;grid-template-columns:repeat(11,1fr);gap:10px;margin-bottom:25px}.stat-card{background:var(--bg-card);border:1px solid var(--border-color);border-radius:6px;padding:12px 10px;display:flex;flex-direction:column;justify-content:center}.stat-card .lbl{font-size:0.65em;color:var(--text-muted);text-transform:uppercase;font-weight:bold;margin-bottom:6px;display:flex;justify-content:space-between}.stat-card .val{font-size:1.1em;font-weight:bold;color:var(--text-main)}.val-blue{color:var(--accent-blue) !important}.val-green{color:var(--accent-green) !important}.val-red{color:var(--accent-red) !important}.dot{width:8px;height:8px;border-radius:50%;display:inline-block}.dot-green{background:var(--accent-green)}.dot-red{background:var(--accent-red)}.calc-panel{background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:20px;margin-bottom:25px}.calc-title{font-size:1.1em;font-weight:bold;margin-bottom:15px;display:flex;align-items:center;gap:8px}.calc-top-boxes{display:flex;gap:15px;margin-bottom:20px}.box-mini{border:1px solid var(--accent-blue);background:var(--bg-input);border-radius:6px;padding:12px 15px;text-align:center;min-width:120px}.box-mini .v{color:var(--accent-blue);font-weight:bold;font-size:1.1em;margin-bottom:6px}.box-mini .l{color:var(--text-sub);font-size:0.65em;text-transform:uppercase;font-weight:bold}.calc-inputs{display:flex;flex-wrap:wrap;gap:15px;align-items:flex-end;margin-bottom:20px}.calc-input-group{flex:1;min-width:130px}.calc-input-group label{display:block;font-size:0.75em;color:var(--text-muted);margin-bottom:8px;font-weight:bold}.calc-input-group input{width:100%;background:var(--bg-input);border:1px solid var(--border-color);color:var(--text-main);padding:10px;border-radius:4px;font-size:1em;box-sizing:border-box}.btn-aplicar{height:41px;background:rgba(51,102,255,0.1);color:var(--accent-blue);border:1px solid var(--accent-blue);padding:0 25px;border-radius:4px;font-weight:bold;cursor:pointer;transition:0.2s;white-space:nowrap;margin-bottom:1px}.btn-aplicar:hover{background:var(--accent-blue);color:white}.calc-resultados{display:grid;grid-template-columns:repeat(9,1fr);gap:10px;margin-bottom:20px}.res-box{background:var(--bg-input);border:1px solid var(--border-color);border-radius:6px;padding:12px 8px;text-align:center}.res-box .val{font-size:1em;font-weight:bold;margin-bottom:4px}.res-box .lbl{font-size:0.6em;color:var(--text-muted);text-transform:uppercase;font-weight:bold}.calc-text-ribbon{font-size:0.85em;color:var(--text-muted);margin-bottom:5px;padding:5px 0;border-top:1px solid var(--border-color);padding-top:15px}.calc-text-ribbon span.hl{color:var(--text-main);font-weight:bold}.calc-text-ribbon .sep{margin:0 10px;color:var(--border-color)}.bulk-bar{margin-bottom:10px;display:flex;gap:10px}.btn-bulk{background:var(--bg-card);border:1px solid var(--border-color);color:var(--text-main);padding:8px 15px;border-radius:4px;cursor:pointer;font-size:0.85em;font-weight:bold;transition:0.2s}.btn-bulk:hover{border-color:var(--accent-blue);color:var(--accent-blue)}.btn-excel{background:rgba(0,230,118,0.1);border:1px solid var(--accent-green);color:var(--accent-green)}.btn-excel:hover{background:var(--accent-green);color:white}.tabla-container{background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;overflow:hidden}table{width:100%;border-collapse:collapse}th,td{padding:12px 20px;text-align:left;border-bottom:1px solid var(--border-color);font-size:0.85em}th{color:var(--text-muted);text-transform:uppercase;background:var(--bg-card);font-weight:bold;font-size:0.75em}tr.fila-minero{cursor:pointer;transition:0.15s ease;border-bottom:1px solid var(--border-color)}tr.fila-minero:hover{background:var(--hover-bg)}.nombre-link{color:var(--text-main);font-size:1.1em;font-weight:bold}</style><div class="cabecera"><div><h1>Resumen</h1><div class="sub">Estado de la flota en 1 granja(s) · <span id="textoActualizado" style="color:var(--accent-green);">actualizado hace 1s</span></div></div><button class="btn-bulk btn-excel" onclick="window.location.href='/exportar_csv'">📥 Exportar CSV</button></div><div class="top-stats-grid"><div class="stat-card"><div class="lbl">MINEROS ⛏️</div><div class="val val-blue" translate="no">{{ totales }}</div></div><div class="stat-card"><div class="lbl">EN LÍNEA <span class="dot dot-green"></span></div><div class="val val-green" translate="no">{{ en_linea }}</div></div><div class="stat-card"><div class="lbl">SIN CONEXIÓN <span class="dot dot-red"></span></div><div class="val val-red" translate="no">{{ offline }}</div></div><div class="stat-card"><div class="lbl">GRANJAS 🏭</div><div class="val val-blue" translate="no">1</div></div><div class="stat-card"><div class="lbl">ALERTAS 🔔</div><div class="val val-red" translate="no">0</div></div><div class="stat-card"><div class="lbl">HASHRATE ⚡</div><div class="val val-blue" translate="no">{{ flota.hashrate_total }} TH/s</div></div><div class="stat-card"><div class="lbl">CONSUMO 🔌</div><div class="val val-blue" translate="no">{{ flota.consumo_kw }} kW</div></div><div class="stat-card"><div class="lbl">TEMP MÁX 🌡️</div><div class="val val-red" translate="no">{{ flota.temp_max }} °C</div></div><div class="stat-card"><div class="lbl">PRECIO BTC ₿</div><div class="val btc-live-price" translate="no">USD {{ flota.precio_btc }}</div></div><div class="stat-card"><div class="lbl">INGRESOS / DÍA 💰</div><div class="val val-green" translate="no">{{ flota.ingreso_dia }} USD</div></div><div class="stat-card"><div class="lbl">GANANCIA / DÍA 💵</div><div class="val" style="color: {{ '#ff5252' if '-' in flota.ganancia_dia else '#00e676' }};" translate="no">{{ flota.ganancia_dia }}</div></div></div><div class="calc-panel"><div class="calc-title">₿ Calculadora de rentabilidad</div><div class="calc-top-boxes"><div class="box-mini"><div class="v" id="topBtcDia" translate="no">0.00</div><div class="l">BTC/DÍA</div></div><div class="box-mini"><div class="v" id="topBtcSemana" translate="no">0.00</div><div class="l">BTC/SEMANA</div></div><div class="box-mini"><div class="v" id="topBtcMes" translate="no">0.00</div><div class="l">BTC/MES</div></div><div class="box-mini"><div class="v" id="topBtcAnio" translate="no">0.00</div><div class="l">BTC/AÑO</div></div></div><div class="calc-inputs"><div class="calc-input-group"><label>Hashrate (TH/s)</label><input type="number" id="hashrateInput" value="{{ flota.hashrate_total }}"></div><div class="calc-input-group"><label>Consumo (W)</label><input type="number" id="consumoInput" value="{{ flota.consumo_w_total }}"></div><div class="calc-input-group"><label>Luz (USD/kWh)</label><input type="number" step="0.01" id="kwhInput" value="0.00"></div><div class="calc-input-group"><label>Comisión (%)</label><input type="number" step="0.1" id="comisionInput" value="0.0"></div><button class="btn-aplicar" onclick="calcularRentabilidad()">Aplicar</button></div><div class="calc-resultados"><div class="res-box"><div class="val" id="resBtcDia" translate="no">0.00</div><div class="lbl">BTC/DÍA</div></div><div class="res-box"><div class="val" id="resIngresoDia" translate="no">USD 0.00</div><div class="lbl">INGRESO/DÍA</div></div><div class="res-box"><div class="val" id="resElecDia" style="color:var(--accent-red);" translate="no">USD 0.00</div><div class="lbl">LUZ/DÍA</div></div><div class="res-box"><div class="val" id="resGananciaDia" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA/DÍA</div></div><div class="res-box"><div class="val" id="resGananciaSemana" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA/SEM</div></div><div class="res-box"><div class="val" id="resGananciaMes" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA/MES</div></div><div class="res-box"><div class="val" id="resGananciaAnio" style="color:var(--accent-green);" translate="no">USD 0.00</div><div class="lbl">GANANCIA/AÑO</div></div><div class="res-box"><div class="val" id="resEq" translate="no">--</div><div class="lbl">EQ /KWH</div></div><div class="res-box"><div class="val" id="resCosteBtc" translate="no">--</div><div class="lbl">COSTE BTC</div></div></div><div class="calc-text-ribbon">Precio BTC <span class="hl btc-live-text" translate="no">USD {{ flota.precio_btc }}</span> <span class="sep">|</span> Moneda <span class="hl" translate="no">USD</span> <span class="sep">|</span> Red <span class="hl" translate="no">{{ flota.red_ehs }} EH/s</span> <span class="sep">|</span> Dificultad <span class="hl" translate="no">{{ flota.dificultad_t }} T</span> <span class="sep">|</span> Recompensa <span class="hl" translate="no">{{ flota.recompensa_bloque }} BTC</span> <span class="sep">|</span> Tu parte <span class="hl" id="ribbonTuParte" translate="no">100.00 %</span></div></div><div class="bulk-bar"><button class="btn-bulk" onclick="bulkAccion('restart')">🔄 Reiniciar</button><button class="btn-bulk" onclick="bulkAccion('sleep')">😴 Reposo</button></div><div class="tabla-container"><table><tr><th style="width:30px;"><input type="checkbox" id="chkTodos" onclick="toggleAll(this)"></th><th>MINERO</th><th>ESTADO</th><th>HASHRATE</th><th>TEMP</th><th>CONSUMO</th><th>EFICIENCIA</th><th>GANANCIA</th></tr>{% for f in registros %}<tr class="fila-minero"><td onclick="event.stopPropagation()"><input type="checkbox" class="chk-minero" value="{{ f.ip }}"></td><td onclick="window.location.href='/minero/{{ f.ip }}'"><span class="nombre-link" translate="no">{{ f.ip.split('.')[-1] }}</span><br><span style="font-size:0.7em; color:var(--text-sub);" translate="no">{{ f.modelo }}</span></td><td onclick="window.location.href='/minero/{{ f.ip }}'">{{ f.estado | safe }}<br><span style="font-size:0.7em; color:var(--text-sub);" translate="no">{{ f.tiempo_act }}</span></td><td onclick="window.location.href='/minero/{{ f.ip }}'" translate="no">{{ f.hashrate }} TH/s</td><td onclick="window.location.href='/minero/{{ f.ip }}'" style="color: {{ 'var(--accent-red)' if f.temp_num > 80 else 'var(--accent-green)' }}; font-weight:bold;" translate="no">{{ f.temp_num }}°C</td><td onclick="window.location.href='/minero/{{ f.ip }}'" translate="no">{{ f.consumo_kw }} kW</td><td onclick="window.location.href='/minero/{{ f.ip }}'" translate="no">{{ f.eficiencia }} J/TH</td><td onclick="window.location.href='/minero/{{ f.ip }}'" style="font-weight:bold;" translate="no" class="celda-ganancia">{{ f.ganancia_neta }}</td></tr>{% endfor %}</table></div><script>let PRECIO_BTC=parseFloat("{{ flota.precio_btc_raw }}");const DIFICULTAD=parseFloat("{{ flota.dificultad_t }}");function toggleAll(source){checkboxes=document.querySelectorAll('.chk-minero');for(var i=0,n=checkboxes.length;i<n;i++){checkboxes[i].checked=source.checked;}}function bulkAccion(accion){let chks=document.querySelectorAll('.chk-minero:checked');let ips=Array.from(chks).map(c=>c.value);if(ips.length===0)return alert("Selecciona mineros");if(confirm(`¿Aplicar a ${ips.length} mineros?`)){fetch('/cmd_bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ips:ips,accion:accion})}).then(()=>{alert("Encolados");location.reload();});}}async function actualizarPrecioEnVivo(){try{let r=await fetch('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT');let d=await r.json();PRECIO_BTC=parseFloat(d.price);let fp=PRECIO_BTC.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});document.querySelectorAll('.btc-live-text').forEach(l=>l.innerText='USD '+fp);document.querySelectorAll('.btc-live-price').forEach(l=>l.innerText='USD '+fp);calcularRentabilidad();}catch(e){}}setInterval(actualizarPrecioEnVivo,5000);function formatVal(val){return val<0?`<span style="color:var(--accent-red);">-USD ${Math.abs(val).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}</span>`:`<span style="color:var(--accent-green);">USD ${val.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}</span>`;}function calcularRentabilidad(){let hTH=parseFloat(document.getElementById('hashrateInput').value)||0,cW=parseFloat(document.getElementById('consumoInput').value)||0;let cKwh=parseFloat(document.getElementById('kwhInput').value)||0,pool=parseFloat(document.getElementById('comisionInput').value)||0;let tP=(100-pool).toFixed(2);document.getElementById('ribbonTuParte').innerText=tP+' %';let btcDia=DIFICULTAD>0?(hTH*3.125*86400)/(DIFICULTAD*4294967296):0;document.getElementById('topBtcDia').innerText=btcDia.toFixed(8);document.getElementById('topBtcSemana').innerText=(btcDia*7).toFixed(8);document.getElementById('topBtcMes').innerText=(btcDia*30).toFixed(6);document.getElementById('topBtcAnio').innerText=(btcDia*365).toFixed(4);document.getElementById('resBtcDia').innerText=btcDia.toFixed(8);let inBruto=btcDia*PRECIO_BTC,inNeto=inBruto-(inBruto*(pool/100)),elecDia=(cW*24/1000)*cKwh,ganDia=inNeto-elecDia;document.getElementById('resIngresoDia').innerText='USD '+inNeto.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});document.getElementById('resElecDia').innerText=elecDia<=0?`USD 0.00`:`USD ${elecDia.toFixed(2)}`;document.getElementById('resGananciaDia').innerHTML=formatVal(ganDia);document.getElementById('resGananciaSemana').innerHTML=formatVal(ganDia*7);document.getElementById('resGananciaMes').innerHTML=formatVal(ganDia*30);document.getElementById('resGananciaAnio').innerHTML=formatVal(ganDia*365);let breakEven=(cW>0&&inNeto>0)?(inNeto/(cW*24/1000)):0;document.getElementById('resEq').innerText=breakEven>0?'$'+breakEven.toFixed(4):'--';let costBtc=btcDia>0?(elecDia/btcDia):0;document.getElementById('resCosteBtc').innerText=costBtc>0?'$'+costBtc.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'--';}window.onload=function(){calcularRentabilidad();};let segs=1;setInterval(()=>{document.getElementById('textoActualizado').innerText='actualizado hace '+(++segs)+'s';},1000);</script>"""
HTML_DETALLE = """<style>.card{background:var(--bg-card);padding:20px;border-radius:8px;border:1px solid var(--border-color);margin-bottom:20px}.header-detalle{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.btn-volver{color:var(--text-muted);font-size:1.5em;font-weight:bold}.titulo-minero{margin:0;font-size:1.5em;display:flex;align-items:center;gap:10px}.estado-punto{height:12px;width:12px;background:var(--accent-red);border-radius:50%;display:inline-block}.status-banner{background:rgba(255,82,82,0.1);border:1px solid var(--accent-red);padding:15px;border-radius:8px;color:var(--text-main);font-size:0.9em;margin-bottom:20px;display:{{ 'block' if offline else 'none' }}}.status-banner span{color:var(--accent-red);font-weight:bold}.grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-bottom:20px}.grid-4 .card{margin-bottom:0}.grid-4 .card h4{margin:0;font-size:0.7em;color:var(--text-muted);text-transform:uppercase}.grid-4 .card .val{font-size:1.6em;font-weight:bold;margin-top:10px;color:var(--accent-blue)}.panel-title{font-weight:bold;margin-bottom:15px;display:flex;align-items:center;justify-content:space-between;font-size:1.1em}.panel-control{display:flex;gap:10px;margin-bottom:20px;background:var(--bg-card);padding:20px;border-radius:8px;border:1px solid var(--border-color);flex-wrap:wrap}.btn-control{background:transparent;border:1px solid var(--border-color);color:var(--text-main);padding:8px 15px;border-radius:20px;cursor:pointer;font-weight:bold;transition:0.2s;font-size:0.85em;display:flex;align-items:center;gap:6px}.btn-control:hover{border-color:var(--accent-blue)}.btn-control.active{border-color:var(--accent-blue);background:rgba(51,102,255,0.1);color:var(--accent-blue)}.chart-filters{display:flex;gap:5px}.btn-filter{background:transparent;border:1px solid var(--border-color);color:var(--text-muted);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:0.8em;font-weight:bold;transition:0.2s}.btn-filter.active,.btn-filter:hover{background:rgba(51,102,255,0.1);color:var(--accent-blue);border-color:var(--accent-blue)}.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}.info-table{width:100%;border-collapse:collapse}.info-table td{padding:12px 0;border-bottom:1px solid var(--border-color);font-size:0.85em}.info-table td:nth-child(1){color:var(--text-main);font-weight:bold}.info-table td:nth-child(2){text-align:right;color:var(--text-muted)}.input-pool{background:rgba(255,255,255,0.05);border:1px solid var(--border-color);color:var(--text-main);padding:8px 10px;width:100%;font-family:monospace;font-size:1em;outline:none;border-radius:4px;transition:0.2s;box-sizing:border-box}.input-pool:focus{border-color:var(--accent-blue);background:rgba(51,102,255,0.05)}.btn-guardar{background:rgba(51,102,255,0.1);border:1px solid var(--accent-blue);color:var(--accent-blue);cursor:pointer;font-weight:bold;padding:6px 12px;border-radius:4px;transition:0.2s;width:100%}.btn-guardar:hover{background:var(--accent-blue);color:#fff}.terminal{background:#000;color:#00e676;padding:15px;border-radius:6px;font-family:monospace;font-size:0.85em;height:300px;overflow-y:scroll;border:1px solid #333;margin-top:20px;display:none;white-space:pre-wrap}</style><div class="header-detalle"><div style="display:flex; align-items:center; gap:15px;"><a href="/" class="btn-volver">← Volver</a></div></div><div class="status-banner"><span>⛔ Este minero está sin conexión</span> (visto {{ ultimo.uptime }}). Datos históricos.</div><div class="grid-4"><div class="card"><h4>Hashrate</h4><div class="val" translate="no">{{ ultimo.hashrate }} TH/s</div></div><div class="card"><h4>Errores</h4><div class="val" style="color:var(--accent-red);" translate="no">{{ ultimo.errores }}</div></div><div class="card"><h4>Consumo</h4><div class="val" style="color:var(--text-main);" translate="no">{{ ultimo.consumo }} kW</div></div><div class="card"><h4>Temp Max</h4><div class="val" style="color:var(--accent-green);" translate="no">{{ ultimo.temp_max }}°C</div></div></div><div class="card" style="padding:20px;"><div class="panel-title" style="justify-content: flex-start;">⚡ Perfiles de Energía</div><div style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid var(--border-color);"><button class="btn-control" style="color:var(--accent-green); border-color:var(--accent-green);" onclick="fetch('/cmd/{{ ip }}/profile_low').then(()=>alert('Perfil Invierno/Eco a cola.'))">❄️ Modo Invierno (Eco)</button><button class="btn-control" style="color:var(--accent-red); border-color:var(--accent-red);" onclick="fetch('/cmd/{{ ip }}/profile_high').then(()=>alert('Perfil Verano/Max a cola.'))">🔥 Modo Verano (Max)</button></div><div class="panel-title" style="justify-content: flex-start;">🛠️ Panel de control</div><div style="display: flex; gap: 10px; flex-wrap: wrap;"><button class="btn-control" style="border-color:var(--accent-red); color:var(--accent-red);" onclick="fetch('/cmd/{{ ip }}/restart').then(()=>location.reload())">🔄 Reiniciar</button><button class="btn-control" onclick="fetch('/cmd/{{ ip }}/sleep').then(()=>location.reload())">😴 Reposo</button><button class="btn-control active" onclick="fetch('/cmd/{{ ip }}/normal').then(()=>location.reload())">▶ Normal</button><button class="btn-control" style="color:gold; border-color:gold;" onclick="fetch('/cmd/{{ ip }}/blink').then(()=>location.reload())">💡 Parpadear LED</button><button class="btn-control" style="color:#a5a7b5; border-color:#a5a7b5;" onclick="solicitarLogs()">🖥️ Consola (Logs)</button></div><div id="terminal-box" class="terminal">Esperando logs...</div></div><div class="card"><div class="panel-title"><div>☑ Hashrate</div><div class="chart-filters"><button class="btn-filter" id="btn-6h" onclick="updateChart('6h')">6 h</button><button class="btn-filter active" id="btn-24h" onclick="updateChart('24h')">24 h</button><button class="btn-filter" id="btn-7d" onclick="updateChart('7d')">7 d</button><button class="btn-filter" id="btn-30d" onclick="updateChart('30d')">30 d</button></div></div><div style="height: 300px;"><canvas id="hashrateChart"></canvas></div></div><div class="grid-2"><div class="card"><div class="panel-title" style="justify-content: flex-start;">🌡️ Cadenas</div><table class="info-table" style="margin-top:15px;">{% for c in extra.cadenas %}<tr><td>Cadena {{ c.id }} <span style="font-weight:normal; color:var(--text-muted); font-size:0.8em; margin-left:10px;">{{ ultimo.hashrate_c0 }} TH/s</span></td><td>Chip <span style="color:var(--accent-blue); font-weight:bold;">{{ c.temp_c }}°C</span> &nbsp;&nbsp; PCB <span style="color:var(--accent-green); font-weight:bold;">{{ c.temp_p }}°C</span></td></tr>{% endfor %}</table></div><div class="card"><div class="panel-title" style="justify-content: flex-start;">ℹ Info</div><table class="info-table"><tr><td>Modelo</td><td translate="no">{{ modelo }}</td></tr><tr><td>Firmware</td><td translate="no">{{ extra.firmware }}</td></tr><tr><td>Serie</td><td translate="no">{{ extra.serial }}</td></tr><tr><td>MAC</td><td translate="no">{{ extra.mac }}</td></tr><tr><td>IP</td><td translate="no">{{ ip }}</td></tr><tr><td>Eficiencia</td><td translate="no">{{ dispositivo.eficiencia }} J/TH</td></tr><tr><td>Ventiladores</td><td translate="no">{{ ultimo.fans }} RPM</td></tr></table></div></div><div class="card"><div class="panel-title" style="justify-content: flex-start;">⛏ Pools</div><table class="info-table" style="text-align: left;"><tr style="color:var(--text-muted); font-size:0.75em; text-transform:uppercase;"><th>#</th><th>Pool</th><th>Worker</th><th>Estado</th><th>Acción</th></tr><tr><td style="color:var(--text-main);">1</td><td style="padding-right: 10px;"><input type="text" id="url_1" value="{{ extra.pool1 }}" class="input-pool" translate="no"></td><td style="padding-right: 10px;"><input type="text" id="user_1" value="{{ extra.worker1 }}" class="input-pool" translate="no"></td><td style="color:var(--accent-green); font-weight:bold;">Alive</td><td><button class="btn-guardar" onclick="guardarPool(1)">Guardar</button></td></tr><tr><td style="color:var(--text-main);">2</td><td style="padding-right: 10px;"><input type="text" id="url_2" value="{{ extra.pool2 }}" class="input-pool" translate="no"></td><td style="padding-right: 10px;"><input type="text" id="user_2" value="{{ extra.worker2 }}" class="input-pool" translate="no"></td><td style="color:var(--accent-green); font-weight:bold;">Alive</td><td><button class="btn-guardar" onclick="guardarPool(2)">Guardar</button></td></tr><tr><td style="color:var(--text-main);">3</td><td style="padding-right: 10px;"><input type="text" id="url_3" value="{{ extra.pool3 }}" class="input-pool" translate="no"></td><td style="padding-right: 10px;"><input type="text" id="user_3" value="{{ extra.worker3 }}" class="input-pool" translate="no"></td><td style="color:var(--accent-red); font-weight:bold;">Invalid</td><td><button class="btn-guardar" onclick="guardarPool(3)">Guardar</button></td></tr></table></div><script>function solicitarLogs(){let tb=document.getElementById('terminal-box');tb.style.display='block';tb.innerText='Solicitando logs...';fetch('/cmd/{{ ip }}/get_log').then(()=>{setInterval(async ()=>{let r=await fetch('/api/ver_log/{{ ip }}');let txt=await r.text();if(txt.length>5){tb.innerText=txt;tb.scrollTop=tb.scrollHeight;}},3000);});}function guardarPool(idx){let u=document.getElementById('url_'+idx).value,us=document.getElementById('user_'+idx).value;if(confirm('¿Guardar Pool '+idx+'?')){fetch('/cmd/{{ ip }}/setpool?url='+encodeURIComponent(u)+'&user='+encodeURIComponent(us)).then(()=>alert('A la cola.'));}}const rawFechas={{ graf_fechas_raw | safe }};const rawHr={{ graf_hashrate | safe }};const rawTemp={{ graf_temps | safe }};const cadenasCount={{ extra.cadenas | length }};let chartInstance=null;function updateChart(rango){document.querySelectorAll('.btn-filter').forEach(b=>b.classList.remove('active'));document.getElementById('btn-'+rango).classList.add('active');let limitDate=new Date();if(rango==='6h')limitDate.setHours(limitDate.getHours()-6);if(rango==='24h')limitDate.setHours(limitDate.getHours()-24);if(rango==='7d')limitDate.setDate(limitDate.getDate()-7);if(rango==='30d')limitDate.setDate(limitDate.getDate()-30);let fL=[],fH=[],fT=[];let labelLimite=(rango==='7d'||rango==='30d')?limitDate.toLocaleDateString()+' '+limitDate.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):limitDate.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});fL.push(labelLimite);fH.push(null);fT.push(null);for(let i=0;i<rawFechas.length;i++){let d=new Date(rawFechas[i]*1000);if(d>=limitDate){let timeStr=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});let label=timeStr;if(rango==='7d'||rango==='30d')label=d.toLocaleDateString()+' '+timeStr;fL.push(label);fH.push(rawHr[i]);fT.push(rawTemp[i]);}}if(chartInstance){chartInstance.data.labels=fL;chartInstance.data.datasets[0].data=fH;chartInstance.data.datasets[1].data=fT;chartInstance.update();}else{const ctx=document.getElementById('hashrateChart').getContext('2d');chartInstance=new Chart(ctx,{type:'line',data:{labels:fL,datasets:[{label:'Hashrate (TH/s)',data:fH,borderColor:'#3366ff',backgroundColor:'rgba(51,102,255,0.1)',fill:true,tension:0.1,pointRadius:0,borderWidth:2,spanGaps:false},{label:'Temp Max °C',data:fT,borderColor:'#ffb300',borderDash:[5,5],tension:0.1,pointRadius:0,borderWidth:1,yAxisID:'y1',spanGaps:false}]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{tooltip:{callbacks:{title:function(context){return context[0].label;},label:function(context){if(context.datasetIndex===0){return `⚡ Hashrate: ${context.parsed.y} TH/s`;}if(context.datasetIndex===1){let t=context.parsed.y;let tooltipArr=[`🌡️ Temp Max: ${t}°C`];for(let c=0;c<cadenasCount;c++){tooltipArr.push(`   Cadena ${c} -> Chip: ${Math.max(0,t-(c*1))}°C | PCB: ${Math.max(0,t-(5+c))}°C`);}return tooltipArr;}}}}},scales:{x:{grid:{color:'rgba(255,255,255,0.05)'}},y:{grid:{color:'rgba(255,255,255,0.05)'},min:0},y1:{position:'right',grid:{drawOnChartArea:false},min:0,max:100}}}});}}window.onload=function(){updateChart('24h');};</script>"""
HTML_SUBPAGINAS = """<style>.card-sp{background:var(--bg-card);border:1px solid var(--border-color);padding:40px;border-radius:8px;text-align:center;max-width:500px;width:100%;margin:0 auto;box-shadow:0 4px 15px rgba(0,0,0,0.3)}.btn-sp{background:var(--accent-blue);color:white;text-decoration:none;padding:12px 25px;border-radius:4px;display:inline-block;margin-top:25px;font-weight:bold;border:none;cursor:pointer}</style><div class="card-sp"><h1 style="margin-top:0;">{{ titulo }}</h1><p style="color:var(--text-muted);line-height:1.6;">Sección administrativa para gestión.</p><a href="/" class="btn-sp">← Volver</a></div>"""


# --- 3. RUTAS FLASK DEL SAAS ---

@app.route('/')
@login_required
def index():
    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos")
    mineros = cur.fetchall()
    con.close()
    
    datos_red = obtener_datos_red()
    registros, en_linea, offline, ht, cw, tm, ig = [], 0, 0, 0.0, 0.0, 0, 0.0
    
    for m in mineros:
        ip = m['ip']; mod = m['modelo']; ult = m['ultima_act'] if m['ultima_act'] else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        hr = float(m['hashrate']) if m['hashrate'] else 0.0
        t = int(m['temp_max']) if m['temp_max'] else 0
        c = float(m['consumo_w']) if m['consumo_w'] else 0.0
        
        segs_desconectado = (datetime.utcnow() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds()
        
        if segs_desconectado < 180:
            en_linea += 1; ht += hr; cw += c; tm = max(tm, t)
            b = (hr * datos_red['recompensa'] * 86400) / (datos_red['dificultad_t'] * 4294967296) if datos_red['dificultad_t'] > 0 else 0
            ib = b * datos_red['precio']; ig += ib
            str_gan = f"-USD {abs(ib):.2f}" if ib < 0 else f"{ib:.2f} USD"
            registros.append({'ip': ip, 'modelo': mod, 'estado': '<span style="color:var(--accent-green);">● En línea</span>', 'tiempo_act': 'hace ' + str(int(segs_desconectado)) + 's', 'hashrate': hr, 'temp_num': t, 'consumo_kw': round(c/1000, 1), 'eficiencia': round(c/hr, 2) if hr>0 else 0, 'ganancia_neta': str_gan})
        else:
            offline += 1
            registros.append({'ip': ip, 'modelo': mod, 'estado': '<span style="color:var(--accent-red);">● Sin conexión</span>', 'tiempo_act': 'Offline', 'hashrate': 0, 'temp_num': 0, 'consumo_w': 0, 'consumo_kw': 0, 'eficiencia': 0, 'ganancia_neta': "USD 0.00"})
            
    flota = {'hashrate_total': round(ht, 1), 'consumo_w_total': round(cw, 0), 'consumo_kw': round(cw/1000, 1), 'temp_max': tm, 'precio_btc_raw': datos_red['precio'], 'precio_btc': f"{datos_red['precio']:,.2f}", 'dificultad_t': round(datos_red['dificultad_t'], 2), 'recompensa_bloque': datos_red['recompensa'], 'red_ehs': datos_red['red_ehs'], 'ingreso_dia': f"{ig:.2f}", 'ganancia_dia': f"{ig:.2f} USD"}
    return render_template_string(HTML_LAYOUT, active_page='resumen', content=render_template_string(HTML_RESUMEN, registros=registros, totales=len(mineros), en_linea=en_linea, offline=offline, flota=flota))

@app.route('/minero/<ip>')
@login_required
def detalle_minero(ip):
    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM mineros_activos WHERE ip=%s", (ip,))
    m = cur.fetchone()
    cur.execute("SELECT fecha, hashrate, temp_max FROM historial_nube WHERE ip=%s ORDER BY id DESC LIMIT 5000", (ip,))
    hist = cur.fetchall()
    con.close()
    if not m: return redirect('/')
    
    hr_val = float(m['hashrate']) if m['hashrate'] else 0.0
    t_val = int(m['temp_max']) if m['temp_max'] else 0
    c_val = float(m['consumo_w']) if m['consumo_w'] else 0.0
    ult = m['ultima_act'] if m['ultima_act'] else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    offline = (datetime.utcnow() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds() > 180
    
    pool1 = str(m['pool1']) if m['pool1'] else ""; worker1 = str(m['worker1']) if m['worker1'] else ""
    pool2 = str(m['pool2']) if m['pool2'] else ""; worker2 = str(m['worker2']) if m['worker2'] else ""
    pool3 = str(m['pool3']) if m['pool3'] else ""; worker3 = str(m['worker3']) if m['worker3'] else ""
    mac = str(m['mac']) if m['mac'] else "-"; serial = str(m['serial']) if m['serial'] else "-"
    fw = str(m['firmware']) if m['firmware'] else "Desconocido"; fans = str(m['fans']) if m['fans'] else "0 / 0"
    
    cadenas_str = m['cadenas'] if m['cadenas'] else "[]"
    try: cadenas = json.loads(cadenas_str)
    except: cadenas = []
    if not cadenas: cadenas = [{"id": 0, "temp_c": t_val, "temp_p": max(0, t_val-5)}, {"id": 1, "temp_c": max(0, t_val-1), "temp_p": max(0, t_val-6)}, {"id": 2, "temp_c": max(0, t_val-2), "temp_p": max(0, t_val-7)}]

    hist.reverse()
    f_raw = []; hr_g = []; tp_g = []
    if hist:
        for idx, h in enumerate(hist):
            dt_actual = datetime.strptime(h['fecha'], "%Y-%m-%d %H:%M:%S")
            ts = calendar.timegm(dt_actual.timetuple()); f_raw.append(ts) 
            val_h = float(h['hashrate']) if h['hashrate'] else 0
            if idx > 0:
                dt_prev = datetime.strptime(hist[idx-1]['fecha'], "%Y-%m-%d %H:%M:%S")
                if (dt_actual - dt_prev).total_seconds() > 300: 
                    f_raw.insert(-1, calendar.timegm(dt_prev.timetuple())); hr_g.insert(-1, 0); tp_g.insert(-1, 0)
            hr_g.append(val_h); tp_g.append(int(h['temp_max']) if h['temp_max'] else 0)
    else:
        f_raw = [calendar.timegm(datetime.utcnow().timetuple())]; hr_g = [hr_val]; tp_g = [t_val]
        
    dt = {'hashrate': hr_val, 'temp_max': t_val, 'errores': m['errores'], 'consumo': round(c_val/1000, 1), 'fans': fans, 'uptime': '0d 13h' if not offline else 'Desconectado', 'hashrate_c0': round(hr_val/len(cadenas), 2) if cadenas else 0}
    extra = {'worker1': worker1, 'pool1': pool1, 'worker2': worker2, 'pool2': pool2, 'worker3': worker3, 'pool3': pool3, 'mac': mac, 'serial': serial, 'firmware': fw, 'cadenas': cadenas}
    disp = {'eficiencia': round(c_val/hr_val, 2) if hr_val > 0 else 0}
    return render_template_string(HTML_LAYOUT, active_page='mineros', content=render_template_string(HTML_DETALLE, ip=ip, modelo=m['modelo'], ultimo=dt, extra=extra, dispositivo=disp, offline=offline, graf_fechas_raw=json.dumps(f_raw), graf_hashrate=json.dumps(hr_g), graf_temps=json.dumps(tp_g)))

@app.route('/plan')
@login_required
def plan():
    return render_template_string(HTML_LAYOUT, active_page='plan', content=render_template_string(HTML_PLAN, current_plan=session.get('plan', 'Gratis'), saldo=session.get('saldo', 0.0)))

@app.route('/granjas')
@login_required
def granjas(): return render_template_string(HTML_LAYOUT, active_page='granjas', content=render_template_string(HTML_SUBPAGINAS, titulo="🏭 Gestión de Granjas"))
@app.route('/mineros')
@login_required
def mineros(): return redirect('/')
@app.route('/alertas')
@login_required
def alertas(): return render_template_string(HTML_LAYOUT, active_page='alertas', content=render_template_string(HTML_SUBPAGINAS, titulo="🔔 Centro de Alertas"))
@app.route('/ajustes')
@login_required
def ajustes(): return render_template_string(HTML_LAYOUT, active_page='ajustes', content=render_template_string(HTML_SUBPAGINAS, titulo="⚙️ Ajustes del Sistema"))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username'); p = request.form.get('password')
        con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM usuarios WHERE username=%s AND password=%s", (u, p))
        usr = cur.fetchone(); con.close()
        if usr:
            session['usuario'] = u; session['plan'] = usr.get('plan', 'Gratis'); session['saldo'] = usr.get('saldo', 0.0)
            return redirect('/')
        return render_template_string(HTML_AUTH, error='Credenciales incorrectas', mode='login')
    return render_template_string(HTML_AUTH, mode='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = request.form.get('username'); p = request.form.get('password')
        con = get_db(); cur = con.cursor()
        try:
            cur.execute("INSERT INTO usuarios (username, password, plan, saldo) VALUES (%s, %s, 'Gratis', 0.0)", (u, p))
            con.commit(); con.close()
            return render_template_string(HTML_AUTH, success='Cuenta creada. Ahora Inicia Sesión.', mode='login')
        except:
            con.close(); return render_template_string(HTML_AUTH, error='El usuario ya existe', mode='register')
    return render_template_string(HTML_AUTH, mode='register')

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/exportar_csv')
@login_required
def exportar_csv():
    con = get_db(); cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT ip, modelo, hashrate, temp_max, consumo_w, estado FROM mineros_activos")
    filas = cur.fetchall(); con.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['IP', 'Modelo', 'Hashrate (TH/s)', 'Temp Max (C)', 'Consumo (W)', 'Estado'])
    for f in filas: writer.writerow([f['ip'], f['modelo'], f['hashrate'], f['temp_max'], f['consumo_w'], f['estado']])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=Reporte_El_Minero_Gris.csv"})

@app.route('/cmd_bulk', methods=['POST'])
@login_required
def cmd_bulk():
    data = request.json; accion = data.get('accion'); ips = data.get('ips', [])
    if accion and ips:
        con = get_db(); cur = con.cursor()
        for ip in ips: cur.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (%s, %s, 'pendiente', %s)", (ip, accion, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit(); con.close()
    return "OK"

@app.route('/cmd/<ip>/<accion>')
@login_required
def comando_minero(ip, accion):
    cmd_str = ""
    if accion in ['restart', 'sleep', 'normal', 'blink', 'scan', 'get_log'] or accion.startswith('profile_'): cmd_str = accion.replace('_', '|') if accion.startswith('profile_') else accion
    elif accion == 'setpool': cmd_str = f"setpool|{request.args.get('url', '')}|{request.args.get('user', '')}"
    if cmd_str:
        con = get_db(); cur = con.cursor()
        cur.execute("INSERT INTO cola_comandos (ip, comando, estado, fecha) VALUES (%s, %s, 'pendiente', %s)", (ip, cmd_str, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit(); con.close()
    return "OK"

@app.route('/api/ver_log/<ip>')
def ver_log(ip):
    con = get_db(); cur = con.cursor()
    cur.execute("SELECT contenido FROM logs_mineros WHERE ip=%s", (ip,)); log = cur.fetchone(); con.close()
    return log[0] if log else "Esperando respuesta..."

@app.route('/api/recibir_logs', methods=['POST'])
def recibir_logs():
    data = request.json
    if data and 'ip' in data and 'log' in data:
        con = get_db(); cur = con.cursor()
        cur.execute("INSERT INTO logs_mineros (ip, contenido, fecha) VALUES (%s, %s, %s) ON CONFLICT(ip) DO UPDATE SET contenido=EXCLUDED.contenido, fecha=EXCLUDED.fecha", (data['ip'], data['log'], datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit(); con.close()
    return "OK", 200

@app.route('/api/recibir_datos', methods=['POST'])
def recibir_datos():
    datos = request.json; if not datos: return "Error", 400
    con = get_db(); cur = con.cursor(); ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for m in datos.get('mineros', []):
        err = m.get('errores', 0); mc = m.get('mac', ''); sr = m.get('serial', ''); fw = m.get('firmware', ''); fs = m.get('fans', '')
        p1 = m.get('pool1', ''); w1 = m.get('worker1', ''); p2 = m.get('pool2', ''); w2 = m.get('worker2', ''); p3 = m.get('pool3', ''); w3 = m.get('worker3', '')
        cadenas_json = json.dumps(m.get('cadenas', []))
        cur.execute('''INSERT INTO mineros_activos (ip, modelo, hashrate, temp_max, consumo_w, estado, ultima_act, errores, pool1, worker1, pool2, worker2, pool3, worker3, mac, serial, firmware, fans, cadenas) 
                       VALUES (%s, %s, %s, %s, %s, 'online', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(ip) DO UPDATE SET 
                       modelo=EXCLUDED.modelo, hashrate=EXCLUDED.hashrate, temp_max=EXCLUDED.temp_max, consumo_w=EXCLUDED.consumo_w, ultima_act=EXCLUDED.ultima_act, errores=EXCLUDED.errores, pool1=EXCLUDED.pool1, worker1=EXCLUDED.worker1, pool2=EXCLUDED.pool2, worker2=EXCLUDED.worker2, pool3=EXCLUDED.pool3, worker3=EXCLUDED.worker3, mac=EXCLUDED.mac, serial=EXCLUDED.serial, firmware=EXCLUDED.firmware, fans=EXCLUDED.fans, cadenas=EXCLUDED.cadenas''', 
                    (m['ip'], m['modelo'], m['hashrate'], m['temp'], m['consumo'], ahora, err, p1, w1, p2, w2, p3, w3, mc, sr, fw, fs, cadenas_json))
        cur.execute("INSERT INTO historial_nube (ip, hashrate, temp_max, fecha) VALUES (%s, %s, %s, %s)", (m['ip'], m['hashrate'], m['temp'], ahora))
    
    cur.execute("SELECT id, ip, comando FROM cola_comandos WHERE estado='pendiente'")
    pend = cur.fetchall()
    ordenes = [{"ip": c[1], "comando": c[2]} for c in pend]
    for c in pend: cur.execute("UPDATE cola_comandos SET estado='enviado' WHERE id=%s", (c[0],))
    con.commit(); con.close()
    return jsonify({"status": "ok", "ordenes": ordenes}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
