import firebase_admin
from firebase_admin import credentials, auth

cred = credentials.Certificate("backend/credenciais-firebase.json")
firebase_admin.initialize_app(cred)

def verificar_token(token):
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token['uid']
    except Exception as e:
        print(f"Erro ao verificar token: {e}")
        return None
