from transformers import pipeline


class NLPDiagnostico:
    def __init__(self):
        self.qa_pipeline = pipeline(
            "question-answering",
            model="distilbert-base-uncased-distilled-squad"
        )

    def responder_pergunta(self, pergunta: str, contexto: str) -> str:
        resultado = self.qa_pipeline(question=pergunta, context=contexto)
        return resultado["answer"]
