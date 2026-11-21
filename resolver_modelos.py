import csv
import io
import threading
import time
import urllib.request

URL_GOOGLE_DEVICES = "https://storage.googleapis.com/play_public/supported_devices.csv"

# Respaldo instantáneo mientras descarga el catálogo oficial en los primeros segundos
MODELOS_BASE = {
    "SM-A107M": ("Samsung Galaxy A10s", "SM-A107M"),
    "SM-A107F": ("Samsung Galaxy A10s", "SM-A107F"),
    "MI A2 LITE": ("Xiaomi Mi A2 Lite", "M1805D1SG"),
    "M1805D1SG": ("Xiaomi Mi A2 Lite", "M1805D1SG"),
}

_lock = threading.Lock()
_catalogo_google = {}
_ultimo_intento = 0.0
_cargando = False


def _limpiar_marca_y_nombre(brand, marketing, model):
    b = str(brand or "").strip()
    m = str(marketing or "").strip()
    cod = str(model or "").strip()

    if not m:
        m = cod
    if b and m:
        if m.lower().startswith(b.lower() + " ") or m.lower() == b.lower():
            comercial = m
        else:
            comercial = f"{b} {m}"
    else:
        comercial = m or b or cod

    return comercial.strip(), cod


def _descargar_catalogo_bg():
    global _catalogo_google, _cargando, _ultimo_intento
    try:
        req = urllib.request.Request(
            URL_GOOGLE_DEVICES,
            headers={"User-Agent": "ElMineroGris-Server/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()

        # El CSV oficial de Google Play viene en UTF-16 (con fallback a UTF-8)
        try:
            texto = raw.decode("utf-16")
        except Exception:
            texto = raw.decode("utf-8", errors="ignore")

        nuevo_mapa = {}
        reader = csv.DictReader(io.StringIO(texto))
        for row in reader:
            modelo = (row.get("Model") or "").strip()
            if not modelo:
                continue
            marca = (row.get("Retail Branding") or "").strip()
            marketing = (row.get("Marketing Name") or "").strip()
            if not marketing:
                continue

            comercial, cod = _limpiar_marca_y_nombre(marca, marketing, modelo)
            clave = modelo.upper()
            # Priorizar entradas que tienen marca y nombre comercial completo
            if clave not in nuevo_mapa or (marca and len(comercial) > len(nuevo_mapa[clave][0])):
                nuevo_mapa[clave] = (comercial, cod)

        if nuevo_mapa:
            with _lock:
                _catalogo_google = nuevo_mapa
            print(f"[MODELOS] Catálogo Google Play cargado: {len(nuevo_mapa)} modelos.")
    except Exception as e:
        print(f"[MODELOS] No se pudo cargar catálogo Google Play: {e}")
    finally:
        with _lock:
            _ultimo_intento = time.time()
            _cargando = False


def asegurar_catalogo_modelos():
    """Lanza la descarga en segundo plano al arrancar o cada 7 días sin bloquear peticiones."""
    global _cargando
    ahora = time.time()
    with _lock:
        if _cargando:
            return
        if _catalogo_google and (ahora - _ultimo_intento) < 604800:
            return
        if not _catalogo_google and _ultimo_intento > 0 and (ahora - _ultimo_intento) < 600:
            return
        _cargando = True
    threading.Thread(target=_descargar_catalogo_bg, daemon=True).start()


def resolver_nombre_dispositivo(brand, model, device_type):
    """
    Traduce cualquier código técnico de celular/tablet a (nombre_comercial, codigo_tecnico).
    Para PC, Laptop o Raspberry Pi devuelve el nombre limpio sin alterar.
    """
    asegurar_catalogo_modelos()

    b = str(brand or "").strip()
    m = str(model or "").strip()
    dt = str(device_type or "Dispositivo Agente").strip()

    if "||" in m:
        partes = m.split("||", 1)
        return partes[0].strip() or dt, partes[1].strip()

    # Candidatos de búsqueda: modelo completo y modelo sin prefijo de marca
    candidatos = []
    if m:
        candidatos.append(m.upper())
        if b and m.upper().startswith(b.upper() + " "):
            candidatos.append(m[len(b):].strip().upper())

    with _lock:
        mapa = _catalogo_google

    for cand in candidatos:
        if cand in mapa:
            return mapa[cand]
        if cand in MODELOS_BASE:
            return MODELOS_BASE[cand]

    # Respaldo inteligente para equipos no certificados, TV Box, PC, Laptop o Raspberry Pi
    if b and m:
        if m.lower().startswith(b.lower() + " ") or m.lower() == b.lower():
            solo_m = m[len(b):].strip() if len(m) > len(b) else ""
            return m, solo_m
        return f"{b} {m}", m
    if m:
        return m, ""
    if b:
        return b, ""
    return dt, ""


# Iniciar carga automática apenas se importe el módulo
asegurar_catalogo_modelos()
