import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# --- 1. CONFIGURACIÓN DE FIREBASE ---
# Asegúrate de que el nombre del archivo .json sea exacto
cred = credentials.Certificate("industrialappmifc-firebase-adminsdk-fbsvc-68f32372ac.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Referencia rápida a tu documento principal
doc_ref_sistema = db.collection("estado_actual").document("sistema")

# --- 2. MODELOS DE DATOS (Lo que envía el Arduino) ---

class DatoNivel(BaseModel):
    nivel: int

class DatoBomba(BaseModel):
    activa: bool

class DatoFallo(BaseModel):
    mensaje: str
    codigo: str
    criticidad: str  # "Alta", "Media", "Baja"

# --- 3. LA APLICACIÓN ---
app = FastAPI()

# ==========================================
# FUNCIONES PARA EL ARDUINO
# ==========================================

@app.get("/arduino/leer_ordenes")
def leer_ordenes():
    """
    El Arduino llama a esto constantemente (polling) para saber:
    1. Si debe estar en modo automático.
    2. Si el usuario pidió encender la bomba manualmente (bomba_activa).
    """
    try:
        doc = doc_ref_sistema.get()
        if not doc.exists:
            return {"modo_automatico": False, "bomba_activa": False}
        
        data = doc.to_dict()
        return {
            "modo_automatico": data.get("modo_automatico", False),
            "bomba_activa": data.get("bomba_activa", False) # Si es true, el Arduino debe encender relé
        }
    except Exception as e:
        print(f"Error leyendo ordenes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/arduino/actualizar_nivel")
def actualizar_nivel(dato: DatoNivel):
    """
    El Arduino llama a esto cada cierto tiempo (ej. 5 seg) para:
    1. Actualizar el nivel de agua visual.
    2. Enviar el 'latido' (heartbeat) para decir "Estoy Online".
    """
    try:
        doc_ref_sistema.update({
            "nivel_agua": dato.nivel,
            "ultima_conexion": firestore.SERVER_TIMESTAMP
        })
        return {"status": "Nivel actualizado"}
    except Exception as e:
        print(f"Error actualizando nivel: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/arduino/confirmar_bomba")
def confirmar_bomba(dato: DatoBomba):
    """
    El Arduino llama a esto CUANDO cambia el estado físico de la bomba.
    - Si estaba en Auto y decide prenderla -> Llama con true.
    - Si estaba en Auto y decide apagarla -> Llama con false.
    Esto mantiene la App sincronizada con la realidad.
    """
    try:
        doc_ref_sistema.update({
            "bomba_activa": dato.activa,
            "ultima_conexion": firestore.SERVER_TIMESTAMP # También sirve de latido
        })
        return {"status": f"Bomba reportada como {'ON' if dato.activa else 'OFF'}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/arduino/reportar_fallo")
def reportar_fallo(fallo: DatoFallo):
    """
    El Arduino llama a esto SOLO si detecta un error nuevo.
    Guarda en el historial y actualiza el error activo en el dashboard.
    """
    try:
        # 1. Crear registro en el historial (registros_fallos)
        nuevo_fallo = {
            "mensaje": fallo.mensaje,
            "codigo_error": fallo.codigo,
            "criticidad": fallo.criticidad,
            "timestamp": firestore.SERVER_TIMESTAMP
        }
        db.collection("registros_fallos").add(nuevo_fallo)

        # 2. Actualizar la alerta en tiempo real (estado_actual)
        doc_ref_sistema.update({
            "error_activo": f"{fallo.codigo}: {fallo.mensaje}"
        })
        
        return {"status": "Fallo registrado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Endpoint extra para limpiar errores ---
@app.post("/arduino/limpiar_error")
def limpiar_error():
    """
    El Arduino llama a esto cuando el sistema vuelve a la normalidad
    para quitar la alerta roja de la App.
    """
    try:
        doc_ref_sistema.update({
            "error_activo": None
        })
        return {"status": "Errores limpiados"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Ejecución ---
if __name__ == "__main__":
    # Escucha en todas las interfaces de red (0.0.0.0) para que el Arduino pueda entrar
    uvicorn.run(app, host="0.0.0.0", port=8000)