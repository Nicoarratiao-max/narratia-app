import streamlit as st
import pandas as pd
import os
import json
import uuid
import base64
from datetime import datetime
from streamlit_calendar import calendar

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="NARRATIA | Sistema Judicial", layout="wide", initial_sidebar_state="expanded")

# --- FUNCIONES PRINCIPALES ---
def obtener_saludo():
    hora = datetime.now().hour
    return "Buenos días" if 0 <= hora < 12 else "Buenas tardes"

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
                    df_pro[col_ideal] = df_hoja[p]; break
        if not df_pro.empty and 'ROL' in df_pro.columns:
            df_pro['Origen_OJV'] = nombre_hoja
            lista_final.append(df_pro)
    if lista_final:
        df_consolidado = pd.concat(lista_final, ignore_index=True).dropna(subset=['ROL'])
        df_consolidado['Estado'] = "Pendiente"
        df_consolidado['Prioridad'] = "Normal"
        df_consolidado['Tipo_Negocio'] = "Grupo Defensa"
        cols_extra = ['Servicio', 'Teléfono', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal']
        for col in cols_extra:
            if col not in df_consolidado.columns: df_consolidado[col] = "--"
        df_consolidado.to_csv(ARCHIVO_BD, index=False)
        return df_consolidado
    return pd.DataFrame()

# --- SISTEMA DE AUTENTICACIÓN Y NOMBRES REALES ---
USUARIOS = {
    "Narratia": "20911237",
    "Vfarfan": "vpfm2404",
    "Gdonoso": "gdonoso123" # Cambia esta contraseña por la real de Gabriel
}

NOMBRES_REALES = {
    "Narratia": "Nicolás Arratia",
    "Vfarfan": "Valentina Farfán",
    "Gdonoso": "Gabriel Donoso"
}

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'username' not in st.session_state: st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.markdown("""
    <style>
        [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
        #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
        [data-testid="stForm"] {
            background-color: white !important;
            border-radius: 16px !important;
            border: 1px solid #e0e4e8 !important;
            padding: 40px 30px !important;
            box-shadow: 0 4px 15px rgba(0,0,0,0.05) !important;
        }
        p, label, span, div { color: #172b4d !important; }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.5, 2, 1.5])
    with c2:
        with st.form("login_form", clear_on_submit=False):
            st.markdown(f"""
            <div style='text-align: center; margin-bottom: 20px;'>
                <img src='{LOGO_URL}' style='width: 140px; margin-bottom: 15px;'>
                <h1 style='color:#172b4d; margin-top: 0; margin-bottom: 5px; font-size: 36px; font-weight: 800;'>NARRATIA</h1>
                <p style='color:#6b778c; font-size: 15px; margin:0;'>Inicia sesión en tu espacio de trabajo</p>
            </div>
            """, unsafe_allow_html=True)
            
            user = st.text_input("Usuario")
            pwd = st.text_input("Contraseña", type="password")
            st.write("") 
            submit = st.form_submit_button("Ingresar al Sistema", use_container_width=True, type="primary")
            
            if submit:
                if user in USUARIOS and USUARIOS[user] == pwd:
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = user
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña incorrectos.")
    st.stop()

# --- ARQUITECTURA MULTI-USUARIO (DATOS INDEPENDIENTES) ---
usuario_actual = st.session_state['username']
nombre_real_usuario = NOMBRES_REALES.get(usuario_actual, usuario_actual.capitalize()) # Transforma el username al nombre real

ARCHIVO_BD = f"base_causas_{usuario_actual}.csv"
ARCHIVO_TAREAS = f"base_tareas_{usuario_actual}.csv"

# --- INICIALIZACIÓN DE ESTADOS GLOBALES DE NAVEGACIÓN ---
if 'menu_radio' not in st.session_state: st.session_state['menu_radio'] = "🏠 Inicio"
if 'causa_seleccionada' not in st.session_state: st.session_state['causa_seleccionada'] = None
if 'cliente_seleccionado' not in st.session_state: st.session_state['cliente_seleccionado'] = None
if 'modo_edicion' not in st.session_state: st.session_state['modo_edicion'] = False
if 'creando_tarea' not in st.session_state: st.session_state['creando_tarea'] = False
if 'editando_tarea' not in st.session_state: st.session_state['editando_tarea'] = None

if not os.path.exists(ARCHIVO_TAREAS):
    pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Prioridad']).to_csv(ARCHIVO_TAREAS, index=False)
else:
    df_t_check = pd.read_csv(ARCHIVO_TAREAS)
    if 'Prioridad' not in df_t_check.columns:
        df_t_check['Prioridad'] = 'Media'
        df_t_check.to_csv(ARCHIVO_TAREAS, index=False)

if not os.path.exists(ARCHIVO_BD):
    pd.DataFrame(columns=['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'RUT', 'Teléfono', 'Tipo_Negocio', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal']).to_csv(ARCHIVO_BD, index=False)

# --- CSS DE ALTA FIDELIDAD Y BLOQUEO DE MODO OSCURO ---
st.markdown("""
<style>
    /* FORZAR MODO CLARO */
    [data-testid="stAppViewContainer"], .stApp { background-color: #f4f5f7 !important; }
    [data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e0e4e8 !important; }
    [data-testid="stHeader"] { background-color: transparent !important; }
    
    .stMarkdown, p, span, label, h1, h2, h3, h4, h5, h6 { color: #172b4d !important; }
    
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
    .stAppDeployButton {display:none;}
    
    .info-card { background: white !important; border-radius: 12px; padding: 20px; border: 1px solid #e0e4e8; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
    .info-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .info-title { font-weight: 600; font-size: 16px; color: #172b4d; }
    
    .badge-active { background: #57a15a !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    .badge-propio { background: #0052cc !important; color: white !important; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
    
    .info-group { margin-bottom: 12px; }
    .info-label { font-size: 13px; color: #6b778c !important; margin-bottom: 2px; }
    .info-value { font-size: 15px; color: #172b4d !important; }
    
    [data-testid="stVerticalBlockBorderWrapper"] { background-color: white !important; border-radius: 12px !important; border: 1px solid #e0e4e8 !important; box-shadow: 0 1px 3px rgba(0,0,0,0.04) !important; }
    [data-testid="stForm"] { border: none; padding: 0; margin-top: 10px; background-color: transparent !important; box-shadow: none !important;}
</style>
""", unsafe_allow_html=True)

# --- MENÚ LATERAL ---
with st.sidebar:
    # ELIMINADO EL "Usuario: Narratia" DE ABAJO
    st.markdown(f"## NARRATIA", unsafe_allow_html=True)
    st.write("---")
    menu_opciones = ["🏠 Inicio", "📅 Calendario", "📋 Agenda", "📄 Generador de contratos", "📆 Estado diario", "☑️ Tareas", "💼 Causas", "👥 Clientes", "📑 Smart documents", "✈️ Mensajería", "⚙️ Automatizaciones", "📊 Informes", "📥 Excel", "📈 Marketing"]
    menu = st.radio("Navegación", menu_opciones, index=menu_opciones.index(st.session_state['menu_radio']), key="radio_nav")
    st.session_state['menu_radio'] = menu
    st.write("---")
    if st.button("🚪 Cerrar Sesión", use_container_width=True): 
        for key in list(st.session_state.keys()): del st.session_state[key]
        st.rerun()

# --- CONTROLADOR DE VISTAS ---
if st.session_state['menu_radio'] == "🏠 Inicio":
    # LOGO ARRIBA DEL SALUDO Y NOMBRE REAL
    st.markdown(f"<img src='{LOGO_URL}' style='height: 90px; margin-bottom: -15px;'>", unsafe_allow_html=True)
    st.title(f"{obtener_saludo()}, {nombre_real_usuario}")
    
    total = len(pd.read_csv(ARCHIVO_BD)) if os.path.exists(ARCHIVO_BD) else 0
    df_tareas_total = pd.read_csv(ARCHIVO_TAREAS) if os.path.exists(ARCHIVO_TAREAS) else pd.DataFrame()
    tareas_activas = len(df_tareas_total[df_tareas_total['Estado'] == 'En progreso']) if not df_tareas_total.empty else 0
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Casos urgentes", "0")
    c2.metric("Total Causas", total)
    c3.metric("Tareas Activas", tareas_activas)
    c4.metric("Documentos", "36")

elif st.session_state['menu_radio'] == "📅 Calendario":
    st.title("📅 Calendario de Tareas")
    col_cal, col_side = st.columns([3, 1])
    eventos_calendario = []
    df_t = pd.read_csv(ARCHIVO_TAREAS) if os.path.exists(ARCHIVO_TAREAS) else pd.DataFrame()
    
    if not df_t.empty:
        for idx, r in df_t.iterrows():
            try:
                d_obj = datetime.strptime(str(r['Fecha_Vencimiento']), "%d/%m/%Y")
                d_str = d_obj.strftime("%Y-%m-%d")
                bg_color = "#ff5630" if r.get('Prioridad') == "Alta" else ("#ffc400" if r.get('Prioridad') == "Media" else "#57a15a")
                eventos_calendario.append({"title": f"{r['Titulo']}", "start": d_str, "backgroundColor": bg_color, "textColor": "white", "borderColor": bg_color})
            except: pass
                
    opciones_calendario = {"initialView": "dayGridMonth", "locale": "es", "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,timeGridWeek"}}
    with col_cal:
        calendario_estado = calendar(events=eventos_calendario, options=opciones_calendario, key="calendario_app")
        
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
                    if tareas_dia.empty: st.write("Sin tareas para este día.")
                    else:
                        for _, td in tareas_dia.iterrows():
                            color_dot = "#ffc400" if td['Estado'] == 'En progreso' else ("#57a15a" if td['Estado'] == 'Aprobada' else "#ff5630")
                            prio_txt_color = "#ff5630" if td.get('Prioridad') == 'Alta' else ("#ffc400" if td.get('Prioridad') == 'Media' else "#57a15a")
                            st.markdown(f"<div style='margin-bottom:5px; border-left:3px solid {color_dot}; padding-left:10px;'><strong style='color:#172b4d;'>{td['Titulo']}</strong> <span style='font-size:11px; color:{prio_txt_color}; font-weight:bold;'>({td.get('Prioridad', 'Media')})</span><br><span style='font-size:13px; color:#6b778c;'>{td['ROL']}</span></div>", unsafe_allow_html=True)
                            if st.button("Ir al expediente ➔", key=f"cal_ir_{td['ID_Tarea']}"):
                                st.session_state['causa_seleccionada'] = td['ROL']
                                st.session_state['menu_radio'] = "💼 Causas"
                                st.rerun()
                            st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)
            except: st.write("Selecciona un día en el calendario.")

elif st.session_state['menu_radio'] == "☑️ Tareas":
    st.title("☑️ Gestor Global de Tareas")
    df_t = pd.read_csv(ARCHIVO_TAREAS) if os.path.exists(ARCHIVO_TAREAS) else pd.DataFrame()
    if df_t.empty: st.info("No hay tareas creadas en el sistema.")
    else:
        for idx, row in df_t.iterrows():
            with st.container(border=True):
                prio_color = "#ff5630" if row.get('Prioridad') == "Alta" else ("#ffc400" if row.get('Prioridad') == "Media" else "#57a15a")
                st.markdown(f"<div style='height: 5px; background-color: {prio_color}; border-radius: 5px 5px 0 0; margin: -1rem -1rem 1rem -1rem;'></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns([4, 2, 1])
                with c1:
                    creador_nombre_real = NOMBRES_REALES.get(row['Creador'], row['Creador'])
                    st.markdown(f"""
                    <div style='display: flex; align-items: center; margin-bottom: 5px;'>
                        <img src='{LOGO_URL}' style='height: 25px; margin-right: 8px;' onerror="this.onerror=null; this.src='https://img.icons8.com/color/48/user.png';">
                        <strong style='font-size:16px; color:#172b4d;'>{row['Titulo']}</strong>
                        <span style='font-size:12px; color:{prio_color}; font-weight:bold; margin-left:8px;'>[{row.get('Prioridad', 'Media')}]</span>
                    </div>
                    """, unsafe_allow_html=True)
                    st.markdown(f"<span style='color:#6b778c;'>{row['Descripcion'][:60]}...</span>", unsafe_allow_html=True)
                with c2:
                    color_bd = "#ffc400" if row['Estado'] == 'En progreso' else ("#57a15a" if row['Estado'] == 'Aprobada' else "#ff5630")
                    st.markdown(f"<span style='background:{color_bd}; padding:3px 8px; border-radius:10px; font-size:12px; font-weight:bold; color:black;'>{row['Estado']}</span>", unsafe_allow_html=True)
                    st.markdown(f"<span style='color:#172b4d; font-size:14px;'><br>Causa: {row['ROL']} | Vence: {row['Fecha_Vencimiento']}</span>", unsafe_allow_html=True)
                with c3:
                    if st.button("Ir al expediente ➔", key=f"global_ir_{row['ID_Tarea']}"):
                        st.session_state['causa_seleccionada'] = row['ROL']
                        st.session_state['menu_radio'] = "💼 Causas"
                        st.rerun()

elif st.session_state['menu_radio'] == "👥 Clientes":
    if not os.path.exists(ARCHIVO_BD): st.info("Importa datos en 'Excel'.")
    else:
        df_causas = pd.read_csv(ARCHIVO_BD)
        if st.session_state['cliente_seleccionado'] is None:
            st.title("👥 Gestión de Clientes")
            st.markdown("Haz clic en cualquier celda o botón de cliente para acceder de inmediato a su ficha unificada:")
            clientes_unicos = df_causas['Cliente'].dropna().unique().tolist()
            
            c_grid1, c_grid2, c_grid3 = st.columns(3)
            for i_cli, cli_nom in enumerate(clientes_unicos):
                c_target = [c_grid1, c_grid2, c_grid3][i_cli % 3]
                if c_target.button(f"👤 {cli_nom}", key=f"select_cli_btn_{i_cli}", use_container_width=True):
                    st.session_state['cliente_seleccionado'] = cli_nom
                    st.rerun()
            st.write("<br>", unsafe_allow_html=True)
            st.dataframe(df_causas[['Cliente', 'RUT', 'Teléfono']].drop_duplicates(subset=['Cliente']).dropna(subset=['Cliente']), use_container_width=True)
        else:
            cli_actual = st.session_state['cliente_seleccionado']
            df_cli = df_causas[df_causas['Cliente'] == cli_actual]
            datos = df_cli.iloc[0]
            if st.button("⬅ Volver al listado"): st.session_state['cliente_seleccionado'] = None; st.rerun()
            st.markdown(f"""
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px;">
                <h2 style="color:#172b4d; margin:0;">Ficha de cliente - {cli_actual} <span style="color:#0052cc; font-size:18px; cursor:pointer;">✏️</span></h2>
                <div style="display:flex; border: 1px solid #0052cc; border-radius:6px; overflow:hidden;">
                    <div style="background:#0052cc; color:white; padding:8px 20px; font-weight:bold; font-size:14px;">Información</div>
                    <div style="background:white; color:#0052cc; padding:8px 20px; font-weight:bold; font-size:14px;">Tareas SAC/EEPP</div>
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
                    <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">Clave única:</div><div style="color:#172b4d; font-size:15px; margin-bottom:20px; display:flex; justify-content:space-between;"><span>🛡️ {datos.get('Clave_unica','*****')}</span><span style="color:#6b778c;">👁️‍🗨️</span></div>
                    <div style="color:#172b4d; font-size:15px; margin-bottom:12px;">📞 {datos.get('Teléfono','--')}</div>
                    <div style="color:#172b4d; font-size:15px; margin-bottom:12px;">📄 {datos.get('Correo','--')}</div>
                    <div style="color:#172b4d; font-size:15px; margin-bottom:30px;">📍 {datos.get('Direccion','--')}</div>
                    <div style="font-weight:bold; color:#172b4d; font-size:15px; margin-bottom:15px;">Información SAC</div>
                    <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">SAC asignado:</div><div style="color:#172b4d; font-size:15px; margin-bottom:15px;">👤 {datos.get('SAC','--')}</div>
                    <div style="color:#6b778c; font-size:13px; margin-bottom:4px;">Sucursal:</div><div style="color:#172b4d; font-size:15px; margin-bottom:10px;">{datos.get('Sucursal','--')}</div>
                </div>
                """, unsafe_allow_html=True)
            with c_der:
                st.markdown("<div style='background:#f8f9fa; padding:20px; border-radius:12px; border:1px solid #e0e4e8; min-height:600px;'><h3 style='color:#172b4d; margin-top:0; margin-bottom:20px;'>Causas</h3>", unsafe_allow_html=True)
                for i, causa in df_cli.iterrows():
                    with st.container(border=True):
                        col_card1, col_card2 = st.columns([5, 1])
                        with col_card1:
                            st.markdown(f"<div><strong style='color:#172b4d; font-size:16px;'>{causa['CARATULADO']}</strong><br><span style='color:#42526e; font-size:14px;'>Rol: {causa['ROL']}</span><br><span style='color:#42526e; font-size:14px;'>👥 {causa.get('Servicio', 'Ejecutivo')}</span><br><span style='color:#42526e; font-size:14px;'>🏛️ {causa.get('TRIBUNAL', 'Sin Tribunal')}</span></div>", unsafe_allow_html=True)
                        with col_card2:
                            color_punto = "#57a15a" if causa['Tipo_Negocio'] == "Grupo Defensa" else "#ff5630"
                            st.markdown(f"<div style='height:12px; width:12px; background:{color_punto}; border-radius:50%; float:right;'></div>", unsafe_allow_html=True)
                            st.write("<br><br>", unsafe_allow_html=True)
                            if st.button("Ir al expediente ➔", key=f"ficha_ir_{causa['ROL']}"):
                                st.session_state['causa_seleccionada'] = causa['ROL']
                                st.session_state['menu_radio'] = "💼 Causas"
                                st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

elif st.session_state['menu_radio'] == "💼 Causas":
    if not os.path.exists(ARCHIVO_BD): st.info("Importa datos en 'Excel'.")
    else:
        df_causas = pd.read_csv(ARCHIVO_BD)
        columnas_req = ['Cliente', 'RUT', 'Servicio', 'Teléfono', 'Tipo_Negocio', 'Clave_unica', 'Correo', 'Direccion', 'SAC', 'Sucursal']
        for c in columnas_req:
            if c not in df_causas.columns: df_causas[c] = "--"
            
        if st.session_state['causa_seleccionada'] is None:
            st.session_state['modo_edicion'] = False
            st.session_state['creando_tarea'] = False
            st.session_state['editando_tarea'] = None
            rol = st.selectbox("🔍 Buscar ROL:", [""] + df_causas['ROL'].astype(str).tolist())
            if rol != "" and st.button("Abrir Expediente"): 
                st.session_state['causa_seleccionada'] = rol
                st.rerun()
            st.dataframe(df_causas[['ROL', 'TRIBUNAL', 'CARATULADO', 'Cliente', 'Tipo_Negocio']], use_container_width=True)
        else:
            rol_actual = st.session_state['causa_seleccionada']
            idx = df_causas[df_causas['ROL'] == rol_actual].index[0]
            c_data = df_causas.loc[idx]
            
            col_back, col_title = st.columns([1, 10])
            with col_back:
                if st.button("⬅ Volver"): st.session_state['causa_seleccionada'] = None; st.rerun()
            with col_title:
                st.markdown(f"<h2>Causa {c_data['CARATULADO']}</h2>", unsafe_allow_html=True)
            st.write("")
            col_izq, col_der = st.columns([2.5, 1.2])
            with col_der:
                col_btn1, col_btn2 = st.columns([2, 1])
                with col_btn2:
                    if st.button("❌ Cancelar" if st.session_state['modo_edicion'] else "✏️ Editar"):
                        st.session_state['modo_edicion'] = not st.session_state['modo_edicion']; st.rerun()
                if st.session_state['modo_edicion']:
                    with st.form("form_edicion_derecha"):
                        st.markdown("#### Editar Causa")
                        n_tribunal = st.text_input("Tribunal", c_data['TRIBUNAL'])
                        n_serv = st.text_input("Servicio", c_data['Servicio'])
                        n_negocio = st.selectbox("Tipo de Negocio", ["Grupo Defensa", "Propio"], index=0 if c_data['Tipo_Negocio'] == "Grupo Defensa" else 1)
                        st.markdown("#### Editar Cliente")
                        n_cliente = st.text_input("Nombre", c_data['Cliente'])
                        n_rut = st.text_input("RUT", c_data['RUT'])
                        n_tel = st.text_input("Teléfono", c_data['Teléfono'])
                        n_correo = st.text_input("Correo", c_data['Correo'])
                        n_dir = st.text_input("Dirección", c_data['Direccion'])
                        n_clave = st.text_input("Clave Única", c_data['Clave_unica'])
                        n_sac = st.text_input("SAC Asignado", c_data['SAC'])
                        n_suc = st.text_input("Sucursal", c_data['Sucursal'])
                        if st.form_submit_button("💾 Guardar Cambios"):
                            df_causas.at[idx, 'TRIBUNAL'] = n_tribunal; df_causas.at[idx, 'Servicio'] = n_serv; df_causas.at[idx, 'Tipo_Negocio'] = n_negocio
                            df_causas.at[idx, 'Cliente'] = n_cliente; df_causas.at[idx, 'RUT'] = n_rut; df_causas.at[idx, 'Teléfono'] = n_tel
                            df_causas.at[idx, 'Correo'] = n_correo; df_causas.at[idx, 'Direccion'] = n_dir; df_causas.at[idx, 'Clave_unica'] = n_clave
                            df_causas.at[idx, 'SAC'] = n_sac; df_causas.at[idx, 'Sucursal'] = n_suc
                            df_causas.to_csv(ARCHIVO_BD, index=False); st.session_state['modo_edicion'] = False; st.rerun()
                else:
                    badge_class = "badge-active" if c_data['Tipo_Negocio'] == "Grupo Defensa" else "badge-propio"
                    st.markdown(f"""
                    <div class="info-card">
                        <div class="info-header"><span class="info-title">Información de la causa</span><span class="{badge_class}">{c_data['Tipo_Negocio']}</span></div>
                        <div class="info-group"><div class="info-label">Causa:</div><div class="info-value">{c_data['CARATULADO']}</div></div>
                        <div class="info-group"><div class="info-label">Rol:</div><div class="info-value">{rol_actual}</div></div>
                        <div class="info-group"><div class="info-label">Tribunal:</div><div class="info-value">{c_data['TRIBUNAL']}</div></div>
                        <div class="info-group"><div class="info-label">Servicio:</div><div class="info-value">{c_data['Servicio']}</div></div>
                    </div>
                    <div class="info-card">
                        <div class="info-header"><span class="info-title">Información del cliente</span></div>
                        <div class="info-group"><div class="info-label">Nombre:</div><div class="info-value">{c_data['Cliente']}</div></div>
                        <div class="info-group"><div class="info-label">RUT:</div><div class="info-value">{c_data['RUT']}</div></div>
                        <div class="info-group"><div class="info-label">Teléfono:</div><div class="info-value">{c_data['Teléfono']}</div></div>
                    </div>
                    """, unsafe_allow_html=True)
            with col_izq:
                t_mov, t_tar, t_leg = st.tabs(["Movimientos", "Tareas", "Movimientos legacy"])
                with t_tar:
                    c_buscar, c_btn_crear = st.columns([3, 1])
                    with c_buscar: filtro_tareas = st.text_input("🔍 Buscar tareas...", label_visibility="collapsed")
                    with c_btn_crear:
                        if st.button("+ Nueva tarea", type="primary", use_container_width=True):
                            st.session_state['creando_tarea'] = not st.session_state['creando_tarea']; st.rerun()
                    if st.session_state['creando_tarea']:
                        with st.container(border=True):
                            with st.form("form_nueva_tarea"):
                                st.markdown("#### ✨ Crear Nueva Tarea")
                                nuevo_titulo = st.text_input("Nomenclatura o Título")
                                nueva_desc = st.text_area("Descripción detallada")
                                prio_seleccionada = st.selectbox("Prioridad", ["Alta", "Media", "Baja"], index=1)
                                nueva_fecha = st.date_input("Fecha de vencimiento")
                                c_guardar, c_cancelar = st.columns([1, 5])
                                if c_guardar.form_submit_button("💾 Guardar"):
                                    df_t = pd.read_csv(ARCHIVO_TAREAS)
                                    nueva_t = {
                                        'ID_Tarea': str(uuid.uuid4())[:8], 'ROL': rol_actual, 'Creador': usuario_actual, # Guarda el ID
                                        'Fecha_Creacion': datetime.now().strftime("%d/%m/%Y"), 'Fecha_Vencimiento': nueva_fecha.strftime("%d/%m/%Y"),
                                        'Titulo': nuevo_titulo, 'Descripcion': nueva_desc, 'Estado': 'En progreso', 'Comentarios': '[]', 'Prioridad': prio_seleccionada
                                    }
                                    df_t = pd.concat([df_t, pd.DataFrame([nueva_t])], ignore_index=True)
                                    df_t.to_csv(ARCHIVO_TAREAS, index=False); st.session_state['creando_tarea'] = False; st.rerun()
                    df_tareas = pd.read_csv(ARCHIVO_TAREAS)
                    tareas_rol = df_tareas[df_tareas['ROL'] == rol_actual]
                    if tareas_rol.empty: st.write("<br>", unsafe_allow_html=True); st.info("Aún no hay tareas registradas para esta causa.")
                    else:
                        st.write("<br>", unsafe_allow_html=True)
                        for idx_t, row_t in tareas_rol.iterrows():
                            with st.container(border=True):
                                border_prio_color = "#ff5630" if row_t.get('Prioridad') == "Alta" else ("#ffc400" if row_t.get('Prioridad') == "Media" else "#57a15a")
                                st.markdown(f"<div style='height: 5px; background-color: {border_prio_color}; border-radius: 5px 5px 0 0; margin: -1rem -1rem 1rem -1rem;'></div>", unsafe_allow_html=True)
                                col_top_left, col_top_right = st.columns([3, 1.8])
                                with col_top_left:
                                    creador_real = NOMBRES_REALES.get(row_t['Creador'], row_t['Creador'])
                                    st.markdown(f"""
                                    <div style='display: flex; align-items: center; margin-bottom: 5px;'>
                                        <img src='{LOGO_URL}' style='height: 25px; margin-right: 8px;' onerror="this.onerror=null; this.src='https://img.icons8.com/color/48/user.png';">
                                        <span style='font-weight: 700; font-size: 15px; color: #172b4d;'>{creador_real}</span>
                                        <span style='font-size:12px; color:{border_prio_color}; font-weight:bold; margin-left:8px;'>[{row_t.get('Prioridad', 'Media')}]</span>
                                    </div>
                                    """, unsafe_allow_html=True)
                                    st.markdown(f"<span style='font-size:13px; color:#6b778c;'>Creado por: {creador_real} • N° tarea {row_t['ID_Tarea']}</span>", unsafe_allow_html=True)
                                    if st.session_state.get('editando_tarea') == row_t['ID_Tarea']:
                                        with st.form(key=f"form_fecha_{row_t['ID_Tarea']}"):
                                            try: d_actual = datetime.strptime(row_t['Fecha_Vencimiento'], "%d/%m/%Y").date()
                                            except: d_actual = datetime.now().date()
                                            n_fecha = st.date_input("Nueva fecha:", d_actual)
                                            cf1, cf2 = st.columns(2)
                                            if cf1.form_submit_button("Guardar"):
                                                df_tareas.at[idx_t, 'Fecha_Vencimiento'] = n_fecha.strftime("%d/%m/%Y")
                                                df_tareas.to_csv(ARCHIVO_TAREAS, index=False); st.session_state['editando_tarea'] = None; st.rerun()
                                            if cf2.form_submit_button("Cancelar"): st.session_state['editando_tarea'] = None; st.rerun()
                                    else:
                                        st.markdown(f"<span style='font-size:13px; color:#6b778c;'>Fecha creación: {row_t['Fecha_Creacion']} • Fecha vencimiento: {row_t['Fecha_Vencimiento']}</span>", unsafe_allow_html=True)
                                with col_top_right:
                                    st.write("")
                                    if row_t['Estado'] == 'En progreso':
                                        btn_cols = st.columns([1, 1, 1.5, 0.5])
                                        if btn_cols[0].button("❌", key=f"rec_{row_t['ID_Tarea']}"):
                                            df_tareas.at[idx_t, 'Estado'] = 'Rechazada'; df_tareas.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                                        if btn_cols[1].button("✅", key=f"apr_{row_t['ID_Tarea']}"):
                                            df_tareas.at[idx_t, 'Estado'] = 'Aprobada'; df_tareas.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                                        btn_cols[2].markdown("<div style='background:#ffc400; color:#172b4d; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:600; text-align:center; margin-top:5px;'>En progreso</div>", unsafe_allow_html=True)
                                        if btn_cols[3].button("✏️", key=f"edit_btn_{row_t['ID_Tarea']}"): st.session_state['editando_tarea'] = row_t['ID_Tarea']; st.rerun()
                                    elif row_t['Estado'] == 'Aprobada':
                                        btn_cols = st.columns([3, 1.5, 0.5])
                                        btn_cols[1].markdown("<div style='background:#57a15a; color:white; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:600; text-align:center; margin-top:5px;'>Aprobada</div>", unsafe_allow_html=True)
                                        if btn_cols[2].button("✏️", key=f"edit_btn_{row_t['ID_Tarea']}"): st.session_state['editando_tarea'] = row_t['ID_Tarea']; st.rerun()
                                    else:
                                        btn_cols = st.columns([3, 1.5, 0.5])
                                        btn_cols[1].markdown("<div style='background:#ff5630; color:white; padding:4px 10px; border-radius:12px; font-size:12px; font-weight:600; text-align:center; margin-top:5px;'>Rechazada</div>", unsafe_allow_html=True)
                                        if btn_cols[2].button("✏️", key=f"edit_btn_{row_t['ID_Tarea']}"): st.session_state['editando_tarea'] = row_t['ID_Tarea']; st.rerun()
                                st.markdown(f"<h3 style='font-size: 18px; color: #172b4d; margin-top: 15px; margin-bottom: 5px;'>{row_t['Titulo']}</h3>", unsafe_allow_html=True)
                                st.markdown(f"<p style='font-size: 15px; color: #172b4d; margin-bottom: 15px; white-space: pre-wrap;'>{row_t['Descripcion']}</p>", unsafe_allow_html=True)
                                
                                comentarios = json.loads(row_t['Comentarios'])
                                comentarios_html = "".join([f"<div style='margin-bottom:15px;'><strong style='color:#172b4d; font-size:14px;'>{c['autor']}</strong> <span style='color:#6b778c; font-size:13px;'>• {c['fecha']}</span><br><span style='color:#42526e; font-size:14px;'>{c['texto']}</span></div>" for c in comentarios]) if comentarios else "<span style='color:#6b778c; font-size:14px;'>No hay comentarios aún.</span>"
                                st.markdown(f"""
                                <div style="background: #f8f9fa; margin: 10px -16px 0 -16px; padding: 12px 20px; border-top: 1px solid #e0e4e8; border-bottom: 1px solid #e0e4e8;">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span style="color:#172b4d; font-size:14px;">Comentarios <span style="background:#e1e4e8; padding:2px 8px; border-radius:12px; font-weight:bold; margin-left:5px; font-size:12px;">{len(comentarios)}</span></span>
                                        <span style="color:#172b4d; font-weight:bold;">^</span>
                                    </div>
                                </div>
                                <div style="padding: 15px 4px 5px 4px;">{comentarios_html}</div>
                                """, unsafe_allow_html=True)
                                
                                archivo_adjunto_coment = st.file_uploader("📎 Adjuntar documento al comentario", key=f"file_uploader_{row_t['ID_Tarea']}", label_visibility="collapsed")
                                with st.form(key=f"form_coment_{row_t['ID_Tarea']}", clear_on_submit=True):
                                    col_inp, col_snd = st.columns([8, 1])
                                    nuevo_comentario = col_inp.text_input("Agregar un comentario...", label_visibility="collapsed", placeholder="Agregar un comentario...")
                                    if col_snd.form_submit_button("Enviar"):
                                        if nuevo_comentario.strip() or archivo_adjunto_coment:
                                            texto_comentario_final = nuevo_comentario.strip()
                                            if archivo_adjunto_coment:
                                                texto_comentario_final += f" <br><em>[📎 Archivo adjunto: {archivo_adjunto_coment.name}]</em>"
                                            comentarios.append({"autor": nombre_real_usuario, "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"), "texto": texto_comentario_final})
                                            df_tareas.at[idx_t, 'Comentarios'] = json.dumps(comentarios)
                                            df_tareas.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()

elif st.session_state['menu_radio'] == "📥 Excel":
    st.title("📥 Importador")
    archivo = st.file_uploader("Sube Excel", type=["xlsx", "xls"])
    if archivo and st.button("Procesar"):
        procesar_ojv_completo(archivo); st.success("Base actualizada.")

else:
    st.title(f"Módulo: {st.session_state['menu_radio'].split(' ')[1]}")
    st.info("En construcción.")