import streamlit as st
import pandas as pd
import os
import json
import uuid
from datetime import datetime

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="NARRATIA | Sistema Judicial", layout="wide")

# --- AUTENTICACIÓN ---
USUARIOS = {"Narratia": "20911237", "Vfarfan": "vpfm2404"}
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'username' not in st.session_state: st.session_state['username'] = ""

if not st.session_state['logged_in']:
    st.title("🔐 Login NARRATIA")
    user = st.text_input("Usuario")
    pwd = st.text_input("Contraseña", type="password")
    if st.button("Ingresar"):
        if user in USUARIOS and USUARIOS[user] == pwd:
            st.session_state['logged_in'] = True; st.session_state['username'] = user; st.rerun()
        else: st.error("Acceso denegado")
    st.stop()

usuario_actual = st.session_state['username']
ARCHIVO_BD = f"base_causas_{usuario_actual}.csv"
ARCHIVO_TAREAS = f"base_tareas_{usuario_actual}.csv"

# --- CSS DE PRIORIDADES ---
st.markdown("""
<style>
    .prio-Alta { border-left: 10px solid #ff4b4b; padding-left: 15px; }
    .prio-Media { border-left: 10px solid #ffdb4b; padding-left: 15px; }
    .prio-Baja { border-left: 10px solid #4bff4b; padding-left: 15px; }
</style>
""", unsafe_allow_html=True)

# --- FUNCIONES ---
def init_files():
    if not os.path.exists(ARCHIVO_TAREAS):
        pd.DataFrame(columns=['ID', 'ROL', 'Titulo', 'Desc', 'Prioridad', 'Fecha', 'Comentarios']).to_csv(ARCHIVO_TAREAS, index=False)
    if not os.path.exists(ARCHIVO_BD):
        pd.DataFrame(columns=['ROL', 'Tribunal', 'CARATULADO', 'Cliente', 'RUT', 'Telefono', 'Servicio']).to_csv(ARCHIVO_BD, index=False)

init_files()

# --- MENÚ ---
with st.sidebar:
    st.title(f"Usuario: {usuario_actual}")
    menu = st.radio("Navegación", ["🏠 Panel", "💼 Causas", "👥 Clientes", "☑️ Tareas", "📥 Excel"])
    if st.button("Cerrar Sesión"): 
        for key in st.session_state.keys(): del st.session_state[key]
        st.rerun()

# --- VISTAS ---
if menu == "🏠 Panel":
    st.title("Panel de Control")
    st.metric("Total Causas", len(pd.read_csv(ARCHIVO_BD)) if os.path.exists(ARCHIVO_BD) else 0)

elif menu == "💼 Causas":
    df_c = pd.read_csv(ARCHIVO_BD)
    if 'causa_sel' not in st.session_state: st.session_state['causa_sel'] = None
    
    if st.session_state['causa_sel'] is None:
        st.title("💼 Mis Causas")
        rol = st.selectbox("Seleccionar ROL", df_c['ROL'].unique() if not df_c.empty else [])
        if st.button("Abrir Expediente"): st.session_state['causa_sel'] = rol; st.rerun()
    else:
        rol = st.session_state['causa_sel']
        st.title(f"Expediente: {rol}")
        if st.button("⬅ Volver"): del st.session_state['causa_sel']; st.rerun()
        
        st.subheader("Nueva Tarea")
        with st.form("crear_t"):
            t = st.text_input("Título"); d = st.text_area("Descripción"); p = st.selectbox("Prioridad", ["Alta", "Media", "Baja"]); f = st.date_input("Vencimiento")
            if st.form_submit_button("Crear"):
                df_t = pd.read_csv(ARCHIVO_TAREAS)
                nueva = pd.DataFrame([{'ID': uuid.uuid4().hex[:8], 'ROL': rol, 'Titulo': t, 'Desc': d, 'Prioridad': p, 'Fecha': str(f), 'Comentarios': '[]'}])
                pd.concat([df_t, nueva]).to_csv(ARCHIVO_TAREAS, index=False); st.rerun()

elif menu == "👥 Clientes":
    st.title("👥 Gestión de Clientes")
    df = pd.read_csv(ARCHIVO_BD) if os.path.exists(ARCHIVO_BD) else pd.DataFrame()
    if 'cli_sel' not in st.session_state: st.session_state['cli_sel'] = None
    
    if st.session_state['cli_sel'] is None:
        for cli in df['Cliente'].dropna().unique():
            if st.button(f"👤 {cli}"): st.session_state['cli_sel'] = cli; st.rerun()
    else:
        c = st.session_state['cli_sel']
        st.title(f"Ficha: {c}")
        if st.button("⬅ Volver"): del st.session_state['cli_sel']; st.rerun()
        st.write(df[df['Cliente'] == c])
        st.subheader("Causas relacionadas")
        st.dataframe(df[df['Cliente'] == c][['ROL', 'CARATULADO', 'Tribunal']])

elif menu == "☑️ Tareas":
    st.title("☑️ Gestión de Tareas")
    df_t = pd.read_csv(ARCHIVO_TAREAS)
    for i, r in df_t.iterrows():
        estilo = f"prio-{r['Prioridad']}"
        st.markdown(f"<div class='{estilo}'>", unsafe_allow_html=True)
        st.write(f"### {r['Titulo']} | Prioridad: {r['Prioridad']}")
        st.write(f"**Vence:** {r['Fecha']} | **Causa:** {r['ROL']}")
        st.write(r['Desc'])
        
        # Adjuntos
        adjunto = st.file_uploader(f"Adjuntar documento para {r['ID']}", key=f"file_{r['ID']}")
        if adjunto: st.success("Archivo subido con éxito")
        
        # Comentarios
        coms = json.loads(r['Comentarios'])
        for c in coms: st.write(f"💬 {c}")
        nuevo_c = st.text_input("Nuevo comentario", key=f"com_{r['ID']}")
        if st.button("Publicar", key=f"btn_{r['ID']}"):
            coms.append(f"{usuario_actual}: {nuevo_c}")
            df_t.at[i, 'Comentarios'] = json.dumps(coms)
            df_t.to_csv(ARCHIVO_TAREAS, index=False); st.rerun()
        st.markdown("</div><br>", unsafe_allow_html=True)

elif menu == "📥 Excel":
    st.title("📥 Importar")
    up = st.file_uploader("Subir", type=['xlsx'])
    if up and st.button("Procesar"): 
        pd.read_excel(up).to_csv(ARCHIVO_BD, index=False); st.success("Base cargada")