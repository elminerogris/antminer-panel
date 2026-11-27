import socket, json, time, requests, concurrent.futures, re, os, sys, subprocess, shutil, uuid, platform
from requests.auth import HTTPDigestAuth

URL_NUBE = "https://elminerogris.com/api/recibir_datos"
URL_LOGS = "https://elminerogris.com/api/recibir_logs"
URL_VERSION = "https://elminerogris.com/api/agente/version"
URL_UPDATE = "https://elminerogris.com/api/agente/descargar_update"
VERSION_AGENTE = "LOCAL"
API_KEY = "TU_API_KEY_AQUI"
SUBNET = "192.168.0"
MAX_WORKERS = 15

MINER_USER = "root"
MINER_PASS = "root"
CREDENCIALES_HTTP = [
    (MINER_USER, MINER_PASS),
    ("admin", "admin"),
    ("root", "admin")
]

def actualizar_credenciales_http(usr, pwd):
    u = str(usr or "").strip()
    p = str(pwd or "").strip()
    if not u or not p:
        return
    par = (u, p)
    if par in CREDENCIALES_HTTP:
        if CREDENCIALES_HTTP[0] != par:
            CREDENCIALES_HTTP.remove(par)
            CREDENCIALES_HTTP.insert(0, par)
    else:
        CREDENCIALES_HTTP.insert(0, par)

IPS_BASE = [
    f"{SUBNET}.11",
    f"{SUBNET}.12",
    f"{SUBNET}.13",
    f"{SUBNET}.14",
    f"{SUBNET}.15"
]
ips_conocidas = list(IPS_BASE)

cache_info_estatica = {}
cache_geo_wifi = {"lat": None, "lon": None, "ts": 0}
cache_hw_agente = None
ultimo_escaneo_auto = 0
ultimo_check_update = 0

def obtener_o_crear_agent_id():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        ruta_id = os.path.join(base_dir, "agent_id.txt")
        if os.path.exists(ruta_id):
            with open(ruta_id, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val and len(val) >= 4:
                    return val[:40]
        prefijo = "TERMUX-" if ("ANDROID_ROOT" in os.environ or "TERMUX_VERSION" in os.environ) else "PC-"
        nuevo_id = f"{prefijo}{uuid.uuid4().hex[:8].upper()}"
        with open(ruta_id, "w", encoding="utf-8") as f:
            f.write(nuevo_id)
        return nuevo_id
    except Exception:
        return f"PC-{uuid.uuid4().hex[:8].upper()}"

AGENT_ID = obtener_o_crear_agent_id()

def obtener_ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""

def _leer_cmd_corto(cmd_list, timeout=2):
    try:
        kwargs = {"stderr": subprocess.DEVNULL, "timeout": timeout}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.check_output(cmd_list, **kwargs).decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""

def _detectar_hw_estatico():
    global cache_hw_agente
    if cache_hw_agente is not None:
        return dict(cache_hw_agente)

    es_termux = bool("TERMUX_VERSION" in os.environ or "ANDROID_ROOT" in os.environ or os.path.exists("/data/data/com.termux"))
    info = {
        "device_type": "PC de Escritorio",
        "run_mode": "Python PC",
        "brand": "",
        "model": platform.node() or "",
        "os_info": f"{platform.system()} {platform.release()}".strip()
    }

    if es_termux:
        info["run_mode"] = "Termux"
        brand = _leer_cmd_corto(["getprop", "ro.product.manufacturer"])
        model = _leer_cmd_corto(["getprop", "ro.product.model"])
        ver_and = _leer_cmd_corto(["getprop", "ro.build.version.release"])
        chars = _leer_cmd_corto(["getprop", "ro.build.characteristics"]).lower()
        info["brand"] = brand.title() if brand else "Android"
        info["model"] = model or "Dispositivo Android"
        info["os_info"] = f"Android {ver_and}".strip() if ver_and else "Android"
        info["device_type"] = "TV Box" if ("tv" in chars or "box" in model.lower()) else "Celular"
    elif sys.platform == "win32":
        info["run_mode"] = "Python Windows"
        info["os_info"] = f"Windows {platform.release()}".strip()
        out_w = _leer_cmd_corto(["wmic", "computersystem", "get", "manufacturer,model", "/format:list"], timeout=3)
        for linea in out_w.splitlines():
            if "=" in linea:
                k, v = linea.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k == "manufacturer" and v:
                    info["brand"] = v
                elif k == "model" and v:
                    info["model"] = v
    else:
        info["run_mode"] = "Python Linux"
        rpi_model = ""
        if os.path.exists("/proc/device-tree/model"):
            try:
                with open("/proc/device-tree/model", "r", encoding="utf-8", errors="ignore") as f:
                    rpi_model = f.read().replace("\x00", "").strip()
            except Exception:
                pass

        if rpi_model:
            info["model"] = rpi_model
            if "raspberry pi" in rpi_model.lower():
                info["device_type"] = "Raspberry Pi"
                info["brand"] = "Raspberry Pi"
            elif any(x in rpi_model.lower() for x in ["amlogic", "rockchip", "allwinner", "tv", "box"]):
                info["device_type"] = "TV Box"
                info["brand"] = "ARM Linux"
        else:
            try:
                if os.path.exists("/sys/class/dmi/id/sys_vendor"):
                    with open("/sys/class/dmi/id/sys_vendor", "r", encoding="utf-8", errors="ignore") as f:
                        info["brand"] = f.read().strip()
                if os.path.exists("/sys/class/dmi/id/product_name"):
                    with open("/sys/class/dmi/id/product_name", "r", encoding="utf-8", errors="ignore") as f:
                        prod = f.read().strip()
                        if prod:
                            info["model"] = prod
            except Exception:
                pass

    cache_hw_agente = dict(info)
    return info

def obtener_telemetria_dispositivo():
    info = _detectar_hw_estatico()
    info["local_ip"] = obtener_ip_local()
    info["has_battery"] = False
    info["battery_pct"] = -1
    info["charging"] = False
    info["battery_temp_c"] = 0.0

    try:
        if info["run_mode"] == "Termux":
            out_b = _leer_cmd_corto(["termux-battery-status"], timeout=2)
            if out_b and out_b.startswith("{"):
                jb = json.loads(out_b)
                pct = int(jb.get("percentage", -1))
                st = str(jb.get("status", "")).upper()
                plugged = str(jb.get("plugged", "")).upper()
                temp_b = float(jb.get("temperature", 0.0) or 0.0)
                if pct >= 0:
                    info["has_battery"] = True
                    info["battery_pct"] = pct
                    info["charging"] = (st in ("CHARGING", "FULL") or plugged not in ("", "UNPLUGGED", "NONE"))
                    info["battery_temp_c"] = round(temp_b, 1)
            elif os.path.exists("/sys/class/power_supply/battery/capacity"):
                with open("/sys/class/power_supply/battery/capacity", "r") as f:
                    info["battery_pct"] = int(f.read().strip())
                    info["has_battery"] = True
                if os.path.exists("/sys/class/power_supply/battery/status"):
                    with open("/sys/class/power_supply/battery/status", "r") as f:
                        st = f.read().strip().lower()
                        info["charging"] = st in ("charging", "full")
            if not info["has_battery"] and info["device_type"] == "Celular":
                info["device_type"] = "TV Box"

        elif sys.platform == "win32":
            import ctypes
            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_byte),
                    ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]
            sps = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
                b_flag = sps.BatteryFlag & 0xFF
                b_pct = sps.BatteryLifePercent & 0xFF
                ac = sps.ACLineStatus & 0xFF
                if b_flag not in (128, 255) and b_pct <= 100:
                    info["has_battery"] = True
                    info["battery_pct"] = int(b_pct)
                    info["charging"] = (ac == 1)
                    info["device_type"] = "Laptop"

        else:
            ps_dir = "/sys/class/power_supply"
            if os.path.isdir(ps_dir):
                for entry in os.listdir(ps_dir):
                    if entry.upper().startswith("BAT"):
                        cap_p = os.path.join(ps_dir, entry, "capacity")
                        st_p = os.path.join(ps_dir, entry, "status")
                        if os.path.exists(cap_p):
                            with open(cap_p, "r") as f:
                                info["battery_pct"] = int(f.read().strip())
                                info["has_battery"] = True
                            if os.path.exists(st_p):
                                with open(st_p, "r") as f:
                                    st = f.read().strip().lower()
                                    info["charging"] = st in ("charging", "full")
                            if info["device_type"] == "PC de Escritorio":
                                info["device_type"] = "Laptop"
                            break
    except Exception:
        pass

    return info

def keep_awake():
    try:
        if sys.platform == "win32":
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            print("[OK] Anti-suspensión de Windows activado.")
        elif "ANDROID_ROOT" in os.environ:
            subprocess.Popen(["termux-wake-lock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("[OK] Wake-lock de Termux activado.")
    except Exception as e:
        print(f"[AVISO] No se pudo activar anti-suspensión: {e}")

def obtener_ubicacion_wifi():
    global cache_geo_wifi
    ahora_ts = time.time()
    if cache_geo_wifi["ts"] > 0 and (ahora_ts - cache_geo_wifi["ts"] < 21600):
        if cache_geo_wifi["lat"] is not None and cache_geo_wifi["lon"] is not None:
            return {"lat": cache_geo_wifi["lat"], "lon": cache_geo_wifi["lon"]}
        return None

    cache_geo_wifi["ts"] = ahora_ts
    aps = []
    try:
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "networks", "mode=bssid"],
                stderr=subprocess.DEVNULL, timeout=5, creationflags=flags
            ).decode("utf-8", errors="ignore")
            bssid_actual = None
            for linea in out.splitlines():
                m_mac = re.search(r'([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})', linea)
                if m_mac:
                    bssid_actual = m_mac.group(1).lower()
                elif bssid_actual and ("%" in linea):
                    m_sig = re.search(r'(\d+)\s*%', linea)
                    if m_sig:
                        pct = int(m_sig.group(1))
                        dbm = int((pct / 2.0) - 100)
                        aps.append({"macAddress": bssid_actual, "signalStrength": dbm})
                        bssid_actual = None
        else:
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "BSSID,SIGNAL", "dev", "wifi"],
                stderr=subprocess.DEVNULL, timeout=5
            ).decode("utf-8", errors="ignore")
            for linea in out.splitlines():
                m_mac = re.search(r'([0-9a-fA-F]{2}(?:\\?:[0-9a-fA-F]{2}){5})', linea)
                if m_mac:
                    mac_clean = m_mac.group(1).replace("\\", "").lower()
                    resto = linea[m_mac.end():]
                    m_sig = re.search(r'(\d+)', resto)
                    if m_sig:
                        pct = int(m_sig.group(1))
                        dbm = int((pct / 2.0) - 100)
                        aps.append({"macAddress": mac_clean, "signalStrength": dbm})

        if len(aps) >= 2:
            r_geo = requests.post(
                "https://api.beacondb.net/v1/geolocate",
                json={"wifiAccessPoints": aps[:15]},
                headers={"User-Agent": "ElMineroGris-Agente/1.1"},
                timeout=5
            )
            if r_geo.status_code == 200:
                loc = r_geo.json().get("location", {})
                if "lat" in loc and "lng" in loc:
                    cache_geo_wifi["lat"] = round(float(loc["lat"]), 6)
                    cache_geo_wifi["lon"] = round(float(loc["lng"]), 6)
                    print(f"[GEO Wi-Fi] Ubicación triangulada ({len(aps)} redes): {cache_geo_wifi['lat']}, {cache_geo_wifi['lon']}")
                    return {"lat": cache_geo_wifi["lat"], "lon": cache_geo_wifi["lon"]}
    except Exception:
        pass
    return None

def verificar_actualizacion():
    global ultimo_check_update
    ahora_ts = time.time()
    if ahora_ts - ultimo_check_update < 1800:
        return
    ultimo_check_update = ahora_ts
    if not API_KEY or API_KEY == "TU_API_KEY_AQUI":
        return
    try:
        r_ver = requests.get(URL_VERSION, timeout=6)
        if r_ver.status_code != 200:
            return
        ver_nube = r_ver.json().get("version", "")
        if not ver_nube or ver_nube == "error" or ver_nube == VERSION_AGENTE:
            return

        print(f"[UPDATE] Nueva versión detectada ({ver_nube}). Descargando...")
        r_code = requests.get(URL_UPDATE, params={"api_key": API_KEY, "subnet": SUBNET}, timeout=12)
        if r_code.status_code == 200 and "def recolectar_y_enviar" in r_code.text:
            compile(r_code.text, "agente.py", "exec")
            ruta_actual = os.path.abspath(__file__)
            ruta_bak = ruta_actual + ".bak"
            ruta_tmp = ruta_actual + ".tmp"
            try:
                shutil.copy2(ruta_actual, ruta_bak)
            except Exception as e_bak:
                print(f"[UPDATE] No se pudo crear respaldo .bak: {e_bak}")
            with open(ruta_tmp, "w", encoding="utf-8") as f:
                f.write(r_code.text)
            os.replace(ruta_tmp, ruta_actual)
            print("[UPDATE] ¡Agente actualizado con éxito! Reiniciando...")
            if os.environ.get("EMG_MANAGED") == "1":
                sys.exit(0)
            else:
                subprocess.Popen([sys.executable, ruta_actual])
                sys.exit(0)
    except Exception as e:
        print(f"[UPDATE] No se pudo verificar actualización: {e}")

def peticion_4028(ip, comando, parametro=""):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((ip, 4028))
        cmd_dict = {"command": comando}
        if parametro != "":
            cmd_dict["parameter"] = parametro
        s.sendall(json.dumps(cmd_dict).encode('utf-8'))
        resp = b""
        s.settimeout(2.5)
        while True:
            try:
                datos = s.recv(8192)
                if not datos:
                    break
                resp += datos
            except socket.timeout:
                break
        s.close()
        txt = resp.decode('utf-8', errors='ignore').replace('\x00', '').strip()
        if not txt:
            return None
        if '}{' in txt:
            try:
                return json.loads(txt.replace('}{', ','))
            except:
                pass
            try:
                return json.loads(txt.split('}{')[0] + '}')
            except:
                pass
        return json.loads(txt)
    except:
        return None

def peticion_http(ip, endpoint, metodo="GET", payload=None, timeout_sec=4.5):
    url = f"http://{ip}/cgi-bin/{endpoint}"
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': f'http://{ip}/'}
    for usr, pwd in list(CREDENCIALES_HTTP):
        auth = HTTPDigestAuth(usr, pwd)
        try:
            if metodo == "GET":
                r = requests.get(url, auth=auth, headers=headers, timeout=timeout_sec)
            elif metodo == "POST_EMPTY":
                r = requests.post(url, auth=auth, headers=headers, data="", timeout=timeout_sec)
            elif metodo == "POST_FORM":
                r = requests.post(url, auth=auth, headers=headers, data=payload, timeout=timeout_sec)
            else:
                r = requests.post(url, auth=auth, headers=headers, json=payload, timeout=timeout_sec)
            if r.status_code == 200:
                return r.text
            if r.status_code != 401:
                break
        except:
            break
    return None

def detectar_fw_custom(fw_str, st, st_sum):
    """Detecta firmwares custom (Vnish, Braiins OS, LuxOS, Hiveon) y su perfil de overclock/potencia."""
    texto = " ".join([
        str(fw_str or ""),
        str(st.get("Type", "")),
        str(st.get("Description", "")),
        str(st_sum.get("Description", ""))
    ]).lower()

    fw_tipo = "Stock"
    if "vnish" in texto or "anthill" in texto:
        fw_tipo = "Vnish"
    elif "braiins" in texto or "bosminer" in texto or "bos+" in texto:
        fw_tipo = "Braiins OS"
    elif "luxos" in texto or "luxminer" in texto:
        fw_tipo = "LuxOS"
    elif "hiveon" in texto or "hiveos" in texto:
        fw_tipo = "Hiveon"

    perfil = ""
    for k in ("preset", "profile", "power_target", "power_limit", "bos_mode", "work_mode"):
        val = buscar_llave(st, [k], buscar_llave(st_sum, [k], None))
        if val is not None and str(val).strip() != "":
            perfil = str(val).strip()[:45]
            break

    if not perfil and fw_tipo != "Stock":
        perfil = "Auto-Tuning"
    return fw_tipo, perfil

def formatear_de_fabrica(ip):
    print(f"[{ip}] Ejecutando formateo de fábrica (Factory Reset)...")
    for ep in ["reset_conf.cgi", "factory_reset.cgi", "reset.cgi"]:
        peticion_http(ip, ep, "POST_EMPTY", timeout_sec=5.0)
        peticion_http(ip, ep, "GET", timeout_sec=5.0)

    payload_default = {
        "pools": [
            {"url": "", "user": "", "pass": ""},
            {"url": "", "user": "", "pass": ""},
            {"url": "", "user": "", "pass": ""}
        ],
        "bitmain-work-mode": "0",
        "bitmain-fan-ctrl": False,
        "bitmain-fan-pwm": "100"
    }
    peticion_http(ip, "set_miner_conf.cgi", "POST", payload_default, timeout_sec=5.0)
    peticion_4028(ip, "removepool", "0")
    peticion_4028(ip, "removepool", "1")
    peticion_4028(ip, "removepool", "2")
    time.sleep(3)
    peticion_http(ip, "reboot.cgi", "GET", timeout_sec=5.0)

def extraer_numeros(val):
    if val is None or val == "":
        return []
    if isinstance(val, (int, float)):
        return [int(val)]
    if isinstance(val, list):
        res = []
        for x in val:
            res.extend(extraer_numeros(x))
        return res
    nums = [int(float(x)) for x in re.findall(r'\d+(?:\.\d+)?', str(val))]
    return [n for n in nums if n > 0]

def a_entero(val):
    try:
        if val is None or val == "":
            return 0
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).strip()
        if 'o' in s.lower() and not any(c.isdigit() for c in s):
            return s.lower().count('o')
        nums = extraer_numeros(val)
        return max(nums) if nums else 0
    except:
        return 0

def a_entero_min(val):
    nums = [n for n in extraer_numeros(val) if 10 <= n <= 65]
    return min(nums) if nums else 0

def a_float(val):
    try:
        if val is None or val == "":
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        s_clean = re.sub(r'[^\d.-]', '', str(val).strip())
        return float(s_clean) if s_clean else 0.0
    except:
        return 0.0

def formatear_uptime(segundos):
    s = a_entero(segundos)
    if s <= 0:
        return "En línea"
    d = s // 86400
    h = (s % 86400) // 3600
    m = (s % 3600) // 60
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m {s % 60}s"

def buscar_llave(dic, posibles, defecto=None):
    if not isinstance(dic, dict):
        return defecto
    mapa = {str(k).lower().strip(): v for k, v in dic.items()}
    for p in posibles:
        k = p.lower().strip()
        if k in mapa and mapa[k] is not None and mapa[k] != "":
            return mapa[k]
    return defecto

def determinar_credenciales_defecto(marca="", modelo=""):
    """
    Retorna la marca normalizada, usuario y contraseña por defecto para el acceso
    web / SSH de cualquier marca de ASIC (Avalon, WhatsMiner, Antminer, Fluminer, IceRiver, etc.)
    """
    txt = f"{marca} {modelo}".lower()
    if any(k in txt for k in ("avalon", "canaan", "q90", "a10", "a11", "a12", "a13", "a14", "a15", "nano")):
        return {
            "marca": "Canaan Avalon",
            "user": "root",
            "pass": "root",
            "alt": "admin / (vacía) o admin / admin",
            "nota": "AvalonMiner · Canaan"
        }
    if any(k in txt for k in ("whatsminer", "microbt", "btminer", "m20", "m21", "m30", "m31", "m32", "m33", "m50", "m53", "m60", "m63")):
        return {
            "marca": "MicroBT WhatsMiner",
            "user": "admin",
            "pass": "admin",
            "alt": "",
            "nota": "BTMiner · WhatsMiner"
        }
    if any(k in txt for k in ("iceriver", "ks0", "ks1", "ks2", "ks3", "ks5")):
        return {
            "marca": "IceRiver",
            "user": "admin",
            "pass": "12345678",
            "alt": "",
            "nota": "IceRiver Kaspa"
        }
    if any(k in txt for k in ("goldshell", "box")):
        return {
            "marca": "Goldshell",
            "user": "admin",
            "pass": "12345678",
            "alt": "admin / admin",
            "nota": "Goldshell Miner"
        }
    if any(k in txt for k in ("fluminer", "flux")):
        return {
            "marca": "Fluminer",
            "user": "root",
            "pass": "root",
            "alt": "admin / admin",
            "nota": "Fluminer Altcoin"
        }
    if any(k in txt for k in ("jasminer", "x4", "x16")):
        return {
            "marca": "Jasminer",
            "user": "root",
            "pass": "root",
            "alt": "admin / admin",
            "nota": "Jasminer Etchash"
        }
    if any(k in txt for k in ("innosilicon", "t2t", "t3", "a10", "a11")):
        return {
            "marca": "Innosilicon",
            "user": "admin",
            "pass": "admin",
            "alt": "root / root",
            "nota": "Innosilicon A10/T2T"
        }
    if any(k in txt for k in ("antminer", "bitmain", "bmminer")):
        return {
            "marca": "Bitmain Antminer",
            "user": "root",
            "pass": "root",
            "alt": "root / admin (antiguos) o Braiins: root / root",
            "nota": "Bitmain Antminer"
        }
    return {
        "marca": "ASIC Estándar",
        "user": "root",
        "pass": "root",
        "alt": "admin / admin",
        "nota": "Acceso de fábrica"
    }

def detectar_algoritmo_y_moneda(modelo_str, pool_url=""):
    mod = str(modelo_str or "").lower()
    url = str(pool_url or "").lower()

    if any(x in mod for x in ["flux", "zel"]) or any(x in url for x in ["flux.", "zel.", "zelhash"]):
        return "ZelHash", "FLUX"
    if any(x in mod for x in ["alph", "alephium"]) or any(x in url for x in ["alph.", "alephium.", "blake3"]):
        return "Blake3", "ALPH"
    if any(x in mod for x in ["ks3", "ks5", "ks0", "ks1", "ks2", "kheavy", "iceriver"]) or any(x in url for x in ["kas.", "kaspa", "kheavy"]):
        return "kHeavyHash", "KAS"
    if any(x in mod for x in ["z15", "z11", "z9", "equihash"]) or any(x in url for x in ["zec.", "equihash", "zen."]):
        return "Equihash", "ZEC"
    if any(x in mod for x in ["l7", "l9", "l3", "scrypt", "elphapex"]) or any(x in url for x in ["ltc.", "doge.", "scrypt"]):
        return "Scrypt", "LTC/DOGE"
    if any(x in mod for x in ["ka3", "kd", "blake2s"]) or any(x in url for x in ["kda.", "kadena"]):
        return "Blake2S", "KDA"
    if any(x in mod for x in ["d9", "d7", "x11"]) or any(x in url for x in ["dash.", "x11"]):
        return "X11", "DASH"
    if any(x in mod for x in ["e9", "etc", "ethash", "jasminer"]) or any(x in url for x in ["etc.", "ethash", "etchash"]):
        return "EtHash", "ETC"
    if any(x in mod for x in ["rvn", "kawpow"]) or any(x in url for x in ["rvn.", "ravencoin", "kawpow"]):
        return "KawPow", "RVN"
    if any(x in url for x in ["bch."]):
        return "SHA-256", "BCH"
    return "SHA-256", "BTC"

def normalizar_hashrate_universal(valor_bruto, unidad_origen, modelo_str, algoritmo="SHA-256"):
    val = a_float(valor_bruto)
    u_orig = str(unidad_origen or "").upper().strip()

    if algoritmo == "ZelHash":
        if val > 1000000:
            return round(val / 1000000.0, 2), "MSol/s"
        if val > 1000 or u_orig == "KSOL/S":
            return round(val / 1000.0 if val > 1000 else val, 2), "kSol/s"
        return round(val, 2), "Sol/s"

    if algoritmo == "Equihash":
        if val > 50000:
            val = val / 1000.0
        return round(val, 2), "kSol/s"

    if algoritmo == "kHeavyHash":
        if u_orig in ("MHS", "MH/S") or val > 500000:
            return round(val / 1000000.0, 2), "TH/s"
        if u_orig in ("GHS", "GH/S") or val > 1000:
            return round(val / 1000.0, 2), "TH/s"
        if 0 < val <= 50:
            return round(val, 2), "TH/s"
        return round(val, 2), "GH/s"

    if algoritmo == "Blake3":
        if val > 500000:
            return round(val / 1000000.0, 2), "TH/s"
        if val > 1000:
            return round(val / 1000.0, 2), "GH/s"
        return round(val, 2), "GH/s"

    if algoritmo in ("Scrypt", "EtHash", "KawPow"):
        if u_orig in ("MHS", "MH/S") or val > 500:
            val = val / 1000.0
        return round(val, 2), "GH/s"

    if algoritmo == "X11":
        if u_orig in ("MHS", "MH/S") or val > 500000:
            return round(val / 1000000.0, 2), "TH/s"
        if val > 50:
            return round(val / 1000.0, 2), "TH/s"
        return round(val, 2), "TH/s"

    if u_orig in ("MHS", "MH/S") or val > 500000:
        return round(val / 1000000.0, 2), "TH/s"
    if u_orig in ("GHS", "GH/S") or (1200 < val <= 500000):
        return round(val / 1000.0, 2), "TH/s"
    if u_orig in ("THS", "TH/S") or (0 < val <= 1200):
        return round(val, 2), "TH/s"
    return round(val / 1000.0, 2), "TH/s"

def es_whatsminer(st, st_sum, ver_resp):
    texto_check = " ".join([
        str(st.get("Type", "")),
        str(st_sum.get("Type", "")),
        str(ver_resp or "")
    ]).lower()
    if any(w in texto_check for w in ["whatsminer", "btminer", "microbt"]):
        return True
    if "Env Temp" in st_sum or "Chip Temp Max" in st_sum or "Fan Speed In" in st_sum:
        return True
    return False

def extraer_whatsminer(ip, st_sum, ver_resp):
    global cache_info_estatica
    ahora_ts = time.time()
    estatica = cache_info_estatica.get(ip, {})

    if not estatica or (ahora_ts - estatica.get('ts', 0) > 600):
        info_wm = peticion_4028(ip, "get_miner_info")
        msg_info = info_wm.get("Msg", {}) if isinstance(info_wm, dict) and isinstance(info_wm.get("Msg"), dict) else {}
        ver_wm = peticion_4028(ip, "get_version")
        msg_ver = ver_wm.get("Msg", {}) if isinstance(ver_wm, dict) and isinstance(ver_wm.get("Msg"), dict) else {}
        devdet = peticion_4028(ip, "devdetails")
        modelo_det = ""
        if isinstance(devdet, dict) and isinstance(devdet.get("DEVDETAILS"), list) and devdet["DEVDETAILS"]:
            modelo_det = str(devdet["DEVDETAILS"][0].get("Model", "")).strip()

        modelo_str = modelo_det or str(buscar_llave(st_sum, ["Type", "Model"], "WhatsMiner")).strip()
        if "whatsminer" not in modelo_str.lower():
            modelo_str = f"WhatsMiner {modelo_str}".strip()

        fw = str(buscar_llave(msg_ver, ["fw_ver", "api_ver"], buscar_llave(st_sum, ["Firmware Version"], "BTMiner"))).strip()
        mac = str(buscar_llave(msg_info, ["mac"], estatica.get("mac", "-"))).strip()
        serial = str(buscar_llave(msg_info, ["minersn", "sn"], f"WM-{ip.replace('.', '')}")).strip()
        hostname = str(buscar_llave(msg_info, ["hostname"], f"WhatsMiner{ip.split('.')[-1]}")).strip()
        proto_raw = str(buscar_llave(msg_info, ["proto"], "Static")).strip()
        net_proto = "DHCP" if "dhcp" in proto_raw.lower() else "Static"
        netmask = str(buscar_llave(msg_info, ["netmask"], "255.255.255.0")).strip()
        gateway = str(buscar_llave(msg_info, ["gateway"], f"{SUBNET}.1")).strip()
        dns = str(buscar_llave(msg_info, ["dns"], "8.8.8.8")).strip()

        estatica = {
            'ts': ahora_ts, 'marca': 'WhatsMiner', 'modelo': modelo_str, 'mac': mac,
            'serial': serial, 'fw': fw, 'hostname': hostname, 'net_proto': net_proto,
            'netmask': netmask, 'gateway': gateway, 'dns': dns
        }
        cache_info_estatica[ip] = estatica

    modelo_str = estatica.get('modelo', 'WhatsMiner')
    fw_str = estatica.get('fw', 'BTMiner')
    fw_tipo, perfil_oc = detectar_fw_custom(fw_str, {}, st_sum)

    piscinas = peticion_4028(ip, "pools")
    pl = piscinas.get('POOLS', []) if isinstance(piscinas, dict) else []

    def datos_pool(idx):
        if idx < len(pl) and isinstance(pl[idx], dict):
            p = pl[idx]
            url = str(buscar_llave(p, ['URL', 'url'], ''))
            usr = str(buscar_llave(p, ['User', 'user'], ''))
            pas = str(buscar_llave(p, ['Pass', 'pass'], '123'))
            st_p = str(buscar_llave(p, ['Status', 'status'], 'Alive' if url else 'Dead'))
            acc = a_entero(buscar_llave(p, ['Accepted', 'accepted'], 0))
            rej = a_entero(buscar_llave(p, ['Rejected', 'rejected'], 0))
            return url, usr, pas, st_p, acc, rej
        return "", "", "123", "Dead", 0, 0

    p1_url, p1_usr, p1_pas, p1_st, p1_acc, p1_rej = datos_pool(0)
    p2_url, p2_usr, p2_pas, p2_st, p2_acc, p2_rej = datos_pool(1)
    p3_url, p3_usr, p3_pas, p3_st, p3_acc, p3_rej = datos_pool(2)

    algoritmo, moneda = detectar_algoritmo_y_moneda(modelo_str, p1_url)

    raw_5s = buscar_llave(st_sum, ['HS RT', 'MHS 5s', 'MHS 1m', 'MHS av'], 0)
    raw_avg = buscar_llave(st_sum, ['MHS av', 'MHS 15m', 'HS RT'], raw_5s)
    hr_5s, unidad_hr = normalizar_hashrate_universal(raw_5s, "MHS", modelo_str, algoritmo)
    hr_30m, _ = normalizar_hashrate_universal(raw_avg, "MHS", modelo_str, algoritmo)
    if hr_30m == 0:
        hr_30m = hr_5s

    c_w = a_float(buscar_llave(st_sum, ['Power', 'Power_RT', 'Power Limit'], 0))
    if c_w < 100 and hr_5s > 0:
        c_w = hr_5s * 28.0
    elif hr_5s == 0:
        c_w = 120.0

    uptime_str = formatear_uptime(buscar_llave(st_sum, ['Elapsed', 'Uptime'], 0))
    errores_hw = a_entero(buscar_llave(st_sum, ['Hardware Errors'], 0))
    temp_amb = a_entero(buscar_llave(st_sum, ['Env Temp'], 0))

    cadenas = []
    temps_max = []
    edevs = peticion_4028(ip, "edevs")
    devs_list = edevs.get("DEVS", []) if isinstance(edevs, dict) and isinstance(edevs.get("DEVS"), list) else []
    for idx, dv in enumerate(devs_list):
        if not isinstance(dv, dict):
            continue
        t_chip = a_entero(buscar_llave(dv, ['Chip Temp Max', 'Chip Temp Avg', 'Temperature'], 0))
        t_pcb = a_entero(buscar_llave(dv, ['Temperature', 'Chip Temp Min'], max(0, t_chip - 6)))
        chips_num = a_entero(buscar_llave(dv, ['Effective Chips', 'Chips'], 0))
        hw_num = a_entero(buscar_llave(dv, ['Hardware Errors'], 0))
        freq_num = a_entero(buscar_llave(dv, ['Chip Frequency', 'Frequency'], 0))
        hr_c, _ = normalizar_hashrate_universal(buscar_llave(dv, ['MHS 5s', 'MHS av'], 0), "MHS", modelo_str, algoritmo)
        cadenas.append({
            "id": a_entero(dv.get("Slot", idx)),
            "temp_c": t_chip, "temp_p": t_pcb, "chips": chips_num,
            "hw": hw_num, "freq": freq_num, "hr": hr_c
        })
        if t_chip > 0:
            temps_max.append(t_chip)

    tm = max(temps_max) if temps_max else a_entero(buscar_llave(st_sum, ['Chip Temp Max', 'Temperature'], 65))

    fan_in = a_entero(buscar_llave(st_sum, ['Fan Speed In'], 0))
    fan_out = a_entero(buscar_llave(st_sum, ['Fan Speed Out'], 0))
    if fan_in > 0 or fan_out > 0:
        fans_str = f"{fan_in} / {fan_out} RPM"
    elif "m33" in modelo_str.lower() or "m53" in modelo_str.lower() or "m63" in modelo_str.lower() or "hyd" in modelo_str.lower():
        fans_str = "💧 Hydro (Refrigeración Líquida Activa)"
    else:
        fans_str = "0 / 0 RPM"

    cred_wm = determinar_credenciales_defecto("WhatsMiner", modelo_str)

    return {
        "ip": ip, "marca": "WhatsMiner", "modelo": modelo_str,
        "algoritmo": algoritmo, "moneda": moneda, "unidad": unidad_hr,
        "hashrate": round(float(hr_5s), 2), "hashrate_30m": round(float(hr_30m), 2),
        "temp": int(tm), "temp_amb": int(temp_amb), "consumo": round(float(c_w), 0), "errores": int(errores_hw),
        "fans": fans_str, "mac": estatica.get('mac', '-'), "serial": estatica.get('serial', f"WM-{ip.replace('.', '')}"),
        "firmware": fw_str, "fw_tipo": fw_tipo, "perfil_oc": perfil_oc, "cadenas": cadenas, "uptime": uptime_str,
        "hostname": estatica.get('hostname', f"WhatsMiner{ip.split('.')[-1]}"),
        "net_proto": estatica.get('net_proto', 'Static'), "netmask": estatica.get('netmask', '255.255.255.0'),
        "gateway": estatica.get('gateway', f"{SUBNET}.1"), "dns": estatica.get('dns', '8.8.8.8'),
        "pool1": p1_url, "worker1": p1_usr, "pass1": p1_pas, "status1": p1_st, "acc1": int(p1_acc), "rej1": int(p1_rej),
        "pool2": p2_url, "worker2": p2_usr, "pass2": p2_pas, "status2": p2_st, "acc2": int(p2_acc), "rej2": int(p2_rej),
        "pool3": p3_url, "worker3": p3_usr, "pass3": p3_pas, "status3": p3_st, "acc3": int(p3_acc), "rej3": int(p3_rej),
        "cred_defecto": f"{cred_wm['user']} / {cred_wm['pass']}"
    }

# ==================== EXTRACTOR CANAAN AVALON (Q90 HYDRO, A12xx-A15xx, NANO) ====================
def es_avalon(st, st_sum, ver_resp):
    texto_check = " ".join([
        str(st.get("Type", "")),
        str(st.get("Miner", "")),
        str(st.get("Description", "")),
        str(st_sum.get("Type", "")),
        str(st_sum.get("Miner", "")),
        str(ver_resp or "")
    ]).lower()
    if any(w in texto_check for w in ["avalon", "canaan", "ava0", "ava1", "q90", "a10", "a11", "a12", "a13", "a14", "a15", "nano"]):
        return True
    if "AVA0" in st or "MM Version" in st or "DNA" in st:
        return True
    return False

def extraer_avalon(ip, st_sum, ver_resp):
    global cache_info_estatica
    ahora_ts = time.time()
    estatica = cache_info_estatica.get(ip, {})

    stats_raw = peticion_4028(ip, "stats")
    st_ava = {}
    if stats_raw and isinstance(stats_raw, dict):
        for item in stats_raw.get("STATS", []):
            if isinstance(item, dict):
                st_ava.update(item)
                if "AVA0" in item and isinstance(item["AVA0"], dict):
                    st_ava.update(item["AVA0"])

    version_raw = peticion_4028(ip, "version")
    ver_str = ""
    miner_str = ""
    if version_raw and isinstance(version_raw, dict):
        v_list = version_raw.get("VERSION", [])
        if v_list and isinstance(v_list[0], dict):
            ver_str = str(v_list[0].get("CGMiner", "") or v_list[0].get("API", "")).strip()
            miner_str = str(v_list[0].get("Miner", "")).strip()

    if not estatica or (ahora_ts - estatica.get('ts', 0) > 600):
        modelo_cand = miner_str or str(buscar_llave(st_sum, ["Type", "Model", "Miner"], buscar_llave(st_ava, ["Ver", "Type", "Model"], "Avalon Q90"))).strip()
        if not modelo_cand or modelo_cand.lower() in ("canaan", "miner"):
            modelo_cand = "Avalon Q90"
        if "avalon" not in modelo_cand.lower():
            modelo_cand = f"Avalon {modelo_cand}".strip()

        dna = str(buscar_llave(st_ava, ["DNA", "dna", "SN", "sn"], f"AVA-{ip.replace('.', '')}")).strip()
        fw = ver_str or str(buscar_llave(st_ava, ["Ver", "Firmware Version", "CGMiner"], "Canaan Avalon Firmware")).strip()
        mac = str(buscar_llave(st_ava, ["MAC", "mac"], estatica.get("mac", "-"))).strip()
        hostname = str(buscar_llave(st_ava, ["hostname"], f"Avalon{ip.split('.')[-1]}")).strip()

        estatica = {
            'ts': ahora_ts, 'marca': 'Avalon', 'modelo': modelo_cand, 'mac': mac,
            'serial': dna, 'fw': fw, 'hostname': hostname, 'net_proto': 'Static',
            'netmask': '255.255.255.0', 'gateway': f"{SUBNET}.1", 'dns': '8.8.8.8'
        }
        cache_info_estatica[ip] = estatica

    modelo_str = estatica.get('modelo', 'Avalon Q90')
    fw_str = estatica.get('fw', 'Canaan Avalon')

    piscinas = peticion_4028(ip, "pools")
    pl = piscinas.get('POOLS', []) if isinstance(piscinas, dict) else []

    def datos_pool(idx):
        if idx < len(pl) and isinstance(pl[idx], dict):
            p = pl[idx]
            url = str(buscar_llave(p, ['URL', 'url'], ''))
            usr = str(buscar_llave(p, ['User', 'user'], ''))
            pas = str(buscar_llave(p, ['Pass', 'pass'], '123'))
            st_p = str(buscar_llave(p, ['Status', 'status'], 'Alive' if url else 'Dead'))
            acc = a_entero(buscar_llave(p, ['Accepted', 'accepted'], 0))
            rej = a_entero(buscar_llave(p, ['Rejected', 'rejected'], 0))
            return url, usr, pas, st_p, acc, rej
        return "", "", "123", "Dead", 0, 0

    p1_url, p1_usr, p1_pas, p1_st, p1_acc, p1_rej = datos_pool(0)
    p2_url, p2_usr, p2_pas, p2_st, p2_acc, p2_rej = datos_pool(1)
    p3_url, p3_usr, p3_pas, p3_st, p3_acc, p3_rej = datos_pool(2)

    algoritmo, moneda = detectar_algoritmo_y_moneda(modelo_str, p1_url)

    raw_5s = buscar_llave(st_sum, ['GHS 5s', 'MHS 5s', 'HS 5s', 'rate_5s'], 0)
    raw_avg = buscar_llave(st_sum, ['GHS av', 'MHS av', 'HS av'], raw_5s)
    u_orig = "GHS" if ('GHS 5s' in st_sum or 'GHS av' in st_sum) else "MHS"
    hr_5s, unidad_hr = normalizar_hashrate_universal(raw_5s, u_orig, modelo_str, algoritmo)
    hr_30m, _ = normalizar_hashrate_universal(raw_avg, u_orig, modelo_str, algoritmo)
    if hr_30m == 0:
        hr_30m = hr_5s

    c_w = a_float(buscar_llave(st_sum, ['Power', 'Power Limit'], buscar_llave(st_ava, ['Power', 'power'], 0)))
    if c_w < 100 and hr_5s > 0:
        c_w = hr_5s * 32.0
    elif hr_5s == 0:
        c_w = 100.0

    uptime_str = formatear_uptime(buscar_llave(st_sum, ['Elapsed', 'Uptime'], 0))
    errores_hw = a_entero(buscar_llave(st_sum, ['Hardware Errors'], 0))

    t_inlet = a_entero(buscar_llave(st_ava, ['ITemp', 'Temp_Inlet', 'Inlet Temp'], 0))
    t_outlet = a_entero(buscar_llave(st_ava, ['OTemp', 'Temp_Outlet', 'Outlet Temp'], 0))
    t_chip_max = a_entero(buscar_llave(st_ava, ['TMax', 'Temp_Max', 'Chip Temp Max'], 0))

    if t_chip_max >= 150: t_chip_max = 0
    if t_inlet >= 120: t_inlet = 0
    if t_outlet >= 120: t_outlet = 0

    tm = t_chip_max if t_chip_max > 0 else (t_outlet if t_outlet > 0 else a_entero(buscar_llave(st_sum, ['Temperature'], 65)))

    fan1 = a_entero(buscar_llave(st_ava, ['Fan1', 'fan1', 'Fan Speed In', 'Fan Speed'], buscar_llave(st_sum, ['Fan Speed In', 'Fan1', 'Fan Speed'], 0)))
    fan2 = a_entero(buscar_llave(st_ava, ['Fan2', 'fan2', 'Fan Speed Out'], buscar_llave(st_sum, ['Fan Speed Out', 'Fan2'], 0)))

    es_avalon_hydro = any(w in modelo_str.lower() for w in ("hyd", "hydro", "water", "1566ha"))
    if es_avalon_hydro:
        delta_t = max(0, t_outlet - t_inlet) if (t_outlet > 0 and t_inlet > 0) else 0
        if t_inlet > 0 and t_outlet > 0:
            fans_str = f"💧 Hydro (Entrada {t_inlet}°C / Salida {t_outlet}°C · ΔT {delta_t}°C)"
        elif t_inlet > 0:
            fans_str = f"💧 Hydro (Entrada {t_inlet}°C)"
        else:
            fans_str = "💧 Hydro (Refrigeración Líquida Activa)"
    elif fan1 > 0 or fan2 > 0:
        fans_str = f"{fan1} / {fan2} RPM"
    else:
        fans_str = "Auto / Silencioso"

    cadenas = []
    for idx in range(3):
        cadenas.append({
            "id": idx + 1,
            "temp_c": tm,
            "temp_p": t_outlet if t_outlet > 0 else max(0, tm - 8),
            "chips": 120,
            "hw": errores_hw // 3 if errores_hw > 0 else 0,
            "freq": a_entero(buscar_llave(st_ava, ['Freq', 'freq'], 450)),
            "hr": round(float(hr_5s) / 3.0, 2) if hr_5s > 0 else 0.0
        })

    cred = determinar_credenciales_defecto("Avalon", modelo_str)

    return {
        "ip": ip, "marca": "Avalon", "modelo": modelo_str,
        "algoritmo": algoritmo, "moneda": moneda, "unidad": unidad_hr,
        "hashrate": round(float(hr_5s), 2), "hashrate_30m": round(float(hr_30m), 2),
        "temp": int(tm), "temp_amb": int(t_inlet), "consumo": round(float(c_w), 0), "errores": int(errores_hw),
        "fans": fans_str, "mac": estatica.get('mac', '-'), "serial": estatica.get('serial', f"AVA-{ip.replace('.', '')}"),
        "firmware": fw_str, "fw_tipo": "Stock", "perfil_oc": "", "cadenas": cadenas, "uptime": uptime_str,
        "hostname": estatica.get('hostname', f"Avalon{ip.split('.')[-1]}"),
        "net_proto": estatica.get('net_proto', 'Static'), "netmask": estatica.get('netmask', '255.255.255.0'),
        "gateway": estatica.get('gateway', f"{SUBNET}.1"), "dns": estatica.get('dns', '8.8.8.8'),
        "pool1": p1_url, "worker1": p1_usr, "pass1": p1_pas, "status1": p1_st, "acc1": int(p1_acc), "rej1": int(p1_rej),
        "pool2": p2_url, "worker2": p2_usr, "pass2": p2_pas, "status2": p2_st, "acc2": int(p2_acc), "rej2": int(p2_rej),
        "pool3": p3_url, "worker3": p3_usr, "pass3": p3_pas, "status3": p3_st, "acc3": int(p3_acc), "rej3": int(p3_rej),
        "cred_defecto": f"{cred['user']} / {cred['pass']}"
    }

# ==================== EXTRACTOR FLUMINER / ALTCOINS / ASICS UNIVERSALES ====================
def es_fluminer_o_altcoin(st, st_sum, ver_resp):
    texto_check = " ".join([
        str(st.get("Type", "")),
        str(st.get("Miner", "")),
        str(st.get("Description", "")),
        str(st_sum.get("Type", "")),
        str(st_sum.get("Miner", "")),
        str(ver_resp or "")
    ]).lower()
    if any(w in texto_check for w in ["fluminer", "flux", "iceriver", "goldshell", "jasminer", "innosilicon", "desiwe", "elphapex"]):
        return True
    return False

def extraer_fluminer_o_generico(ip, st, st_sum, ver_resp):
    global cache_info_estatica
    ahora_ts = time.time()
    estatica = cache_info_estatica.get(ip, {})

    version_raw = peticion_4028(ip, "version")
    ver_str = ""
    miner_str = ""
    if version_raw and isinstance(version_raw, dict):
        v_list = version_raw.get("VERSION", [])
        if v_list and isinstance(v_list[0], dict):
            ver_str = str(v_list[0].get("CGMiner", "") or v_list[0].get("API", "")).strip()
            miner_str = str(v_list[0].get("Miner", "")).strip()

    piscinas = peticion_4028(ip, "pools")
    pl = piscinas.get('POOLS', []) if isinstance(piscinas, dict) else []

    def datos_pool(idx):
        if idx < len(pl) and isinstance(pl[idx], dict):
            p = pl[idx]
            url = str(buscar_llave(p, ['URL', 'url'], ''))
            usr = str(buscar_llave(p, ['User', 'user'], ''))
            pas = str(buscar_llave(p, ['Pass', 'pass'], '123'))
            st_p = str(buscar_llave(p, ['Status', 'status'], 'Alive' if url else 'Dead'))
            acc = a_entero(buscar_llave(p, ['Accepted', 'accepted'], 0))
            rej = a_entero(buscar_llave(p, ['Rejected', 'rejected'], 0))
            return url, usr, pas, st_p, acc, rej
        return "", "", "123", "Dead", 0, 0

    p1_url, p1_usr, p1_pas, p1_st, p1_acc, p1_rej = datos_pool(0)
    p2_url, p2_usr, p2_pas, p2_st, p2_acc, p2_rej = datos_pool(1)
    p3_url, p3_usr, p3_pas, p3_st, p3_acc, p3_rej = datos_pool(2)

    cand_texto = f"{miner_str} {buscar_llave(st, ['Type', 'Model', 'Miner'], '')} {buscar_llave(st_sum, ['Type', 'Model'], '')} {p1_url}".lower()
    marca = "Fluminer" if ("fluminer" in cand_texto or "flux" in cand_texto) else (
        "IceRiver" if ("iceriver" in cand_texto or "ks" in cand_texto) else (
            "Goldshell" if "goldshell" in cand_texto else (
                "Jasminer" if "jasminer" in cand_texto else (
                    "Innosilicon" if "innosilicon" in cand_texto else "ASIC Universal"
                )
            )
        )
    )

    if not estatica or (ahora_ts - estatica.get('ts', 0) > 600):
        mod_det = miner_str or str(buscar_llave(st, ['Type', 'Model', 'Miner'], buscar_llave(st_sum, ['Type', 'Model'], f"{marca} ASIC"))).strip()
        serial = str(buscar_llave(st, ['Serial', 'sn', 'DNA'], f"{marca[:3].upper()}-{ip.replace('.', '')}")).strip()
        fw = ver_str or str(buscar_llave(st, ['Firmware Version', 'CGMiner'], f"{marca} Firmware")).strip()
        mac = str(buscar_llave(st, ['mac', 'MAC'], estatica.get('mac', '-'))).strip()
        hostname = str(buscar_llave(st, ['hostname'], f"{marca}{ip.split('.')[-1]}")).strip()

        estatica = {
            'ts': ahora_ts, 'marca': marca, 'modelo': mod_det, 'mac': mac,
            'serial': serial, 'fw': fw, 'hostname': hostname, 'net_proto': 'Static',
            'netmask': '255.255.255.0', 'gateway': f"{SUBNET}.1", 'dns': '8.8.8.8'
        }
        cache_info_estatica[ip] = estatica

    modelo_str = estatica.get('modelo', f"{marca} ASIC")
    algoritmo, moneda = detectar_algoritmo_y_moneda(modelo_str, p1_url)

    raw_5s = buscar_llave(st_sum, ['HS 5s', 'GHS 5s', 'MHS 5s', 'rate_5s', 'KHS 5s'], buscar_llave(st, ['GHS 5s', 'MHS 5s', 'HS 5s'], 0))
    raw_avg = buscar_llave(st_sum, ['HS av', 'GHS av', 'MHS av', 'rate_avg'], raw_5s)
    u_orig = "MHS"
    if 'GHS 5s' in st_sum or 'GHS av' in st_sum: u_orig = "GHS"
    elif 'THS 5s' in st_sum or 'THS rt' in st_sum: u_orig = "THS"
    elif 'KHS 5s' in st_sum: u_orig = "KHS"

    hr_5s, unidad_hr = normalizar_hashrate_universal(raw_5s, u_orig, modelo_str, algoritmo)
    hr_30m, _ = normalizar_hashrate_universal(raw_avg, u_orig, modelo_str, algoritmo)
    if hr_30m == 0: hr_30m = hr_5s

    c_w = a_float(buscar_llave(st_sum, ['Power', 'Power Limit'], buscar_llave(st, ['Power', 'power'], 0)))
    if c_w < 50 and hr_5s > 0:
        c_w = hr_5s * (30.0 if "TH" in unidad_hr else 1.2)

    uptime_str = formatear_uptime(buscar_llave(st_sum, ['Elapsed', 'Uptime'], 0))
    errores_hw = a_entero(buscar_llave(st_sum, ['Hardware Errors'], 0))

    tm = a_entero(buscar_llave(st, ['Temperature', 'Chip Temp Max', 'temp_max'], buscar_llave(st_sum, ['Temperature', 'Chip Temp Max'], 60)))
    if tm >= 150: tm = 0
    t_amb = a_entero(buscar_llave(st, ['Env Temp', 'temp_pcb', 'Inlet Temp'], 0))
    if t_amb >= 120: t_amb = 0

    fan1 = a_entero(buscar_llave(st, ['Fan Speed In', 'Fan1', 'fan1'], 0))
    fan2 = a_entero(buscar_llave(st, ['Fan Speed Out', 'Fan2', 'fan2'], 0))
    fans_str = f"{fan1} / {fan2} RPM" if (fan1 > 0 or fan2 > 0) else "Auto / Normal"

    cadenas = [{
        "id": 1, "temp_c": tm, "temp_p": max(0, tm - 8),
        "chips": 60, "hw": errores_hw, "freq": 0, "hr": hr_5s
    }]

    cred = determinar_credenciales_defecto(marca, modelo_str)

    return {
        "ip": ip, "marca": marca, "modelo": modelo_str,
        "algoritmo": algoritmo, "moneda": moneda, "unidad": unidad_hr,
        "hashrate": round(float(hr_5s), 2), "hashrate_30m": round(float(hr_30m), 2),
        "temp": int(tm), "temp_amb": int(t_amb), "consumo": round(float(c_w), 0), "errores": int(errores_hw),
        "fans": fans_str, "mac": estatica.get('mac', '-'), "serial": estatica.get('serial', f"{marca[:3].upper()}-{ip.replace('.', '')}"),
        "firmware": estatica.get('fw', f"{marca} Firmware"), "fw_tipo": "Stock", "perfil_oc": "", "cadenas": cadenas,
        "uptime": uptime_str,
        "hostname": estatica.get('hostname', f"{marca}{ip.split('.')[-1]}"),
        "net_proto": estatica.get('net_proto', 'Static'), "netmask": estatica.get('netmask', '255.255.255.0'),
        "gateway": estatica.get('gateway', f"{SUBNET}.1"), "dns": estatica.get('dns', '8.8.8.8'),
        "pool1": p1_url, "worker1": p1_usr, "pass1": p1_pas, "status1": p1_st, "acc1": int(p1_acc), "rej1": int(p1_rej),
        "pool2": p2_url, "worker2": p2_usr, "pass2": p2_pas, "status2": p2_st, "acc2": int(p2_acc), "rej2": int(p2_rej),
        "pool3": p3_url, "worker3": p3_usr, "pass3": p3_pas, "status3": p3_st, "acc3": int(p3_acc), "rej3": int(p3_rej),
        "cred_defecto": f"{cred['user']} / {cred['pass']}"
    }

def extraer_todo(ip):
    global cache_info_estatica
    summary = peticion_4028(ip, "summary")
    st_sum = {}
    if summary and 'SUMMARY' in summary and len(summary['SUMMARY']) > 0:
        st_sum = summary['SUMMARY'][0]

    if st_sum and es_whatsminer({}, st_sum, summary.get("STATUS", "")):
        return extraer_whatsminer(ip, st_sum, summary.get("STATUS", ""))

    if st_sum and es_avalon({}, st_sum, summary.get("STATUS", "")):
        return extraer_avalon(ip, st_sum, summary.get("STATUS", ""))

    stats = peticion_4028(ip, "stats")
    st = {}
    if stats and 'STATS' in stats:
        for item in stats['STATS']:
            if isinstance(item, dict):
                st.update(item)

    if (st or st_sum) and es_whatsminer(st, st_sum, ""):
        return extraer_whatsminer(ip, st_sum, "")

    if (st or st_sum) and es_avalon(st, st_sum, ""):
        return extraer_avalon(ip, st_sum, "")

    if (st or st_sum) and es_fluminer_o_altcoin(st, st_sum, ""):
        return extraer_fluminer_o_generico(ip, st, st_sum, "")

    if not st and not st_sum:
        raw_st_cgi = peticion_http(ip, "stats.cgi", timeout_sec=4.5)
        if raw_st_cgi:
            try:
                j_st = json.loads(raw_st_cgi)
                if isinstance(j_st, dict):
                    st = j_st.get('STATS', [j_st])[0] if isinstance(j_st.get('STATS'), list) and j_st.get('STATS') else j_st
            except:
                pass
        if not st:
            raw_sum_cgi = peticion_http(ip, "summary.cgi", timeout_sec=4.5)
            if raw_sum_cgi:
                try:
                    j_sum = json.loads(raw_sum_cgi)
                    if isinstance(j_sum, dict):
                        st_sum = j_sum.get('SUMMARY', [j_sum])[0] if isinstance(j_sum.get('SUMMARY'), list) and j_sum.get('SUMMARY') else j_sum
                except:
                    pass

    if not st and not st_sum:
        return None

    modelo_str = str(buscar_llave(st, ["Type", "Miner", "Model", "minertype", "Description"], buscar_llave(st_sum, ["Type", "Miner", "Description"], ""))).strip()

    ahora_ts = time.time()
    estatica = cache_info_estatica.get(ip, {})
    if not estatica or (ahora_ts - estatica.get('ts', 0) > 600):
        mac = estatica.get('mac', '-')
        fw = estatica.get('fw', 'Desconocido')
        hostname = estatica.get('hostname', f"Antminer{ip.split('.')[-1]}")
        net_proto = estatica.get('net_proto', 'Static')
        netmask = estatica.get('netmask', '255.255.255.0')
        gateway = estatica.get('gateway', f"{SUBNET}.1")
        dns = estatica.get('dns', '8.8.8.8')

        for k in ['Firmware Version', 'CompileTime', 'version', 'CGMiner', 'SubVersion']:
            val = str(buscar_llave(st, [k], '')).strip()
            if val and val.lower() != "release" and len(val) > 1:
                fw = val
                break

        info_http = peticion_http(ip, "get_system_info.cgi", timeout_sec=4.5)
        if info_http:
            try:
                j_net = json.loads(info_http)
                if not modelo_str or modelo_str.lower() == "antminer":
                    m_http = str(buscar_llave(j_net, ['minertype', 'model', 'type'], '')).strip()
                    if m_http:
                        modelo_str = m_http
                mac = str(buscar_llave(j_net, ['macaddr', 'mac'], mac))
                fw_cand = str(buscar_llave(j_net, ['system_filesystem_version', 'file_system_version', 'firmware_type', 'CompileTime'], '')).strip()
                if fw_cand and fw_cand.lower() != "release":
                    fw = fw_cand
                hostname = str(buscar_llave(j_net, ['hostname', 'ipHost'], hostname)).strip()
                p_cand = str(buscar_llave(j_net, ['nettype', 'conf_nettype', 'ipPro'], '')).strip()
                if p_cand:
                    net_proto = "DHCP" if "dhcp" in p_cand.lower() or p_cand == "1" else "Static"
                netmask = str(buscar_llave(j_net, ['netmask', 'conf_netmask', 'ipSub'], netmask)).strip()
                gateway = str(buscar_llave(j_net, ['gateway', 'conf_gateway', 'ipGateway'], gateway)).strip()
                dns = str(buscar_llave(j_net, ['dnsservers', 'conf_dnsservers', 'ipDns'], dns)).strip()
            except:
                pass

        estatica = {
            'ts': ahora_ts, 'marca': 'Antminer', 'modelo': modelo_str, 'mac': mac, 'fw': fw,
            'hostname': hostname, 'net_proto': net_proto, 'netmask': netmask, 'gateway': gateway, 'dns': dns
        }
        cache_info_estatica[ip] = estatica

    if not modelo_str and estatica.get('modelo'):
        modelo_str = estatica['modelo']

    mac = estatica.get('mac', '-')
    fw = estatica.get('fw', 'Desconocido')
    fw_tipo, perfil_oc = detectar_fw_custom(fw, st, st_sum)
    hostname = estatica.get('hostname', f"Antminer{ip.split('.')[-1]}")
    net_proto = estatica.get('net_proto', 'Static')
    netmask = estatica.get('netmask', '255.255.255.0')
    gateway = estatica.get('gateway', f"{SUBNET}.1")
    dns = estatica.get('dns', '8.8.8.8')

    piscinas = peticion_4028(ip, "pools")
    pl = piscinas.get('POOLS', []) if piscinas and isinstance(piscinas, dict) else []

    def datos_pool(idx):
        if idx < len(pl) and isinstance(pl[idx], dict):
            p = pl[idx]
            url = str(buscar_llave(p, ['URL', 'url'], ''))
            usr = str(buscar_llave(p, ['User', 'user', 'worker'], ''))
            pas = str(buscar_llave(p, ['Pass', 'pass', 'password'], '123'))
            st_p = str(buscar_llave(p, ['Status', 'status'], 'Alive' if url else 'Dead'))
            acc = a_entero(buscar_llave(p, ['Accepted', 'accepted', 'accept'], 0))
            rej = a_entero(buscar_llave(p, ['Rejected', 'rejected', 'reject'], 0))
            return url, usr, pas, st_p, acc, rej
        return "", "", "123", "Dead", 0, 0

    p1_url, p1_usr, p1_pas, p1_st, p1_acc, p1_rej = datos_pool(0)
    p2_url, p2_usr, p2_pas, p2_st, p2_acc, p2_rej = datos_pool(1)
    p3_url, p3_usr, p3_pas, p3_st, p3_acc, p3_rej = datos_pool(2)

    if (p1_acc + p2_acc + p3_acc) == 0:
        p1_acc = a_entero(buscar_llave(st_sum, ['Accepted', 'accepted'], 0))
        p1_rej = a_entero(buscar_llave(st_sum, ['Rejected', 'rejected'], 0))

    raw_5s = buscar_llave(st, ['GHS 5s', 'rate_5s'], buscar_llave(st_sum, ['GHS 5s', 'HS 5s'], None))
    u_detectada = "GHS"
    if raw_5s is None:
        raw_5s = buscar_llave(st_sum, ['THS 5s', 'THS rt'], None)
        if raw_5s is not None:
            u_detectada = "THS"
    if raw_5s is None:
        raw_5s = buscar_llave(st_sum, ['MHS 5s', 'MHS av'], 0)
        if a_float(raw_5s) > 0:
            u_detectada = "MHS"

    raw_avg = buscar_llave(st, ['GHS av', 'rate_avg', 'rate_30m'], buscar_llave(st_sum, ['GHS av', 'THS av', 'MHS av'], raw_5s))

    val_test = a_float(raw_5s)
    if not modelo_str or modelo_str.lower() in ["antminer", "bmminer", "cgminer"]:
        if 400 <= val_test <= 1100: modelo_str = "Antminer Z15 Pro"
        elif val_test > 380000 or (u_detectada == "THS" and val_test > 380): modelo_str = "Antminer S23 Hyd"
        elif val_test > 280000 or (u_detectada == "THS" and val_test > 280): modelo_str = "Antminer S23"
        elif val_test > 180000 or (u_detectada == "THS" and val_test > 180): modelo_str = "Antminer S21+"
        elif val_test > 130000 or (u_detectada == "THS" and val_test > 130): modelo_str = "Antminer S19 XP"
        elif val_test > 100000 or (u_detectada == "THS" and val_test > 100): modelo_str = "Antminer S19k Pro"
        else: modelo_str = "Antminer S19"

    algoritmo, moneda = detectar_algoritmo_y_moneda(modelo_str, p1_url)
    hr_5s, unidad_hr = normalizar_hashrate_universal(raw_5s, u_detectada, modelo_str, algoritmo)
    hr_30m, _ = normalizar_hashrate_universal(raw_avg, u_detectada, modelo_str, algoritmo)
    if hr_30m == 0:
        hr_30m = hr_5s

    mod_lower = modelo_str.lower()

    chips_por_defecto = 114
    efi = 17.5
    if "z15" in mod_lower: chips_por_defecto = 3; efi = 3.31
    elif "l9" in mod_lower: chips_por_defecto = 120; efi = 210.0
    elif "l7" in mod_lower: chips_por_defecto = 120; efi = 360.0
    elif "ks5" in mod_lower: chips_por_defecto = 92; efi = 150.0
    elif "ks3" in mod_lower: chips_por_defecto = 92; efi = 340.0
    elif "s23" in mod_lower: chips_por_defecto = 108; efi = 11.5 if "hyd" in mod_lower else 13.5
    elif "s21" in mod_lower: chips_por_defecto = 108; efi = 16.0 if "hyd" in mod_lower else 16.5
    elif "s19k" in mod_lower: chips_por_defecto = 77; efi = 23.0
    elif "xp" in mod_lower: chips_por_defecto = 110; efi = 20.8 if "hyd" in mod_lower else 21.5
    elif "s19" in mod_lower: chips_por_defecto = 114; efi = 29.5

    c_w = a_float(buscar_llave(st, ['watt', 'power', 'chain_power', 'power_consumption', 'psu_power'], buscar_llave(st_sum, ['Power', 'watt', 'Power Consumption'], 0)))
    if c_w < 100 and hr_5s > 0:
        c_w = hr_5s * efi
    elif hr_5s == 0:
        c_w = 120.0

    elapsed_sec = buscar_llave(st, ['Elapsed', 'uptime'], buscar_llave(st_sum, ['Elapsed', 'uptime'], 0))
    uptime_str = formatear_uptime(elapsed_sec)
    errores_hw = a_entero(buscar_llave(st, ['Hardware Errors', 'Device Hardware Errors', 'hw_errors'], buscar_llave(st_sum, ['Hardware Errors'], 0)))
    freq_global = a_entero(buscar_llave(st, ['frequency', 'freq_avg', 'freq', 'total_freqavg'], 0))

    es_hydro = ("hyd" in mod_lower or "hydro" in mod_lower or "water" in mod_lower or "m33" in mod_lower or "m53" in mod_lower or "m63" in mod_lower or "1566ha" in mod_lower)
    amb_candidates = []
    water_out_candidates = []

    for k, v in st.items():
        kl = str(k).lower()
        if any(w in kl for w in ['ambient', 'env_temp', 'temp_in', 'inlet', 'water_in', 'liquid_in']):
            val_a = a_entero_min(v)
            if 10 <= val_a <= 60:
                amb_candidates.append(val_a)
        elif any(w in kl for w in ['water_out', 'outlet', 'liquid_out', 'temp_out']):
            val_o = a_entero(v)
            if 15 <= val_o <= 85:
                water_out_candidates.append(val_o)

    cadenas = []
    temps_max = []

    s_chains = st.get('chain', [])
    if isinstance(s_chains, list) and len(s_chains) > 0:
        for idx, ch in enumerate(s_chains):
            if not isinstance(ch, dict):
                continue
            t_chip = a_entero(buscar_llave(ch, ['temp2', 'temp_chip', 'temp_max'], 0))
            raw_pcb = buscar_llave(ch, ['temp', 'temp_pcb', 'temp_pic', 'temp_water'], 0)
            nums_pcb = extraer_numeros(raw_pcb)
            t_pcb = max(nums_pcb) if nums_pcb else a_entero(raw_pcb)
            if t_pcb == 0 and t_chip > 0:
                t_pcb = max(0, t_chip - 5)

            if es_hydro and len(nums_pcb) >= 2:
                v_in = min(nums_pcb)
                v_out = max(nums_pcb)
                if 10 <= v_in <= 65:
                    amb_candidates.append(v_in)
                if 15 <= v_out <= 85:
                    water_out_candidates.append(v_out)
            else:
                raw_amb_ch = buscar_llave(ch, ['ambientemp', 'ambient_temp', 'env_temp', 'temp_in', 'water_in'], None)
                if raw_amb_ch is not None:
                    val_ac = a_entero_min(raw_amb_ch)
                    if 10 <= val_ac <= 55:
                        amb_candidates.append(val_ac)
                elif len(nums_pcb) >= 2 and min(nums_pcb) < (max(nums_pcb) - 5) and 15 <= min(nums_pcb) <= 55:
                    amb_candidates.append(min(nums_pcb))

            chips_num = a_entero(buscar_llave(ch, ['asic_num', 'chipNum', 'acn', 'chips', 'asic'], 0))
            hw_num = a_entero(buscar_llave(ch, ['hw', 'hwNum', 'hw_errors'], 0))
            freq_num = a_entero(buscar_llave(ch, ['freq_avg', 'freq', 'frequency'], freq_global))
            rate_raw = a_float(buscar_llave(ch, ['rate_real', 'rate', 'rate_ideal'], 0))
            hr_chain, _ = normalizar_hashrate_universal(rate_raw, u_detectada, modelo_str, algoritmo)

            if t_chip > 0 or t_pcb > 0 or chips_num > 0 or hr_chain > 0:
                if chips_num == 0 and (t_chip > 0 or hr_chain > 0):
                    chips_num = chips_por_defecto
                cadenas.append({
                    "id": len(cadenas), "temp_c": t_chip, "temp_p": t_pcb,
                    "chips": chips_num, "hw": hw_num, "freq": freq_num, "hr": hr_chain
                })
                if t_chip > 0:
                    temps_max.append(t_chip)

    if not cadenas:
        for i in range(1, 10):
            tc = buscar_llave(st, [f'temp2_{i}', f'temp3_{i}', f'temp_chip_{i}', f'temp_max_{i}'])
            tp = buscar_llave(st, [f'temp_{i}', f'temp1_{i}', f'temp_pcb_{i}', f'temp_pic_{i}'])
            acn = buscar_llave(st, [f'chain_acn{i}', f'chain acn{i}', f'acn{i}', f'acn_{i}', f'chain_acs{i}', f'chain acs{i}'])
            hw_c = buscar_llave(st, [f'chain_hw{i}', f'chain hw{i}', f'hw{i}', f'hw_{i}'])
            rt_c = buscar_llave(st, [f'chain_rate{i}', f'chain rate{i}', f'rate{i}', f'rate_{i}'])
            fq_c = buscar_llave(st, [f'freq_avg{i}', f'freq{i}', f'freq_{i}', f'chain_freq{i}'], freq_global)

            for raw_t in [tp, st.get(f'temp1_{i}'), st.get(f'temp_in_{i}'), st.get(f'ambientemp_{i}')]:
                if raw_t is not None:
                    nums_t = extraer_numeros(raw_t)
                    if es_hydro and len(nums_t) >= 2:
                        if 10 <= min(nums_t) <= 65:
                            amb_candidates.append(min(nums_t))
                        if 15 <= max(nums_t) <= 85:
                            water_out_candidates.append(max(nums_t))
                    elif len(nums_t) >= 2 and min(nums_t) < (max(nums_t) - 4) and 15 <= min(nums_t) <= 55:
                        amb_candidates.append(min(nums_t))

            t_chip = a_entero(tc)
            t_pcb = a_entero(tp)
            chips_num = a_entero(acn)
            hw_num = a_entero(hw_c)
            freq_num = a_entero(fq_c)
            hr_chain, _ = normalizar_hashrate_universal(rt_c, u_detectada, modelo_str, algoritmo)

            if t_chip > 0 or t_pcb > 0 or chips_num > 0 or hr_chain > 0:
                if t_pcb == 0 and t_chip > 0:
                    t_pcb = max(0, t_chip - 5)
                if chips_num == 0 and (t_chip > 0 or hr_chain > 0):
                    chips_num = chips_por_defecto
                cadenas.append({
                    "id": len(cadenas), "temp_c": t_chip, "temp_p": t_pcb,
                    "chips": chips_num, "hw": hw_num, "freq": freq_num, "hr": hr_chain
                })
                if t_chip > 0:
                    temps_max.append(t_chip)

    if not amb_candidates and any(m in mod_lower for m in ["s21", "s23", "hyd"]):
        raw_cgi = peticion_http(ip, "stats.cgi", timeout_sec=3.0)
        if raw_cgi:
            matches = re.findall(r'"(?:ambientemp|ambient_temp|env_temp|water_in_temp)"\s*:\s*([\d"\[\],\s.-]+)', raw_cgi, re.IGNORECASE)
            for m_str in matches:
                val_m = a_entero_min(m_str)
                if 10 <= val_m <= 60:
                    amb_candidates.append(val_m)

    if not cadenas:
        t_fallback = a_entero(st.get('temp_max', 65))
        num_placas_def = 4 if ("z15" in mod_lower or "l7" in mod_lower or "l9" in mod_lower) else 3
        hr_div = round(hr_5s / float(num_placas_def), 2)
        for p_idx in range(num_placas_def):
            cadenas.append({"id": p_idx, "temp_c": t_fallback, "temp_p": max(0, t_fallback-5), "chips": chips_por_defecto, "hw": 0, "freq": freq_global, "hr": hr_div})
        temps_max = [t_fallback]

    suma_hw_placas = sum(c.get('hw', 0) for c in cadenas)
    if suma_hw_placas == 0 and errores_hw > 0 and len(cadenas) > 0:
        cadenas[0]['hw'] = errores_hw

    for c in cadenas:
        if c.get('hr', 0) == 0 and hr_5s > 0:
            c['hr'] = round(hr_5s / len(cadenas), 2)

    tm = max(temps_max) if temps_max else 65
    temp_amb = (min(amb_candidates) if es_hydro else max(amb_candidates)) if amb_candidates else 0
    agua_out = max(water_out_candidates) if water_out_candidates else (max([c.get('temp_p', 0) for c in cadenas]) if cadenas else 0)

    fans = [str(st.get(f)) for f in ['fan1', 'fan2', 'fan3', 'fan4'] if st.get(f) and a_entero(st.get(f)) > 0]

    if fans and not es_hydro:
        fans_str = " / ".join(fans) + " RPM"
    elif es_hydro or (not fans and hr_5s > 0):
        if temp_amb > 0 and agua_out > 0:
            delta_t = max(0, agua_out - temp_amb)
            fans_str = f"💧 Hydro (Entrada {temp_amb}°C / Salida {agua_out}°C · ΔT {delta_t}°C)"
        elif temp_amb > 0:
            fans_str = f"💧 Hydro (Entrada {temp_amb}°C)"
        else:
            fans_str = "💧 Hydro (Refrigeración Líquida Activa)"
    else:
        fans_str = "0 / 0 RPM"

    return {
        "ip": ip, "marca": "Antminer", "modelo": modelo_str,
        "algoritmo": algoritmo, "moneda": moneda, "unidad": unidad_hr,
        "hashrate": round(float(hr_5s), 2), "hashrate_30m": round(float(hr_30m), 2),
        "temp": int(tm), "temp_amb": int(temp_amb), "consumo": round(float(c_w), 0), "errores": int(errores_hw),
        "fans": fans_str, "mac": mac, "serial": str(st.get('Hash Board SN', f"SN-{ip.replace('.', '')}")),
        "firmware": fw, "fw_tipo": fw_tipo, "perfil_oc": perfil_oc, "cadenas": cadenas,
        "uptime": uptime_str,
        "hostname": hostname, "net_proto": net_proto, "netmask": netmask, "gateway": gateway, "dns": dns,
        "pool1": p1_url, "worker1": p1_usr, "pass1": p1_pas, "status1": p1_st, "acc1": int(p1_acc), "rej1": int(p1_rej),
        "pool2": p2_url, "worker2": p2_usr, "pass2": p2_pas, "status2": p2_st, "acc2": int(p2_acc), "rej2": int(p2_rej),
        "pool3": p3_url, "worker3": p3_usr, "pass3": p3_pas, "status3": p3_st, "acc3": int(p3_acc), "rej3": int(p3_rej),
        "cred_defecto": "root / root"
    }

def escanear_red():
    print(f"Escaneando subred {SUBNET}.2 - {SUBNET}.254 (máx. {MAX_WORKERS} conexiones)...")
    encontradas = list(IPS_BASE)
    def probar_ip(ip):
        for port in (4028, 80):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((ip, port))
                s.close()
                return ip
            except:
                pass
        return None
    ips_a_probar = [f"{SUBNET}.{i}" for i in range(2, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for res in executor.map(probar_ip, ips_a_probar):
            if res and res not in encontradas:
                encontradas.append(res)
    print(f"Escaneo finalizado: {len(encontradas)} IP(s) monitoreadas: {encontradas}")
    return encontradas

def actualizar_config_minero_segura(ip_dest, nuevos_campos):
    conf_actual = {}
    raw_conf = peticion_http(ip_dest, "get_miner_conf.cgi", "GET", timeout_sec=5.0)
    if raw_conf:
        try:
            j_conf = json.loads(raw_conf)
            if isinstance(j_conf, dict):
                conf_actual = j_conf
        except:
            pass
    conf_actual.update(nuevos_campos)
    return peticion_http(ip_dest, "set_miner_conf.cgi", "POST", conf_actual, timeout_sec=5.0)

def cambiar_modo_inteligente(ip_dest, modo):
    es_wm = cache_info_estatica.get(ip_dest, {}).get('marca') == 'WhatsMiner'
    if es_wm:
        peticion_4028(ip_dest, "power_off" if modo == "sleep" else "power_on")
        return
    if modo == "sleep":
        actualizar_config_minero_segura(ip_dest, {"bitmain-work-mode": "1", "miner-mode": 1})
        peticion_4028(ip_dest, "pause")
        peticion_4028(ip_dest, "sleep")
        peticion_http(ip_dest, "sleep.cgi", "POST_EMPTY", timeout_sec=5.0)
        peticion_http(ip_dest, "sleep.cgi", "GET", timeout_sec=5.0)
    elif modo == "normal":
        actualizar_config_minero_segura(ip_dest, {"bitmain-work-mode": "0", "miner-mode": 0})
        peticion_4028(ip_dest, "resume")
        peticion_4028(ip_dest, "wakeup")
        peticion_http(ip_dest, "resume.cgi", "POST_EMPTY", timeout_sec=5.0)
        peticion_http(ip_dest, "resume.cgi", "GET", timeout_sec=5.0)

# ========= FIRMWARE OTA: Descargar + Flashear =========
def reportar_progreso_firmware(fw_id, ip, estado, progreso, mensaje=""):
    """Reporta progreso de flash al servidor."""
    try:
        requests.post(
            f"{URL_NUBE.replace('/api/recibir_datos', '')}/api/firmware/progreso",
            json={
                "api_key": API_KEY,
                "firmware_id": fw_id,
                "ip": ip,
                "estado": estado,
                "progreso": progreso,
                "mensaje": mensaje
            },
            timeout=10
        )
    except Exception as e:
        print(f"[FW-OTA] Error reportando progreso: {e}")

def flash_firmware_a_minero(fw_id, checksum, api_key_fw, ip_dest):
    """Descarga firmware del servidor y lo flashea al minero vía HTTP upload."""
    import hashlib, tempfile
    base_url = URL_NUBE.replace('/api/recibir_datos', '')

    reportar_progreso_firmware(fw_id, ip_dest, "en_progreso", 5, "Descargando firmware...")

    # 1) Descargar firmware del servidor
    try:
        r = requests.get(
            f"{base_url}/api/firmware/descargar/{fw_id}",
            headers={"api_key": api_key_fw},
            timeout=120, stream=True
        )
        if r.status_code != 200:
            reportar_progreso_firmware(fw_id, ip_dest, "error", 0, f"Error descarga: HTTP {r.status_code}")
            return
    except Exception as e:
        reportar_progreso_firmware(fw_id, ip_dest, "error", 0, f"Error descarga: {e}")
        return

    reportar_progreso_firmware(fw_id, ip_dest, "en_progreso", 30, "Verificando checksum SHA256...")

    # 2) Guardar en temporal y verificar checksum
    try:
        tmp_dir = tempfile.mkdtemp(prefix="fw_ota_")
        nombre_archivo = r.headers.get('Content-Disposition', '').split('filename=')[-1].strip('"') or f"firmware_{fw_id}.tar.gz"
        ruta_fw = os.path.join(tmp_dir, nombre_archivo)
        sha256 = hashlib.sha256()
        with open(ruta_fw, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                sha256.update(chunk)
        checksum_local = sha256.hexdigest()
        if checksum and checksum_local != checksum:
            reportar_progreso_firmware(fw_id, ip_dest, "error", 0, f"Checksum no coincide: {checksum_local[:12]}… ≠ {checksum[:12]}…")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return
    except Exception as e:
        reportar_progreso_firmware(fw_id, ip_dest, "error", 0, f"Error guardando archivo: {e}")
        return

    reportar_progreso_firmware(fw_id, ip_dest, "en_progreso", 50, "Subiendo firmware al ASIC...")

    # 3) Flashear al minero — intentar múltiples métodos
    exito = False
    msg_final = ""

    # Método A: HTTP upgrade endpoint (Antminer stock firmware)
    for usr, pwd in list(CREDENCIALES_HTTP):
        try:
            auth = HTTPDigestAuth(usr, pwd)
            with open(ruta_fw, 'rb') as fw_file:
                r_up = requests.post(
                    f"http://{ip_dest}/cgi-bin/upgrade.cgi",
                    auth=auth,
                    files={"datafile": (nombre_archivo, fw_file)},
                    headers={'Referer': f'http://{ip_dest}/'},
                    timeout=300
                )
                if r_up.status_code == 200:
                    exito = True
                    msg_final = "Firmware enviado OK vía HTTP upgrade.cgi"
                    break
        except Exception as e:
            msg_final = f"HTTP upgrade falló ({usr}): {e}"
            continue

    # Método B: Braiins OS / sysupgrade via SSH (si HTTP no funcionó)
    if not exito:
        try:
            sshpass_bin = shutil.which("sshpass")
            scp_bin = shutil.which("scp")
            ssh_bin = shutil.which("ssh")

            if sshpass_bin and scp_bin and ssh_bin:
                for usr, pwd in list(CREDENCIALES_HTTP):
                    reportar_progreso_firmware(fw_id, ip_dest, "en_progreso", 65, f"Intentando SSH ({usr})...")
                    ret_scp = subprocess.run(
                        [sshpass_bin, "-p", pwd, scp_bin,
                         "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
                         ruta_fw, f"{usr}@{ip_dest}:/tmp/{nombre_archivo}"],
                        capture_output=True, text=True, timeout=180
                    )
                    if ret_scp.returncode == 0:
                        reportar_progreso_firmware(fw_id, ip_dest, "en_progreso", 80, "Ejecutando sysupgrade...")
                        subprocess.run(
                            [sshpass_bin, "-p", pwd, ssh_bin,
                             "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                             f"{usr}@{ip_dest}",
                             f"nohup sysupgrade /tmp/{nombre_archivo} > /dev/null 2>&1 &"],
                            capture_output=True, text=True, timeout=30
                        )
                        exito = True
                        msg_final = f"Firmware enviado vía SSH+sysupgrade ({usr})"
                        break
                    else:
                        msg_final = f"SCP falló ({usr}): {ret_scp.stderr[:120]}"
            else:
                msg_final = "SSH no disponible (sshpass/scp no encontrados)"
        except subprocess.TimeoutExpired:
            msg_final = "Timeout en transferencia SSH"
        except Exception as e:
            msg_final = f"Error SSH: {e}"

    # 4) Limpiar y reportar resultado final
    shutil.rmtree(tmp_dir, ignore_errors=True)

    if exito:
        reportar_progreso_firmware(fw_id, ip_dest, "completado", 100, msg_final)
        print(f"[FW-OTA ✅] {ip_dest}: {msg_final}")
    else:
        reportar_progreso_firmware(fw_id, ip_dest, "error", 0, msg_final)
        print(f"[FW-OTA ❌] {ip_dest}: {msg_final}")


def procesar_comandos(ordenes):
    global ips_conocidas
    for cmd in ordenes:
        accion = cmd['comando']
        ip_dest = cmd['ip']
        es_wm = cache_info_estatica.get(ip_dest, {}).get('marca') == 'WhatsMiner'
        print(f"--> Ejecutando orden remota [{accion}] en {ip_dest} ({'WhatsMiner' if es_wm else 'Antminer'})")

        if accion in ("scan", "scan_now"):
            ips_conocidas = escanear_red()
        elif accion == "restart":
            if es_wm:
                peticion_4028(ip_dest, "reboot")
                peticion_4028(ip_dest, "restart_btminer")
            else:
                peticion_http(ip_dest, "reboot.cgi", "POST_EMPTY", timeout_sec=5.0)
                peticion_http(ip_dest, "reboot.cgi", "GET", timeout_sec=5.0)
                peticion_4028(ip_dest, "restart")
        elif accion == "reset":
            if not es_wm:
                formatear_de_fabrica(ip_dest)
        elif accion == "sleep":
            cambiar_modo_inteligente(ip_dest, "sleep")
        elif accion == "normal":
            cambiar_modo_inteligente(ip_dest, "normal")
        elif accion == "blink":
            if es_wm:
                peticion_4028(ip_dest, "set_led")
            else:
                peticion_http(ip_dest, "blink.cgi", "POST", {"blink": "true"}, timeout_sec=4.5)
        elif accion.startswith("change_pass|"):
            partes = accion.split('|')
            if len(partes) >= 3 and not es_wm:
                old_p, new_p = partes[1], partes[2]
                payload_pwd = {"cur_pwd": old_p, "new_pwd": new_p, "confirm_pwd": new_p}
                peticion_http(ip_dest, "set_passwd.cgi", "POST", payload_pwd, timeout_sec=5.0)
                peticion_http(ip_dest, "passwd.cgi", "POST_FORM", payload_pwd, timeout_sec=5.0)
                actualizar_credenciales_http(MINER_USER, new_p)
        elif accion.startswith("profile|"):
            modo_str = accion.split('|')[1].lower()
            if es_wm:
                peticion_4028(ip_dest, "set_low_power" if "low" in modo_str else "set_normal_power")
            else:
                val_modo = "2" if "low" in modo_str else "0"
                actualizar_config_minero_segura(ip_dest, {"bitmain-work-mode": val_modo, "miner-mode": int(val_modo)})
        elif accion.startswith("set_network|"):
            partes = accion.split('|')
            if len(partes) >= 7 and not es_wm:
                host, proto, nip, mask, gw, dns = partes[1], partes[2], partes[3], partes[4], partes[5], partes[6]
                payload_json = {"ipHost": host, "ipPro": int(proto) if proto.isdigit() else 2, "ipAddress": nip, "ipSub": mask, "ipGateway": gw, "ipDns": dns}
                payload_form = {"_ant_conf_hostname": host, "_ant_conf_nettype": "DHCP" if proto == "1" else "Static", "_ant_conf_ipaddress": nip, "_ant_conf_netmask": mask, "_ant_conf_gateway": gw, "_ant_conf_dnsservers": dns}
                peticion_http(ip_dest, "set_network_conf.cgi", "POST", payload_json, timeout_sec=5.0)
                peticion_http(ip_dest, "set_network_conf.cgi", "POST_FORM", payload_form, timeout_sec=5.0)
                if nip and nip not in ips_conocidas:
                    ips_conocidas.append(nip)
        elif accion.startswith("guardar_pools|"):
            partes = accion.split('|')
            if len(partes) >= 10:
                u1, w1, p1 = partes[1], partes[2], partes[3]
                u2, w2, p2 = partes[4], partes[5], partes[6]
                u3, w3, p3 = partes[7], partes[8], partes[9]
                if es_wm:
                    try: peticion_4028(ip_dest, "addpool", f"{u1},{w1},{p1}")
                    except: pass
                    if u2:
                        try: peticion_4028(ip_dest, "addpool", f"{u2},{w2},{p2}")
                        except: pass
                    if u3:
                        try: peticion_4028(ip_dest, "addpool", f"{u3},{w3},{p3}")
                        except: pass
                else:
                    nuevas_pools = {
                        "pools": [
                            {"url": u1, "user": w1, "pass": p1},
                            {"url": u2, "user": w2, "pass": p2},
                            {"url": u3, "user": w3, "pass": p3}
                        ]
                    }
                    actualizar_config_minero_segura(ip_dest, nuevas_pools)
                    payload_form_pools = {
                        "_ant_pool1url": u1, "_ant_pool1user": w1, "_ant_pool1pw": p1,
                        "_ant_pool2url": u2, "_ant_pool2user": w2, "_ant_pool2pw": p2,
                        "_ant_pool3url": u3, "_ant_pool3user": w3, "_ant_pool3pw": p3
                    }
                    peticion_http(ip_dest, "set_miner_conf.cgi", "POST_FORM", payload_form_pools, timeout_sec=5.0)
                    peticion_http(ip_dest, "set_miner_conf.cgi", "POST", nuevas_pools, timeout_sec=5.0)
                    try: peticion_4028(ip_dest, "addpool", f"{u1},{w1},{p1}")
                    except: pass
        elif accion in ["get_log", "clog", "hlog", "dlog"]:
            if not es_wm:
                endpoint = f"{accion}.cgi" if accion != "get_log" else "log.cgi"
                log_txt = peticion_http(ip_dest, endpoint, timeout_sec=5.0)
                if not log_txt and accion == "get_log":
                    log_txt = peticion_http(ip_dest, "get_kernel_log.cgi", timeout_sec=5.0)
                if log_txt:
                    try:
                        requests.post(URL_LOGS, json={"api_key": API_KEY, "ip": ip_dest, "log": log_txt}, timeout=6)
                    except:
                        pass
        elif accion.startswith("flash_firmware|"):
            partes = accion.split('|')
            if len(partes) >= 4:
                fw_id_str, fw_checksum, fw_api_key = partes[1], partes[2], partes[3]
                print(f"[FW-OTA] Iniciando flash de firmware #{fw_id_str} en {ip_dest}")
                try:
                    import threading
                    t = threading.Thread(
                        target=flash_firmware_a_minero,
                        args=(int(fw_id_str), fw_checksum, fw_api_key, ip_dest),
                        daemon=True
                    )
                    t.start()
                except Exception as e:
                    print(f"[FW-OTA] Error lanzando thread: {e}")
                    reportar_progreso_firmware(int(fw_id_str), ip_dest, "error", 0, f"Error lanzando flash: {e}")

def recolectar_y_enviar():
    global ips_conocidas
    verificar_actualizacion()

    # Escaneo automático desactivado: solo busca en la red cuando el usuario da la orden explícita (comando 'scan')
    for ip_b in IPS_BASE:
        if ip_b not in ips_conocidas:
            ips_conocidas.append(ip_b)

    datos = {
        "api_key": API_KEY,
        "agent_id": AGENT_ID,
        "device": obtener_telemetria_dispositivo(),
        "mineros": []
    }
    geo_wifi = obtener_ubicacion_wifi()
    if geo_wifi:
        datos["lat"] = geo_wifi["lat"]
        datos["lon"] = geo_wifi["lon"]
        datos["geo_source"] = "Wi-Fi PC"

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for res in executor.map(extraer_todo, ips_conocidas):
            if res:
                print(f"[{res['ip']}] {res['modelo']} ({res['fw_tipo']}) | {res['hashrate']} {res['unidad']} | Temp: {res['temp']}°C")
                datos["mineros"].append(res)

    try:
        r = requests.post(URL_NUBE, json=datos, timeout=15)
        if r.status_code == 200:
            resp_json = r.json()
            actualizar_credenciales_http(resp_json.get("miner_user"), resp_json.get("miner_pass"))
            print(f"[NUBE OK 200] {len(datos['mineros'])} mineros actualizados en línea (Sede: {AGENT_ID}).")
            ordenes = resp_json.get("ordenes", [])
            if ordenes:
                procesar_comandos(ordenes)
        else:
            print(f"[ERROR NUBE {r.status_code}] Respuesta: {r.text[:150]}")
    except Exception as e:
        print(f"[ERROR CONEXIÓN NUBE]: {e}")

if __name__ == '__main__':
    print(f"Iniciando Agente Universal El Minero Gris | Sede ID: {AGENT_ID}")
    keep_awake()
    while True:
        recolectar_y_enviar()
        time.sleep(25)
