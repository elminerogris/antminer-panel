from flask import Blueprint, request, session, jsonify
import psycopg2.extras, json, os, uuid, hmac, hashlib, urllib.request, urllib.error
from datetime import datetime, timedelta

from db import get_db
from utils import login_required, a_float_seguro, a_int_seguro, enviar_telegram

pagos_bp = Blueprint('pagos_bp', __name__)

NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "").strip()
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://elminerogris.com").rstrip("/")

REDES_VALIDAS = {
    "usdttrc20": "USDT (TRC20 - Tron)",
    "usdtbsc": "USDT (BEP20 - BSC)",
    "usdtsol": "USDT (Solana)"
}

PRECIO_PRO_30D = 15.0  # USD por 30 días de Plan Pro


def _acreditar_pago_si_corresponde(cur, order_id, payment_id="", status_remoto="finished"):
    """Acredita el saldo una sola vez de forma segura (idempotente)."""
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "SELECT id, username, monto_usd, estado FROM pagos_usdt WHERE order_id=%s LIMIT 1",
        (order_id,)
    )
    pago = cur.fetchone()
    if not pago:
        return False
    if str(pago.get("estado") or "").lower() == "pagado":
        return True

    if str(status_remoto or "").lower() not in ("finished", "confirmed"):
        cur.execute(
            "UPDATE pagos_usdt SET estado=%s, payment_id=CASE WHEN %s != '' THEN %s ELSE payment_id END WHERE id=%s",
            (status_remoto, payment_id, payment_id, pago["id"])
        )
        return False

    # Marcar como pagado solo si aún no lo estaba
    cur.execute(
        "UPDATE pagos_usdt SET estado='pagado', fecha_pago=%s, payment_id=CASE WHEN %s != '' THEN %s ELSE payment_id END WHERE id=%s AND estado != 'pagado'",
        (ahora, payment_id, payment_id, pago["id"])
    )
    if cur.rowcount == 0:
        return True

    monto = round(a_float_seguro(pago.get("monto_usd"), 0.0), 2)
    uname = pago["username"]
    cur.execute("UPDATE usuarios SET saldo = COALESCE(saldo, 0) + %s WHERE username=%s", (monto, uname))
    cur.execute(
        "INSERT INTO movimientos_saldo (username, tipo, monto, nota, fecha) VALUES (%s, 'deposito', %s, %s, %s)",
        (uname, monto, f"Recarga automática USDT ({order_id})", ahora)
    )

    # Notificar por Telegram si el usuario lo tiene configurado
    try:
        cur.execute("SELECT tg_token, tg_chat_id FROM usuarios WHERE username=%s", (uname,))
        u_tg = cur.fetchone()
        if u_tg and u_tg.get("tg_token") and u_tg.get("tg_chat_id"):
            enviar_telegram(
                u_tg["tg_token"],
                u_tg["tg_chat_id"],
                f"💰 <b>Recarga USDT Confirmada</b>\nCuenta: <b>{uname}</b>\nMonto acreditado: <b>+${monto:.2f} USDT</b>\nOrden: <code>{order_id}</code>"
            )
    except Exception:
        pass
    return True


@pagos_bp.route('/api/pagos/crear_usdt', methods=['POST'])
@login_required
def api_crear_pago_usdt():
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return jsonify({"ok": False, "msg": "Permiso denegado para el rol Técnico."}), 403

    if not NOWPAYMENTS_API_KEY:
        return jsonify({
            "ok": False,
            "msg": "Falta configurar NOWPAYMENTS_API_KEY en las variables de entorno de Render."
        }), 503

    data = request.get_json(silent=True) or {}
    monto = round(a_float_seguro(data.get("monto"), 0.0), 2)
    red = str(data.get("red") or "usdttrc20").strip().lower()

    if monto < 5.0 or monto > 5000.0:
        return jsonify({"ok": False, "msg": "El monto debe estar entre $5 y $5,000 USDT."}), 400
    if red not in REDES_VALIDAS:
        red = "usdttrc20"

    username = session.get("usuario", "Admin")
    order_id = f"EMG-{uuid.uuid4().hex[:10].upper()}"
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "price_amount": monto,
        "price_currency": "usd",
        "pay_currency": red,
        "ipn_callback_url": f"{PUBLIC_BASE_URL}/api/pagos/webhook_nowpayments",
        "order_id": order_id,
        "order_description": f"Recarga El Minero Gris - {username}"
    }

    try:
        req = urllib.request.Request(
            "https://api.nowpayments.io/v1/payment",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": NOWPAYMENTS_API_KEY,
                "Content-Type": "application/json",
                "User-Agent": "ElMineroGris/1.0"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_np = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as he:
        err_txt = he.read().decode("utf-8", errors="ignore")
        return jsonify({"ok": False, "msg": f"Error de pasarela ({he.code}): {err_txt[:160]}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "msg": f"No se pudo conectar con NOWPayments: {e}"}), 502

    payment_id = str(res_np.get("payment_id") or "")
    pay_address = str(res_np.get("pay_address") or "")
    pay_amount = a_float_seguro(res_np.get("pay_amount"), monto)
    estado = str(res_np.get("payment_status") or "waiting")

    if not pay_address:
        return jsonify({"ok": False, "msg": "La pasarela no devolvió dirección de depósito."}), 502

    con = get_db(); con.autocommit = True
    cur = con.cursor()
    cur.execute("""
        INSERT INTO pagos_usdt (username, order_id, provider, payment_id, monto_usd, estado, pay_address, pay_currency, fecha_creacion)
        VALUES (%s, %s, 'nowpayments', %s, %s, %s, %s, %s, %s)
    """, (username, order_id, payment_id, monto, estado, pay_address, red, ahora))

    return jsonify({
        "ok": True,
        "order_id": order_id,
        "payment_id": payment_id,
        "pay_address": pay_address,
        "pay_amount": pay_amount,
        "pay_currency": red,
        "red_label": REDES_VALIDAS[red],
        "estado": estado
    }), 200


@pagos_bp.route('/api/pagos/estado/<order_id>', methods=['GET'])
@login_required
def api_estado_pago(order_id):
    username = session.get("usuario", "Admin")
    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM pagos_usdt WHERE order_id=%s AND username=%s LIMIT 1",
        (order_id, username)
    )
    pago = cur.fetchone()
    if not pago:
        return jsonify({"ok": False, "msg": "Orden no encontrada"}), 404

    estado = str(pago.get("estado") or "waiting")
    pid = str(pago.get("payment_id") or "")

    # Si aún no está pagado y tenemos API Key, consultar estado directo a NOWPayments
    if estado != "pagado" and pid and NOWPAYMENTS_API_KEY:
        try:
            req = urllib.request.Request(
                f"https://api.nowpayments.io/v1/payment/{pid}",
                headers={"x-api-key": NOWPAYMENTS_API_KEY, "User-Agent": "ElMineroGris/1.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                info = json.loads(resp.read().decode("utf-8"))
                st_remoto = str(info.get("payment_status") or estado)
                if _acreditar_pago_si_corresponde(cur, order_id, pid, st_remoto):
                    estado = "pagado"
                else:
                    estado = st_remoto
        except Exception:
            pass

    return jsonify({"ok": True, "order_id": order_id, "estado": estado}), 200


@pagos_bp.route('/api/pagos/webhook_nowpayments', methods=['POST'])
def webhook_nowpayments():
    raw_body = request.get_data() or b""
    data = request.get_json(silent=True) or {}

    if NOWPAYMENTS_IPN_SECRET:
        sig_recibida = (request.headers.get("x-nowpayments-sig") or "").strip().lower()
        canonical = json.dumps(data, sort_keys=True, separators=(',', ':')).encode("utf-8")
        sig_calc = hmac.new(NOWPAYMENTS_IPN_SECRET.encode("utf-8"), canonical, hashlib.sha512).hexdigest().lower()
        if not sig_recibida or not hmac.compare_digest(sig_recibida, sig_calc):
            return jsonify({"ok": False, "msg": "Firma IPN inválida"}), 403

    order_id = str(data.get("order_id") or "").strip()
    payment_id = str(data.get("payment_id") or "").strip()
    status = str(data.get("payment_status") or "").strip().lower()
    if not order_id:
        return jsonify({"ok": False}), 400

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    _acreditar_pago_si_corresponde(cur, order_id, payment_id, status)
    return jsonify({"ok": True}), 200


@pagos_bp.route('/api/pagos/historial', methods=['GET'])
@login_required
def api_historial_pagos():
    username = session.get("usuario", "Admin")
    con = get_db()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT order_id, monto_usd, pay_currency, estado, fecha_creacion, fecha_pago FROM pagos_usdt WHERE username=%s ORDER BY id DESC LIMIT 15",
        (username,)
    )
    return jsonify({"ok": True, "pagos": cur.fetchall()}), 200


@pagos_bp.route('/api/plan/activar_pro', methods=['POST'])
@pagos_bp.route('/api/plan/activar_plan', methods=['POST'])
@login_required
def api_activar_pro_con_saldo():
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return jsonify({"ok": False, "msg": "Permiso denegado."}), 403

    username = session.get("usuario", "Admin")
    data = request.get_json(silent=True) or {}
    meses = max(1, min(12, a_int_seguro(data.get("meses"), 1)))
    asics_in = a_int_seguro(data.get("asics"), 0)
    plan_solicitado = str(data.get("plan") or "").strip().lower()

    if plan_solicitado == "inicio" or asics_in == 10:
        asics = 10
        costo_mensual = 3.0
        plan_nombre = "Inicio"
    elif plan_solicitado == "pro" or asics_in == 20:
        asics = 20
        costo_mensual = 6.0
        plan_nombre = "Pro"
    elif asics_in > 0:
        if asics_in % 10 != 0:
            asics_in = ((asics_in // 10) + 1) * 10
        asics = max(10, asics_in)
        costo_mensual = round((asics / 10.0) * 3.0, 2)
        plan_nombre = f"Personalizado ({asics} ASICs)"
    else:
        asics = 20
        costo_mensual = 6.0
        plan_nombre = "Pro"

    costo = round(costo_mensual * meses, 2)

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, saldo, pro_hasta FROM usuarios WHERE username=%s", (username,))
    u = cur.fetchone()
    if not u:
        return jsonify({"ok": False, "msg": "Usuario no encontrado"}), 404

    saldo_real = round(a_float_seguro(u.get("saldo"), 0.0), 2)
    disponible = saldo_real

    if disponible < costo:
        faltante = round(costo - disponible, 2)
        return jsonify({
            "ok": False,
            "saldo_insuficiente": True,
            "disponible": disponible,
            "costo": costo,
            "faltante": faltante,
            "msg": f"Saldo insuficiente. Necesitas ${costo:.2f} USDT y tienes ${disponible:.2f} USDT disponibles."
        }), 400

    nuevo_saldo = round(saldo_real - costo, 2)

    base_dt = datetime.utcnow()
    ph_prev = str(u.get("pro_hasta") or "").strip()
    if ph_prev:
        try:
            dt_p = datetime.strptime(ph_prev, "%Y-%m-%d %H:%M:%S")
            if dt_p > base_dt:
                base_dt = dt_p
        except Exception:
            pass
    nueva_exp = (base_dt + timedelta(days=30 * meses)).strftime("%Y-%m-%d %H:%M:%S")
    ahora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        "UPDATE usuarios SET plan=%s, saldo=%s, pro_hasta=%s, limite_mineros=%s WHERE id=%s",
        (plan_nombre, nuevo_saldo, nueva_exp, asics, u["id"])
    )
    cur.execute(
        "INSERT INTO movimientos_saldo (username, tipo, monto, nota, fecha) VALUES (%s, 'gasto', %s, %s, %s)",
        (username, -costo, f"Activación {plan_nombre} ({meses * 30} días · hasta {nueva_exp[:10]})", ahora)
    )
    session["plan"] = plan_nombre
    session["saldo"] = nuevo_saldo

    return jsonify({
        "ok": True,
        "msg": f"¡Plan {plan_nombre} activado con éxito! Capacidad de {asics} mineros por {meses * 30} días (vence {nueva_exp[:10]}).",
        "nuevo_saldo": nuevo_saldo,
        "pro_hasta": nueva_exp[:10],
        "limite_mineros": asics,
        "plan": plan_nombre
    }), 200


@pagos_bp.route('/api/plan/canjear_cupon', methods=['POST'])
@login_required
def api_canjear_cupon():
    """Canjea un cupón promocional para aumentar el límite de mineros."""
    if str(session.get('rol') or 'owner').lower() == 'tecnico':
        return jsonify({"ok": False, "msg": "Permiso denegado para técnicos."}), 403

    username = session.get("usuario", "Admin")
    data = request.get_json(silent=True) or {}
    codigo = str(data.get("codigo") or "").strip().upper()

    if not codigo:
        return jsonify({"ok": False, "msg": "Por favor ingresa un código de cupón."}), 400

    # Cupones definidos:
    # Cupón especial para ampliar de 3 a 10 máquinas hasta el 1 de febrero del 2027
    CUPONES_10_MAQUINAS = {"PRO10", "MINERO10", "GRATIS10", "LANZAMIENTO10", "ELMINEROGRIS10", "10MAQUINAS", "CUPON10"}

    if codigo not in CUPONES_10_MAQUINAS:
        return jsonify({"ok": False, "msg": "El código de cupón no es válido o ha expirado."}), 400

    EXPIRACION_CUPON = "2027-02-01 23:59:59"
    ahora_dt = datetime.utcnow()
    exp_dt = datetime.strptime(EXPIRACION_CUPON, "%Y-%m-%d %H:%M:%S")

    if ahora_dt > exp_dt:
        return jsonify({"ok": False, "msg": "Este cupón promocional expiró el 1 de febrero de 2027."}), 400

    con = get_db(); con.autocommit = True
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, limite_mineros, pro_hasta FROM usuarios WHERE username=%s", (username,))
    u = cur.fetchone()
    if not u:
        return jsonify({"ok": False, "msg": "Usuario no encontrado."}), 404

    lim_actual = a_int_seguro(u.get("limite_mineros"), 0)
    pro_h_actual = str(u.get("pro_hasta") or "").strip()

    # Si ya tiene un límite igual o mayor a 10 vigente hasta 2027-02-01
    if lim_actual >= 10 and pro_h_actual >= "2027-02-01":
        return jsonify({
            "ok": True,
            "msg": f"Ya tienes activo el beneficio de {lim_actual} máquinas hasta el {pro_h_actual[:10]}."
        }), 200

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cupones_canjeados (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            codigo TEXT NOT NULL,
            fecha_canje TEXT DEFAULT '',
            expira_en TEXT DEFAULT '',
            nuevo_limite INTEGER DEFAULT 10
        )
    """)

    ahora_str = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        "INSERT INTO cupones_canjeados (username, codigo, fecha_canje, expira_en, nuevo_limite) VALUES (%s, %s, %s, %s, 10)",
        (username, codigo, ahora_str, EXPIRACION_CUPON)
    )

    cur.execute(
        "UPDATE usuarios SET limite_mineros=10, pro_hasta=%s WHERE id=%s",
        (EXPIRACION_CUPON, u["id"])
    )

    session["plan"] = "Pro"

    return jsonify({
        "ok": True,
        "msg": "🎉 ¡Cupón aplicado con éxito! Tu límite fue ampliado de 3 a 10 máquinas hasta el 1 de febrero de 2027 con todas las funciones Pro gratis.",
        "limite_mineros": 10,
        "pro_hasta": "2027-02-01"
    }), 200
