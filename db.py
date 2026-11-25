from flask import g
from werkzeug.security import generate_password_hash
from datetime import datetime
import os, psycopg2
from utils import generar_api_key

DB_URL = os.environ.get("DATABASE_URL", "")
db_inicializada = False

def get_db():
    if not DB_URL:
        raise Exception("Falta DATABASE_URL en Render")
    if 'db_con' not in g or g.db_con.closed != 0:
        con = psycopg2.connect(DB_URL, connect_timeout=8)
        con.autocommit = True
        g.db_con = con
    else:
        try:
            if not g.db_con.autocommit:
                g.db_con.rollback()
                g.db_con.autocommit = True
        except:
            pass
    return g.db_con

def cerrar_conexion_bd(exception=None):
    con = g.pop('db_con', None)
    if con is not None and con.closed == 0:
        try: con.close()
        except: pass

def asegurar_bd_inicializada():
    if not db_inicializada:
        init_db()

def init_db():
    global db_inicializada
    if not DB_URL: return
    con = None
    try:
        con = psycopg2.connect(DB_URL, connect_timeout=8)
        con.autocommit = True
        c = con.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS mineros_activos (ip TEXT, owner TEXT DEFAULT 'Admin', agent_id TEXT DEFAULT 'default', farm_id TEXT DEFAULT '', miner_uid TEXT DEFAULT '', modelo TEXT, algoritmo TEXT DEFAULT 'SHA-256', moneda TEXT DEFAULT 'BTC', unidad TEXT DEFAULT 'TH/s', hashrate REAL, hashrate_30m REAL, temp_max INTEGER, temp_amb INTEGER DEFAULT 0, consumo_w REAL, estado TEXT, ultima_act TEXT, errores INTEGER, pool1 TEXT, worker1 TEXT, pass1 TEXT, pool2 TEXT, worker2 TEXT, pass2 TEXT, pool3 TEXT, worker3 TEXT, pass3 TEXT, mac TEXT, serial TEXT, firmware TEXT, fw_tipo TEXT DEFAULT 'Stock', perfil_oc TEXT DEFAULT '', fans TEXT, cadenas TEXT, uptime TEXT DEFAULT '0h 0m', status1 TEXT DEFAULT 'Alive', acc1 INTEGER DEFAULT 0, rej1 INTEGER DEFAULT 0, status2 TEXT DEFAULT 'Alive', acc2 INTEGER DEFAULT 0, rej2 INTEGER DEFAULT 0, status3 TEXT DEFAULT 'Dead', acc3 INTEGER DEFAULT 0, rej3 INTEGER DEFAULT 0, hostname TEXT DEFAULT 'Antminer', net_proto TEXT DEFAULT 'Static', netmask TEXT DEFAULT '255.255.255.0', gateway TEXT DEFAULT '192.168.0.1', dns TEXT DEFAULT '8.8.8.8', farm_lat REAL DEFAULT -17.7833, farm_lon REAL DEFAULT -63.1821, farm_city TEXT DEFAULT 'Santa Cruz de la Sierra', farm_country TEXT DEFAULT 'Bolivia', gps_manual INTEGER DEFAULT 0, real_lat REAL DEFAULT 0.0, real_lon REAL DEFAULT 0.0, real_source TEXT DEFAULT 'IP Pública', corte_activo INTEGER DEFAULT 0, corte_temp INTEGER DEFAULT 85, watchdog_activo INTEGER DEFAULT 1, wd_fallo_desde TEXT DEFAULT '', wd_motivo_fallo TEXT DEFAULT '', wd_ultimo_reinicio TEXT DEFAULT '', wd_reinicios_seguidos INTEGER DEFAULT 0, wd_cuarentena INTEGER DEFAULT 0, placas_normales INTEGER DEFAULT 0, prot_sleep_desde TEXT DEFAULT '', prot_motivo TEXT DEFAULT '', rack_nombre TEXT DEFAULT '', rack_fila INTEGER DEFAULT 0, rack_col INTEGER DEFAULT 0, alerta_hr_disparada INTEGER DEFAULT 0, alerta_temp_disparada INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS granjas_sedes (
            id SERIAL PRIMARY KEY,
            owner TEXT DEFAULT 'Admin',
            farm_id TEXT NOT NULL,
            agent_id TEXT DEFAULT '',
            nombre TEXT DEFAULT '',
            ciudad TEXT DEFAULT 'Santa Cruz de la Sierra',
            subred TEXT DEFAULT '192.168.0.x',
            gateway TEXT DEFAULT '192.168.0.1',
            router_mac TEXT DEFAULT '',
            cap_kw REAL DEFAULT 50.0,
            tarifa_kwh REAL DEFAULT 0.06,
            notas TEXT DEFAULT '',
            device_type TEXT DEFAULT 'Desconocido',
            run_mode TEXT DEFAULT 'Agente',
            brand TEXT DEFAULT '',
            model TEXT DEFAULT '',
            local_ip TEXT DEFAULT '',
            has_battery INTEGER DEFAULT 0,
            battery_pct INTEGER DEFAULT -1,
            charging INTEGER DEFAULT 0,
            battery_temp_c REAL DEFAULT 0.0,
            os_info TEXT DEFAULT '',
            app_version TEXT DEFAULT '',
            ultima_act TEXT DEFAULT '',
            zt_activo INTEGER DEFAULT 0,
            zt_pool1 TEXT DEFAULT '',
            zt_worker_prefix TEXT DEFAULT '',
            zt_pass1 TEXT DEFAULT '123',
            zt_pool2 TEXT DEFAULT '',
            zt_pool3 TEXT DEFAULT ''
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT, email TEXT DEFAULT '', fecha_registro TEXT DEFAULT '', suspendido INTEGER DEFAULT 0, plan TEXT DEFAULT 'Gratis', saldo REAL DEFAULT 0.0, saldo_bonus REAL DEFAULT 0.0, pro_hasta TEXT DEFAULT '', limite_mineros INTEGER DEFAULT 0, notas_admin TEXT DEFAULT '', ultima_conexion TEXT DEFAULT '', api_key TEXT, subnet TEXT DEFAULT '192.168.0', tarifa_kwh REAL DEFAULT 0.0, comision_pool REAL DEFAULT 0.0, miner_user TEXT DEFAULT 'root', miner_pass TEXT DEFAULT '', parent_owner TEXT DEFAULT '', rol TEXT DEFAULT 'owner', tg_token TEXT DEFAULT '', tg_chat_id TEXT DEFAULT '', umbral_temp INTEGER DEFAULT 80, umbral_hr REAL DEFAULT 50.0, alertas_activas INTEGER DEFAULT 0, wd_activo INTEGER DEFAULT 0, wd_alcance TEXT DEFAULT 'all', wd_granjas TEXT DEFAULT '[]', wd_mineros TEXT DEFAULT '[]', wd_zero_hr INTEGER DEFAULT 0, wd_cold_on INTEGER DEFAULT 0, wd_cold_temp INTEGER DEFAULT 31, wd_amb_cold_on INTEGER DEFAULT 0, wd_amb_cold_temp INTEGER DEFAULT 18, wd_dead_board INTEGER DEFAULT 0, wd_gracia_min INTEGER DEFAULT 5, wd_cooldown_min INTEGER DEFAULT 30, wd_cuarentena_on INTEGER DEFAULT 0, wd_max_reinicios INTEGER DEFAULT 3, prot_activo INTEGER DEFAULT 0, prot_temp_on INTEGER DEFAULT 0, prot_temp_val INTEGER DEFAULT 85, prot_temp_accion TEXT DEFAULT 'sleep', prot_clima_on INTEGER DEFAULT 0, prot_clima_val INTEGER DEFAULT 38, prot_clima_accion TEXT DEFAULT 'sleep', prot_net_on INTEGER DEFAULT 0, prot_net_rej_pct REAL DEFAULT 5.0, prot_net_accion TEXT DEFAULT 'sleep', prot_fan_on INTEGER DEFAULT 0, prot_fan_accion TEXT DEFAULT 'sleep', prot_wakeup_on INTEGER DEFAULT 0, prot_wakeup_min INTEGER DEFAULT 30, curt_activo INTEGER DEFAULT 0, curt_inicio TEXT DEFAULT '18:00', curt_fin TEXT DEFAULT '22:00', curt_accion TEXT DEFAULT 'profile|low', curt_estado_actual TEXT DEFAULT 'normal', mineros_omitidos INTEGER DEFAULT 0, totp_secret TEXT DEFAULT '', totp_activo INTEGER DEFAULT 0, email_verificado INTEGER DEFAULT 1, email_token TEXT DEFAULT '', email_token_exp TEXT DEFAULT '')''')
        try:
            c.execute("UPDATE usuarios SET email_verificado = 1 WHERE email_verificado = 0")
        except:
            pass
        c.execute('''CREATE TABLE IF NOT EXISTS cola_comandos (id SERIAL PRIMARY KEY, owner TEXT DEFAULT 'Admin', agent_id TEXT DEFAULT 'default', ip TEXT, comando TEXT, estado TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS historial_nube (id SERIAL PRIMARY KEY, owner TEXT DEFAULT 'Admin', ip TEXT, hashrate REAL, temp_max INTEGER, temp_amb INTEGER DEFAULT 0, errores INTEGER DEFAULT 0, cadenas TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS nombres_mineros (ip TEXT, owner TEXT DEFAULT 'Admin', nombre TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs_mineros (ip TEXT, owner TEXT DEFAULT 'Admin', contenido TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS registro_alertas (id SERIAL PRIMARY KEY, owner TEXT, ip TEXT, tipo TEXT, mensaje TEXT, fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS movimientos_saldo (id SERIAL PRIMARY KEY, username TEXT, tipo TEXT, monto REAL, nota TEXT DEFAULT '', fecha TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS config_global (clave TEXT PRIMARY KEY, valor TEXT DEFAULT '')''')
        c.execute('''CREATE TABLE IF NOT EXISTS dispositivos_confianza (
            id SERIAL PRIMARY KEY,
            username TEXT,
            token_hash TEXT UNIQUE,
            dispositivo TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            fecha_creacion TEXT DEFAULT '',
            expira_en TEXT DEFAULT ''
        )''')

        # --- Tablas Pro: Racks, Enlaces de Hosting (Solo Lectura) y Pagos Automáticos USDT ---
        c.execute('''CREATE TABLE IF NOT EXISTS racks_sedes (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            farm_id TEXT DEFAULT '',
            nombre TEXT NOT NULL,
            filas INTEGER DEFAULT 4,
            columnas INTEGER DEFAULT 6
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS enlaces_hosting (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            titulo TEXT DEFAULT 'Vista de Cliente',
            ips_json TEXT DEFAULT '[]',
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT ''
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS pagos_usdt (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            order_id TEXT UNIQUE NOT NULL,
            provider TEXT DEFAULT 'nowpayments',
            payment_id TEXT DEFAULT '',
            monto_usd REAL DEFAULT 0.0,
            estado TEXT DEFAULT 'pendiente',
            pay_address TEXT DEFAULT '',
            pay_currency TEXT DEFAULT 'usdttrc20',
            fecha_creacion TEXT DEFAULT '',
            fecha_pago TEXT DEFAULT ''
        )''')

        # --- Tabla de Clientes Hosting con Login Propio y Permisos Granulares ---
        c.execute('''CREATE TABLE IF NOT EXISTS hosting_cuentas (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            nombre_display TEXT DEFAULT 'Cliente',
            email TEXT DEFAULT '',
            ips_json TEXT DEFAULT '[]',
            permisos_json TEXT DEFAULT '{"ver_estado":true,"ver_logs":false,"reiniciar":false,"cambiar_pools":false,"blink_led":false}',
            activo INTEGER DEFAULT 1,
            ultimo_acceso TEXT DEFAULT '',
            fecha_creacion TEXT DEFAULT '',
            notas TEXT DEFAULT ''
        )''')

        # --- Tabla de Firmwares subidos para OTA masivo ---
        c.execute('''CREATE TABLE IF NOT EXISTS firmware_ota (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            nombre TEXT NOT NULL,
            nombre_archivo TEXT NOT NULL,
            modelo_compatible TEXT DEFAULT '',
            version TEXT DEFAULT '',
            tamano_mb REAL DEFAULT 0.0,
            checksum_sha256 TEXT DEFAULT '',
            notas TEXT DEFAULT '',
            fecha_subida TEXT DEFAULT ''
        )''')

        # --- Tabla de Trabajos OTA (cada flash individual) ---
        c.execute('''CREATE TABLE IF NOT EXISTS firmware_ota_jobs (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            firmware_id INTEGER NOT NULL,
            agent_id TEXT DEFAULT 'default',
            ip TEXT NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            progreso INTEGER DEFAULT 0,
            mensaje TEXT DEFAULT '',
            fecha_creacion TEXT DEFAULT '',
            fecha_fin TEXT DEFAULT ''
        )''')

        # --- Tabla temporal de Mineros Descubiertos por el Escáner ---
        c.execute('''CREATE TABLE IF NOT EXISTS mineros_descubiertos (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            agent_id TEXT DEFAULT 'default',
            farm_id TEXT DEFAULT '',
            ip TEXT NOT NULL,
            mac TEXT DEFAULT '',
            serial TEXT DEFAULT '',
            marca TEXT DEFAULT '',
            modelo TEXT DEFAULT 'Antminer',
            algoritmo TEXT DEFAULT 'SHA-256',
            moneda TEXT DEFAULT 'BTC',
            unidad TEXT DEFAULT 'TH/s',
            hashrate REAL DEFAULT 0.0,
            temp_max INTEGER DEFAULT 0,
            consumo_w REAL DEFAULT 0.0,
            worker1 TEXT DEFAULT '',
            pool1 TEXT DEFAULT '',
            cred_defecto TEXT DEFAULT '',
            ultima_act TEXT DEFAULT ''
        )''')

        # --- Tabla de Agentes Conectados (múltiples celulares/PCs por granja) ---
        c.execute('''CREATE TABLE IF NOT EXISTS agentes_conectados (
            id SERIAL PRIMARY KEY,
            owner TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            farm_id TEXT DEFAULT '',
            device_type TEXT DEFAULT 'Celular',
            run_mode TEXT DEFAULT 'Agente',
            brand TEXT DEFAULT '',
            model TEXT DEFAULT '',
            local_ip TEXT DEFAULT '',
            subred TEXT DEFAULT '',
            app_version TEXT DEFAULT '',
            ultima_act TEXT DEFAULT ''
        )''')

        columnas_nuevas = [
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS agent_id TEXT DEFAULT 'default'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_id TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS miner_uid TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS algoritmo TEXT DEFAULT 'SHA-256'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS moneda TEXT DEFAULT 'BTC'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS unidad TEXT DEFAULT 'TH/s'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS hashrate_30m REAL DEFAULT 0.0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS temp_amb INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS pass1 TEXT DEFAULT '123'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS pass2 TEXT DEFAULT '123'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS pass3 TEXT DEFAULT '123'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS cadenas TEXT",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS fw_tipo TEXT DEFAULT 'Stock'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS perfil_oc TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rack_nombre TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rack_fila INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rack_col INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS uptime TEXT DEFAULT '0h 0m'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS status1 TEXT DEFAULT 'Alive'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS acc1 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rej1 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS status2 TEXT DEFAULT 'Alive'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS acc2 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rej2 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS status3 TEXT DEFAULT 'Dead'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS acc3 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS rej3 INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS hostname TEXT DEFAULT 'Antminer'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS net_proto TEXT DEFAULT 'Static'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS netmask TEXT DEFAULT '255.255.255.0'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS gateway TEXT DEFAULT '192.168.0.1'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS dns TEXT DEFAULT '8.8.8.8'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_lat REAL DEFAULT -17.7833",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_lon REAL DEFAULT -63.1821",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_city TEXT DEFAULT 'Santa Cruz de la Sierra'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS farm_country TEXT DEFAULT 'Bolivia'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS gps_manual INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS real_lat REAL DEFAULT 0.0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS real_lon REAL DEFAULT 0.0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS real_source TEXT DEFAULT 'IP Pública'",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS corte_activo INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS corte_temp INTEGER DEFAULT 85",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS watchdog_activo INTEGER DEFAULT 1",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_fallo_desde TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_motivo_fallo TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_ultimo_reinicio TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_reinicios_seguidos INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS wd_cuarentena INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS placas_normales INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS prot_sleep_desde TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS prot_motivo TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS alerta_hr_disparada INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS alerta_temp_disparada INTEGER DEFAULT 0",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS marca TEXT DEFAULT ''",
            "ALTER TABLE mineros_activos ADD COLUMN IF NOT EXISTS cred_defecto TEXT DEFAULT ''",
            "ALTER TABLE mineros_descubiertos ADD COLUMN IF NOT EXISTS marca TEXT DEFAULT ''",
            "ALTER TABLE mineros_descubiertos ADD COLUMN IF NOT EXISTS cred_defecto TEXT DEFAULT ''",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS router_mac TEXT DEFAULT ''",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS app_version TEXT DEFAULT ''",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS zt_activo INTEGER DEFAULT 0",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS zt_pool1 TEXT DEFAULT ''",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS zt_worker_prefix TEXT DEFAULT ''",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS zt_pass1 TEXT DEFAULT '123'",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS zt_pool2 TEXT DEFAULT ''",
            "ALTER TABLE granjas_sedes ADD COLUMN IF NOT EXISTS zt_pool3 TEXT DEFAULT ''",
            "ALTER TABLE cola_comandos ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE cola_comandos ADD COLUMN IF NOT EXISTS agent_id TEXT DEFAULT 'default'",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS errores INTEGER DEFAULT 0",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS temp_amb INTEGER DEFAULT 0",
            "ALTER TABLE historial_nube ADD COLUMN IF NOT EXISTS cadenas TEXT",
            "ALTER TABLE nombres_mineros ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE logs_mineros ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT 'Admin'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS email TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS fecha_registro TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS suspendido INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS saldo_bonus REAL DEFAULT 0.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS pro_hasta TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS limite_mineros INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS notas_admin TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultima_conexion TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS api_key TEXT",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS subnet TEXT DEFAULT '192.168.0'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tarifa_kwh REAL DEFAULT 0.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS comision_pool REAL DEFAULT 0.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS miner_user TEXT DEFAULT 'root'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS miner_pass TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS parent_owner TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol TEXT DEFAULT 'owner'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tg_token TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS tg_chat_id TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS umbral_temp INTEGER DEFAULT 80",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS umbral_hr REAL DEFAULT 50.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS alertas_activas INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_activo INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_alcance TEXT DEFAULT 'all'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_granjas TEXT DEFAULT '[]'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_mineros TEXT DEFAULT '[]'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_zero_hr INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cold_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cold_temp INTEGER DEFAULT 31",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_amb_cold_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_amb_cold_temp INTEGER DEFAULT 18",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_dead_board INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_gracia_min INTEGER DEFAULT 5",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cooldown_min INTEGER DEFAULT 30",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_cuarentena_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS wd_max_reinicios INTEGER DEFAULT 3",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_activo INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_temp_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_temp_val INTEGER DEFAULT 85",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_temp_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_clima_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_clima_val INTEGER DEFAULT 38",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_clima_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_net_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_net_rej_pct REAL DEFAULT 5.0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_net_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_fan_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_fan_accion TEXT DEFAULT 'sleep'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_wakeup_on INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS prot_wakeup_min INTEGER DEFAULT 30",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS curt_activo INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS curt_inicio TEXT DEFAULT '18:00'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS curt_fin TEXT DEFAULT '22:00'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS curt_accion TEXT DEFAULT 'profile|low'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS curt_estado_actual TEXT DEFAULT 'normal'",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS mineros_omitidos INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS totp_secret TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS totp_activo INTEGER DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS email_verificado INTEGER DEFAULT 1",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS email_token TEXT DEFAULT ''",
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS email_token_exp TEXT DEFAULT ''"
        ]
        for col_sql in columnas_nuevas:
            try: c.execute(col_sql)
            except: pass

        c.execute("UPDATE mineros_activos SET owner='Admin' WHERE owner IS NULL OR owner=''")
        c.execute("UPDATE mineros_activos SET agent_id='default' WHERE agent_id IS NULL OR agent_id=''")
        c.execute("UPDATE mineros_activos SET farm_id=agent_id WHERE farm_id IS NULL OR farm_id=''")
        c.execute("UPDATE cola_comandos SET agent_id='default' WHERE agent_id IS NULL OR agent_id=''")
        c.execute("UPDATE nombres_mineros SET owner='Admin' WHERE owner IS NULL OR owner=''")
        c.execute("UPDATE logs_mineros SET owner='Admin' WHERE owner IS NULL OR owner=''")
        c.execute("UPDATE historial_nube SET owner='Admin' WHERE owner IS NULL OR owner=''")

        for drop_sql in [
            "ALTER TABLE mineros_activos DROP CONSTRAINT IF EXISTS mineros_activos_ip_key",
            "DROP INDEX IF EXISTS idx_mineros_owner_ip"
        ]:
            try: c.execute(drop_sql)
            except: pass

        try:
            c.execute("SELECT id FROM usuarios WHERE LOWER(username)='giovanni'")
            row_old_adm = c.fetchone()
            c.execute("SELECT id FROM usuarios WHERE LOWER(username)='admin'")
            row_new_adm = c.fetchone()
            if row_old_adm and not row_new_adm:
                c.execute("UPDATE usuarios SET username='Admin', plan='Pro', suspendido=0 WHERE id=%s", (row_old_adm[0],))
        except:
            pass

        for tbl_u in ('mineros_activos', 'nombres_mineros', 'logs_mineros'):
            try:
                c.execute(f"DELETE FROM {tbl_u} WHERE LOWER(owner)='giovanni' AND ip IN (SELECT ip FROM {tbl_u} WHERE owner='Admin')")
            except:
                pass

        for tbl, col in [
            ('mineros_activos', 'owner'), ('granjas_sedes', 'owner'),
            ('nombres_mineros', 'owner'), ('logs_mineros', 'owner'),
            ('historial_nube', 'owner'), ('cola_comandos', 'owner'),
            ('registro_alertas', 'owner'), ('movimientos_saldo', 'username')
        ]:
            try:
                c.execute(f"UPDATE {tbl} SET {col}='Admin' WHERE LOWER({col})='giovanni'")
            except:
                pass

        hoy_str = datetime.utcnow().strftime("%Y-%m-%d")
        c.execute("UPDATE usuarios SET fecha_registro=%s WHERE fecha_registro IS NULL OR fecha_registro=''", (hoy_str,))

        try:
            c.execute("UPDATE historial_nube SET temp_max = 0 WHERE temp_max >= 150")
            c.execute("UPDATE mineros_activos SET temp_max = 0 WHERE temp_max >= 150")
            c.execute("UPDATE mineros_activos SET temp_amb = 0 WHERE temp_amb >= 120 OR temp_amb < -40")
        except:
            pass

        migraciones_unique = [
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mineros_owner_agent_ip ON mineros_activos (owner, agent_id, ip)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_granjas_owner_farm_id ON granjas_sedes (owner, farm_id)",
            "CREATE INDEX IF NOT EXISTS idx_granjas_owner_router_mac ON granjas_sedes (owner, router_mac)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_racks_owner_nombre ON racks_sedes (owner, nombre)",
            "CREATE INDEX IF NOT EXISTS idx_mineros_owner_uid ON mineros_activos (owner, miner_uid)",
            "CREATE INDEX IF NOT EXISTS idx_mineros_owner_farm ON mineros_activos (owner, farm_id)",
            "CREATE INDEX IF NOT EXISTS idx_hosting_owner ON enlaces_hosting (owner)",
            "CREATE INDEX IF NOT EXISTS idx_pagos_user ON pagos_usdt (username, id DESC)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_hosting_cuentas_owner_user ON hosting_cuentas (owner, LOWER(username))",
            "CREATE INDEX IF NOT EXISTS idx_hosting_cuentas_owner ON hosting_cuentas (owner)",
            "CREATE INDEX IF NOT EXISTS idx_firmware_ota_owner ON firmware_ota (owner, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_firmware_jobs_owner ON firmware_ota_jobs (owner, estado, id DESC)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_descubiertos_owner_agent_ip ON mineros_descubiertos (owner, agent_id, ip)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agentes_conectados_owner_ag ON agentes_conectados (owner, agent_id)",
            "CREATE INDEX IF NOT EXISTS idx_agentes_conectados_farm ON agentes_conectados (owner, farm_id)",
            "ALTER TABLE nombres_mineros DROP CONSTRAINT IF EXISTS nombres_mineros_ip_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_nombres_owner_ip ON nombres_mineros (owner, ip)",
            "ALTER TABLE logs_mineros DROP CONSTRAINT IF EXISTS logs_mineros_ip_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_owner_ip ON logs_mineros (owner, ip)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_username_lower ON usuarios (LOWER(username))",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_email_lower ON usuarios (LOWER(email)) WHERE email IS NOT NULL AND email != ''",
            "CREATE INDEX IF NOT EXISTS idx_usuarios_api_key ON usuarios (api_key)"
        ]
        for m_sql in migraciones_unique:
            try: c.execute(m_sql)
            except: pass

        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_owner_ip_id ON historial_nube (owner, ip, id DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_owner_ip_fecha ON historial_nube (owner, ip, fecha)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_historial_fecha ON historial_nube (fecha)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cola_estado_owner ON cola_comandos (estado, owner, agent_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_alertas_owner_ip_tipo ON registro_alertas (owner, ip, tipo, id DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_alertas_owner_id ON registro_alertas (owner, id DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_alertas_fecha ON registro_alertas (fecha)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mov_saldo_user ON movimientos_saldo (username, id DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_disp_conf_user ON dispositivos_confianza (username, token_hash)")

        c.execute("SELECT id FROM usuarios WHERE LOWER(username)='admin'")
        if not c.fetchone():
            c.execute("INSERT INTO usuarios (username, password, email, fecha_registro, plan, api_key) VALUES ('Admin', %s, '', %s, 'Pro', %s)",
                      (generate_password_hash('admin123'), hoy_str, generar_api_key()))
        else:
            c.execute("UPDATE usuarios SET username='Admin', plan='Pro', suspendido=0 WHERE LOWER(username)='admin'")

        c.execute("SELECT id, username FROM usuarios WHERE api_key IS NULL OR api_key = ''")
        for u_row in c.fetchall():
            c.execute("UPDATE usuarios SET api_key=%s WHERE id=%s", (generar_api_key(), u_row[0]))

        con.close()
        db_inicializada = True
    except Exception as e:
        print(f"Error BD Init: {e}")
        if con is not None and con.closed == 0:
            try: con.close()
            except: pass
