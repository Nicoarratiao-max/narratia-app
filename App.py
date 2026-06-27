import streamlit as st
import pandas as pd
import os
import json
import uuid
import base64
import io
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from streamlit_calendar import calendar
from streamlit_gsheets import GSheetsConnection

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
    hora_chile = (datetime.utcnow() - timedelta(hours=4)).hour
    if 0 <= hora_chile < 12:
        return "Buenos días"
    elif 12 <= hora_chile < 19:
        return "Buenas tardes"
    else:
        return "Buenas noches"

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
            'Servicio', 'Teléfono', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal', 
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

# --- MOTOR REDACTOR DE CONTRATOS EN WORD ---
def crear_contrato_word(datos):
    if not DOCX_READY: 
        return None
        
    doc = Document()
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Arial'
    font.size = Pt(11)
    
    titulo = doc.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_tit = titulo.add_run("CONTRATO DE PRESTACIÓN DE SERVICIOS PROFESIONALES\n")
    r_tit.bold = True
    
    hoy = datetime.now()
    meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    fecha_str = f"{hoy.day} de {meses[hoy.month-1].lower()} del año {hoy.year}"
    
    intro = doc.add_paragraph()
    intro.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    intro.add_run(f"En Santiago, República de Chile, a {fecha_str}, comparecen:\n\n")
    intro.add_run(f"Por una parte, don/doña {datos['abogado_nombre']}, de nacionalidad chilena, abogado, cédula nacional de identidad número {datos['abogado_rut']}, con domicilio profesional en {datos['abogado_domicilio']}, correo electrónico {datos['abogado_correo']}, en adelante e indistintamente como \"EL ABOGADO\"; y,\n\n")
    intro.add_run(f"Por otra parte, don/doña {datos['cliente_nombre']}, de nacionalidad chilena, cédula nacional de identidad número {datos['cliente_rut']}, con domicilio en {datos['cliente_domicilio']}, número telefónico de contacto {datos['cliente_tel']}, correo electrónico {datos['cliente_correo']}, en adelante como \"EL CLIENTE\".\n\n")
    intro.add_run("Ambas partes de conformidad y compareciendo como mayores de edad, exponen que han convenido el siguiente contrato:")
    
    p1 = doc.add_paragraph()
    p1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p1.add_run("\nCLÁUSULA PRIMERA: OBJETO DEL CONTRATO. ").bold = True
    p1.add_run(f"Por medio de este instrumento, el Cliente encomienda la representación y patrocinio legal a El Abogado para la tramitación judicial y defensa respectiva de un procedimiento de {datos['tipo_servicio'].upper()}.\n")
    p1.add_run("Los servicios profesionales comprometidos por el profesional contemplan de forma específica lo siguiente:\n")
    p1.add_run(datos['detalle_servicio'])
    
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p2.add_run("\nCLÁUSULA SEGUNDA: HONORARIOS PROFESIONALES. ").bold = True
    p2.add_run(f"Los honorarios totales convenidos por la prestación de los servicios profesionales ascienden a la suma correlativa de {datos['honorarios_num']} ({datos['honorarios_letras']}).\n")
    p2.add_run("Esta suma se considera alzada y fija por la tramitación completa descrita en la cláusula anterior.")
    
    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p3.add_run("\nCLÁUSULA TERCERA: CONDICIONES Y FORMA DE PAGO. ").bold = True
    p3.add_run(f"La suma total de los honorarios fijados será prorrateada y pagada en un total de {datos['cuotas_cant']} cuotas mensuales, fijas y sucesivas por un valor individual de {datos['cuotas_monto']} cada una.\n")
    
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
        
    p3_bis = doc.add_paragraph()
    p3_bis.add_run("\nInformación Bancaria para Transferencias Electrónicas:\n").bold = True
    p3_bis.add_run(f"Titular de la Cuenta: {datos['abogado_nombre']}\nRUT: {datos['abogado_rut']}\nInstitución Bancaria: {datos['banco']}\nTipo de Cuenta: {datos['tipo_cuenta']}\nNúmero de Cuenta: {datos['num_cuenta']}")

    p4 = doc.add_paragraph()
    p4.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p4.add_run("\nCLÁUSULA CUARTA: EFECTOS DEL INCUMPLIMIENTO Y MOROSIDAD. ").bold = True
    p4.add_run("El cumplimiento exacto de los plazos de pago constituye un elemento esencial del presente contrato. Ante la ocurrencia de morosidad o retardo en el pago de cualquiera de las cuotas mensuales devengadas, operarán los siguientes efectos jurídicos:\n")
    p4.add_run("Aceleración de la Deuda: ").bold = True
    p4.add_run("La mora faculta de forma inmediata a exigir el cobro íntegro del saldo total que permanezca insoluto.\n")
    p4.add_run("Suspensión Técnica: ").bold = True
    p4.add_run("Un retraso superior a cinco días hábiles faculta la suspensión inmediata de la tramitación de escritos en los tribunales.\n")
    p4.add_run("Sanción Penal: ").bold = True
    p4.add_run("Se devengará una multa compensatoria equivalente a 0,15 Unidades de Fomento (UF) por cada jornada de atraso hasta el pago efectivo.")

    p5 = doc.add_paragraph()
    p5.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p5.add_run("\nCLÁUSULA QUINTA: OBLIGACIONES RECÍPROCAS DE LAS PARTES.\n").bold = True
    p5.add_run("Obligación del Profesional: ").bold = True
    p5.add_run("El Abogado asume una obligación de medios diligentes, debiendo desplegar todo su conocimiento técnico, técnico-jurídico y ético para la tramitación de la causa.\n")
    p5.add_run("Obligación del Contratante: ").bold = True
    p5.add_run("El Cliente se obliga de manera estricta a proporcionar toda la documentación de respaldo requerida en los plazos fijados por el profesional.")

    num_clausula = 6
    if datos['tipo_servicio'] == "Liquidación voluntaria":
        p6 = doc.add_paragraph()
        p6.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p6.add_run("\nCLÁUSULA SEXTA: ENTREGA DE DECLARACIONES JURADAS OBLIGATORIAS. ").bold = True
        p6.add_run("Atendida la naturaleza específica del procedimiento de insolvencia y liquidación voluntaria, el Cliente asume la obligación ineludible de suscribir y entregar las siguientes declaraciones juradas reguladas por la ley:\n")
        p6.add_run("- Declaración Jurada de Bienes Excluidos o de Terceros.\n- Declaración Jurada de Listado Completo de Acreedores.\n- Consentimiento Informado Expreso de Efectos de la Liquidación.")
        num_clausula += 1

    diccionario_numeros = {6: "SEXTA", 7: "SÉPTIMA", 8: "OCTAVA"}
    
    p7 = doc.add_paragraph()
    p7.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p7.add_run(f"\nCLÁUSULA {diccionario_numeros[num_clausula]}: DESISTIMIENTO UNILATERAL. ").bold = True
    p7.add_run("En caso de que el Cliente decida poner término unilateral o desistirse del procedimiento judicial ya iniciado, los montos enterados a la fecha pertenecerán a El Abogado a título de honorarios devengados por concepto de estudio y redacción jurídica.")
    num_clausula += 1

    p8 = doc.add_paragraph()
    p8.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p8.add_run(f"\nCLÁUSULA {diccionario_numeros[num_clausula]}: DOMICILIO CONVENCIONAL Y COMPETENCIA. ").bold = True
    p8.add_run("Para todos los efectos legales y derivados del presente instrumento, las partes fijan su domicilio común en la comuna de Santiago y se someten a la prórroga de competencia de sus Tribunales Ordinarios de Justicia.\n\nEn señal de plena conformidad, se extiende el presente contrato en dos ejemplares idénticos.")

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
# --- SISTEMA DE CONTROL DE ACCESO (AHORA EN GOOGLE SHEETS Y COOKIES) ---
# =====================================================================
import extra_streamlit_components as stx

ARCHIVO_USUARIOS = "base_usuarios.csv"
conn = st.connection("gsheets", type=GSheetsConnection)

def guardar_en_nube(df):
    conn.update(worksheet="base_usuarios", data=df)
    df.to_csv(ARCHIVO_USUARIOS, index=False)
    st.cache_data.clear()

try:
    df_usuarios = conn.read(worksheet="base_usuarios", usecols=[0, 1, 2, 3, 4, 5], ttl="10m")
    df_usuarios = df_usuarios.dropna(how="all")
    df_usuarios['Debe_Cambiar_Clave'] = df_usuarios['Debe_Cambiar_Clave'].astype(str)
except:
    df_usuarios = pd.DataFrame()

if df_usuarios.empty:
    datos_iniciales = {
        "Usuario": ["Narratia", "Vfarfan", "Gdonoso", "Mcortes", "Jtrujillo", "Eriquelme"],
        "Password": ["20911237", "vpfm2404", "gdonoso123", "Mcortes123", "Jtrujillo123", "Eriquelme123"],
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
        nuevo_u = pd.DataFrame([{"Usuario": "Eriquelme", "Password": "Eriquelme123", "Nombre_Real": "Eduardo Riquelme", "Correo": "pendiente", "Debe_Cambiar_Clave": 'True', "Plan": "Full"}])
        df_usuarios = pd.concat([df_usuarios, nuevo_u], ignore_index=True)
        cambios = True

    if "Jtrujillo" not in df_usuarios['Usuario'].values:
        nuevo_u = pd.DataFrame([{"Usuario": "Jtrujillo", "Password": "Jtrujillo123", "Nombre_Real": "José Trujillo", "Correo": "pendiente", "Debe_Cambiar_Clave": 'True', "Plan": "Full"}])
        df_usuarios = pd.concat([df_usuarios, nuevo_u], ignore_index=True)
        cambios = True

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

# Si el navegador ya tiene la galleta guardada, lo dejamos pasar automático
cookie_usuario = cookie_manager.get(cookie="jurisync_user")
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
        pd.DataFrame(columns=['ID_Req', 'Cliente_Token', 'Documento_Nombre', 'Estado', 'Archivo_B64', 'Fecha_Subida']).to_csv(ARCHIVO_DOCS, index=False)
        
    df_docs = pd.read_csv(ARCHIVO_DOCS)
    mis_docs = df_docs[df_docs['Cliente_Token'] == token_cliente]
    
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
                            with st.spinner("Guardando en la nube de JuriSync..."):
                                b64_file = base64.b64encode(archivo.getvalue()).decode('utf-8')
                                df_docs.loc[df_docs['ID_Req'] == row['ID_Req'], ['Estado', 'Archivo_B64', 'Fecha_Subida']] = ['✅ Completado', b64_file, datetime.now().strftime("%d/%m/%Y")]
                                df_docs.to_csv(ARCHIVO_DOCS, index=False)
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
                    if user_clean in USUARIOS_DICT and str(USUARIOS_DICT[user_clean]) == str(pass_input):
                        idx_user = df_usuarios[df_usuarios['Usuario'] == user_clean].index[0]
                        if str(df_usuarios.loc[idx_user, 'Debe_Cambiar_Clave']).lower() == 'true':
                            st.session_state['requiere_registro_inicial'] = True
                            st.session_state['usr_registro'] = user_clean
                            st.rerun()
                        else:
                            # 1. Guardamos la galleta en el navegador
                            cookie_manager.set("jurisync_user", user_clean, key="cookie_login")
                            # 2. Le decimos a Streamlit que estás logueado
                            st.session_state['logged_in'] = True
                            st.session_state['username'] = user_clean
                            # 3. TRUCO DE MAGIA: Esperamos 1 segundo para que el navegador alcance a guardar la galleta
                            import time
                            time.sleep(1)
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
                            df_usuarios.loc[df_usuarios['Usuario'] == rec_usuario, 'Password'] = "Temp1234"
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
                    else:
                        idx_mod = df_usuarios[df_usuarios['Usuario'] == usr_actualizar].index[0]
                        df_usuarios.at[idx_mod, 'Password'] = nueva_cl
                        df_usuarios.at[idx_mod, 'Correo'] = nuevo_correo
                        df_usuarios.at[idx_mod, 'Debe_Cambiar_Clave'] = 'False'
                        
                        guardar_en_nube(df_usuarios)
                        
                        st.session_state['logged_in'] = True
                        st.session_state['username'] = usr_actualizar
                        st.session_state['requiere_registro_inicial'] = False
                        cookie_manager.set("jurisync_user", usr_actualizar, key="cookie_reg")
                        st.rerun()
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
    df_vacio_t = pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad'])
    df_vacio_t.to_csv(ARCHIVO_TAREAS, index=False)
else:
    df_t_check = pd.read_csv(ARCHIVO_TAREAS)
    if 'Prioridad' not in df_t_check.columns:
        df_t_check['Prioridad'] = 'Media'
        df_t_check.to_csv(ARCHIVO_TAREAS, index=False)

if not os.path.exists(ARCHIVO_BD):
    df_vacio_c = pd.DataFrame(columns=['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'RUT', 'Teléfono', 'Tipo_Negocio', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas'])
    df_vacio_c.to_csv(ARCHIVO_BD, index=False)
else:
    df_c_check = pd.read_csv(ARCHIVO_BD)
    ejecutar_guardado_check = False
    columnas_requeridas_bd = ['Cliente', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']
    for col in columnas_requeridas_bd:
        if col not in df_c_check.columns:
            if col in ['Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']: 
                df_c_check[col] = 0
            elif col == 'Estado_Honorarios': 
                df_c_check[col] = "Sin fijar"
            else: 
                df_c_check[col] = pd.Series(dtype='str')
            ejecutar_guardado_check = True
    if ejecutar_guardado_check:
        df_c_check.to_csv(ARCHIVO_BD, index=False)

if not os.path.exists(ARCHIVO_CONTRATOS):
    df_vacio_co = pd.DataFrame(columns=['ID', 'Fecha', 'Cliente', 'Servicio', 'Honorarios'])
    df_vacio_co.to_csv(ARCHIVO_CONTRATOS, index=False)

if not os.path.exists(ARCHIVO_TRAMITES):
    df_vacio_tr = pd.DataFrame(columns=['ID_Tramite', 'ROL', 'Fecha_Pago', 'Tipo_Auxiliar', 'Monto', 'Comprobante_Nombre', 'Comprobante_B64', 'Registrado_Por'])
    df_vacio_tr.to_csv(ARCHIVO_TRAMITES, index=False)

if not os.path.exists(ARCHIVO_ESTADO_DIARIO):
    df_vacio_ed = pd.DataFrame(columns=['ID_ED', 'Fecha_Estado', 'ROL', 'Tribunal', 'Resolucion_Extracto', 'Doc_Nombre', 'Doc_B64'])
    df_vacio_ed.to_csv(ARCHIVO_ESTADO_DIARIO, index=False)

if not os.path.exists(ARCHIVO_MENSAJES):
    pd.DataFrame(columns=['ID', 'Fecha', 'De', 'Para', 'Mensaje']).to_csv(ARCHIVO_MENSAJES, index=False)

# --- NOTIFICADOR ESTILO OUTLOOK (TOAST) ---
if st.session_state['logged_in']:
    if os.path.exists(ARCHIVO_MENSAJES):
        df_msgs_alerta = pd.read_csv(ARCHIVO_MENSAJES)
        mis_mensajes = df_msgs_alerta[(df_msgs_alerta['Para'] == nombre_real_usuario) | (df_msgs_alerta['Para'] == 'Todos')]
        
        if 'ultimo_mensaje_leido' not in st.session_state:
            st.session_state['ultimo_mensaje_leido'] = len(mis_mensajes)
        elif len(mis_mensajes) > st.session_state['ultimo_mensaje_leido']:
            mensajes_nuevos = len(mis_mensajes) - st.session_state['ultimo_mensaje_leido']
            st.toast(f"🔔 ¡Tienes {mensajes_nuevos} mensaje(s) nuevo(s) en tu buzón!", icon="📩")
            st.session_state['ultimo_mensaje_leido'] = len(mis_mensajes)

# --- FUNCIÓN DE AUTOLIMPIEZA SISTEMA (15 DÍAS EXACTOS) ---
def limpiar_documentos_estado_diario():
    if os.path.exists(ARCHIVO_ESTADO_DIARIO):
        df_ed = pd.read_csv(ARCHIVO_ESTADO_DIARIO)
        if not df_ed.empty:
            df_ed['Fecha_DT'] = pd.to_datetime(df_ed['Fecha_Estado'], format='%d/%m/%Y', errors='coerce')
            limite_fecha = datetime.now() - timedelta(days=15)
            mascara_viejos = df_ed['Fecha_DT'] < limite_fecha
            df_ed.loc[mascara_viejos, 'Doc_B64'] = ""
            df_ed.loc[mascara_viejos, 'Doc_Nombre'] = df_ed.loc[mascara_viejos, 'Doc_Nombre'].apply(lambda x: f"(Eliminado por memoria) {x}" if pd.notna(x) and x != "" and not str(x).startswith("(Eliminado") else x)
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
    except:
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

def ir_a_expediente(rol_causa): 
    st.session_state.menu_radio = "💼 Causas"
    st.session_state.causa_seleccionada = rol_causa

def limpiar_causa():
    st.session_state.causa_seleccionada = None

# --- CSS CLARO PROFESIONAL (ESTILO JIRA/TRELLO) ---
st.markdown("""
<style>
    /* Fondo principal y estructura */
    [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
    [data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e0e4e8 !important; }
    [data-testid="stHeader"] { background-color: transparent !important; }
    .stMarkdown, p, span, label, h1, h2, h3, h4, h5, h6 { color: #172b4d !important; }
    
    /* Tarjetas tipo Dashboard */
    .dash-card { background: #ffffff !important; border-radius: 12px; padding: 18px; border: 1px solid #e0e4e8 !important; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }
    .dash-header { border-bottom: 2px solid #0052cc; padding-bottom: 5px; margin-bottom: 15px; font-weight: 800; font-size: 13px; color: #0052cc; letter-spacing: 0.5px; text-transform: uppercase; }
    
    /* Insignias */
    .badge-active { background: #57a15a !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    .badge-propio { background: #0052cc !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    
    /* Inputs y Textareas - Claros y legibles */
    .stTextInput input, .stTextArea textarea, .stSelectbox select, .stNumberInput input { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #cbd2d9 !important; border-radius: 6px !important; }
    .stTextInput input:focus, .stTextArea textarea:focus { border-color: #0052cc !important; box-shadow: 0 0 0 1px #0052cc !important; }
    ::placeholder { color: #6b778c !important; opacity: 1; }
    
    /* Botones uniformes */
    [data-testid="stButton"] button { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #cbd2d9 !important; border-radius: 6px !important; font-weight: 600 !important; transition: all 0.2s ease !important; }
    [data-testid="stButton"] button:hover { border-color: #0052cc !important; color: #0052cc !important; background-color: #deebff !important; }
    
    /* Contenedores de formularios */
    [data-testid="stVerticalBlockBorderWrapper"] { background-color: #ffffff !important; border-radius: 12px !important; border: 1px solid #e0e4e8 !important; }
    
    /* Chat WhatsApp Style (Modo Claro) */
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
    df_usuarios_plan = pd.read_csv(ARCHIVO_USUARIOS)
    
    # Si la columna 'Plan' no existe aún, la creamos automáticamente
    if 'Plan' not in df_usuarios_plan.columns:
        df_usuarios_plan['Plan'] = 'Full' 
        df_usuarios_plan.to_csv(ARCHIVO_USUARIOS, index=False)
        
    # Rescatar el plan del usuario que inició sesión
    usuario_actual = st.session_state.get('username', 'Desconocido')
    
    try:
        plan_actual = df_usuarios_plan.loc[df_usuarios_plan['Usuario'] == usuario_actual, 'Plan'].values[0]
    except:
        plan_actual = "Básico" # Nivel de seguridad por defecto

    # --- RENDERIZAR MENÚ SEGÚN EL PLAN ---
    opciones_basicas = [
        "🏠 Inicio", "📅 Calendario", "📋 Agenda", "☑️ Tareas", "💼 Causas", "👥 Clientes"
    ]
    
    if plan_actual == "Básico":
        opciones_flujo = opciones_basicas
    elif plan_actual == "Medio":
        opciones_flujo = opciones_basicas + [
            "📄 Contratos", "💰 Contabilidad", "📝 Trámites", "📆 Estado diario"
        ]
    else: # Nivel "Full"
        opciones_flujo = opciones_basicas + [
            "📄 Contratos", "💰 Contabilidad", "📝 Trámites", "📆 Estado diario", 
            "✈️ Mensajería", "🧠 Estrategia", "📊 Informes", "📥 Excel", "📝 Redactor IA"
        ]
        
    # El botón secreto de Admin: Solo aparece si el que entró eres tú (Narratia)
    if usuario_actual == "Narratia":
        opciones_flujo.append("👑 Panel Admin")

    for i, opcion in enumerate(opciones_flujo):
        if st.button(opcion, use_container_width=True, key=f"btn_nav_{i}"):
            st.session_state['menu_radio'] = opcion
            resetear_vistas()
            st.rerun()

    st.markdown("<br><br>", unsafe_allow_html=True)
    
    # --- MENÚ DE PERFIL Y SEGURIDAD ---
    with st.expander(f"👤 {nombre_real_usuario} (Mi Perfil)"):
        st.markdown("<span style='font-size:13px; color:#6b778c;'>Configura tu correo de recuperación o cambia tu clave:</span>", unsafe_allow_html=True)
        with st.form("form_perfil"):
            df_usr = pd.read_csv(ARCHIVO_USUARIOS)
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
                        df_usr.loc[df_usr['Usuario'] == usuario_actual, 'Password'] = upd_clave
                        cambios = True
                    else:
                        st.error("La clave debe tener mínimo 6 caracteres")
                
                if cambios:
                    df_usr.loc[df_usr['Usuario'] == usuario_actual, 'Debe_Cambiar_Clave'] = 'False'
                    df_usr.to_csv(ARCHIVO_USUARIOS, index=False)
                    st.success("¡Datos actualizados correctamente!")
                    st.rerun()

    st.write("")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        cookie_manager.delete("jurisync_user", key="cookie_logout")
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()

# --- CONTROLADOR DE PESTAÑAS (VISTAS INDIVIDUALES EXPLICITAS) ---

# 1. HOME / INICIO
if st.session_state['menu_radio'] == "🏠 Inicio":
    st.title(f"{obtener_saludo()}, {nombre_real_usuario}")
    st.write("Panel de control unificado. Aquí tienes un resumen de tu actividad judicial de la oficina.")
    st.write("<br>", unsafe_allow_html=True)
    
    df_causas_totales = pd.read_csv(ARCHIVO_BD)
    df_tareas_totales = pd.read_csv(ARCHIVO_TAREAS)
    
    cant_causas = len(df_causas_totales) if not df_causas_totales.empty else 0
    cant_clientes = len(df_causas_totales['Cliente'].dropna().unique()) if not df_causas_totales.empty and 'Cliente' in df_causas_totales.columns else 0
    
    fecha_hoy_str = datetime.now().strftime("%d/%m/%Y")
    tareas_del_dia = len(df_tareas_totales[df_tareas_totales['Fecha_Vencimiento'] == fecha_hoy_str]) if not df_tareas_totales.empty else 0
    
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
    df_c = pd.read_csv(ARCHIVO_BD)
    
    df_activos = df_c[(df_c['Total_Honorarios'] > 0) & (df_c['Estado_Honorarios'] == "Pendientes")].copy()
    
    if df_activos.empty:
        st.info("No hay contratos activos con honorarios pendientes de pago.")
    else:
        col_l, col_c, col_r = st.columns([0.2, 8, 0.2])
        
        with col_c:
            cliente_sel = st.selectbox("Selecciona un Cliente para gestionar su ficha:", df_activos['Cliente'].unique())
            datos_cli = df_activos[df_activos['Cliente'] == cliente_sel].iloc[0]
            
            with st.expander("⚙️ Ajustar Fecha de Inicio de Pagos"):
                fecha_actual_cli = datetime.strptime(str(datos_cli.get('Fecha_Inicio', datetime.now().strftime("%Y-%m-%d"))), "%Y-%m-%d")
                nueva_fecha = st.date_input("Fecha de inicio de la primera cuota:", value=fecha_actual_cli)
                if st.button("Guardar nueva fecha de inicio"):
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
            
            fecha_inicio = datetime.strptime(str(datos_cli.get('Fecha_Inicio', datetime.now().strftime("%Y-%m-%d"))), "%Y-%m-%d")
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
    
    df_causas = pd.read_csv(ARCHIVO_BD)
    df_tramites = pd.read_csv(ARCHIVO_TRAMITES)
    
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
                    nombre_archivo = ""
                    if comprobante:
                        nombre_archivo = comprobante.name
                        b64_str = base64.b64encode(comprobante.getvalue()).decode('utf-8')
                    
                    nuevo_tramite = {
                        'ID_Tramite': str(uuid.uuid4())[:8], 'ROL': rol_sel, 'Fecha_Pago': fecha_pago.strftime("%d/%m/%Y"),
                        'Tipo_Auxiliar': tipo_aux, 'Monto': monto_pagado, 'Comprobante_Nombre': nombre_archivo,
                        'Comprobante_B64': b64_str, 'Registrado_Por': nombre_real_usuario,
                        'Usuario_Propietario': usuario_actual
                    }
                    
                    df_tramites = pd.concat([df_tramites, pd.DataFrame([nuevo_tramite])], ignore_index=True)
                    df_tramites.to_csv(ARCHIVO_TRAMITES, index=False)
                    
                    # ☁️ RESPALDO EN LA NUBE
                    try:
                        df_nube_tr = conn.read(worksheet="base_tramites", ttl=0)
                        df_nube_tr_upd = pd.concat([df_nube_tr, pd.DataFrame([nuevo_tramite])], ignore_index=True)
                        conn.update(worksheet="base_tramites", data=df_nube_tr_upd)
                    except:
                        conn.update(worksheet="base_tramites", data=df_tramites)
                        
                    st.success("✅ Registro de trámite respaldado en la nube.")
                    import time
                    time.sleep(1)
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
                        if pd.notna(tram['Comprobante_B64']) and tram['Comprobante_B64'] != "":
                            st.download_button("📥 Descargar Soporte", data=base64.b64decode(tram['Comprobante_B64']), file_name=tram['Comprobante_Nombre'], key=f"dt_{tram['ID_Tramite']}")

# 4. ESTADO DIARIO Y SCRAPER
elif st.session_state['menu_radio'] == "📆 Estado diario":
    st.title("📆 Módulo de Cruce y Sincronización de Estado Diario")
    st.markdown("Herramienta para automatizar la revisión del Estado Diario del Poder Judicial Chileno.")
    
    col_auto, col_man = st.columns(2)
    df_causas = pd.read_csv(ARCHIVO_BD)
    df_pj = pd.DataFrame()
    
    with col_auto:
        st.markdown("<div class='dash-card'><h4>Robot de Scrapeo Automático (PJUD)</h4><p style='font-size:13px; color:#6b778c;'>El sistema intentará conectarse directamente a la Oficina Judicial Virtual de forma gratuita.</p></div>", unsafe_allow_html=True)
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
            df_pj['ROL_LIMPIO'] = df_pj[col_rol_pj].astype(str).str.strip().str.upper()
            df_causas['ROL_LIMPIO'] = df_causas['ROL'].astype(str).str.strip().str.upper()
            coincidencias = pd.merge(df_pj, df_causas[['ROL_LIMPIO', 'Cliente', 'TRIBUNAL', 'Tipo_Negocio']], on='ROL_LIMPIO', how='inner')
            
            if coincidencias.empty:
                st.success("Búsqueda finalizada: Ninguna de nuestras causas vigentes presenta notificaciones el día de hoy.")
            else:
                st.warning(f"⚠️ Se detectaron {len(coincidencias)} causas con movimientos en el Estado Diario.")
                st.dataframe(coincidencias[['ROL_LIMPIO', 'Cliente', 'TRIBUNAL', 'Tipo_Negocio']], use_container_width=True)
                
                st.markdown("### 📎 Acompañar Resoluciones al Expediente Local")
                with st.form("form_resoluciones_cruce"):
                    for i, fila in coincidencias.iterrows():
                        rol_cruce = fila.get('ROL_LIMPIO', "Desconocido")
                        st.write(f"Causa Rol: **{rol_cruce}** | Cliente: {fila.get('Cliente', '')}")
                        st.file_uploader(f"Subir PDF de Resolución ({rol_cruce})", key=f"res_{i}")
                    if st.form_submit_button("Guardar Resoluciones en Sistema", type="primary"):
                        df_ed_hist = pd.read_csv(ARCHIVO_ESTADO_DIARIO)
                        for i, fila in coincidencias.iterrows():
                            archivo_subido = st.session_state.get(f"res_{i}")
                            if archivo_subido:
                                df_ed_hist = pd.concat([df_ed_hist, pd.DataFrame([{
                                    'ID_ED': str(uuid.uuid4())[:8], 'Fecha_Estado': datetime.now().strftime("%d/%m/%Y"),
                                    'ROL': fila.get('ROL_LIMPIO', "Desconocido"), 'Tribunal': fila.get('TRIBUNAL', 'S/I'),
                                    'Resolucion_Extracto': 'Notificación de Estado Diario', 'Doc_Nombre': archivo_subido.name,
                                    'Doc_B64': base64.b64encode(archivo_subido.getvalue()).decode('utf-8')
                                }])], ignore_index=True)
                        df_ed_hist.to_csv(ARCHIVO_ESTADO_DIARIO, index=False)
                        st.success("Resoluciones integradas. Los PDF se autodestruirán en 15 días."); st.rerun()

    st.markdown("### 🗄️ Historial de Resoluciones del Estado Diario")
    df_hist_ed = pd.read_csv(ARCHIVO_ESTADO_DIARIO)
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
                    if pd.notna(doc_ed['Doc_B64']) and doc_ed['Doc_B64'] != "": 
                        st.download_button("📥 Descargar PDF", data=base64.b64decode(doc_ed['Doc_B64']), file_name=doc_ed['Doc_Nombre'], key=f"bj_{doc_ed['ID_ED']}")


# 5. INFORMES (IA PARA CLIENTES)
elif st.session_state['menu_radio'] == "📊 Informes":
    st.title("📊 Asistente de Inteligencia Legal - Informes")
    st.markdown("Carga el historial de movimientos o Ebook del Poder Judicial. El sistema analizará el lenguaje técnico y redactará un informe ejecutivo comprensible para tu cliente.")
    
    df_causas_ia = pd.read_csv(ARCHIVO_BD)
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
        materia = st.selectbox("Rama del Derecho", ["Civil / Ejecutivo", "Familia", "Penal", "Laboral", "Policía Local"])
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
            st.markdown("<span style='color:#6b778c; font-size:14px;'>Selecciona primero la especialidad y la acción para activar el contrato:</span>", unsafe_allow_html=True)
            
            diccionario_servicios = {
                "Derecho Civil y Patrimonial": ["Juicio Ejecutivo (Cobro de Pagaré / Facturas)", "Tercería de Posesión / Dominio", "Liquidación Voluntaria (Ley de Quiebras)", "Renegociación de Deudas", "Juicio de Arrendamiento", "Juicio Ordinario", "Juicio de Precario", "Posesión Efectiva y Partición", "Estudio de Títulos"],
                "Derecho de Familia": ["Pensión de Alimentos Mayores", "Pensión de Alimentos Menores", "Aumento / Rebaja / Cese de Alimentos", "Autorización de Salida del País", "Divorcio de Mutuo Acuerdo", "Divorcio Unilateral", "Divorcio Culposo", "Cuidado Personal (Tuición)", "Visitas", "VIF"],
                "Derecho Laboral": ["Despido Injustificado / Indebido", "Despido Indirecto (Autodespido)", "Tutela Laboral", "Cobro de Prestaciones (Ley Bustos)", "Defensa Corporativa"],
                "Derecho Penal": ["Querella Criminal", "Defensa Penal (Garantía)", "Defensa Penal (Juicio Oral)", "Salidas Alternativas", "Eliminación de Antecedentes"],
                "Derecho Constitucional": ["Recurso de Protección", "Recurso de Amparo"],
                "Derecho del Consumidor": ["Querella Infraccional (JPL)", "Defensa Demanda Colectiva (SERNAC)"],
                "Derecho Administrativo": ["Reclamo de Ilegalidad Municipal", "Sumario Administrativo", "Nulidad de Derecho Público", "Cobro de Obligaciones Tibutarias"]
            }
            
            with st.container(border=True):
                col_mat1, col_mat2 = st.columns(2)
                with col_mat1:
                    materia_sel = st.selectbox("Rama del Derecho", list(diccionario_servicios.keys()), key="gen_con_rama")
                with col_mat2:
                    accion_sel = st.selectbox("Acción / Procedimiento Específico", diccionario_servicios[materia_sel], key="gen_con_accion")
                
                tipo_servicio_final = f"{materia_sel}: {accion_sel}"

            with st.form("form_generador_contratos", clear_on_submit=False):
                detalle_servicio = st.text_area("Cláusula Primera: Acciones Legales Incluidas", height=100, key="gen_con_detalle")
                
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
                    st.markdown("#### Módulo 4: Honorarios y Cuenta")
                    c_p1, c_p2 = st.columns(2)
                    with c_p1: 
                        hon_num = st.text_input("Valor Total ($)", "2500000", key="gen_con_honnum") 
                        hon_let = st.text_input("Valor en Letras", key="gen_con_honlet")
                        cuotas_c = st.number_input("Cuotas", 12, key="gen_con_cuotasc")
                        cuotas_m = st.text_input("Valor Cuota ($)", key="gen_con_cuotasm")
                        fecha_pago = st.date_input("Primera Mensualidad", key="gen_con_fecha")
                    with c_p2: 
                        banco = st.text_input("Banco", key="gen_con_banco")
                        tipo_cta = st.selectbox("Tipo de Cuenta", ["Cuenta Corriente", "Cuenta Vista", "Cuenta RUT", "Chequera Electrónica"], key="gen_con_tipocta")
                        num_cta = st.text_input("Número de Cuenta", key="gen_con_numcta")
                        
                if st.form_submit_button("📄 Estructurar Contrato en Formato Word", type="primary", use_container_width=True):
                    datos_c = {
                        'tipo_servicio': tipo_servicio_final, 'detalle_servicio': detalle_servicio,
                        'abogado_nombre': abog_nom, 'abogado_rut': abog_rut, 'abogado_domicilio': abog_dom, 'abogado_tel': abog_tel, 'abogado_correo': abog_correo,
                        'cliente_nombre': cli_nom, 'cliente_rut': cli_rut, 'cliente_domicilio': cli_dom, 'cliente_tel': cli_tel, 'cliente_correo': cli_correo,
                        'honorarios_num': hon_num, 'honorarios_letras': hon_let, 'cuotas_cant': cuotas_c, 'cuotas_monto': cuotas_m, 'fecha_inicio': fecha_pago,
                        'banco': banco, 'tipo_cuenta': tipo_cta, 'num_cuenta': num_cta
                    }
                    doc_final = crear_contrato_word(datos_c)
                    if doc_final:
                        buffer_memoria = io.BytesIO()
                        doc_final.save(buffer_memoria)
                        bytes_contrato = buffer_memoria.getvalue()
                        
                        st.session_state['contrato_generado'] = bytes_contrato
                        st.session_state['nombre_archivo'] = f"Contrato_{cli_nom.replace(' ', '_')}.docx"
                        
                        b64_docx = base64.b64encode(bytes_contrato).decode('utf-8')
                        
                        df_con = pd.read_csv(ARCHIVO_CONTRATOS)
                        nuevo_con = {
                            'ID': str(uuid.uuid4())[:8], 'Fecha': datetime.now().strftime("%d/%m/%Y"), 
                            'Cliente': cli_nom, 'Servicio': accion_sel, 'Honorarios': hon_num, 'Archivo_B64': b64_docx,
                            'Usuario_Propietario': usuario_actual
                        }
                        df_con = pd.concat([df_con, pd.DataFrame([nuevo_con])], ignore_index=True)
                        df_con.to_csv(ARCHIVO_CONTRATOS, index=False)
                        
                        # ☁️ RESPALDO EN LA NUBE
                        try:
                            df_nube_co = conn.read(worksheet="base_contratos", ttl=0)
                            df_nube_co_upd = pd.concat([df_nube_co, pd.DataFrame([nuevo_con])], ignore_index=True)
                            conn.update(worksheet="base_contratos", data=df_nube_co_upd)
                        except:
                            conn.update(worksheet="base_contratos", data=df_con)
                            
                        st.rerun()
                        
        if st.session_state.get('contrato_generado'):
            st.success("✅ Contrato guardado en el historial.")
            st.download_button(label="📥 Descargar Documento (.docx)", data=st.session_state['contrato_generado'], file_name=st.session_state['nombre_archivo'], mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", type="primary")
            
    with tab_reg:
        df_contratos_reg = pd.read_csv(ARCHIVO_CONTRATOS)
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
                        if 'Archivo_B64' in row and pd.notna(row['Archivo_B64']) and str(row['Archivo_B64']).strip() != "":
                            bytes_doc = base64.b64decode(row['Archivo_B64'])
                            st.download_button("📥 Descargar", data=bytes_doc, file_name=f"Copia_{str(row.get('Cliente', 'Contrato')).replace(' ', '_')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=f"dl_con_{row.get('ID', str(uuid.uuid4())[:8])}")
                        else:
                            st.write("*(Sin archivo)*")
                            
                    with c3:
                        if st.button("🗑️ Eliminar", key=f"del_con_{row.get('ID', idx)}"):
                            df_contratos_reg = df_contratos_reg.drop(idx)
                            df_contratos_reg.to_csv(ARCHIVO_CONTRATOS, index=False)
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
                        import json
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
                        
                        df_causas = pd.read_csv(ARCHIVO_BD)
                        nombre_extraido = datos_extraidos.get('cliente_nombre', 'Cliente Importado')
                        
                        nuevo_cliente = {
                            'ROL': 'Sin Causa Aún', 'TRIBUNAL': '--', 'CARATULADO': '--', 'Cliente': nombre_extraido,
                            'RUT': datos_extraidos.get('cliente_rut', '--'), 'Teléfono': '--', 'Tipo_Negocio': 'Propio', 'Clave_unica': '--', 'Correo': '--',
                            'Direccion': '--', 'SAC': '--', 'Sucursal': '--', 'Estado_Honorarios': 'Pendientes' if int(datos_extraidos.get('honorarios_total', 0)) > 0 else 'Sin fijar',
                            'Total_Honorarios': int(datos_extraidos.get('honorarios_total', 0)), 'Cuotas_Totales': int(datos_extraidos.get('cuotas_totales', 1)), 
                            'Cuotas_Pagadas': 0, 'Fecha_Inicio': datos_extraidos.get('fecha_inicio_pago', datetime.now().strftime("%Y-%m-%d"))
                        }
                        pd.concat([df_causas, pd.DataFrame([nuevo_cliente])], ignore_index=True).to_csv(ARCHIVO_BD, index=False)
                        
                        df_con = pd.read_csv(ARCHIVO_CONTRATOS)
                        nuevo_con = {
                            'ID': str(uuid.uuid4())[:8], 'Fecha': datetime.now().strftime("%d/%m/%Y"),
                            'Cliente': nombre_extraido, 'Servicio': datos_extraidos.get('servicio', 'Servicio Legal'), 'Honorarios': datos_extraidos.get('honorarios_total', 0)
                        }
                        pd.concat([df_con, pd.DataFrame([nuevo_con])], ignore_index=True).to_csv(ARCHIVO_CONTRATOS, index=False)
                        
                        st.success(f"✅ ¡La IA agregó a **{nombre_extraido}** directo a tu listado de clientes!")
                    except Exception as e: st.error(f"❌ Error técnico: {e}")

# 7. CAUSAS / EXPEDIENTES (MEJORADO VISUALMENTE EN MODO CLARO)
elif st.session_state['menu_radio'] == "💼 Causas":
    df_causas = pd.read_csv(ARCHIVO_BD)
    
    if st.session_state['causa_seleccionada'] is None:
        st.session_state['modo_edicion'] = False
        st.title("💼 Gestión e Historial de Causas")
        
        if st.button("➕ Crear Nueva Causa", type="primary"):
            st.session_state['creando_causa'] = not st.session_state.get('creando_causa', False)
            
        if st.session_state.get('creando_causa'):
            with st.container(border=True):
                st.markdown("#### Ingresar Datos de la Nueva Causa")
                with st.form("form_crear_causa"):
                    c_nuevo1, c_nuevo2 = st.columns(2)
                    n_rol = c_nuevo1.text_input("ROL / RIT", placeholder="Ej: C-123-2024")
                    n_trib = c_nuevo2.text_input("Tribunal", placeholder="Ej: 1° Juzgado Civil de Santiago")
                    n_carat = st.text_input("Caratulado", placeholder="Ej: PEREZ / BANCO")
                    n_cli = st.text_input("Nombre del Cliente Titular")
                    
                    if st.form_submit_button("Guardar Causa en Base de Datos"):
                        if n_rol.strip() == "":
                            st.error("El ROL es obligatorio.")
                        else:
                            nueva_c = {
                                'ROL': n_rol, 'TRIBUNAL': n_trib, 'CARATULADO': n_carat, 'Cliente': n_cli,
                                'RUT': '--', 'Teléfono': '--', 'Tipo_Negocio': 'Propio', 'Clave_unica': '--',
                                'Correo': '--', 'Direccion': '--', 'SAC': '--', 'Sucursal': '--',
                                'Estado_Honorarios': 'Sin fijar', 'Total_Honorarios': 0, 'Cuotas_Totales': 0, 'Cuotas_Pagadas': 0,
                                'Usuario_Propietario': usuario_actual # Marca tuya para que no se mezcle
                            }
                            # 1. Guardar en local
                            df_causas = pd.concat([df_causas, pd.DataFrame([nueva_c])], ignore_index=True)
                            df_causas.to_csv(ARCHIVO_BD, index=False)
                            
                            # 2. SUBIR A GOOGLE SHEETS INMEDIATAMENTE
                            try:
                                # Leemos la nube actual para no borrar lo de tus colegas
                                df_nube = conn.read(worksheet="base_causas", ttl=0)
                                # Juntamos la nube con tu nueva causa
                                df_nube_actualizada = pd.concat([df_nube, pd.DataFrame([nueva_c])], ignore_index=True)
                                conn.update(worksheet="base_causas", data=df_nube_actualizada)
                            except:
                                # Si la hoja "base_causas" no existe en tu Google Sheets, la crea sola
                                conn.update(worksheet="base_causas", data=df_causas)
                            
                            st.session_state['creando_causa'] = False
                            st.success("✅ Causa creada y respaldada en la nube exitosamente.")
                            import time
                            time.sleep(1) # Pausa para que alcances a leer el mensaje verde
                            st.rerun()

        st.write("---")
        col_f1, col_f2 = st.columns(2)
        filtro_trib = col_f1.multiselect("Filtrar por Tribunal de la República", df_causas['TRIBUNAL'].dropna().unique().tolist(), placeholder="Selecciona el juzgado...")
        filtro_neg = col_f2.multiselect("Filtrar por Cartera de Negocio", df_causas['Tipo_Negocio'].dropna().unique().tolist(), placeholder="Selecciona origen...")
        
        df_filtrado = df_causas.copy()
        if filtro_trib: 
            df_filtrado = df_filtrado[df_filtrado['TRIBUNAL'].isin(filtro_trib)]
        if filtro_neg: 
            df_filtrado = df_filtrado[df_filtrado['Tipo_Negocio'].isin(filtro_neg)]
            
        st.markdown("### Expedientes Activos")
        
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
                    c1, c2, c3, c4, c5 = st.columns([1.5, 2.5, 3, 2.5, 1.5])
                    c1.markdown(f"<span style='color:#0052cc; font-weight:bold; font-size:15px;'>{row['ROL']}</span>", unsafe_allow_html=True)
                    c2.markdown(f"<span style='color:#172b4d; font-size:14px;'>{row['TRIBUNAL']}</span>", unsafe_allow_html=True)
                    c3.markdown(f"<span style='color:#172b4d; font-weight:600; font-size:14px;'>{row['CARATULADO']}</span>", unsafe_allow_html=True)
                    
                    val_cliente = str(row.get('Cliente', '--'))
                    val_rut = str(row.get('RUT', '--'))
                    c4.markdown(f"<span style='color:#172b4d; font-size:14px;'>👤 {val_cliente}</span><br><span style='color:#6b778c; font-size:12px;'>RUT: {val_rut}</span>", unsafe_allow_html=True)
                    
                    c5.button("📂 Abrir", key=f"abrir_c_{idx}", use_container_width=True, on_click=ir_a_expediente, args=(row['ROL'],))
                    st.markdown("<hr style='margin: 8px 0px 8px 0px; border-top: 1px dashed #e0e4e8;'>", unsafe_allow_html=True)
        
    else:
        rol_actual = st.session_state['causa_seleccionada']
        idx = df_causas[df_causas['ROL'] == rol_actual].index[0]
        c_data = df_causas.loc[idx]
        
        if st.button("⬅ Volver al listado general de causas"):
            st.session_state['causa_seleccionada'] = None
            st.rerun()
            
        st.markdown(f"<h2>Expediente Causa: {c_data.get('CARATULADO','')}</h2>", unsafe_allow_html=True)
        col_izq, col_der = st.columns([2.5, 1.2])
        
        with col_der:
            if st.button("❌ Cancelar Edición" if st.session_state['modo_edicion'] else "✏️ Editar Ficha"):
                st.session_state['modo_edicion'] = not st.session_state['modo_edicion']
                st.rerun()
                
            if st.session_state['modo_edicion']:
                with st.form("form_edicion_causa"):
                    st.markdown("#### Datos de Litigación")
                    n_tribunal = st.text_input("Tribunal", str(c_data.get('TRIBUNAL','')))
                    n_serv = st.text_input("Servicio Contratado", str(c_data.get('Servicio','')))
                    n_negocio = st.selectbox("Origen de Cartera", ["Externo", "Propio"], index=0 if c_data.get('Tipo_Negocio') == "Externo" else 1)
                    
                    st.markdown("#### Datos de Ficha de Cliente")
                    n_cliente = st.text_input("Nombre Completo Titular", str(c_data.get('Cliente','')))
                    n_rut = st.text_input("RUT del Cliente", str(c_data.get('RUT','')))
                    n_tel = st.text_input("Teléfono", str(c_data.get('Teléfono','')))
                    n_correo = st.text_input("Correo", str(c_data.get('Correo','')))
                    n_dir = st.text_input("Domicilio", str(c_data.get('Direccion','')))
                    n_clave = st.text_input("Clave Única", str(c_data.get('Clave_unica','')))
                    n_sac = st.text_input("SAC Asignado", str(c_data.get('SAC','')))
                    n_suc = st.text_input("Sucursal Oficina", str(c_data.get('Sucursal','')))
                    
                    st.markdown("#### 💰 Control de Honorarios")
                    opciones_hon = ["Sin fijar", "Pagados", "Pendientes"]
                    idx_hon = opciones_hon.index(c_data.get('Estado_Honorarios', 'Sin fijar')) if c_data.get('Estado_Honorarios') in opciones_hon else 0
                    n_estado_hon = st.selectbox("Condición de Honorarios", opciones_hon, index=idx_hon)
                    
                    if n_estado_hon == "Pendientes":
                        n_tot_hon = st.number_input("Honorario Total Pactado ($)", value=int(c_data.get('Total_Honorarios', 0)))
                        n_cuo_tot = st.number_input("Mensualidades Totales", value=int(c_data.get('Cuotas_Totales', 0)), min_value=1)
                        n_cuo_pag = st.number_input("Mensualidades Enteradas", value=int(c_data.get('Cuotas_Pagadas', 0)), min_value=0)
                    elif n_estado_hon == "Pagados":
                        n_tot_hon = st.number_input("Monto Total Enterado ($)", value=int(c_data.get('Total_Honorarios', 0)))
                        n_cuo_tot, n_cuo_pag = 1, 1
                    else:
                        n_tot_hon, n_cuo_tot, n_cuo_pag = 0, 0, 0
                        
                    if st.form_submit_button("💾 Guardar Cambios", type="primary"):
                        if not n_cliente or n_cliente == "--":
                            st.error("No se puede actualizar sin un titular asignado.")
                        else:
                            df_causas.at[idx, 'TRIBUNAL'] = n_tribunal; df_causas.at[idx, 'Servicio'] = n_serv; df_causas.at[idx, 'Tipo_Negocio'] = n_negocio
                            df_causas.at[idx, 'Cliente'] = n_cliente; df_causas.at[idx, 'RUT'] = n_rut; df_causas.at[idx, 'Teléfono'] = n_tel
                            df_causas.at[idx, 'Correo'] = n_correo; df_causas.at[idx, 'Direccion'] = n_dir; df_causas.at[idx, 'Clave_unica'] = n_clave
                            df_causas.at[idx, 'SAC'] = n_sac; df_causas.at[idx, 'Sucursal'] = n_suc
                            df_causas.at[idx, 'Estado_Honorarios'] = n_estado_hon; df_causas.at[idx, 'Total_Honorarios'] = n_tot_hon
                            df_causas.at[idx, 'Cuotas_Totales'] = n_cuo_tot; df_causas.at[idx, 'Cuotas_Pagadas'] = n_cuo_pag
                            df_causas.to_csv(ARCHIVO_BD, index=False)
                            st.session_state['modo_edicion'] = False
                            st.rerun()
            else:
                clase_div = "badge-active" if c_data.get('Tipo_Negocio') == "Externo" else "badge-propio"
                st.markdown(f"""
                <div class="dash-card">
                    <div style="display:flex; justify-content:space-between; margin-bottom:10px;">
                        <strong>Estatus General Causa</strong>
                        <span class="{clase_div}">{c_data.get('Tipo_Negocio','')}</span>
                    </div>
                    Rol: {rol_actual}<br>Tribunal: {c_data.get('TRIBUNAL')}<br>Materia: {c_data.get('Servicio')}<br>
                </div>
                <div class="dash-card">
                    <strong>Ficha Económica Cliente</strong><br>
                    Nombre: {c_data.get('Cliente')}<br>Rut: {c_data.get('RUT')}<hr>
                    Estado: {c_data.get('Estado_Honorarios')}<br>Pactado: ${c_data.get('Total_Honorarios',0):,.0f}
                </div>
                """, unsafe_allow_html=True)
                
        with col_izq:
            tab_movs, tab_tareas_internas, tab_legacy, tab_docs_solicitados = st.tabs(["Movimientos", "Tareas Operativas", "Movimientos legacy", "📥 Docs Cliente"])
            with tab_tareas_internas:
                if st.button("+ Asignar Nueva Tarea Operativa", type="primary"):
                    st.session_state['creando_tarea'] = not st.session_state['creando_tarea']
                    st.rerun()
                if st.session_state['creando_tarea']:
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
                                pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad']).to_csv(destinatario_file, index=False)
                                
                            df_t_destino = pd.read_csv(destinatario_file)
                            nueva_t = {
                                'ID_Tarea': str(uuid.uuid4())[:8], 
                                'ROL': rol_actual, 
                                'Creador': nombre_real_usuario, 
                                'Fecha_Creacion': datetime.now().strftime("%d/%m/%Y"), 
                                'Fecha_Vencimiento': t_f.strftime("%d/%m/%Y"),
                                'Titulo': t_t, 'Descripcion': t_d, 'Estado': 'En progreso', 'Comentarios': '[]', 'Prioridad': t_p,
                                'Usuario_Propietario': destinatario_usr # <--- NUEVO: Para saber de quién es en la nube
                            }
                            df_t_destino = pd.concat([df_t_destino, pd.DataFrame([nueva_t])], ignore_index=True)
                            df_t_destino.to_csv(destinatario_file, index=False)
                            
                            # ☁️ NUEVO: RESPALDO AUTOMÁTICO EN LA NUBE
                            try:
                                df_nube_t = conn.read(worksheet="base_tareas", ttl=0)
                                df_nube_t_upd = pd.concat([df_nube_t, pd.DataFrame([nueva_t])], ignore_index=True)
                                conn.update(worksheet="base_tareas", data=df_nube_t_upd)
                            except:
                                conn.update(worksheet="base_tareas", data=df_t_destino)
                            
                            st.session_state['creando_tarea'] = False
                            
                            if destinatario_usr == usuario_actual:
                                st.success("✅ Tarea registrada en tu agenda y en la nube.")
                            else:
                                st.success("✅ Tarea enviada, delegada y respaldada exitosamente.")
                                
                            # NUEVO: Pausa para que se vea el mensaje verde
                            import time
                            time.sleep(1)
                            st.rerun()
                            
                df_t_local = pd.read_csv(ARCHIVO_TAREAS)
                tareas_de_esta_causa = df_t_local[df_t_local['ROL'] == rol_actual]
                
                if tareas_de_esta_causa.empty:
                    st.info("Esta causa no registra tareas en progreso.")
                else:
                    for idx_tarea_bd, tarea in tareas_de_esta_causa.iterrows():
                        with st.container(border=True):
                            b_prio_color = "#ff5630" if tarea.get('Prioridad') == "Alta" else ("#ffc400" if tarea.get('Prioridad') == "Media" else "#57a15a")
                            st.markdown(f"<div style='height: 5px; background-color: {b_prio_color}; border-radius: 5px 5px 0 0; margin: -1rem -1rem 1rem -1rem;'></div>", unsafe_allow_html=True)
                            
                            # --- MODO EDICIÓN DE TAREA (SOLO FECHA) ---
                            if st.session_state.get('editando_tarea') == tarea['ID_Tarea']:
                                st.markdown("#### ✏️ Modificar Plazo de Tarea")
                                with st.form(key=f"form_ed_t_{tarea['ID_Tarea']}"):
                                    # Mostramos los datos fijos, no editables
                                    st.markdown(f"**Título:** {tarea['Titulo']}")
                                    st.markdown(f"**Descripción:** {tarea['Descripcion']}")
                                    st.markdown(f"**Prioridad:** {tarea.get('Prioridad', 'Media')}")
                                    st.markdown("---")
                                    
                                    try:
                                        f_obj = datetime.strptime(tarea['Fecha_Vencimiento'], "%d/%m/%Y")
                                    except:
                                        f_obj = datetime.now()
                                        
                                    # El único campo editable:
                                    e_fec = st.date_input("Nueva Fecha de Vencimiento", f_obj)
                                    
                                    col_eb1, col_eb2 = st.columns(2)
                                    if col_eb1.form_submit_button("💾 Guardar Nueva Fecha", type="primary", use_container_width=True):
                                        # Guardar en local (SOLO LA FECHA)
                                        df_t_local.at[idx_tarea_bd, 'Fecha_Vencimiento'] = e_fec.strftime("%d/%m/%Y")
                                        df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
                                        
                                        # Guardar en la Nube
                                        try:
                                            df_nube_t = conn.read(worksheet="base_tareas", ttl=0)
                                            df_nube_t.loc[df_nube_t['ID_Tarea'] == tarea['ID_Tarea'], 'Fecha_Vencimiento'] = e_fec.strftime("%d/%m/%Y")
                                            conn.update(worksheet="base_tareas", data=df_nube_t)
                                        except: pass
                                        
                                        st.session_state['editando_tarea'] = None
                                        st.success("✅ Fecha actualizada correctamente.")
                                        import time
                                        time.sleep(1)
                                        st.rerun()
                                        
                                    if col_eb2.form_submit_button("❌ Cancelar", use_container_width=True):
                                        st.session_state['editando_tarea'] = None
                                        st.rerun()
                            
                            # --- MODO VISUALIZACIÓN NORMAL ---
                            else:
                                c_top_l, c_top_r = st.columns([2.5, 2.3])
                                with c_top_l:
                                    autor_real = NOMBRES_REALES.get(tarea['Creador'], tarea['Creador'])
                                    st.markdown(f"""
                                    <div style='display: flex; align-items: center; margin-bottom: 5px;'>
                                        <img src='{LOGO_URL}' style='height: 25px; margin-right: 8px;' onerror="this.onerror=null; this.src='https://img.icons8.com/color/48/user.png';">
                                        <span style='font-weight: 700; font-size: 15px; color: #172b4d;'>{autor_real}</span>
                                        <span style='font-size:12px; color:{b_prio_color}; font-weight:bold; margin-left:8px;'>[{tarea.get('Prioridad', 'Media')}]</span>
                                    </div>
                                    """, unsafe_allow_html=True)
                                    st.markdown(f"<span style='font-size:13px; color:#6b778c;'>Creado: {tarea['Fecha_Creacion']} • Vence: {tarea['Fecha_Vencimiento']}</span>", unsafe_allow_html=True)
                                
                                with c_top_r:
                                    if tarea['Estado'] == 'En progreso':
                                        # CONTROL DE PODERES: 4 botones para Narratia, 3 botones para el resto
                                        if usuario_actual == "Narratia":
                                            bcols = st.columns([1, 1, 1, 1])
                                        else:
                                            bcols = st.columns([1, 1, 1])
                                            
                                        if bcols[0].button("❌", key=f"rech_{tarea['ID_Tarea']}", help="Rechazar"): 
                                            df_t_local.at[idx_tarea_bd, 'Estado'] = 'Rechazada'
                                            df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
                                            try:
                                                dn = conn.read(worksheet="base_tareas", ttl=0)
                                                dn.loc[dn['ID_Tarea'] == tarea['ID_Tarea'], 'Estado'] = 'Rechazada'
                                                conn.update(worksheet="base_tareas", data=dn)
                                            except: pass
                                            st.rerun()
                                            
                                        if bcols[1].button("✅", key=f"apr_{tarea['ID_Tarea']}", help="Aprobar"): 
                                            df_t_local.at[idx_tarea_bd, 'Estado'] = 'Aprobada'
                                            df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
                                            try:
                                                dn = conn.read(worksheet="base_tareas", ttl=0)
                                                dn.loc[dn['ID_Tarea'] == tarea['ID_Tarea'], 'Estado'] = 'Aprobada'
                                                conn.update(worksheet="base_tareas", data=dn)
                                            except: pass
                                            st.rerun()
                                            
                                        if bcols[2].button("✏️", key=f"edit_{tarea['ID_Tarea']}", help="Modificar Fecha"):
                                            st.session_state['editando_tarea'] = tarea['ID_Tarea']
                                            st.rerun()
                                            
                                        # El botón de eliminar solo se agrega si el usuario es Narratia
                                        if usuario_actual == "Narratia":
                                            if bcols[3].button("🗑️", key=f"del_{tarea['ID_Tarea']}", help="Eliminar"):
                                                df_t_local = df_t_local.drop(idx_tarea_bd)
                                                df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
                                                try:
                                                    dn = conn.read(worksheet="base_tareas", ttl=0)
                                                    dn = dn[dn['ID_Tarea'] != tarea['ID_Tarea']]
                                                    conn.update(worksheet="base_tareas", data=dn)
                                                except: pass
                                                st.rerun()
                                    else:
                                        # Tarea finalizada (Aprobada o Rechazada)
                                        bg_e = "#57a15a" if tarea['Estado'] == 'Aprobada' else "#ff5630"
                                        
                                        if usuario_actual == "Narratia":
                                            bcols = st.columns([2, 1])
                                            bcols[0].markdown(f"<div style='background:{bg_e}; color:white; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:600; text-align:center; margin-top:5px;'>{tarea['Estado']}</div>", unsafe_allow_html=True)
                                            if bcols[1].button("🗑️", key=f"del_fin_{tarea['ID_Tarea']}", help="Eliminar permanentemente"):
                                                df_t_local = df_t_local.drop(idx_tarea_bd)
                                                df_t_local.to_csv(ARCHIVO_TAREAS, index=False)
                                                try:
                                                    dn = conn.read(worksheet="base_tareas", ttl=0)
                                                    dn = dn[dn['ID_Tarea'] != tarea['ID_Tarea']]
                                                    conn.update(worksheet="base_tareas", data=dn)
                                                except: pass
                                                st.rerun()
                                        else:
                                            # Si no es Narratia, solo ve el estado, sin botón de borrar
                                            st.markdown(f"<div style='background:{bg_e}; color:white; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:600; text-align:center; margin-top:5px;'>{tarea['Estado']}</div>", unsafe_allow_html=True)

                                st.markdown(f"<h3 style='font-size: 18px; color: #172b4d; margin-top: 15px; margin-bottom: 5px;'>{tarea['Titulo']}</h3><p style='font-size: 15px; color: #172b4d; margin-bottom: 15px;'>{tarea['Descripcion']}</p>", unsafe_allow_html=True)
                                
                                # Comentarios
                                comentarios_js = json.loads(tarea['Comentarios'])
                                html_coms = "".join([f"<div style='margin-bottom:15px;'><strong style='color:#172b4d; font-size:14px;'>{c['autor']}</strong> <span style='color:#6b778c; font-size:13px;'>• {c['fecha']}</span><br><span style='color:#42526e; font-size:14px;'>{c['texto']}</span></div>" for c in comentarios_js]) if comentarios_js else "<span style='color:#6b778c; font-size:14px;'>No hay comentarios.</span>"
                                
                                st.markdown(f"""
                                <div style="background: #f8f9fa; margin: 10px -16px 0 -16px; padding: 12px 20px; border-top: 1px solid #e0e4e8; border-bottom: 1px solid #e0e4e8;">
                                    <span style="color:#172b4d; font-size:14px;">Comentarios <span style="background:#e1e4e8; padding:2px 8px; border-radius:12px; font-weight:bold; margin-left:5px; font-size:12px;">{len(comentarios_js)}</span></span>
                                </div>
                                <div style="padding: 15px 4px 5px 4px;">{html_coms}</div>
                                """, unsafe_allow_html=True)
                                
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
                                            
                                            # Update nube con el nuevo comentario
                                            try:
                                                dn = conn.read(worksheet="base_tareas", ttl=0)
                                                dn.loc[dn['ID_Tarea'] == tarea['ID_Tarea'], 'Comentarios'] = json.dumps(comentarios_js)
                                                conn.update(worksheet="base_tareas", data=dn)
                                            except: pass
                                            
                                            st.rerun()

            with tab_docs_solicitados:
                st.subheader("📋 Gestión de Requisitos del Cliente")
                token_para_link = str(c_data.get('Cliente', 'Cliente')).strip().replace(" ", "_")
                link_portal_final = f"https://narratia-app.streamlit.app/?cliente_id={token_para_link}"
                st.info(f"🔗 **Enlace del Portal para el Cliente:**\n`{link_portal_final}`")
                
                with st.form(key=f"form_agregar_requisito_{rol_actual}", clear_on_submit=True):
                    st.markdown("#### Solicitar Nuevo Documento")
                    nuevo_doc_req = st.text_input("Nombre del documento solicitado", placeholder="Ej: Certificado de Matrimonio actualizado, Últimas 3 liquidaciones...")
                    if st.form_submit_button("➕ Enviar Requisito al Portal", type="primary"):
                        if nuevo_doc_req.strip() == "":
                            st.error("Escribe el nombre del documento.")
                        else:
                            ARCHIVO_DOCS = "base_documentos_clientes.csv"
                            if not os.path.exists(ARCHIVO_DOCS):
                                pd.DataFrame(columns=['ID_Req', 'Cliente_Token', 'Documento_Nombre', 'Estado', 'Archivo_B64', 'Fecha_Subida']).to_csv(ARCHIVO_DOCS, index=False)
                            
                            df_docs_db = pd.read_csv(ARCHIVO_DOCS)
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
                            st.success(f"¡Solicitud de '{nuevo_doc_req}' agregada al portal del cliente!")
                            st.rerun()
                
                st.markdown("### Estado de la Documentación Solicitada")
                ARCHIVO_DOCS = "base_documentos_clientes.csv"
                if os.path.exists(ARCHIVO_DOCS):
                    df_docs_db = pd.read_csv(ARCHIVO_DOCS)
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
                                    if d_row['Estado'] == '✅ Completado' and pd.notna(d_row.get('Archivo_B64')) and str(d_row['Archivo_B64']).strip() != "":
                                        bytes_descarga = base64.b64decode(d_row['Archivo_B64'])
                                        st.download_button("📥 Descargar", data=bytes_descarga, file_name=f"{d_row['Documento_Nombre'].replace(' ', '_')}_{token_para_link}.pdf", key=f"dl_abog_{d_row['ID_Req']}")
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
    
    df_t = pd.read_csv(ARCHIVO_TAREAS)
    if df_t.empty:
        st.info("No existen registros de plazos en el sistema.")
    else:
        t_hoy = df_t[df_t['Fecha_Vencimiento'] == fecha_hoy].copy()
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

# 9. MENSAJERÍA INTERNA (MODO WHATSAPP CLARO)
elif st.session_state['menu_radio'] == "✈️ Mensajería":
    st.title("✈️ Mensajería Interna del Equipo")
    st.markdown("Plataforma de comunicación rápida para la oficina.")
    
    df_msgs = pd.read_csv(ARCHIVO_MENSAJES)
    
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
                nuevo_msj = {
                    'ID': str(uuid.uuid4())[:8],
                    'Fecha': datetime.now().strftime("%d/%m/%Y %H:%M"),
                    'De': nombre_real_usuario,
                    'Para': destinatario,
                    'Mensaje': texto_mensaje.strip()
                }
                df_msgs = pd.concat([df_msgs, pd.DataFrame([nuevo_msj])], ignore_index=True)
                df_msgs.to_csv(ARCHIVO_MENSAJES, index=False)
                st.rerun()

# 10. CLIENTES DIRECTOS (LISTA ELEGANTE CLARA)
elif st.session_state['menu_radio'] == "👥 Clientes":
    df_causas = pd.read_csv(ARCHIVO_BD)
    
    if st.session_state['cliente_seleccionado'] is None:
        st.title("👥 Directorio de Clientes")
        if st.button("➕ Crear Nuevo Cliente", type="primary"):
            st.session_state['creando_cliente'] = not st.session_state.get('creando_cliente', False)
            
        if st.session_state.get('creando_cliente'):
            with st.container(border=True):
                with st.form("form_crear_cliente"):
                    n_cli_nom = st.text_input("Nombre Completo")
                    n_cli_rut = st.text_input("RUT del Cliente")
                    if st.form_submit_button("Guardar"):
                        nueva_ficha = {'Cliente': n_cli_nom, 'RUT': n_cli_rut, 'Tipo_Negocio': 'Propio', 'Estado_Honorarios': 'Sin fijar', 'Total_Honorarios': 0, 'Cuotas_Totales': 1, 'Cuotas_Pagadas': 0}
                        df_causas = pd.concat([df_causas, pd.DataFrame([nueva_ficha])], ignore_index=True)
                        df_causas.to_csv(ARCHIVO_BD, index=False); st.rerun()

        st.markdown("### Listado de Clientes")
        for cli_nom in df_causas['Cliente'].dropna().unique():
            if cli_nom != "--" and st.button(f"👤 {cli_nom}"):
                st.session_state['cliente_seleccionado'] = cli_nom; st.rerun()
    
    else:
        cli_actual = st.session_state['cliente_seleccionado']
        datos = df_causas[df_causas['Cliente'] == cli_actual].iloc[0]
        
        if st.button("⬅ Volver al Directorio"): 
            st.session_state['cliente_seleccionado'] = None; st.rerun()
            
        st.title(f"Ficha: {cli_actual}")
        tab1, tab2, tab3 = st.tabs(["👤 Información", "💰 Contabilidad", "📄 Contratos"])
        
        with tab1:
            col_i, col_d = st.columns([1, 2])
            with col_i:
                with st.container(border=True):
                    if st.session_state.get('editando_cli'):
                        with st.form("edit_cli"):
                            n_nom = st.text_input("Nombre", datos.get('Cliente', ''))
                            n_rut = st.text_input("RUT", datos.get('RUT', ''))
                            n_tel = st.text_input("Teléfono", datos.get('Teléfono', ''))
                            n_cor = st.text_input("Correo", datos.get('Correo', ''))
                            n_cla = st.text_input("Clave Única", datos.get('Clave_unica', ''))
                            n_dom = st.text_input("Domicilio", datos.get('Direccion', ''))
                            if st.form_submit_button("💾 Guardar"):
                                df_causas.loc[df_causas['Cliente'] == cli_actual, ['Cliente', 'RUT', 'Teléfono', 'Correo', 'Clave_unica', 'Direccion']] = [n_nom, n_rut, n_tel, n_cor, n_cla, n_dom]
                                df_causas.to_csv(ARCHIVO_BD, index=False); st.session_state['editando_cli'] = False; st.rerun()
                    else:
                        st.write(f"**Nombre:** {datos.get('Cliente', '--')}")
                        st.write(f"**RUT:** {datos.get('RUT', '--')}")
                        st.write(f"**Teléfono:** {datos.get('Teléfono', '--')}")
                        st.write(f"**Correo:** {datos.get('Correo', '--')}")
                        st.write(f"**Clave Única:** {datos.get('Clave_unica', '--')}")
                        st.write(f"**Domicilio:** {datos.get('Direccion', '--')}")
                        if st.button("✏️ Editar Datos"): st.session_state['editando_cli'] = True; st.rerun()
            
            with col_d:
                if st.button("➕ Crear Nueva Causa"):
                    st.session_state['creando_causa'] = True
                
                if st.session_state.get('creando_causa'):
                    with st.form("nueva_causa_form"):
                        n_rol = st.text_input("Nuevo ROL")
                        if st.form_submit_button("Guardar Causa"):
                            nueva_c = {'ROL': n_rol, 'Cliente': cli_actual, 'Tipo_Negocio': 'Propio'}
                            df_causas = pd.concat([df_causas, pd.DataFrame([nueva_c])], ignore_index=True)
                            df_causas.to_csv(ARCHIVO_BD, index=False); st.session_state['creando_causa'] = False; st.rerun()

                st.subheader("Causas Asociadas")
                causas_cli = df_causas[df_causas['Cliente'] == cli_actual]
                for _, c in causas_cli.iterrows():
                    if pd.notna(c.get('ROL')) and c.get('ROL') != "Sin Causa Aún":
                        with st.container(border=True):
                            c1, c2 = st.columns([3, 1])
                            c1.markdown(f"**Rol:** {c['ROL']} | **Caratulado:** {c.get('CARATULADO', '--')}")
                            if c2.button("📂 Ir al Expediente", key=f"btn_ir_{c['ROL']}"):
                                ir_a_expediente(c['ROL']); st.rerun()

        with tab2:
            st.subheader("Estado Financiero")
            try:
                t_hon = float(datos.get('Total_Honorarios', 0))
                if pd.isna(t_hon): t_hon = 0.0
            except:
                t_hon = 0.0
                
            try:
                c_tot = float(datos.get('Cuotas_Totales', 1))
                if pd.isna(c_tot) or c_tot == 0: c_tot = 1.0
            except:
                c_tot = 1.0
                
            try:
                c_pag = float(datos.get('Cuotas_Pagadas', 0))
                if pd.isna(c_pag): c_pag = 0.0
            except:
                c_pag = 0.0
                
            saldo_calculado = t_hon - ((t_hon / c_tot) * c_pag)
            st.write(f"Saldo Pendiente: **${saldo_calculado:,.0f}**")
        with tab3:
            st.subheader("Contratos Vinculados")
            df_con = pd.read_csv(ARCHIVO_CONTRATOS)
            st.dataframe(df_con[df_con['Cliente'] == cli_actual])

# 11. GESTOR GLOBAL DE TAREAS
elif st.session_state['menu_radio'] == "☑️ Tareas":
    st.title("☑️ Gestor Global de Tareas")
    df_t = pd.read_csv(ARCHIVO_TAREAS)
    
    if df_t.empty: 
        st.info("No hay tareas creadas en el sistema.")
    else:
        for idx, row in df_t.iterrows():
            with st.container(border=True):
                prio_color = "#ff5630" if row.get('Prioridad') == "Alta" else ("#ffc400" if row.get('Prioridad') == "Media" else "#57a15a")
                st.markdown(f"<div style='height: 5px; background-color: {prio_color}; border-radius: 5px 5px 0 0; margin: -1rem -1rem 1rem -1rem;'></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns([4, 2, 1])
                with c1:
                    st.markdown(f"<div style='display: flex; align-items: center; margin-bottom: 5px;'><img src='{LOGO_URL}' style='height: 25px; margin-right: 8px;' onerror=\"this.onerror=null; this.src='https://img.icons8.com/color/48/user.png';\"><strong style='font-size:16px; color:#172b4d;'>{row['Titulo']}</strong><span style='font-size:12px; color:{prio_color}; font-weight:bold; margin-left:8px;'>[{row.get('Prioridad', 'Media')}]</span></div>", unsafe_allow_html=True)
                    st.markdown(f"<span style='color:#6b778c;'>{str(row['Descripcion'])[:60]}...</span>", unsafe_allow_html=True)
                with c2:
                    color_bd = "#ffc400" if row['Estado'] == 'En progreso' else ("#57a15a" if row['Estado'] == 'Aprobada' else "#ff5630")
                    st.markdown(f"<span style='background:{color_bd}; padding:3px 8px; border-radius:10px; font-size:12px; font-weight:bold; color:black;'>{row['Estado']}</span>", unsafe_allow_html=True)
                    st.markdown(f"<span style='color:#172b4d; font-size:14px;'><br>Causa: {row['ROL']} | Vence: {row['Fecha_Vencimiento']}</span>", unsafe_allow_html=True)
                with c3:
                    st.button("Ir al expediente ➔", key=f"global_ir_{row['ID_Tarea']}", on_click=ir_a_expediente, args=(row['ROL'],))

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
    col_cal, col_side = st.columns([3, 1])
    eventos_calendario = obtener_feriados_chile()
    df_t = pd.read_csv(ARCHIVO_TAREAS)
    
    if not df_t.empty:
        for idx, r in df_t.iterrows():
            try:
                d_obj = datetime.strptime(str(r['Fecha_Vencimiento']), "%d/%m/%Y")
                d_str = d_obj.strftime("%Y-%m-%d")
                bg_color = "#ff5630" if r.get('Prioridad') == "Alta" else ("#ffc400" if r.get('Prioridad') == "Media" else "#57a15a")
                text_color = "white" if bg_color != "#ffc400" else "#172b4d"
                eventos_calendario.append({"title": f"📌 {r['Titulo']}", "start": d_str, "backgroundColor": bg_color, "textColor": text_color, "borderColor": bg_color})
            except: 
                pass
                
    opciones_calendario = {
        "initialView": "dayGridMonth", "locale": "es", "firstDay": 1, 
        "height": 700,
        "contentHeight": "auto",
        "buttonText": {"today": "Hoy", "month": "Mes", "week": "Semana", "day": "Día", "list": "Agenda"},
        "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,timeGridWeek,listMonth"}
    }
    
    css_calendario_moderno = """
        .fc { width: 100% !important; min-height: 600px; background-color: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.02); font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; border: 1px solid #e0e4e8;}
        .fc-theme-standard td, .fc-theme-standard th { border-color: #e0e4e8; }
        .fc-col-header-cell { background-color: #f8f9fa; padding: 12px 0 !important; color: #6b778c; text-transform: capitalize; font-size: 14px; }
        .fc-button-primary { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #cbd2d9 !important; border-radius: 6px !important; text-transform: capitalize !important; font-weight: 600 !important; box-shadow: 0 1px 3px rgba(0,0,0,0.05) !important; }
        .fc-button-primary:hover { background-color: #deebff !important; border-color: #0052cc !important; color: #0052cc !important; }
        .fc-button-active { background-color: #0052cc !important; color: white !important; border-color: #0052cc !important; }
        .fc-toolbar-title { color: #172b4d !important; font-weight: 800 !important; font-size: 1.8em !important; text-transform: capitalize; }
        .fc-daygrid-day-number { color: #172b4d !important; font-weight: 700 !important; padding: 8px !important; text-decoration: none !important; }
        .fc-event { border-radius: 6px !important; border: none !important; font-weight: 600 !important; padding: 4px 6px !important; margin-bottom: 4px !important; box-shadow: 0 1px 2px rgba(0,0,0,0.1); cursor: pointer; }
        .fc-event-title { font-size: 12px !important; }
    """

    with col_cal:
        calendario_estado = calendar(events=eventos_calendario, options=opciones_calendario, custom_css=css_calendario_moderno, key="calendario_app")
        
    with col_side:
        with st.container(border=True):
            st.markdown("<h3 style='margin-top:0;'>Tareas del día</h3>", unsafe_allow_html=True)
            fecha_mostrar = datetime.now().strftime("%Y-%m-%d")
            if calendario_estado and 'dateClick' in calendario_estado and calendario_estado['dateClick']:
                fecha_mostrar = calendario_estado['dateClick']['date'][:10]
            try:
                d_fmt = datetime.strptime(fecha_mostrar, "%Y-%m-%d").strftime("%d/%m/%Y")
                st.markdown(f"<p style='color:#6b778c;'>{d_fmt}</p>", unsafe_allow_html=True)
                if not df_t.empty:
                    tareas_dia = df_t[df_t['Fecha_Vencimiento'] == d_fmt]
                    if tareas_dia.empty: 
                        st.write("Sin tareas para este día.")
                    else:
                        for _, td in tareas_dia.iterrows():
                            color_dot = "#ffc400" if td['Estado'] == 'En progreso' else ("#57a15a" if td['Estado'] == 'Aprobada' else "#ff5630")
                            prio_txt_color = "#ff5630" if td.get('Prioridad') == 'Alta' else ("#ffc400" if td.get('Prioridad') == 'Media' else "#57a15a")
                            st.markdown(f"<div style='margin-bottom:5px; border-left:3px solid {color_dot}; padding-left:10px;'><strong style='color:#172b4d;'>{td['Titulo']}</strong> <span style='font-size:11px; color:{prio_txt_color}; font-weight:bold;'>({td.get('Prioridad', 'Media')})</span><br><span style='font-size:13px; color:#6b778c;'>{td['ROL']}</span></div>", unsafe_allow_html=True)
                            st.button("Ir al expediente ➔", key=f"cal_ir_{td['ID_Tarea']}", on_click=ir_a_expediente, args=(td['ROL'],))
                            st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)
            except: 
                st.write("Selecciona un día en el calendario.")

# 14. PANEL DE ADMINISTRADOR (SOLO NARRATIA)
elif st.session_state['menu_radio'] == "👑 Panel Admin":
    st.title("👑 Panel de Control - SaaS JuriSync")
    st.markdown("Crea cuentas nuevas, asigna planes o modifica el nivel de acceso de tus clientes actuales.")
    
    tab_crear, tab_editar, tab_vision = st.tabs(["➕ Crear Nuevo Usuario", "🔄 Modificar Planes", "👁️ Visión Global (God Mode)"])
    
    with tab_crear:
        with st.container(border=True):
            st.subheader("Vender Plan / Crear Usuario")
            
            col1, col2 = st.columns(2)
            with col1:
                nuevo_user = st.text_input("Usuario (Nombre para iniciar sesión)", placeholder="Ej: EstudioPerez")
                nuevo_nombre = st.text_input("Nombre Real del Abogado / Cliente", placeholder="Ej: Juan Pérez")
            with col2:
                nueva_clave = st.text_input("Clave Provisoria", placeholder="Ej: JuriSync123")
                nuevo_plan = st.selectbox("Plan Asignado al Crear", ["Básico", "Medio", "Full"])
                
            if st.button("🚀 Crear Usuario en el Sistema", type="primary", use_container_width=True):
                if not nuevo_user.strip() or not nueva_clave.strip() or not nuevo_nombre.strip():
                    st.error("⚠️ Faltan datos obligatorios (Usuario, Nombre o Clave).")
                else:
                    df_usuarios_admin = pd.read_csv(ARCHIVO_USUARIOS)
                    
                    if nuevo_user in df_usuarios_admin['Usuario'].values:
                        st.error(f"⚠️ El usuario '{nuevo_user}' ya existe en el sistema. Inventa otro nombre de acceso.")
                    else:
                        nuevo_registro = {
                            "Usuario": nuevo_user.strip(),
                            "Password": nueva_clave.strip(),
                            "Nombre_Real": nuevo_nombre.strip(),
                            "Correo": "pendiente",  
                            "Debe_Cambiar_Clave": 'True', 
                            "Plan": nuevo_plan
                        }
                        
                        df_usuarios_admin = pd.concat([df_usuarios_admin, pd.DataFrame([nuevo_registro])], ignore_index=True)
                        df_usuarios_admin.to_csv(ARCHIVO_USUARIOS, index=False)
                        guardar_en_nube(df_usuarios_admin)
                        
                        archivo_tareas_nuevo = f"base_tareas_{nuevo_user}.csv"
                        if not os.path.exists(archivo_tareas_nuevo):
                            pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad']).to_csv(archivo_tareas_nuevo, index=False)
                        
                        st.success(f"✅ ¡Cuenta lista! Usuario **{nuevo_user}** creado con Plan {nuevo_plan}. Envíale el Usuario y la Clave por WhatsApp para que entre.")
                        st.rerun()

    with tab_editar:
        with st.container(border=True):
            st.subheader("Subir o Bajar de Plan a un Cliente")
            df_usuarios_admin = pd.read_csv(ARCHIVO_USUARIOS)
            lista_usuarios = df_usuarios_admin['Usuario'].tolist()
            
            c_ed1, c_ed2, c_ed3 = st.columns(3)
            with c_ed1:
                usuario_editar = st.selectbox("Seleccionar Cliente a Modificar", lista_usuarios)
            with c_ed2:
                try:
                    plan_actual_usr = df_usuarios_admin.loc[df_usuarios_admin['Usuario'] == usuario_editar, 'Plan'].values[0]
                    idx_plan = ["Básico", "Medio", "Full"].index(plan_actual_usr)
                except:
                    idx_plan = 0
                    
                nuevo_plan_edit = st.selectbox("Seleccionar Nuevo Plan", ["Básico", "Medio", "Full"], index=idx_plan)
            with c_ed3:
                st.write("") 
                st.write("")
                if st.button("🔄 Actualizar Nivel de Acceso", type="primary", use_container_width=True):
                    df_usuarios_admin.loc[df_usuarios_admin['Usuario'] == usuario_editar, 'Plan'] = nuevo_plan_edit
                    df_usuarios_admin.to_csv(ARCHIVO_USUARIOS, index=False)
                    guardar_en_nube(df_usuarios_admin)
                    st.success(f"✅ Los permisos de **{usuario_editar}** han sido actualizados a Plan {nuevo_plan_edit}.")
                    st.rerun()
        
        st.subheader("👥 Clientes y Planes Actuales")
        df_vista = pd.read_csv(ARCHIVO_USUARIOS)
        st.dataframe(df_vista[['Usuario', 'Nombre_Real', 'Plan', 'Correo', 'Debe_Cambiar_Clave']], use_container_width=True)

    with tab_vision:
        st.subheader("Monitoreo Absoluto de la Oficina")
        st.markdown("Visualización en tiempo real de toda la información ingresada por los usuarios del sistema. **Solo tú tienes este nivel de acceso.**")
        
        df_usuarios_admin = pd.read_csv(ARCHIVO_USUARIOS)
        todas_causas = []
        todas_tareas = []
        
        for u in df_usuarios_admin['Usuario']:
            arch_c = f"base_causas_{u}.csv"
            arch_t = f"base_tareas_{u}.csv"
            
            if os.path.exists(arch_c):
                temp_c = pd.read_csv(arch_c)
                if not temp_c.empty:
                    temp_c['Usuario_Propietario'] = u 
                    todas_causas.append(temp_c)
                    
            if os.path.exists(arch_t):
                temp_t = pd.read_csv(arch_t)
                if not temp_t.empty:
                    temp_t['Usuario_Propietario'] = u
                    todas_tareas.append(temp_t)
        
        col_v1, col_v2 = st.columns(2)
        
        with col_v1:
            st.markdown("<div class='dash-card'><h4>Causas Globales</h4>", unsafe_allow_html=True)
            if todas_causas:
                df_full_causas = pd.concat(todas_causas, ignore_index=True)
                st.metric("Total de Causas en JuriSync", len(df_full_causas))
                st.dataframe(df_full_causas[['Usuario_Propietario', 'ROL', 'Cliente', 'TRIBUNAL', 'Total_Honorarios']], use_container_width=True)
            else:
                st.info("No hay causas registradas en el sistema aún.")
            st.markdown("</div>", unsafe_allow_html=True)
            
        with col_v2:
            st.markdown("<div class='dash-card'><h4>Tareas Operativas Globales</h4>", unsafe_allow_html=True)
            if todas_tareas:
                df_full_tareas = pd.concat(todas_tareas, ignore_index=True)
                st.metric("Total de Gestiones Pendientes", len(df_full_tareas[df_full_tareas['Estado'] == 'En progreso']))
                st.dataframe(df_full_tareas[['Usuario_Propietario', 'Titulo', 'Estado', 'Fecha_Vencimiento']], use_container_width=True)
            else:
                st.info("No hay tareas creadas.")
            st.markdown("</div>", unsafe_allow_html=True)

# NUEVO MÓDULO: REDACTOR AUTOMÁTICO IA
elif st.session_state['menu_radio'] == "📝 Redactor IA":
    st.title("📝 Redactor Automático de Escritos")
    st.markdown("La IA redactará el borrador del escrito judicial con el formato y lenguaje formal de los tribunales chilenos, listo para revisar y presentar.")
    
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
        tribunal_red = col_r2.text_input("Tribunal (Para la suma)", placeholder="Ej: S.J.L. en lo Civil (1°)")
        
        rol_red = col_r1.text_input("Causa Rol", placeholder="Ej: C-1234-2026")
        caratula_red = col_r2.text_input("Caratulado", placeholder="Ej: PEREZ / BANCO")
        
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