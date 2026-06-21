import streamlit as st
import pandas as pd
import os
import json
import uuid
import base64
import io
from datetime import datetime
from streamlit_calendar import calendar

# Intentamos importar la librería para crear Word. 
try:
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_READY = True
except ImportError:
    DOCX_READY = False

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="JuriSync | Sistema Judicial", layout="wide", initial_sidebar_state="expanded")

# --- FUNCIONES PRINCIPALES ---
def obtener_saludo():
    hora = datetime.now().hour
    if 0 <= hora < 12:
        return "Buenos días"
    else:
        return "Buenas tardes"

def get_logo_src():
    ruta_base = os.path.dirname(os.path.abspath(__file__))
    for ext in ['png', 'jpg', 'jpeg', 'PNG', 'JPG']:
        ruta_logo = os.path.join(ruta_base, f"logo.{ext}")
        if os.path.exists(ruta_logo):
            with open(ruta_logo, "rb") as f:
                return f"data:image/{ext.lower()};base64,{base64.b64encode(f.read()).decode()}"
    return "https://img.icons8.com/color/48/user.png"

LOGO_URL = get_logo_src()

def procesar_ojv_completo(archivo):
    diccionario_hojas = pd.read_excel(archivo, sheet_name=None)
    mapa = {'ROL': ['ROL', 'RIT', 'Rol', 'Rit'], 'TRIBUNAL': ['TRIBUNAL', 'Tribunal', 'Juzgado', 'Corte'], 'CARATULADO': ['CARATULA', 'Carátula', 'Caratulado', 'Causa']}
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

def obtener_feriados_chile():
    feriados = []
    for anio in [2025, 2026, 2027]:
        fijos = [
            (f"{anio}-01-01", "Año Nuevo"), (f"{anio}-05-01", "Día del Trabajador"),
            (f"{anio}-05-21", "Glorias Navales"), (f"{anio}-06-21", "Pueblos Indígenas"),
            (f"{anio}-06-29", "San Pedro y San Pablo"), (f"{anio}-07-16", "Virgen del Carmen"),
            (f"{anio}-08-15", "Asunción de la Virgen"), (f"{anio}-09-18", "Independencia Nacional"),
            (f"{anio}-09-19", "Glorias del Ejército"), (f"{anio}-10-12", "Encuentro de Dos Mundos"),
            (f"{anio}-10-31", "Iglesias Evangélicas"), (f"{anio}-11-01", "Todos los Santos"),
            (f"{anio}-12-08", "Inmaculada Concepción"), (f"{anio}-12-25", "Navidad")
        ]
        for fecha, nombre in fijos:
            feriados.append({"title": f"🇨🇱 {nombre}", "start": fecha, "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"})
    
    feriados.extend([
        {"title": "🇨🇱 Viernes Santo", "start": "2025-04-18", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Sábado Santo", "start": "2025-04-19", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Viernes Santo", "start": "2026-04-03", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Sábado Santo", "start": "2026-04-04", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Viernes Santo", "start": "2027-03-26", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"},
        {"title": "🇨🇱 Sábado Santo", "start": "2027-03-27", "color": "#ffebe6", "textColor": "#bf2600", "allDay": True, "display": "block"}
    ])
    return feriados

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
    intro.add_run(f"Por una parte, don/doña {datos['abogado_nombre']}, chileno/a, abogado, cédula nacional de identidad número {datos['abogado_rut']}, con domicilio profesional en {datos['abogado_domicilio']}, correo electrónico {datos['abogado_correo']}, en adelante \"EL ABOGADO\"; y,\n\n")
    intro.add_run(f"Por otra parte, don/doña {datos['cliente_nombre']}, chileno/a, cédula nacional de identidad número {datos['cliente_rut']}, con domicilio en {datos['cliente_domicilio']}, número de contacto {datos['cliente_tel']}, correo electrónico {datos['cliente_correo']}, en adelante \"LA CLIENTE\" o \"EL CLIENTE\".\n\n")
    intro.add_run("Ambas partes mayores de edad, quienes acreditan su identidad con las cédulas citadas y exponen que han convenido lo siguiente:")
    
    p1 = doc.add_paragraph()
    p1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p1.add_run("CLÁUSULA PRIMERA: DEL SERVICIO PROFESIONAL. ").bold = True
    p1.add_run(f"Por el presente acto e instrumento, La Cliente contrata los servicios profesionales de El Abogado, a quien encarga la gestión y representación jurídica integral en la tramitación de un {datos['tipo_servicio'].upper()}.\n")
    p1.add_run("El servicio incluye:\n")
    p1.add_run(datos['detalle_servicio'])
    
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p2.add_run("CLÁUSULA SEGUNDA: HONORARIOS. ").bold = True
    p2.add_run(f"Como remuneración por los servicios contratados, las partes acuerdan un honorario total de {datos['honorarios_num']} ({datos['honorarios_letras']}).\n")
    p2.add_run("Este monto cubre la defensa letrada durante todo el procedimiento, independiente del tiempo que este demore, hasta la dictación de la resolución de término o equivalente jurisdiccional.")
    
    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p3.add_run("CLÁUSULA TERCERA: FORMA DE PAGO. ").bold = True
    p3.add_run(f"El monto total pactado será pagado en {datos['cuotas_cant']} cuotas mensuales, fijas y sucesivas de {datos['cuotas_monto']} cada una.\n")
    p3.add_run("Considerando que el mes actual se destinará a la recopilación de antecedentes y preparación de la demanda, el calendario de pagos será el siguiente:\n")
    
    table = doc.add_table(rows=1, cols=4)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = 'Cuota'
    hdr_cells[1].text = 'Vencimiento'
    hdr_cells[2].text = 'Monto'
    hdr_cells[3].text = 'Estado'
    
    fecha_base = datos['fecha_inicio']
    for i in range(datos['cuotas_cant']):
        row_cells = table.add_row().cells
        row_cells[0].text = f"{i+1:02d}"
        m = fecha_base.month + i
        y = fecha_base.year + ((m - 1) // 12)
        m = ((m - 1) % 12) + 1
        row_cells[1].text = f"{fecha_base.day:02d} de {meses[m-1]} de {y}"
        row_cells[2].text = str(datos['cuotas_monto'])
        row_cells[3].text = "PENDIENTE"
        
    p3_bis = doc.add_paragraph()
    p3_bis.add_run("\nDatos para Transferencia:\n").bold = True
    p3_bis.add_run(f"Titular: {datos['abogado_nombre']}\nRUT: {datos['abogado_rut']}\nBanco: {datos['banco']}\nTipo de Cuenta: {datos['tipo_cuenta']}\nN° de Cuenta: {datos['num_cuenta']}\nCorreo: {datos['abogado_correo']}")

    p4 = doc.add_paragraph()
    p4.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p4.add_run("CLÁUSULA CUARTA: INCUMPLIMIENTO, ACELERACIÓN Y MULTA. ").bold = True
    p4.add_run("Las partes elevan a la calidad de esencial el pago oportuno de los honorarios pactados. En consecuencia, se acuerdan las siguientes sanciones estrictas para el caso de mora o simple retardo:\n")
    p4.add_run("Cláusula de Aceleración: ").bold = True
    p4.add_run("El no pago íntegro y oportuno de una cualquiera de las cuotas pactadas, hará exigible de inmediato el monto total insoluto de la deuda.\n")
    p4.add_run("Suspensión Inmediata y Renuncia: ").bold = True
    p4.add_run("El atraso superior a 5 días corridos facultará a El Abogado para suspender de inmediato cualquier gestión y renunciar al patrocinio y poder.\n")
    p4.add_run("Multa e Intereses: ").bold = True
    p4.add_run("En caso de mora, la deuda devengará el interés máximo convencional. Adicionalmente, se aplicará una multa diaria a título de cláusula penal equivalente a 0,15 Unidades de Fomento (UF) por cada día de atraso, más los gastos de cobranza extrajudicial que correspondan.")

    p5 = doc.add_paragraph()
    p5.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p5.add_run("CLÁUSULA QUINTA: OBLIGACIONES DEL ABOGADO Y DEL CLIENTE.\n").bold = True
    p5.add_run("Del Abogado: ").bold = True
    p5.add_run("Se obliga a actuar con la debida diligencia profesional en todas las gestiones del proceso. Se deja expresa constancia de que la obligación del abogado es de medios y no de resultados.\n")
    p5.add_run("Del Cliente: ").bold = True
    p5.add_run("Se obliga a entregar de forma íntegra, veraz y oportuna toda la documentación y antecedentes solicitados por El Abogado. La Cliente declara que los antecedentes aportados son fidedignos.")

    num_clausula = 6
    if datos['tipo_servicio'] == "Liquidación voluntaria":
        p6 = doc.add_paragraph()
        p6.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p6.add_run("CLÁUSULA SEXTA: FIRMA DE DECLARACIONES JURADAS Y DOCUMENTOS ANEXOS. ").bold = True
        p6.add_run("Como requisito esencial para la presentación de la Solicitud de Liquidación Voluntaria, La Cliente se obliga a firmar y entregar en este acto las siguientes Declaraciones Juradas, que serán entregadas una vez firmando el presente contrato, manifestando entender cabalmente su contenido y alcance legal:\n")
        p6.add_run("- Declaración Jurada de Calidad de Allegado (si aplica).\n- Declaración Jurada de Bienes de Terceros (si aplica).\n- Consentimiento Informado sobre Derechos Hereditarios.\n- Declaración Jurada de Antecedentes Completos y Fehacientes.")
        num_clausula += 1

    numeros_letras = {6: "SEXTA", 7: "SÉPTIMA", 8: "OCTAVA"}
    
    p7 = doc.add_paragraph()
    p7.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p7.add_run(f"CLÁUSULA {numeros_letras[num_clausula]}: DESISTIMIENTO. ").bold = True
    p7.add_run("En caso de desistimiento o término unilateral del contrato por parte de La Cliente, no habrá lugar a devolución de los dineros ya pagados, los que se imputarán a los servicios profesionales prestados.")
    num_clausula += 1

    p8 = doc.add_paragraph()
    p8.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p8.add_run(f"CLÁUSULA {numeros_letras[num_clausula]}: DOMICILIO Y JURISDICCIÓN. ").bold = True
    p8.add_run("Para todos los efectos legales derivados del presente contrato, las partes fijan su domicilio en la ciudad indicada en la comparecencia y se someten a la jurisdicción de sus Tribunales de Justicia.\n\nEl presente instrumento se firma en dos ejemplares del mismo tenor y fecha, quedando uno en poder de cada parte.")

    doc.add_paragraph("\n\n\n")
    table_firmas = doc.add_table(rows=1, cols=2)
    c_abog = table_firmas.cell(0, 0).paragraphs[0]
    c_abog.alignment = WD_ALIGN_PARAGRAPH.CENTER
    c_abog.add_run("___________________________________\n")
    c_abog.add_run(f"{datos['abogado_nombre'].upper()}\n")
    c_abog.add_run(f"R.U.T.: {datos['abogado_rut']}")
    
    c_cli = table_firmas.cell(0, 1).paragraphs[0]
    c_cli.alignment = WD_ALIGN_PARAGRAPH.CENTER
    c_cli.add_run("___________________________________\n")
    c_cli.add_run(f"{datos['cliente_nombre'].upper()}\n")
    c_cli.add_run(f"R.U.T.: {datos['cliente_rut']}")
    
    return doc

# --- SISTEMA DE AUTENTICACIÓN ---
USUARIOS = {
    "Narratia": "20911237", 
    "Vfarfan": "vpfm2404", 
    "Gdonoso": "gdonoso123",
    "Mcortes": "Mcortes123"  # <--- Agregada Miryam Cortés
}

NOMBRES_REALES = {
    "Narratia": "Nicolás Arratia", 
    "Vfarfan": "Valentina Farfán", 
    "Gdonoso": "Gabriel Donoso",
    "Mcortes": "Miryam Cortés" # <--- Agregada Miryam Cortés
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
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        with st.form("login_form", clear_on_submit=False):
            st.markdown(f"""
            <div style='text-align: center; margin-bottom: 20px;'>
                <img src='{LOGO_URL}' style='width: 140px; margin-bottom: 5px;'>
                <h1 style='color:#172b4d; margin-top: 0; margin-bottom: 5px; font-size: 32px; font-weight: 800; letter-spacing: 1px;'>JuriSync</h1>
                <p style='color:#6b778c; font-size: 15px; margin:0;'>Inicia sesión en tu espacio de trabajo</p>
            </div>
            """, unsafe_allow_html=True)
            
            user = st.text_input("Usuario")
            pwd = st.text_input("Contraseña", type="password")
            st.write("") 
            
            if st.form_submit_button("Ingresar al Sistema", use_container_width=True):
                if user in USUARIOS and USUARIOS[user] == pwd:
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = user
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña incorrectos.")
    st.stop()

# --- ARQUITECTURA DE DATOS LOCALES ---
usuario_actual = st.session_state['username']
nombre_real_usuario = NOMBRES_REALES.get(usuario_actual, usuario_actual.capitalize())

ARCHIVO_BD = f"base_causas_{usuario_actual}.csv"
ARCHIVO_TAREAS = f"base_tareas_{usuario_actual}.csv"
ARCHIVO_CONTRATOS = f"base_contratos_{usuario_actual}.csv"

# Verificaciones y creación de archivos locales si no existen
if not os.path.exists(ARCHIVO_TAREAS):
    df_vacio_tareas = pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad'])
    df_vacio_tareas.to_csv(ARCHIVO_TAREAS, index=False)
else:
    df_t_check = pd.read_csv(ARCHIVO_TAREAS)
    if 'Prioridad' not in df_t_check.columns:
        df_t_check['Prioridad'] = 'Media'
        df_t_check.to_csv(ARCHIVO_TAREAS, index=False)

if not os.path.exists(ARCHIVO_BD):
    df_vacio_causas = pd.DataFrame(columns=['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'RUT', 'Teléfono', 'Tipo_Negocio', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas'])
    df_vacio_causas.to_csv(ARCHIVO_BD, index=False)
else:
    df_c_check = pd.read_csv(ARCHIVO_BD)
    cambios_bd = False
    
    # Asegurarnos de que existan las nuevas columnas de Contabilidad
    for col in ['Cliente', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']:
        if col not in df_c_check.columns:
            if col in ['Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']: 
                df_c_check[col] = 0
            elif col == 'Estado_Honorarios': 
                df_c_check[col] = "Sin fijar"
            else: 
                df_c_check[col] = pd.Series(dtype='str')
            cambios_bd = True
            
    if cambios_bd:
        df_c_check.to_csv(ARCHIVO_BD, index=False)

if not os.path.exists(ARCHIVO_CONTRATOS):
    df_vacio_contratos = pd.DataFrame(columns=['ID', 'Fecha', 'Cliente', 'Servicio', 'Honorarios'])
    df_vacio_contratos.to_csv(ARCHIVO_CONTRATOS, index=False)

# --- ESTADOS Y CALLBACKS DE NAVEGACIÓN ---
def resetear_vistas():
    st.session_state.causa_seleccionada = None
    st.session_state.cliente_seleccionado = None
    st.session_state.modo_edicion = False
    st.session_state.creando_tarea = False
    st.session_state.editando_tarea = None

if 'menu_radio' not in st.session_state: 
    st.session_state['menu_radio'] = "🏠 Inicio"

for key in ['causa_seleccionada', 'cliente_seleccionado', 'modo_edicion', 'creando_tarea', 'editando_tarea']:
    if key not in st.session_state: 
        if key == 'modo_edicion' or key == 'creando_tarea':
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

# --- CSS ALTA FIDELIDAD ---
st.markdown("""
<style>
    .block-container { max-width: 1350px !important; margin: 0 auto !important; padding-top: 3rem !important; padding-left: 2rem !important; padding-right: 2rem !important; }
    [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
    [data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e0e4e8 !important; }
    [data-testid="stHeader"] { background-color: transparent !important; }
    .stMarkdown, p, span, label, h1, h2, h3, h4, h5, h6 { color: #172b4d !important; }
    
    .dash-card { background: white !important; border-radius: 12px; padding: 15px; border: 1px solid #e0e4e8; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
    .dash-header { border-bottom: 2px solid #e0e4e8; padding-bottom: 10px; margin-bottom: 15px; font-weight: 800; font-size: 14px; color: #6b778c; letter-spacing: 0.5px; }
    .badge-active { background: #57a15a !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    .badge-propio { background: #0052cc !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    
    [data-testid="stVerticalBlockBorderWrapper"] { background-color: white !important; border-radius: 12px !important; border: 1px solid #e0e4e8 !important; }
    [data-testid="stButton"] button { background-color: #ffffff !important; color: #172b4d !important; border: 1px solid #e0e4e8 !important; }
    [data-testid="stButton"] button:hover { border-color: #0052cc !important; color: #0052cc !important; }
</style>
""", unsafe_allow_html=True)

# --- MENÚ LATERAL ---
with st.sidebar:
    st.markdown(f"""
    <div style='display: flex; justify-content: center; flex-direction: column; align-items: center; margin-bottom: 20px;'>
        <img src='{LOGO_URL}' style='width: 140px;'>
        <h2 style='color:#172b4d; margin-top: 5px; margin-bottom: 0; font-size: 22px; font-weight: 800; letter-spacing: 1px;'>JuriSync</h2>
    </div>
    """, unsafe_allow_html=True)
    st.write("---")
    
    menu_opciones = ["🏠 Inicio", "📅 Calendario", "📋 Agenda", "📄 Contratos", "💰 Contabilidad", "📝 Trámites", "☑️ Tareas", "💼 Causas", "👥 Clientes", "✈️ Mensajería", "📥 Excel"]
    
    st.radio("Navegación", menu_opciones, key="menu_radio", on_change=resetear_vistas)
    
    st.write("---")
    if st.button("🚪 Cerrar Sesión", use_container_width=True): 
        for key in list(st.session_state.keys()): 
            del st.session_state[key]
        st.rerun()

# --- CONTROLADOR DE VISTAS ---
if st.session_state['menu_radio'] == "🏠 Inicio":
    st.title(f"{obtener_saludo()}, {nombre_real_usuario}")
    
    df_causas_totales = pd.read_csv(ARCHIVO_BD)
    df_tareas_totales = pd.read_csv(ARCHIVO_TAREAS)
    
    cant_causas = len(df_causas_totales) if not df_causas_totales.empty else 0
    cant_clientes = len(df_causas_totales['Cliente'].dropna().unique()) if not df_causas_totales.empty and 'Cliente' in df_causas_totales.columns else 0
    
    fecha_hoy_str = datetime.now().strftime("%d/%m/%Y")
    tareas_del_dia = len(df_tareas_totales[df_tareas_totales['Fecha_Vencimiento'] == fecha_hoy_str]) if not df_tareas_totales.empty else 0
    
    c1, c2, c3, c4 = st.columns(4)
    with c1: 
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>CAUSAS</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>{cant_causas}</h2></div>", unsafe_allow_html=True)
    with c2: 
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>CLIENTES</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>{cant_clientes}</h2></div>", unsafe_allow_html=True)
    with c3: 
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>TAREAS HOY</h3><h2 style='margin:0; font-size:28px; color:#ff5630;'>{tareas_del_dia}</h2></div>", unsafe_allow_html=True)
    with c4:
        st.markdown(f"<div class='dash-card'><h3 style='margin:0; font-size:14px; color:#6b778c;'>DOCUMENTOS</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>0</h2></div>", unsafe_allow_html=True)
        
elif st.session_state['menu_radio'] == "💰 Contabilidad":
    st.title("💰 Panel de Honorarios y Contabilidad")
    df_c = pd.read_csv(ARCHIVO_BD)
    
    if df_c.empty or 'Total_Honorarios' not in df_c.columns:
        st.info("Aún no hay registros financieros en el sistema. Asegúrate de ingresar las causas y fijar los honorarios.")
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
        
        c1, c2, c3 = st.columns(3)
        with c1: 
            st.markdown(f"<div class='dash-card' style='border-left: 4px solid #0052cc;'><h3 style='margin:0; font-size:14px; color:#6b778c;'>TOTAL FACTURADO</h3><h2 style='margin:0; font-size:28px; color:#172b4d;'>${total_facturado:,.0f}</h2></div>", unsafe_allow_html=True)
        with c2: 
            st.markdown(f"<div class='dash-card' style='border-left: 4px solid #57a15a;'><h3 style='margin:0; font-size:14px; color:#6b778c;'>RECAUDADO</h3><h2 style='margin:0; font-size:28px; color:#57a15a;'>${recaudado:,.0f}</h2></div>", unsafe_allow_html=True)
        with c3: 
            st.markdown(f"<div class='dash-card' style='border-left: 4px solid #ff5630;'><h3 style='margin:0; font-size:14px; color:#6b778c;'>POR COBRAR</h3><h2 style='margin:0; font-size:28px; color:#ff5630;'>${por_cobrar:,.0f}</h2></div>", unsafe_allow_html=True)

        st.markdown("### Estado de Cuentas por Cliente")
        
        df_mostrar = df_financiero[['Cliente', 'ROL', 'Estado_Honorarios', 'Total_Honorarios', 'Cuotas_Totales', 'Cuotas_Pagadas']].copy()
        df_mostrar.columns = ['Cliente', 'Rol Causa', 'Estado', 'Total ($)', 'Mensualidades', 'Cuotas Pagadas']
        
        df_mostrar['Deuda ($)'] = df_mostrar.apply(lambda x: 
            0 if x['Estado'] == 'Pagados' 
            else (x['Total ($)'] - ((x['Total ($)'] / x['Mensualidades'] * x['Cuotas Pagadas']) if x['Mensualidades'] > 0 else 0)), axis=1)
        
        st.dataframe(df_mostrar.style.format({"Total ($)": "${:,.0f}", "Deuda ($)": "${:,.0f}"}), use_container_width=True)

elif st.session_state['menu_radio'] == "💼 Causas":
    df_causas = pd.read_csv(ARCHIVO_BD)
    
    if st.session_state['causa_seleccionada'] is None:
        st.session_state['modo_edicion'] = False
        st.title("💼 Gestión de Causas")
        
        rol = st.selectbox("🔍 Buscar ROL:", [""] + df_causas['ROL'].astype(str).tolist())
        if rol != "" and st.button("Abrir Expediente", type="primary"): 
            st.session_state['causa_seleccionada'] = rol
            st.rerun()
            
        st.dataframe(df_causas[['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'Estado_Honorarios']], use_container_width=True)
        
    else:
        rol_actual = st.session_state['causa_seleccionada']
        idx = df_causas[df_causas['ROL'] == rol_actual].index[0]
        c_data = df_causas.loc[idx]
        
        col_back, col_title = st.columns([1, 10])
        with col_back:
            if st.button("⬅ Volver al Listado", on_click=limpiar_causa): 
                pass
            
        st.markdown(f"<h2>Causa {c_data.get('CARATULADO','')}</h2>", unsafe_allow_html=True)
        
        col_izq, col_der = st.columns([2.5, 1.2])
        with col_der:
            col_btn1, col_btn2 = st.columns([2, 1])
            with col_btn2:
                if st.button("❌ Cancelar" if st.session_state['modo_edicion'] else "✏️ Editar"):
                    st.session_state['modo_edicion'] = not st.session_state['modo_edicion']
                    st.rerun()
                    
            if st.session_state['modo_edicion']:
                with st.form("form_edicion_derecha"):
                    st.markdown("#### Datos Generales")
                    n_tribunal = st.text_input("Tribunal", str(c_data.get('TRIBUNAL','')))
                    n_serv = st.text_input("Servicio", str(c_data.get('Servicio','')))
                    n_negocio = st.selectbox("Tipo de Negocio", ["Grupo Defensa", "Propio"], index=0 if c_data.get('Tipo_Negocio') == "Grupo Defensa" else 1)
                    
                    st.markdown("#### Cliente")
                    n_cliente = st.text_input("Nombre", str(c_data.get('Cliente','')))
                    
                    st.markdown("#### 💰 Honorarios")
                    opciones_hon = ["Sin fijar", "Pagados", "Pendientes"]
                    idx_hon = opciones_hon.index(c_data.get('Estado_Honorarios', 'Sin fijar')) if c_data.get('Estado_Honorarios') in opciones_hon else 0
                    n_estado_hon = st.selectbox("Estado de Honorarios", opciones_hon, index=idx_hon)
                    
                    if n_estado_hon == "Pendientes":
                        n_tot_hon = st.number_input("Total Honorarios ($)", value=int(c_data.get('Total_Honorarios', 0)), step=10000)
                        n_cuo_tot = st.number_input("Cantidad de Mensualidades", value=int(c_data.get('Cuotas_Totales', 0)), step=1, min_value=1)
                        n_cuo_pag = st.number_input("Mensualidades Pagadas", value=int(c_data.get('Cuotas_Pagadas', 0)), step=1, min_value=0)
                    elif n_estado_hon == "Pagados":
                        n_tot_hon = st.number_input("Monto Pagado Total ($)", value=int(c_data.get('Total_Honorarios', 0)), step=10000)
                        n_cuo_tot = 1
                        n_cuo_pag = 1 
                    else:
                        n_tot_hon = 0
                        n_cuo_tot = 0
                        n_cuo_pag = 0
                    
                    if st.form_submit_button("💾 Guardar Cambios", type="primary"):
                        if not n_cliente or n_cliente == "--":
                            st.error("⚠️ El nombre del cliente es obligatorio para fijar honorarios.")
                        else:
                            df_causas.at[idx, 'TRIBUNAL'] = n_tribunal
                            df_causas.at[idx, 'Servicio'] = n_serv
                            df_causas.at[idx, 'Tipo_Negocio'] = n_negocio
                            df_causas.at[idx, 'Cliente'] = n_cliente
                            df_causas.at[idx, 'Estado_Honorarios'] = n_estado_hon
                            df_causas.at[idx, 'Total_Honorarios'] = n_tot_hon
                            df_causas.at[idx, 'Cuotas_Totales'] = n_cuo_tot
                            df_causas.at[idx, 'Cuotas_Pagadas'] = n_cuo_pag
                            
                            df_causas.to_csv(ARCHIVO_BD, index=False)
                            st.session_state['modo_edicion'] = False
                            st.rerun()
            else:
                st.markdown(f"""
                <div class="dash-card">
                    <span style="font-weight:bold; color:#172b4d;">{c_data.get('CARATULADO')}</span><br>
                    <span style="color:#6b778c; font-size:14px;">Cliente: {c_data.get('Cliente')}</span><hr>
                    <span style="font-weight:bold;">Honorarios:</span> {c_data.get('Estado_Honorarios')}<br>
                    <span style="color:#6b778c; font-size:14px;">Total: ${c_data.get('Total_Honorarios',0):,.0f}</span>
                </div>
                """, unsafe_allow_html=True)
                
        with col_izq:
            t_tar = st.tabs(["Tareas de la causa"])[0]
            with t_tar:
                if st.button("+ Nueva tarea", type="primary"): 
                    st.session_state['creando_tarea'] = not st.session_state['creando_tarea']
                    st.rerun()
                    
                if st.session_state['creando_tarea']:
                    with st.form("form_nueva_tarea"):
                        n_tit = st.text_input("Título")
                        n_desc = st.text_area("Descripción")
                        n_f = st.date_input("Vencimiento")
                        if st.form_submit_button("Guardar"):
                            df_t = pd.read_csv(ARCHIVO_TAREAS)
                            nueva_t = {
                                'ID_Tarea': str(uuid.uuid4())[:8], 
                                'ROL': rol_actual, 
                                'Creador': usuario_actual, 
                                'Fecha_Creacion': datetime.now().strftime("%d/%m/%Y"), 
                                'Fecha_Vencimiento': n_f.strftime("%d/%m/%Y"), 
                                'Titulo': n_tit, 
                                'Descripcion': n_desc, 
                                'Estado': 'En progreso', 
                                'Comentarios': '[]', 
                                'Prioridad': 'Media'
                            }
                            df_t = pd.concat([df_t, pd.DataFrame([nueva_t])], ignore_index=True)
                            df_t.to_csv(ARCHIVO_TAREAS, index=False)
                            st.session_state['creando_tarea'] = False
                            st.rerun()
                            
                df_tareas = pd.read_csv(ARCHIVO_TAREAS)
                for _, r in df_tareas[df_tareas['ROL'] == rol_actual].iterrows():
                    st.markdown(f"<div class='dash-card'><strong>{r['Titulo']}</strong> ({r['Estado']})</div>", unsafe_allow_html=True)

elif st.session_state['menu_radio'] == "📄 Contratos":
    st.title("📄 Gestión de Contratos")
    tab_gen, tab_reg = st.tabs(["Generar Nuevo Contrato", "Registro Histórico"])
    
    with tab_gen:
        if not DOCX_READY:
            st.error("⚠️ Falta el motor para generar documentos. Ve a tu archivo `requirements.txt` en GitHub, agrega la palabra `python-docx` en una línea nueva y guarda los cambios.")
        else:
            st.markdown("Rellena los módulos para generar el documento Word automático.")
            with st.form("form_generador", clear_on_submit=False):
                with st.container(border=True):
                    st.markdown("<h4 style='color:#172b4d;'>1. Datos del Servicio</h4>", unsafe_allow_html=True)
                    tipo_servicio = st.selectbox("Tipo de Procedimiento", ["Liquidación voluntaria", "Juicio ejecutivo", "Derecho de familia", "Derecho penal", "Derecho civil"])
                    detalle_servicio = st.text_area("¿Qué incluye el servicio?", height=150, placeholder="- Estudio y análisis de antecedentes...\n- Redacción y presentación de demanda...")
                
                c_abog, c_cli = st.columns(2)
                with c_abog:
                    with st.container(border=True):
                        st.markdown("<h4 style='color:#172b4d;'>2. Datos del Abogado</h4>", unsafe_allow_html=True)
                        abog_nom = st.text_input("Nombre Completo Abogado", placeholder="Ej: Eduardo Riquelme Zambrano")
                        abog_rut = st.text_input("RUT Abogado", placeholder="Ej: 17.427.459-2")
                        abog_dom = st.text_input("Domicilio Profesional", placeholder="Ej: Carlos Antúnez 2025, Providencia")
                        abog_tel = st.text_input("Teléfono Abogado", placeholder="Ej: +569 1234 5678")
                        abog_correo = st.text_input("Correo Electrónico", placeholder="Ej: abogado@correo.cl")
                
                with c_cli:
                    with st.container(border=True):
                        st.markdown("<h4 style='color:#172b4d;'>3. Datos del Cliente</h4>", unsafe_allow_html=True)
                        cli_nom = st.text_input("Nombre Completo Cliente", placeholder="Ej: Natalia Vásquez Lagos")
                        cli_rut = st.text_input("RUT Cliente", placeholder="Ej: 17.578.045-9")
                        cli_dom = st.text_input("Domicilio Cliente", placeholder="Ej: Camino Huape Km 12, Malloa")
                        cli_tel = st.text_input("Teléfono Cliente", placeholder="Ej: +569 8765 4321")
                        cli_correo = st.text_input("Correo Cliente", placeholder="Ej: cliente@correo.cl")
                        
                with st.container(border=True):
                    st.markdown("<h4 style='color:#172b4d;'>4. Honorarios y Pago</h4>", unsafe_allow_html=True)
                    c_pago1, c_pago2 = st.columns(2)
                    with c_pago1:
                        hon_num = st.text_input("Honorarios (Números)", placeholder="Ej: $2.220.000")
                        hon_let = st.text_input("Honorarios (Letras)", placeholder="Ej: dos millones doscientos veinte mil pesos")
                        cuotas_c = st.number_input("Cantidad de Cuotas", min_value=1, max_value=60, value=12)
                        cuotas_m = st.text_input("Monto por Cuota", placeholder="Ej: $185.000")
                        fecha_pago = st.date_input("Fecha de inicio de pagos")
                    with c_pago2:
                        st.markdown("Datos para Transferencia")
                        banco = st.text_input("Banco", placeholder="Ej: Banco Falabella")
                        tipo_cta = st.selectbox("Tipo de Cuenta", ["Cuenta Corriente", "Cuenta Vista", "Cuenta RUT", "Chequera Electrónica"])
                        num_cta = st.text_input("Número de Cuenta", placeholder="Ej: 019996291120")

                btn_gen = st.form_submit_button("📄 Construir Contrato Word", type="primary", use_container_width=True)
                
            if btn_gen:
                datos_contrato = {
                    'tipo_servicio': tipo_servicio, 'detalle_servicio': detalle_servicio,
                    'abogado_nombre': abog_nom, 'abogado_rut': abog_rut, 'abogado_domicilio': abog_dom, 'abogado_tel': abog_tel, 'abogado_correo': abog_correo,
                    'cliente_nombre': cli_nom, 'cliente_rut': cli_rut, 'cliente_domicilio': cli_dom, 'cliente_tel': cli_tel, 'cliente_correo': cli_correo,
                    'honorarios_num': hon_num, 'honorarios_letras': hon_let, 'cuotas_cant': cuotas_c, 'cuotas_monto': cuotas_m, 'fecha_inicio': fecha_pago,
                    'banco': banco, 'tipo_cuenta': tipo_cta, 'num_cuenta': num_cta
                }
                
                doc = crear_contrato_word(datos_contrato)
                if doc:
                    bio = io.BytesIO()
                    doc.save(bio)
                    st.session_state['contrato_generado'] = bio.getvalue()
                    nombre_limpio = cli_nom.replace(' ', '_') if cli_nom else "Sin_Nombre"
                    st.session_state['nombre_archivo'] = f"Contrato_Servicios_{nombre_limpio}.docx"
                    
                    df_contratos = pd.read_csv(ARCHIVO_CONTRATOS)
                    nuevo_c = {
                        'ID': str(uuid.uuid4())[:8], 
                        'Fecha': datetime.now().strftime("%d/%m/%Y"), 
                        'Cliente': cli_nom, 
                        'Servicio': tipo_servicio, 
                        'Honorarios': hon_num
                    }
                    df_contratos = pd.concat([df_contratos, pd.DataFrame([nuevo_c])], ignore_index=True)
                    df_contratos.to_csv(ARCHIVO_CONTRATOS, index=False)
                    st.rerun()

        if st.session_state.get('contrato_generado'):
            st.success("✅ ¡El contrato ha sido redactado con éxito y está listo para descargar!")
            st.download_button(
                label="📥 Descargar Documento Word", 
                data=st.session_state['contrato_generado'], 
                file_name=st.session_state['nombre_archivo'], 
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", 
                type="primary"
            )

    with tab_reg:
        st.markdown("### Registro Histórico de Contratos")
        df_contratos_reg = pd.read_csv(ARCHIVO_CONTRATOS)
        if df_contratos_reg.empty:
            st.info("No hay contratos registrados aún.")
        else:
            st.dataframe(df_contratos_reg, use_container_width=True)

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

elif st.session_state['menu_radio'] == "📋 Agenda":
    st.title("📋 Agenda Diaria")
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    
    st.markdown(f"<p style='color:#6b778c; font-size:16px; margin-bottom: 25px;'>Tareas programadas para hoy: <strong>{fecha_hoy}</strong></p>", unsafe_allow_html=True)
    df_t = pd.read_csv(ARCHIVO_TAREAS)
    
    if df_t.empty:
        st.info("Aún no hay tareas creadas en el sistema.")
    else:
        tareas_hoy = df_t[df_t['Fecha_Vencimiento'] == fecha_hoy].copy()
        if tareas_hoy.empty:
            st.success("🎉 ¡Excelente! No tienes tareas pendientes para el día de hoy.")
        else:
            orden_prioridades = {"Alta": 1, "Media": 2, "Baja": 3}
            tareas_hoy['Orden_Prio'] = tareas_hoy['Prioridad'].map(orden_prioridades).fillna(4)
            tareas_hoy = tareas_hoy.sort_values(by='Orden_Prio')
            
            for idx, row in tareas_hoy.iterrows():
                with st.container(border=True):
                    prio_color = "#ff5630" if row.get('Prioridad') == "Alta" else ("#ffc400" if row.get('Prioridad') == "Media" else "#57a15a")
                    st.markdown(f"<div style='height: 5px; background-color: {prio_color}; border-radius: 5px 5px 0 0; margin: -1rem -1rem 1rem -1rem;'></div>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns([4, 2, 1])
                    with c1:
                        st.markdown(f"<div style='display: flex; align-items: center; margin-bottom: 5px;'><img src='{LOGO_URL}' style='height: 25px; margin-right: 8px;' onerror=\"this.onerror=null; this.src='https://img.icons8.com/color/48/user.png';\"><strong style='font-size:16px; color:#172b4d;'>{row['Titulo']}</strong><span style='font-size:12px; color:{prio_color}; font-weight:bold; margin-left:8px;'>[{row.get('Prioridad', 'Media')}]</span></div>", unsafe_allow_html=True)
                        st.markdown(f"<span style='color:#6b778c;'>{str(row['Descripcion'])[:80]}...</span>", unsafe_allow_html=True)
                    with c2:
                        color_bd = "#ffc400" if row['Estado'] == 'En progreso' else ("#57a15a" if row['Estado'] == 'Aprobada' else "#ff5630")
                        st.markdown(f"<span style='background:{color_bd}; padding:3px 8px; border-radius:10px; font-size:12px; font-weight:bold; color:black;'>{row['Estado']}</span>", unsafe_allow_html=True)
                        st.markdown(f"<span style='color:#172b4d; font-size:14px;'><br>Causa: {row['ROL']}</span>", unsafe_allow_html=True)
                    with c3:
                        st.button("Ir al expediente ➔", key=f"agenda_ir_{row['ID_Tarea']}", on_click=ir_a_expediente, args=(row['ROL'],))

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

elif st.session_state['menu_radio'] == "📥 Excel":
    st.title("📥 Importador de Causas (OJV)")
    archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx", "xls"])
    if archivo and st.button("Procesar"):
        procesar_ojv_completo(archivo)
        st.success("¡Base de datos actualizada con éxito!")

else:
    st.title(f"{st.session_state['menu_radio'].split(' ')[1]}")
    st.info("🚧 Módulo en construcción. Estará disponible en futuras actualizaciones.")