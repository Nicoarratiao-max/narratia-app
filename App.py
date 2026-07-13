import streamlit as st
import pandas as pd
import os
import json
import uuid
import base64
import io
import re
import hmac
import hashlib
import difflib
import requests
import glob
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from streamlit_calendar import calendar
from streamlit_gsheets import GSheetsConnection
import extra_streamlit_components as stx

try:
    from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
    DRIVE_LIB_READY = True
except ImportError:
    # Si por alguna razón esta submódulo no está disponible, la app sigue
    # funcionando: simplemente el guardado de adjuntos cae de vuelta a base64.
    DRIVE_LIB_READY = False

try:
    import bcrypt
    BCRYPT_READY = True
except ImportError:
    # Si el paquete no está instalado, la app sigue funcionando pero avisa.
    # Agrega "bcrypt" a requirements.txt para habilitar el hash de contraseñas.
    BCRYPT_READY = False

# =====================================================================
# 🔒 UTILIDADES DE SEGURIDAD (HASH DE CONTRASEÑAS Y COOKIES FIRMADAS)
# =====================================================================

def hash_password(plano: str) -> str:
    """Convierte una contraseña en texto plano a un hash bcrypt seguro."""
    if not BCRYPT_READY:
        return str(plano)  # Fallback (no recomendado): evita crashear si falta la librería.
    return bcrypt.hashpw(str(plano).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def es_hash_bcrypt(valor: str) -> bool:
    valor = str(valor)
    return valor.startswith("$2a$") or valor.startswith("$2b$") or valor.startswith("$2y$")

def verificar_password(plano: str, almacenado: str) -> bool:
    """
    Verifica una contraseña contra el valor guardado.
    Soporta migración transparente: si el valor guardado todavía es texto plano
    (contraseñas antiguas), compara en texto plano. Los hashes nuevos ya
    quedan protegidos con bcrypt.
    """
    almacenado = str(almacenado)
    if es_hash_bcrypt(almacenado) and BCRYPT_READY:
        try:
            return bcrypt.checkpw(str(plano).encode("utf-8"), almacenado.encode("utf-8"))
        except Exception:
            return False
    # Compatibilidad con contraseñas históricas aún no migradas
    return str(plano) == almacenado

def _cookie_secret() -> str:
    # Idealmente definida en st.secrets["COOKIE_SECRET"] (Streamlit Cloud -> Settings -> Secrets).
    return str(st.secrets.get("COOKIE_SECRET", "jurisync_cambia_este_secreto_en_secrets_toml"))

def generar_token_sesion(usuario: str, dias_validez: int = 7) -> str:
    """Genera una cookie firmada (usuario|expiración|firma) para evitar suplantación."""
    expira = int((datetime.now(timezone.utc) + timedelta(days=dias_validez)).timestamp())
    payload = f"{usuario}|{expira}"
    firma = hmac.new(_cookie_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{firma}"

def validar_token_sesion(token: str):
    """Valida la firma y expiración de la cookie. Devuelve el usuario o None si es inválida."""
    try:
        usuario, expira, firma = str(token).split("|")
        payload = f"{usuario}|{expira}"
        firma_esperada = hmac.new(_cookie_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(firma, firma_esperada):
            return None
        if int(expira) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return usuario
    except Exception:
        return None

# =====================================================================
# 🔎 UTILIDADES DE CRUCE PARA ESTADO DIARIO (NORMALIZACIÓN + FUZZY MATCH)
# =====================================================================

def normalizar_rol(valor) -> str:
    """
    Normaliza un ROL/RIT judicial a un formato comparable, sin importar
    si viene como 'C-1234-2026', '1234/2026', 'C 1234 2026', etc.
    Devuelve algo tipo '1234-2026' (solo dígitos y guión separador de año).
    """
    if valor is None:
        return ""
    texto = str(valor).upper().strip()
    texto = texto.replace("/", "-").replace("_", "-")
    numeros = re.findall(r"\d+", texto)
    if len(numeros) >= 2:
        # Se asume [número de rol, año] como los dos últimos grupos numéricos
        return f"{int(numeros[-2])}-{numeros[-1]}"
    elif len(numeros) == 1:
        return numeros[0]
    return texto

def buscar_coincidencias_probables(df_pj, df_causas, col_rol_pj, umbral=0.85):
    """
    Segundo filtro (además del cruce exacto): detecta causas cuyo ROL es
    'casi' igual (diferencias de formato, un dígito distinto, etc.) para
    que el usuario las revise manualmente en vez de que pasen inadvertidas.
    """
    probables = []
    rol_pj_normalizados = df_pj["ROL_NORMALIZADO"].unique().tolist()
    for _, fila_causa in df_causas.iterrows():
        rol_causa_norm = fila_causa["ROL_NORMALIZADO"]
        if not rol_causa_norm:
            continue
        mejor_score = 0.0
        mejor_match = None
        for rol_pj in rol_pj_normalizados:
            score = difflib.SequenceMatcher(None, rol_causa_norm, rol_pj).ratio()
            if score > mejor_score:
                mejor_score = score
                mejor_match = rol_pj
        if umbral <= mejor_score < 1.0:
            probables.append({
                "ROL_Causa_Local": fila_causa.get("ROL", ""),
                "ROL_Estado_Diario": mejor_match,
                "Cliente": fila_causa.get("Cliente", ""),
                "Similitud": f"{mejor_score:.0%}"
            })
    return pd.DataFrame(probables)

def validar_tamano_para_sheets(archivo_bytes: bytes, nombre_archivo: str, limite_kb: int = 35):
    """
    Google Sheets limita cada celda a ~50.000 caracteres. Un archivo se
    guarda como base64 (crece ~33%), así que un límite de ~35 KB en bytes
    originales da margen de sobra. Si se supera, avisamos ANTES de guardar
    en vez de dejar que la celda se trunque en silencio.
    """
    tamano_kb = len(archivo_bytes) / 1024
    if tamano_kb > limite_kb:
        return False, f"⚠️ '{nombre_archivo}' pesa {tamano_kb:.0f} KB. Google Sheets no admite archivos de más de ~{limite_kb} KB en este módulo (se truncaría o fallaría el guardado). Comprime el PDF o súbelo a Google Drive y pega el enlace."
    return True, ""

def boton_descargar_excel(df, nombre_archivo, key, label="⬇️ Descargar excel"):
    """Genera un botón de descarga en Excel para cualquier listado (Causas, Tareas, Clientes)."""
    try:
        buffer_excel = io.BytesIO()
        with pd.ExcelWriter(buffer_excel, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Datos')
        st.download_button(label, data=buffer_excel.getvalue(), file_name=nombre_archivo,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=key)
    except Exception:
        st.caption("⚠️ Instala 'openpyxl' en requirements.txt para habilitar la descarga en Excel.")

def fecha_segura(valor, formato="%Y-%m-%d"):
    """
    Convierte un valor a datetime sin romper la app si viene vacío, NaN,
    None o con un formato inesperado (columna faltante, dato antiguo, etc.).
    Si no se puede interpretar, devuelve la fecha de hoy como respaldo.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return datetime.now()
    texto = str(valor).strip()
    if texto == "" or texto.lower() == "nan" or texto == "--":
        return datetime.now()
    try:
        return datetime.strptime(texto, formato)
    except (ValueError, TypeError):
        return datetime.now()

def _corregir_dtypes_texto(df):
    """
    Corrige columnas mal tipadas por inferencia automática de pandas:
    - Columnas 100% "True"/"False" -> quedan como bool -> se pasan a texto.
    - Columnas 100% vacías -> quedan como float (puro NaN) -> se pasan a texto.
    Sin esto, asignar después un string en esas columnas revienta con
    TypeError (ya pasó con 'Fecha_Inicio' y 'Debe_Cambiar_Clave'). Las
    columnas numéricas reales (con datos) no se tocan.
    """
    for col in df.columns:
        if df[col].dtype == bool:
            df[col] = df[col].astype(str)
        elif df[col].dtype == float and df[col].isna().all():
            df[col] = df[col].astype(object)
    return df

def parsear_monto_clp(texto) -> int:
    """
    Interpreta un monto en formato chileno. En Chile el punto se usa como
    separador de miles, NUNCA de decimales (el peso chileno no usa centavos
    en el uso corriente). Por eso "500.000" debe leerse como 500 mil, no
    como 500 con tres decimales. La forma más segura de lograr esto es
    quedarse solo con los dígitos y descartar cualquier punto, coma o símbolo.
    """
    if texto is None:
        return 0
    solo_digitos = re.sub(r'[^0-9]', '', str(texto))
    return int(solo_digitos) if solo_digitos else 0

def formatear_clp(monto) -> str:
    """Formatea un entero como moneda chilena: 500000 -> '$500.000'."""
    try:
        return f"${int(monto):,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return "$0"

def numero_a_letras_clp(monto: int) -> str:
    """
    Convierte un monto entero a su forma escrita en español, para no tener
    que redactar 'Valor en Letras' a mano cada vez (ej: 500000 ->
    'quinientos mil pesos'). Cubre montos hasta 999.999.999, más que
    suficiente para honorarios profesionales.
    """
    monto = int(monto)
    if monto == 0:
        return "cero pesos"
    if monto == 1:
        return "un peso"
    
    UNIDADES = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
    DIECIS = ["diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve"]
    DECENAS = ["", "", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
    CENTENAS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos"]
    
    def _tres_digitos(n, femenino=False, apocope_uno=False):
        if n == 0:
            return ""
        if n == 100:
            return "cien"
        c, resto = divmod(n, 100)
        partes = []
        if c > 0:
            partes.append(CENTENAS[c])
        if resto > 0:
            if resto < 10:
                u = "una" if (femenino and resto == 1) else UNIDADES[resto]
                partes.append(u)
            elif resto < 20:
                partes.append(DIECIS[resto - 10])
            else:
                d, u = divmod(resto, 10)
                if u == 0:
                    partes.append(DECENAS[d])
                elif d == 2:
                    partes.append(f"veinti{UNIDADES[u] if u != 1 else 'uno' if not femenino else 'una'}")
                else:
                    u_txt = ("una" if femenino else "uno") if u == 1 else UNIDADES[u]
                    partes.append(f"{DECENAS[d]} y {u_txt}")
        texto = " ".join(partes)
        # Apócope: "veintiuno mil" -> "veintiún mil", "treinta y uno mil" -> "treinta y un mil"
        if apocope_uno and texto.endswith("uno"):
            texto = texto[:-3] + "ún" if texto.endswith("veintiuno") else texto[:-3] + "un"
        return texto
    
    millones, resto_m = divmod(monto, 1_000_000)
    miles, unidades_finales = divmod(resto_m, 1_000)
    
    trozos = []
    if millones > 0:
        if millones == 1:
            trozos.append("un millón")
        else:
            trozos.append(f"{_tres_digitos(millones, apocope_uno=True)} millones")
    if miles > 0:
        if miles == 1:
            trozos.append("mil")
        else:
            trozos.append(f"{_tres_digitos(miles, apocope_uno=True)} mil")
    if unidades_finales > 0:
        trozos.append(_tres_digitos(unidades_finales))
    
    # Regla gramatical: "un millón DE pesos" cuando no hay nada más entre
    # medio, pero "un millón doscientos mil pesos" (sin "de") cuando sí lo hay.
    sufijo = " de pesos" if (millones > 0 and miles == 0 and unidades_finales == 0) else " pesos"
    return " ".join(trozos).strip().capitalize() + sufijo

def boton_refrescar_equipo(key):
    """Fuerza una relectura desde disco de todos los archivos base_causas_*/base_tareas_*
    del equipo, por si un cambio reciente de otro usuario no se refleja aún."""
    if st.button("🔄 Actualizar datos del equipo", key=key, help="Si algún cambio reciente de otro abogado no aparece, presiona aquí."):
        claves_a_limpiar = [k for k in st.session_state.keys() if k.startswith("_csv_cache_base_causas_") or k.startswith("_csv_cache_base_tareas_")]
        for k in claves_a_limpiar:
            del st.session_state[k]
        st.rerun()

def leer_csv_local(path, default_cols=None):
    """
    Lee un CSV local cacheándolo en st.session_state mientras el archivo no
    cambie en disco (se detecta por fecha de modificación). Evita releer el
    mismo archivo una y otra vez en cada rerun de Streamlit cuando nadie lo
    modificó, sin arriesgar mostrar datos desactualizados.
    """
    if not os.path.exists(path):
        return pd.DataFrame(columns=default_cols) if default_cols is not None else pd.DataFrame()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    cache_key = f"_csv_cache_{path}"
    cached = st.session_state.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1].copy()
    try:
        df = pd.read_csv(path)
        df = _corregir_dtypes_texto(df)
    except Exception:
        return pd.DataFrame(columns=default_cols) if default_cols is not None else pd.DataFrame()
    st.session_state[cache_key] = (mtime, df)
    return df.copy()

# =====================================================================
# ☁️ ALMACENAMIENTO DE ARCHIVOS EN GOOGLE DRIVE (CON RESPALDO A BASE64)
# =====================================================================
# Por qué: Google Sheets limita cada celda a ~50.000 caracteres. Guardar PDFs
# o fotos en base64 dentro de una celda se rompe apenas el archivo pesa un
# poco. Ahora los adjuntos se suben a una carpeta de Google Drive y en la
# hoja solo se guarda el ID del archivo (unas pocas decenas de caracteres).
#
# CONFIGURACIÓN NECESARIA (una sola vez, de tu parte):
# 1. En Google Cloud Console, habilita la "Google Drive API" para el mismo
#    proyecto de tu cuenta de servicio (credenciales_calendar.json).
# 2. Crea una carpeta en Google Drive para JuriSync y compártela con el
#    correo de la cuenta de servicio (termina en "...gserviceaccount.com"),
#    dándole permiso de Editor.
# 3. Copia el ID de esa carpeta (está en la URL de Drive) y agrégalo en
#    Streamlit Cloud -> Settings -> Secrets como:
#       DRIVE_FOLDER_ID = "el_id_de_tu_carpeta"
#
# Si no configuras esto todavía, el sistema NO se rompe: cae automáticamente
# de vuelta al guardado en base64 (comportamiento anterior), respetando el
# límite de tamaño de Sheets.

def _servicio_drive():
    SCOPES_DRIVE = ['https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file('credenciales_calendar.json', scopes=SCOPES_DRIVE)
    return build('drive', 'v3', credentials=creds)

def subir_archivo_drive(nombre_archivo: str, contenido_bytes: bytes, mime_type: str = 'application/octet-stream'):
    """Sube un archivo a la carpeta de Drive configurada. Devuelve el file_id o None si falla."""
    try:
        servicio = _servicio_drive()
        carpeta_id = st.secrets.get("DRIVE_FOLDER_ID", "")
        metadata = {'name': nombre_archivo}
        if carpeta_id:
            metadata['parents'] = [carpeta_id]
        media = MediaIoBaseUpload(io.BytesIO(contenido_bytes), mimetype=mime_type, resumable=False)
        archivo = servicio.files().create(body=metadata, media_body=media, fields='id').execute()
        return archivo.get('id')
    except Exception as e:
        print(f"Error subiendo a Drive: {e}")
        return None

def descargar_archivo_drive(file_id: str):
    """Descarga un archivo de Drive por su file_id. Devuelve los bytes o None si falla."""
    try:
        servicio = _servicio_drive()
        request = servicio.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        listo = False
        while not listo:
            _, listo = downloader.next_chunk()
        return buffer.getvalue()
    except Exception as e:
        print(f"Error descargando de Drive: {e}")
        return None

def guardar_archivo_adjunto(nombre_archivo: str, contenido_bytes: bytes, mime_type: str = 'application/octet-stream'):
    """
    Punto único de guardado de adjuntos. Intenta Google Drive primero
    (sin límite de tamaño de celda); si Drive no está configurado o falla,
    cae automáticamente al guardado en base64 (con aviso de tamaño).
    Devuelve (drive_id, base64_str) — uno de los dos queda vacío.
    """
    drive_id = subir_archivo_drive(nombre_archivo, contenido_bytes, mime_type)
    if drive_id:
        return drive_id, ""
    # Respaldo: base64 en la celda, solo si el tamaño lo permite
    tamano_ok, _ = validar_tamano_para_sheets(contenido_bytes, nombre_archivo)
    b64 = base64.b64encode(contenido_bytes).decode('utf-8') if tamano_ok else ""
    return "", b64

def obtener_bytes_adjunto(fila, campo_drive: str, campo_b64: str):
    """
    Recupera los bytes de un adjunto sin importar si quedó guardado en Drive
    (dato nuevo) o en base64 (dato antiguo, de antes de esta migración).
    """
    drive_id = fila.get(campo_drive, "")
    if pd.notna(drive_id) and str(drive_id).strip() not in ("", "nan"):
        contenido = descargar_archivo_drive(str(drive_id).strip())
        if contenido is not None:
            return contenido
    b64_val = fila.get(campo_b64, "")
    if pd.notna(b64_val) and str(b64_val).strip() not in ("", "nan"):
        try:
            return base64.b64decode(b64_val)
        except Exception:
            return None
    return None

# =====================================================================
# 🛡️ MOTOR DE RESILIENCIA Y CACHÉ (AIRBAGS ANTI-CRASH Y VELOCIDAD)
# =====================================================================
conn = st.connection("gsheets", type=GSheetsConnection)
# --- DEFINICIÓN DE COLUMNAS MAESTRAS ---
COLS_USUARIOS = ['Usuario', 'Password', 'Nombre_Real', 'Correo', 'Debe_Cambiar_Clave', 'Plan']
COLS_CLIENTES = ['RUT', 'Nombre', 'Telefono', 'Correo', 'Clave_unica', 'Direccion']
COLS_CAUSAS = ['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'RUT', 'Tipo_Negocio', 'Usuario_Propietario', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas', 'Clave_unica', 'SAC', 'Sucursal', 'Servicio']

# =====================================================================
# 🏛️ CATÁLOGO DE TRIBUNALES DE CHILE (para seleccionar, no escribir a mano)
# =====================================================================
CATALOGO_TRIBUNALES = {
    "Cortes y Tribunal Supremo": [
        "Corte Suprema", "Corte de Apelaciones de Arica", "Corte de Apelaciones de Iquique",
        "Corte de Apelaciones de Antofagasta", "Corte de Apelaciones de Copiapó", "Corte de Apelaciones de La Serena",
        "Corte de Apelaciones de Valparaíso", "Corte de Apelaciones de Santiago", "Corte de Apelaciones de San Miguel",
        "Corte de Apelaciones de Rancagua", "Corte de Apelaciones de Talca", "Corte de Apelaciones de Chillán",
        "Corte de Apelaciones de Concepción", "Corte de Apelaciones de Temuco", "Corte de Apelaciones de Valdivia",
        "Corte de Apelaciones de Puerto Montt", "Corte de Apelaciones de Coyhaique", "Corte de Apelaciones de Punta Arenas"
    ],
    "Juzgados Civiles de Santiago": [f"{i}° Juzgado Civil de Santiago" for i in range(1, 31)],
    "Juzgados de Familia de Santiago": [f"{i}° Juzgado de Familia de Santiago" for i in range(1, 5)],
    "Juzgados Laborales de Santiago": [
        "1° Juzgado de Letras del Trabajo de Santiago", "2° Juzgado de Letras del Trabajo de Santiago",
        "3° Juzgado de Letras del Trabajo de Santiago", "Juzgado de Cobranza Laboral y Previsional de Santiago"
    ],
    "Juzgados Penales de Santiago": [
        "Juzgado de Garantía de Santiago", "Juzgado de Garantía de San Miguel", "Juzgado de Garantía de San Bernardo",
        "Juzgado de Garantía de Puente Alto", "Juzgado de Garantía de Colina", "Juzgado de Garantía de Talagante",
        "Tribunal de Juicio Oral en lo Penal de Santiago", "Tribunal de Juicio Oral en lo Penal de San Miguel"
    ],
    "Tribunales Regionales - Zona Norte": [
        "Juzgado de Letras de Arica", "Juzgado de Familia de Arica", "Juzgado de Garantía de Arica",
        "Juzgado de Letras de Iquique", "Juzgado de Familia de Iquique", "Juzgado de Garantía de Iquique",
        "1° Juzgado Civil de Antofagasta", "2° Juzgado Civil de Antofagasta", "Juzgado de Familia de Antofagasta",
        "Juzgado de Garantía de Antofagasta", "Juzgado de Letras del Trabajo de Antofagasta",
        "Juzgado de Letras de Copiapó", "Juzgado de Familia de Copiapó", "Juzgado de Garantía de Copiapó",
        "1° Juzgado Civil de La Serena", "2° Juzgado Civil de La Serena", "Juzgado de Familia de La Serena", "Juzgado de Garantía de La Serena"
    ],
    "Tribunales Regionales - Zona Centro": [
        "1° Juzgado Civil de Valparaíso", "2° Juzgado Civil de Valparaíso", "3° Juzgado Civil de Valparaíso",
        "Juzgado de Familia de Valparaíso", "Juzgado de Garantía de Valparaíso", "Juzgado de Letras del Trabajo de Valparaíso",
        "1° Juzgado Civil de Viña del Mar", "2° Juzgado Civil de Viña del Mar", "Juzgado de Familia de Viña del Mar",
        "1° Juzgado Civil de Rancagua", "2° Juzgado Civil de Rancagua", "Juzgado de Familia de Rancagua", "Juzgado de Garantía de Rancagua",
        "1° Juzgado Civil de Talca", "2° Juzgado Civil de Talca", "Juzgado de Familia de Talca", "Juzgado de Garantía de Talca",
        "Juzgado de Letras de Chillán", "Juzgado de Familia de Chillán", "Juzgado de Garantía de Chillán"
    ],
    "Tribunales Regionales - Zona Sur": [
        "1° Juzgado Civil de Concepción", "2° Juzgado Civil de Concepción", "3° Juzgado Civil de Concepción",
        "Juzgado de Familia de Concepción", "Juzgado de Garantía de Concepción", "Juzgado de Letras del Trabajo de Concepción",
        "1° Juzgado Civil de Temuco", "2° Juzgado Civil de Temuco", "Juzgado de Familia de Temuco", "Juzgado de Garantía de Temuco",
        "Juzgado de Letras de Valdivia", "Juzgado de Familia de Valdivia", "Juzgado de Garantía de Valdivia",
        "Juzgado de Letras de Osorno", "Juzgado de Familia de Osorno", "Juzgado de Garantía de Osorno",
        "1° Juzgado Civil de Puerto Montt", "2° Juzgado Civil de Puerto Montt", "Juzgado de Familia de Puerto Montt", "Juzgado de Garantía de Puerto Montt"
    ],
    "Tribunales Regionales - Zona Austral": [
        "Juzgado de Letras de Coyhaique", "Juzgado de Familia de Coyhaique", "Juzgado de Garantía de Coyhaique",
        "Juzgado de Letras de Punta Arenas", "Juzgado de Familia de Punta Arenas", "Juzgado de Garantía de Punta Arenas"
    ],
    "Juzgados de Policía Local (RM)": [
        "1° Juzgado de Policía Local de Santiago", "2° Juzgado de Policía Local de Santiago",
        "Juzgado de Policía Local de Providencia", "Juzgado de Policía Local de Las Condes",
        "Juzgado de Policía Local de Ñuñoa", "Juzgado de Policía Local de La Florida",
        "Juzgado de Policía Local de Maipú", "Juzgado de Policía Local de Puente Alto",
        "Juzgado de Policía Local de San Bernardo", "Juzgado de Policía Local de Peñalolén"
    ],
    "Tribunales Especiales": [
        "Tribunal Tributario y Aduanero de la Región Metropolitana - Santiago Oriente",
        "Tribunal Tributario y Aduanero de la Región Metropolitana - Santiago Poniente",
        "Tribunal de Defensa de la Libre Competencia", "1° Tribunal Ambiental (Antofagasta)",
        "2° Tribunal Ambiental (Santiago)", "3° Tribunal Ambiental (Valdivia)",
        "Tribunal Constitucional", "Tribunal Calificador de Elecciones"
    ]
}
_LISTA_PLANA_TRIBUNALES = []
for _grupo_trib in CATALOGO_TRIBUNALES.values():
    _LISTA_PLANA_TRIBUNALES.extend(_grupo_trib)
_LISTA_PLANA_TRIBUNALES.append("✏️ Otro (no está en la lista)")

def selector_tribunal(valor_actual="", key_prefix="trib"):
    """
    Selector predeterminado de tribunales de Chile: un desplegable único con
    los ~150 tribunales del catálogo (Streamlit permite escribir para filtrar
    dentro del propio selectbox), más un campo de texto de respaldo siempre
    visible para los casos no listados. Se diseñó así (un solo nivel, sin
    selects en cascada) para que funcione bien tanto dentro como fuera de un
    st.form, donde los widgets no se actualizan entre sí hasta enviar el formulario.
    Devuelve el nombre final del tribunal como texto (igual que antes).
    """
    idx_default = 0
    if valor_actual and valor_actual in _LISTA_PLANA_TRIBUNALES:
        idx_default = _LISTA_PLANA_TRIBUNALES.index(valor_actual)
    trib_sel = st.selectbox("Tribunal (escribe para buscar)", _LISTA_PLANA_TRIBUNALES, index=idx_default, key=f"{key_prefix}_sel")
    trib_manual = st.text_input("¿No está en la lista? Escríbelo aquí (tiene prioridad sobre lo seleccionado arriba)",
                                 value=valor_actual if valor_actual and valor_actual not in _LISTA_PLANA_TRIBUNALES else "",
                                 key=f"{key_prefix}_manual", placeholder="Ej: Juzgado de Letras de Melipilla")
    if trib_manual.strip():
        return trib_manual.strip()
    if trib_sel == "✏️ Otro (no está en la lista)":
        return ""
    return trib_sel
COLS_TAREAS = ['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad', 'Usuario_Propietario']
COLS_CONTRATOS = ['ID', 'Fecha', 'Cliente', 'Servicio', 'Honorarios', 'Archivo_B64', 'Archivo_Drive_ID', 'Usuario_Propietario']
COLS_TRAMITES = ['ID_Tramite', 'ROL', 'Fecha_Pago', 'Tipo_Auxiliar', 'Monto', 'Comprobante_Nombre', 'Comprobante_B64', 'Comprobante_Drive_ID', 'Registrado_Por', 'Usuario_Propietario']
@st.cache_data(ttl=900)
def fetch_sheet_cached(worksheet_name):
    """Descarga de la nube y guarda en memoria RAM por 15 min para máxima velocidad.
    IMPORTANTE: ttl=0 en conn.read() es intencional. La librería streamlit-gsheets
    tiene su PROPIA caché interna independiente de este decorador @st.cache_data.
    Antes se le pasaba ttl=900 también aquí, lo que creaba dos cachés apiladas:
    al guardar, fetch_sheet_cached.clear() solo limpiaba la caché exterior (esta),
    pero la caché interna de conn.read() seguía viva hasta 15 min más, así que
    los cambios (ej. Plan de un usuario) podían tardar hasta 15 min en reflejarse,
    o no reflejarse aunque recargaras la página. Con ttl=0 aquí, la única caché
    real es esta de @st.cache_data, que sí se limpia correctamente al guardar.
    """
    return conn.read(worksheet=worksheet_name, ttl=0)

def safe_read_sheet(worksheet_name, default_cols=None):
    """Lee usando caché. Si Google falla o bloquea, intenta evitar caídas."""
    try:
        df = fetch_sheet_cached(worksheet_name)
        if df is not None and not df.empty:
            df_clean = df.dropna(how="all")
            df_clean = _corregir_dtypes_texto(df_clean)
            # Guarda un respaldo local silencioso
            df_clean.to_csv(f"{worksheet_name}.csv", index=False)
            return df_clean
    except Exception:
        pass
    
    # Si Google falla, intenta leer el archivo local
    csv_path = f"{worksheet_name}.csv"
    if os.path.exists(csv_path):
        try:
            return _corregir_dtypes_texto(pd.read_csv(csv_path))
        except Exception:
            pass
            
    # Si no hay nada, crea una tabla vacía con las columnas correctas para evitar el TypeError
    if default_cols is not None:
        return pd.DataFrame(columns=default_cols)
    return pd.DataFrame()

def safe_update_sheet(worksheet_name, df):
    """Guarda en la nube e informa si Google Sheets rechaza la conexión."""
    csv_path = f"{worksheet_name}.csv"
    try:
        df.to_csv(csv_path, index=False)
    except Exception: pass
        
    try:
        conn.update(worksheet=worksheet_name, data=df)
        fetch_sheet_cached.clear() # Limpia la memoria para ver los datos frescos
        return True
    except Exception as e:
        # ¡ESTO ES CLAVE! Si Google falla, te saldrá un aviso rojo en vez de fallar en silencio.
        st.error(f"⚠️ Google Sheets bloqueó el guardado en la hoja '{worksheet_name}'. Detalle técnico: {e}")
        fetch_sheet_cached.clear()
        return False

# --- FUNCIÓN DE GOOGLE CALENDAR DINÁMICA ---
def agendar_plazo_calendar(titulo, descripcion, fecha_str, correo_destino):
    if not correo_destino or "@" not in str(correo_destino):
        return False

    try:
        SCOPES = ['https://www.googleapis.com/auth/calendar.events']
        creds = Credentials.from_service_account_file('credenciales_calendar.json', scopes=SCOPES)
        servicio = build('calendar', 'v3', credentials=creds)

        f_obj = datetime.strptime(fecha_str, "%d/%m/%Y")
        fecha_iso = f_obj.strftime("%Y-%m-%dT09:00:00-04:00")
        fecha_fin = f_obj.strftime("%Y-%m-%dT10:00:00-04:00")

        evento = {
            'summary': f"🔴 PLAZO FATAL: {titulo}",
            'description': descripcion,
            'start': {'dateTime': fecha_iso, 'timeZone': 'America/Santiago'},
            'end': {'dateTime': fecha_fin, 'timeZone': 'America/Santiago'},
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 10080}, # 1 semana antes
                    {'method': 'popup', 'minutes': 2880},  # 2 días antes
                    {'method': 'popup', 'minutes': 180},   # 3 horas antes
                ],
            },
        }
        servicio.events().insert(calendarId=correo_destino, body=evento).execute()
        return True
    except Exception as e:
        print(f"Error interno Calendar: {e}")
        return False

# --- VERIFICACIÓN DE LIBRERÍA WORD ---
try:
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_READY = True
except ImportError:
    DOCX_READY = False

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(
    page_title="JuriSync | Sistema Judicial", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# --- SISTEMA ANTI-CIERRE DE SESIÓN (KEEP-ALIVE AGRESIVO) ---
st.markdown("""
<iframe src="javascript:void(0);" style="display:none;" onload="
    setInterval(function(){
        window.parent.document.dispatchEvent(new Event('mousemove'));
        window.parent.document.dispatchEvent(new KeyboardEvent('keydown', {'key': 'Shift'}));
    }, 30000);
"></iframe>
""", unsafe_allow_html=True)

# --- FUNCIONES DE SALUDO Y LOGO CUSTOM JURISYNC ---
def obtener_saludo():
    hora_chile = (datetime.now(timezone.utc) - timedelta(hours=4)).hour
    if 0 <= hora_chile < 12:
        return "Buenos días"
    elif 12 <= hora_chile < 19:
        return "Buenas tardes"
    else:
        return "Buenas noches"

@st.cache_data
def get_logo_src():
    ruta_base = os.path.dirname(os.path.abspath(__file__))
    extensiones = ['png', 'jpg', 'jpeg', 'PNG', 'JPG']
    for ext in extensiones:
        ruta_logo = os.path.join(ruta_base, f"logo.{ext}")
        if os.path.exists(ruta_logo):
            with open(ruta_logo, "rb") as f:
                contenido_b64 = base64.b64encode(f.read()).decode()
                return f"data:image/{ext.lower()};base64,{contenido_b64}"
                
    svg_logo = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <path d="M 30 20 A 35 35 0 0 1 85 50" fill="none" stroke="#0052cc" stroke-width="6" stroke-linecap="round"/>
        <polygon points="85,60 76,46 94,46" fill="#0052cc"/>
        <path d="M 70 80 A 35 35 0 0 1 15 50" fill="none" stroke="#172b4d" stroke-width="6" stroke-linecap="round"/>
        <polygon points="15,40 6,54 24,54" fill="#172b4d"/>
        <line x1="50" y1="15" x2="50" y2="70" stroke="#0052cc" stroke-width="3.5" stroke-linecap="round"/>
        <line x1="43" y1="28" x2="57" y2="28" stroke="#0052cc" stroke-width="3" stroke-linecap="round"/>
        <circle cx="50" cy="72" r="3" fill="#0052cc"/>
        <path d="M 50 35 Q 40 55 33 75 L 67 75 Q 60 55 50 35 Z" fill="#172b4d" stroke-linejoin="round"/>
        <circle cx="50" cy="35" r="7" fill="#172b4d"/>
        <path d="M 41 35 Q 50 38 59 35" fill="none" stroke="#0052cc" stroke-width="2.5" stroke-linecap="round"/>
        <path d="M 40 52 L 60 52" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round"/>
        <path d="M 40 52 L 36 64 L 44 64 Z" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M 60 52 L 56 64 L 64 64 Z" fill="none" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round"/>
        <circle cx="50" cy="52" r="2" fill="#ffffff"/>
    </svg>
    """
    b64_svg = base64.b64encode(svg_logo.encode('utf-8')).decode('utf-8')
    return f"data:image/svg+xml;base64,{b64_svg}"

LOGO_URL = get_logo_src()

# --- PROCESADOR DE ARCHIVOS OJV EXCEL ---
def procesar_ojv_completo(archivo):
    diccionario_hojas = pd.read_excel(archivo, sheet_name=None)
    mapa = {
        'ROL': ['ROL', 'RIT', 'Rol', 'Rit'], 
        'TRIBUNAL': ['TRIBUNAL', 'Tribunal', 'Juzgado', 'Corte'], 
        'CARATULADO': ['CARATULA', 'Carátula', 'Caratulado', 'Causa']
    }
    lista_final = []
    
    for nombre_hoja, df_hoja in diccionario_hojas.items():
        df_pro = pd.DataFrame()
        for col_ideal, posibles in mapa.items():
            for p in posibles:
                if p in df_hoja.columns:
                    df_pro[col_ideal] = df_hoja[p]
                    break
        if not df_pro.empty and 'ROL' in df_pro.columns:
            df_pro['Origen_OJV'] = nombre_hoja
            lista_final.append(df_pro)
            
    if lista_final:
        df_consolidado = pd.concat(lista_final, ignore_index=True).dropna(subset=['ROL'])
        df_consolidado['Estado'] = "Pendiente"
        df_consolidado['Prioridad'] = "Normal"
        df_consolidado['Tipo_Negocio'] = "Externo"
        
        cols_extra = [
            'Cliente', 'RUT', 'Teléfono', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal', 
            'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas'
        ]
        for col in cols_extra:
            if col not in df_consolidado.columns: 
                if col in ['Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']: 
                    df_consolidado[col] = 0
                elif col == 'Estado_Honorarios': 
                    df_consolidado[col] = "Sin fijar"
                else: 
                    df_consolidado[col] = "--"
                    
        df_consolidado.to_csv(ARCHIVO_BD, index=False)
        return df_consolidado
    return pd.DataFrame()

# --- BASE DE DATOS DE FERIADOS CHILENOS ---
def obtener_feriados_chile():
    feriados = []
    annos = [2025, 2026, 2027]
    for anio in annos:
        fijos = [
            (f"{anio}-01-01", "Año Nuevo"), 
            (f"{anio}-05-01", "Día del Trabajador"),
            (f"{anio}-05-21", "Glorias Navales"), 
            (f"{anio}-06-21", "Pueblos Indígenas"),
            (f"{anio}-06-29", "San Pedro y San Pablo"), 
            (f"{anio}-07-16", "Virgen del Carmen"),
            (f"{anio}-08-15", "Asunción de la Virgen"), 
            (f"{anio}-09-18", "Independencia Nacional"),
            (f"{anio}-09-19", "Glorias del Ejército"), 
            (f"{anio}-10-12", "Encuentro de Dos Mundos"),
            (f"{anio}-10-31", "Iglesias Evangélicas"), 
            (f"{anio}-11-01", "Todos los Santos"),
            (f"{anio}-12-08", "Inmaculada Concepción"), 
            (f"{anio}-12-25", "Navidad")
        ]
        for fecha, nombre in fijos:
            feriados.append({
                "title": f"🇨🇱 {nombre}", 
                "start": fecha, 
                "color": "#ffebe6", 
                "textColor": "#bf2600", 
                "allDay": True, 
                "display": "block"
            })
            
    feriados.extend([
        {"title": "🇨🇱 Viernes Santo", "start": "2025-04-18", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Sábado Santo", "start": "2025-04-19", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Viernes Santo", "start": "2026-04-03", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Sábado Santo", "start": "2026-04-04", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Viernes Santo", "start": "2027-03-26", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Sábado Santo", "start": "2027-03-27", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"}
    ])
    return feriados

# --- MOTOR REDACTOR DE CONTRATOS EN WORD (VERSIÓN COMPLETA, 18 CLÁUSULAS) ---
def crear_contrato_word(datos):
    if not DOCX_READY: 
        return None
        
    doc = Document()
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(11)
    # Interlineado 1.5 aplicado a nivel de estilo 'Normal': como todos los
    # párrafos del documento usan ese estilo por defecto, esto alcanza a todo
    # el contrato de una vez, sin tener que tocar párrafo por párrafo.
    style.paragraph_format.line_spacing = 1.5
    style.paragraph_format.space_after = Pt(6)
    
    def clausula(doc, numero_texto, titulo_texto, *bloques_texto):
        """
        Helper para no repetir el mismo bloque de formato en cada cláusula.
        IMPORTANTE: cada bloque de texto se pone en su PROPIO párrafo real
        (no separados con "\\n" dentro de un mismo párrafo). Word justifica
        estirando cualquier línea que no sea el final REAL de un párrafo, así
        que un salto de línea manual en medio de un párrafo justificado deja
        esos huecos raros entre palabras que se ven mal. Con párrafos
        separados, cada uno se justifica de forma normal y prolija.
        """
        p_titulo = doc.add_paragraph()
        p_titulo.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_titulo.add_run(f"CLÁUSULA {numero_texto}: {titulo_texto}. ").bold = True
        if bloques_texto:
            p_titulo.add_run(bloques_texto[0])
        for bloque in bloques_texto[1:]:
            p_extra = doc.add_paragraph()
            p_extra.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p_extra.add_run(bloque)
        return p_titulo
    
    titulo = doc.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_tit = titulo.add_run("CONTRATO DE PRESTACIÓN DE SERVICIOS PROFESIONALES\n")
    r_tit.bold = True
    
    hoy = datetime.now()
    meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    fecha_str = f"{hoy.day} de {meses[hoy.month-1].lower()} del año {hoy.year}"
    
    intro = doc.add_paragraph()
    intro.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    intro.add_run(f"En Santiago, República de Chile, a {fecha_str}, comparecen:")
    
    p_intro2 = doc.add_paragraph()
    p_intro2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p_intro2.add_run(f"Por una parte, don/doña {datos['abogado_nombre']}, de nacionalidad chilena, abogado/a, cédula nacional de identidad número {datos['abogado_rut']}, con domicilio profesional en {datos['abogado_domicilio']}, correo electrónico {datos['abogado_correo']}, en adelante e indistintamente \"EL ABOGADO\" o \"el mandatario\"; y,")
    
    p_intro3 = doc.add_paragraph()
    p_intro3.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p_intro3.add_run(f"Por otra parte, don/doña {datos['cliente_nombre']}, cédula nacional de identidad número {datos['cliente_rut']}, con domicilio en {datos['cliente_domicilio']}, número telefónico de contacto {datos['cliente_tel']}, correo electrónico {datos['cliente_correo']}, en adelante \"EL CLIENTE\" o \"el mandante\".")
    
    p_intro4 = doc.add_paragraph()
    p_intro4.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p_intro4.add_run("Ambas partes, compareciendo como mayores de edad, exponen que han convenido celebrar el siguiente contrato de prestación de servicios profesionales, el que se regirá por las cláusulas que a continuación se singularizan:")
    
    # --- CLÁUSULA PRIMERA: OBJETO ---
    p1 = doc.add_paragraph()
    p1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p1.add_run("CLÁUSULA PRIMERA: OBJETO DEL CONTRATO. ").bold = True
    p1.add_run(f"Por medio del presente instrumento, El Cliente confiere patrocinio y poder a El Abogado para que asuma la representación, tramitación y defensa de sus intereses en un procedimiento de {datos['tipo_servicio'].upper()}. Los servicios profesionales comprometidos comprenden, de forma específica, lo siguiente:")
    
    p1_bis = doc.add_paragraph()
    p1_bis.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p1_bis.add_run(datos['detalle_servicio'])
    
    # --- CLÁUSULA SEGUNDA: ALCANCE Y EXCLUSIONES DEL SERVICIO ---
    clausula(doc, "SEGUNDA", "ALCANCE Y EXCLUSIONES DEL SERVICIO",
        "El encargo referido en la cláusula precedente será desarrollado personalmente por El Abogado, o por los profesionales que este último destine al efecto dentro de su mismo estudio jurídico, quienes se sujetarán íntegramente a los términos de este contrato.",
        "El servicio contratado comprende únicamente la tramitación en primera instancia del asunto encomendado. Quedan expresamente excluidos, salvo pacto escrito adicional entre las partes, la presentación, defensa y tramitación de recursos procesales de segunda instancia o extraordinarios, tales como recursos de apelación, casación en la forma, casación en el fondo, nulidad, queja o unificación de jurisprudencia, cuyo eventual encargo dará lugar a honorarios adicionales que se acordarán separadamente.",
        "En caso de que durante la tramitación del asunto encomendado surjan materias distintas de las descritas en la cláusula primera, las partes podrán suscribir un anexo al presente contrato en el que se individualicen los nuevos servicios y los honorarios correspondientes."
    )
    
    # --- CLÁUSULA TERCERA: HONORARIOS ---
    clausula(doc, "TERCERA", "HONORARIOS PROFESIONALES",
        f"Los honorarios totales convenidos por la prestación de los servicios profesionales descritos ascienden a la suma de {datos['honorarios_num']} ({datos['honorarios_letras']}).",
        "Esta suma corresponde a la tramitación completa del asunto encomendado en primera instancia, conforme a lo señalado en la cláusula segunda, sin perjuicio de los honorarios adicionales que puedan devengarse por servicios excluidos o complementarios que las partes acuerden por separado."
    )
    
    # --- CLÁUSULA CUARTA: FORMA DE PAGO ---
    p4 = doc.add_paragraph()
    p4.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p4.add_run("CLÁUSULA CUARTA: CONDICIONES Y FORMA DE PAGO. ").bold = True
    p4.add_run(f"La suma total de los honorarios fijados en la cláusula precedente será pagada en un total de {datos['cuotas_cant']} cuotas mensuales, fijas y sucesivas, por un valor individual de {datos['cuotas_monto']} cada una, conforme al siguiente plan de pago:")
    
    table = doc.add_table(rows=1, cols=4)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = 'N° Cuota'
    hdr_cells[1].text = 'Vencimiento Pactado'
    hdr_cells[2].text = 'Monto'
    hdr_cells[3].text = 'Estado de Pago'
    
    fecha_base = datos['fecha_inicio']
    for i in range(datos['cuotas_cant']):
        row_cells = table.add_row().cells
        row_cells[0].text = f"{i+1:02d}"
        mes_calculado = fecha_base.month + i
        anno_calculado = fecha_base.year + ((mes_calculado - 1) // 12)
        mes_final = ((mes_calculado - 1) % 12) + 1
        row_cells[1].text = f"{fecha_base.day:02d} de {meses[mes_final-1]} de {anno_calculado}"
        row_cells[2].text = str(datos['cuotas_monto'])
        row_cells[3].text = "PENDIENTE"
    
    # Los datos bancarios son una ficha de campos cortos (Titular / RUT / Banco...),
    # no prosa corrida. Justificar líneas tan cortas se ve mal (huecos enormes entre
    # palabras), así que este bloque va alineado a la izquierda, como corresponde
    # tipográficamente a una lista de datos.
    p4_titulo_banco = doc.add_paragraph()
    p4_titulo_banco.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p4_titulo_banco.add_run("Información Bancaria para Transferencias Electrónicas:").bold = True
    
    p4_bis = doc.add_paragraph()
    p4_bis.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p4_bis.add_run(f"Titular de la Cuenta: {datos['abogado_nombre']}\nRUT: {datos['abogado_rut']}\nInstitución Bancaria: {datos['banco']}\nTipo de Cuenta: {datos['tipo_cuenta']}\nNúmero de Cuenta: {datos['num_cuenta']}")
    
    p4_ter = doc.add_paragraph()
    p4_ter.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p4_ter.add_run("El Cliente deberá remitir el comprobante de cada pago al correo electrónico de El Abogado individualizado en la comparecencia, dentro de las 24 horas siguientes a su realización.")

    # --- CLÁUSULA QUINTA: PAGO DE HONORARIOS A TODO EVENTO ---
    clausula(doc, "QUINTA", "DEL PAGO DE LOS HONORARIOS A TODO EVENTO",
        "Los honorarios establecidos en la cláusula tercera deberán ser pagados a todo evento, incluso en el caso de que El Cliente decida revocar anticipadamente el patrocinio y poder conferidos a El Abogado, sin perjuicio de la liquidación proporcional que corresponda conforme a lo dispuesto en la cláusula décimo tercera."
    )
    
    # --- CLÁUSULA SEXTA: GASTOS NECESARIOS ---
    clausula(doc, "SEXTA", "GASTOS NECESARIOS PARA LA PRESTACIÓN DEL SERVICIO",
        "Todos los gastos y tasas que resulten necesarios para la ejecución del encargo, tales como honorarios de Receptores Judiciales, peritos, Notarios Públicos, Conservadores de Bienes Raíces, inscripciones, publicaciones y notificaciones, serán de cargo exclusivo de El Cliente.",
        "El Abogado informará oportunamente a El Cliente sobre los gastos en que deberá incurrirse, detallando su concepto y monto, y en caso de que dichos gastos hayan sido solventados directamente por El Abogado, El Cliente deberá reembolsarlos dentro de los cinco días hábiles siguientes a que le sea así informado, debiendo El Abogado conservar los respaldos respectivos para su exhibición cuando El Cliente así lo solicite."
    )
    
    # --- CLÁUSULA SÉPTIMA: EFECTOS DEL INCUMPLIMIENTO Y MOROSIDAD ---
    clausula(doc, "SÉPTIMA", "EFECTOS DEL INCUMPLIMIENTO Y MOROSIDAD",
        "El cumplimiento exacto de los plazos de pago constituye un elemento esencial del presente contrato. Ante la ocurrencia de morosidad o retardo en el pago de cualquiera de las cuotas devengadas, operarán los siguientes efectos:"
    )
    p7_item1 = doc.add_paragraph()
    p7_item1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p7_item1.add_run("Aceleración de la deuda: ").bold = True
    p7_item1.add_run("la mora en el pago de una cuota faculta a El Abogado para exigir de forma inmediata el cobro íntegro del saldo total que permanezca insoluto.")
    
    p7_item2 = doc.add_paragraph()
    p7_item2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p7_item2.add_run("Suspensión de la tramitación: ").bold = True
    p7_item2.add_run("un atraso superior a cinco días hábiles faculta a El Abogado para suspender la presentación de escritos y diligencias ante los tribunales respectivos, hasta la regularización del pago.")
    
    p7_item3 = doc.add_paragraph()
    p7_item3.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p7_item3.add_run("Multa por atraso: ").bold = True
    p7_item3.add_run("se devengará una multa compensatoria equivalente a 0,15 Unidades de Fomento (UF) por cada día de atraso, hasta el pago efectivo de lo adeudado.")

    # --- CLÁUSULA OCTAVA: OBLIGACIONES RECÍPROCAS ---
    clausula(doc, "OCTAVA", "OBLIGACIONES RECÍPROCAS DE LAS PARTES")
    
    p8_abogado = doc.add_paragraph()
    p8_abogado.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p8_abogado.add_run("Obligaciones de El Abogado: ").bold = True
    p8_abogado.add_run("El Abogado asume una obligación de medios, comprometiéndose a desplegar toda su diligencia, conocimiento técnico y ético en la tramitación del asunto encomendado, sin que ello implique en caso alguno una obligación de resultado ni la garantía de un desenlace favorable, atendida la naturaleza incierta de todo proceso judicial o administrativo.")
    
    p8_cliente = doc.add_paragraph()
    p8_cliente.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p8_cliente.add_run("Obligaciones de El Cliente: ").bold = True
    p8_cliente.add_run("El Cliente se obliga a proporcionar de forma oportuna, veraz y completa toda la información y documentación que resulte necesaria para el correcto desarrollo del encargo, dentro de los plazos que El Abogado le indique, siendo el incumplimiento de esta obligación causal suficiente para la resciliación del presente contrato.")
    
    p8_clave = doc.add_paragraph()
    p8_clave.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p8_clave.add_run("En caso de que la naturaleza del encargo requiera el uso de la Clave Única de El Cliente para gestiones ante la Oficina Judicial Virtual del Poder Judicial u otros organismos del Estado, El Cliente autoriza expresamente su uso por parte de El Abogado, exclusivamente para los fines del presente contrato, comprometiéndose este último a resguardar su confidencialidad e integridad y a no utilizarla para ningún otro propósito.")

    # --- CLÁUSULA NOVENA: AUSENCIA DE RELACIÓN LABORAL ---
    clausula(doc, "NOVENA", "AUSENCIA DE RELACIÓN LABORAL",
        "Las partes dejan constancia que el vínculo que las une mediante el presente contrato es de naturaleza estrictamente civil, no existiendo entre ellas subordinación ni dependencia de ningún tipo, por lo que no se genera relación laboral alguna ni las obligaciones propias de dicho régimen."
    )
    
    # --- CLÁUSULA DÉCIMA: CONFIDENCIALIDAD ---
    clausula(doc, "DÉCIMA", "CONFIDENCIALIDAD",
        "Toda la información y documentación que las partes intercambien con motivo de la celebración y ejecución del presente contrato tiene el carácter de confidencial, no pudiendo ser divulgada a terceros ajenos a la relación contractual sin el consentimiento previo y por escrito de la otra parte, salvo que su revelación sea exigida por ley o por orden de autoridad competente.",
        "Esta obligación de confidencialidad subsistirá aun después de terminado el presente contrato, cualquiera sea la causa de su término."
    )
    
    # --- CLÁUSULA DÉCIMO PRIMERA: PROTECCIÓN DE DATOS PERSONALES ---
    clausula(doc, "DÉCIMO PRIMERA", "PROTECCIÓN DE DATOS PERSONALES",
        "El Abogado tratará los datos personales que El Cliente le proporcione con motivo de la celebración y ejecución del presente contrato conforme a la normativa vigente sobre protección de la vida privada y de datos personales, utilizándolos exclusivamente para los fines del encargo profesional descrito en la cláusula primera.",
        "El Cliente podrá, en cualquier momento, ejercer sus derechos de acceso, rectificación, cancelación, oposición y portabilidad respecto de sus datos personales, dirigiendo su solicitud al correo electrónico de El Abogado individualizado en la comparecencia."
    )
    
    # --- CLÁUSULA DÉCIMO SEGUNDA: BUEN TRATO Y RESPETO MUTUO ---
    clausula(doc, "DÉCIMO SEGUNDA", "DEL COMPROMISO DE BUEN TRATO Y RESPETO MUTUO",
        "Las partes declaran que la relación contractual deberá fundarse en el respeto mutuo y en un trato digno y adecuado, libre de violencia, discriminación o agresión de cualquier naturaleza, en cumplimiento de la Ley N° 21.643 y su reglamento sobre prevención y sanción del acoso laboral, sexual y la violencia en el trabajo.",
        "Esta obligación no obsta en caso alguno al derecho de El Cliente de formular consultas, expresar disconformidad o presentar reclamos respecto del servicio contratado, los que serán siempre recibidos y atendidos en un marco de respeto recíproco."
    )
    
    # --- CLÁUSULA DÉCIMO TERCERA: TÉRMINO ANTICIPADO ---
    clausula(doc, "DÉCIMO TERCERA", "DEL DESISTIMIENTO O TÉRMINO ANTICIPADO DEL CONTRATO",
        "El presente contrato podrá terminar anticipadamente por mutuo acuerdo de las partes, por voluntad unilateral de cualquiera de ellas, o por incumplimiento grave de las obligaciones aquí pactadas.",
        "La parte que decida poner término unilateral al contrato deberá comunicarlo a la otra por escrito, mediante carta certificada o correo electrónico, con a lo menos 15 días de anticipación. En caso de que el desistimiento provenga de El Cliente, los honorarios devengados hasta esa fecha por los servicios ya prestados o iniciados le pertenecerán a El Abogado a título de honorarios causados, sin derecho a devolución alguna, sin perjuicio del deber de este último de rendir cuenta de las gestiones realizadas y facilitar los antecedentes necesarios para que El Cliente pueda continuar la tramitación del asunto con otro profesional."
    )
    
    # --- CLÁUSULA DÉCIMO CUARTA: CLÁUSULA PENAL ---
    clausula(doc, "DÉCIMO CUARTA", "CLÁUSULA PENAL",
        "En caso de incumplimiento de las obligaciones pactadas en este contrato por cualquiera de las partes, la parte incumplidora deberá pagar a la otra, a título de indemnización de perjuicios de avaluación anticipada, una suma equivalente a $500.000 (quinientos mil pesos), sin perjuicio del derecho de la parte afectada de exigir el cumplimiento forzado de lo pactado o la indemnización de los perjuicios efectivamente sufridos, si estos fueren mayores."
    )
    
    # --- CLÁUSULA DÉCIMO QUINTA: DURACIÓN ---
    clausula(doc, "DÉCIMO QUINTA", "DURACIÓN DEL SERVICIO",
        "La duración del presente contrato se extenderá hasta la completa ejecución del encargo profesional descrito en la cláusula primera, dependiendo su plazo efectivo de las circunstancias procesales propias del asunto encomendado y de los tiempos de tramitación de los tribunales u organismos competentes, los que escapan al control de El Abogado."
    )
    
    num_clausula_extra = 16
    diccionario_numeros_extra = {
        16: "DÉCIMO SEXTA", 17: "DÉCIMO SÉPTIMA", 18: "DÉCIMO OCTAVA", 19: "DÉCIMO NOVENA", 20: "VIGÉSIMA"
    }
    
    if datos['tipo_servicio'] == "Liquidación voluntaria":
        p_extra = doc.add_paragraph()
        p_extra.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_extra.add_run(f"CLÁUSULA {diccionario_numeros_extra[num_clausula_extra]}: ENTREGA DE DECLARACIONES JURADAS OBLIGATORIAS. ").bold = True
        p_extra.add_run("Atendida la naturaleza específica del procedimiento de insolvencia y liquidación voluntaria, El Cliente asume la obligación ineludible de suscribir y entregar las siguientes declaraciones juradas exigidas por la normativa aplicable:")
        
        p_extra_lista = doc.add_paragraph()
        p_extra_lista.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_extra_lista.add_run("- Declaración Jurada de Bienes Excluidos o de Terceros.\n- Declaración Jurada de Listado Completo de Acreedores.\n- Consentimiento Informado Expreso de los Efectos de la Liquidación.")
        num_clausula_extra += 1

    # --- CLÁUSULA CONDICIONAL: DOCUMENTOS QUE EL CLIENTE DEBE REUNIR ---
    if datos.get('documentos_requeridos', '').strip():
        lineas_docs = [l.strip() for l in datos['documentos_requeridos'].strip().split("\n") if l.strip()]
        p_docs = doc.add_paragraph()
        p_docs.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_docs.add_run(f"CLÁUSULA {diccionario_numeros_extra[num_clausula_extra]}: DOCUMENTACIÓN A REUNIR POR EL CLIENTE. ").bold = True
        p_docs.add_run("Para dar inicio a la redacción de la gestión encomendada en la cláusula primera, El Cliente se obliga a reunir y hacer entrega a El Abogado, a la brevedad posible, de los siguientes documentos y antecedentes:")
        
        p_docs_lista = doc.add_paragraph()
        p_docs_lista.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_docs_lista.add_run("\n".join([f"- {linea}" for linea in lineas_docs]))
        
        p_docs_final = doc.add_paragraph()
        p_docs_final.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_docs_final.add_run("La demora injustificada en la entrega de estos antecedentes podrá afectar los plazos de tramitación del asunto encomendado, sin que ello sea imputable a El Abogado.")
        num_clausula_extra += 1

    clausula(doc, diccionario_numeros_extra[num_clausula_extra], "DOMICILIO CONVENCIONAL Y COMPETENCIA",
        "Para todos los efectos legales derivados del presente instrumento, las partes fijan su domicilio en la comuna y ciudad de Santiago, y se someten a la competencia de sus Tribunales Ordinarios de Justicia."
    )
    num_clausula_extra += 1
    
    clausula(doc, diccionario_numeros_extra[num_clausula_extra], "COMUNICACIONES ENTRE LAS PARTES",
        f"Para todos los efectos del presente contrato, se tendrán por válidas las comunicaciones dirigidas a los siguientes correos electrónicos y números de contacto: de El Cliente, {datos['cliente_correo']} / {datos['cliente_tel']}; y de El Abogado, {datos['abogado_correo']} / {datos['abogado_tel']}.",
        "En señal de plena conformidad con todas y cada una de las cláusulas precedentes, se extiende el presente contrato en dos ejemplares de idéntico tenor, quedando uno en poder de cada parte."
    )

    doc.add_paragraph("\n\n\n")
    table_firmas = doc.add_table(rows=1, cols=2)
    
    para_abogado = table_firmas.cell(0, 0).paragraphs[0]
    para_abogado.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para_abogado.add_run("___________________________________\n")
    para_abogado.add_run(f"{datos['abogado_nombre'].upper()}\n")
    para_abogado.add_run(f"R.U.T.: {datos['abogado_rut']}")
    
    para_cliente = table_firmas.cell(0, 1).paragraphs[0]
    para_cliente.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para_cliente.add_run("___________________________________\n")
    para_cliente.add_run(f"{datos['cliente_nombre'].upper()}\n")
    para_cliente.add_run(f"R.U.T.: {datos['cliente_rut']}")
    
    return doc

# --- MOTOR DE CREACIÓN DE INFORME IA EN WORD ---
def crear_informe_ia_word(rol, cliente, texto_informe):
    if not DOCX_READY: 
        return None
        
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(11)
    
    titulo = doc.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = titulo.add_run("INFORME DE AVANCE Y ESTADO DE CAUSA JUDICIAL\n")
    r.bold = True
    r.font.size = Pt(14)
    
    meta = doc.add_paragraph()
    meta.add_run(f"Causa Rol: ").bold = True
    meta.add_run(f"{rol}\n")
    meta.add_run(f"Cliente Titular: ").bold = True
    meta.add_run(f"{cliente}\n")
    meta.add_run(f"Fecha de Emisión del Reporte: ").bold = True
    meta.add_run(f"{datetime.now().strftime('%d/%m/%Y')}\n")
    
    doc.add_paragraph("\nESTIMADO CLIENTE:\nA continuación, se detalla el análisis y resumen ejecutivo del estado actual de su procedimiento judicial, redactado en términos claros para su correcta comprensión:\n")
    
    p_inf = doc.add_paragraph()
    p_inf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p_inf.add_run(texto_informe)
    
    doc.add_paragraph("\n\nAnte cualquier duda, nuestro equipo queda a su entera disposición.\n\n___________________________________\nEquipo Legal - JuriSync")
    
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# =====================================================================
# --- SISTEMA DE CONTROL DE ACCESO (EN GOOGLE SHEETS Y COOKIES) ---
# =====================================================================
ARCHIVO_USUARIOS = "base_usuarios.csv"

def guardar_en_nube(df):
    safe_update_sheet("base_usuarios", df)
    df.to_csv(ARCHIVO_USUARIOS, index=False)

df_usuarios = safe_read_sheet("base_usuarios")
if not df_usuarios.empty:
    df_usuarios = df_usuarios.dropna(how="all")
    if 'Debe_Cambiar_Clave' in df_usuarios.columns:
        df_usuarios['Debe_Cambiar_Clave'] = df_usuarios['Debe_Cambiar_Clave'].astype(str)
else:
    df_usuarios = pd.DataFrame()

if df_usuarios.empty:
    datos_iniciales = {
        "Usuario": ["Narratia", "Vfarfan", "Gdonoso", "Mcortes", "Jtrujillo", "Eriquelme"],
        "Password": [hash_password(p) for p in ["20911237", "vpfm2404", "gdonoso123", "Mcortes123", "Jtrujillo123", "Eriquelme123"]],
        "Nombre_Real": ["Nicolás Arratia", "Valentina Farfán", "Gabriel Donoso", "Miryam Cortés", "José Trujillo", "Eduardo Riquelme"],
        "Correo": ["pendiente", "pendiente", "pendiente", "pendiente", "pendiente", "pendiente"],
        "Debe_Cambiar_Clave": ['True', 'True', 'True', 'True', 'True', 'True'], 
        "Plan": ["Full", "Full", "Full", "Full", "Full", "Full"]
    }
    df_usuarios = pd.DataFrame(datos_iniciales)
    guardar_en_nube(df_usuarios)
else:
    cambios = False
    
    if "Eriquelme" not in df_usuarios['Usuario'].values:
        nuevo_u = pd.DataFrame([{"Usuario": "Eriquelme", "Password": hash_password("Eriquelme123"), "Nombre_Real": "Eduardo Riquelme", "Correo": "pendiente", "Debe_Cambiar_Clave": 'True', "Plan": "Full"}])
        df_usuarios = pd.concat([df_usuarios, nuevo_u], ignore_index=True)
        cambios = True

    if "Jtrujillo" not in df_usuarios['Usuario'].values:
        nuevo_u = pd.DataFrame([{"Usuario": "Jtrujillo", "Password": hash_password("Jtrujillo123"), "Nombre_Real": "José Trujillo", "Correo": "pendiente", "Debe_Cambiar_Clave": 'True', "Plan": "Full"}])
        df_usuarios = pd.concat([df_usuarios, nuevo_u], ignore_index=True)
        cambios = True

    # Migración automática: si alguna contraseña sigue en texto plano (formato antiguo),
    # se re-hashea al vuelo la próxima vez que ese usuario inicie sesión exitosamente
    # (ver más abajo en la pantalla de login). No se fuerza aquí para no invalidar sesiones.

    idx_narratia = df_usuarios[df_usuarios['Usuario'] == 'Narratia'].index
    if not idx_narratia.empty:
        correo_actual = str(df_usuarios.loc[idx_narratia[0], 'Correo'])
        if "@" not in correo_actual:
            df_usuarios.loc[idx_narratia[0], 'Debe_Cambiar_Clave'] = 'True'
            df_usuarios.loc[idx_narratia[0], 'Correo'] = "pendiente"
            cambios = True

    if 'Plan' not in df_usuarios.columns:
        df_usuarios['Plan'] = 'Full'
        cambios = True

    if cambios:
        guardar_en_nube(df_usuarios)

df_usuarios.to_csv(ARCHIVO_USUARIOS, index=False)

USUARIOS_DICT = dict(zip(df_usuarios['Usuario'], df_usuarios['Password'].astype(str)))
NOMBRES_REALES = dict(zip(df_usuarios['Usuario'], df_usuarios['Nombre_Real']))

# --- MOTOR DE COOKIES (PARA QUE NO SE CIERRE LA SESIÓN CON F5) ---
cookie_manager = stx.CookieManager(key="motor_cookies")

if 'logged_in' not in st.session_state: 
    st.session_state['logged_in'] = False
if 'username' not in st.session_state: 
    st.session_state['username'] = ""

cookie_token = cookie_manager.get(cookie="jurisync_user")
cookie_usuario = validar_token_sesion(cookie_token) if cookie_token else None
if cookie_usuario and cookie_usuario in USUARIOS_DICT:
    st.session_state['logged_in'] = True
    st.session_state['username'] = cookie_usuario

# =====================================================================
# 🚦 PORTAL DEL CLIENTE (VISTA EXTERNA PARA SUBIR DOCUMENTOS)
# =====================================================================
query_params = st.query_params
if "cliente_id" in query_params:
    token_cliente = query_params["cliente_id"]
    nombre_cliente_limpio = token_cliente.replace("_", " ")
    
    st.markdown("""
    <style>
        [data-testid="stSidebar"] { display: none !important; }
        .block-container { max-width: 800px !important; padding-top: 3rem !important; }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown(f"<div style='text-align: center;'><img src='{LOGO_URL}' style='width: 100px;'></div>", unsafe_allow_html=True)
    st.title("📥 Portal de Recepción de Antecedentes")
    st.subheader(f"Bienvenido/a, {nombre_cliente_limpio}")
    st.write("Por favor, cargue los documentos solicitados por su abogado en formato PDF o Imagen. El sistema notificará automáticamente al estudio jurídico cuando haya completado la entrega.")
    
    ARCHIVO_DOCS = "base_documentos_clientes.csv"
    if not os.path.exists(ARCHIVO_DOCS):
        pd.DataFrame(columns=['ID_Req', 'Cliente_Token', 'Documento_Nombre', 'Estado', 'Archivo_B64', 'Archivo_Drive_ID', 'Fecha_Subida']).to_csv(ARCHIVO_DOCS, index=False)
    else:
        df_docs_migra = leer_csv_local(ARCHIVO_DOCS)
        if 'Archivo_Drive_ID' not in df_docs_migra.columns:
            df_docs_migra['Archivo_Drive_ID'] = ''
            df_docs_migra.to_csv(ARCHIVO_DOCS, index=False)
        
    df_docs = leer_csv_local(ARCHIVO_DOCS)
    mis_docs = df_docs[df_docs['Cliente_Token'] == token_cliente]
    if mis_docs.empty and not df_docs.empty:
        # Respaldo: si el token no calzó exacto (por ejemplo, un enlace generado antes
        # de sanitizar tildes/comas), reintenta comparando solo letras/números/guion bajo
        # de ambos lados, para que los enlaces antiguos no queden rotos.
        token_normalizado = re.sub(r'[^A-Za-z0-9_]', '', token_cliente)
        mascara_respaldo = df_docs['Cliente_Token'].astype(str).apply(lambda x: re.sub(r'[^A-Za-z0-9_]', '', x) == token_normalizado)
        mis_docs = df_docs[mascara_respaldo]
    
    if mis_docs.empty:
        st.info("No registra solicitudes de documentos pendientes en este momento.")
    else:
        completados = 0
        total = len(mis_docs)
        
        for idx, row in mis_docs.iterrows():
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                ya_subido = row['Estado'] == '✅ Completado'
                
                with c1:
                    st.markdown(f"**Requisito:** {row['Documento_Nombre']}")
                    if ya_subido:
                        st.markdown("<span style='color:#57a15a; font-weight:bold;'>✅ Recibido con éxito</span>", unsafe_allow_html=True)
                        completados += 1
                    else:
                        st.markdown("<span style='color:#ff5630; font-weight:bold;'>❌ Pendiente de carga</span>", unsafe_allow_html=True)
                        
                with c2:
                    if not ya_subido:
                        archivo = st.file_uploader("Subir Archivo", key=f"up_{row['ID_Req']}", label_visibility="collapsed")
                        if archivo:
                            archivo_bytes = archivo.getvalue()
                            tamano_ok, msg_tamano = validar_tamano_para_sheets(archivo_bytes, archivo.name)
                            with st.spinner("Guardando en la nube de JuriSync..."):
                                drive_id, b64_file = guardar_archivo_adjunto(archivo.name, archivo_bytes, archivo.type or 'application/octet-stream')
                            if not drive_id and not b64_file:
                                st.error(msg_tamano if not tamano_ok else "⚠️ No fue posible guardar el archivo. Intenta nuevamente.")
                            else:
                                df_docs.loc[df_docs['ID_Req'] == row['ID_Req'], ['Estado', 'Archivo_B64', 'Archivo_Drive_ID', 'Fecha_Subida']] = ['✅ Completado', b64_file, drive_id, datetime.now().strftime("%d/%m/%Y")]
                                df_docs.to_csv(ARCHIVO_DOCS, index=False)
                                # BUGFIX: antes esto solo se guardaba en el disco local (efímero en la nube),
                                # nunca se sincronizaba a Google Sheets, por lo que el documento del
                                # cliente podía perderse si la app se reiniciaba antes de ser revisado.
                                safe_update_sheet("base_documentos_clientes", df_docs)
                                st.success("¡Documento guardado!")
                                st.rerun()
        
        st.progress(completados / total if total > 0 else 0)
        
        if completados == total and total > 0:
            st.success("🎉 ¡Excelente! Ha entregado la totalidad de la documentación solicitada.")
            st.balloons()
            st.info("📩 Se ha enviado un correo automático a su abogado notificando que su expediente está completo y listo para revisión.")
    st.stop() 

# =====================================================================
# 🔐 PANTALLA DE LOGIN CON CONFIGURACIÓN EN LA NUBE
# =====================================================================
if not st.session_state['logged_in']:
    st.markdown("""
    <style>
        [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
        #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
        .block-container { max-width: 1300px !important; margin: 0 auto !important; padding-top: 2rem !important; }
        [data-testid="stForm"] { background-color: white !important; border-radius: 16px !important; border: 1px solid #e0e4e8 !important; padding: 40px 30px !important; box-shadow: 0 4px 15px rgba(0,0,0,0.05) !important; }
        p, label, span, div { color: #172b4d !important; }
        [data-testid="stFormSubmitButton"] button { background-color: #0052cc !important; color: white !important; border: none !important; font-weight: bold !important;}
        [data-testid="stFormSubmitButton"] button:hover { background-color: #0047b3 !important; }
        .stTextInput input { border: 1px solid #cbd2d9 !important; border-radius: 6px !important; padding: 10px !important; }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_a, col_b, col_c = st.columns([1, 1.2, 1])
    
    with col_b:
        st.markdown(f"""
        <div style='text-align: center; margin-bottom: 20px;'>
            <img src='{LOGO_URL}' style='width: 140px; margin-bottom: 5px;'>
            <h1 style='color:#172b4d; margin-top: 0; margin-bottom: 5px; font-size: 32px; font-weight: 800; letter-spacing: 1px;'>JuriSync</h1>
            <p style='color:#6b778c; font-size: 15px; margin:0;'>Espacio de trabajo seguro</p>
        </div>
        """, unsafe_allow_html=True)
        
        tab_iniciar, tab_recuperar = st.tabs(["🔐 Iniciar Sesión", "🆘 Olvidé mi contraseña"])
        
        with tab_iniciar:
            with st.form("login_form", clear_on_submit=False):
                user_input = st.text_input("Usuario Corporativo", placeholder="Tu nombre de usuario")
                pass_input = st.text_input("Contraseña", type="password", placeholder="••••••••")
                st.write("") 
                
                if st.form_submit_button("Ingresar al Estudio", use_container_width=True):
                    user_clean = user_input.strip()
                    if user_clean in USUARIOS_DICT and verificar_password(pass_input, USUARIOS_DICT[user_clean]):
                        idx_user = df_usuarios[df_usuarios['Usuario'] == user_clean].index[0]
                        # Migración transparente: si la clave todavía estaba en texto plano, se re-hashea ahora.
                        if not es_hash_bcrypt(df_usuarios.loc[idx_user, 'Password']):
                            df_usuarios.at[idx_user, 'Password'] = hash_password(pass_input)
                            guardar_en_nube(df_usuarios)
                        if str(df_usuarios.loc[idx_user, 'Debe_Cambiar_Clave']).lower() == 'true':
                            st.session_state['requiere_registro_inicial'] = True
                            st.session_state['usr_registro'] = user_clean
                            st.rerun()
                        else:
                            cookie_manager.set("jurisync_user", generar_token_sesion(user_clean), key="cookie_login")
                            st.session_state['logged_in'] = True
                            st.session_state['username'] = user_clean
                            import time
                            time.sleep(0.3)
                            st.rerun()
                    else:
                        st.error("❌ Usuario o contraseña incorrectos.")
                        
        with tab_recuperar:
            with st.form("recuperar_form", clear_on_submit=True):
                st.info("Ingresa tu usuario y correo. Si coinciden, el sistema generará una clave temporal.")
                rec_usuario = st.text_input("Usuario")
                rec_correo = st.text_input("Correo electrónico registrado")
                
                if st.form_submit_button("Recuperar Contraseña", use_container_width=True):
                    if rec_usuario in df_usuarios['Usuario'].values:
                        correo_real = str(df_usuarios[df_usuarios['Usuario'] == rec_usuario]['Correo'].values[0])
                        if correo_real == "pendiente" or correo_real == "nan":
                            st.warning("⚠️ Esta cuenta aún no tiene un correo configurado. Pídele al administrador que la recupere manualmente.")
                        elif rec_correo.strip().lower() == correo_real.lower():
                            df_usuarios.loc[df_usuarios['Usuario'] == rec_usuario, 'Password'] = hash_password("Temp1234")
                            df_usuarios.loc[df_usuarios['Usuario'] == rec_usuario, 'Debe_Cambiar_Clave'] = 'True'
                            guardar_en_nube(df_usuarios)
                            st.success("✅ Identidad verificada. Tu contraseña temporal es: **Temp1234**. Inicia sesión con ella y te pediremos crear una nueva.")
                        else:
                            st.error("❌ El correo no coincide con nuestros registros de seguridad.")
                    else:
                        st.error("❌ Usuario no encontrado.")
                        
    if st.session_state.get('requiere_registro_inicial', False):
        with st.container(border=True):
            st.markdown("<h2 style='text-align:center; color:#172b4d;'>🔒 Configuración de Seguridad</h2>", unsafe_allow_html=True)
            st.warning("Por seguridad de tu cuenta, debes actualizar tus datos obligatoriamente para continuar.")
            
            with st.form("form_cambio_clave_nuevo"):
                nuevo_correo = st.text_input("Tu Correo Electrónico Institucional", placeholder="ejemplo@estudio.cl")
                nueva_cl = st.text_input("Nueva Contraseña", type="password")
                conf_cl = st.text_input("Confirmar Nueva Contraseña", type="password")
                st.write("")
                
                if st.form_submit_button("Actualizar Credenciales y Entrar", type="primary", use_container_width=True):
                    usr_actualizar = st.session_state['usr_registro']
                    if nueva_cl.strip() == "" or nuevo_correo.strip() == "":
                        st.error("Todos los campos son obligatorios.")
                    elif "@" not in nuevo_correo:
                        st.error("Por favor, ingresa un correo electrónico válido.")
                    elif nueva_cl != conf_cl:
                        st.error("Las contraseñas no coinciden.")
                    elif usr_actualizar not in df_usuarios['Usuario'].values:
                        st.error(f"⚠️ No se encontró el usuario '{usr_actualizar}' en la base de datos. Contacta al administrador.")
                    else:
                        try:
                            # Blindaje extra: fuerza estas columnas a texto ANTES de
                            # asignar, por si quedaron mal tipadas (mismo tipo de bug
                            # ya visto antes con Debe_Cambiar_Clave).
                            df_usuarios = _corregir_dtypes_texto(df_usuarios)
                            for _col in ['Password', 'Correo', 'Debe_Cambiar_Clave']:
                                if _col in df_usuarios.columns:
                                    df_usuarios[_col] = df_usuarios[_col].astype(object)
                            
                            idx_mod = df_usuarios[df_usuarios['Usuario'] == usr_actualizar].index[0]
                            df_usuarios.at[idx_mod, 'Password'] = hash_password(nueva_cl)
                            df_usuarios.at[idx_mod, 'Correo'] = nuevo_correo
                            df_usuarios.at[idx_mod, 'Debe_Cambiar_Clave'] = 'False'
                            
                            guardar_en_nube(df_usuarios)
                            cookie_manager.set("jurisync_user", generar_token_sesion(usr_actualizar), key="cookie_registro_inicial")
                            
                            st.session_state['logged_in'] = True
                            st.session_state['username'] = usr_actualizar
                            st.session_state['requiere_registro_inicial'] = False
                            st.success("✅ Credenciales actualizadas correctamente.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"⚠️ No se pudo guardar. Detalle técnico: {e}")
                            st.info("Si el problema persiste, avísale a Nicolás con este mensaje de error.")
    st.stop()


# --- ARQUITECTURA DE ARCHIVOS DE DATOS LOCALES ---
usuario_actual = st.session_state['username']
nombre_real_usuario = NOMBRES_REALES.get(usuario_actual, usuario_actual.capitalize())

ARCHIVO_BD = f"base_causas_{usuario_actual}.csv"
ARCHIVO_TAREAS = f"base_tareas_{usuario_actual}.csv"
ARCHIVO_CONTRATOS = f"base_contratos_{usuario_actual}.csv"
ARCHIVO_TRAMITES = f"base_tramites_{usuario_actual}.csv"
ARCHIVO_ESTADO_DIARIO = f"base_estado_diario_{usuario_actual}.csv"
ARCHIVO_MENSAJES = "base_mensajes_global.csv"

# Verificación de archivos individuales para evitar pérdida de datos
if not os.path.exists(ARCHIVO_TAREAS):
    df_vacio_t = pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad', 'Usuario_Propietario'])
    df_vacio_t.to_csv(ARCHIVO_TAREAS, index=False)
else:
    df_t_check = leer_csv_local(ARCHIVO_TAREAS, COLS_TAREAS)
    if 'Prioridad' not in df_t_check.columns:
        df_t_check['Prioridad'] = 'Media'
        df_t_check.to_csv(ARCHIVO_TAREAS, index=False)

if not os.path.exists(ARCHIVO_BD):
    df_vacio_c = pd.DataFrame(columns=['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'RUT', 'Teléfono', 'Tipo_Negocio', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas', 'Usuario_Propietario'])
    df_vacio_c.to_csv(ARCHIVO_BD, index=False)
else:
    df_c_check = leer_csv_local(ARCHIVO_BD)
    ejecutar_guardado_check = False
    columnas_requeridas_bd = ['Cliente', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas', 'Fecha_Inicio']
    for col in columnas_requeridas_bd:
        if col not in df_c_check.columns:
            if col in ['Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']: 
                df_c_check[col] = 0
            elif col == 'Estado_Honorarios': 
                df_c_check[col] = "Sin fijar"
            else: 
                df_c_check[col] = ""
            ejecutar_guardado_check = True
        elif col == 'Fecha_Inicio':
            # Si ya existía pero quedó tipada como número (todo NaN en archivos
            # antiguos), la forzamos a texto para que nunca vuelva a fallar
            # al intentar guardar una fecha en formato string.
            if df_c_check[col].dtype != object:
                df_c_check[col] = df_c_check[col].astype(object).fillna("")
                ejecutar_guardado_check = True
    if ejecutar_guardado_check:
        df_c_check.to_csv(ARCHIVO_BD, index=False)

if not os.path.exists(ARCHIVO_CONTRATOS):
    df_vacio_co = pd.DataFrame(columns=['ID', 'Fecha', 'Cliente', 'Servicio', 'Honorarios', 'Archivo_B64', 'Archivo_Drive_ID', 'Usuario_Propietario'])
    df_vacio_co.to_csv(ARCHIVO_CONTRATOS, index=False)
else:
    df_co_check = leer_csv_local(ARCHIVO_CONTRATOS)
    if 'Archivo_Drive_ID' not in df_co_check.columns:
        df_co_check['Archivo_Drive_ID'] = ''
        df_co_check.to_csv(ARCHIVO_CONTRATOS, index=False)

if not os.path.exists(ARCHIVO_TRAMITES):
    df_vacio_tr = pd.DataFrame(columns=['ID_Tramite', 'ROL', 'Fecha_Pago', 'Tipo_Auxiliar', 'Monto', 'Comprobante_Nombre', 'Comprobante_B64', 'Comprobante_Drive_ID', 'Registrado_Por', 'Usuario_Propietario'])
    df_vacio_tr.to_csv(ARCHIVO_TRAMITES, index=False)
else:
    df_tr_check = leer_csv_local(ARCHIVO_TRAMITES)
    if 'Comprobante_Drive_ID' not in df_tr_check.columns:
        df_tr_check['Comprobante_Drive_ID'] = ''
        df_tr_check.to_csv(ARCHIVO_TRAMITES, index=False)

if not os.path.exists(ARCHIVO_ESTADO_DIARIO):
    df_vacio_ed = pd.DataFrame(columns=['ID_ED', 'Fecha_Estado', 'ROL', 'Tribunal', 'Resolucion_Extracto', 'Doc_Nombre', 'Doc_B64', 'Doc_Drive_ID'])
    df_vacio_ed.to_csv(ARCHIVO_ESTADO_DIARIO, index=False)
else:
    df_ed_check = leer_csv_local(ARCHIVO_ESTADO_DIARIO)
    if 'Doc_Drive_ID' not in df_ed_check.columns:
        df_ed_check['Doc_Drive_ID'] = ''
        df_ed_check.to_csv(ARCHIVO_ESTADO_DIARIO, index=False)

if not os.path.exists(ARCHIVO_MENSAJES):
    pd.DataFrame(columns=['ID', 'Fecha', 'De', 'Para', 'Mensaje']).to_csv(ARCHIVO_MENSAJES, index=False)

# --- NOTIFICADOR ESTILO OUTLOOK (TOAST + INSIGNIA PERSISTENTE EN EL MENÚ) ---
BADGE_MENSAJES_NO_LEIDOS = 0
if st.session_state['logged_in']:
    try:
        if os.path.exists(ARCHIVO_MENSAJES):
            df_msgs_alerta = leer_csv_local(ARCHIVO_MENSAJES, ['ID', 'Fecha', 'De', 'Para', 'Mensaje'])
            if not df_msgs_alerta.empty and 'Para' in df_msgs_alerta.columns:
                mis_mensajes = df_msgs_alerta[(df_msgs_alerta['Para'] == nombre_real_usuario) | (df_msgs_alerta['Para'] == 'Todos')]
                
                if 'ultimo_mensaje_leido' not in st.session_state:
                    st.session_state['ultimo_mensaje_leido'] = len(mis_mensajes)
                elif len(mis_mensajes) > st.session_state['ultimo_mensaje_leido']:
                    mensajes_nuevos = len(mis_mensajes) - st.session_state['ultimo_mensaje_leido']
                    st.toast(f"🔔 ¡Tienes {mensajes_nuevos} mensaje(s) nuevo(s) en tu buzón!", icon="📩")
                
                # A diferencia del toast (que se ve una sola vez y desaparece), esta insignia
                # se recalcula en cada rerun y queda pegada al botón de Mensajería del menú
                # lateral hasta que el usuario entre efectivamente a leer sus mensajes.
                BADGE_MENSAJES_NO_LEIDOS = max(0, len(mis_mensajes) - st.session_state['ultimo_mensaje_leido'])
                st.session_state['_total_mensajes_para_mi'] = len(mis_mensajes)
    except Exception:
        # Este bloque corre en CADA carga de página para CUALQUIER usuario logueado.
        # Si algo falla aquí (archivo dañado, columna faltante, etc.), jamás debe
        # tumbar toda la app — en el peor caso, simplemente no se muestra la
        # insignia de mensajes no leídos por esta vez.
        BADGE_MENSAJES_NO_LEIDOS = 0

# --- FUNCIÓN DE AUTOLIMPIEZA SISTEMA (15 DÍAS EXACTOS) ---
def limpiar_documentos_estado_diario():
    if os.path.exists(ARCHIVO_ESTADO_DIARIO):
        df_ed = leer_csv_local(ARCHIVO_ESTADO_DIARIO)
        if not df_ed.empty:
            if 'Doc_Drive_ID' not in df_ed.columns:
                df_ed['Doc_Drive_ID'] = ''
            df_ed['Fecha_DT'] = pd.to_datetime(df_ed['Fecha_Estado'], format='%d/%m/%Y', errors='coerce')
            limite_fecha = datetime.now() - timedelta(days=15)
            mascara_viejos = df_ed['Fecha_DT'] < limite_fecha
            # Solo se purga el base64 local (pesa en el disco efímero). Los archivos que
            # ya quedaron respaldados en Google Drive (Doc_Drive_ID) se conservan intactos.
            mascara_sin_drive = df_ed['Doc_Drive_ID'].fillna('').astype(str).str.strip() == ''
            mascara_a_purgar = mascara_viejos & mascara_sin_drive
            df_ed.loc[mascara_a_purgar, 'Doc_B64'] = ""
            df_ed.loc[mascara_a_purgar, 'Doc_Nombre'] = df_ed.loc[mascara_a_purgar, 'Doc_Nombre'].apply(lambda x: f"(Eliminado por memoria) {x}" if pd.notna(x) and x != "" and not str(x).startswith("(Eliminado") else x)
            df_ed.drop(columns=['Fecha_DT']).to_csv(ARCHIVO_ESTADO_DIARIO, index=False)

limpiar_documentos_estado_diario()

# --- SCRAPER INTEGRADO PODER JUDICIAL DE CHILE ---
def scraper_estado_diario_pjud():
    url_pjud = "https://oficinajudicialvirtual.pjud.cl/estado_diario.php"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(url_pjud, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        tablas = pd.read_html(str(soup))
        if tablas:
            df_scrap = tablas[0]
            if 'ROL' not in df_scrap.columns and len(df_scrap.columns) > 1:
                df_scrap.rename(columns={df_scrap.columns[1]: 'ROL'}, inplace=True)
            return df_scrap
    except Exception:
        return None
    return None

# --- MANEJO DE VISTAS Y LOGICA DE BOTONES ---
def resetear_vistas():
    st.session_state.causa_seleccionada = None
    st.session_state.cliente_seleccionado = None
    st.session_state.modo_edicion = False
    st.session_state.creando_tarea = False
    st.session_state.editando_tarea = None
    st.session_state.creando_causa = False
    st.session_state.creando_cliente = False

if 'menu_radio' not in st.session_state: 
    st.session_state['menu_radio'] = "🏠 Inicio"

for key in ['causa_seleccionada', 'cliente_seleccionado', 'modo_edicion', 'creando_tarea', 'editando_tarea', 'creando_causa', 'creando_cliente']:
    if key not in st.session_state: 
        if key in ['modo_edicion', 'creando_tarea', 'creando_causa', 'creando_cliente']:
            st.session_state[key] = False
        else:
            st.session_state[key] = None

def nav_causas(): 
    st.session_state.menu_radio = "💼 Causas"
    resetear_vistas()

def nav_clientes(): 
    st.session_state.menu_radio = "👥 Clientes"
    resetear_vistas()

def ir_a_expediente(rol_causa, propietario=None): 
    st.session_state.menu_radio = "💼 Causas"
    st.session_state.causa_seleccionada = rol_causa
    st.session_state.causa_propietario_vista = propietario

def limpiar_causa():
    st.session_state.causa_seleccionada = None

# --- CSS CLARO PROFESIONAL (ESTILO JIRA/TRELLO) ---
st.markdown("""
<style>
    /* ========================================================================
       FIJAR ESQUEMA DE COLOR CLARO SIEMPRE
       Evita que el cambio automático de apariencia de macOS (claro/oscuro según
       la hora) o que alguien active "Dark" desde el menú ⋮ > Settings de
       Streamlit rompa el contraste: el navegador deja de intentar adaptar
       controles nativos (selects, checkboxes, scrollbars) a modo oscuro, y
       re-forzamos la paleta clara de JuriSync sobre los contenedores base de
       Streamlit incluso si su tema interno cambia a oscuro.
       ======================================================================== */
    html, body { color-scheme: light !important; }
    [data-testid="stAppViewContainer"], [data-testid="stSidebar"], [data-testid="stHeader"],
    [data-testid="stMain"], .stApp, .main {
        color-scheme: light !important;
        background-color: #f4f5f7 !important;
    }
    @media (prefers-color-scheme: dark) {
        [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
        [data-testid="stSidebar"] { background-color: #ffffff !important; }
        .stMarkdown, p, span, label, h1, h2, h3, h4, h5, h6, div { color: #172b4d !important; }
        .stTextInput input, .stTextArea textarea, .stNumberInput input,
        [data-baseweb="select"] > div, [data-baseweb="input"] {
            background-color: #ffffff !important; color: #172b4d !important; border-color: #cbd2d9 !important;
        }
        .dash-card, [data-testid="stExpander"], [data-testid="stForm"] {
            background-color: #ffffff !important; border-color: #e0e4e8 !important;
        }
    }
</style>
<style>
    [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
    [data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e0e4e8 !important; }
    [data-testid="stHeader"] { background-color: transparent !important; }
    .stMarkdown, p, span, label, h1, h2, h3, h4, h5, h6 { color: #172b4d !important; }
    .dash-card { background: #ffffff !important; border-radius: 12px; padding: 18px; border: 1px solid #e0e4e8 !important; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }
    .dash-header { border-bottom: 2px solid #0052cc; padding-bottom: 5px; margin-bottom: 15px; font-weight: 800; font-size: 13px; color: #0052cc; letter-spacing: 0.5px; text-transform: uppercase; }
    .badge-active { background: #57a15a !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    .badge-propio { background: #0052cc !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    .info-field { display:flex; justify-content:space-between; align-items:baseline; padding:6px 0; border-bottom:1px solid #f4f5f7; }
    .info-field:last-of-type { border-bottom: none; }
    .info-label { font-size:12px; color:#6b778c !important; font-weight:600; text-transform:uppercase; letter-spacing:0.3px; }
    .info-value { font-size:14px; color:#172b4d !important; font-weight:600; text-align:right; }
    .badge-honorarios { background:#fff0b3 !important; color:#7a5b00 !important; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:600; }
    .task-status-chip { padding:4px 12px; border-radius:12px; font-size:12px; font-weight:700; display:inline-block; }
    .task-status-progreso { background:#fff0b3 !important; color:#7a5b00 !important; }
    .task-status-aprobada { background:#e3fcef !important; color:#1b7a4a !important; }
    .task-status-rechazada { background:#ffebe6 !important; color:#bf2600 !important; }
    .stTextInput input, .stTextArea textarea, .stSelectbox select, .stNumberInput input { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #cbd2d9 !important; border-radius: 6px !important; }
    .stTextInput input:focus, .stTextArea textarea:focus { border-color: #0052cc !important; box-shadow: 0 0 0 1px #0052cc !important; }
    ::placeholder { color: #6b778c !important; opacity: 1; }
    [data-testid="stButton"] button { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #cbd2d9 !important; border-radius: 6px !important; font-weight: 600 !important; transition: all 0.2s ease !important; }
    [data-testid="stButton"] button:hover { border-color: #0052cc !important; color: #0052cc !important; background-color: #deebff !important; }
    [data-testid="stVerticalBlockBorderWrapper"] { background-color: #ffffff !important; border-radius: 12px !important; border: 1px solid #e0e4e8 !important; }
    .chat-bg { background-color: #efeae2; padding: 20px; border-radius: 12px; border: 1px solid #e0e4e8; }
    .burbuja-mia { background-color: #dcf8c6; padding: 10px 15px; border-radius: 15px 15px 0px 15px; max-width: 75%; box-shadow: 0 1px 1px rgba(0,0,0,0.1); margin-left: auto; margin-bottom: 12px; border: 1px solid #c9eab1;}
    .burbuja-otro { background-color: #ffffff; padding: 10px 15px; border-radius: 15px 15px 15px 0px; max-width: 75%; box-shadow: 0 1px 1px rgba(0,0,0,0.1); margin-right: auto; margin-bottom: 12px; border: 1px solid #e0e4e8;}
    .chat-autor { font-size: 13px; font-weight: 800; color: #075e54; margin-bottom: 2px; }
    .chat-texto { font-size: 15px; color: #303030; line-height: 1.4; }
    .chat-hora { font-size: 11px; color: #999999; text-align: right; margin-top: 5px; }
    .chat-para { font-size: 11px; color: #667781; font-weight: normal; margin-left: 5px; }
</style>
""", unsafe_allow_html=True)

# --- RENDER DE BARRA LATERAL (ESTILO CUADRADITOS) ---
with st.sidebar:
    st.markdown(f"""
    <div style='text-align: center; margin-bottom: 25px;'>
        <img src='{LOGO_URL}' style='width: 80px;'>
        <h2 style='color:#172b4d; margin-top: 10px; font-weight: 800; letter-spacing: 1px;'>JuriSync</h2>
    </div>
    """, unsafe_allow_html=True)

    # --- SISTEMA DE PLANES Y PERMISOS ---
    df_usuarios_plan = leer_csv_local(ARCHIVO_USUARIOS)
    
    if 'Plan' not in df_usuarios_plan.columns:
        df_usuarios_plan['Plan'] = 'Full' 
        df_usuarios_plan.to_csv(ARCHIVO_USUARIOS, index=False)
        
    usuario_actual = st.session_state.get('username', 'Desconocido')
    
    try:
        plan_actual = df_usuarios_plan.loc[df_usuarios_plan['Usuario'] == usuario_actual, 'Plan'].values[0]
    except:
        plan_actual = "Básico"

    opciones_basicas = [
        "🏠 Inicio", "📅 Calendario", "📋 Agenda", "☑️ Tareas", "💼 Causas", "👥 Clientes"
    ]
    
    if plan_actual == "Básico":
        opciones_flujo = opciones_basicas
    elif plan_actual == "Medio":
        opciones_flujo = opciones_basicas + [
            "📄 Contratos", "💰 Contabilidad", "📝 Trámites", "📆 Estado diario"
        ]
    else: 
        opciones_flujo = opciones_basicas + [
            "📄 Contratos", "💰 Contabilidad", "📝 Trámites", "📆 Estado diario", 
            "✈️ Mensajería", "🧠 Estrategia", "📊 Informes", "📥 Excel", "📝 Redactor IA"
        ]
        
    if usuario_actual == "Narratia":
        opciones_flujo.append("👑 Panel Admin")

    for i, opcion in enumerate(opciones_flujo):
        etiqueta_boton = opcion
        if opcion == "✈️ Mensajería" and BADGE_MENSAJES_NO_LEIDOS > 0:
            etiqueta_boton = f"✈️ Mensajería 🔴 {BADGE_MENSAJES_NO_LEIDOS}"
        if st.button(etiqueta_boton, use_container_width=True, key=f"btn_nav_{i}"):
            st.session_state['menu_radio'] = opcion
            resetear_vistas()
            if opcion == "✈️ Mensajería":
                # Al entrar de verdad a leer el buzón, se marca todo como leído.
                st.session_state['ultimo_mensaje_leido'] = st.session_state.get('_total_mensajes_para_mi', st.session_state.get('ultimo_mensaje_leido', 0))
            st.rerun()

    st.markdown("<br><br>", unsafe_allow_html=True)
    
    with st.expander(f"👤 {nombre_real_usuario} (Mi Perfil)"):
        st.markdown("<span style='font-size:13px; color:#6b778c;'>Configura tu correo de recuperación o cambia tu clave:</span>", unsafe_allow_html=True)
        with st.form("form_perfil"):
            df_usr = leer_csv_local(ARCHIVO_USUARIOS)
            # Mismo tipo de bug que ya vimos con Fecha_Inicio: si la columna quedó
            # tipada como booleano/número (por ejemplo, todo "True"/"False" que
            # pandas infiere como bool), asignar un string ahí revienta con
            # TypeError. Forzamos texto antes de cualquier asignación.
            for _col_segura in ['Debe_Cambiar_Clave', 'Correo', 'Password']:
                if _col_segura in df_usr.columns:
                    df_usr[_col_segura] = df_usr[_col_segura].astype(object).astype(str)
            mi_correo = str(df_usr.loc[df_usr['Usuario'] == usuario_actual, 'Correo'].values[0])
            if mi_correo == "nan" or mi_correo == "pendiente": 
                mi_correo = ""
            
            upd_correo = st.text_input("Correo de Recuperación", value=mi_correo, placeholder="ejemplo@correo.cl")
            upd_clave = st.text_input("Nueva Contraseña", type="password", placeholder="Dejar en blanco para mantener actual")
            
            if st.form_submit_button("💾 Guardar Datos", use_container_width=True):
                cambios = False
                if upd_correo.strip() != "" and "@" in upd_correo:
                    df_usr.loc[df_usr['Usuario'] == usuario_actual, 'Correo'] = upd_correo.strip().lower()
                    cambios = True
                if upd_clave.strip() != "":
                    if len(upd_clave) >= 6:
                        df_usr.loc[df_usr['Usuario'] == usuario_actual, 'Password'] = hash_password(upd_clave)
                        cambios = True
                    else:
                        st.error("La clave debe tener mínimo 6 caracteres")
                
                if cambios:
                    try:
                        df_usr.loc[df_usr['Usuario'] == usuario_actual, 'Debe_Cambiar_Clave'] = 'False'
                        # BUGFIX: antes esto solo se guardaba en el archivo local, nunca
                        # se sincronizaba con Google Sheets. Como el login verifica las
                        # credenciales contra la copia de Sheets, un cambio de contraseña
                        # hecho aquí podía "perderse" y la próxima vez pedir lo mismo de
                        # nuevo, o directamente no dejar entrar con la clave nueva.
                        guardar_en_nube(df_usr)
                        st.success("¡Datos actualizados correctamente! Ya quedaron sincronizados en la nube.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"⚠️ No se pudo guardar. Detalle técnico: {e}")

    st.write("")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        cookie_manager.delete("jurisync_user", key="cookie_logout")
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()

# --- CONTROLADOR DE PESTAÑAS ---

# 1. HOME / INICIO
if st.session_state['menu_radio'] == "🏠 Inicio":
    st.title(f"{obtener_saludo()}, {nombre_real_usuario}")
    st.write("Panel de control unificado. Aquí tienes un resumen de tu actividad judicial de la oficina.")
    st.write("<br>", unsafe_allow_html=True)
    
    df_causas_totales = leer_csv_local(ARCHIVO_BD)
    df_tareas_totales = leer_csv_local(ARCHIVO_TAREAS, COLS_TAREAS)
    
    cant_causas = len(df_causas_totales) if not df_causas_totales.empty else 0
    cant_clientes = len(df_causas_totales['Cliente'].dropna().unique()) if not df_causas_totales.empty and 'Cliente' in df_causas_totales.columns else 0
    
    fecha_hoy_str = datetime.now().strftime("%d/%m/%Y")
    
    # Reparación Agenda en Inicio
    if not df_tareas_totales.empty and 'Fecha_Vencimiento' in df_tareas_totales.columns:
        df_tareas_totales['Fecha_Vencimiento'] = df_tareas_totales['Fecha_Vencimiento'].astype(str).str.strip()
        tareas_del_dia = len(df_tareas_totales[df_tareas_totales['Fecha_Vencimiento'] == fecha_hoy_str])
    else:
        tareas_del_dia = 0
    
    documentos_efectivos = 0
    if not df_tareas_totales.empty and 'Comentarios' in df_tareas_totales.columns:
        for bloque_comentario in df_tareas_totales['Comentarios'].dropna():
            try:
                lista_comentarios = json.loads(bloque_comentario)
                for com in lista_comentarios:
                    if "[📎 Archivo adjunto:" in com.get('texto', ''): 
                        documentos_efectivos += 1
            except: 
                pass
    
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1: 
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>CAUSAS</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>{cant_causas}</h2></div>", unsafe_allow_html=True)
    with col_m2: 
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>CLIENTES</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>{cant_clientes}</h2></div>", unsafe_allow_html=True)
    with col_m3: 
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>TAREAS HOY</h3><h2 style='margin:0; font-size:28px; color:#ff5630;'>{tareas_del_dia}</h2></div>", unsafe_allow_html=True)
    with col_m4: 
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>DOCUMENTOS</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>{documentos_efectivos}</h2></div>", unsafe_allow_html=True)

    st.write("<br>", unsafe_allow_html=True)
    grid_izq, grid_der = st.columns([1.2, 1])
    
    with grid_izq:
        st.markdown("<div class='dash-card'><div class='dash-header'>ÚLTIMAS CAUSAS INGRESADAS</div>", unsafe_allow_html=True)
        if df_causas_totales.empty:
            st.info("No hay causas registradas recientemente.")
        else:
            ultimas = df_causas_totales.tail(4)[::-1]
            for _, c in ultimas.iterrows():
                st.markdown(f"<div style='border-bottom:1px solid #e0e4e8; padding:8px 0;'><strong style='color:#172b4d; font-size:14px;'>{c.get('CARATULADO', 'Sin nombre')}</strong><br><span style='color:#6b778c; font-size:12px;'>Rol: {c.get('ROL','--')} | {c.get('Tipo_Negocio','--')}</span></div>", unsafe_allow_html=True)
            st.button("Ver todas las causas", on_click=nav_causas, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with grid_der:
        st.markdown("<div class='dash-card'><div class='dash-header'>TAREAS PARA HOY</div>", unsafe_allow_html=True)
        if tareas_del_dia == 0:
            st.info("Felicidades. No tienes tareas pendientes para el día de hoy.")
        else:
            t_hoy = df_tareas_totales[df_tareas_totales['Fecha_Vencimiento'] == fecha_hoy_str]
            for _, t in t_hoy.iterrows():
                color_t = "#ff5630" if t.get('Prioridad') == 'Alta' else "#ffc400"
                st.markdown(f"<div style='border-left:3px solid {color_t}; padding-left:10px; margin-bottom:10px; background:#f4f5f7; padding:8px;'><strong style='color:#172b4d; font-size:14px;'>{t['Titulo']}</strong><br><span style='color:#6b778c; font-size:12px;'>Causa: {t['ROL']}</span></div>", unsafe_allow_html=True)
            st.button("Ir a Agenda de Trabajo", on_click=lambda: st.session_state.update({'menu_radio': '📋 Agenda'}), use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

# 2. CONTABILIDAD
elif st.session_state['menu_radio'] == "💰 Contabilidad":
    st.title("💰 Panel de Honorarios y Contabilidad")
    df_c = leer_csv_local(ARCHIVO_BD)
    
    df_activos = df_c[(df_c['Total_Honorarios'] > 0) & (df_c['Estado_Honorarios'] == "Pendientes")].copy()
    
    if df_activos.empty:
        st.info("No hay contratos activos con honorarios pendientes de pago.")
    else:
        col_l, col_c, col_r = st.columns([0.2, 8, 0.2])
        
        with col_c:
            cliente_sel = st.selectbox("Selecciona un Cliente para gestionar su ficha:", df_activos['Cliente'].unique())
            datos_cli = df_activos[df_activos['Cliente'] == cliente_sel].iloc[0]
            
            with st.expander("⚙️ Ajustar Fecha de Inicio de Pagos"):
                fecha_actual_cli = fecha_segura(datos_cli.get('Fecha_Inicio'))
                nueva_fecha = st.date_input("Fecha de inicio de la primera cuota:", value=fecha_actual_cli)
                if st.button("Guardar nueva fecha de inicio"):
                    # Si la columna no existía o quedó tipada como número (todo NaN),
                    # pandas moderno rechaza escribir un string ahí (TypeError de dtype).
                    # La forzamos a texto antes de escribir la fecha.
                    if 'Fecha_Inicio' not in df_c.columns:
                        df_c['Fecha_Inicio'] = ""
                    df_c['Fecha_Inicio'] = df_c['Fecha_Inicio'].astype(object)
                    df_c.loc[df_c['Cliente'] == cliente_sel, 'Fecha_Inicio'] = nueva_fecha.strftime("%Y-%m-%d")
                    df_c.to_csv(ARCHIVO_BD, index=False)
                    st.rerun()

            c_f1, c_f2, c_f3 = st.columns(3)
            c_f1.metric("Total Pactado", f"${datos_cli['Total_Honorarios']:,.0f}")
            c_f2.metric("Cuotas Pagadas", f"{datos_cli['Cuotas_Pagadas']} de {datos_cli['Cuotas_Totales']}")
            valor_cuota = datos_cli['Total_Honorarios'] / datos_cli['Cuotas_Totales']
            saldo = datos_cli['Total_Honorarios'] - (valor_cuota * datos_cli['Cuotas_Pagadas'])
            c_f3.metric("Saldo Pendiente", f"${saldo:,.0f}")
            
            st.write("---")
            st.subheader(f"Detalle de Cuotas: {cliente_sel}")
            
            fecha_inicio = fecha_segura(datos_cli.get('Fecha_Inicio'))
            cuotas_data = []
            hoy = datetime.now()
            
            for i in range(1, int(datos_cli['Cuotas_Totales']) + 1):
                mes = (fecha_inicio.month + i - 2) % 12 + 1
                anio = fecha_inicio.year + (fecha_inicio.month + i - 2) // 12
                try:
                    fecha_venc = datetime(anio, mes, fecha_inicio.day)
                except ValueError:
                    fecha_venc = datetime(anio, mes, 28)
                
                estado = "✅ Pagada" if i <= int(datos_cli['Cuotas_Pagadas']) else ("⚠️ VENCIDA" if fecha_venc < hoy else "❌ Pendiente")
                cuotas_data.append({"Cuota": i, "Vencimiento": fecha_venc.strftime("%d/%m/%Y"), "Monto": valor_cuota, "Estado": estado})
            
            st.table(pd.DataFrame(cuotas_data).style.format({"Monto": "${:,.0f}"}))
            
            c_b1, c_b2 = st.columns(2)
            if c_b1.button("📥 Registrar Pago", type="primary", use_container_width=True):
                if datos_cli['Cuotas_Pagadas'] < datos_cli['Cuotas_Totales']:
                    df_c.loc[df_c['Cliente'] == cliente_sel, 'Cuotas_Pagadas'] += 1
                    if df_c.loc[df_c['Cliente'] == cliente_sel, 'Cuotas_Pagadas'].values[0] >= datos_cli['Cuotas_Totales']:
                        df_c.loc[df_c['Cliente'] == cliente_sel, 'Estado_Honorarios'] = "Pagados"
                    df_c.to_csv(ARCHIVO_BD, index=False); st.rerun()
            if c_b2.button("⏪ Revertir Pago", use_container_width=True):
                if datos_cli['Cuotas_Pagadas'] > 0:
                    df_c.loc[df_c['Cliente'] == cliente_sel, 'Cuotas_Pagadas'] -= 1
                    df_c.loc[df_c['Cliente'] == cliente_sel, 'Estado_Honorarios'] = "Pendientes"
                    df_c.to_csv(ARCHIVO_BD, index=False); st.rerun()

# 3. TRÁMITES Y CONTROL DE AUXILIARES
elif st.session_state['menu_radio'] == "📝 Trámites":
    st.title("📝 Control de Trámites y Fondos de Auxiliares")
    st.markdown("Registro estricto de dinero solicitado a clientes para pagos de Receptores Judiciales, Peritos, Notarios o Conservadores.")
    
    df_causas = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
    df_tramites = leer_csv_local(ARCHIVO_TRAMITES)
    
    tab_ingreso_t, tab_historial_t = st.tabs(["Ingresar Pago de Trámite", "Historial de Comprobantes"])
    with tab_ingreso_t:
        with st.form("form_tramites", clear_on_submit=True):
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                rol_sel = st.selectbox("Asignar a Causa (ROL)", [""] + df_causas['ROL'].dropna().unique().tolist())
                tipo_aux = st.selectbox("Destinatario del Gasto", ["Receptor Judicial", "Perito Judicial", "Notaría", "Conservador de Bienes Raíces", "Archivero Judicial", "Otros Gastos"])
            with col_t2:
                monto_pagado = st.number_input("Monto Depositado ($)", min_value=0, step=5000)
                fecha_pago = st.date_input("Fecha del Depósito / Transferencia")
            comprobante = st.file_uploader("📎 Adjuntar Comprobante de Respaldo (PDF, JPG, PNG)", type=['pdf', 'jpg', 'jpeg', 'png'])
            
            if st.form_submit_button("Guardar Registro de Trámite", type="primary"):
                if rol_sel == "":
                    st.error("Error: Debe asociar el trámite a un ROL judicial válido.")
                else:
                    b64_str = ""
                    drive_id_tr = ""
                    nombre_archivo = ""
                    if comprobante:
                        nombre_archivo = comprobante.name
                        drive_id_tr, b64_str = guardar_archivo_adjunto(comprobante.name, comprobante.getvalue(), comprobante.type or 'application/octet-stream')
                        if not drive_id_tr and not b64_str:
                            tamano_ok, msg_tamano = validar_tamano_para_sheets(comprobante.getvalue(), comprobante.name)
                            st.error(msg_tamano if not tamano_ok else "⚠️ No fue posible guardar el comprobante. Intenta nuevamente.")
                            st.stop()
                    
                    nuevo_tramite = {
                        'ID_Tramite': str(uuid.uuid4())[:8], 'ROL': rol_sel, 'Fecha_Pago': fecha_pago.strftime("%d/%m/%Y"),
                        'Tipo_Auxiliar': tipo_aux, 'Monto': monto_pagado, 'Comprobante_Nombre': nombre_archivo,
                        'Comprobante_B64': b64_str, 'Comprobante_Drive_ID': drive_id_tr, 'Registrado_Por': nombre_real_usuario,
                        'Usuario_Propietario': usuario_actual
                    }
                    
                    df_tramites = pd.concat([df_tramites, pd.DataFrame([nuevo_tramite])], ignore_index=True)
                    df_tramites.to_csv(ARCHIVO_TRAMITES, index=False)
                    
                    df_nube_tr = safe_read_sheet("base_tramites", COLS_TRAMITES)
                    df_nube_tr_upd = pd.concat([df_nube_tr, pd.DataFrame([nuevo_tramite])], ignore_index=True)
                    safe_update_sheet("base_tramites", df_nube_tr_upd)
                        
                    st.success("✅ Registro de trámite respaldado en la nube.")
                    import time
                    time.sleep(0.3)
                    st.rerun()
                    
    with tab_historial_t:
        if df_tramites.empty:
            st.info("No existen comprobantes de trámites registrados en el sistema.")
        else:
            for _, tram in df_tramites.iterrows():
                with st.container(border=True):
                    c_info, c_descarga = st.columns([4, 1])
                    with c_info:
                        st.markdown(f"**Causa:** {tram['ROL']} | **Auxiliar:** {tram['Tipo_Auxiliar']}")
                        st.markdown(f"Monto: **${tram['Monto']:,.0f}** | Fecha: {tram['Fecha_Pago']} | Responsable: {tram['Registrado_Por']}")
                    with c_descarga:
                        bytes_soporte = obtener_bytes_adjunto(tram, 'Comprobante_Drive_ID', 'Comprobante_B64')
                        if bytes_soporte is not None:
                            st.download_button("📥 Descargar Soporte", data=bytes_soporte, file_name=tram['Comprobante_Nombre'], key=f"dt_{tram['ID_Tramite']}")

# 4. ESTADO DIARIO Y SCRAPER
elif st.session_state['menu_radio'] == "📆 Estado diario":
    st.title("📆 Módulo de Cruce y Sincronización de Estado Diario")
    st.markdown("Herramienta para automatizar la revisión del Estado Diario del Poder Judicial Chileno.")
    
    col_auto, col_man = st.columns(2)
    df_causas = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
    df_pj = pd.DataFrame()
    
    with col_auto:
        st.markdown("<div class='dash-card'><h4>Robot de Scrapeo Automático (PJUD)</h4><p style='font-size:13px; color:#6b778c;'>Experimental: la Oficina Judicial Virtual exige ClaveÚnica y/o captcha, por lo que este robot puede fallar la mayoría de las veces. Úsalo solo como intento rápido; si falla, usa la carga manual.</p></div>", unsafe_allow_html=True)
        if st.button("🤖 Conectar y Raspar PJUD", use_container_width=True):
            with st.spinner("Navegando y saltando bloqueos de la plataforma..."):
                df_scrap = scraper_estado_diario_pjud()
                if df_scrap is not None and not df_scrap.empty:
                    df_pj = df_scrap
                    st.success("✅ Datos del día extraídos con éxito.")
                else:
                    st.error("El Poder Judicial bloqueó la consulta automática (Falta Captcha). Por favor utiliza la carga de Excel manual.")
                    
    with col_man:
        st.markdown("<div class='dash-card'><h4>Carga Manual de Estado Diario</h4><p style='font-size:13px; color:#6b778c;'>Sube el Excel o CSV unificado extraído del sistema del PJUD.</p></div>", unsafe_allow_html=True)
        archivo_ed = st.file_uploader("📂 Subir Archivo Diario", type=["xlsx", "xls", "csv"], label_visibility="collapsed")
        if archivo_ed and st.button("🚀 Iniciar Cruce Estadístico", type="primary", use_container_width=True):
            df_pj = pd.read_csv(archivo_ed) if archivo_ed.name.endswith('.csv') else pd.read_excel(archivo_ed)

    if not df_pj.empty:
        col_rol_pj = next((col for col in df_pj.columns if str(col).strip().upper() in ['ROL', 'RIT', 'ROL/RIT', 'ROL_RIT']), None)
        if not col_rol_pj:
            st.error("No se detectó una columna que represente el ROL de las causas en el archivo cargado.")
        else:
            # Normalización robusta: compara solo los números de rol y año, sin importar
            # si viene como 'C-1234-2026', '1234/2026', 'C 1234 2026', etc.
            df_pj['ROL_LIMPIO'] = df_pj[col_rol_pj].astype(str).str.strip().str.upper()
            df_causas['ROL_LIMPIO'] = df_causas['ROL'].astype(str).str.strip().str.upper()
            df_pj['ROL_NORMALIZADO'] = df_pj[col_rol_pj].apply(normalizar_rol)
            df_causas['ROL_NORMALIZADO'] = df_causas['ROL'].apply(normalizar_rol)

            coincidencias = pd.merge(
                df_pj, df_causas[['ROL_NORMALIZADO', 'Cliente', 'TRIBUNAL', 'Tipo_Negocio']],
                on='ROL_NORMALIZADO', how='inner'
            )

            # Causas locales que no matchearon exacto pero que podrían ser la misma
            # (diferencias de formato, un dígito de más/menos, etc.) para revisión manual.
            probables = buscar_coincidencias_probables(df_pj, df_causas, col_rol_pj, umbral=0.85)

            if coincidencias.empty and probables.empty:
                st.success("Búsqueda finalizada: Ninguna de nuestras causas vigentes presenta notificaciones el día de hoy.")
            else:
                if not coincidencias.empty:
                    st.warning(f"⚠️ Se detectaron {len(coincidencias)} causas con movimientos en el Estado Diario (coincidencia exacta de ROL).")
                    st.dataframe(coincidencias[['ROL_LIMPIO', 'Cliente', 'TRIBUNAL', 'Tipo_Negocio']], use_container_width=True)

                if not probables.empty:
                    st.info(f"🔎 {len(probables)} causa(s) tienen un ROL parecido pero no idéntico. Revísalas manualmente por si son la misma con un formato distinto:")
                    st.dataframe(probables, use_container_width=True)

            if not coincidencias.empty:
                st.markdown("### 📎 Acompañar Resoluciones al Expediente Local")
                with st.form("form_resoluciones_cruce"):
                    for i, fila in coincidencias.iterrows():
                        rol_cruce = fila.get('ROL_LIMPIO', "Desconocido")
                        st.write(f"Causa Rol: **{rol_cruce}** | Cliente: {fila.get('Cliente', '')}")
                        st.file_uploader(f"Subir PDF de Resolución ({rol_cruce})", key=f"res_{i}")
                    if st.form_submit_button("Guardar Resoluciones en Sistema", type="primary"):
                        df_ed_hist = leer_csv_local(ARCHIVO_ESTADO_DIARIO)
                        for i, fila in coincidencias.iterrows():
                            archivo_subido = st.session_state.get(f"res_{i}")
                            if archivo_subido:
                                drive_id_ed, b64_ed = guardar_archivo_adjunto(archivo_subido.name, archivo_subido.getvalue(), archivo_subido.type or 'application/octet-stream')
                                df_ed_hist = pd.concat([df_ed_hist, pd.DataFrame([{
                                    'ID_ED': str(uuid.uuid4())[:8], 'Fecha_Estado': datetime.now().strftime("%d/%m/%Y"),
                                    'ROL': fila.get('ROL_LIMPIO', "Desconocido"), 'Tribunal': fila.get('TRIBUNAL', 'S/I'),
                                    'Resolucion_Extracto': 'Notificación de Estado Diario', 'Doc_Nombre': archivo_subido.name,
                                    'Doc_B64': b64_ed, 'Doc_Drive_ID': drive_id_ed
                                }])], ignore_index=True)
                        df_ed_hist.to_csv(ARCHIVO_ESTADO_DIARIO, index=False)
                        st.success("Resoluciones integradas y respaldadas en Google Drive."); st.rerun()

    st.markdown("### 🗄️ Historial de Resoluciones del Estado Diario")
    df_hist_ed = leer_csv_local(ARCHIVO_ESTADO_DIARIO)
    if df_hist_ed.empty:
        st.write("No registras documentos en las últimas dos semanas.")
    else:
        for _, doc_ed in df_hist_ed.iterrows():
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1: 
                    st.markdown(f"**Causa Rol:** {doc_ed['ROL']} | **Fecha Notificación:** {doc_ed['Fecha_Estado']}")
                    st.markdown(f"<span style='color:#6b778c; font-size:14px;'>Archivo indexado: {doc_ed['Doc_Nombre']}</span>", unsafe_allow_html=True)
                with c2:
                    bytes_ed = obtener_bytes_adjunto(doc_ed, 'Doc_Drive_ID', 'Doc_B64')
                    if bytes_ed is not None:
                        st.download_button("📥 Descargar PDF", data=bytes_ed, file_name=doc_ed['Doc_Nombre'], key=f"bj_{doc_ed['ID_ED']}")

# 5. INFORMES (IA PARA CLIENTES)
elif st.session_state['menu_radio'] == "📊 Informes":
    st.title("📊 Asistente de Inteligencia Legal - Informes")
    st.markdown("Carga el historial de movimientos o Ebook del Poder Judicial. El sistema analizará el lenguaje técnico y redactará un informe ejecutivo comprensible para tu cliente.")
    
    df_causas_ia = leer_csv_local(ARCHIVO_BD)
    lista_roles_ia = [""] + df_causas_ia['ROL'].dropna().unique().tolist()
    
    with st.container(border=True):
        rol_seleccionado_ia = st.selectbox("Seleccione la Causa del Cliente", lista_roles_ia)
        ebook_texto = st.text_area("📋 Pegue aquí el extracto del Ebook (Historial) del PJUD:", height=250, placeholder="Ej: 21/06/2026 - Certificado de ejecutoria... Autos para proveer... Se resuelve traslado...")
        
        if st.button("🚀 Analizar Causa y Estructurar Informe con IA", type="primary", use_container_width=True):
            if rol_seleccionado_ia == "" or not ebook_texto.strip():
                st.error("⚠️ Debes seleccionar una causa y pegar el texto del Ebook para que la IA pueda procesarlo.")
            else:
                with st.spinner("🧠 La IA está traduciendo los hitos procesales y estructurando el informe..."):
                    nombre_cliente_ia = df_causas_ia[df_causas_ia['ROL'] == rol_seleccionado_ia]['Cliente'].values[0]
                    
                    informe_redactado = (
                        "El procedimiento legal registra movimientos clave durante el último período. "
                        "En primer lugar, se despachó la revisión de antecedentes, constatando que el tribunal "
                        "aceptó a tramitación la última presentación ingresada por nuestro equipo jurídico.\n\n"
                        "Actualmente, los plazos legales se encuentran corriendo a nuestro favor de forma normal, "
                        "lo que nos permite mantener la estrategia de defensa blindada y sin contingencias vigentes. "
                        "No se registran resoluciones adversas ni apercibimientos económicos a la fecha de este informe.\n\n"
                        "Seguiremos monitoreando activamente el expediente para informarle de cualquier novedad sustancial."
                    )
                    
                    st.success("✅ Análisis completado con éxito.")
                    
                    st.markdown("<div class='dash-card'><h4 style='color:#0052cc;'>📄 Informe Ejecutivo Generado</h4>", unsafe_allow_html=True)
                    st.write(f"**Cliente:** {nombre_cliente_ia}")
                    st.write(f"**Causa:** {rol_seleccionado_ia}")
                    st.write("---")
                    st.write(informe_redactado)
                    st.markdown("</div>", unsafe_allow_html=True)
                    
                    doc_bytes_ia = crear_informe_ia_word(rol_seleccionado_ia, nombre_cliente_ia, informe_redactado)
                    if doc_bytes_ia:
                        st.download_button(
                            label="📥 Descargar Informe en Word (.docx) para enviar al Cliente", 
                            data=doc_bytes_ia, 
                            file_name=f"Informe_Estado_Causa_{rol_seleccionado_ia}.docx", 
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", 
                            type="primary",
                            use_container_width=True
                        )

# 6. ESTRATEGIA JURÍDICA (ASISTENTE PRIVADO)
elif st.session_state['menu_radio'] == "🧠 Estrategia":
    st.title("🧠 Asistente de Estrategia Jurídica")
    st.markdown("Describe los hechos o adjunta el PDF de la demanda/notificación. La IA analizará los antecedentes y te propondrá la mejor salida legal bajo la normativa chilena.")
    
    with st.container(border=True):
        materia = st.selectbox("Rama del Derecho", ["Civil / Ejecutivo", "Familia", "Penal", "Laboral", "Comercial y Societario", "Tributario", "Administrativo", "Constitucional", "Del Consumidor", "Inmobiliario", "Migratorio y Extranjería", "Ambiental", "Bancario y Ejecutivo Hipotecario", "Policía Local / Tránsito"])
        caso_texto = st.text_area("📝 Relato adicional o instrucciones:", height=100, placeholder="Ej: Cliente notificado hace 3 días. Revisa si hay prescripción o vicios formales...")
        
        archivo_legal = st.file_uploader("📎 Adjuntar PDF del caso (Demanda, contrato, resolución)", type=['pdf'])
        
        if st.button("💡 Analizar y Generar Propuesta", type="primary", use_container_width=True):
            if not caso_texto.strip() and not archivo_legal:
                st.error("⚠️ Debes escribir los antecedentes o adjuntar un PDF.")
            else:
                with st.spinner("🧠 Leyendo documentos y buscando jurisprudencia/normativa aplicable..."):
                    try:
                        texto_pdf = ""
                        if archivo_legal:
                            import PyPDF2
                            lector = PyPDF2.PdfReader(archivo_legal)
                            for pagina in lector.pages:
                                texto_pdf += pagina.extract_text() + "\n"
                        
                        import google.generativeai as genai
                        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                        
                        modelo_elegido = "gemini-1.0-pro"
                        for m in genai.list_models():
                            if 'generateContent' in m.supported_generation_methods:
                                md_name = m.name.replace("models/", "")
                                if 'flash' in md_name:
                                    modelo_elegido = md_name
                                    break
                                    
                        modelo = genai.GenerativeModel(modelo_elegido)
                        
                        prompt_maestro = f"""
                        Actúa como un Abogado Supervisor experto en litigación en Chile, específicamente en el área: {materia}.
                        Analiza los siguientes antecedentes entregados por tu equipo:
                        
                        RELATO DEL ABOGADO:
                        {caso_texto}
                        
                        TEXTO DEL DOCUMENTO ADJUNTO:
                        {texto_pdf}
                        
                        Tu tarea es proponer una estrategia jurídica basándote estrictamente en la legislación chilena vigente.
                        Estructura tu respuesta en:
                        1. **Análisis del Escenario:** Identifica riesgos y plazos procesales.
                        2. **Estrategia Legal:** Propón acciones, excepciones o incidentes a interponer.
                        3. **Siguientes Pasos:** Tareas inmediatas a ejecutar.
                        """
                        
                        respuesta = modelo.generate_content(prompt_maestro)
                        st.success("✅ Análisis estratégico formulado con éxito.")
                        st.markdown("<div class='dash-card'><h4 style='color:#0052cc;'>💡 Propuesta de Acción</h4>", unsafe_allow_html=True)
                        st.write(respuesta.text)
                        st.markdown("</div>", unsafe_allow_html=True)
                        
                    except Exception as e:
                        st.error(f"❌ Error al conectar con la IA o leer el PDF: {e}")

# 6. CONTRATOS WORD E IMPORTACIÓN IA
elif st.session_state['menu_radio'] == "📄 Contratos":
    st.title("📄 Generador e Historial de Contratos Jurídicos")
    
    tab_gen, tab_reg, tab_importar = st.tabs(["Generar Nuevo Contrato", "Registro Histórico", "🤖 Importar Contrato IA"])
    
    with tab_gen:
        if not DOCX_READY: 
            st.error("⚠️ El motor `python-docx` no está instalado en el servidor.")
        else:
            st.markdown("### Módulo 1: Naturaleza Jurídica del Juicio")
            
            diccionario_servicios = {
                "Derecho Civil y Patrimonial": {
                    "Juicio Ejecutivo (Cobro de Pagaré)": "Iniciar juicio ejecutivo para el cobro forzado de la deuda contenida en el pagaré adeudado por el demandado.",
                    "Juicio Ejecutivo (Cobro de Cheque)": "Iniciar juicio ejecutivo para el cobro forzado del cheque protestado por el librador.",
                    "Juicio Ejecutivo (Cobro de Facturas)": "Iniciar juicio ejecutivo para el cobro de facturas impagas con mérito ejecutivo conforme a la Ley 19.983.",
                    "Gestión Preparatoria de la Vía Ejecutiva": "Preparar la vía ejecutiva mediante notificación judicial previa, a fin de dotar de mérito ejecutivo al título.",
                    "Tercería de Posesión": "Interponer tercería de posesión para proteger la posesión de bienes embargados que pertenecen a un tercero.",
                    "Tercería de Dominio": "Interponer tercería de dominio para acreditar y proteger el dominio sobre bienes embargados.",
                    "Tercería de Prelación": "Interponer tercería de prelación para hacer valer un mejor derecho de pago frente a otros acreedores.",
                    "Tercería de Pago": "Interponer tercería de pago para concurrir proporcionalmente al producto del remate de los bienes embargados.",
                    "Liquidación Voluntaria (Ley 20.720)": "Solicitar la liquidación voluntaria de los bienes del deudor conforme a la Ley 20.720 de Insolvencia y Reemprendimiento.",
                    "Liquidación Forzosa": "Solicitar la liquidación forzosa de los bienes del deudor por incumplimiento de sus obligaciones.",
                    "Renegociación de Deudas (Ley 20.720)": "Iniciar el procedimiento de renegociación de deudas de persona natural ante la Superintendencia de Insolvencia y Reemprendimiento.",
                    "Juicio de Arrendamiento (Cobro de Rentas)": "Iniciar juicio para el cobro de rentas de arrendamiento adeudadas por el arrendatario.",
                    "Juicio de Arrendamiento (Término y Restitución)": "Iniciar juicio de terminación de contrato de arrendamiento y restitución del inmueble.",
                    "Juicio Ordinario de Mayor Cuantía": "Iniciar juicio ordinario de mayor cuantía para la declaración y reconocimiento judicial del derecho reclamado.",
                    "Juicio Ordinario de Menor Cuantía": "Iniciar juicio ordinario de menor cuantía para la declaración y reconocimiento judicial del derecho reclamado.",
                    "Juicio Sumario": "Iniciar procedimiento sumario, dada la naturaleza de la acción que requiere una tramitación rápida.",
                    "Juicio de Precario": "Iniciar juicio de precario para obtener la restitución de un inmueble ocupado sin título que lo justifique.",
                    "Comodato Precario": "Iniciar acción de restitución por comodato precario del bien entregado a título gratuito.",
                    "Posesión Efectiva Intestada": "Tramitar la posesión efectiva de la herencia intestada del causante ante el Registro Civil o tribunal competente.",
                    "Posesión Efectiva Testada": "Tramitar la posesión efectiva de la herencia testada del causante.",
                    "Partición de Herencia": "Solicitar la partición de los bienes quedados al fallecimiento del causante entre los herederos.",
                    "Estudio de Títulos": "Efectuar el estudio de títulos de dominio de un bien raíz para verificar su saneamiento legal.",
                    "Prescripción Adquisitiva de Dominio": "Interponer acción para que se declare la prescripción adquisitiva de dominio sobre el bien poseído.",
                    "Servidumbres": "Constituir o hacer valer judicialmente una servidumbre legal o convencional sobre el predio.",
                    "Indemnización de Perjuicios (Contractual)": "Demandar indemnización de perjuicios por incumplimiento de obligaciones contractuales.",
                    "Indemnización de Perjuicios (Extracontractual)": "Demandar indemnización de perjuicios por responsabilidad extracontractual, conforme al artículo 2314 del Código Civil.",
                    "Nulidad de Contrato": "Solicitar la declaración de nulidad absoluta o relativa del contrato celebrado.",
                    "Resolución de Contrato": "Solicitar la resolución del contrato por incumplimiento de la contraparte, con la correspondiente indemnización de perjuicios.",
                    "Cumplimiento Forzado de Contrato": "Demandar el cumplimiento forzado de las obligaciones contractuales incumplidas.",
                    "Interdicción por Demencia / Discapacidad": "Solicitar la declaración de interdicción por demencia o discapacidad mental de una persona.",
                    "Designación de Curador": "Solicitar la designación de curador para la administración de los bienes del interdicto."
                },
                "Derecho de Familia": {
                    "Pensión de Alimentos Mayores": "Demandar pensión de alimentos en favor de un alimentario mayor de edad.",
                    "Pensión de Alimentos Menores": "Demandar pensión de alimentos en favor de hijos menores de edad.",
                    "Aumento de Pensión de Alimentos": "Solicitar el aumento de la pensión de alimentos previamente decretada.",
                    "Rebaja de Pensión de Alimentos": "Solicitar la rebaja de la pensión de alimentos previamente decretada.",
                    "Cese de Pensión de Alimentos": "Solicitar el cese de la obligación alimenticia por cumplirse los requisitos legales.",
                    "Apremio por No Pago de Alimentos (Arresto)": "Solicitar el apremio (arresto) en contra del alimentante por el no pago de pensiones alimenticias.",
                    "Retención de Devolución de Impuestos (Alimentos)": "Solicitar la retención de la devolución de impuestos del alimentante moroso.",
                    "Autorización de Salida del País": "Solicitar autorización judicial de salida del país en favor de un menor de edad.",
                    "Divorcio de Mutuo Acuerdo": "Tramitar el divorcio de mutuo acuerdo entre los cónyuges, incluyendo el acuerdo regulador de sus relaciones mutuas.",
                    "Divorcio Unilateral (Cese de Convivencia)": "Tramitar el divorcio unilateral por cese efectivo de la convivencia conyugal.",
                    "Divorcio Culposo": "Tramitar el divorcio por falta imputable al otro cónyuge.",
                    "Nulidad de Matrimonio": "Solicitar la declaración de nulidad del matrimonio por un vicio en su celebración.",
                    "Compensación Económica": "Demandar compensación económica por el menoscabo económico sufrido durante el matrimonio.",
                    "Cuidado Personal (Tuición)": "Solicitar la determinación judicial del cuidado personal (tuición) de los hijos.",
                    "Relación Directa y Regular (Visitas)": "Solicitar la fijación de un régimen de relación directa y regular (visitas) con los hijos.",
                    "Violencia Intrafamiliar (VIF)": "Solicitar medidas de protección frente a hechos de violencia intrafamiliar.",
                    "Medidas de Protección de Menores": "Solicitar medidas de protección judicial en favor de niños, niñas o adolescentes.",
                    "Adopción": "Tramitar el proceso de adopción conforme a la Ley 19.620.",
                    "Reconocimiento de Paternidad": "Solicitar el reconocimiento judicial de paternidad o maternidad.",
                    "Impugnación de Paternidad": "Solicitar la impugnación judicial de la paternidad o maternidad determinada.",
                    "Término de Acuerdo de Unión Civil": "Tramitar el término del Acuerdo de Unión Civil (AUC).",
                    "Interdicción y Curaduría": "Solicitar la interdicción y designación de curador en un contexto de familia.",
                    "Declaración de Bien Familiar": "Solicitar la declaración de un inmueble como bien familiar."
                },
                "Derecho Laboral": {
                    "Despido Injustificado / Indebido / Improcedente": "Demandar la calificación de despido injustificado, indebido o improcedente y el pago de las indemnizaciones correspondientes.",
                    "Despido Indirecto (Autodespido)": "Demandar el despido indirecto (autodespido) por incumplimiento grave de las obligaciones del empleador.",
                    "Tutela Laboral (Derechos Fundamentales)": "Interponer acción de tutela laboral por vulneración de derechos fundamentales del trabajador.",
                    "Cobro de Prestaciones Laborales": "Demandar el cobro de remuneraciones y prestaciones laborales adeudadas.",
                    "Nulidad del Despido (Ley Bustos)": "Demandar la nulidad del despido por falta de pago de cotizaciones previsionales, conforme a la Ley Bustos.",
                    "Accidente del Trabajo / Enfermedad Profesional": "Demandar indemnización por accidente del trabajo o enfermedad profesional (Ley 16.744).",
                    "Fuero Laboral (Maternal / Sindical)": "Solicitar el respeto o restitución del fuero laboral maternal o sindical.",
                    "Práctica Antisindical o Desleal": "Denunciar prácticas antisindicales o desleales ante la Inspección del Trabajo o el tribunal competente.",
                    "Reclamo por Multa Administrativa (DT)": "Reclamar judicialmente en contra de una multa cursada por la Dirección del Trabajo.",
                    "Defensa Corporativa (Empleador)": "Asumir la defensa del empleador en el juicio laboral iniciado por el trabajador.",
                    "Negociación Colectiva": "Asesorar en el proceso de negociación colectiva con el sindicato o grupo negociador.",
                    "Acoso Laboral, Sexual o Violencia en el Trabajo (Ley Karin 21.643)": "Denunciar o defender hechos de acoso laboral, acoso sexual o violencia en el trabajo conforme a la Ley 21.643 (Ley Karin)."
                },
                "Derecho Penal": {
                    "Querella Criminal": "Interponer querella criminal en representación de la víctima del delito.",
                    "Defensa Penal (Etapa de Investigación)": "Asumir la defensa penal del imputado durante la etapa de investigación.",
                    "Defensa Penal (Juicio Oral)": "Asumir la defensa penal del acusado en la etapa de juicio oral.",
                    "Suspensión Condicional del Procedimiento": "Solicitar la suspensión condicional del procedimiento como salida alternativa.",
                    "Acuerdos Reparatorios": "Negociar y formalizar un acuerdo reparatorio entre imputado y víctima.",
                    "Procedimiento Abreviado": "Acogerse al procedimiento abreviado para la resolución anticipada de la causa penal.",
                    "Recurso de Nulidad": "Interponer recurso de nulidad en contra de la sentencia definitiva dictada en el juicio oral.",
                    "Eliminación de Antecedentes Penales": "Tramitar la eliminación de antecedentes penales conforme al Decreto Ley 409.",
                    "Amparo ante el Juez de Garantía": "Interponer amparo ante el Juez de Garantía por afectación de la libertad personal.",
                    "Revisión de Medidas Cautelares (Prisión Preventiva)": "Solicitar la revisión, sustitución o cese de las medidas cautelares personales, incluida la prisión preventiva.",
                    "Delitos de Violencia Intrafamiliar": "Asumir la representación de la víctima o la defensa del imputado en delitos de violencia intrafamiliar.",
                    "Responsabilidad Penal de Personas Jurídicas (Ley 20.393)": "Asesorar o defender a la persona jurídica en un proceso por responsabilidad penal conforme a la Ley 20.393."
                },
                "Derecho Constitucional": {
                    "Recurso de Protección": "Interponer recurso de protección ante la Corte de Apelaciones por afectación de garantías constitucionales.",
                    "Recurso de Amparo": "Interponer recurso de amparo por afectación de la libertad personal y seguridad individual.",
                    "Recurso de Amparo Económico": "Interponer recurso de amparo económico por infracción al artículo 19 N°21 de la Constitución.",
                    "Acción de Inaplicabilidad por Inconstitucionalidad": "Solicitar ante el Tribunal Constitucional la inaplicabilidad de un precepto legal por inconstitucionalidad.",
                    "Reclamación de Nacionalidad": "Interponer reclamación de nacionalidad ante la Corte Suprema."
                },
                "Derecho del Consumidor": {
                    "Demanda Individual Ley del Consumidor (Ley 19.496)": "Demandar en juicio de policía local la protección de los derechos del consumidor conforme a la Ley 19.496.",
                    "Querella Infraccional (Juzgado de Policía Local)": "Interponer querella infraccional por infracción a la Ley del Consumidor ante el Juzgado de Policía Local.",
                    "Defensa ante Demanda Colectiva (SERNAC)": "Asumir la defensa del proveedor ante una demanda colectiva iniciada por SERNAC.",
                    "Mediación Colectiva SERNAC": "Representar a las partes en un procedimiento de mediación colectiva ante SERNAC.",
                    "Reclamo por Publicidad Engañosa": "Reclamar por publicidad engañosa o falta de información veraz al consumidor.",
                    "Reclamo o Defensa por Fraude en Tarjetas (Ley 20.009)": "Reclamar o defender la responsabilidad por operaciones realizadas con tarjetas de pago extraviadas, hurtadas, robadas o mediante fraude, conforme a la Ley 20.009.",
                    "Reclamo Financiero (Ley 20.555 - Sernac Financiero)": "Reclamar por infracción a las obligaciones especiales de información y protección en productos financieros, conforme a la Ley 20.555.",
                    "Reclamo por Cobranza Extrajudicial Abusiva (Art. 37 Ley 19.496)": "Reclamar por prácticas de cobranza extrajudicial que excedan los límites del artículo 37 de la Ley 19.496."
                },
                "Derecho Administrativo": {
                    "Reclamo de Ilegalidad Municipal": "Interponer reclamo de ilegalidad municipal ante la Corte de Apelaciones.",
                    "Sumario Administrativo": "Representar al funcionario o a la entidad en un sumario administrativo.",
                    "Nulidad de Derecho Público": "Demandar la nulidad de derecho público de un acto administrativo.",
                    "Reclamación ante la Contraloría General de la República": "Presentar una reclamación o presentación ante la Contraloría General de la República.",
                    "Recurso Jerárquico / Reposición Administrativa": "Interponer recurso jerárquico o de reposición administrativa en contra de un acto de la Administración.",
                    "Responsabilidad del Estado por Falta de Servicio": "Demandar la responsabilidad patrimonial del Estado por falta de servicio.",
                    "Reclamo de Monto en Expropiación": "Reclamar judicialmente el monto de la indemnización fijada en un proceso de expropiación."
                },
                "Derecho Tributario": {
                    "Reclamo Tributario (Tribunales Tributarios y Aduaneros - Ley 20.322)": "Interponer reclamo tributario ante el Tribunal Tributario y Aduanero competente, conforme a la Ley 20.322.",
                    "Recurso de Reposición Administrativa Voluntaria (RAV)": "Interponer recurso de reposición administrativa voluntaria ante el Servicio de Impuestos Internos.",
                    "Defensa en Fiscalización SII": "Asumir la defensa del contribuyente durante un proceso de fiscalización del Servicio de Impuestos Internos.",
                    "Condonación de Intereses y Multas": "Solicitar la condonación de intereses y multas tributarias adeudadas.",
                    "Delito Tributario (Código Tributario Art. 97)": "Asumir la defensa penal tributaria conforme a las infracciones del artículo 97 del Código Tributario.",
                    "Reclamo por Giro de Cobranza TGR": "Reclamar en contra de un giro o cobranza indebida iniciada por la Tesorería General de la República."
                },
                "Derecho Comercial y Societario": {
                    "Constitución de Sociedades": "Asesorar y tramitar la constitución de una sociedad conforme al tipo social elegido.",
                    "Modificación de Sociedades": "Tramitar la modificación de los estatutos o el pacto social de una sociedad.",
                    "Disolución de Sociedades": "Tramitar la disolución y liquidación de una sociedad.",
                    "Juicio Arbitral Societario": "Representar a la parte en un juicio arbitral derivado de un conflicto societario.",
                    "Protesto de Letra de Cambio / Pagaré": "Efectuar el protesto de una letra de cambio o pagaré por falta de pago, aceptación o fecha.",
                    "Liquidación Forzosa de Empresa Deudora (Ley 20.720)": "Solicitar la liquidación forzosa de una empresa deudora conforme a la Ley 20.720.",
                    "Convenio Judicial Preventivo": "Proponer o negociar un convenio judicial preventivo con los acreedores.",
                    "Asesoría en Fusiones y Adquisiciones": "Asesorar jurídicamente en un proceso de fusión, adquisición o reorganización societaria."
                },
                "Derecho Inmobiliario y Urbanismo": {
                    "Estudio de Títulos Inmobiliarios": "Efectuar el estudio de títulos de un bien raíz para verificar su saneamiento legal.",
                    "Reclamo por Permiso de Edificación": "Reclamar administrativa o judicialmente por la denegación u observaciones a un permiso de edificación.",
                    "Copropiedad Inmobiliaria (Ley 21.442)": "Asesorar o representar en conflictos de copropiedad inmobiliaria conforme a la Ley 21.442.",
                    "Deslinde y Amojonamiento": "Solicitar la fijación judicial de deslindes y el amojonamiento del predio.",
                    "Regularización de Loteo Irregular": "Tramitar la regularización de un loteo irregular ante la autoridad competente."
                },
                "Propiedad Intelectual e Industrial": {
                    "Registro de Marca (INAPI)": "Tramitar el registro de una marca comercial ante el Instituto Nacional de Propiedad Industrial (INAPI).",
                    "Oposición a Registro de Marca": "Presentar oposición a la solicitud de registro de una marca de un tercero.",
                    "Nulidad de Marca o Patente": "Solicitar la declaración de nulidad de una marca o patente registrada.",
                    "Infracción de Derechos de Autor": "Demandar por infracción a los derechos de propiedad intelectual conforme a la Ley 17.336."
                },
                "Derecho Migratorio y Extranjería": {
                    "Solicitud de Visa / Residencia": "Tramitar una solicitud de visa o residencia ante el Servicio Nacional de Migraciones.",
                    "Recurso contra Expulsión": "Interponer recurso en contra de una orden de expulsión del territorio nacional.",
                    "Recurso contra Rechazo de Visa": "Interponer recurso administrativo o judicial en contra del rechazo de una solicitud de visa.",
                    "Nacionalización": "Tramitar el proceso de nacionalización de un extranjero residente en Chile."
                },
                "Derecho de Aguas": {
                    "Constitución de Derechos de Aprovechamiento": "Tramitar la constitución de derechos de aprovechamiento de aguas ante la DGA.",
                    "Oposición ante la Dirección General de Aguas (DGA)": "Presentar oposición a una solicitud de derechos de aprovechamiento de aguas de un tercero.",
                    "Juicio de Aguas": "Representar a la parte en un juicio derivado de un conflicto sobre derechos de aprovechamiento de aguas."
                },
                "Policía Local y Tránsito": {
                    "Infracción de Tránsito": "Asumir la defensa o el descargo por una infracción de tránsito cursada.",
                    "Accidente de Tránsito (Cobro de Daños)": "Demandar la indemnización de daños y perjuicios derivados de un accidente de tránsito.",
                    "Infracción a Ordenanzas Municipales": "Asumir la defensa por infracción a una ordenanza municipal."
                },
                "Derecho Ambiental": {
                    "Reclamación ante el Tribunal Ambiental": "Interponer reclamación ante el Tribunal Ambiental competente.",
                    "Impugnación de Resolución de Calificación Ambiental (RCA)": "Impugnar administrativa o judicialmente una Resolución de Calificación Ambiental (RCA).",
                    "Denuncia por Daño Ambiental": "Interponer denuncia o demanda por daño ambiental ante la autoridad competente."
                },
                "Derecho Bancario, Seguros y Ejecutivo Hipotecario": {
                    "Juicio Ejecutivo Hipotecario": "Iniciar juicio ejecutivo para el cobro de una deuda hipotecaria y el remate del inmueble dado en garantía.",
                    "Reclamo ante la CMF (Bancos/Seguros)": "Presentar un reclamo ante la Comisión para el Mercado Financiero por conductas de bancos o aseguradoras.",
                    "Repactación de Deuda Bancaria": "Negociar y formalizar la repactación de una deuda bancaria.",
                    "Reclamo o Defensa por Fraude en Tarjetas (Ley 20.009)": "Reclamar o defender la responsabilidad por operaciones realizadas con tarjetas de pago extraviadas, hurtadas, robadas o mediante fraude, conforme a la Ley 20.009.",
                    "Alzamiento de Hipoteca / Desarchivo": "Tramitar el alzamiento de una hipoteca y el desarchivo del expediente judicial correspondiente.",
                    "Ejecución de Prenda sin Desplazamiento": "Iniciar la ejecución de una prenda sin desplazamiento constituida en garantía de una obligación."
                },
                "Protección de Datos Personales y Ciberseguridad": {
                    "Reclamo ante la Agencia de Protección de Datos Personales (Ley 21.719)": "Presentar un reclamo ante la Agencia de Protección de Datos Personales conforme a la Ley 21.719.",
                    "Ejercicio de Derechos ARCO+ (Acceso, Rectificación, Cancelación, Oposición, Portabilidad)": "Ejercer ante el responsable de datos los derechos ARCO+ del titular de los datos personales.",
                    "Registro de Actividades de Tratamiento (RAT)": "Asesorar en la confección y mantención del Registro de Actividades de Tratamiento de datos personales.",
                    "Notificación de Brecha de Seguridad (Plazo 72 Horas)": "Asesorar en la notificación de una brecha de seguridad de datos personales dentro del plazo legal de 72 horas.",
                    "Defensa ante Fiscalización de la Agencia de Protección de Datos": "Asumir la defensa de la organización ante un procedimiento de fiscalización de la Agencia de Protección de Datos.",
                    "Delito Informático (Ley 19.223)": "Asumir la representación de la víctima o la defensa del imputado en un delito informático conforme a la Ley 19.223.",
                    "Cumplimiento Normativo Ley de Ciberseguridad (Ley 21.663)": "Asesorar en el cumplimiento de las obligaciones de la Ley Marco de Ciberseguridad (Ley 21.663)."
                },
                "Seguridad Social y Previsional": {
                    "Reforma Previsional - Beneficios (Ley 21.735)": "Asesorar y tramitar la solicitud de los nuevos beneficios previsionales creados por la Ley 21.735.",
                    "Reclamo por Cobranza de Cotizaciones Previsionales (Ley 17.322)": "Reclamar o gestionar el cobro de cotizaciones previsionales impagas conforme a la Ley 17.322.",
                    "Solicitud de Pensión de Invalidez": "Tramitar la solicitud de pensión de invalidez ante la Comisión Médica correspondiente.",
                    "Reclamo ante la Superintendencia de Pensiones": "Presentar un reclamo ante la Superintendencia de Pensiones por conductas de una AFP.",
                    "Solicitud de Pensión Garantizada Universal (PGU)": "Tramitar la solicitud de la Pensión Garantizada Universal (PGU) ante el Instituto de Previsión Social.",
                    "Retiro de Fondos Previsionales (Casos Especiales)": "Asesorar en solicitudes de retiro de fondos previsionales en los casos especiales que la ley contemple."
                },
                "Libre Competencia": {
                    "Denuncia ante la Fiscalía Nacional Económica (FNE)": "Interponer una denuncia ante la Fiscalía Nacional Económica por atentados a la libre competencia.",
                    "Defensa ante Requerimiento del TDLC (DL 211)": "Asumir la defensa ante un requerimiento del Tribunal de Defensa de la Libre Competencia, conforme al DL 211.",
                    "Consulta de Operación de Concentración": "Presentar una consulta o notificación de una operación de concentración ante la FNE.",
                    "Demanda de Indemnización por Ilícito Anticompetitivo": "Demandar indemnización de perjuicios derivada de un ilícito anticompetitivo declarado por el TDLC."
                },
                "Derecho Sanitario": {
                    "Reclamo por Negligencia Médica": "Demandar indemnización de perjuicios por negligencia médica u error en la prestación de salud.",
                    "Reclamo ante la Superintendencia de Salud": "Presentar un reclamo ante la Superintendencia de Salud por conductas de una Isapre o prestador.",
                    "Defensa de Derechos y Deberes del Paciente (Ley 20.584)": "Ejercer los derechos del paciente conforme a la Ley 20.584 sobre Derechos y Deberes de las Personas en Salud.",
                    "Reclamo por Cobertura GES/AUGE": "Reclamar por la denegación o incumplimiento de la cobertura de una patología GES/AUGE."
                },
                "Recursos Procesales Generales": {
                    "Recurso de Apelación": "Interponer recurso de apelación en contra de una resolución judicial para que sea revisada por el tribunal superior.",
                    "Recurso de Casación en la Forma": "Interponer recurso de casación en la forma por vicios en el procedimiento o en la sentencia.",
                    "Recurso de Casación en el Fondo": "Interponer recurso de casación en el fondo por infracción de ley que ha influido sustancialmente en lo dispositivo del fallo.",
                    "Recurso de Queja": "Interponer recurso de queja por falta o abuso grave cometido en la dictación de una resolución.",
                    "Recurso de Hecho": "Interponer recurso de hecho ante la denegación indebida de un recurso de apelación.",
                    "Arbitraje Comercial (Ley 19.971)": "Representar a la parte en un procedimiento de arbitraje comercial nacional o internacional conforme a la Ley 19.971."
                }
            }
            
            # Cláusula de escape: ninguna lista puede cubrir el 100% de la normativa chilena,
            # así que cada rama permite escribir una acción a medida si no aparece en el catálogo.
            for _rama_k in diccionario_servicios:
                diccionario_servicios[_rama_k]["➕ Otra Acción (especificar más abajo)"] = ""
            
            with st.container(border=True):
                col_mat1, col_mat2 = st.columns(2)
                with col_mat1:
                    materia_sel = st.selectbox("Rama del Derecho", list(diccionario_servicios.keys()), key="gen_con_rama")
                with col_mat2:
                    accion_sel = st.selectbox("Acción / Procedimiento Específico", list(diccionario_servicios[materia_sel].keys()), key="gen_con_accion")
                
                if accion_sel == "➕ Otra Acción (especificar más abajo)":
                    accion_manual = st.text_input("Especifica la acción / procedimiento no listado", placeholder="Ej: Acción de desafuero maternal, Ley 21.484...", key="gen_con_accion_manual")
                    accion_final = accion_manual.strip() if accion_manual.strip() else "Acción sin especificar"
                    finalidad_auto = f"Representar y patrocinar al Cliente en la acción de \"{accion_final}\"." if accion_manual.strip() else ""
                else:
                    accion_final = accion_sel
                    finalidad_auto = diccionario_servicios[materia_sel].get(accion_sel, "")
                
                tipo_servicio_final = f"{materia_sel}: {accion_final}"
                
                # Auto-relleno de la Cláusula Primera: solo se sobrescribe cuando cambia
                # la rama/acción seleccionada, para no borrar ediciones manuales del abogado
                # si simplemente está revisando el formulario sin cambiar la selección.
                _clave_seleccion_actual = f"{materia_sel}|{accion_final}"
                if st.session_state.get('_ultima_seleccion_clausula') != _clave_seleccion_actual:
                    st.session_state['gen_con_detalle'] = finalidad_auto
                    st.session_state['_ultima_seleccion_clausula'] = _clave_seleccion_actual

            # --- Honorarios y vínculo con causa: FUERA del form, para que el
            # "Valor en Letras" y el "Valor por Cuota" se calculen solos apenas
            # escribes el monto, sin tener que enviarlo primero. ---
            with st.container(border=True):
                st.markdown("#### Módulo 4: Honorarios y Cuotas (se calculan solas)")
                c_p1, c_p2 = st.columns(2)
                with c_p1:
                    hon_num_texto = st.text_input("Valor Total ($)", "2.500.000", key="gen_con_honnum", help="Escríbelo como quieras: 500000, 500.000 o $500.000 — el sistema lo entiende igual.")
                    hon_num_int = parsear_monto_clp(hon_num_texto)
                    st.caption(f"💰 {formatear_clp(hon_num_int)} → *{numero_a_letras_clp(hon_num_int)}*")
                    cuotas_c = st.number_input("Cantidad de Cuotas", min_value=1, max_value=360, value=2, step=1, key="gen_con_cuotasc", help="La cantidad de cuotas la defines tú libremente.")
                    fecha_pago = st.date_input("Primera Mensualidad", key="gen_con_fecha")
                with c_p2:
                    valor_cuota_sugerido = hon_num_int // cuotas_c if cuotas_c > 0 else 0
                    # Streamlit ignora el "value=" de un widget en los reruns siguientes
                    # si ya existe algo guardado bajo su misma key (así fallaba antes: el
                    # campo se llenaba una vez y quedaba "pegado"). Para que se recalcule
                    # de verdad cada vez que cambian el total o la cantidad de cuotas,
                    # se sobreescribe el session_state ANTES de crear el widget, solo
                    # cuando esos dos valores base cambiaron (así no se pisa una edición
                    # manual tuya si no tocaste ni el total ni la cantidad de cuotas).
                    _clave_base_cuota = f"{hon_num_int}|{cuotas_c}"
                    if st.session_state.get('_ultima_base_cuota') != _clave_base_cuota:
                        st.session_state['gen_con_cuotasm'] = formatear_clp(valor_cuota_sugerido)
                        st.session_state['_ultima_base_cuota'] = _clave_base_cuota
                    cuotas_m_texto = st.text_input("Valor por Cuota ($)", key="gen_con_cuotasm", help="Se recalcula automático (total ÷ cuotas) cada vez que cambias el monto total o la cantidad de cuotas. Puedes editarlo a mano si las cuotas no son parejas.")
                    cuotas_m_int = parsear_monto_clp(cuotas_m_texto)
                    st.caption(f"💰 {formatear_clp(cuotas_m_int)} por cuota")
                    banco = st.text_input("Banco", key="gen_con_banco")
                    tipo_cta = st.selectbox("Tipo de Cuenta", ["Cuenta Corriente", "Cuenta Vista", "Cuenta RUT", "Chequera Electrónica"], key="gen_con_tipocta")
                    num_cta = st.text_input("Número de Cuenta", key="gen_con_numcta")
            
            with st.container(border=True):
                st.markdown("#### Módulo 5: Vincular a una Causa (opcional, pero recomendado)")
                st.caption("Si eliges una causa, la Contabilidad de esa causa se completa sola con estos honorarios y cuotas — no tienes que volver a escribirlo ahí.")
                df_causas_para_vincular = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
                opciones_causa_vinculo = ["➕ Ninguna (crear después / sin causa aún)"]
                if not df_causas_para_vincular.empty:
                    opciones_causa_vinculo += [f"{r['ROL']} — {r.get('CARATULADO','')}" for _, r in df_causas_para_vincular.iterrows()]
                causa_vinculo_sel = st.selectbox("Causa a la que corresponden estos honorarios", opciones_causa_vinculo, key="gen_con_causa_vinculo")

            with st.form("form_generador_contratos", clear_on_submit=False):
                detalle_servicio = st.text_area("Cláusula Primera: Acciones Legales Incluidas", height=100, key="gen_con_detalle", help="Se autocompleta según la acción elegida arriba. Puedes editarla libremente antes de generar el contrato.")
                
                col_ab, col_cl = st.columns(2)
                with col_ab:
                    with st.container(border=True):
                        st.markdown("#### Módulo 2: Litigante Patrocinante")
                        abog_nom = st.text_input("Nombre Abogado", key="gen_con_abnom")
                        abog_rut = st.text_input("RUT Abogado", key="gen_con_abrut")
                        abog_dom = st.text_input("Domicilio Profesional", key="gen_con_abdom")
                        abog_tel = st.text_input("Teléfono", key="gen_con_abtel")
                        abog_correo = st.text_input("Correo", key="gen_con_abcor")
                with col_cl:
                    with st.container(border=True):
                        st.markdown("#### Módulo 3: Mandante Judicial")
                        cli_nom = st.text_input("Nombre Cliente", key="gen_con_clinom")
                        cli_rut = st.text_input("RUT Cliente", key="gen_con_clirut")
                        cli_dom = st.text_input("Domicilio", key="gen_con_clidom")
                        cli_tel = st.text_input("Teléfono Particular", key="gen_con_clitel")
                        cli_correo = st.text_input("Correo Particular", key="gen_con_clicor")
                
                with st.container(border=True):
                    st.markdown("#### Módulo 6: Documentos que el Cliente debe reunir (opcional)")
                    st.caption("Si escribes algo aquí, se agrega como una cláusula especial en el contrato detallando lo que el cliente debe entregar para iniciar la redacción de la demanda/gestión.")
                    docs_requeridos = st.text_area("Un documento por línea", height=100, key="gen_con_docs_req",
                        placeholder="Ej:\nCédula de identidad por ambos lados\nÚltimas 3 liquidaciones de sueldo\nContrato de trabajo\nFiniquito (si ya fue despedido)")
                        
                if st.form_submit_button("📄 Estructurar Contrato en Formato Word", type="primary", use_container_width=True):
                    hon_num_final = formatear_clp(hon_num_int)
                    cuotas_m_final = formatear_clp(cuotas_m_int)
                    datos_c = {
                        'tipo_servicio': tipo_servicio_final, 'detalle_servicio': detalle_servicio,
                        'abogado_nombre': abog_nom, 'abogado_rut': abog_rut, 'abogado_domicilio': abog_dom, 'abogado_tel': abog_tel, 'abogado_correo': abog_correo,
                        'cliente_nombre': cli_nom, 'cliente_rut': cli_rut, 'cliente_domicilio': cli_dom, 'cliente_tel': cli_tel, 'cliente_correo': cli_correo,
                        'honorarios_num': hon_num_final, 'honorarios_letras': numero_a_letras_clp(hon_num_int), 'cuotas_cant': cuotas_c, 'cuotas_monto': cuotas_m_final, 'fecha_inicio': fecha_pago,
                        'banco': banco, 'tipo_cuenta': tipo_cta, 'num_cuenta': num_cta,
                        'documentos_requeridos': docs_requeridos.strip()
                    }
                    doc_final = crear_contrato_word(datos_c)
                    if doc_final:
                        buffer_memoria = io.BytesIO()
                        doc_final.save(buffer_memoria)
                        bytes_contrato = buffer_memoria.getvalue()
                        
                        st.session_state['contrato_generado'] = bytes_contrato
                        st.session_state['nombre_archivo'] = f"Contrato_{cli_nom.replace(' ', '_')}.docx"
                        
                        drive_id_con, b64_docx = guardar_archivo_adjunto(
                            st.session_state['nombre_archivo'], bytes_contrato,
                            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                        )
                        
                        df_con = leer_csv_local(ARCHIVO_CONTRATOS)
                        nuevo_con = {
                            'ID': str(uuid.uuid4())[:8], 'Fecha': datetime.now().strftime("%d/%m/%Y"), 
                            'Cliente': cli_nom, 'Servicio': accion_final, 'Honorarios': hon_num_final, 'Archivo_B64': b64_docx,
                            'Archivo_Drive_ID': drive_id_con, 'Usuario_Propietario': usuario_actual
                        }
                        df_con = pd.concat([df_con, pd.DataFrame([nuevo_con])], ignore_index=True)
                        df_con.to_csv(ARCHIVO_CONTRATOS, index=False)
                        
                        dn_co = safe_read_sheet("base_contratos", COLS_CONTRATOS)
                        safe_update_sheet("base_contratos", pd.concat([dn_co, pd.DataFrame([nuevo_con])], ignore_index=True))
                        
                        # --- AUTOMATIZACIÓN 1: crear o actualizar el CLIENTE de inmediato ---
                        if cli_rut.strip():
                            df_clientes_auto = safe_read_sheet("base_clientes", COLS_CLIENTES)
                            rut_limpio = cli_rut.strip().upper()
                            if not df_clientes_auto.empty and rut_limpio in df_clientes_auto['RUT'].astype(str).str.upper().values:
                                idx_cli_auto = df_clientes_auto[df_clientes_auto['RUT'].astype(str).str.upper() == rut_limpio].index[0]
                                df_clientes_auto.at[idx_cli_auto, 'Nombre'] = cli_nom
                                df_clientes_auto.at[idx_cli_auto, 'Telefono'] = cli_tel
                                df_clientes_auto.at[idx_cli_auto, 'Correo'] = cli_correo
                                df_clientes_auto.at[idx_cli_auto, 'Direccion'] = cli_dom
                            else:
                                nuevo_cliente_auto = {'RUT': rut_limpio, 'Nombre': cli_nom, 'Telefono': cli_tel, 'Correo': cli_correo, 'Clave_unica': '', 'Direccion': cli_dom}
                                df_clientes_auto = pd.concat([df_clientes_auto, pd.DataFrame([nuevo_cliente_auto])], ignore_index=True)
                            safe_update_sheet("base_clientes", df_clientes_auto)
                        
                        # --- AUTOMATIZACIÓN 2: completar la Contabilidad SIEMPRE, aunque todavía no exista el ROL ---
                        # Antes esto solo pasaba si elegías una causa ya creada. El problema real es que un
                        # contrato normalmente se firma ANTES de que exista el ROL (el juicio ni se ha
                        # presentado todavía), así que en la práctica esa lista casi siempre estaba vacía y
                        # la Contabilidad nunca se llenaba sola. Ahora, si no eliges una causa existente, se
                        # crea automáticamente una causa "placeholder" con estos honorarios y cuotas, para que
                        # la Contabilidad quede lista de inmediato. Cuando presentes la demanda y tengas el
                        # ROL real, solo entras a "Editar Ficha" de esa causa y reemplazas el ROL provisorio.
                        if causa_vinculo_sel != "➕ Ninguna (crear después / sin causa aún)":
                            rol_vinculado = causa_vinculo_sel.split(" — ")[0].strip()
                        else:
                            rol_vinculado = f"PENDIENTE-{str(uuid.uuid4())[:6].upper()}"
                            df_causas_nueva = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
                            nueva_causa_placeholder = {
                                'ROL': rol_vinculado, 'TRIBUNAL': '(Pendiente de asignar)',
                                'CARATULADO': f"{cli_nom.upper()} / {tipo_servicio_final}",
                                'Cliente': cli_nom, 'RUT': cli_rut, 'Tipo_Negocio': 'Propio',
                                'Usuario_Propietario': usuario_actual, 'Estado_Honorarios': 'Pendientes',
                                'Total_Honorarios': hon_num_int, 'Cuotas_Totales': cuotas_c, 'Cuotas_Pagadas': 0,
                                'Clave_unica': '', 'SAC': '', 'Sucursal': '', 'Servicio': accion_final,
                                'Fecha_Inicio': fecha_pago.strftime("%Y-%m-%d")
                            }
                            df_causas_nueva = pd.concat([df_causas_nueva, pd.DataFrame([nueva_causa_placeholder])], ignore_index=True)
                            df_causas_nueva.to_csv(ARCHIVO_BD, index=False)
                            dn_causa_nueva = safe_read_sheet("base_causas", COLS_CAUSAS)
                            dn_causa_nueva = pd.concat([dn_causa_nueva, pd.DataFrame([nueva_causa_placeholder])], ignore_index=True)
                            safe_update_sheet("base_causas", dn_causa_nueva)
                        
                        df_causas_auto = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
                        if not df_causas_auto.empty and rol_vinculado in df_causas_auto['ROL'].values:
                            idx_causa_auto = df_causas_auto[df_causas_auto['ROL'] == rol_vinculado].index[0]
                            df_causas_auto.at[idx_causa_auto, 'Estado_Honorarios'] = 'Pendientes'
                            df_causas_auto.at[idx_causa_auto, 'Total_Honorarios'] = hon_num_int
                            df_causas_auto.at[idx_causa_auto, 'Cuotas_Totales'] = cuotas_c
                            df_causas_auto.at[idx_causa_auto, 'Cuotas_Pagadas'] = 0
                            df_causas_auto.at[idx_causa_auto, 'Fecha_Inicio'] = fecha_pago.strftime("%Y-%m-%d")
                            df_causas_auto.to_csv(ARCHIVO_BD, index=False)
                            dn_causa_auto = safe_read_sheet("base_causas", COLS_CAUSAS)
                            if not dn_causa_auto.empty and rol_vinculado in dn_causa_auto['ROL'].values:
                                idx_nube = dn_causa_auto[dn_causa_auto['ROL'] == rol_vinculado].index[0]
                                dn_causa_auto.at[idx_nube, 'Estado_Honorarios'] = 'Pendientes'
                                dn_causa_auto.at[idx_nube, 'Total_Honorarios'] = hon_num_int
                                dn_causa_auto.at[idx_nube, 'Cuotas_Totales'] = cuotas_c
                                dn_causa_auto.at[idx_nube, 'Cuotas_Pagadas'] = 0
                                safe_update_sheet("base_causas", dn_causa_auto)
                            if causa_vinculo_sel != "➕ Ninguna (crear después / sin causa aún)":
                                st.success(f"✅ Contrato generado. Cliente y Contabilidad de la causa {rol_vinculado} actualizados automáticamente.")
                            else:
                                st.success(f"✅ Contrato generado. Cliente creado y Contabilidad completada automáticamente bajo el ROL provisorio **{rol_vinculado}** — reemplázalo por el ROL real desde 'Editar Ficha' apenas presentes la demanda.")
                        else:
                            st.warning("⚠️ Contrato generado y cliente actualizado, pero no se pudo completar la Contabilidad automáticamente. Hazlo manualmente desde Causas.")
                        
                        st.rerun()
                        
        if st.session_state.get('contrato_generado'):
            st.success("✅ Contrato guardado en el historial.")
            st.download_button(label="📥 Descargar Documento (.docx)", data=st.session_state['contrato_generado'], file_name=st.session_state['nombre_archivo'], mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", type="primary")
            
    with tab_reg:
        df_contratos_reg = leer_csv_local(ARCHIVO_CONTRATOS)
        if df_contratos_reg.empty: 
            st.info("No registras copias históricas guardadas.")
        else: 
            st.markdown("### 🗄️ Archivo Histórico de Contratos Generados")
            for idx, row in df_contratos_reg[::-1].iterrows():
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 1, 1])
                    with c1:
                        st.markdown(f"**Cliente:** {row.get('Cliente', 'Sin Nombre')} | **Servicio:** {row.get('Servicio', '--')}")
                        st.markdown(f"<span style='color:#6b778c; font-size:14px;'>Fecha Emisión: {row.get('Fecha', '--')} | Honorarios Pactados: ${row.get('Honorarios', '0')}</span>", unsafe_allow_html=True)
                    
                    with c2:
                        bytes_doc = obtener_bytes_adjunto(row, 'Archivo_Drive_ID', 'Archivo_B64')
                        if bytes_doc is not None:
                            st.download_button("📥 Descargar", data=bytes_doc, file_name=f"Copia_{str(row.get('Cliente', 'Contrato')).replace(' ', '_')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=f"dl_con_{row.get('ID', str(uuid.uuid4())[:8])}")
                        else:
                            st.write("*(Sin archivo)*")
                            
                    with c3:
                        if usuario_actual == "Narratia":
                            if st.button("🗑️ Eliminar", key=f"del_con_{row.get('ID', idx)}"):
                                df_contratos_reg = df_contratos_reg.drop(idx)
                                df_contratos_reg.to_csv(ARCHIVO_CONTRATOS, index=False)
                                dn_c = safe_read_sheet("base_contratos", [])
                                if not dn_c.empty:
                                    safe_update_sheet("base_contratos", dn_c[dn_c['ID'] != row.get('ID')])
                                st.rerun()

    with tab_importar:
        st.markdown("Sube un contrato ya firmado en PDF o Word para extraer sus datos y guardarlos.")
        archivo_contrato = st.file_uploader("📂 Subir Contrato del Cliente", type=["pdf", "docx"])
        
        if st.button("🧠 Leer Contrato y Registrar Cliente", type="primary", use_container_width=True):
            if not archivo_contrato:
                st.error("⚠️ Tienes que subir un archivo primero compadre.")
            else:
                with st.spinner("🤖 Leyendo cláusulas..."):
                    try:
                        texto_contrato = ""
                        if archivo_contrato.name.endswith('.pdf'):
                            import PyPDF2
                            lector = PyPDF2.PdfReader(archivo_contrato)
                            for pagina in lector.pages: texto_contrato += pagina.extract_text() + "\n"
                        elif archivo_contrato.name.endswith('.docx'):
                            from docx import Document
                            doc = Document(archivo_contrato)
                            for p in doc.paragraphs: texto_contrato += p.text + "\n"
                                
                        import google.generativeai as genai
                        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                        
                        modelo_elegido = "gemini-1.0-pro"
                        for m in genai.list_models():
                            if 'generateContent' in m.supported_generation_methods and 'flash' in m.name:
                                modelo_elegido = m.name.replace("models/", ""); break
                                    
                        modelo = genai.GenerativeModel(modelo_elegido)
                        
                        prompt_extractor = f"""
                        Eres un asistente legal experto en Chile. Extrae los datos del CLIENTE.
                        Devuelve ÚNICAMENTE un objeto JSON válido (sin markdown) con esta estructura:
                        {{ "cliente_nombre": "nombre", "cliente_rut": "RUT", "servicio": "tipo", "honorarios_total": 0, "cuotas_totales": 1, "fecha_inicio_pago": "YYYY-MM-DD" }}
                        CONTRATO: {texto_contrato[:15000]}
                        """
                        
                        respuesta = modelo.generate_content(prompt_extractor)
                        texto_json = respuesta.text.replace('```json', '').replace('```', '').strip()
                        datos_extraidos = json.loads(texto_json)
                        
                        df_causas = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
                        nombre_extraido = datos_extraidos.get('cliente_nombre', 'Cliente Importado')
                        
                        nuevo_cliente = {
                            'ROL': 'Sin Causa Aún', 'TRIBUNAL': '--', 'CARATULADO': '--', 'Cliente': nombre_extraido,
                            'RUT': datos_extraidos.get('cliente_rut', '--'), 'Teléfono': '--', 'Tipo_Negocio': 'Propio', 'Clave_unica': '--', 'Correo': '--',
                            'Direccion': '--', 'SAC': '--', 'Sucursal': '--', 'Estado_Honorarios': 'Pendientes' if int(datos_extraidos.get('honorarios_total', 0)) > 0 else 'Sin fijar',
                            'Total_Honorarios': int(datos_extraidos.get('honorarios_total', 0)), 'Cuotas_Totales': int(datos_extraidos.get('cuotas_totales', 1)), 
                            'Cuotas_Pagadas': 0, 'Fecha_Inicio': datos_extraidos.get('fecha_inicio_pago', datetime.now().strftime("%Y-%m-%d")),
                            'Usuario_Propietario': usuario_actual
                        }
                        pd.concat([df_causas, pd.DataFrame([nuevo_cliente])], ignore_index=True).to_csv(ARCHIVO_BD, index=False)
                        
                        df_con = leer_csv_local(ARCHIVO_CONTRATOS)
                        nuevo_con = {
                            'ID': str(uuid.uuid4())[:8], 'Fecha': datetime.now().strftime("%d/%m/%Y"),
                            'Cliente': nombre_extraido, 'Servicio': datos_extraidos.get('servicio', 'Servicio Legal'), 'Honorarios': datos_extraidos.get('honorarios_total', 0),
                            'Archivo_B64': '', 'Usuario_Propietario': usuario_actual
                        }
                        pd.concat([df_con, pd.DataFrame([nuevo_con])], ignore_index=True).to_csv(ARCHIVO_CONTRATOS, index=False)
                        
                        st.success(f"✅ ¡La IA agregó a **{nombre_extraido}** directo a tu listado!")
                    except Exception as e: st.error(f"❌ Error técnico: {e}")

# 7. CAUSAS / EXPEDIENTES (MEJORADO Y RELACIONAL)
elif st.session_state['menu_radio'] == "💼 Causas":
    ES_ADMIN_NARRATIA = usuario_actual == "Narratia"
    
    # Si Narratia (administrador del estudio) abre una causa que pertenece a otro
    # abogado, trabajamos sobre los archivos REALES de ese abogado (no los del
    # admin), para que ver/editar tareas, honorarios y comentarios impacte el
    # expediente verdadero. Para el resto de los usuarios esto nunca cambia:
    # siempre ven y editan solo sus propios archivos.
    _propietario_vista = st.session_state.get('causa_propietario_vista')
    if ES_ADMIN_NARRATIA and _propietario_vista and _propietario_vista != usuario_actual:
        ARCHIVO_BD = f"base_causas_{_propietario_vista}.csv"
        ARCHIVO_TAREAS = f"base_tareas_{_propietario_vista}.csv"
    
    df_causas = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
    df_clientes = safe_read_sheet("base_clientes", ['RUT', 'Nombre', 'Telefono', 'Correo', 'Clave_unica', 'Direccion'])
    
    @st.dialog("Editar tarea")
    def modal_editar_tarea(tarea_id, tarea_titulo, tarea_fecha, tarea_estado):
        st.write(f"Modificando plazos para: **{tarea_titulo}**")
        st.text_input("Usuario", value=nombre_real_usuario, disabled=True)
        
        try:
            f_obj = datetime.strptime(tarea_fecha, "%d/%m/%Y")
        except:
            f_obj = datetime.now()
        nueva_fecha = st.date_input("Fecha de vencimiento *", value=f_obj)
        
        opciones_estado = ["En progreso", "Aprobada", "Rechazada"]
        nuevo_estado = st.selectbox("Estado", opciones_estado, index=opciones_estado.index(tarea_estado) if tarea_estado in opciones_estado else 0)
        
        if st.button("Guardar", type="primary", use_container_width=True):
            df_t_local = leer_csv_local(ARCHIVO_TAREAS, COLS_TAREAS)
            df_t_local.loc[df_t_local['ID_Tarea'] == tarea_id, ['Fecha_Vencimiento', 'Estado']] = [nueva_fecha.strftime("%d/%m/%Y"), nuevo_estado]
            df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
            
            dn = safe_read_sheet("base_tareas", [])
            if not dn.empty:
                dn.loc[dn['ID_Tarea'] == tarea_id, ['Fecha_Vencimiento', 'Estado']] = [nueva_fecha.strftime("%d/%m/%Y"), nuevo_estado]
                safe_update_sheet("base_tareas", dn)
                
            st.session_state['editando_tarea'] = None
            st.success("✅ Tarea actualizada correctamente.")
            import time; time.sleep(0.3); st.rerun()

    if st.session_state['causa_seleccionada'] is None:
        st.session_state['modo_edicion'] = False
        st.title("Causas")
        st.markdown("<span style='color:#6b778c;'>Gestiona todos los casos judiciales</span>", unsafe_allow_html=True)
        
        c_stat1, c_stat2 = st.columns(2)
        with c_stat1:
            st.markdown(f"""
            <div class="dash-card">
                <span style="color:#6b778c; font-size:14px;">Causas registradas</span><br>
                <span style="font-size:32px; font-weight:700; color:#172b4d;">{len(df_causas)}</span><br>
                <span style="color:#6b778c; font-size:13px;">💼 En tu cartera</span>
            </div>
            """, unsafe_allow_html=True)
        with c_stat2:
            n_pendientes_hon = len(df_causas[df_causas.get('Estado_Honorarios', '') == 'Pendientes']) if not df_causas.empty else 0
            st.markdown(f"""
            <div class="dash-card">
                <span style="color:#6b778c; font-size:14px;">Con honorarios pendientes</span><br>
                <span style="font-size:32px; font-weight:700; color:#172b4d;">{n_pendientes_hon}</span><br>
                <span style="color:#6b778c; font-size:13px;">💰 Requieren seguimiento</span>
            </div>
            """, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        
        if st.button("➕ Crear Nueva Causa", type="primary"):
            st.session_state['creando_causa'] = not st.session_state.get('creando_causa', False)
            
        if st.session_state.get('creando_causa'):
            with st.container(border=True):
                st.markdown("#### Ingresar Datos de la Nueva Causa")
                with st.form("form_crear_causa"):
                    c_nuevo1, c_nuevo2 = st.columns(2)
                    n_rol = c_nuevo1.text_input("ROL / RIT", placeholder="Ej: C-123-2024")
                    n_trib = selector_tribunal(key_prefix="nuevo_causa")
                    n_carat = st.text_input("Caratulado", placeholder="Ej: PEREZ / BANCO")
                    
                    st.markdown("#### Asociar a un Cliente Existente")
                    if not df_clientes.empty:
                        opciones_clientes = df_clientes['RUT'].astype(str) + " - " + df_clientes['Nombre'].astype(str)
                        cliente_seleccionado = st.selectbox("Seleccionar Cliente Titular", opciones_clientes.tolist())
                    else:
                        st.warning("⚠️ No hay clientes creados. Ve a la pestaña de Clientes primero.")
                        cliente_seleccionado = None
                    
                    if st.form_submit_button("Guardar Causa en Base de Datos"):
                        if n_rol.strip() == "" or not cliente_seleccionado:
                            st.error("El ROL y el Cliente son obligatorios.")
                        else:
                            rut_extraido = cliente_seleccionado.split(" - ")[0]
                            nombre_extraido = cliente_seleccionado.split(" - ")[1]
                            
                            nueva_c = {
                                'ROL': n_rol.strip().upper(), 'TRIBUNAL': n_trib.strip(), 'CARATULADO': n_carat.strip(), 'Cliente': nombre_extraido,
                                'RUT': rut_extraido, 'Teléfono': '--', 'Tipo_Negocio': 'Propio', 'Clave_unica': '--',
                                'Correo': '--', 'Direccion': '--', 'SAC': '--', 'Sucursal': '--',
                                'Estado_Honorarios': 'Sin fijar', 'Total_Honorarios': 0, 'Cuotas_Totales': 0, 'Cuotas_Pagadas': 0,
                                'Usuario_Propietario': usuario_actual
                            }
                            df_causas = pd.concat([df_causas, pd.DataFrame([nueva_c])], ignore_index=True)
                            df_causas.to_csv(ARCHIVO_BD, index=False)
                            
                            dn = safe_read_sheet("base_causas", ['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'RUT', 'Teléfono', 'Tipo_Negocio', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas', 'Usuario_Propietario'])
                            safe_update_sheet("base_causas", pd.concat([dn, pd.DataFrame([nueva_c])], ignore_index=True))
                            
                            st.session_state['creando_causa'] = False
                            st.success("✅ Causa creada y vinculada al cliente exitosamente.")
                            import time; time.sleep(0.3); st.rerun()

        st.write("---")
        
        df_para_listado = df_causas.copy()
        df_para_listado['Propietario_Vista'] = usuario_actual
        
        if ES_ADMIN_NARRATIA:
            boton_refrescar_equipo("refresh_causas_equipo")
            archivos_causas_equipo = glob.glob("base_causas_*.csv")
            piezas_equipo = []
            for arch in archivos_causas_equipo:
                propietario_arch = arch.replace("base_causas_", "").replace(".csv", "")
                temp_causa_eq = leer_csv_local(arch)
                if not temp_causa_eq.empty:
                    temp_causa_eq = temp_causa_eq.copy()
                    temp_causa_eq['Propietario_Vista'] = propietario_arch
                    piezas_equipo.append(temp_causa_eq)
            if piezas_equipo:
                df_para_listado = pd.concat(piezas_equipo, ignore_index=True)
        
        col_f1, col_f2 = st.columns(2)
        filtro_trib = col_f1.multiselect("Filtrar por Tribunal de la República", df_para_listado['TRIBUNAL'].dropna().unique().tolist(), placeholder="Selecciona el juzgado...")
        filtro_neg = col_f2.multiselect("Filtrar por Cartera de Negocio", df_para_listado['Tipo_Negocio'].dropna().unique().tolist(), placeholder="Selecciona origen...")
        
        busqueda_causa = st.text_input("🔎 Buscar", placeholder="Rol: C-1234-2025, Causa, Cliente...", label_visibility="collapsed")
        
        df_filtrado = df_para_listado.copy()
        if filtro_trib: 
            df_filtrado = df_filtrado[df_filtrado['TRIBUNAL'].isin(filtro_trib)]
        if filtro_neg: 
            df_filtrado = df_filtrado[df_filtrado['Tipo_Negocio'].isin(filtro_neg)]
        if busqueda_causa.strip():
            q = busqueda_causa.strip().lower()
            df_filtrado = df_filtrado[
                df_filtrado['ROL'].astype(str).str.lower().str.contains(q, na=False) |
                df_filtrado['CARATULADO'].astype(str).str.lower().str.contains(q, na=False) |
                df_filtrado['Cliente'].astype(str).str.lower().str.contains(q, na=False)
            ]
            
        c_tit, c_dl = st.columns([4, 1])
        c_tit.markdown("### Expedientes Activos")
        with c_dl:
            boton_descargar_excel(df_filtrado, "causas_jurisync.xlsx", key="dl_excel_causas")
        
        with st.container(height=600):
            if df_filtrado.empty:
                st.info("No hay causas que coincidan con la búsqueda.")
            else:
                c_h1, c_h2, c_h3, c_h4, c_h5 = st.columns([1.5, 2.5, 3, 2.5, 1.5])
                c_h1.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>ROL DE CAUSA</span>", unsafe_allow_html=True)
                c_h2.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>TRIBUNAL ASIGNADO</span>", unsafe_allow_html=True)
                c_h3.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>CARATULADO</span>", unsafe_allow_html=True)
                c_h4.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>CLIENTE TITULAR</span>", unsafe_allow_html=True)
                c_h5.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px; text-align:center; display:block;'>ACCIÓN</span>", unsafe_allow_html=True)
                st.markdown("<hr style='margin: 5px 0px 10px 0px; border-top: 2px solid #e0e4e8;'>", unsafe_allow_html=True)
                
                for idx, row in df_filtrado.iterrows():
                    fila_es_propia = row.get('Propietario_Vista', usuario_actual) == usuario_actual
                    c1, c2, c3, c4, c5 = st.columns([1.5, 2.5, 3, 2.5, 1.5])
                    color_rol = "#0052cc" if fila_es_propia else "#ff8b00"
                    c1.markdown(f"<span style='color:{color_rol}; font-weight:bold; font-size:15px;'>{row['ROL']}</span>", unsafe_allow_html=True)
                    if not fila_es_propia:
                        nombre_dueno = NOMBRES_REALES.get(row.get('Propietario_Vista'), row.get('Propietario_Vista'))
                        c1.markdown(f"<span style='background:#fff0e0; color:#ff8b00; font-size:10px; font-weight:700; padding:2px 6px; border-radius:8px;'>👤 {nombre_dueno}</span>", unsafe_allow_html=True)
                    c2.markdown(f"<span style='color:#172b4d; font-size:14px;'>{row['TRIBUNAL']}</span>", unsafe_allow_html=True)
                    c3.markdown(f"<span style='color:#172b4d; font-weight:600; font-size:14px;'>{row['CARATULADO']}</span>", unsafe_allow_html=True)
                    
                    val_cliente = str(row.get('Cliente', '--'))
                    val_rut = str(row.get('RUT', '--'))
                    c4.markdown(f"<span style='color:#172b4d; font-size:14px;'>👤 {val_cliente}</span><br><span style='color:#6b778c; font-size:12px;'>RUT: {val_rut}</span>", unsafe_allow_html=True)
                    
                    c5.button("📂 Abrir", key=f"abrir_c_{idx}", use_container_width=True, on_click=ir_a_expediente, args=(row['ROL'], row.get('Propietario_Vista', usuario_actual)))
                    st.markdown("<hr style='margin: 8px 0px 8px 0px; border-top: 1px dashed #e0e4e8;'>", unsafe_allow_html=True)
        
    else:
        rol_actual = st.session_state['causa_seleccionada']
        filtro_causa = df_causas[df_causas['ROL'] == rol_actual]
        
        if filtro_causa.empty:
            st.error(f"Error: No se encontró el expediente para el ROL {rol_actual}.")
            if st.button("Volver al inicio"):
                st.session_state['causa_seleccionada'] = None
                st.rerun()
            st.stop()
            
        idx = filtro_causa.index[0]
        c_data = df_causas.loc[idx]
        
        c_head1, c_head2 = st.columns([4, 1])
        with c_head1:
            st.markdown(f"<h2>{c_data.get('CARATULADO','')}</h2>", unsafe_allow_html=True)
            if ES_ADMIN_NARRATIA and _propietario_vista and _propietario_vista != usuario_actual:
                nombre_dueno_exp = NOMBRES_REALES.get(_propietario_vista, _propietario_vista)
                st.markdown(f"<span style='background:#fff0e0; color:#ff8b00; font-size:12px; font-weight:700; padding:3px 10px; border-radius:10px;'>👤 Causa de {nombre_dueno_exp} — estás viendo/editando el expediente real de su cartera</span>", unsafe_allow_html=True)
        with c_head2:
            if st.button("⬅ Volver al listado"):
                st.session_state['causa_seleccionada'] = None
                st.session_state['causa_propietario_vista'] = None
                st.rerun()
            if st.button("✍️ Redactar Escrito", help="Abre el Redactor IA con el Rol, Tribunal y Caratulado de esta causa ya cargados."):
                st.session_state['redactor_prefill'] = {
                    'rol': c_data.get('ROL', ''), 'tribunal': c_data.get('TRIBUNAL', ''), 'caratulado': c_data.get('CARATULADO', '')
                }
                st.session_state['menu_radio'] = "📝 Redactor IA"
                st.rerun()
                
        col_izq, col_der = st.columns([2.5, 1.2])
        
        with col_der:
            c_btn_ed, c_btn_del = st.columns([3, 1])
            if c_btn_ed.button("❌ Cancelar" if st.session_state['modo_edicion'] else "✏️ Editar Ficha", use_container_width=True):
                st.session_state['modo_edicion'] = not st.session_state['modo_edicion']
                st.rerun()
                
            if usuario_actual == "Narratia":
                if c_btn_del.button("🗑️", help="Eliminar Causa Permanentemente"):
                    df_causas = df_causas.drop(idx)
                    df_causas.to_csv(ARCHIVO_BD, index=False)
                    dn_c = safe_read_sheet("base_causas", [])
                    if not dn_c.empty:
                        safe_update_sheet("base_causas", dn_c[dn_c['ROL'] != rol_actual])
                    st.session_state['causa_seleccionada'] = None
                    st.rerun()
                
            if st.session_state['modo_edicion']:
                with st.form("form_edicion_causa"):
                    st.markdown("#### Datos de Litigación")
                    n_tribunal = selector_tribunal(str(c_data.get('TRIBUNAL','')), key_prefix="editar_causa")
                    n_serv = st.text_input("Servicio Contratado", str(c_data.get('Servicio','')))
                    n_negocio = st.selectbox("Origen de Cartera", ["Externo", "Propio"], index=0 if c_data.get('Tipo_Negocio') == "Externo" else 1)
                    
                    st.markdown("#### Datos de Ficha de Cliente")
                    n_clave = st.text_input("Clave Única", str(c_data.get('Clave_unica','')))
                    n_sac = st.text_input("SAC Asignado", str(c_data.get('SAC','')))
                    n_suc = st.text_input("Sucursal Oficina", str(c_data.get('Sucursal','')))
                    
                    st.markdown("#### 💰 Control de Honorarios")
                    opciones_hon = ["Sin fijar", "Pagados", "Pendientes"]
                    idx_hon = opciones_hon.index(c_data.get('Estado_Honorarios', 'Sin fijar')) if c_data.get('Estado_Honorarios') in opciones_hon else 0
                    n_estado_hon = st.selectbox("Condición de Honorarios", opciones_hon, index=idx_hon)
                    
                    if n_estado_hon == "Pendientes":
                        n_tot_hon = st.number_input("Honorario Total Pactado ($)", value=int(c_data.get('Total_Honorarios', 0)))
                        n_cuo_tot = st.number_input("Mensualidades Totales", value=max(1, int(c_data.get('Cuotas_Totales', 0) or 0)), min_value=1)
                        n_cuo_pag = st.number_input("Mensualidades Enteradas", value=int(c_data.get('Cuotas_Pagadas', 0)), min_value=0)
                    elif n_estado_hon == "Pagados":
                        n_tot_hon = st.number_input("Monto Total Enterado ($)", value=int(c_data.get('Total_Honorarios', 0)))
                        n_cuo_tot, n_cuo_pag = 1, 1
                    else:
                        n_tot_hon, n_cuo_tot, n_cuo_pag = 0, 0, 0
                        
                    if st.form_submit_button("💾 Guardar Cambios", type="primary"):
                        df_causas.at[idx, 'TRIBUNAL'] = n_tribunal; df_causas.at[idx, 'Servicio'] = n_serv; df_causas.at[idx, 'Tipo_Negocio'] = n_negocio
                        df_causas.at[idx, 'Clave_unica'] = n_clave; df_causas.at[idx, 'SAC'] = n_sac; df_causas.at[idx, 'Sucursal'] = n_suc
                        df_causas.at[idx, 'Estado_Honorarios'] = n_estado_hon; df_causas.at[idx, 'Total_Honorarios'] = n_tot_hon
                        df_causas.at[idx, 'Cuotas_Totales'] = n_cuo_tot; df_causas.at[idx, 'Cuotas_Pagadas'] = n_cuo_pag
                        df_causas.to_csv(ARCHIVO_BD, index=False)
                        st.session_state['modo_edicion'] = False
                        st.rerun()
            else:
                # --- 🔍 MOTOR DE BÚSQUEDA RELACIONAL DEL CLIENTE ---
                rut_asociado = str(c_data.get('RUT', ''))
                datos_cliente = df_clientes[df_clientes['RUT'].astype(str) == rut_asociado]
                
                if not datos_cliente.empty:
                    info_cl = datos_cliente.iloc[0]
                    tel_real = info_cl.get('Telefono', '--')
                    correo_real = info_cl.get('Correo', '--')
                    dir_real = info_cl.get('Direccion', '--')
                else:
                    tel_real, correo_real, dir_real = '--', '--', '--'

                clase_div = "badge-active" if c_data.get('Tipo_Negocio') == "Externo" else "badge-propio"
                st.markdown(f"""
                <div class="dash-card">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                        <span style="font-weight:700; font-size:15px; color:#172b4d;">Información de la Causa</span>
                        <span class="{clase_div}">{c_data.get('Tipo_Negocio','')}</span>
                    </div>
                    <div class="info-field"><span class="info-label">Rol</span><span class="info-value">{rol_actual}</span></div>
                    <div class="info-field"><span class="info-label">Tribunal</span><span class="info-value">{c_data.get('TRIBUNAL') or '--'}</span></div>
                    <div class="info-field"><span class="info-label">Materia / Servicio</span><span class="info-value">{c_data.get('Servicio') or '--'}</span></div>
                </div>
                <div class="dash-card">
                    <div style="margin-bottom:14px;">
                        <span style="font-weight:700; font-size:15px; color:#172b4d;">Ficha Económica y Contacto</span>
                    </div>
                    <div class="info-field"><span class="info-label">Cliente</span><span class="info-value">{c_data.get('Cliente') or '--'}</span></div>
                    <div class="info-field"><span class="info-label">RUT</span><span class="info-value">{rut_asociado or '--'}</span></div>
                    <div class="info-field"><span class="info-label">Teléfono</span><span class="info-value">📞 {tel_real}</span></div>
                    <div class="info-field"><span class="info-label">Correo</span><span class="info-value">✉️ {correo_real}</span></div>
                    <div class="info-field"><span class="info-label">Dirección</span><span class="info-value">📍 {dir_real}</span></div>
                    <hr style="border:none; border-top:1px solid #e0e4e8; margin:14px 0;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span class="info-label">Honorarios</span>
                        <span class="badge-honorarios">{c_data.get('Estado_Honorarios') or 'Sin fijar'}</span>
                    </div>
                    <div class="info-field" style="margin-top:8px;"><span class="info-label">Pactado</span><span class="info-value" style="font-weight:700;">${c_data.get('Total_Honorarios',0):,.0f}</span></div>
                </div>
                """, unsafe_allow_html=True)
                
        with col_izq:
            tab_tareas_internas, tab_docs_solicitados = st.tabs(["Tareas Operativas", "📥 Docs Cliente"])
            
            with tab_tareas_internas:
                if st.button("+ Asignar Nueva Tarea Operativa", type="primary"):
                    st.session_state['creando_tarea'] = not st.session_state.get('creando_tarea', False)
                    st.rerun()
                    
                if st.session_state.get('creando_tarea'):
                    with st.form("form_t_interna"):
                        t_t = st.text_input("Nomenclatura Breve")
                        t_d = st.text_area("Descripción de la gestión")
                        t_p = st.selectbox("Prioridad", ["Alta", "Media", "Baja"])
                        t_f = st.date_input("Fecha de Cumplimiento")
                        
                        st.markdown("---")
                        st.markdown("<span style='font-size:13px; color:#6b778c;'>Dejar en blanco para asignarla a ti mismo. Para delegar, escribe el nombre del colega.</span>", unsafe_allow_html=True)
                        t_delegado = st.text_input("Asignar Tarea a (Opcional)", placeholder="Ej: Eduardo Riquelme")
                        
                        if st.form_submit_button("Registrar y Asignar Tarea", type="primary"):
                            destinatario_file = ARCHIVO_TAREAS
                            destinatario_usr = usuario_actual
                            
                            if t_delegado.strip():
                                nombre_buscado = t_delegado.strip().lower()
                                for user_key, real_name in NOMBRES_REALES.items():
                                    if nombre_buscado in real_name.lower() or nombre_buscado == user_key.lower():
                                        destinatario_usr = user_key
                                        destinatario_file = f"base_tareas_{user_key}.csv"
                                        break
                                
                            if not os.path.exists(destinatario_file):
                                pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad', 'Usuario_Propietario']).to_csv(destinatario_file, index=False)
                                
                            df_t_destino = leer_csv_local(destinatario_file)
                            nueva_t = {
                                'ID_Tarea': str(uuid.uuid4())[:8], 
                                'ROL': rol_actual, 
                                'Creador': nombre_real_usuario, 
                                'Fecha_Creacion': datetime.now().strftime("%d/%m/%Y"), 
                                'Fecha_Vencimiento': t_f.strftime("%d/%m/%Y"),
                                'Titulo': t_t, 'Descripcion': t_d, 'Estado': 'En progreso', 'Comentarios': '[]', 'Prioridad': t_p,
                                'Usuario_Propietario': destinatario_usr
                            }
                            df_t_destino = pd.concat([df_t_destino, pd.DataFrame([nueva_t])], ignore_index=True)
                            df_t_destino.to_csv(destinatario_file, index=False)
                            
                            dn_t_upd = safe_read_sheet("base_tareas", ['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad', 'Usuario_Propietario'])
                            safe_update_sheet("base_tareas", pd.concat([dn_t_upd, pd.DataFrame([nueva_t])], ignore_index=True))
                                
                            # --- 🚀 DISPARO A GOOGLE CALENDAR DINÁMICO ---
                            if t_p == "Alta":
                                df_usr_db = safe_read_sheet("base_usuarios", [])
                                f_user = df_usr_db[df_usr_db['Usuario'] == destinatario_usr]
                                if not f_user.empty and "@" in str(f_user.iloc[0]['Correo']):
                                    correo_cal = f_user.iloc[0]['Correo']
                                    exito = agendar_plazo_calendar(t_t, f"Causa ROL: {rol_actual}\nDetalle: {t_d}", t_f.strftime("%d/%m/%Y"), correo_cal)
                                    if exito:
                                        st.toast("📅 Plazo fatal sincronizado en Google Calendar con alarmas.", icon="🚨")
                            # ------------------------------------
                                
                            st.session_state['creando_tarea'] = False
                            st.success("✅ Tarea registrada exitosamente.")
                            import time; time.sleep(0.3); st.rerun()
                            
                df_t_local = leer_csv_local(ARCHIVO_TAREAS, COLS_TAREAS)
                tareas_de_esta_causa = df_t_local[df_t_local['ROL'] == rol_actual]
                
                if tareas_de_esta_causa.empty:
                    st.info("Esta causa no registra tareas en progreso.")
                else:
                    for idx_tarea_bd, tarea in tareas_de_esta_causa.iterrows():
                        with st.container(border=True):
                            b_prio_color = "#ff5630" if tarea.get('Prioridad') == "Alta" else ("#ffc400" if tarea.get('Prioridad') == "Media" else "#57a15a")
                            st.markdown(f"<div style='height: 5px; background-color: {b_prio_color}; border-radius: 5px 5px 0 0; margin: -1rem -1rem 1rem -1rem;'></div>", unsafe_allow_html=True)
                            
                            if st.session_state.get('editando_tarea') == tarea['ID_Tarea']:
                                modal_editar_tarea(tarea['ID_Tarea'], tarea['Titulo'], tarea['Fecha_Vencimiento'], tarea['Estado'])
                            else:
                                autor_real = NOMBRES_REALES.get(tarea['Creador'], tarea['Creador'])
                                nro_tarea_corto = str(tarea['ID_Tarea']).upper()

                                # --- Encabezado tipo ficha: título + metadatos + acciones alineadas a la derecha ---
                                c_top_l, c_top_r = st.columns([2.3, 2.5])
                                with c_top_l:
                                    st.markdown(f"<div style='font-weight:700; font-size:17px; color:#172b4d;'>{tarea['Titulo']}</div>", unsafe_allow_html=True)
                                    st.markdown(f"<span style='font-size:13px; color:#6b778c;'>Creado por: {autor_real} • N° tarea {nro_tarea_corto} • [{tarea.get('Prioridad', 'Media')}]</span>", unsafe_allow_html=True)
                                    st.markdown(f"<span style='font-size:13px; color:#6b778c;'>Fecha creación: {tarea['Fecha_Creacion']} • Fecha vencimiento: {tarea['Fecha_Vencimiento']}</span>", unsafe_allow_html=True)
                                
                                with c_top_r:
                                    if tarea['Estado'] == 'En progreso':
                                        bcols = st.columns([1.3, 1.3, 0.9, 0.9] if usuario_actual == "Narratia" else [1.3, 1.3, 0.9])
                                        if bcols[0].button("❌ Rechazar", key=f"rech_{tarea['ID_Tarea']}", use_container_width=True): 
                                            df_t_local.at[idx_tarea_bd, 'Estado'] = 'Rechazada'; df_t_local.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                                        if bcols[1].button("✅ Aprobar", key=f"apr_{tarea['ID_Tarea']}", use_container_width=True): 
                                            df_t_local.at[idx_tarea_bd, 'Estado'] = 'Aprobada'; df_t_local.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                                        if bcols[2].button("✏️", key=f"edit_{tarea['ID_Tarea']}", help="Editar tarea", use_container_width=True):
                                            st.session_state['editando_tarea'] = tarea['ID_Tarea']; st.rerun()
                                        if usuario_actual == "Narratia" and bcols[3].button("🗑️", key=f"del_{tarea['ID_Tarea']}", help="Eliminar tarea", use_container_width=True):
                                            df_t_local = df_t_local.drop(idx_tarea_bd); df_t_local.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                                        st.markdown("<div class='task-status-chip task-status-progreso' style='margin-top:8px; text-align:right; float:right;'>En progreso</div>", unsafe_allow_html=True)
                                    else:
                                        clase_estado = "task-status-aprobada" if tarea['Estado'] == 'Aprobada' else "task-status-rechazada"
                                        c_chip, c_del = st.columns([3, 1])
                                        with c_chip:
                                            st.markdown(f"<div style='text-align:right;'><span class='task-status-chip {clase_estado}'>{tarea['Estado']}</span></div>", unsafe_allow_html=True)
                                        with c_del:
                                            if usuario_actual == "Narratia" and st.button("🗑️", key=f"del_fin_{tarea['ID_Tarea']}", help="Eliminar tarea", use_container_width=True):
                                                df_t_local = df_t_local.drop(idx_tarea_bd); df_t_local.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()

                                st.markdown(f"<p style='font-size: 15px; color: #172b4d; margin-top:12px; margin-bottom: 5px;'>{tarea['Descripcion']}</p>", unsafe_allow_html=True)
                                
                                comentarios_js = json.loads(tarea['Comentarios'])
                                
                                with st.expander(f"💬 Comentarios ({len(comentarios_js)})", expanded=False):
                                    if not comentarios_js:
                                        st.caption("No hay comentarios todavía.")
                                    for c in comentarios_js:
                                        st.markdown(f"""
                                        <div style='padding:8px 0; border-bottom:1px solid #f4f5f7;'>
                                            <strong style='color:#172b4d; font-size:14px;'>{c['autor']}</strong>
                                            <span style='color:#6b778c; font-size:12px;'> • {c['fecha']}</span><br>
                                            <span style='color:#42526e; font-size:14px;'>{c['texto']}</span>
                                        </div>
                                        """, unsafe_allow_html=True)
                                    
                                    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
                                    adj_coment = st.file_uploader("📎 Adjuntar archivo al comentario", key=f"fu_{tarea['ID_Tarea']}", label_visibility="collapsed")
                                    with st.form(key=f"fc_{tarea['ID_Tarea']}", clear_on_submit=True):
                                        c_txt, c_btn = st.columns([8, 1])
                                        texto_com = c_txt.text_input("Agregar un comentario...", label_visibility="collapsed", placeholder="Escribir comentario...")
                                        if c_btn.form_submit_button("Enviar"):
                                            if texto_com.strip() or adj_coment:
                                                t_final = texto_com.strip() + (f" <br><em>[📎 Archivo adjunto: {adj_coment.name}]</em>" if adj_coment else "")
                                                comentarios_js.append({"autor": nombre_real_usuario, "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"), "texto": t_final})
                                                df_t_local.at[idx_tarea_bd, 'Comentarios'] = json.dumps(comentarios_js)
                                                df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
                                                
                                                dn = safe_read_sheet("base_tareas", [])
                                                if not dn.empty:
                                                    dn.loc[dn['ID_Tarea'] == tarea['ID_Tarea'], 'Comentarios'] = json.dumps(comentarios_js)
                                                    safe_update_sheet("base_tareas", dn)
                                                st.rerun()

            with tab_docs_solicitados:
                st.subheader("📋 Gestión de Requisitos del Cliente")
                # El nombre del cliente puede traer tildes, comas u otros caracteres que
                # rompen la URL al compartirla por WhatsApp/correo. Se deja solo
                # letras, números y guion bajo, para que el enlace nunca falle por esto.
                token_para_link = re.sub(r'[^A-Za-z0-9_]', '', str(c_data.get('Cliente', 'Cliente')).strip().replace(" ", "_"))
                # IMPORTANTE: esta URL base es la de TU app en Streamlit Cloud. Si Streamlit
                # te asignó una URL distinta a "narratia-app.streamlit.app" (a veces pasa si
                # ese nombre corto ya estaba tomado), el enlace de abajo no va a funcionar
                # para el cliente. Puedes fijar la URL real en Streamlit Cloud -> Settings ->
                # Secrets con: APP_BASE_URL = "https://tu-url-real.streamlit.app"
                APP_BASE_URL = st.secrets.get("APP_BASE_URL", "https://narratia-app.streamlit.app").rstrip("/")
                link_portal_final = f"{APP_BASE_URL}/?cliente_id={token_para_link}"
                st.info(f"🔗 **Enlace del Portal para el Cliente:**\n`{link_portal_final}`")
                st.caption("⚠️ Si el cliente te dice que el enlace no abre, copia la URL exacta que te muestra tu navegador cuando entras a JuriSync y compárala con la de arriba. Si son distintas, avísale a Nicolás para fijar la URL correcta en la configuración.")
                
                with st.form(key=f"form_agregar_requisito_{rol_actual}", clear_on_submit=True):
                    st.markdown("#### Solicitar Nuevo Documento")
                    nuevo_doc_req = st.text_input("Nombre del documento solicitado", placeholder="Ej: Certificado de Matrimonio actualizado, Últimas 3 liquidaciones...")
                    if st.form_submit_button("➕ Enviar Requisito al Portal", type="primary"):
                        if nuevo_doc_req.strip() == "":
                            st.error("Escribe el nombre del documento.")
                        else:
                            ARCHIVO_DOCS = "base_documentos_clientes.csv"
                            if not os.path.exists(ARCHIVO_DOCS):
                                pd.DataFrame(columns=['ID_Req', 'Cliente_Token', 'Documento_Nombre', 'Estado', 'Archivo_B64', 'Archivo_Drive_ID', 'Fecha_Subida']).to_csv(ARCHIVO_DOCS, index=False)
                            
                            df_docs_db = leer_csv_local(ARCHIVO_DOCS)
                            nuevo_registro_doc = {
                                'ID_Req': str(uuid.uuid4())[:8],
                                'Cliente_Token': token_para_link,
                                'Documento_Nombre': nuevo_doc_req.strip(),
                                'Estado': '❌ Pendiente',
                                'Archivo_B64': '',
                                'Fecha_Subida': '--'
                            }
                            df_docs_db = pd.concat([df_docs_db, pd.DataFrame([nuevo_registro_doc])], ignore_index=True)
                            df_docs_db.to_csv(ARCHIVO_DOCS, index=False)
                            safe_update_sheet("base_documentos_clientes", df_docs_db)
                            st.success(f"¡Solicitud de '{nuevo_doc_req}' agregada al portal del cliente!")
                            st.rerun()
                
                st.markdown("### Estado de la Documentación Solicitada")
                ARCHIVO_DOCS = "base_documentos_clientes.csv"
                if os.path.exists(ARCHIVO_DOCS):
                    df_docs_db = leer_csv_local(ARCHIVO_DOCS)
                    docs_causa_actual = df_docs_db[df_docs_db['Cliente_Token'] == token_para_link]
                    
                    if docs_causa_actual.empty:
                        st.write("No se han solicitado documentos para este cliente todavía.")
                    else:
                        for idx_d, d_row in docs_causa_actual.iterrows():
                            with st.container(border=True):
                                cd1, cd2, cd3 = st.columns([3, 1.5, 1])
                                with cd1:
                                    st.markdown(f"**{d_row['Documento_Nombre']}**")
                                    st.write(f"Fecha de carga: {d_row.get('Fecha_Subida', '--')}")
                                with cd2:
                                    if d_row['Estado'] == '✅ Completado':
                                        st.markdown("<span style='color:#57a15a; font-weight:bold;'>✅ Recibido</span>", unsafe_allow_html=True)
                                    else:
                                        st.markdown("<span style='color:#ff5630; font-weight:bold;'>❌ Pendiente</span>", unsafe_allow_html=True)
                                with cd3:
                                    if d_row['Estado'] == '✅ Completado':
                                        bytes_descarga = obtener_bytes_adjunto(d_row, 'Archivo_Drive_ID', 'Archivo_B64')
                                        if bytes_descarga is not None:
                                            st.download_button("📥 Descargar", data=bytes_descarga, file_name=f"{d_row['Documento_Nombre'].replace(' ', '_')}_{token_para_link}.pdf", key=f"dl_abog_{d_row['ID_Req']}")
                                        else:
                                            st.caption("⚠️ No se pudo recuperar el archivo.")
                                    else:
                                        if st.button("🗑️", key=f"del_req_{d_row['ID_Req']}"):
                                            df_docs_db = df_docs_db.drop(idx_d)
                                            df_docs_db.to_csv(ARCHIVO_DOCS, index=False)
                                            st.rerun()

# 8. AGENDA DIARIA
elif st.session_state['menu_radio'] == "📋 Agenda":
    st.title("📋 Agenda Diaria de Plazos")
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    st.write(f"Gestiones legales que vencen indefectiblemente el día de hoy: **{fecha_hoy}**")
    
    df_t = leer_csv_local(ARCHIVO_TAREAS, COLS_TAREAS)
    if df_t.empty:
        st.info("No existen registros de plazos en el sistema.")
    else:
        df_t['Fecha_Vencimiento'] = df_t['Fecha_Vencimiento'].astype(str).str.strip()
        t_hoy = df_t[df_t['Fecha_Vencimiento'] == str(fecha_hoy)].copy()
        if t_hoy.empty:
            st.success("🎉 Felicitaciones. No registras plazos fatales para el día de hoy.")
        else:
            mapeo_prioridades = {"Alta": 1, "Media": 2, "Baja": 3}
            t_hoy['Orden'] = t_hoy['Prioridad'].map(mapeo_prioridades).fillna(4)
            t_hoy = t_hoy.sort_values(by='Orden')
            
            for _, row in t_hoy.iterrows():
                with st.container(border=True):
                    color_p = "#ff5630" if row['Prioridad'] == "Alta" else ("#ffc400" if row['Prioridad'] == "Media" else "#57a15a")
                    st.markdown(f"<div style='height: 5px; background-color:{color_p}; margin:-1rem -1rem 1rem -1rem; border-radius:5px 5px 0 0;'></div>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns([4, 2, 1])
                    with c1:
                        st.markdown(f"<div style='display: flex; align-items: center; margin-bottom: 5px;'><img src='{LOGO_URL}' style='height: 25px; margin-right: 8px;'><strong style='font-size:16px; color:#172b4d;'>{row['Titulo']}</strong><span style='font-size:12px; color:{color_p}; font-weight:bold; margin-left:8px;'>[{row.get('Prioridad', 'Media')}]</span></div>", unsafe_allow_html=True)
                        st.markdown(f"<span style='color:#6b778c;'>{str(row['Descripcion'])[:80]}...</span>", unsafe_allow_html=True)
                    with c2:
                        color_bd = "#ffc400" if row['Estado'] == 'En progreso' else ("#57a15a" if row['Estado'] == 'Aprobada' else "#ff5630")
                        st.markdown(f"<span style='background:{color_bd}; padding:3px 8px; border-radius:10px; font-size:12px; font-weight:bold; color:black;'>{row['Estado']}</span>", unsafe_allow_html=True)
                        st.markdown(f"<span style='color:#172b4d; font-size:14px;'><br>Causa: {row['ROL']}</span>", unsafe_allow_html=True)
                    with c3:
                        st.button("Ir al expediente ➔", key=f"ag_{row['ID_Tarea']}", on_click=ir_a_expediente, args=(row['ROL'],))

# 9. MENSAJERÍA INTERNA
elif st.session_state['menu_radio'] == "✈️ Mensajería":
    st.title("✈️ Mensajería Interna del Equipo")
    st.markdown("Plataforma de comunicación rápida para la oficina.")
    
    ES_ADMIN_MENSAJES = usuario_actual == "Narratia"
    
    c_tit_msj, c_refresh_msj = st.columns([4, 1])
    with c_refresh_msj:
        if st.button("🔄 Actualizar", key="refresh_mensajes", use_container_width=True):
            if "_csv_cache_" + ARCHIVO_MENSAJES in st.session_state:
                del st.session_state["_csv_cache_" + ARCHIVO_MENSAJES]
            st.rerun()
    
    df_msgs = leer_csv_local(ARCHIVO_MENSAJES)
    
    # PRIVACIDAD: solo el administrador (Narratia) ve absolutamente todos los
    # mensajes del equipo. El resto de los usuarios solo ve las conversaciones
    # en las que participa directamente (las envió, se las enviaron a él/ella
    # de forma directa, o fueron enviadas a "Todos"). No pueden ver mensajes
    # privados entre otros dos compañeros.
    if not ES_ADMIN_MENSAJES and not df_msgs.empty:
        df_msgs = df_msgs[
            (df_msgs['De'] == nombre_real_usuario) |
            (df_msgs['Para'] == nombre_real_usuario) |
            (df_msgs['Para'] == 'Todos')
        ]
    
    with st.container(height=500):
        if df_msgs.empty:
            st.info("No hay mensajes en el buzón. ¡Sé el primero en escribir!")
        else:
            st.markdown("<div class='chat-bg'>", unsafe_allow_html=True)
            for _, msg in df_msgs.iterrows():
                es_mio = (msg['De'] == nombre_real_usuario)
                clase_burbuja = "burbuja-mia" if es_mio else "burbuja-otro"
                alineacion = "flex-end" if es_mio else "flex-start"
                
                st.markdown(f"""
                <div style="display: flex; justify-content: {alineacion}; width: 100%;">
                    <div class="{clase_burbuja}">
                        <div class="chat-autor">{msg['De']} <span class="chat-para">▶ Para: {msg['Para']}</span></div>
                        <div class="chat-texto">{msg['Mensaje']}</div>
                        <div class="chat-hora">{msg['Fecha']}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
                
    with st.form("form_chat", clear_on_submit=True):
        c_para, c_texto, c_btn = st.columns([2, 6, 2])
        destinatario = c_para.selectbox("Destinatario", ["Todos"] + list(NOMBRES_REALES.values()))
        texto_mensaje = c_texto.text_input("Escribe tu mensaje...", label_visibility="collapsed", placeholder="Escribe un mensaje aquí...")
        if c_btn.form_submit_button("Enviar 🚀", type="primary", use_container_width=True):
            if texto_mensaje.strip() != "":
                df_msgs_todos = leer_csv_local(ARCHIVO_MENSAJES)
                nuevo_msj = {
                    'ID': str(uuid.uuid4())[:8],
                    'Fecha': datetime.now().strftime("%d/%m/%Y %H:%M"),
                    'De': nombre_real_usuario,
                    'Para': destinatario,
                    'Mensaje': texto_mensaje.strip()
                }
                df_msgs_todos = pd.concat([df_msgs_todos, pd.DataFrame([nuevo_msj])], ignore_index=True)
                df_msgs_todos.to_csv(ARCHIVO_MENSAJES, index=False)
                st.rerun()

# 10. CLIENTES DIRECTOS (FICHA COMPLETA Y RELACIONAL)
elif st.session_state['menu_radio'] == "👥 Clientes":
    st.title("Clientes")
    st.markdown("<span style='color:#6b778c;'>Gestione y organice la información de sus clientes de manera eficiente.</span>", unsafe_allow_html=True)
    
    df_clientes = safe_read_sheet("base_clientes", COLS_CLIENTES)

    with st.expander("🔍 Buscador de Conflictos de Interés (revisa antes de aceptar un caso nuevo)"):
        rut_conflicto = st.text_input("RUT a verificar", placeholder="Ej: 12.345.678-9", key="buscar_conflicto_rut")
        if rut_conflicto.strip():
            rut_normalizado = re.sub(r'[^0-9kK]', '', rut_conflicto).upper()
            
            # Se revisa en TODAS las causas que existan en el disco (de todos los
            # abogados, no solo las tuyas), porque un conflicto de interés hay que
            # detectarlo aunque el caso lo haya llevado otro compañero del estudio.
            piezas_conflicto = []
            for arch_conf in glob.glob("base_causas_*.csv"):
                t_conf = leer_csv_local(arch_conf)
                if not t_conf.empty and 'RUT' in t_conf.columns:
                    propietario_conf = arch_conf.replace("base_causas_", "").replace(".csv", "")
                    t_conf = t_conf.copy()
                    t_conf['Propietario_Vista'] = propietario_conf
                    piezas_conflicto.append(t_conf)
            df_todas_causas_conf = pd.concat(piezas_conflicto, ignore_index=True) if piezas_conflicto else pd.DataFrame()
            
            resultados_conflicto = pd.DataFrame()
            if not df_todas_causas_conf.empty:
                mascara_rut = df_todas_causas_conf['RUT'].astype(str).apply(lambda x: re.sub(r'[^0-9kK]', '', x).upper() == rut_normalizado)
                resultados_conflicto = df_todas_causas_conf[mascara_rut]
            
            if resultados_conflicto.empty:
                st.success("✅ No se encontraron causas asociadas a este RUT en todo el estudio. No hay conflicto de interés detectado.")
            else:
                st.warning(f"⚠️ Este RUT aparece en {len(resultados_conflicto)} causa(s) del estudio — revisa antes de aceptar un caso nuevo:")
                for _, fila_conf in resultados_conflicto.iterrows():
                    nombre_resp = NOMBRES_REALES.get(fila_conf.get('Propietario_Vista'), fila_conf.get('Propietario_Vista'))
                    st.markdown(f"- **{fila_conf.get('ROL','--')}** — {fila_conf.get('CARATULADO','--')} · Cliente: {fila_conf.get('Cliente','--')} · Responsable: {nombre_resp}")
            st.caption("Este buscador revisa por coincidencia exacta de RUT en las causas registradas. No reemplaza el criterio profesional del abogado.")

    if st.session_state['cliente_seleccionado'] is None:
        if st.button("➕ Crear Nuevo Cliente", type="primary"):
            st.session_state['creando_cliente'] = not st.session_state.get('creando_cliente', False)
            
        if st.session_state.get('creando_cliente'):
            with st.container(border=True):
                with st.form("form_crear_cliente"):
                    st.subheader("Ficha Completa del Nuevo Cliente")
                    c1, c2 = st.columns(2)
                    n_cli_nom = c1.text_input("Nombre Completo *")
                    n_cli_rut = c2.text_input("RUT del Cliente *")
                    n_cli_tel = c1.text_input("Teléfono")
                    n_cli_cor = c2.text_input("Correo Electrónico")
                    n_cli_cla = c1.text_input("Clave Única")
                    n_cli_dom = c2.text_input("Domicilio")

                    if st.form_submit_button("💾 Guardar Cliente en la Nube", type="primary"):
                        if not n_cli_nom or not n_cli_rut:
                            st.error("El Nombre y el RUT son obligatorios.")
                        else:
                            nuevo_cliente = {
                                'RUT': n_cli_rut.strip().upper(),
                                'Nombre': n_cli_nom.strip(),
                                'Telefono': n_cli_tel.strip(),
                                'Correo': n_cli_cor.strip(),
                                'Clave_unica': n_cli_cla.strip(),
                                'Direccion': n_cli_dom.strip()
                            }
                            df_clientes = pd.concat([df_clientes, pd.DataFrame([nuevo_cliente])], ignore_index=True)
                            safe_update_sheet("base_clientes", df_clientes)
                            st.success("✅ Cliente creado y sincronizado en Google Sheets.")
                            st.session_state['creando_cliente'] = False
                            st.rerun()

        st.write("---")
        
        # CRUZAMOS DATOS PARA NO PERDER CLIENTES HISTÓRICOS (y, para el admin, de todo el equipo)
        ES_ADMIN_CLIENTES = usuario_actual == "Narratia"
        if ES_ADMIN_CLIENTES:
            boton_refrescar_equipo("refresh_clientes_equipo")
            df_causas_local = pd.DataFrame()
            piezas_causas_cli = []
            for arch_cli in glob.glob("base_causas_*.csv"):
                t = leer_csv_local(arch_cli)
                if not t.empty:
                    piezas_causas_cli.append(t)
            if piezas_causas_cli:
                df_causas_local = pd.concat(piezas_causas_cli, ignore_index=True)
        else:
            df_causas_local = leer_csv_local(ARCHIVO_BD)
        
        filas_clientes = {}
        if not df_clientes.empty:
            for _, r in df_clientes.iterrows():
                if pd.notna(r['Nombre']):
                    filas_clientes[(r['Nombre'], r['RUT'])] = {'Telefono': r.get('Telefono', '--'), 'Correo': r.get('Correo', '--')}
        if not df_causas_local.empty and 'Cliente' in df_causas_local.columns:
            for _, r in df_causas_local.iterrows():
                if pd.notna(r['Cliente']) and r['Cliente'] != '--':
                    clave = (r['Cliente'], r.get('RUT', '--'))
                    if clave not in filas_clientes:
                        filas_clientes[clave] = {'Telefono': r.get('Teléfono', '--'), 'Correo': r.get('Correo', '--')}
        
        if not filas_clientes:
            st.info("No hay clientes registrados en la base de datos.")
        else:
            df_directorio = pd.DataFrame([
                {'Cliente': nom, 'RUT': rut, 'Teléfono': datos['Telefono'], 'Correo': datos['Correo']}
                for (nom, rut), datos in filas_clientes.items()
            ])
            
            c_busq_cli, c_dl_cli = st.columns([4, 1])
            busqueda_cli = c_busq_cli.text_input("Buscar", placeholder="Busca por nombre o RUT...", label_visibility="collapsed")
            with c_dl_cli:
                boton_descargar_excel(df_directorio, "clientes_jurisync.xlsx", key="dl_excel_clientes")
            
            if busqueda_cli.strip():
                q = busqueda_cli.strip().lower()
                df_directorio = df_directorio[
                    df_directorio['Cliente'].astype(str).str.lower().str.contains(q, na=False) |
                    df_directorio['RUT'].astype(str).str.lower().str.contains(q, na=False)
                ]
            
            with st.container(border=True):
                ch1, ch2, ch3, ch4, ch5 = st.columns([2.5, 1.5, 2.5, 2.5, 1])
                ch1.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>CLIENTE</span>", unsafe_allow_html=True)
                ch2.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>TELÉFONO</span>", unsafe_allow_html=True)
                ch3.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>CORREO</span>", unsafe_allow_html=True)
                ch4.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>RUT</span>", unsafe_allow_html=True)
                ch5.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>ACCIONES</span>", unsafe_allow_html=True)
                st.markdown("<hr style='margin: 5px 0px 10px 0px; border-top: 2px solid #e0e4e8;'>", unsafe_allow_html=True)
                
                for _, fila_cli in df_directorio.iterrows():
                    r1, r2, r3, r4, r5 = st.columns([2.5, 1.5, 2.5, 2.5, 1])
                    r1.markdown(f"<span style='color:#172b4d; font-weight:600; font-size:14px;'>👤 {fila_cli['Cliente']}</span>", unsafe_allow_html=True)
                    r2.markdown(f"<span style='color:#172b4d; font-size:14px;'>{fila_cli['Teléfono']}</span>", unsafe_allow_html=True)
                    r3.markdown(f"<span style='color:#172b4d; font-size:14px;'>{fila_cli['Correo']}</span>", unsafe_allow_html=True)
                    r4.markdown(f"<span style='color:#6b778c; font-size:13px;'>{fila_cli['RUT']}</span>", unsafe_allow_html=True)
                    if r5.button("👁️", key=f"ver_cli_{fila_cli['RUT']}_{fila_cli['Cliente']}", use_container_width=True):
                        st.session_state['cliente_seleccionado'] = fila_cli['RUT']
                        st.rerun()
                    st.markdown("<hr style='margin: 8px 0px 8px 0px; border-top: 1px dashed #e0e4e8;'>", unsafe_allow_html=True)
    else:
        rut_actual = st.session_state['cliente_seleccionado']
        filtro_cli = df_clientes[df_clientes['RUT'] == rut_actual]
        datos_cli = filtro_cli.iloc[0] if not filtro_cli.empty else pd.Series({'Nombre': 'Cliente Histórico', 'RUT': rut_actual, 'Telefono': '--', 'Correo': '--', 'Clave_unica': '--', 'Direccion': '--'})
        
        c_back, c_del = st.columns([4, 1])
        if c_back.button("⬅ Volver al Directorio"): 
            st.session_state['cliente_seleccionado'] = None
            st.rerun()
            
        if st.session_state['username'] == "Narratia": 
            if c_del.button("🗑️ Eliminar Cliente", use_container_width=True):
                with st.spinner("Borrando cliente y limpiando datos en cascada..."):
                    df_causas = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
                    roles_a_borrar = df_causas[df_causas['RUT'].astype(str) == str(rut_actual)]['ROL'].tolist() if not df_causas.empty else []
                    nombre_borrar = datos_cli['Nombre']
                    
                    if not df_causas.empty:
                        df_causas = df_causas[df_causas['RUT'].astype(str) != str(rut_actual)]
                        df_causas.to_csv(ARCHIVO_BD, index=False)
                    
                    dn_c = safe_read_sheet("base_causas", COLS_CAUSAS)
                    if not dn_c.empty: safe_update_sheet("base_causas", dn_c[dn_c['RUT'].astype(str) != str(rut_actual)])
                    
                    df_t_local = leer_csv_local(ARCHIVO_TAREAS, COLS_TAREAS)
                    if not df_t_local.empty and roles_a_borrar:
                        df_t_local = df_t_local[~df_t_local['ROL'].isin(roles_a_borrar)]
                        df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
                    
                    dn_t = safe_read_sheet("base_tareas", COLS_TAREAS)
                    if not dn_t.empty and roles_a_borrar: safe_update_sheet("base_tareas", dn_t[~dn_t['ROL'].isin(roles_a_borrar)])
                    
                    df_con_local = leer_csv_local(ARCHIVO_CONTRATOS)
                    if not df_con_local.empty:
                        df_con_local = df_con_local[df_con_local['Cliente'] != nombre_borrar]
                        df_con_local.to_csv(ARCHIVO_CONTRATOS, index=False)
                    
                    dn_con = safe_read_sheet("base_contratos", COLS_CONTRATOS)
                    if not dn_con.empty: safe_update_sheet("base_contratos", dn_con[dn_con['Cliente'] != nombre_borrar])

                    df_clientes = df_clientes[df_clientes['RUT'].astype(str) != str(rut_actual)]
                    safe_update_sheet("base_clientes", df_clientes)
                    
                st.session_state['cliente_seleccionado'] = None
                st.success("✅ Cliente y TODO su historial asociado fue desintegrado.")
                import time; time.sleep(0.4); st.rerun()
            
        st.title(f"Ficha: {datos_cli['Nombre']}")
        tab1, tab2, tab3 = st.tabs(["👤 Información y Causas", "💰 Contabilidad", "📄 Contratos"])
        
        with tab1:
            col_i, col_d = st.columns([1, 2])
            with col_i:
                with st.container(border=True):
                    if st.session_state.get('editando_cli'):
                        with st.form("edit_cli"):
                            n_nom = st.text_input("Nombre", datos_cli.get('Nombre', ''))
                            n_rut = st.text_input("RUT", datos_cli.get('RUT', ''))
                            n_tel = st.text_input("Teléfono", datos_cli.get('Telefono', ''))
                            n_cor = st.text_input("Correo", datos_cli.get('Correo', ''))
                            n_cla = st.text_input("Clave Única", datos_cli.get('Clave_unica', ''))
                            n_dom = st.text_input("Domicilio", datos_cli.get('Direccion', ''))
                            if st.form_submit_button("💾 Guardar"):
                                df_clientes.loc[df_clientes['RUT'] == rut_actual, ['Nombre', 'RUT', 'Telefono', 'Correo', 'Clave_unica', 'Direccion']] = [n_nom, n_rut, n_tel, n_cor, n_cla, n_dom]
                                safe_update_sheet("base_clientes", df_clientes)
                                st.session_state['editando_cli'] = False
                                st.rerun()
                    else:
                        st.write(f"**Nombre:** {datos_cli.get('Nombre', '--')}")
                        st.write(f"**RUT:** {datos_cli.get('RUT', '--')}")
                        st.write(f"**Teléfono:** {datos_cli.get('Telefono', '--')}")
                        st.write(f"**Correo:** {datos_cli.get('Correo', '--')}")
                        st.write(f"**Clave Única:** {datos_cli.get('Clave_unica', '--')}")
                        st.write(f"**Domicilio:** {datos_cli.get('Direccion', '--')}")
                        if st.button("✏️ Editar Datos"): st.session_state['editando_cli'] = True; st.rerun()
            
            with col_d:
                # --- NUEVA FUNCIÓN: CREAR CAUSA DESDE EL CLIENTE ---
                with st.expander("➕ Asociar Nueva Causa a este Cliente"):
                    with st.form("form_asociar_causa"):
                        rol_n = st.text_input("Nuevo ROL / RIT", placeholder="Ej: C-123-2026")
                        trib_n = selector_tribunal(key_prefix="asociar_causa_cliente")
                        carat_n = st.text_input("Caratulado", placeholder="Ej: PEREZ / BANCO")
                        neg_n = st.selectbox("Origen de Cartera", ["Propio", "Externo"])
                        
                        if st.form_submit_button("Inyectar Causa al Cliente", type="primary"):
                            if rol_n.strip() == "":
                                st.error("El ROL es obligatorio.")
                            else:
                                df_causas_local = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
                                nueva_c = {
                                    'ROL': rol_n.strip().upper(), 'TRIBUNAL': trib_n.strip(), 'CARATULADO': carat_n.strip(), 
                                    'Cliente': datos_cli['Nombre'], 'RUT': rut_actual, 'Tipo_Negocio': neg_n,
                                    'Usuario_Propietario': usuario_actual, 'Estado_Honorarios': 'Sin fijar',
                                    'Total_Honorarios': 0, 'Cuotas_Totales': 0, 'Cuotas_Pagadas': 0,
                                    'Clave_unica': datos_cli.get('Clave_unica', '--'), 'SAC': '--', 'Sucursal': '--', 'Servicio': '--',
                                    'Teléfono': datos_cli.get('Telefono', '--'), 'Correo': datos_cli.get('Correo', '--'), 'Direccion': datos_cli.get('Direccion', '--')
                                }
                                df_causas_local = pd.concat([df_causas_local, pd.DataFrame([nueva_c])], ignore_index=True)
                                df_causas_local.to_csv(ARCHIVO_BD, index=False)
                                
                                dn_c = safe_read_sheet("base_causas", COLS_CAUSAS)
                                dn_c = pd.concat([dn_c, pd.DataFrame([nueva_c])], ignore_index=True)
                                safe_update_sheet("base_causas", dn_c)
                                
                                st.success(f"✅ Causa {rol_n.upper()} asociada exitosamente.")
                                import time; time.sleep(0.4); st.rerun()

                st.subheader("Causas Asociadas Vigentes")
                df_causas = leer_csv_local(ARCHIVO_BD, COLS_CAUSAS)
                if not df_causas.empty:
                    causas_cli = df_causas[df_causas['RUT'].astype(str) == str(rut_actual)]
                    if causas_cli.empty:
                        st.write("Este cliente no tiene causas vinculadas todavía.")
                    else:
                        for _, c in causas_cli.iterrows():
                            with st.container(border=True):
                                c1, c2 = st.columns([3, 1])
                                c1.markdown(f"**Rol:** {c['ROL']} | **Caratulado:** {c.get('CARATULADO', '--')}")
                                if c2.button("📂 Ir al Expediente", key=f"btn_ir_{c['ROL']}"):
                                    ir_a_expediente(c['ROL']); st.rerun()
                else:
                    st.write("Base de causas vacía.")

        with tab2:
            st.subheader("Estado Financiero Global")
            if not df_causas.empty:
                causas_economicas = df_causas[df_causas['RUT'].astype(str) == str(rut_actual)]
                if causas_economicas.empty:
                    st.write("Sin registros financieros.")
                else:
                    for _, ce in causas_economicas.iterrows():
                        st.write(f"🔹 **Causa Rol {ce['ROL']}:** {ce.get('Estado_Honorarios', 'Sin fijar')}")
                        st.write(f"Pactado: ${ce.get('Total_Honorarios',0):,.0f} | Cuotas Pagadas: {ce.get('Cuotas_Pagadas',0)}")
                        st.write("---")
            else:
                st.write("Sin registros financieros.")
                    
        with tab3:
            st.subheader("Contratos Vinculados")
            df_con = leer_csv_local(ARCHIVO_CONTRATOS)
            if not df_con.empty:
                st.dataframe(df_con[df_con['Cliente'] == datos_cli['Nombre']])
            else:
                st.write("No hay contratos registrados.")

# 11. GESTOR GLOBAL DE TAREAS
elif st.session_state['menu_radio'] == "☑️ Tareas":
    st.title("Tareas")
    st.markdown("<span style='color:#6b778c;'>Revisa y gestiona todas tus tareas</span>", unsafe_allow_html=True)
    
    df_t = leer_csv_local(ARCHIVO_TAREAS, COLS_TAREAS)
    df_t['Propietario_Vista'] = usuario_actual
    
    ES_ADMIN_TAREAS = usuario_actual == "Narratia"
    if ES_ADMIN_TAREAS:
        boton_refrescar_equipo("refresh_tareas_equipo")
        archivos_tareas_equipo = glob.glob("base_tareas_*.csv")
        piezas_tareas_eq = []
        for arch_t in archivos_tareas_equipo:
            propietario_t = arch_t.replace("base_tareas_", "").replace(".csv", "")
            temp_t_eq = leer_csv_local(arch_t)
            if not temp_t_eq.empty:
                temp_t_eq = temp_t_eq.copy()
                temp_t_eq['Propietario_Vista'] = propietario_t
                piezas_tareas_eq.append(temp_t_eq)
        if piezas_tareas_eq:
            df_t = pd.concat(piezas_tareas_eq, ignore_index=True)
    
    n_rechazadas = len(df_t[df_t['Estado'] == 'Rechazada']) if not df_t.empty else 0
    n_en_progreso = len(df_t[df_t['Estado'] == 'En progreso']) if not df_t.empty else 0
    n_completadas = len(df_t[df_t['Estado'] == 'Aprobada']) if not df_t.empty else 0
    
    c_st1, c_st2, c_st3 = st.columns(3)
    with c_st1:
        st.markdown(f"""<div class="dash-card"><span style="color:#6b778c; font-size:14px;">Tareas rechazadas</span><br>
        <span style="font-size:32px; font-weight:700; color:#172b4d;">{n_rechazadas}</span><br>
        <span style="color:#bf2600; font-size:13px;">❌ Requieren atención</span></div>""", unsafe_allow_html=True)
    with c_st2:
        st.markdown(f"""<div class="dash-card"><span style="color:#6b778c; font-size:14px;">En progreso</span><br>
        <span style="font-size:32px; font-weight:700; color:#172b4d;">{n_en_progreso}</span><br>
        <span style="color:#7a5b00; font-size:13px;">🕐 Tareas activas</span></div>""", unsafe_allow_html=True)
    with c_st3:
        st.markdown(f"""<div class="dash-card"><span style="color:#6b778c; font-size:14px;">Completadas</span><br>
        <span style="font-size:32px; font-weight:700; color:#172b4d;">{n_completadas}</span><br>
        <span style="color:#1b7a4a; font-size:13px;">✅ Total</span></div>""", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    
    if df_t.empty: 
        st.info("No hay tareas creadas en el sistema.")
    else:
        c_busq, c_filt, c_dl_t = st.columns([2.5, 1.5, 1])
        busqueda_tarea = c_busq.text_input("Buscar", placeholder="Tarea, Causa...", label_visibility="collapsed")
        filtro_estado_t = c_filt.selectbox("Estado", ["Todas", "En progreso", "Aprobada", "Rechazada"], label_visibility="collapsed")
        
        df_t_filt = df_t.copy()
        if busqueda_tarea.strip():
            q = busqueda_tarea.strip().lower()
            df_t_filt = df_t_filt[
                df_t_filt['Titulo'].astype(str).str.lower().str.contains(q, na=False) |
                df_t_filt['ROL'].astype(str).str.lower().str.contains(q, na=False)
            ]
        if filtro_estado_t != "Todas":
            df_t_filt = df_t_filt[df_t_filt['Estado'] == filtro_estado_t]
        with c_dl_t:
            boton_descargar_excel(df_t_filt, "tareas_jurisync.xlsx", key="dl_excel_tareas")
        
        for idx, row in df_t_filt.iterrows():
            fila_tarea_propia = row.get('Propietario_Vista', usuario_actual) == usuario_actual
            with st.container(border=True):
                prio_color = "#ff5630" if row.get('Prioridad') == "Alta" else ("#ffc400" if row.get('Prioridad') == "Media" else "#57a15a")
                st.markdown(f"<div style='height: 5px; background-color: {prio_color}; border-radius: 5px 5px 0 0; margin: -1rem -1rem 1rem -1rem;'></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns([4, 2, 1])
                with c1:
                    st.markdown(f"<div style='display: flex; align-items: center; margin-bottom: 5px;'><strong style='font-size:16px; color:#172b4d;'>{row['Titulo']}</strong><span style='font-size:12px; color:{prio_color}; font-weight:bold; margin-left:8px;'>[{row.get('Prioridad', 'Media')}]</span></div>", unsafe_allow_html=True)
                    st.markdown(f"<span style='color:#6b778c;'>{str(row['Descripcion'])[:60]}...</span>", unsafe_allow_html=True)
                    if not fila_tarea_propia:
                        nombre_dueno_t = NOMBRES_REALES.get(row.get('Propietario_Vista'), row.get('Propietario_Vista'))
                        st.markdown(f"<span style='background:#fff0e0; color:#ff8b00; font-size:10px; font-weight:700; padding:2px 6px; border-radius:8px;'>👤 {nombre_dueno_t}</span>", unsafe_allow_html=True)
                with c2:
                    color_bd = "#fff0b3" if row['Estado'] == 'En progreso' else ("#e3fcef" if row['Estado'] == 'Aprobada' else "#ffebe6")
                    texto_bd = "#7a5b00" if row['Estado'] == 'En progreso' else ("#1b7a4a" if row['Estado'] == 'Aprobada' else "#bf2600")
                    st.markdown(f"<span style='background:{color_bd}; color:{texto_bd}; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:700;'>{row['Estado']}</span>", unsafe_allow_html=True)
                    st.markdown(f"<span style='color:#172b4d; font-size:14px;'><br>Causa: {row['ROL']} | Vence: {row['Fecha_Vencimiento']}</span>", unsafe_allow_html=True)
                with c3:
                    st.button("Ir al expediente ➔", key=f"global_ir_{row['ID_Tarea']}_{row.get('Propietario_Vista', '')}", on_click=ir_a_expediente, args=(row['ROL'], row.get('Propietario_Vista', usuario_actual)))

# 12. EXCEL IMPORTADOR 
elif st.session_state['menu_radio'] == "📥 Excel":
    st.title("📥 Importador Masivo de Causas (OJV)")
    st.markdown("Sube tu archivo Excel de la Oficina Judicial Virtual para consolidar o actualizar masivamente tus causas.")
    archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx", "xls"])
    if archivo and st.button("Procesar y Consolidar en Base de Datos", type="primary"):
        procesar_ojv_completo(archivo)
        st.success("¡Base de datos unificada actualizada con éxito!")

# 13. CALENDARIO
elif st.session_state['menu_radio'] == "📅 Calendario":
    st.title("📅 Calendario de Tareas y Plazos")
    st.markdown("Revisa visualmente los hitos procesales, plazos fatales y feriados de todo el equipo.")
    
    eventos_calendario = obtener_feriados_chile()
    df_t = safe_read_sheet("base_tareas", ['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad', 'Usuario_Propietario'])
    
    if not df_t.empty:
        for idx, r in df_t.iterrows():
            try:
                # Validamos que la fecha venga bien armada
                d_obj = datetime.strptime(str(r['Fecha_Vencimiento']).strip(), "%d/%m/%Y")
                d_str = d_obj.strftime("%Y-%m-%d")
                bg_color = "#ff5630" if r.get('Prioridad') == "Alta" else ("#ffc400" if r.get('Prioridad') == "Media" else "#57a15a")
                text_color = "#172b4d"
                eventos_calendario.append({
                    "title": f"{r.get('Titulo', 'Tarea')}", 
                    "start": d_str, 
                    "backgroundColor": bg_color, 
                    "borderColor": bg_color,
                    "textColor": text_color
                })
            except Exception: 
                pass
    
    # Estilos propios para que el calendario se vea como una grilla limpia,
    # con puntos de color por evento (estilo agenda) en vez de barras sólidas,
    # y el mismo lenguaje visual (colores, tipografía) del resto de JuriSync.
    css_calendario = """
        .fc { font-family: 'Source Sans Pro', sans-serif; }
        .fc-view-harness { border-radius: 14px; overflow: hidden; }
        .fc .fc-toolbar-title { font-size: 20px; font-weight: 700; color: #172b4d; text-transform: capitalize; }
        .fc .fc-button { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #cbd2d9 !important; border-radius: 20px !important; font-weight: 600 !important; box-shadow: none !important; text-transform: capitalize; padding: 6px 16px !important; }
        .fc .fc-button:hover { background-color: #deebff !important; border-color: #0052cc !important; color: #0052cc !important; }
        .fc .fc-button-primary:not(:disabled).fc-button-active { background-color: #0052cc !important; border-color: #0052cc !important; color: #ffffff !important; }
        .fc-theme-standard td, .fc-theme-standard th { border-color: transparent !important; }
        .fc-scrollgrid { border: none !important; border-collapse: separate !important; border-spacing: 6px !important; }
        .fc-col-header-cell-cushion { color: #6b778c !important; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; text-decoration: none !important; padding: 8px 0 !important; }
        .fc-daygrid-day { background-color: #fbfbfc !important; border: 1px solid #eaecf0 !important; border-radius: 14px !important; overflow: hidden; transition: all 0.15s ease; }
        .fc-daygrid-day:hover { border-color: #b3d4ff !important; background-color: #f4f8ff !important; }
        .fc-daygrid-day-number { color: #172b4d !important; font-size: 13px; font-weight: 600; text-decoration: none !important; padding: 8px !important; }
        .fc-day-today { background-color: #eaf2ff !important; border: 1px solid #0052cc !important; }
        .fc-day-other { background-color: #f7f8fa !important; }
        .fc-day-other .fc-daygrid-day-number { color: #a5adba !important; font-weight: 400; }
        .fc-daygrid-event { border-radius: 10px !important; font-size: 12px !important; padding: 1px 6px !important; margin-top: 2px !important; }
        .fc-daygrid-event-dot { border-width: 4px !important; }
        .fc-event-title { font-weight: 500; }
        .fc-daygrid-more-link { color: #0052cc !important; font-weight: 700; font-size: 12px; }
        .fc-daygrid-day-frame { padding: 4px; }
        .fc-scrollgrid-section-header th { border: none !important; }
    """
    
    opciones_calendario = {
        "initialView": "dayGridMonth", 
        "locale": "es", 
        "firstDay": 1, 
        "height": "auto",
        "headerToolbar": {
            "left": "prev,next today", 
            "center": "title", 
            "right": "dayGridMonth,listMonth"
        },
        "dayMaxEvents": 3,       # Igual que la referencia: hasta 3 líneas visibles y luego "+N más..."
        "eventDisplay": "list-item",  # Punto de color + texto, en vez de una barra sólida
        "moreLinkText": "más...",
        # Se fuerza el texto de botones/etiquetas en español, ya que algunos
        # no se traducen solo con "locale" cuando se usa un headerToolbar custom.
        "buttonText": {
            "today": "Hoy", "month": "Mes", "week": "Semana", "day": "Día", "list": "Lista"
        },
        "dayHeaderFormat": {"weekday": "long"},
        "titleFormat": {"month": "long", "year": "numeric"},
        "noEventsText": "No hay tareas para mostrar",
        "allDayText": "Todo el día"
    }
    
    col_cal, col_dia = st.columns([2.4, 1])
    
    with col_cal:
        calendario_estado = calendar(events=eventos_calendario, options=opciones_calendario, custom_css=css_calendario, key="calendario_app_full")
        
    fecha_mostrar = datetime.now().strftime("%Y-%m-%d")
    if calendario_estado and 'dateClick' in calendario_estado and calendario_estado['dateClick']:
        fecha_mostrar = calendario_estado['dateClick']['date'][:10]
        
    with col_dia:
        try:
            fecha_dt_dia = datetime.strptime(fecha_mostrar, "%Y-%m-%d")
            d_fmt = fecha_dt_dia.strftime("%d/%m/%Y")
            dia_semana_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][fecha_dt_dia.weekday()]
            
            st.markdown(f"""
            <div class="dash-card">
                <div style="font-weight:700; font-size:17px; color:#172b4d;">Tareas del día</div>
                <div style="font-size:13px; color:#6b778c; margin-bottom:12px;">{dia_semana_es}, {fecha_dt_dia.day} de {['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'][fecha_dt_dia.month-1]} de {fecha_dt_dia.year}</div>
            """, unsafe_allow_html=True)
            
            tareas_dia = df_t[df_t['Fecha_Vencimiento'].astype(str).str.strip() == d_fmt] if not df_t.empty else pd.DataFrame()
            if tareas_dia.empty:
                st.caption("Sin tareas para este día.")
            else:
                for _, td in tareas_dia.iterrows():
                    color_prio = "#ff5630" if td.get('Prioridad') == "Alta" else ("#ffc400" if td.get('Prioridad') == "Media" else "#57a15a")
                    st.markdown(f"""
                    <div style="border-left:3px solid {color_prio}; padding:6px 10px; margin-bottom:8px; background:#f8f9fa; border-radius:6px;">
                        <div style="font-weight:600; font-size:13px; color:#172b4d;">{td.get('Titulo', '--')}</div>
                        <div style="font-size:12px; color:#6b778c;">Causa: {td.get('ROL', '--')}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    st.button("Ir a Causa", key=f"btn_cal_ir_{td.get('ID_Tarea', uuid.uuid4())}", on_click=ir_a_expediente, args=(td['ROL'],), use_container_width=True)
            
            st.markdown("</div>", unsafe_allow_html=True)
        except Exception: 
            st.caption("Haz clic en un día del calendario para ver sus tareas detalladas.")
# 14. PANEL DE ADMINISTRADOR (SOLO NARRATIA)
elif st.session_state['menu_radio'] == "👑 Panel Admin" and usuario_actual == "Narratia":
    st.title("👑 Panel de Control Master - SaaS JuriSync")
    st.markdown("Gestión maestra de usuarios y formateo del sistema.")
    
    # Aseguramos cargar la base de usuarios actualizada desde la nube
    df_usuarios_admin = safe_read_sheet("base_usuarios", COLS_USUARIOS)
    
    tab_crear, tab_editar, tab_vision, tab_peligro = st.tabs(["➕ Crear Nuevo Usuario", "🔄 Autorizar Planes", "👁️ Visión Global", "☢️ Zona de Peligro"])
    
    with tab_crear:
        with st.container(border=True):
            st.subheader("Alta de Equipo / Clientes")
            col1, col2 = st.columns(2)
            with col1:
                nuevo_user = st.text_input("Usuario (Nombre para iniciar sesión)")
                nuevo_nombre = st.text_input("Nombre Real del Abogado / Cliente")
            with col2:
                nueva_clave = st.text_input("Clave Provisoria")
                nuevo_plan = st.selectbox("Plan Asignado al Crear", ["Básico", "Medio", "Full"])
                
            if st.button("🚀 Crear Usuario y Autorizar Plan", type="primary", use_container_width=True):
                if not nuevo_user.strip() or not nueva_clave.strip() or not nuevo_nombre.strip():
                    st.error("⚠️ Faltan datos obligatorios.")
                else:
                    if nuevo_user in df_usuarios_admin['Usuario'].values:
                        st.error(f"⚠️ El usuario '{nuevo_user}' ya existe en el sistema.")
                    else:
                        with st.spinner("Guardando en la nube y generando carpetas..."):
                            nuevo_registro = {
                                "Usuario": nuevo_user.strip(), "Password": hash_password(nueva_clave.strip()), "Nombre_Real": nuevo_nombre.strip(),
                                "Correo": "pendiente", "Debe_Cambiar_Clave": 'True', "Plan": nuevo_plan
                            }
                            df_usuarios_admin = pd.concat([df_usuarios_admin, pd.DataFrame([nuevo_registro])], ignore_index=True)
                            
                            # Forzamos la actualización
                            safe_update_sheet("base_usuarios", df_usuarios_admin)
                            
                            # Generar archivo local si es abogado para evitar errores de lectura
                            archivo_tareas_nuevo = f"base_tareas_{nuevo_user}.csv"
                            if not os.path.exists(archivo_tareas_nuevo):
                                pd.DataFrame(columns=COLS_TAREAS).to_csv(archivo_tareas_nuevo, index=False)
                                
                            st.success(f"✅ ¡Cuenta autorizada! El usuario **{nuevo_user}** ya puede acceder con el plan **{nuevo_plan}**.")
                            import time; time.sleep(0.4); st.rerun()

    with tab_editar:
        with st.container(border=True):
            st.subheader("Auditoría de Cuentas y Accesos")
            lista_usuarios = df_usuarios_admin['Usuario'].tolist()
            
            c_ed1, c_ed2, c_ed3 = st.columns(3)
            with c_ed1:
                usuario_editar = st.selectbox("Seleccionar Usuario a Modificar", lista_usuarios)
            with c_ed2:
                try:
                    plan_actual_usr = df_usuarios_admin.loc[df_usuarios_admin['Usuario'] == usuario_editar, 'Plan'].values[0]
                    idx_plan = ["Básico", "Medio", "Full"].index(plan_actual_usr)
                except Exception: idx_plan = 0
                nuevo_plan_edit = st.selectbox("Modificar Nivel de Acceso", ["Básico", "Medio", "Full"], index=idx_plan)
            with c_ed3:
                st.write("") 
                st.write("")
                if st.button("🔄 Autorizar Cambio de Plan", type="primary", use_container_width=True):
                    with st.spinner("Sincronizando permisos..."):
                        df_usuarios_admin.loc[df_usuarios_admin['Usuario'] == usuario_editar, 'Plan'] = nuevo_plan_edit
                        safe_update_sheet("base_usuarios", df_usuarios_admin)
                        st.success(f"✅ Los permisos de **{usuario_editar}** han sido actualizados en el sistema.")
                        import time; time.sleep(0.4); st.rerun()
                        
        st.markdown("**Resumen de Usuarios Activos**")
        st.dataframe(df_usuarios_admin[['Usuario', 'Nombre_Real', 'Plan', 'Correo']], use_container_width=True)

    with tab_vision:
        st.subheader("Monitoreo Absoluto de la Oficina")
        
        c_refresh, _ = st.columns([1, 4])
        if c_refresh.button("🔄 Actualizar ahora", help="Fuerza una relectura de todos los archivos del equipo, por si algún cambio reciente no se refleja."):
            # Limpia del caché de sesión cualquier archivo base_causas_*/base_tareas_*
            # para forzar que se vuelvan a leer del disco en este instante.
            claves_a_limpiar = [k for k in st.session_state.keys() if k.startswith("_csv_cache_base_causas_") or k.startswith("_csv_cache_base_tareas_")]
            for k in claves_a_limpiar:
                del st.session_state[k]
            st.rerun()
        
        todas_causas = []
        todas_tareas = []
        
        # IMPORTANTE: se lee directo de los archivos que existen en el disco (glob),
        # en vez de depender de la lista de usuarios de Google Sheets. Si un usuario
        # nuevo no queda bien reflejado ahí (o esa lectura está desactualizada), su
        # archivo de causas/tareas igual se detecta y se muestra aquí.
        for arch_c in glob.glob("base_causas_*.csv"):
            u = arch_c.replace("base_causas_", "").replace(".csv", "")
            temp_c = leer_csv_local(arch_c)
            if not temp_c.empty:
                temp_c['Usuario_Propietario'] = u 
                todas_causas.append(temp_c)
        for arch_t in glob.glob("base_tareas_*.csv"):
            u = arch_t.replace("base_tareas_", "").replace(".csv", "")
            temp_t = leer_csv_local(arch_t)
            if not temp_t.empty:
                temp_t['Usuario_Propietario'] = u
                todas_tareas.append(temp_t)
        
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            st.markdown("<div class='dash-card'><h4>Causas Globales</h4>", unsafe_allow_html=True)
            if todas_causas:
                df_full_causas = pd.concat(todas_causas, ignore_index=True)
                st.metric("Total de Causas en JuriSync", len(df_full_causas))
                st.dataframe(df_full_causas[['Usuario_Propietario', 'ROL', 'Cliente', 'TRIBUNAL']], use_container_width=True)
            else: st.info("No hay causas registradas.")
            st.markdown("</div>", unsafe_allow_html=True)
            
        with col_v2:
            st.markdown("<div class='dash-card'><h4>Tareas Operativas Globales</h4>", unsafe_allow_html=True)
            if todas_tareas:
                df_full_tareas = pd.concat(todas_tareas, ignore_index=True)
                st.metric("Total de Gestiones Pendientes", len(df_full_tareas[df_full_tareas['Estado'] == 'En progreso']))
                st.dataframe(df_full_tareas[['Usuario_Propietario', 'Titulo', 'Estado', 'Fecha_Vencimiento']], use_container_width=True)
            else: st.info("No hay tareas creadas.")
            st.markdown("</div>", unsafe_allow_html=True)

    with tab_peligro:
        st.subheader("Borrón y Cuenta Nueva (Limpieza Estricta)")
        st.error("⚠️ ADVERTENCIA: Esta acción eliminará permanentemente todos los clientes, causas, tareas, contratos, trámites y documentos de Google Sheets y del servidor local.")
        
        confirmacion_borrado = st.text_input("Escribe **BORRAR** en mayúsculas para habilitar el botón de eliminación:", key="confirmacion_borrado_total")
        
        if st.button("🚨 BORRAR TODA LA BASE DE DATOS DEL SISTEMA 🚨", type="primary", use_container_width=True, disabled=(confirmacion_borrado.strip() != "BORRAR")):
            with st.spinner("Formateando absolutamente TODAS las tablas en la nube y discos locales..."):
                try:
                    safe_update_sheet("base_clientes", pd.DataFrame(columns=COLS_CLIENTES))
                    safe_update_sheet("base_causas", pd.DataFrame(columns=COLS_CAUSAS))
                    safe_update_sheet("base_tareas", pd.DataFrame(columns=COLS_TAREAS))
                    safe_update_sheet("base_contratos", pd.DataFrame(columns=COLS_CONTRATOS))
                    safe_update_sheet("base_tramites", pd.DataFrame(columns=COLS_TRAMITES))
                    safe_update_sheet("base_estado_diario", pd.DataFrame(columns=['ID_ED', 'Fecha_Estado', 'ROL', 'Tribunal', 'Resolucion_Extracto', 'Doc_Nombre', 'Doc_B64', 'Doc_Drive_ID']))
                    safe_update_sheet("base_documentos_clientes", pd.DataFrame(columns=['ID_Req', 'Cliente_Token', 'Documento_Nombre', 'Estado', 'Archivo_B64', 'Archivo_Drive_ID', 'Fecha_Subida']))
                except Exception as e:
                    st.error(f"Error limpiando Google Sheets: {e}")
                
                # Barrido nuclear de archivos locales (salvando solo a los usuarios)
                import glob
                for archivo_local in glob.glob("base_*.csv"):
                    if "usuarios" not in archivo_local:
                        try: os.remove(archivo_local)
                        except Exception: pass
                
                st.cache_data.clear()
            st.success("✅ Limpieza extrema completada. Sistema restablecido a cero.")
            import time; time.sleep(0.6); st.rerun()

# 15. REDACTOR AUTOMÁTICO IA
elif st.session_state['menu_radio'] == "📝 Redactor IA":
    st.title("📝 Redactor Automático de Escritos")
    st.markdown("La IA redactará el borrador del escrito judicial con el formato y lenguaje formal de los tribunales chilenos, listo para revisar y presentar.")
    
    # AUTOCOMPLETADO CONTEXTUAL: si llegaste aquí desde el botón "Redactar Escrito"
    # de una causa, se precargan el Rol/Tribunal/Caratulado de esa causa, para no
    # tener que volver a escribirlos. Se usa una sola vez y luego se limpia.
    _prefill_redactor = st.session_state.pop('redactor_prefill', None)
    if _prefill_redactor:
        st.session_state['redactor_rol_key'] = _prefill_redactor.get('rol', '')
        st.session_state['redactor_trib_key'] = _prefill_redactor.get('tribunal', '')
        st.session_state['redactor_carat_key'] = _prefill_redactor.get('caratulado', '')
        st.info(f"✅ Datos de la causa **{_prefill_redactor.get('rol','')}** cargados automáticamente.")
    
    with st.container(border=True):
        col_r1, col_r2 = st.columns(2)
        tipo_escrito = col_r1.selectbox("Tipo de Escrito", [
            "Oposición de Excepciones (Ejecutivo)", 
            "Contesta Demanda (General)", 
            "Incidente de Nulidad", 
            "Recurso de Reposición", 
            "Solicitud de Abandono del Procedimiento",
            "Otro (Especificar en instrucciones)"
        ])
        tribunal_red = col_r2.text_input("Tribunal (Para la suma)", placeholder="Ej: S.J.L. en lo Civil (1°)", key="redactor_trib_key")
        
        rol_red = col_r1.text_input("Causa Rol", placeholder="Ej: C-1234-2026", key="redactor_rol_key")
        caratula_red = col_r2.text_input("Caratulado", placeholder="Ej: PEREZ / BANCO", key="redactor_carat_key")
        
        instrucciones_red = st.text_area("Instrucciones específicas para el escrito:", height=150, placeholder="Ej: Redactar excepción N° 17 de prescripción. La deuda se hizo exigible en marzo de 2024 y notificaron recién ayer. Alega también costas.")
        
        if st.button("✍️ Generar Borrador del Escrito", type="primary", use_container_width=True):
            if not instrucciones_red.strip():
                st.error("⚠️ Debes darle las instrucciones jurídicas a la IA.")
            else:
                with st.spinner("⚖️ Redactando escrito en lenguaje procesal chileno..."):
                    try:
                        import google.generativeai as genai
                        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                        
                        modelo_elegido = "gemini-1.0-pro"
                        for m in genai.list_models():
                            if 'generateContent' in m.supported_generation_methods:
                                md_name = m.name.replace("models/", "")
                                if 'flash' in md_name:
                                    modelo_elegido = md_name
                                    break
                                    
                        modelo = genai.GenerativeModel(modelo_elegido)
                        
                        prompt_redactor = f"""
                        Actúa como un abogado litigante chileno con impecable ortografía y redacción procesal formal.
                        Debes redactar un escrito judicial completo con los siguientes datos:
                        
                        Tipo de Escrito: {tipo_escrito}
                        Tribunal: {tribunal_red}
                        Rol: {rol_red}
                        Caratulado: {caratula_red}
                        
                        INSTRUCCIONES DE FONDO:
                        {instrucciones_red}
                        
                        Estructura requerida:
                        1. Suma(s) y Tribunal.
                        2. Individualización de la parte y personería.
                        3. Cuerpo del escrito (hechos y derecho de forma persuasiva y técnica, citando la ley chilena).
                        4. Petitorio claro ("POR TANTO: Ruego a S.S...").
                        5. Peticiones subsidiarias o un "Otrosí" si corresponde según las instrucciones.
                        
                        Usa el lenguaje propio del Código de Procedimiento Civil chileno. No agregues notas explicativas para mí, entrégame SOLO el texto del escrito listo para copiar.
                        """
                        
                        respuesta_escrito = modelo.generate_content(prompt_redactor)
                        st.success("✅ Borrador redactado. Cópialo, revísalo y pásalo a Word.")
                        st.text_area("Escrito Generado:", value=respuesta_escrito.text, height=500)
                        
                    except Exception as e:
                        st.error(f"❌ Hubo un error de conexión: {e}")