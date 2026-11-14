#!/usr/bin/env python3
"""
deploy_to_railway.py
Script automatizado para preparar e fazer deploy no Railway
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}{Colors.END}\n")

def print_step(number, text):
    print(f"{Colors.CYAN}{Colors.BOLD}[{number}] {text}{Colors.END}")

def print_success(text):
    print(f"{Colors.GREEN}{text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.WARNING} {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.FAIL}{text}{Colors.END}")

def run_command(cmd, check=True):
    """Executa comando shell"""
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            check=check,
            capture_output=True,
            text=True
        )
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def check_prerequisites():
    """Verifica pré-requisitos"""
    print_step(1, "Verificando pré-requisitos")
    
    # Git instalado
    success, _ = run_command("git --version", check=False)
    if success:
        print_success("Git instalado")
    else:
        print_error("Git não instalado")
        return False
    
    # Python instalado
    success, output = run_command("python --version", check=False)
    if success:
        print_success(f"Python instalado: {output.strip()}")
    else:
        print_error("Python não instalado")
        return False
    
    # Dentro de um repositório git
    success, _ = run_command("git rev-parse --git-dir", check=False)
    if success:
        print_success("Dentro de repositório Git")
    else:
        print_error("Não está dentro de um repositório Git")
        return False
    
    return True

def backup_files():
    """Faz backup dos arquivos originais"""
    print_step(2, "Fazendo backup de arquivos originais")
    
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"backup_deploy_{timestamp}"
    
    os.makedirs(backup_dir, exist_ok=True)
    
    files_to_backup = {
        'app.py': 'app_old.py',
        'railway.toml': 'railway_old.toml'
    }
    
    for original, backup_name in files_to_backup.items():
        if os.path.exists(original):
            backup_path = os.path.join(backup_dir, backup_name)
            shutil.copy2(original, backup_path)
            print_success(f"Backup: {original} → {backup_path}")
    
    print_success(f"Backups salvos em: {backup_dir}/")
    return backup_dir

def update_files():
    """Atualiza arquivos com versões otimizadas"""
    print_step(3, "Atualizando arquivos para versões otimizadas")
    
    replacements = {
        'app_robust.py': 'app.py',
        'railway_optimized.toml': 'railway.toml'
    }
    
    for source, dest in replacements.items():
        if os.path.exists(source):
            shutil.copy2(source, dest)
            print_success(f"Atualizado: {dest} (origem: {source})")
        else:
            print_warning(f"Arquivo fonte não encontrado: {source}")
            print_warning(f"Mantendo {dest} como está")
    
    return True

def run_diagnostics():
    """Executa diagnósticos"""
    print_step(4, "Executando diagnósticos pré-deploy")
    
    if not os.path.exists('railway_diagnostics.py'):
        print_warning("railway_diagnostics.py não encontrado - pulando diagnóstico")
        return True
    
    print("Executando testes...")
    success, output = run_command("python railway_diagnostics.py", check=False)
    
    if success:
        print_success("Todos os diagnósticos passaram!")
        return True
    else:
        print_error("Alguns diagnósticos falharam")
        print(output)
        
        response = input(f"\n{Colors.WARNING}Continuar mesmo assim? (s/N): {Colors.END}").lower()
        return response == 's'

def check_environment_vars():
    """Verifica variáveis de ambiente essenciais"""
    print_step(5, "Verificando variáveis de ambiente")
    
    required = ['SECRET_KEY']
    recommended = [
        'BASE_URL',
        'COMPANY_NAME',
        'GOOGLE_SHEETS_CREDENTIALS',
        'SPREADSHEET_ID',
        'MAIL_USERNAME',
        'MAIL_PASSWORD'
    ]
    
    print("\nVariáveis essenciais:")
    for var in required:
        if os.environ.get(var):
            print_success(f"{var}: Configurado")
        else:
            print_error(f"{var}: NÃO CONFIGURADO")
            print(f"\n{Colors.FAIL}ERRO: Variável essencial faltando!{Colors.END}")
            print(f"Configure no Railway Dashboard > Settings > Variables")
            return False
    
    print("\nVariáveis recomendadas:")
    for var in recommended:
        if os.environ.get(var):
            print_success(f"{var}: Configurado")
        else:
            print_warning(f"{var}: Não configurado (opcional)")
    
    print()
    print_warning("IMPORTANTE: Certifique-se de configurar estas variáveis no Railway!")
    print(f"{Colors.CYAN}Railway Dashboard > Settings > Variables{Colors.END}")
    
    return True

def git_status():
    """Mostra status do Git"""
    print_step(6, "Status do repositório Git")
    
    success, output = run_command("git status --short")
    
    if output.strip():
        print("Arquivos modificados:")
        print(output)
    else:
        print_success("Nenhuma modificação pendente")
    
    return True

def commit_changes():
    """Commita mudanças"""
    print_step(7, "Commitando mudanças")
    
    # Adicionar arquivos
    files_to_add = [
        'app.py',
        'railway.toml',
        'app_robust.py',
        'railway_optimized.toml',
        'railway_diagnostics.py',
        'RAILWAY_DEPLOY.md',
        'RAILWAY_TROUBLESHOOTING.md'
    ]
    
    for file in files_to_add:
        if os.path.exists(file):
            run_command(f"git add {file}", check=False)
    
    # Commit
    commit_msg = "feat: Implementar deploy robusto para Railway com healthcheck otimizado"
    success, _ = run_command(f'git commit -m "{commit_msg}"', check=False)
    
    if success:
        print_success("Mudanças commitadas")
        return True
    else:
        print_warning("Nada para commitar (talvez já commitado)")
        return True

def push_to_git():
    """Push para repositório"""
    print_step(8, "Fazendo push para repositório")
    
    success, output = run_command("git push", check=False)
    
    if success:
        print_success("Push realizado com sucesso!")
        print_success("Railway iniciará deploy automaticamente")
        return True
    else:
        print_error("Erro ao fazer push")
        print(output)
        return False

def show_next_steps():
    """Mostra próximos passos"""
    print_header("PREPARAÇÃO COMPLETA!")
    
    print(f"{Colors.BOLD}Próximos Passos:{Colors.END}\n")
    
    steps = [
        "1⃣  Acesse Railway Dashboard: https://railway.app/dashboard",
        "",
        "2️⃣  Verifique o deploy em andamento:",
        "    • Deployments > Latest",
        "    • Acompanhe os logs em tempo real",
        "",
        "3️⃣  Aguarde o build completar (~13 minutos):",
        "    • Build: Instalando dependências",
        "    • Healthcheck: Verificando saúde do app",
        "    • Deploy: Ativando aplicação",
        "",
        "4️⃣  Verifique se passou no healthcheck:",
        "    • Procure por: 'Healthcheck passed' ",
        "    • Status deve ficar: HEALTHY 🟢",
        "",
        "5️⃣  Teste a aplicação:",
        "    • Acesse: https://seu-app.railway.app",
        "    • Teste: https://seu-app.railway.app/health",
        "    • Faça login e crie um ticket de teste",
        "",
        "6️⃣  Configure variáveis (se ainda não fez):",
        "    • Settings > Variables",
        "    • Adicione todas as variáveis necessárias",
        "    • Redeploy se necessário",
        "",
        "7⃣  Monitore logs:",
        "    • Procure por: 'SISTEMA INICIADO COM SUCESSO'",
        "    • Verifique avisos e erros",
        ""
    ]
    
    for step in steps:
        if step.startswith(('1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣')):
            print(f"{Colors.CYAN}{step}{Colors.END}")
        elif step.startswith('   '):
            print(f"  {Colors.BLUE}{step}{Colors.END}")
        else:
            print(step)
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}Deploy iniciado! Boa sorte!{Colors.END}\n")
    
    print(f"{Colors.WARNING}Documentação adicional:{Colors.END}")
    print(f"  • RAILWAY_DEPLOY.md - Guia completo de deploy")
    print(f"  • RAILWAY_TROUBLESHOOTING.md - Solução de problemas")
    print()

def main():
    """Função principal"""
    print_header("DEPLOY AUTOMATIZADO PARA RAILWAY")
    
    print("Este script irá:")
    print("  • Verificar pré-requisitos")
    print("  • Fazer backup dos arquivos originais")
    print("  • Atualizar para versões otimizadas")
    print("  • Executar diagnósticos")
    print("  • Commitar e fazer push")
    print()
    
    response = input(f"{Colors.WARNING}Deseja continuar? (s/N): {Colors.END}").lower()
    
    if response != 's':
        print(f"\n{Colors.WARNING}Deploy cancelado.{Colors.END}\n")
        return
    
    try:
        # Executar passos
        if not check_prerequisites():
            print_error("Pré-requisitos não atendidos")
            return
        
        backup_dir = backup_files()
        
        if not update_files():
            print_error("Erro ao atualizar arquivos")
            return
        
        if not run_diagnostics():
            print_error("Diagnósticos falharam - corrija antes de continuar")
            return
        
        if not check_environment_vars():
            print_warning("Configure as variáveis no Railway antes de fazer deploy")
        
        git_status()
        
        if not commit_changes():
            print_error("Erro ao commitar")
            return
        
        if not push_to_git():
            print_error("Erro ao fazer push")
            return
        
        # Sucesso!
        show_next_steps()
        
        print(f"\n{Colors.CYAN}Backup dos arquivos originais em: {backup_dir}/{Colors.END}\n")
        
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}Deploy interrompido pelo usuário{Colors.END}\n")
    except Exception as e:
        print_error(f"Erro durante deploy: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
