from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os

from backend.firebase import initialize_firebase, verificar_token
from backend.database import get_db
from backend.modelos import SintomaInput, DiagnosticoResposta, Recomendacoes
from backend.nlp_engine import analisar_sintomas
from backend.tratamento_engine import obter_tratamentos
from backend.laudos import gerar_laudo

app = FastAPI(
    title="Diagnóstico IA",
    description="Sistema de Saúde Preditiva com IA — análise de sintomas e recomendações de tratamento.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializa Firebase ao subir (requer credenciais configuradas)
try:
    initialize_firebase()
except Exception as e:
    print(f"[AVISO] Firebase não inicializado: {e}")


@app.get("/")
def index():
    return {"status": "Sistema de Saúde Preditiva com IA ativo"}


@app.post("/api/diagnostico", response_model=DiagnosticoResposta)
def diagnosticar(
    entrada: SintomaInput,
    authorization: str = Header(default=None),
):
    # Autenticação via Firebase token (opcional se FIREBASE_AUTH_DISABLED=true)
    auth_desabilitada = os.environ.get("FIREBASE_AUTH_DISABLED", "false").lower() == "true"

    usuario_id = entrada.usuario_id or "anonimo"

    if not auth_desabilitada:
        if not authorization:
            raise HTTPException(status_code=401, detail="Token de autenticação ausente.")
        token = authorization.removeprefix("Bearer ").strip()
        uid = verificar_token(token)
        if not uid:
            raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
        usuario_id = uid

    # Análise de sintomas
    resultado = analisar_sintomas(entrada.relato)
    naturais, farmacologicos = obter_tratamentos(resultado["doencas"])

    # Gera e persiste o laudo
    laudo = gerar_laudo(
        usuario_id=usuario_id,
        sintomas=entrada.relato,
        possiveis_doencas=resultado["doencas"],
        tratamentos={"naturais": naturais, "farmacos": farmacologicos},
    )

    try:
        db = get_db()
        db["historico"].insert_one({**laudo})
    except Exception as e:
        print(f"[AVISO] Falha ao salvar no MongoDB: {e}")

    return DiagnosticoResposta(
        doencas=resultado["doencas"],
        recomendacoes=Recomendacoes(naturais=naturais, farmacos=farmacologicos),
        proximos_passos=resultado["proximos_passos"],
    )


@app.get("/api/historico/{usuario_id}")
def obter_historico(usuario_id: str, authorization: str = Header(default=None)):
    auth_desabilitada = os.environ.get("FIREBASE_AUTH_DISABLED", "false").lower() == "true"

    if not auth_desabilitada:
        if not authorization:
            raise HTTPException(status_code=401, detail="Token ausente.")
        token = authorization.removeprefix("Bearer ").strip()
        uid = verificar_token(token)
        if not uid or uid != usuario_id:
            raise HTTPException(status_code=403, detail="Acesso negado.")

    try:
        db = get_db()
        registros = list(db["historico"].find({"usuario_id": usuario_id}, {"_id": 0}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar histórico: {e}")

    return {"usuario_id": usuario_id, "total": len(registros), "historico": registros}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
