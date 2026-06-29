import firebase_admin
from firebase_admin import credentials, auth
import os


def initialize_firebase():
    if firebase_admin._apps:
        return
    cert_path = os.environ.get("FIREBASE_CREDENTIALS", "backend/credenciais-firebase.json")
    cred = credentials.Certificate(cert_path)
    firebase_admin.initialize_app(cred)


def verificar_token(token: str):
    try:
        decoded = auth.verify_id_token(token)
        return decoded["uid"]
    except Exception as e:
        print(f"Erro ao verificar token: {e}")
        return None
