from fastapi import FastAPI, Header, HTTPException
from backend.firebase import verificar_token
from backend.database import historico_collection
from backend.nlp_model import NLPDiagnostico
from backend.laudos import gerar_laudo
from models.sintomas_input import SintomasInput
import json

app = FastAPI()
modelo_nlp = NLPDiagnostico()

with open("dados/sintomas.json", "r", encoding="utf-8") as f:
    sintomas_db = json.load(f)

with open("dados/tratamentos_naturais.json", "r", encoding="utf-8") as f:
    naturais_db = json.load(f)

with open("dados/tratamentos_farmaceuticos.json", "r", encoding="utf-8") as f:
    farmaceuticos_db = json.load(f)

@app.post("/diagnostico/")
def diagnostico(input: SintomasInput, authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "")
    usuario_id = verificar_token(token)

    if not usuario_id:
        raise HTTPException(status_code=401, detail="Token inválido")

    sintomas_relatados = input.texto.lower()
    doencas_detectadas = []
    tratamentos_encontrados = []

    for item in sintomas_db:
        if any(sintoma in sintomas_relatados for sintoma in item["sintomas"]):
            doencas_detectadas.append(item["doenca"])
            tratamentos_encontrados.extend(item.get("tratamentos", []))

    tratamentos_naturais = [t for t in naturais_db if t["doenca"] in doencas_detectadas]
    tratamentos_farmaceuticos = [t for t in farmaceuticos_db if t["doenca"] in doencas_detectadas]

    laudo = gerar_laudo(usuario_id, sintomas_relatados, doencas_detectadas,
                        {"naturais": tratamentos_naturais, "farmaceuticos": tratamentos_farmaceuticos})

    historico_collection.insert_one(laudo)

    return {"laudo": laudo}
