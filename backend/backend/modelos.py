from pydantic import BaseModel

class SintomasInput(BaseModel):
    texto: str
    usuario_id: str
