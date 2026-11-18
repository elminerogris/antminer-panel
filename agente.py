import socket, json, time, requests, concurrent.futures, re
from requests.auth import HTTPDigestAuth

URL_NUBE = "https://antminer-panel.onrender.com/api/recibir_datos"
URL_LOGS = "https://antminer-panel.onrender.com/api/recibir_logs"
API_KEY = "TU_API_KEY_AQUI"
SUBNET = "192.168.0"
MAX_WORKERS = 15

MINER_USER = "root"
MINER_PASS = "root"

ips_conocidas = [
    f"{SUBNET}.11",
    f"{SUBNET}.12",
    f"{SUBNET}.13",
    f"{SUBNET}.14",
    f"{SUBNET}.15"
]

def peticion_4028(ip, comando, parametro=""):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.5)
        s.connect((ip, 4028))
        cmd_dict = {"command": comando}
        if parametro: cmd_dict["parameter"] = parametro
        s.sendall(json.dumps(cmd_dict).encode('utf-8'))
        resp = b""
        s.settimeout(1.5)
        while True:
            try:
                datos = s.recv(8192)
                if not datos: break
                resp += datos
            except socket.timeout: break
        s.close()
        txt = resp.decode('utf-8', errors='ignore').replace('\x00', '').strip()
        if not txt: return None
        if '}{' in txt:
            try: return json.loads(txt.replace('}{', ','))
            except: pass
            try: return json.loads(txt.split('}{')[0] + '}')
            except: pass
        return json.loads(txt)
    except:
        return None

def peticion_http(ip, endpoint, metodo="GET", payload=None):
    url = f"http://{ip}/cgi-bin/{endpoint}"
    auth = HTTPDigestAuth(MINER_USER, MINER_PASS)
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': f'http://{ip}/'}
    try:
        if metodo == "GET":
            r = requests.get(url, auth=auth, headers=headers, timeout=5)
        elif metodo == "POST_EMPTY":
            r = requests.post(url, auth=auth, headers=headers, data="", timeout=5)
        elif metodo == "POST_FORM":
            r = requests.post(url, auth=auth, headers=headers, data=payload, timeout=5)
        else:
            r = requests.post(url, auth=auth, headers=headers, json=payload, timeout=5)
        if r.status_code == 200: return r.text
    except: pass
    try:
        if metodo == "GET":
            r = requests.get(url, headers=headers, timeout=5)
        elif metodo == "POST_EMPTY":
            r = requests.post(url, headers=headers, data="", timeout=5)
        elif metodo == "POST_FORM":
            r = requests.post(url, headers=headers, data=payload, timeout=5)
        else:
            r = requests.post(url, headers=headers, json=payload, timeout=5)
        if r.status_code == 200: return r.text
    except: pass
    return None

def formatear_de_fabrica(ip):
    print(f"[{ip}] Ejecutando formateo de fábrica (Factory Reset)...")
    for ep in ["reset_conf.cgi", "factory_reset.cgi", "reset.cgi"]:
        res_post = peticion_http(ip, ep, "POST_EMPTY")
        res_get = peticion_http(ip, ep, "GET")
        if res_post is not None or res_get is not None:
            print(f"[{ip}] Comando de fábrica aceptado en {ep}.")

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
    peticion_http(ip, "set_miner_conf.cgi", "POST", payload_default)
    peticion_4028(ip, "removepool", "0")
    peticion_4028(ip, "removepool", "1")
    peticion_4028(ip, "removepool", "2")
    
    time.sleep(5)
    peticion_http(ip, "reboot.cgi", "GET")

def extraer_numeros(val):
    if val is None or val == "": return []
    if isinstance(val, (int, float)): return [int(val)]
    if isinstance(val, list):
        res = []
        for x in val: res.extend(extraer_numeros(x))
        return res
    nums = [int(float(x)) for x in re.findall(r'\d+(?:\.\d+)?', str(val))]
    return [n for n in nums if n > 0]

def a_entero(val):
    try:
        if val is None or val == "": return 0
        if isinstance(val, (int, float)): return int(val)
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
        if val is None or val == "": return 0.0
        if isinstance(val, (int, float)): return float(val)
        s_clean = re.sub(r'[^\d.-]', '', str(val).strip())
        return float(s_clean) if s_clean else 0.0
    except:
        return 0.0

def formatear_uptime(segundos):
    s = a_entero(segundos)
    if s <= 0: return "En línea"
    d = s // 86400
    h = (s % 86400) // 3600
    m = (s % 3600) // 60
    if d > 0: return f"{d}d {h}h {m}m"
    if h > 0: return f"{h}h {m}m"
    return f"{m}m {s % 60}s"

def buscar_llave(dic, posibles, defecto=None):
    if not isinstance(dic, dict): return defecto
    mapa = {str(k).lower().strip(): v for k, v in dic.items()}
    for p in posibles:
        k = p.lower().strip()
        if k in mapa and mapa[k] is not None and mapa[k] != "":
            return mapa[k]
    return defecto

def normalizar_hashrate_universal(valor_bruto, unidad_origen, modelo_str):
    mod = modelo_str.lower()
    val = a_float(valor_bruto)
    u_orig = str(unidad_origen or "").upper().strip()

    if "z15" in mod or "z11" in mod or "z9" in mod or "equihash" in mod:
        if val > 50000: val = val / 1000.0
        return round(val, 2), "kSol/s"

    if "l7" in mod or "l9" in mod or "l3" in mod:
        if val > 500: val = val / 1000.0
        return round(val, 2), "GH/s"

    if "e9" in mod or "etc" in mod:
        if val > 10000: val = val / 1000.0
        return round(val, 2), "GH/s"

    if u_orig == "MHS" or val > 500000:
        return round(val / 1000000.0, 2), "TH/s"
    if u_orig == "THS" or (0 < val < 1200):
        return round(val, 2), "TH/s"
    return round(val / 1000.0, 2), "TH/s"

def extraer_todo(ip):
    stats = peticion_4028(ip, "stats")
    summary = peticion_4028(ip, "summary")
    
    st = {}
    if stats and 'STATS' in stats:
        for item in stats['STATS']:
            if isinstance(item, dict): st.update(item)
            
    st_sum = {}
    if summary and 'SUMMARY' in summary and len(summary['SUMMARY']) > 0:
        st_sum = summary['SUMMARY'][0]

    if not st and not st_sum:
        return None

    # 1. DETECCIÓN DE MODELO Y SISTEMA INTELIGENTE (Braiins OS+, Vnish, LuxOS, S23, Hyd, Z15 Pro)
    modelo_str = str(buscar_llave(st, ["Type", "Miner", "Model", "minertype", "Description"], buscar_llave(st_sum, ["Type", "Miner", "Description"], ""))).strip()
    sistema_inteligente = ""

    ver_info = peticion_4028(ip, "version")
    v_data = ver_info['VERSION'][0] if ver_info and 'VERSION' in ver_info and len(ver_info['VERSION']) > 0 else {}
    if not modelo_str or modelo_str.lower() in ["antminer", "bmminer", "cgminer"]:
        m_ver = str(buscar_llave(v_data, ['Type', 'Model', 'Miner', 'Description'], '')).strip()
        if m_ver: modelo_str = m_ver

    raw_str_all = (str(v_data) + " " + str(st_sum)).lower()
    if "bos" in raw_str_all or "braiins" in raw_str_all: sistema_inteligente = "Braiins OS+"
    elif "vnish" in raw_str_all or "anthill" in raw_str_all: sistema_inteligente = "Vnish AutoTune"
    elif "luxos" in raw_str_all or "luxminer" in raw_str_all: sistema_inteligente = "LuxOS Smart"

    mac = "-"
    fw = "Desconocido"
    hostname = f"Antminer{ip.split('.')[-1]}"
    net_proto = "Static"
    netmask = "255.255.255.0"
    gateway = f"{SUBNET}.1"
    dns = "8.8.8.8"

    for ep_net in ["get_system_info.cgi", "get_network_info.cgi"]:
        info_http = peticion_http(ip, ep_net)
        if info_http:
            try:
                j_net = json.loads(info_http)
                if not modelo_str or modelo_str.lower() == "antminer":
                    m_http = str(buscar_llave(j_net, ['minertype', 'model', 'type'], '')).strip()
                    if m_http: modelo_str = m_http
                if mac == "-": mac = str(buscar_llave(j_net, ['macaddr', 'mac'], '-'))
                fw_cand = str(buscar_llave(j_net, ['system_filesystem_version', 'file_system_version', 'firmware_type', 'CompileTime'], '')).strip()
                if fw_cand and fw_cand.lower() != "release": fw = fw_cand
                h_cand = str(buscar_llave(j_net, ['hostname', 'ipHost'], '')).strip()
                if h_cand: hostname = h_cand
                p_cand = str(buscar_llave(j_net, ['nettype', 'conf_nettype', 'ipPro'], '')).strip()
                if p_cand: net_proto = "DHCP" if "dhcp" in p_cand.lower() or p_cand == "1" else "Static"
                m_cand = str(buscar_llave(j_net, ['netmask', 'conf_netmask', 'ipSub'], '')).strip()
                if m_cand: netmask = m_cand
                g_cand = str(buscar_llave(j_net, ['gateway', 'conf_gateway', 'ipGateway'], '')).strip()
                if g_cand: gateway = g_cand
                d_cand = str(buscar_llave(j_net, ['dnsservers', 'conf_dnsservers', 'ipDns'], '')).strip()
                if d_cand: dns = d_cand
            except: pass

    if fw == "Desconocido":
        for k in ['Firmware Version', 'CompileTime', 'version', 'CGMiner', 'SubVersion']:
            val = str(buscar_llave(st, [k], buscar_llave(v_data, [k], ''))).strip()
            if val and val.lower() != "release" and len(val) > 1:
                fw = val; break
    if sistema_inteligente and sistema_inteligente.lower() not in fw.lower():
        fw = f"{sistema_inteligente} ({fw})" if fw != "Desconocido" else sistema_inteligente

    # 2. EXTRACCIÓN DE HASHRATE Y CLASIFICACIÓN DE MODELO
    raw_5s = buscar_llave(st, ['GHS 5s', 'rate_5s'], buscar_llave(st_sum, ['GHS 5s', 'HS 5s'], None))
    u_detectada = "GHS"
    if raw_5s is None:
        raw_5s = buscar_llave(st_sum, ['THS 5s', 'THS rt'], None)
        if raw_5s is not None: u_detectada = "THS"
    if raw_5s is None:
        raw_5s = buscar_llave(st_sum, ['MHS 5s', 'MHS av'], 0)
        if a_float(raw_5s) > 0: u_detectada = "MHS"

    raw_avg = buscar_llave(st, ['GHS av', 'rate_avg', 'rate_30m'], buscar_llave(st_sum, ['GHS av', 'THS av', 'MHS av'], raw_5s))

    val_test = a_float(raw_5s)
    if not modelo_str or modelo_str.lower() in ["antminer", "bmminer", "cgminer"]:
        if 400 <= val_test <= 1100: modelo_str = "Antminer Z15 Pro"
        elif val_test > 380000: modelo_str = "Antminer S23 Hyd"
        elif val_test > 280000: modelo_str = "Antminer S23"
        elif val_test > 180000: modelo_str = "Antminer S21+"
        elif val_test > 130000: modelo_str = "Antminer S19 XP"
        elif val_test > 100000: modelo_str = "Antminer S19k Pro"
        else: modelo_str = "Antminer S19"

    hr_5s, unidad_hr = normalizar_hashrate_universal(raw_5s, u_detectada, modelo_str)
    hr_30m, _ = normalizar_hashrate_universal(raw_avg, u_detectada, modelo_str)
    if hr_30m == 0: hr_30m = hr_5s

    mod_lower = modelo_str.lower()

    # 3. CHIPS Y EFICIENCIA SEGÚN MODELO
    chips_por_defecto = 114
    efi = 17.5
    if "z15" in mod_lower:
        chips_por_defecto = 3
        efi = 3.31
    elif "s23" in mod_lower:
        chips_por_defecto = 108
        efi = 11.5 if "hyd" in mod_lower else 13.5
    elif "s21" in mod_lower:
        chips_por_defecto = 108
        efi = 16.0 if "hyd" in mod_lower else 16.5
    elif "s19k" in mod_lower:
        chips_por_defecto = 77; efi = 23.0
    elif "xp" in mod_lower:
        chips_por_defecto = 110; efi = 20.8 if "hyd" in mod_lower else 21.5
    elif "s19" in mod_lower:
        chips_por_defecto = 114; efi = 29.5

    c_w = a_float(buscar_llave(st, ['watt', 'power', 'chain_power', 'power_consumption', 'psu_power'], buscar_llave(st_sum, ['Power', 'watt', 'Power Consumption'], 0)))
    if c_w == 0:
        tuner = peticion_4028(ip, "tunerstatus")
        if tuner and 'TUNERSTATUS' in tuner and len(tuner['TUNERSTATUS']) > 0:
            c_w = a_float(buscar_llave(tuner['TUNERSTATUS'][0], ['ApproximatePowerConsumption', 'Power', 'PowerLimit'], 0))

    if c_w < 100 and hr_5s > 0:
        c_w = hr_5s * efi
    elif hr_5s == 0:
        c_w = 120.0

    # 4. TIEMPO ACTIVO Y POOLS
    elapsed_sec = buscar_llave(st, ['Elapsed', 'uptime'], buscar_llave(st_sum, ['Elapsed', 'uptime'], 0))
    uptime_str = formatear_uptime(elapsed_sec)

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

    errores_hw = a_entero(buscar_llave(st, ['Hardware Errors', 'Device Hardware Errors', 'hw_errors'], buscar_llave(st_sum, ['Hardware Errors'], 0)))
    freq_global = a_entero(buscar_llave(st, ['frequency', 'freq_avg', 'freq', 'total_freqavg'], 0))

    # 5. TEMPERATURA AMBIENTE / ENTRADA DE AGUA
    amb_candidates = []
    for k, v in st.items():
        kl = str(k).lower()
        if any(w in kl for w in ['ambient', 'env_temp', 'temp_in', 'inlet', 'water_in', 'liquid_in']):
            val_a = a_entero_min(v)
            if 10 <= val_a <= 60: amb_candidates.append(val_a)

    # 6. EXTRACCIÓN DE PLACAS DE HASH
    cadenas = []
    temps_max = []

    s_chains = st.get('chain', [])
    if isinstance(s_chains, list) and len(s_chains) > 0:
        for idx, ch in enumerate(s_chains):
            if not isinstance(ch, dict): continue
            t_chip = a_entero(buscar_llave(ch, ['temp2', 'temp_chip', 'temp_max'], 0))
            raw_pcb = buscar_llave(ch, ['temp', 'temp_pcb', 'temp_pic', 'temp_water'], 0)
            t_pcb = a_entero(raw_pcb)
            if t_pcb == 0 and t_chip > 0: t_pcb = max(0, t_chip - 5)
            
            raw_amb_ch = buscar_llave(ch, ['ambientemp', 'ambient_temp', 'env_temp', 'temp_in', 'water_in'], None)
            if raw_amb_ch is not None:
                val_ac = a_entero_min(raw_amb_ch)
                if 10 <= val_ac <= 55: amb_candidates.append(val_ac)
            elif isinstance(raw_pcb, (list, str)):
                nums_pcb = extraer_numeros(raw_pcb)
                if len(nums_pcb) >= 2 and min(nums_pcb) < (max(nums_pcb) - 5) and 15 <= min(nums_pcb) <= 55:
                    amb_candidates.append(min(nums_pcb))
            
            chips_num = a_entero(buscar_llave(ch, ['asic_num', 'chipNum', 'acn', 'chips', 'asic'], 0))
            hw_num = a_entero(buscar_llave(ch, ['hw', 'hwNum', 'hw_errors'], 0))
            freq_num = a_entero(buscar_llave(ch, ['freq_avg', 'freq', 'frequency'], freq_global))
            rate_raw = a_float(buscar_llave(ch, ['rate_real', 'rate', 'rate_ideal'], 0))
            hr_chain, _ = normalizar_hashrate_universal(rate_raw, u_detectada, modelo_str)

            if t_chip > 0 or t_pcb > 0 or chips_num > 0 or hr_chain > 0:
                if chips_num == 0 and (t_chip > 0 or hr_chain > 0):
                    chips_num = chips_por_defecto
                cadenas.append({
                    "id": len(cadenas),
                    "temp_c": t_chip,
                    "temp_p": t_pcb,
                    "chips": chips_num,
                    "hw": hw_num,
                    "freq": freq_num,
                    "hr": hr_chain
                })
                if t_chip > 0: temps_max.append(t_chip)

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
                    if len(nums_t) >= 2 and min(nums_t) < (max(nums_t) - 4) and 15 <= min(nums_t) <= 55:
                        amb_candidates.append(min(nums_t))

            t_chip = a_entero(tc)
            t_pcb = a_entero(tp)
            chips_num = a_entero(acn)
            hw_num = a_entero(hw_c)
            freq_num = a_entero(fq_c)
            hr_chain, _ = normalizar_hashrate_universal(rt_c, u_detectada, modelo_str)

            if t_chip > 0 or t_pcb > 0 or chips_num > 0 or hr_chain > 0:
                if t_pcb == 0 and t_chip > 0: t_pcb = max(0, t_chip - 5)
                if chips_num == 0 and (t_chip > 0 or hr_chain > 0):
                    chips_num = chips_por_defecto
                cadenas.append({
                    "id": len(cadenas),
                    "temp_c": t_chip,
                    "temp_p": t_pcb,
                    "chips": chips_num,
                    "hw": hw_num,
                    "freq": freq_num,
                    "hr": hr_chain
                })
                if t_chip > 0: temps_max.append(t_chip)

    if not cadenas:
        devs = peticion_4028(ip, "devs")
        if devs and 'DEVS' in devs and len(devs['DEVS']) > 0:
            for idx, dv in enumerate(devs['DEVS']):
                t_chip = a_entero(buscar_llave(dv, ['Temperature', 'Chip Temp', 'temp'], 65))
                hr_raw = buscar_llave(dv, ['MHS 5s', 'GHS 5s', 'THS 5s'], 0)
                hr_chain, _ = normalizar_hashrate_universal(hr_raw, "MHS" if "MHS 5s" in dv else u_detectada, modelo_str)
                hw_num = a_entero(buscar_llave(dv, ['Hardware Errors', 'hw'], 0))
                freq_num = a_entero(buscar_llave(dv, ['Frequency', 'freq'], freq_global))
                cadenas.append({
                    "id": idx,
                    "temp_c": t_chip,
                    "temp_p": max(0, t_chip - 5),
                    "chips": chips_por_defecto,
                    "hw": hw_num,
                    "freq": freq_num,
                    "hr": hr_chain
                })
                if t_chip > 0: temps_max.append(t_chip)

    if not amb_candidates and any(m in mod_lower for m in ["s21", "s23", "hyd"]):
        for cgi_ep in ["stats.cgi", "summary.cgi"]:
            raw_cgi = peticion_http(ip, cgi_ep)
            if raw_cgi:
                matches = re.findall(r'"(?:ambientemp|ambient_temp|env_temp|water_in_temp)"\s*:\s*([\d"\[\],\s.-]+)', raw_cgi, re.IGNORECASE)
                for m_str in matches:
                    val_m = a_entero_min(m_str)
                    if 10 <= val_m <= 60:
                        amb_candidates.append(val_m)
                if amb_candidates: break

    if not cadenas:
        t_fallback = a_entero(st.get('temp_max', 65))
        num_placas_def = 4 if "z15" in mod_lower else 3
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
    temp_amb = max(amb_candidates) if amb_candidates else 0

    # 7. DETECCIÓN INTELIGENTE DE VENTILADORES vs HYDRO
    fans = [str(st.get(f)) for f in ['fan1', 'fan2', 'fan3', 'fan4'] if st.get(f) and a_entero(st.get(f)) > 0]
    es_hydro = ("hyd" in mod_lower or "hydro" in mod_lower or "water" in mod_lower)
    
    if fans and not es_hydro:
        fans_str = " / ".join(fans) + " RPM"
    elif es_hydro or (not fans and hr_5s > 0):
        if temp_amb > 0:
            fans_str = f"💧 Hydro (Entrada {temp_amb}°C / Salida {max(temp_amb+5, tm-12)}°C)"
        else:
            fans_str = "💧 Hydro (Refrigeración Líquida Activa)"
    else:
        fans_str = "0 / 0 RPM"

    return {
        "ip": ip, "modelo": modelo_str, "unidad": unidad_hr, "hashrate": round(hr_5s, 2), "hashrate_30m": round(hr_30m, 2),
        "temp": tm, "temp_amb": temp_amb, "consumo": round(c_w, 0), "errores": errores_hw,
        "fans": fans_str, "mac": mac, "serial": st.get('Hash Board SN', f"SN-{ip.replace('.', '')}"), "firmware": fw, "cadenas": cadenas,
        "uptime": uptime_str,
        "hostname": hostname, "net_proto": net_proto, "netmask": netmask, "gateway": gateway, "dns": dns,
        "pool1": p1_url, "worker1": p1_usr, "pass1": p1_pas, "status1": p1_st, "acc1": p1_acc, "rej1": p1_rej,
        "pool2": p2_url, "worker2": p2_usr, "pass2": p2_pas, "status2": p2_st, "acc2": p2_acc, "rej2": p2_rej,
        "pool3": p3_url, "worker3": p3_usr, "pass3": p3_pas, "status3": p3_st, "acc3": p3_acc, "rej3": p3_rej
    }

def escanear_red():
    print(f"Escaneando subred {SUBNET}.2 - {SUBNET}.254 en busca de mineros...")
    encontradas = []
    def probar_4028(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.35); s.connect((ip, 4028)); s.close(); return ip
        except: return None
    ips_a_probar = [f"{SUBNET}.{i}" for i in range(2, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=35) as executor:
        for res in executor.map(probar_4028, ips_a_probar):
            if res: encontradas.append(res)
    print(f"Escaneo finalizado: {len(encontradas)} minero(s) detectado(s).")
    return encontradas

def cambiar_modo_inteligente(ip_dest, modo):
    if modo == "sleep":
        peticion_http(ip_dest, "set_miner_conf.cgi", "POST", {"bitmain-work-mode": "1", "miner-mode": 1})
        peticion_4028(ip_dest, "pause")
    elif modo == "normal":
        peticion_http(ip_dest, "set_miner_conf.cgi", "POST", {"bitmain-work-mode": "0", "miner-mode": 0})
        peticion_4028(ip_dest, "resume")

def procesar_comandos(ordenes):
    global ips_conocidas
    for cmd in ordenes:
        accion = cmd['comando']; ip_dest = cmd['ip']
        if accion == "scan":
            nuevas_ips = escanear_red()
            if nuevas_ips: ips_conocidas = nuevas_ips
        elif accion == "restart":
            peticion_http(ip_dest, "reboot.cgi")
            peticion_4028(ip_dest, "restart")
        elif accion == "reset":
            formatear_de_fabrica(ip_dest)
        elif accion == "sleep":
            cambiar_modo_inteligente(ip_dest, "sleep")
        elif accion == "normal":
            cambiar_modo_inteligente(ip_dest, "normal")
        elif accion == "blink":
            peticion_http(ip_dest, "blink.cgi", "POST", {"blink": "true"})
        elif accion.startswith("set_network|"):
            partes = accion.split('|')
            if len(partes) >= 7:
                host, proto, nip, mask, gw, dns = partes[1], partes[2], partes[3], partes[4], partes[5], partes[6]
                payload_json = {
                    "ipHost": host,
                    "ipPro": int(proto) if proto.isdigit() else 2,
                    "ipAddress": nip,
                    "ipSub": mask,
                    "ipGateway": gw,
                    "ipDns": dns
                }
                payload_form = {
                    "_ant_conf_hostname": host,
                    "_ant_conf_nettype": "DHCP" if proto == "1" else "Static",
                    "_ant_conf_ipaddress": nip,
                    "_ant_conf_netmask": mask,
                    "_ant_conf_gateway": gw,
                    "_ant_conf_dnsservers": dns
                }
                peticion_http(ip_dest, "set_network_conf.cgi", "POST", payload_json)
                peticion_http(ip_dest, "set_network_conf.cgi", "POST_FORM", payload_form)
                if nip and nip not in ips_conocidas:
                    ips_conocidas.append(nip)
        elif accion.startswith("guardar_pools|"):
            partes = accion.split('|')
            if len(partes) >= 10:
                payload = {
                    "pools": [
                        {"url": partes[1], "user": partes[2], "pass": partes[3]},
                        {"url": partes[4], "user": partes[5], "pass": partes[6]},
                        {"url": partes[7], "user": partes[8], "pass": partes[9]}
                    ]
                }
                peticion_http(ip_dest, "set_miner_conf.cgi", "POST", payload)
                peticion_4028(ip_dest, "removepool", "0")
                peticion_4028(ip_dest, "removepool", "1")
                peticion_4028(ip_dest, "removepool", "2")
                time.sleep(0.3)
                if partes[1]: peticion_4028(ip_dest, "addpool", f"{partes[1]},{partes[2]},{partes[3]}")
                if partes[4]: peticion_4028(ip_dest, "addpool", f"{partes[4]},{partes[5]},{partes[6]}")
                if partes[7]: peticion_4028(ip_dest, "addpool", f"{partes[7]},{partes[8]},{partes[9]}")
        elif accion in ["get_log", "clog", "hlog", "dlog"]:
            endpoint = f"{accion}.cgi" if accion != "get_log" else "log.cgi"
            log_txt = peticion_http(ip_dest, endpoint)
            if not log_txt and accion == "get_log":
                log_txt = peticion_http(ip_dest, "get_kernel_log.cgi")
            if log_txt: 
                try: requests.post(URL_LOGS, json={"api_key": API_KEY, "ip": ip_dest, "log": log_txt}, timeout=5)
                except: pass

def recolectar_y_enviar():
    global ips_conocidas
    datos = {"api_key": API_KEY, "mineros": []}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for res in executor.map(extraer_todo, ips_conocidas):
            if res:
                print(f"[{res['ip']}] {res['modelo']} | {res['hashrate']} {res['unidad']} | {res['fans']} | Acc1: {res['acc1']}")
                datos["mineros"].append(res)
    
    # Si en las IPs por defecto no respondió ningún minero, auto-escanear la subred automáticamente
    if len(datos["mineros"]) == 0:
        nuevas = escanear_red()
        if nuevas:
            ips_conocidas = nuevas
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                for res in executor.map(extraer_todo, ips_conocidas):
                    if res:
                        print(f"[{res['ip']}] {res['modelo']} | {res['hashrate']} {res['unidad']} | {res['fans']} | Acc1: {res['acc1']}")
                        datos["mineros"].append(res)

    try:
        r = requests.post(URL_NUBE, json=datos, timeout=12)
        if r.status_code == 200:
            ordenes = r.json().get("ordenes", [])
            if ordenes: procesar_comandos(ordenes)
    except Exception as e:
        print(f"Error nube: {e}")

if __name__ == '__main__':
    print("Iniciando Agente Universal El Minero Gris (S19/S21/S23/Hyd/Z15/SmartOS)...")
    while True:
        recolectar_y_enviar()
        time.sleep(30)