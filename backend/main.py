from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import auth as firebase_auth
from pymongo import MongoClient
from pydantic import BaseModel
import uvicorn
import json
import os
from firebase import initialize_firebase
from database import get_db
from modelos import SintomaInput, DiagnosticoResposta
from nlp_engine import analisar_sintomas
from tratamento_engine import obter_tratamentos

# Inicialização do app
app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Firebase
initialize_firebase()

# MongoDB
client = MongoClient(os.environ.get("MONGODB_URI", "mongodb://localhost:27017"))
db = client["diagnostico_db"]

@app.get("/")
def index():
    return {"status": "Sistema de Saúde Preditiva com IA ativo"}

@app.post("/api/diagnostico", response_model=DiagnosticoResposta)
def diagnosticar_sintomas(sintomas: SintomaInput, request: Request):
    try:
        # IA: analisa os sintomas
        resultado_diagnostico = analisar_sintomas(sintomas.relato)

        # Recomendação de tratamento
        naturais, farmacologicos = obter_tratamentos(resultado_diagnostico["doencas"])

        # Salva no MongoDB
        db.historico.insert_one({
            "sintomas": sintomas.relato,
            "diagnostico": resultado_diagnostico,
            "tratamentos": {
                "naturais": naturais,
                "farmacos": farmacologicos
            }
        })

        return {
            "doencas": resultado_diagnostico["doencas"],
            "recomendacoes": {
                "naturais": naturais,
                "farmacos": farmacologicos
            },
            "proximos_passos": resultado_diagnostico["proximos_passos"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
      sistema principal backend
