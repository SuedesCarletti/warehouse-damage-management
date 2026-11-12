# Sistema de Chamados TI - HelpDesk

Sistema completo de gerenciamento de chamados técnicos com integração ao Google Sheets e notificações automáticas por email.

## Funcionalidades

- **Interface Responsiva**: Dashboard moderno com Bootstrap 5
- **Sistema de Login**: Controle de acesso por níveis (Usuário, Gestor, Admin) e senha.
- **Controle de Acesso Granular**: Permissão por Empresa, Nível/Cargo e por tela, configurável via Google Sheets.
- **Google Sheets**: Gerenciamento centralizado de usuários, categorias, empresas e permissões.
- **Email Automático**: Notificações para terceiros com tracking
- **Resolução por Email**: Links únicos para resolução
- **Dashboard**: Estatísticas e gráficos em tempo real
- **Painel Admin**: Controle completo do sistema

## Configuração Rápida

### 1. Clonar o Projeto
```bash
git clone <seu-repositorio>
cd sistema-chamados-ti
```

### 2. Configurar Google Sheets
```bash
# Executar script de configuração
python setup_sheets.py
```

**Pré-requisitos Google Sheets:**
1. Conta no Google Cloud Console
2. API do Google Sheets ativada
3. Conta de serviço criada
4. Arquivo JSON das credenciais baixado
5. Planilha criada e compartilhada com a conta de serviço

### 3. Configurar Email
Copie `.env.example` para `.env` e configure:

```env
# Email
MAIL_SERVER=smtp.gmail.com
MAIL_USERNAME=seu-email@gmail.com
MAIL_PASSWORD=sua-senha-de-app

# Para Gmail: use senha de app
# Para Zimbra: configure servidor da empresa
```

### 4. Testar Localmente
```bash
# Instalar dependências
pip install -r requirements.txt

# Gerar hash de senha para usuários
python utils/hash_password.py
# Copie o hash gerado e cole na coluna 'senha' da sua planilha

# Executar aplicação
python app.py
```

### 5. Deploy no Railway

1. **Criar projeto no Railway:**
```bash
railway login
railway init
```

2. **Configurar variáveis de ambiente:**
```bash
railway variables set SECRET_KEY="sua-chave-secreta"
# ... (demais variáveis)
```

3. **Fazer deploy:**
```bash
railway up
```

## Estrutura do Google Sheets

### Aba "Usuários"
| nome | email | departamento | nivel | ativo | senha | empresa |
|---|---|---|---|---|---|---|
| Admin Sistema | admin@empresa.com | TI | admin | sim | hash_da_senha_aqui | Matriz |
| João Silva | joao@empresa.com | Vendas | usuario | sim | hash_da_senha_aqui | Filial A |

**Configurações:**
- `senha`: Hash da senha gerado por `utils/hash_password.py`. **Nunca salve a senha real aqui!**
- `empresa`: Nome da empresa do usuário (deve existir na aba `Empresas`).
- `nivel`: Nível/Cargo do usuário (deve existir na aba `Permissoes`).

### Aba "Empresas" (NOVA)
Define quais módulos estão disponíveis para cada empresa.

| empresa | modulos_permitidos |
|---|---|
| Matriz | helpdesk,wms |
| Filial A | helpdesk |

### Aba "Permissoes" (NOVA)
Controla o acesso a telas específicas para cada nível de usuário.

| nivel | modulo | tela | permitido |
|---|---|---|---|
| admin | helpdesk | * | sim |
| gestor | helpdesk | dashboard | sim |
| gestor | helpdesk | list_tickets_all | sim |
| usuario | helpdesk | list_tickets_own | sim |
| usuario | wms | avarias_dashboard | sim |

- `tela`: O `*` funciona como um curinga para dar acesso a todas as telas do módulo.

### Aba "Categorias"
| categoria | descricao | email_terceiro | requer_gestor | sla_horas | ativo | cor |
|-----------|-----------|----------------|---------------|-----------|-------|-----|
| Servidor | Infraestrutura | servidor@terceiro.com | sim | 4 | sim | #e74c3c |
| Suporte Geral | Questões gerais | | nao | 48 | sim | #95a5a6 |

## Resolução de Problemas

### Usuário não consegue logar
1. Verifique se o email está correto na aba "Usuários".
2. Verifique se o campo "ativo" está como "sim".
3. Gere um novo hash de senha com `python utils/hash_password.py`, copie e cole na planilha para resetar a senha do usuário.

---

**Sistema pronto para uso em produção!**
