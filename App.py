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

# --- FUNCIONES DE SALUDO Y LOGO ---
def obtener_saludo():
    hora = datetime.now().hour
    if 0 <= hora < 12:
        return "Buenos días"
    else:
        return "Buenas tardes"

def get_logo_src():
    ruta_base = os.path.dirname(os.path.abspath(__file__))
    extensiones = ['png', 'jpg', 'jpeg', 'PNG', 'JPG']
    for ext in extensiones:
        ruta_logo = os.path.join(ruta_base, f"logo.{ext}")
        if os.path.exists(ruta_logo):
            with open(ruta_logo, "rb") as f:
                contenido_b64 = base64.b64encode(f.read()).decode()
                return f"data:image/{ext.lower()};base64,{contenido_b64}"
    return "https://img.icons8.com/color/48/user.png"

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
        df_consolidado['Tipo_Negocio'] = "Grupo Defensa"
        
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
    p3.add_run("El calendario de vencimientos de las mensualidades respectivas queda fijado bajo la siguiente distribución de fechas:\n")
    
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
    p3_bis.add_run(f"Titular de la Cuenta: {datos['abogado_nombre']}\nRUT: {datos['abogado_rut']}\nInstitución Bancaria: {datos['banco']}\nTipo de Cuenta: {datos['tipo_cuenta']}\nNúmero de Cuenta: {datos['num_cuenta']}\nCorreo de Confirmación: {datos['abogado_correo']}")

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

# --- SISTEMA DE CONTROL DE ACCESO ---
USUARIOS = {
    "Narratia": "20911237", 
    "Vfarfan": "vpfm2404", 
    "Gdonoso": "gdonoso123",
    "Mcortes": "Mcortes123"
}

NOMBRES_REALES = {
    "Narratia": "Nicolás Arratia", 
    "Vfarfan": "Valentina Farfán", 
    "Gdonoso": "Gabriel Donoso",
    "Mcortes": "Miryam Cortés"
}

if 'logged_in' not in st.session_state: 
    st.session_state['logged_in'] = False
    
if 'username' not in st.session_state: 
    st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.markdown("""
    <style>
        [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
        #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
        .block-container { max-width: 1300px !important; margin: 0 auto !important; padding-top: 2rem !important; }
        [data-testid="stForm"] { background-color: white !important; border-radius: 16px !important; border: 1px solid #e0e4e8 !important; padding: 40px 30px !important; box-shadow: 0 4px 15px rgba(0,0,0,0.05) !important; }
        p, label, span, div { color: #172b4d !important; }
        [data-testid="stFormSubmitButton"] button { background-color: #0052cc !important; color: white !important; border: none !important; }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_a, col_b, col_c = st.columns([1, 1.2, 1])
    with col_b:
        with st.form("login_form", clear_on_submit=False):
            st.markdown(f"""
            <div style='text-align: center; margin-bottom: 20px;'>
                <img src='{LOGO_URL}' style='width: 140px; margin-bottom: 5px;'>
                <h1 style='color:#172b4d; margin-top: 0; margin-bottom: 5px; font-size: 32px; font-weight: 800; letter-spacing: 1px;'>JuriSync</h1>
                <p style='color:#6b778c; font-size: 15px; margin:0;'>Inicia sesión en tu espacio de trabajo</p>
            </div>
            """, unsafe_allow_html=True)
            
            input_usuario = st.text_input("Usuario")
            input_password = st.text_input("Contraseña", type="password")
            st.write("") 
            
            boton_ingresar = st.form_submit_button("Ingresar al Sistema", use_container_width=True)
            if boton_ingresar:
                if input_usuario in USUARIOS and USUARIOS[input_usuario] == input_password:
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = input_usuario
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña incorrectos.")
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

# --- ESTILOS CSS ENCUADRE DE ALTA FIDELIDAD ---
st.markdown("""
<style>
    /* Fondo principal: Gris azulado profundo (no negro puro) */
    [data-testid="stAppViewContainer"] { 
        background-color: #0d1117 !important; 
    }
    
    /* Sidebar: Un tono apenas más oscuro para dar profundidad */
    [data-testid="stSidebar"] { 
        background-color: #161b22 !important; 
        border-right: 1px solid #30363d !important; 
    }
    
    /* Texto: Blanco apagado para evitar el brillo que fatiga */
    .stMarkdown, p, span, label, h1, h2, h3, h4, h5, h6 { 
        color: #c9d1d9 !important; 
    }
    
    /* Tarjetas: Fondo gris oscuro con un borde sutil */
    .dash-card { 
        background-color: #21262d !important; 
        border-radius: 10px; 
        padding: 18px; 
        border: 1px solid #30363d !important; 
        margin-bottom: 15px; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.2); 
    }
    
    /* Inputs y Campos de texto: Contraste suave */
    .stTextInput input, .stTextArea textarea, .stSelectbox select, .stNumberInput input {
        background-color: #0d1117 !important;
        border: 1px solid #30363d !important;
        color: #e6edf3 !important;
    }
    
    /* Placeholder: Que sea visible pero no intrusivo */
    ::placeholder {
        color: #484f58 !important;
    }
    
    /* Botones estilo "cuadraditos": Uniformes y elegantes */
    [data-testid="stButton"] button { 
        background-color: #30363d !important; 
        color: #c9d1d9 !important; 
        border: 1px solid #484f58 !important; 
        border-radius: 6px !important;
        font-weight: 500 !important;
        transition: all 0.2s ease !important;
    }
    [data-testid="stButton"] button:hover { 
        background-color: #3b434d !important;
        border-color: #58a6ff !important;
        color: #ffffff !important;
    }
</style>
""", unsafe_allow_html=True)

# --- RENDER DE BARRA LATERAL ---
with st.sidebar:
    st.markdown(f"""
    <div style='text-align: center; margin-bottom: 25px;'>
        <img src='{LOGO_URL}' style='width: 80px;'>
        <h2 style='color:#c9d1d9; margin-top: 10px; font-weight: 800; letter-spacing: 1px;'>Legaliz</h2>
    </div>
    """, unsafe_allow_html=True)

    # Definición del orden exacto que tenías
    opciones_flujo = [
        "🏠 Inicio", "📅 Calendario", "📋 Agenda", "📄 Contratos", 
        "💰 Contabilidad", "📝 Trámites", "📆 Estado diario", "☑️ Tareas", 
        "💼 Causas", "👥 Clientes", "✈️ Mensajería", "⚙️ Automatizaciones", 
        "📊 Informes", "📥 Excel", "📈 Marketing"
    ]

    # Generamos los "cuadraditos" con botones
    for opcion in opciones_flujo:
        # Esto hace que el botón se vea como un bloque uniforme
        if st.button(opcion, use_container_width=True, key=f"btn_{opcion}"):
            st.session_state['menu_radio'] = opcion
            resetear_vistas()
            st.rerun()

    st.markdown("<br><br>", unsafe_allow_html=True)
    
    # Bloque Usuario Fijo Abajo
    st.markdown(f"""
    <div style='padding: 10px; background: #21262d; border-radius: 8px; border: 1px solid #30363d;'>
        <div style='display: flex; align-items: center;'>
            <div style='background:#58a6ff; color:white; width:40px; height:40px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:bold; margin-right:10px;'>
                {nombre_real_usuario[0]}
            </div>
            <div>
                <div style='font-size:14px; font-weight:bold; color:#c9d1d9;'>{nombre_real_usuario}</div>
                <div style='font-size:11px; color:#8b949e;'>Supervisor_general</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    st.write("")
    if st.button("🚪 Cerrar Sesión", use_container_width=True): 
        for key in list(st.session_state.keys()): 
            del st.session_state[key]
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
                st.markdown(f"<div style='border-bottom:1px solid #f4f5f7; padding:8px 0;'><strong style='color:#172b4d; font-size:14px;'>{c.get('CARATULADO', 'Sin nombre')}</strong><br><span style='color:#6b778c; font-size:12px;'>Rol: {c.get('ROL','--')} | {c.get('Tipo_Negocio','--')}</span></div>", unsafe_allow_html=True)
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
                st.markdown(f"<div style='border-left:3px solid {color_t}; padding-left:10px; margin-bottom:10px;'><strong style='color:#172b4d; font-size:14px;'>{t['Titulo']}</strong><br><span style='color:#6b778c; font-size:12px;'>Causa: {t['ROL']}</span></div>", unsafe_allow_html=True)
            st.button("Ir a Agenda de Trabajo", on_click=lambda: st.session_state.update({'menu_radio': '📋 Agenda'}), use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

# 2. CONTABILIDAD
elif st.session_state['menu_radio'] == "💰 Contabilidad":
    st.title("💰 Panel de Honorarios y Contabilidad")
    df_c = pd.read_csv(ARCHIVO_BD)
    
    if df_c.empty or 'Total_Honorarios' not in df_c.columns:
        st.info("Aún no existen registros financieros en el sistema.")
    else:
        df_financiero = df_c[df_c['Estado_Honorarios'].isin(["Pagados", "Pendientes"])].copy()
        for col in ['Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']:
            df_financiero[col] = pd.to_numeric(df_financiero[col], errors='coerce').fillna(0)
            
        total_facturado = df_financiero['Total_Honorarios'].sum()
        recaudado = 0
        for _, row in df_financiero.iterrows():
            if row['Estado_Honorarios'] == 'Pagados':
                recaudado += row['Total_Honorarios']
            elif row['Estado_Honorarios'] == 'Pendientes' and row['Cuotas_Totales'] > 0:
                valor_cuota = row['Total_Honorarios'] / row['Cuotas_Totales']
                recaudado += (valor_cuota * row['Cuotas_Pagadas'])
        por_cobrar = total_facturado - recaudado
        
        c_f1, c_f2, c_f3 = st.columns(3)
        with c_f1: 
            st.markdown(f"<div class='dash-card' style='border-left: 4px solid #0052cc;'><h3 style='margin:0; font-size:14px; color:#6b778c;'>TOTAL FACTURADO</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>${total_facturado:,.0f}</h2></div>", unsafe_allow_html=True)
        with c_f2: 
            st.markdown(f"<div class='dash-card' style='border-left: 4px solid #57a15a;'><h3 style='margin:0; font-size:14px; color:#6b778c;'>RECAUDADO REGISTRADO</h3><h2 style='margin:0; font-size:28px; color:#57a15a;'>${recaudado:,.0f}</h2></div>", unsafe_allow_html=True)
        with c_f3: 
            st.markdown(f"<div class='dash-card' style='border-left: 4px solid #ff5630;'><h3 style='margin:0; font-size:14px; color:#6b778c;'>SALDO POR COBRAR</h3><h2 style='margin:0; font-size:28px; color:#ff5630;'>${por_cobrar:,.0f}</h2></div>", unsafe_allow_html=True)

        st.markdown("### Estado de Cuentas por Cliente")
        df_mostrar = df_financiero[['Cliente', 'ROL', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']].copy()
        df_mostrar.columns = ['Cliente', 'Rol Causa', 'Estado', 'Total ($)', 'Mensualidades', 'Cuotas Pagadas']
        df_mostrar['Deuda ($)'] = df_mostrar.apply(lambda x: 0 if x['Estado'] == 'Pagados' else (x['Total ($)'] - ((x['Total ($)'] / x['Mensualidades'] * x['Cuotas Pagadas']) if x['Mensualidades'] > 0 else 0)), axis=1)
        st.dataframe(df_mostrar.style.format({"Total ($)": "${:,.0f}", "Deuda ($)": "${:,.0f}"}), use_container_width=True)

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
                    df_tramites = pd.concat([df_tramites, pd.DataFrame([{
                        'ID_Tramite': str(uuid.uuid4())[:8], 'ROL': rol_sel, 'Fecha_Pago': fecha_pago.strftime("%d/%m/%Y"),
                        'Tipo_Auxiliar': tipo_aux, 'Monto': monto_pagado, 'Comprobante_Nombre': nombre_archivo,
                        'Comprobante_B64': b64_str, 'Registrado_Por': nombre_real_usuario
                    }])], ignore_index=True)
                    df_tramites.to_csv(ARCHIVO_TRAMITES, index=False)
                    st.success("✅ Trámite guardado en la base local.")
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

# 5. CONTRATOS WORD
elif st.session_state['menu_radio'] == "📄 Contratos":
    st.title("📄 Generador e Historial de Contratos Jurídicos")
    tab_gen, tab_reg = st.tabs(["Generar Nuevo Contrato", "Registro de Copias Guardadas"])
    
    with tab_gen:
        if not DOCX_READY: 
            st.error("⚠️ El motor `python-docx` no está instalado en el servidor.")
        else:
            st.markdown("Formulario completamente limpio para redacción de contratos desde cero:")
            with st.form("form_generador", clear_on_submit=False):
                with st.container(border=True):
                    st.markdown("#### Módulo 1: Naturaleza Jurídica del Juicio")
                    tipo_servicio = st.selectbox("Servicio a Contratar", ["Liquidación voluntaria", "Juicio ejecutivo", "Derecho de familia", "Derecho penal", "Derecho civil"])
                    detalle_servicio = st.text_area("Cláusula Primera: Acciones Legales Incluidas en la Representación", height=120)
                
                col_ab, col_cl = st.columns(2)
                with col_ab:
                    with st.container(border=True):
                        st.markdown("#### Módulo 2: Litigante Patrocinante (Abogado)")
                        abog_nom = st.text_input("Nombre Completo del Abogado")
                        abog_rut = st.text_input("Cédula de Identidad Abogado")
                        abog_dom = st.text_input("Domicilio Profesional Completo")
                        abog_tel = st.text_input("Teléfono de Contacto")
                        abog_correo = st.text_input("Correo Electrónico de Coordinación")
                with col_cl:
                    with st.container(border=True):
                        st.markdown("#### Módulo 3: Mandante Judicial (Cliente)")
                        cli_nom = st.text_input("Nombre Completo del Cliente")
                        cli_rut = st.text_input("Cédula de Identidad Cliente")
                        cli_dom = st.text_input("Domicilio Particular")
                        cli_tel = st.text_input("Teléfono Particular")
                        cli_correo = st.text_input("Correo Electrónico Cliente")
                        
                with st.container(border=True):
                    st.markdown("#### Módulo 4: Estipulación de Honorarios y Cuenta de Abono")
                    c_p1, c_p2 = st.columns(2)
                    with c_p1: 
                        hon_num = st.text_input("Valor Total en Números (Ej: $2.500.000)") 
                        hon_let = st.text_input("Valor Total en Letras")
                        cuotas_c = st.number_value = st.number_input("Número de Mensualidades Fijadas", min_value=1, value=1)
                        cuotas_m = st.text_input("Valor de la Cuota Mensual")
                        fecha_pago = st.date_input("Vencimiento de la Primera Mensualidad")
                    with c_p2: 
                        banco = st.text_input("Banco de Destino")
                        tipo_cta = st.selectbox("Tipo de Cuenta Bancaria", ["Cuenta Corriente", "Cuenta Vista", "Cuenta RUT", "Chequera Electrónica"])
                        num_cta = st.text_input("Número de Cuenta Corriente/Vista")
                        
                if st.form_submit_button("📄 Estructurar Contrato en Formato Word", type="primary", use_container_width=True):
                    datos_c = {
                        'tipo_servicio': tipo_servicio, 'detalle_servicio': detalle_servicio,
                        'abogado_nombre': abog_nom, 'abogado_rut': abog_rut, 'abogado_domicilio': abog_dom, 'abogado_tel': abog_tel, 'abogado_correo': abog_correo,
                        'cliente_nombre': cli_nom, 'cliente_rut': cli_rut, 'cliente_domicilio': cli_dom, 'cliente_tel': cli_tel, 'cliente_correo': cli_correo,
                        'honorarios_num': hon_num, 'honorarios_letras': hon_let, 'cuotas_cant': cuotas_c, 'cuotas_monto': cuotas_m, 'fecha_inicio': fecha_pago,
                        'banco': banco, 'tipo_cuenta': tipo_cta, 'num_cuenta': num_cta
                    }
                    doc_final = crear_contrato_word(datos_c)
                    if doc_final:
                        buffer_memoria = io.BytesIO()
                        doc_final.save(buffer_memoria)
                        st.session_state['contrato_generado'] = buffer_memoria.getvalue()
                        st.session_state['nombre_archivo'] = f"Contrato_{cli_nom.replace(' ', '_')}.docx"
                        
                        df_con = pd.read_csv(ARCHIVO_CONTRATOS)
                        df_con = pd.concat([df_con, pd.DataFrame([{'ID': str(uuid.uuid4())[:8], 'Fecha': datetime.now().strftime("%d/%m/%Y"), 'Cliente': cli_nom, 'Servicio': tipo_servicio, 'Honorarios': hon_num}])], ignore_index=True)
                        df_con.to_csv(ARCHIVO_CONTRATOS, index=False)
                        st.rerun()
                        
        if st.session_state.get('contrato_generado'):
            st.success("Contrato estructurado perfectamente conforme al modelo base de la oficina.")
            st.download_button(label="📥 Descargar Documento Word (.docx)", data=st.session_state['contrato_generado'], file_name=st.session_state['nombre_archivo'], mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", type="primary")
            
    with tab_reg:
        df_contratos_reg = pd.read_csv(ARCHIVO_CONTRATOS)
        if df_contratos_reg.empty: 
            st.info("No registras copias históricas guardadas.")
        else: 
            st.dataframe(df_contratos_reg, use_container_width=True)

# 6. CAUSAS / EXPEDIENTES (MEJORADO VISUALMENTE)
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
                    n_rol = c_nuevo1.text_input("ROL / RIT (Ej: C-123-2024)")
                    n_trib = c_nuevo2.text_input("Tribunal")
                    n_carat = st.text_input("Caratulado (Ej: PEREZ / BANCO)")
                    n_cli = st.text_input("Nombre del Cliente Titular")
                    
                    if st.form_submit_button("Guardar Causa en Base de Datos"):
                        if n_rol.strip() == "":
                            st.error("El ROL es obligatorio.")
                        else:
                            nueva_c = {
                                'ROL': n_rol, 'TRIBUNAL': n_trib, 'CARATULADO': n_carat, 'Cliente': n_cli,
                                'RUT': '--', 'Teléfono': '--', 'Tipo_Negocio': 'Propio', 'Clave_unica': '--',
                                'Correo': '--', 'Direccion': '--', 'SAC': '--', 'Sucursal': '--',
                                'Estado_Honorarios': 'Sin fijar', 'Total_Honorarios': 0, 'Cuotas_Totales': 0, 'Cuotas_Pagadas': 0
                            }
                            df_causas = pd.concat([df_causas, pd.DataFrame([nueva_c])], ignore_index=True)
                            df_causas.to_csv(ARCHIVO_BD, index=False)
                            st.session_state['creando_causa'] = False
                            st.success("Causa creada exitosamente.")
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
        
        # Renderizado Visual Estilo Lista Elegante
        with st.container(height=600):
            if df_filtrado.empty:
                st.info("No hay causas que coincidan con la búsqueda.")
            else:
                # Encabezado de la "Tabla"
                c_h1, c_h2, c_h3, c_h4 = st.columns([2, 3, 4, 2])
                c_h1.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>ROL DE CAUSA</span>", unsafe_allow_html=True)
                c_h2.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>TRIBUNAL ASIGNADO</span>", unsafe_allow_html=True)
                c_h3.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>CARATULADO</span>", unsafe_allow_html=True)
                c_h4.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px; text-align:center; display:block;'>ACCIÓN</span>", unsafe_allow_html=True)
                st.markdown("<hr style='margin: 5px 0px 10px 0px;'>", unsafe_allow_html=True)
                
                # Filas de la Tabla
                for idx, row in df_filtrado.iterrows():
                    c1, c2, c3, c4 = st.columns([2, 3, 4, 2])
                    c1.markdown(f"<span style='color:#0052cc; font-weight:bold; font-size:15px;'>{row['ROL']}</span>", unsafe_allow_html=True)
                    c2.markdown(f"<span style='color:#172b4d; font-size:14px;'>{row['TRIBUNAL']}</span>", unsafe_allow_html=True)
                    c3.markdown(f"<span style='color:#172b4d; font-size:14px;'>{row['CARATULADO']}</span>", unsafe_allow_html=True)
                    c4.button("📂 Abrir", key=f"abrir_c_{idx}", use_container_width=True, on_click=ir_a_expediente, args=(row['ROL'],))
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
                    n_negocio = st.selectbox("Origen de Cartera", ["Grupo Defensa", "Propio"], index=0 if c_data.get('Tipo_Negocio') == "Grupo Defensa" else 1)
                    
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
                        
                    if st.form_submit_button("💾 Actualizar Ficha de Causa", type="primary"):
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
                clase_div = "badge-active" if c_data.get('Tipo_Negocio') == "Grupo Defensa" else "badge-propio"
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
            tab_movs, tab_tareas_internas, tab_legacy = st.tabs(["Movimientos", "Tareas Operativas", "Movimientos legacy"])
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
                        if st.form_submit_button("Registrar Tarea"):
                            df_t = pd.read_csv(ARCHIVO_TAREAS)
                            nueva_t = {
                                'ID_Tarea': str(uuid.uuid4())[:8], 'ROL': rol_actual, 'Creador': usuario_actual,
                                'Fecha_Creacion': datetime.now().strftime("%d/%m/%Y"), 'Fecha_Vencimiento': t_f.strftime("%d/%m/%Y"),
                                'Titulo': t_t, 'Descripcion': t_d, 'Estado': 'En progreso', 'Comentarios': '[]', 'Prioridad': t_p
                            }
                            df_t = pd.concat([df_t, pd.DataFrame([nueva_t])], ignore_index=True)
                            df_t.to_csv(ARCHIVO_TAREAS, index=False)
                            st.session_state['creando_tarea'] = False
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
                            
                            c_top_l, c_top_r = st.columns([3, 1.8])
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
                                    bcols = st.columns([1, 1, 1.5, 0.5])
                                    if bcols[0].button("❌", key=f"rechar_{tarea['ID_Tarea']}"): 
                                        df_t_local.at[idx_tarea_bd, 'Estado'] = 'Rechazada'
                                        df_t_local.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                                    if bcols[1].button("✅", key=f"aprobar_{tarea['ID_Tarea']}"): 
                                        df_t_local.at[idx_tarea_bd, 'Estado'] = 'Aprobada'
                                        df_t_local.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                                    bcols[2].markdown("<div style='background:#ffc400; color:#172b4d; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:600; text-align:center; margin-top:5px;'>En progreso</div>", unsafe_allow_html=True)
                                else:
                                    bg_e = "#57a15a" if tarea['Estado'] == 'Aprobada' else "#ff5630"
                                    bcols = st.columns([3, 1.5, 0.5])
                                    bcols[1].markdown(f"<div style='background:{bg_e}; color:white; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:600; text-align:center; margin-top:5px;'>{tarea['Estado']}</div>", unsafe_allow_html=True)

                            st.markdown(f"<h3 style='font-size: 18px; color: #172b4d; margin-top: 15px; margin-bottom: 5px;'>{tarea['Titulo']}</h3><p style='font-size: 15px; color: #172b4d; margin-bottom: 15px;'>{tarea['Descripcion']}</p>", unsafe_allow_html=True)
                            
                            # Render avanzado de comentarios 
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
                                texto_com = c_txt.text_input("Agregar un comentario...", label_visibility="collapsed")
                                if c_btn.form_submit_button("Enviar"):
                                    if texto_com.strip() or adj_coment:
                                        t_final = texto_com.strip() + (f" <br><em>[📎 Archivo adjunto: {adj_coment.name}]</em>" if adj_coment else "")
                                        comentarios_js.append({"autor": nombre_real_usuario, "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"), "texto": t_final})
                                        df_t_local.at[idx_tarea_bd, 'Comentarios'] = json.dumps(comentarios_js)
                                        df_t_local.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()

# 7. AGENDA DIARIA
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

# 8. MENSAJERÍA INTERNA (MODO WHATSAPP)
elif st.session_state['menu_radio'] == "✈️ Mensajería":
    st.title("✈️ Mensajería Interna del Equipo")
    st.markdown("Plataforma de comunicación rápida para la oficina.")
    
    df_msgs = pd.read_csv(ARCHIVO_MENSAJES)
    
    # CSS Custom para imitar WhatsApp
    st.markdown("""
    <style>
        .chat-bg { background-color: #efeae2; padding: 20px; border-radius: 12px; border: 1px solid #e0e4e8; }
        .burbuja-mia { background-color: #dcf8c6; padding: 10px 15px; border-radius: 15px 15px 0px 15px; max-width: 75%; box-shadow: 0 1px 1px rgba(0,0,0,0.1); margin-left: auto; margin-bottom: 12px; }
        .burbuja-otro { background-color: #ffffff; padding: 10px 15px; border-radius: 15px 15px 15px 0px; max-width: 75%; box-shadow: 0 1px 1px rgba(0,0,0,0.1); margin-right: auto; margin-bottom: 12px; }
        .chat-autor { font-size: 13px; font-weight: 800; color: #075e54; margin-bottom: 2px; }
        .chat-texto { font-size: 15px; color: #303030; line-height: 1.4; }
        .chat-hora { font-size: 11px; color: #999999; text-align: right; margin-top: 5px; }
        .chat-para { font-size: 11px; color: #667781; font-weight: normal; margin-left: 5px; }
    </style>
    """, unsafe_allow_html=True)
    
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

# 9. CLIENTES DIRECTOS (MEJORADO VISUALMENTE)
elif st.session_state['menu_radio'] == "👥 Clientes":
    df_causas = pd.read_csv(ARCHIVO_BD)
    
    if st.session_state['cliente_seleccionado'] is None:
        st.title("👥 Directorio de Clientes")
        
        if st.button("➕ Crear Nuevo Cliente", type="primary"):
            st.session_state['creando_cliente'] = not st.session_state.get('creando_cliente', False)
            
        if st.session_state.get('creando_cliente'):
            with st.container(border=True):
                st.markdown("#### Ingresar Datos del Nuevo Cliente")
                with st.form("form_crear_cliente"):
                    c_c1, c_c2 = st.columns(2)
                    n_cli_nom = c_c1.text_input("Nombre Completo")
                    n_cli_rut = c_c2.text_input("RUT del Cliente")
                    n_cli_tel = st.text_input("Teléfono")
                    
                    if st.form_submit_button("Guardar Cliente"):
                        if n_cli_nom.strip() == "":
                            st.error("El nombre del cliente es obligatorio.")
                        else:
                            nueva_ficha_cli = {
                                'ROL': 'Sin Causa Aún', 'TRIBUNAL': '--', 'CARATULADO': '--', 'Cliente': n_cli_nom,
                                'RUT': n_cli_rut, 'Teléfono': n_cli_tel, 'Tipo_Negocio': 'Propio', 'Clave_unica': '--',
                                'Correo': '--', 'Direccion': '--', 'SAC': '--', 'Sucursal': '--',
                                'Estado_Honorarios': 'Sin fijar', 'Total_Honorarios': 0, 'Cuotas_Totales': 0, 'Cuotas_Pagadas': 0
                            }
                            df_causas = pd.concat([df_causas, pd.DataFrame([nueva_ficha_cli])], ignore_index=True)
                            df_causas.to_csv(ARCHIVO_BD, index=False)
                            st.session_state['creando_cliente'] = False
                            st.success("Cliente guardado en el directorio.")
                            st.rerun()

        st.write("---")
        clientes_unicos = df_causas['Cliente'].dropna().unique().tolist() if 'Cliente' in df_causas.columns else []
        
        st.markdown("### Listado de Clientes Activos")
        
        # Renderizado Visual Estilo Lista Elegante para Clientes
        with st.container(height=600):
            # Encabezado
            ch1, ch2, ch3, ch4 = st.columns([1, 4, 3, 2])
            ch1.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>PERFIL</span>", unsafe_allow_html=True)
            ch2.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>NOMBRE COMPLETO</span>", unsafe_allow_html=True)
            ch3.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px;'>DATOS CONTACTO</span>", unsafe_allow_html=True)
            ch4.markdown("<span style='color:#6b778c; font-weight:800; font-size:13px; text-align:center; display:block;'>ACCIÓN</span>", unsafe_allow_html=True)
            st.markdown("<hr style='margin: 5px 0px 10px 0px;'>", unsafe_allow_html=True)
            
            for cli_nom in clientes_unicos:
                if cli_nom.strip() and cli_nom != "--":
                    fila_ref = df_causas[df_causas['Cliente'] == cli_nom].iloc[0]
                    c1, c2, c3, c4 = st.columns([1, 4, 3, 2])
                    
                    c1.markdown(f"<div style='font-size:24px; text-align:center;'>👤</div>", unsafe_allow_html=True)
                    c2.markdown(f"<span style='color:#172b4d; font-weight:bold; font-size:15px; display:block; margin-top:5px;'>{cli_nom}</span>", unsafe_allow_html=True)
                    c3.markdown(f"<span style='color:#6b778c; font-size:13px;'>RUT: {fila_ref.get('RUT', '--')}<br>Tel: {fila_ref.get('Teléfono', '--')}</span>", unsafe_allow_html=True)
                    c4.button("Ver Ficha", key=f"v_cli_{cli_nom}", use_container_width=True, on_click=lambda c=cli_nom: st.session_state.update({'cliente_seleccionado': c}))
                    st.markdown("<hr style='margin: 8px 0px 8px 0px; border-top: 1px dashed #e0e4e8;'>", unsafe_allow_html=True)
                    
    else:
        cli_actual = st.session_state['cliente_seleccionado']
        df_cli = df_causas[df_causas['Cliente'] == cli_actual]
        datos = df_cli.iloc[0]
        
        if st.button("⬅ Volver al Directorio", key="back_to_cli_list", on_click=nav_clientes): 
            pass
            
        st.markdown(f"""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px;">
            <h2 style="color:#172b4d; margin:0;">Ficha de cliente - {cli_actual}</h2>
            <div style="display:flex; border: 1px solid #0052cc; border-radius:6px; overflow:hidden;">
                <div style="background:#0052cc; color:white; padding:8px 20px; font-weight:bold; font-size:14px;">Información</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        c_izq, c_der = st.columns([1, 2.5])
        with c_izq:
            st.markdown(f"""
            <div style="background:white; padding:25px; border-radius:12px; border:1px solid #e0e4e8; margin-bottom:20px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:25px;">
                    <span style="font-weight:bold; color:#172b4d; font-size:16px;">Información</span>
                    <span style="background:#57a15a; color:white; padding:4px 12px; border-radius:15px; font-size:13px; font-weight:bold; display:flex; align-items:center; gap:6px;">Activo <span style="height:8px; width:8px; background:white; border-radius:50%; display:inline-block;"></span></span>
                </div>
                <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">Nombre:</div><div style="color:#172b4d; font-size:15px; margin-bottom:15px;">👤 {datos.get('Cliente','--')}</div>
                <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">Rut cliente:</div><div style="color:#172b4d; font-size:15px; margin-bottom:15px;">👤 {datos.get('RUT','--')}</div>
                <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">Clave única:</div><div style="color:#172b4d; font-size:15px; margin-bottom:20px;"><span>🛡️ {datos.get('Clave_unica','*****')}</span></div>
                <div style="color:#172b4d; font-size:15px; margin-bottom:12px;">📞 {datos.get('Teléfono','--')}</div>
                <div style="color:#172b4d; font-size:15px; margin-bottom:12px;">📄 {datos.get('Correo','--')}</div>
                <div style="color:#172b4d; font-size:15px; margin-bottom:30px;">📍 {datos.get('Direccion','--')}</div>
                <div style="font-weight:bold; color:#172b4d; font-size:15px; margin-bottom:15px;">Información SAC</div>
                <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">SAC asignado:</div><div style="color:#172b4d; font-size:15px; margin-bottom:15px;">👤 {datos.get('SAC','--')}</div>
                <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">Sucursal:</div><div style="color:#172b4d; font-size:15px; margin-bottom:10px;">{datos.get('Sucursal','--')}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with c_der:
            st.markdown("<div style='background:#f8f9fa; padding:20px; border-radius:12px; border:1px solid #e0e4e8; min-height:600px;'><h3 style='color:#172b4d; margin-top:0; margin-bottom:20px;'>Causas Asociadas</h3>", unsafe_allow_html=True)
            for i, causa in df_cli.iterrows():
                if causa.get('ROL') != "Sin Causa Aún":
                    with st.container(border=True):
                        col_card1, col_card2 = st.columns([5, 1])
                        with col_card1:
                            st.markdown(f"<div><strong style='color:#172b4d; font-size:16px;'>{causa.get('CARATULADO','--')}</strong><br><span style='color:#42526e; font-size:14px;'>Rol: {causa.get('ROL','--')}</span><br><span style='color:#42526e; font-size:14px;'>👥 {causa.get('Servicio', 'Ejecutivo')}</span><br><span style='color:#42526e; font-size:14px;'>🏛️ {causa.get('TRIBUNAL', 'Sin Tribunal')}</span></div>", unsafe_allow_html=True)
                        with col_card2:
                            st.write("<br>", unsafe_allow_html=True)
                            st.button("Abrir Expediente", key=f"ficha_ir_{causa.get('ROL')}", on_click=ir_a_expediente, args=(causa.get('ROL'),), use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

# 10. GESTOR GLOBAL DE TAREAS
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

# 11. EXCEL IMPORTADOR 
elif st.session_state['menu_radio'] == "📥 Excel":
    st.title("📥 Importador Masivo de Causas (OJV)")
    archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx", "xls"])
    if archivo and st.button("Procesar y Consolidar en Base de Datos"):
        procesar_ojv_completo(archivo)
        st.success("¡Base de datos unificada actualizada con éxito!")

# 12. RESTO DE PESTAÑAS (EN ESPERA)
elif st.session_state['menu_radio'] == "📅 Calendario":
    st.title("📅 Calendario de Tareas")
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
        "buttonText": {"today": "Hoy", "month": "Mes", "week": "Semana", "day": "Día", "list": "Agenda"},
        "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,timeGridWeek,listMonth"}
    }
    
    css_calendario_moderno = """
        .fc { background-color: white; border-radius: 16px; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; }
        .fc-theme-standard td, .fc-theme-standard th { border-color: #e0e4e8; }
        .fc-col-header-cell { background-color: #f8f9fa; padding: 12px 0 !important; color: #6b778c; text-transform: capitalize; font-size: 14px; }
        .fc-button-primary { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #e0e4e8 !important; border-radius: 8px !important; text-transform: capitalize !important; font-weight: 600 !important; box-shadow: 0 1px 3px rgba(0,0,0,0.05) !important; }
        .fc-button-primary:hover { background-color: #f4f5f7 !important; border-color: #0052cc !important; color: #0052cc !important; }
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
else:
    st.title(f"{st.session_state['menu_radio'].split(' ')[1]}")
    st.info("🚧 Módulo en construcción. Estará disponible en futuras actualizaciones.")