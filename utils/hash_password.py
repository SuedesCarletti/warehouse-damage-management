"""
utils/hash_password.py

Script utilitário para gerar hashes de senha para o Google Sheets.
Execute no terminal: python utils/hash_password.py
"""

import getpass
from werkzeug.security import generate_password_hash

def main():
    """Função principal para gerar hash de senha."""
    print("=" * 50)
    print("Gerador de Hash de Senha para o Sistema")
    print("=" * 50)
    print("Este script gera um hash seguro para ser copiado na coluna 'senha' da sua planilha 'Usuários'.")
    print("NUNCA armazene a senha em texto plano!\n")

    try:
        password = getpass.getpass("Digite a senha a ser codificada: ")
        if not password:
            print("\nSenha não pode ser vazia.")
            return

        password_confirm = getpass.getpass("Confirme a senha: ")

        if password != password_confirm:
            print("\nAs senhas não coincidem.")
            return

        # Gera o hash usando o método recomendado pela Werkzeug
        # Inclui o método e o salt automaticamente
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        print("\n" + "-" * 50)
        print("Hash gerado com sucesso!")
        print("COPIE o texto abaixo e COLE na planilha:")
        print("-" * 50)
        print(f"\n{hashed_password}\n")
        print("=" * 50)

    except KeyboardInterrupt:
        print("\n\nOperação cancelada pelo usuário.")
    except Exception as e:
        print(f"\nOcorreu um erro inesperado: {e}")

if __name__ == "__main__":
    main()
