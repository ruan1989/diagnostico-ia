from pydantic import BaseModel
from typing import List, Any, Optional


class SintomaInput(BaseModel):
    relato: str
    usuario_id: Optional[str] = None


class SintomasInput(BaseModel):
    texto: str
    usuario_id: str


class Recomendacoes(BaseModel):
    naturais: List[Any]
    farmacos: List[Any]


class DiagnosticoResposta(BaseModel):
    doencas: List[str]
    recomendacoes: Recomendacoes
    proximos_passos: str
