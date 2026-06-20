import streamlit as st
import pandas as pd
import os
import json
import uuid
from datetime import datetime
from streamlit_calendar import calendar

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="NARRATIA Social Judicial", layout="wide", initial_sidebar_state="expanded")

# --- SISTEMA DE AUTENTICACIÓN (LOGIN) ---
USUARIOS = {
    "Narratia": "20911237",
    "Vfarfan": "vpfm2404"
}

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'username' not in st.session_state: st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.markdown("""
    <style>
        .stApp { background-color: #f4f5f7; }
        #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
        .login-box { background: white; padding: 40px; border-radius: 12px; border: 1px solid #e0e4e8; box-shadow: 0 4px 10px rgba(0,0,0,0.05); text-align: center; }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<br><br><br><br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.5, 2, 1.5])
    with c2:
        st.markdown("<div class='login-box'>", unsafe_allow_html=True)
        st.markdown("<h1 style='color:#172b4d; margin-bottom: 5px;'>NARRATIA</h1>", unsafe_allow_html=True)
        st.markdown("<p style='color:#6b778c; margin-bottom: 30px;'>Inicia sesión en tu espacio de trabajo</p>", unsafe_allow_html=True)
        
        with st.form("login_form"):
            user = st.text_input("Usuario")
            pwd = st.text_input("Contraseña", type="password")
            submit = st.form_submit_button("Ingresar al Sistema", use_container_width=True, type="primary")
            
            if submit:
                if user in USUARIOS and USUARIOS[user] == pwd:
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = user
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña incorrectos.")
        st.markdown("</div>", unsafe_allow_html=True)
    st.stop() 

# --- ARQUITECTURA MULTI-USUARIO ---
usuario_actual = st.session_state['username']
ARCHIVO_BD = f"base_causas_{usuario_actual}.csv"
ARCHIVO_TAREAS = f"base_tareas_{usuario_actual}.csv"

# --- INICIALIZACIÓN DE ESTADOS GLOBALES ---
if 'menu_radio' not in st.session_state: st.session_state['menu_radio'] = "🏠 Muro"
if 'causa_seleccionada' not in st.session_state: st.session_state['causa_seleccionada'] = None
if 'creando_publicacion' not in st.session_state: st.session_state['creando_publicacion'] = False

# Crear base de tareas independiente si no existe
if not os.path.exists(ARCHIVO_TAREAS):
    pd.DataFrame(columns=['ID_Tarea', 'ROL', 'Creador', 'Fecha_Creacion', 'Fecha_Vencimiento', 'Titulo', 'Descripcion', 'Estado', 'Comentarios', 'Tipo']).to_csv(ARCHIVO_TAREAS, index=False)

# Crear base de causas vacía si no existe (multi-usuario)
if not os.path.exists(ARCHIVO_BD):
    pd.DataFrame(columns=['ROL', 'Tribunal', 'CARATULADO', 'Cliente', 'Tipo_Negocio', 'Estado_Causa']).to_csv(ARCHIVO_BD, index=False)

# --- FUNCIONES ---
def procesar_ojv_completo(archivo):
    diccionario_hojas = pd.read_excel(archivo, sheet_name=None)
    mapa = {'ROL': ['ROL', 'RIT', 'Rol', 'Rit'], 'Tribunal': ['TRIBUNAL', 'Tribunal', 'Juzgado', 'Corte'], 'CARATULADO': ['CARATULA', 'Carátula', 'Caratulado', 'Causa']}
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
        df_consolidado['Tipo_Negocio'] = "Grupo Defensa"
        df_consolidado['Estado_Causa'] = "Activa"
        
        # Cargar datos existentes y unirlos para no perder lo anterior
        if os.path.exists(ARCHIVO_BD):
            df_existente = pd.read_csv(ARCHIVO_BD)
            df_consolidado = pd.concat([df_existente, df_consolidado]).drop_duplicates(subset=['ROL'])

        df_consolidado.to_csv(ARCHIVO_BD, index=False)
        return df_consolidado
    return pd.DataFrame()

# --- CSS ESTILO FACEBOOK (Narratia Blue) ---
# Usamos los colores de Facebook/Narratia y la tipografía para dar ese aire familiar.
st.markdown("""
<style>
    /* Estilo General */
    .stApp { background-color: #f0f2f5; } /* Gris claro de fondo de Facebook */
    header { background-color: transparent !important; }
    .stAppDeployButton { display:none; }
    #MainMenu { visibility: hidden; }

    /* Tipografía y Textos */
    h1, h2, h3, p, div, span, strong {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    
    h2 { color: #1c1e21; font-weight: 700; font-size: 24px !important; }

    /* Barra Superior Simulación */
    .fb-header {
        background-color: #ffffff;
        padding: 10px 20px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-radius: 12px;
        margin-bottom: 20px;
    }
    .fb-logo {
        color: #0052cc; /* Tu azul Narratia */
        font-size: 30px;
        font-weight: 800;
        letter-spacing: -1px;
    }

    /* Columnas Tipo Facebook */
    [data-testid="stSidebar"] {
        background-color: #f0f2f5;
        border-right: none;
        padding-top: 20px;
    }
    
    /* Post/Publicación */
    .fb-post {
        background: white;
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #e0e4e8;
        margin-bottom: 20px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    
    .post-header { display: flex; align-items: center; margin-bottom: 15px; }
    .avatar-sim {
        width: 40px; height: 40px;
        border-radius: 50%;
        background-color: #0052cc;
        color: white;
        display: flex; align-items: center; justify-content: center;
        font-weight: bold; font-size: 18px;
        margin-right: 12px;
    }
    
    .author-name { color: #1c1e21; font-weight: 600; font-size: 15px; }
    .post-time { color: #65676b; font-size: 13px; }
    .rol-tag { color: #65676b; font-size: 13px; font-weight: 600; margin-left: 5px; }

    .post-title { color: #1c1e21; font-weight: 700; font-size: 18px; margin-bottom: 10px; }
    .post-content { color: #1c1e21; font-size: 15px; line-height: 1.4; white-space: pre-wrap; margin-bottom: 15px; }
    
    /* Botones de Reacción Simulación */
    .post-actions {
        border-top: 1px solid #e0e4e8;
        border-bottom: 1px solid #e0e4e8;
        padding: 8px 0;
        display: flex;
        justify-content: space-around;
        margin-bottom: 15px;
    }
    
    .action-btn {
        color: #65676b;
        font-weight: 600;
        font-size: 14px;
        display: flex; align-items: center; gap: 8px;
        cursor: pointer;
    }
    
    /* Comentarios */
    .fb-comment { display: flex; margin-bottom: 15px; gap: 10px; }
    .comment-avatar {
        width: 32px; height: 32px;
        border-radius: 50%;
        background-color: #42526e;
        color: white;
        display: flex; align-items: center; justify-content: center;
        font-weight: bold; font-size: 14px;
    }
    
    .comment-box {
        background-color: #f0f2f5;
        border-radius: 18px;
        padding: 10px 15px;
        max-width: 85%;
    }
    
    .comment-author { color: #1c1e21; font-weight: 600; font-size: 13px; }
    .comment-text { color: #1c1e21; font-size: 14px; }
    
    /* Widgets Laterales */
    .fb-widget { background: white; border-radius: 12px; padding: 15px; margin-bottom: 20px; border: 1px solid #e0e4e8; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .widget-title { color: #65676b; font-weight: 600; font-size: 17px; margin-bottom: 15px; }

    /* Sidenav items style */
    .stRadio > label {
        padding: 8px 12px;
        border-radius: 8px;
        cursor: pointer;
    }
    .stRadio > label:hover {
        background-color: #e4e6eb;
    }
</style>
""", unsafe_allow_html=True)

# --- CABECERA SUPERIOR SIMULADA ---
st.markdown(f"""
<div class="fb-header">
    <div class="fb-logo">narratia</div>
    <div style="font-weight:600; color:#1c1e21;">{usuario_actual.capitalize()} Judicial</div>
</div>
""", unsafe_allow_html=True)

# --- MENÚ LATERAL (IZQUIERDA) ---
with st.sidebar:
    # Simulación de menú de Facebook
    st.markdown(f"""
    <div style="display:flex; align-items:center; margin-bottom:20px;">
        <div class="avatar-sim">{usuario_actual[0].upper()}</div>
        <div style="color:#1c1e21; font-weight:600; font-size:16px;">{usuario_actual}</div>
    </div>
    """, unsafe_allow_html=True)
    
    # Navegación
    menu_opciones = ["🏠 Muro", "👥 Cusas (Páginas)", "📅 Calendario de Plazos", "☑️ Tareas Pendientes", "📥 Importar Excel"]
    menu = st.radio("Menú", menu_opciones, index=menu_opciones.index(st.session_state['menu_radio']), label_visibility="collapsed")
    st.session_state['menu_radio'] = menu
    
    st.write("---")
    # Botón de búsqueda de expedientes rápido
    df_causas = pd.read_csv(ARCHIVO_BD) if os.path.exists(ARCHIVO_BD) else pd.DataFrame()
    if not df_causas.empty:
        st.markdown("### Mis Causas")
        roles = df_causas['ROL'].tolist()
        for rol in roles[:10]: # Solo mostramos 10 para no saturar
            if st.button(f"📄 {rol}", key=f"side_rol_{rol}"):
                st.session_state['causa_seleccionada'] = rol
                st.session_state['menu_radio'] = "👥 Cusas (Páginas)"
                st.rerun()

    st.write("---")
    if st.button("🚪 Cerrar Sesión", use_container_width=True): 
        for key in list(st.session_state.keys()): del st.session_state[key]
        st.rerun()

# --- VISTAS PRINCIPALES ---
df_causas = pd.read_csv(ARCHIVO_BD) if os.path.exists(ARCHIVO_BD) else pd.DataFrame()
df_tareas = pd.read_csv(ARCHIVO_TAREAS) if os.path.exists(ARCHIVO_TAREAS) else pd.DataFrame()

# --- VISTA: MURO (La "HomePage") ---
if st.session_state['menu_radio'] == "🏠 Muro":
    # Muro Tipo Facebook: Widgets izquierda, Post centro, Widgets derecha
    c_izq, c_centro, c_der = st.columns([1, 2.5, 1])
    
    # Widget Izquierda: Atajos
    with c_izq:
        st.markdown("<div class='fb-widget'><div class='widget-title'>Atajos</div><div>👥 Amigos (Clientes)</div><div>📄 Smart Documents</div><div>✈️ Mensajería</div></div>", unsafe_allow_html=True)

    # Centro: El "Feed" de actividad judicial
    with c_centro:
        st.markdown("<h2>Muro de Actividad Judicial</h2>", unsafe_allow_html=True)
        
        # Simulación de "Qué estás pensando" para crear una Tarea
        if not st.session_state['creando_publicacion']:
            with st.container(border=True):
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap:10px; cursor:pointer;" onclick="document.getElementById('crear_post_btn').click();">
                    <div class="avatar-sim" style="width:35px; height:35px; font-size:16px;">{usuario_actual[0]}</div>
                    <div style="background-color:#f0f2f5; color:#65676b; border-radius:20px; padding:8px 15px; flex-grow:1; font-size:15px;">¿Qué tarea judicial hay que hacer, {usuario_actual}?</div>
                </div>
                """, unsafe_allow_html=True)
                if st.button("Crear publicación / tarea", key="crear_post_btn"):
                    st.session_state['creando_publicacion'] = True; st.rerun()
        else:
            # Formulario para crear un "Post" (Tarea)
            with st.container(border=True):
                with st.form("form_nuevo_post"):
                    st.markdown("#### ✨ Crear Publicación / Tarea")
                    # Debemos asignar el post a un expediente
                    if df_causas.empty: st.warning("Importa causas primero.")
                    else:
                        rol_post = st.selectbox("Asignar a Causa (Rol)", df_causas['ROL'].tolist())
                        titulo_post = st.text_input("Nomenclatura (Título)", placeholder="Ej: Ingreso de demanda")
                        desc_post = st.text_area("Descripción detallada (El Post)", placeholder="Describe qué hay que hacer...")
                        fecha_post = st.date_input("Fecha de vencimiento (Plazo)")
                        
                        col_btns = st.columns([1, 5])
                        if col_btns[0].form_submit_button("💾 Publicar", type="primary"):
                            nueva_t = {
                                'ID_Tarea': str(uuid.uuid4())[:8], 'ROL': rol_post, 'Creador': usuario_actual,
                                'Fecha_Creacion': datetime.now().strftime("%d/%m/%Y"), 'Fecha_Vencimiento': fecha_post.strftime("%d/%m/%Y"),
                                'Titulo': titulo_post, 'Descripcion': desc_post, 'Estado': 'En progreso', 'Comentarios': '[]', 'Tipo': 'Movimiento'
                            }
                            df_updated = pd.concat([df_tareas, pd.DataFrame([nueva_t])], ignore_index=True)
                            df_updated.to_csv(ARCHIVO_TAREAS, index=False)
                            st.session_state['creando_publicacion'] = False; st.rerun()
                        if col_btns[1].form_submit_button("Cancelar"):
                            st.session_state['creando_publicacion'] = False; st.rerun()

        # FEED: Mostrar todas las tareas como "Posts" de Facebook
        if df_tareas.empty:
            st.info("No hay actividad judicial para mostrar. ¡Crea tu primera tarea!")
        else:
            # Ordenamos por fecha de creación (los más nuevos primero)
            df_feed = df_tareas.sort_values(by='Fecha_Creacion', ascending=False)
            
            for idx_t, row_t in df_feed.iterrows():
                # Obtenemos datos de la causa para el post
                causa_info = df_causas[df_causas['ROL'] == row_t['ROL']]
                caratulado = causa_info.iloc[0]['CARATULADO'] if not causa_info.empty else "Causa Desconocida"

                # RECUADRO POST FACEBOOK
                with st.container():
                    # HEADER
                    st.markdown(f"""
                    <div class="fb-post">
                        <div class="post-header">
                            <div class="avatar-sim">{row_t['Creador'][0].upper()}</div>
                            <div>
                                <div class="author-name">{row_t['Creador']} ➔ <span style="color:#0052cc;">{caratulado}</span></div>
                                <div class="post-time">{row_t['Fecha_Creacion']} • N° Tarea {row_t['ID_Tarea']} <span class="rol-tag">Rol: {row_t['ROL']}</span></div>
                            </div>
                        </div>
                        <div class="post-title">{row_t['Titulo']}</div>
                        <div class="post-content">{row_t['Descripcion']}</div>
                        
                        <div style="font-size:14px; color:#65676b; margin-bottom:10px;">📅 Vence: <strong>{row_t['Fecha_Vencimiento']}</strong> | Estado: <strong style="color:#ffc400;">{row_t['Estado']}</strong></div>

                        <div class="post-actions">
                            <div class="action-btn">👍 <span style="font-weight:normal;">Me gusta</span></div>
                            <div class="action-btn">💬 <span style="font-weight:normal;">Comentar</span></div>
                            <div class="action-btn">🔗 <span style="font-weight:normal;">Compartir</span></div>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    # COMENTARIOS DEL POST
                    comentarios = json.loads(row_t['Comentarios'])
                    
                    if comentarios:
                        for c in comentarios:
                            st.markdown(f"""
                            <div class="fb-comment">
                                <div class="comment-avatar">{c['autor'][0].upper()}</div>
                                <div class="comment-box">
                                    <div class="comment-author">{c['autor']}</div>
                                    <div class="comment-text">{c['texto']}</div>
                                    <div class="post-time">{c['fecha']}</div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                    
                    # Formulario de Comentario de Streamlit integrado al final del post
                    with st.form(key=f"form_feed_coment_{row_t['ID_Tarea']}", clear_on_submit=True):
                        col_c1, col_c2 = st.columns([8, 1.2])
                        nuevo_comentario_feed = col_c1.text_input("Escribe un comentario...", label_visibility="collapsed", placeholder="Escribe un comentario...")
                        if col_c2.form_submit_button("Comentar", use_container_width=True):
                            if nuevo_comentario_feed.strip():
                                comentarios.append({
                                    "autor": usuario_actual, "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"), "texto": nuevo_comentario_feed.strip()
                                })
                                df_tareas.at[idx_t, 'Comentarios'] = json.dumps(comentarios)
                                df_tareas.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
                    
                    st.markdown("</div>", unsafe_allow_html=True) # Cierre fb-post

    # Widget Derecha: Recordatorios y Contactos
    with c_der:
        st.markdown("<div class='fb-widget'><div class='widget-title'>Recordatorios Plazos</div><div>⏰ Vencimiento MON/EXC en 48h</div><div>⏰ Plazo Oposición en Causa Y hoy</div></div>", unsafe_allow_html=True)
        # Simulación contactos
        st.markdown("<div class='fb-widget'><div class='widget-title'>Contactos (Abogados)</div><div>🟢 Narratia (Tú)</div><div>🟢 Vfarfan</div><div>⚪ Abogado 3</div></div>", unsafe_allow_html=True)

# --- VISTA: CAUSAS (Simulación de "Páginas") ---
elif st.session_state['menu_radio'] == "👥 Cusas (Páginas)":
    if df_causas.empty:
        st.title("👥 Mis Causas Judiciales (Páginas)")
        st.info("Aún no tienes causas. Ve a 'Importar Excel'.")
    else:
        if st.session_state['causa_seleccionada'] is None:
            st.title("👥 Mis Causas Judiciales (Páginas)")
            st.write("Selecciona una 'página' de causa para ver su muro específico:")
            # Listado de causas como "Páginas" de Facebook
            for idx_c, row_c in df_causas.iterrows():
                with st.container(border=True):
                    col_p1, col_p2 = st.columns([5, 1])
                    with col_p1:
                        st.markdown(f"### 📄 {row_c['CARATULADO']}\nRol: {row_c['ROL']} | Tribunal: {row_c['Tribunal']}")
                    with col_p2:
                        st.write("<br>", unsafe_allow_html=True)
                        if st.button("Ver Muro ➔", key=f"ver_page_{row_c['ROL']}"):
                            st.session_state['causa_seleccionada'] = row_c['ROL']; st.rerun()
        else:
            # VISTA DE PERFIL DE CAUSA ESPECÍFICO
            rol_actual = st.session_state['causa_seleccionada']
            c_data = df_causas[df_causas['ROL'] == rol_actual].iloc[0]
            
            # HEADER DEL PERFIL DE CAUSA (Simulando portada de Facebook)
            col_bk, col_tit = st.columns([1, 10])
            col_bk.write("<br>", unsafe_allow_html=True)
            if col_bk.button("⬅", key="back_muro"): st.session_state['causa_seleccionada'] = None; st.rerun()
            
            st.markdown(f"""
            <div style="background:#f0f2f5; border-radius:12px; margin-bottom:20px; border: 1px solid #e0e4e8;">
                <div style="background-color:#0052cc; height:150px; border-radius:12px 12px 0 0;"></div> <div style="padding:20px; display:flex; align-items: flex-end; margin-top:-60px;">
                    <div class="avatar-sim" style="width:120px; height:120px; font-size:60px; border:5px solid white;">{c_data['CARATULADO'][0].upper()}</div>
                    <div style="margin-left:20px; margin-bottom:10px;">
                        <h1 style="margin:0; color:#1c1e21;">Causa {c_data['CARATULADO']}</h1>
                        <div style="color:#65676b; font-weight:600; font-size:16px;">Rol: {rol_actual} | {c_data['Tribunal']}</div>
                    </div>
                </div>
                <div style="border-top: 1px solid #e0e4e8; padding: 10px 20px; display:flex; gap:20px;">
                    <strong style="color:#0052cc; border-bottom:3px solid #0052cc; padding-bottom:5px;">Muro</strong>
                    <span style="color:#65676b; font-weight:600;">Información</span>
                    <span style="color:#65676b; font-weight:600;">Documentos</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # ESTRUCTURA DE PERFIL: Info izquierda, Posts derecha
            col_pi, col_pd = st.columns([1, 2.3])
            
            with col_pi:
                st.markdown(f"""
                <div class="fb-widget">
                    <div class="widget-title">Información de la Causa</div>
                    <div style="margin-bottom:10px; color:#1c1e21; font-size:15px;">🏛️ Tribunal: {c_data['Tribunal']}</div>
                    <div style="margin-bottom:10px; color:#1c1e21; font-size:15px;">👤 Cliente: {c_data['Cliente']}</div>
                    <div style="margin-bottom:10px; color:#1c1e21; font-size:15px;">👥 Negocio: {c_data['Tipo_Negocio']}</div>
                    <div style="margin-bottom:10px; color:#1c1e21; font-size:15px;">🟢 Estado: {c_data['Estado_Causa']}</div>
                </div>
                """, unsafe_allow_html=True)
                
                # Simulación de Fotos (OJV)
                st.markdown(f"<div class='fb-widget'><div class='widget-title'>Documentos Recientes (OJV)</div><div style='background-color:#e4e6eb; height:60px; border-radius:8px; margin-bottom:10px; padding:10px; color:#65676b;'>📄 PDF Demanda.pdf</div><div style='background-color:#e4e6eb; height:60px; border-radius:8px; padding:10px; color:#65676b;'>📄 Resolución 01-01.pdf</div></div>", unsafe_allow_html=True)

            with col_pd:
                # El Feed específico de esta causa
                tareas_causa = df_tareas[df_tareas['ROL'] == rol_actual]
                st.markdown(f"<h2>Muro de {rol_actual}</h2>", unsafe_allow_html=True)
                
                if tareas_causa.empty:
                    st.info("No hay publicaciones judiciales en esta causa. ¡Crea una nueva tarea desde el Muro principal!")
                else:
                    df_causa_feed = tareas_causa.sort_values(by='Fecha_Creacion', ascending=False)
                    for idx_t, row_t in df_causa_feed.iterrows():
                        with st.container():
                            st.markdown(f"""
                            <div class="fb-post">
                                <div class="post-header">
                                    <div class="avatar-sim">{row_t['Creador'][0].upper()}</div>
                                    <div>
                                        <div class="author-name">{row_t['Creador']}</div>
                                        <div class="post-time">{row_t['Fecha_Creacion']} • {row_t['ID_Tarea']}</div>
                                    </div>
                                </div>
                                <div class="post-title">{row_t['Titulo']}</div>
                                <div class="post-content">{row_t['Descripcion']}</div>
                                <div style="font-size:14px; color:#65676b; margin-bottom:10px;">📅 Plazo: <strong>{row_t['Fecha_Vencimiento']}</strong> | Estado: <strong>{row_t['Estado']}</strong></div>
                                <div class="post-actions">
                                    <div class="action-btn">👍 Me gusta</div>
                                    <div class="action-btn">💬 Comentar</div>
                                </div>
                            """, unsafe_allow_html=True)
                            
                            # Comentarios (duplicamos la lógica del feed para consistencia)
                            comentarios = json.loads(row_t['Comentarios'])
                            if comentarios:
                                for c in comentarios:
                                    st.markdown(f"""
                                    <div class="fb-comment">
                                        <div class="comment-avatar">{c['autor'][0].upper()}</div>
                                        <div class="comment-box">
                                            <div class="comment-author">{c['autor']}</div>
                                            <div class="comment-text">{c['texto']}</div>
                                            <div class="post-time">{c['fecha']}</div>
                                        </div>
                                    </div>
                                    """, unsafe_allow_html=True)
                            
                            # Input comentario específico
                            with st.form(key=f"form_page_coment_{row_t['ID_Tarea']}", clear_on_submit=True):
                                col_cp1, col_cp2 = st.columns([8, 2])
                                nuevo_comentario_page = col_cp1.text_input("Escribe un comentario...", label_visibility="collapsed", placeholder="Escribe un comentario...")
                                if col_cp2.form_submit_button("Comentar"):
                                    if nuevo_comentario_page.strip():
                                        comentarios.append({
                                            "autor": usuario_actual, "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"), "texto": nuevo_comentario_page.strip()
                                        })
                                        # Actualizar base de tareas (necesitamos encontrar el índice original)
                                        df_tareas.at[idx_t, 'Comentarios'] = json.dumps(comentarios)
                                        df_tareas.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()

                            st.markdown("</div>", unsafe_allow_html=True) # Cierre fb-post

# --- VISTA: IMPORTAR (Sigue igual, pero con estilo) ---
elif st.session_state['menu_radio'] == "📥 Importar Excel":
    st.title("📥 Importador de Causas")
    with st.container(border=True):
        st.markdown("Sube tu archivo Excel (.xlsx) para cargar tus causas de forma masiva.")
        archivo = st.file_uploader("Sube Excel", type=["xlsx", "xls"], label_visibility="collapsed")
        if archivo and st.button("Procesar Excel", type="primary"):
            with st.spinner("Procesando..."):
                procesar_ojv_completo(archivo)
                st.success("✅ Causas importadas correctamente. Revisa el menú 'Mis Causas'.")
                st.session_state['menu_radio'] = "🏠 Muro"
                st.rerun()

# --- VISTAS NO IMPLEMENTADAS ---
else:
    st.title(st.session_state['menu_radio'])
    st.info("Este módulo está en construcción, pero la estructura visual tipo Facebook ya está lista.")