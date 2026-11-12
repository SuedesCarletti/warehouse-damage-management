import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Se alterar estes escopos, delete o arquivo token.pickle.
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def main():
    creds = None
    # O arquivo token.pickle armazena os tokens de acesso e atualização do usuário.
    # Ele é criado automaticamente quando o fluxo de autorização é concluído pela primeira vez.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # Se não houver credenciais válidas disponíveis, peça ao usuário para fazer login.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Salve as credenciais para a próxima execução
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    print("Autenticação realizada com sucesso! O arquivo token.pickle foi atualizado.")

if __name__ == '__main__':
    main()
