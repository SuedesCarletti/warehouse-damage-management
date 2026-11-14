import os
import sys
import logging
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, redirect, url_for, session, request
from flask_session import Session
from core.services.webhook_service import WebhookService
from config import Config

# LOGGING CONFIGURATION

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

# APPLICATION FACTORY

from utils.decorators import api_admin_empresa_required, api_module_required
def create_app():
    logger.info("=" * 70)
    logger.info("INICIANDO MULTISOLUCOES APPLICATION")
    logger.info("=" * 70)

    app = Flask(__name__)

    app.config['SECRET_KEY'] = Config.SECRET_KEY
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_PERMANENT'] = True
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
    app.config['DEBUG'] = Config.DEBUG
    # Cookie Secure só funciona sob HTTPS: navegadores descartam cookies Secure em HTTP
    # puro e a sessão nunca persiste. Por isso é opt-in: defina SESSION_COOKIE_SECURE=true
    # quando houver TLS na frente da aplicação.
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'  # defina true quando houver TLS na frente
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['COMPANY_NAME'] = os.environ.get('COMPANY_NAME', 'MultiSoluções')

    config_errors, config_warnings = Config.validate()
    for _err in config_errors:
        logger.error(f"[CONFIG] {_err}")
    for _warn in config_warnings:
        logger.warning(f"[CONFIG] {_warn}")

    # ── CARREGAR CONFIGURAÇÕES DE E-MAIL E URL ──
    app.config['MAIL_SERVER']   = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT']     = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS']  = str(os.environ.get('MAIL_USE_TLS', 'true')).lower() in ('true', '1', 'sim', 'yes')
    app.config['MAIL_USE_SSL']  = str(os.environ.get('MAIL_USE_SSL', 'false')).lower() in ('true', '1', 'sim', 'yes')
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_FROM_NAME']= os.environ.get('MAIL_FROM_NAME', app.config['COMPANY_NAME'])
    app.config['BASE_URL']      = os.environ.get('BASE_URL', 'http://localhost:5000')
    app.config['PASSWORD_RESET_TIMEOUT_SECONDS'] = int(os.environ.get('PASSWORD_RESET_TIMEOUT_SECONDS', 3600))

    # Configuração de upload para Batch Import
    app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', '/tmp/uploads')
    # 50MB max upload size
    app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', 50 * 1024 * 1024))

    Session(app)

    logger.info("Flask App configurado")

    services_status = {
        'sheets': False,
        'email': False,
        'auth': False,
        'tickets': False,
        'asn': False,
        'convites': False,
    }

    # GOOGLE SHEETS SERVICE

    # sheets_service precisa existir antes do webhook_service, que depende dele.
    sheets_service = None

    try:
        from core.services.google_sheets_service import GoogleSheetsService

        credentials_b64 = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
        spreadsheet_id = os.environ.get('SPREADSHEET_ID')

        if credentials_b64 and spreadsheet_id:
            logger.info("Credenciais Google Sheets encontradas nas variáveis de ambiente")
            sheets_service = GoogleSheetsService(
                credentials_b64=credentials_b64,
                spreadsheet_id=spreadsheet_id
            )
            services_status['sheets'] = sheets_service.connected
            logger.info("Google Sheets Service inicializado")
        else:
            logger.warning("Credenciais Google Sheets não encontradas")
            sheets_service = GoogleSheetsService()

    except Exception as e:
        logger.error(f"CRÍTICO - Google Sheets: {e}")
        try:
            sheets_service = GoogleSheetsService()
            services_status['sheets'] = sheets_service.connected
            logger.warning("Inicialização do Sheets falhou. Forçando modo fallback para continuar.")
        except Exception as fallback_e:
            logger.error(f"FALHA ESTRUTURAL: Não foi possível inicializar Sheets nem em modo fallback: {fallback_e}")
            raise Exception(f"Falha ao inicializar Google Sheets Service: {e}")

    webhook_service = WebhookService(sheets_service)

    try:
        from core.services.email_service import EmailService
        email_service = EmailService(app.config)
        
        # ── Tentar resgatar as configurações SMTP salvas pela UI na aba WMS_Config ──
        if sheets_service and sheets_service.connected:
            try:
                ws_config = sheets_service.sheet.worksheet("WMS_Config")
                records = ws_config.get_all_records()
                smtp_db = {}
                for row in records:
                    chave = str(row.get('chave', '')).strip()
                    if chave.startswith('email_smtp_') or chave in ('MAIL_FROM_NAME', 'COMPANY_NAME'):
                        smtp_db[chave] = str(row.get('valor', '')).strip()
                
                if smtp_db.get('email_smtp_host') and smtp_db.get('email_smtp_usuario'):
                    email_service.update_smtp_config(smtp_db)
                    logger.info("EmailService: Configurações SMTP recarregadas da aba WMS_Config com sucesso!")
            except Exception as wms_cfg_err:
                logger.info(f"ℹWMS_Config não encontrada ou sem SMTP configurado: {wms_cfg_err}")

        services_status['email'] = email_service.enabled
        if email_service.enabled:
            logger.info("Email Service inicializado")
        else:
            logger.info("ℹEmail Service desabilitado (configure MAIL_* vars)")
    except Exception as e:
        logger.error(f"Email Service: {e}")
        logger.warning("Continuando sem Email Service")
        email_service = None

    try:
        from modules.helpdesk.database.ticket_db_manager import DatabaseManager
        db_path = os.path.join(os.getcwd(), 'helpdesk.db')
        db_manager = DatabaseManager(db_path=db_path)
        logger.info("Database Manager inicializado")
    except Exception as e:
        logger.error(f"CRÍTICO - Database Manager: {e}")
        raise Exception(f"Falha ao inicializar Database Manager: {e}")

    try:
        from core.services.auth_service import AuthService
        auth_service = AuthService(sheets_service, app.config)
        services_status['auth'] = True
        logger.info("Auth Service inicializado")
    except Exception as e:
        logger.error(f"CRÍTICO - Auth Service: {e}")
        raise Exception(f"Falha ao inicializar Auth Service: {e}")

    try:
        from modules.helpdesk.services.ticket_service import TicketService
        ticket_service = TicketService(db_manager, sheets_service, email_service, config=app.config)
        services_status['tickets'] = True
        logger.info("Ticket Service inicializado")
    except Exception as e:
        logger.error(f"CRÍTICO - Ticket Service: {e}")
        raise Exception(f"Falha ao inicializar Ticket Service: {e}")

    # Serviços WMS declarados como None aqui, antes de qualquer bloco que os referencie.
    sankhya_service = None
    avaria_sankhya_service = None
    cache_sankhya_service = None
    parametrizacao_service = None
    automacao_svc = None
    historico_estoque_svc = None

    # SANKHYA/WMS SERVICE INITIALIZATION (Fixed Dependency Order)

    try:
        from core.services.sankhya.service import SankhyaService
        from modules.wms.services.sankhya_avaria_service import AvariaService as SankhyaAvariaService
        from modules.wms.services.cache_sankhya_service import CacheSankhyaService

        # 1. Initialize SankhyaService FIRST
        sankhya_service = SankhyaService()

        if sankhya_service.enabled:
            # v6.0: Warm-up (pré-aquece conexão HTTP + token)
            try:
                warm_start = time.time()
                warm_ok = SankhyaService.warm_up()
                warm_time = time.time() - warm_start
                if warm_ok:
                    logger.info(f"Sankhya warm-up OK em {warm_time:.1f}s — conexão HTTP pré-aquecida")
                else:
                    logger.warning(f"Sankhya warm-up falhou ({warm_time:.1f}s) — 1ª consulta será mais lenta")
            except Exception as e:
                logger.warning(f"Warm-up erro (não-fatal): {e}")

            logger.info("Sankhya Service ativado. Inicializando Cache e Avaria...")

            # 2. Initialize Cache Service (depends on SankhyaService)
            try:
                cache_sankhya_service = CacheSankhyaService(sankhya_service)
                services_status['cache_sankhya'] = True
                logger.info("Cache Sankhya Service inicializado")
            except Exception as e:
                logger.error(f"Cache Sankhya Service: {e}")
                services_status['cache_sankhya'] = False
                cache_sankhya_service = None

            # 3. Initialize Avaria Service (depends on SankhyaService, SheetsService, CacheService)
            avaria_sankhya_service = SankhyaAvariaService(
                sankhya_service,
                sheets_service,
                cache_sankhya_service
            )

            services_status['sankhya'] = True
            services_status['avaria_sankhya'] = True
            logger.info("Sankhya Integration Service inicializado")
            logger.info("Sankhya Avaria Service (Mapeamento) inicializado")

        else:
            services_status['sankhya'] = False
            services_status['avaria_sankhya'] = False
            services_status['cache_sankhya'] = False
            logger.warning("Sankhya Service desabilitado (configurações faltando)")

    except ImportError:
        logger.warning("Sankhya modules not found. Continuing without Sankhya/WMS integration.")
        services_status['sankhya'] = False
        services_status['avaria_sankhya'] = False
        services_status['cache_sankhya'] = False
    except Exception as e:
        logger.error(f"Sankhya Service: {e}")
        logger.warning("Continuing without Sankhya Service")
        sankhya_service = None
        avaria_sankhya_service = None
        cache_sankhya_service = None

    # WMS SERVICES EXPANSION (CACHE FALLBACK)

    # Verificar fallback de cache se Sankhya está ativo mas cache falhou
    if sankhya_service and sankhya_service.enabled:
        if not cache_sankhya_service:
            try:
                from modules.wms.services.cache_sankhya_service import CacheSankhyaService
                cache_sankhya_service = CacheSankhyaService(sankhya_service)
                services_status['cache_sankhya'] = True
                logger.info("Cache Sankhya Service (Fallback) inicializado")
            except Exception as e:
                logger.error(f"Cache Sankhya Service (Fallback): {e}")
                services_status['cache_sankhya'] = False
                cache_sankhya_service = None
    else:
        if not cache_sankhya_service:
            services_status['cache_sankhya'] = False
            logger.info("ℹCache Sankhya Service desabilitado (Sankhya base não está ativo)")

    # ETIQUETA SERVICE

    logger.info("Inicializando Etiqueta Service...")
    logger.info(f"   sheets_service disponível: {bool(sheets_service)}")
    logger.info(f"   cache_sankhya_service disponível: {bool(cache_sankhya_service)}")

    etiqueta_service = None

    if sheets_service and cache_sankhya_service:
        try:
            from modules.wms.services.etiqueta_service import EtiquetaService

            # automacao_svc é injetado via setter após AutomacaoStatusService ser criado no bloco WMS.
            etiqueta_service = EtiquetaService(
                sheets_service,
                cache_sankhya_service,
                automacao_service=automacao_svc
            )
            app.etiqueta_service = etiqueta_service

            services_status['etiqueta'] = True
            logger.info("Etiqueta Service inicializado COM SUCESSO")
            logger.info(f"   Instncia criada: {type(etiqueta_service).__name__}")
            logger.info(f"   app.etiqueta_service definido: {hasattr(app, 'etiqueta_service')}")

        except Exception as e:
            logger.error(f"Erro ao inicializar Etiqueta Service: {e}")
            import traceback
            logger.error(traceback.format_exc())
            services_status['etiqueta'] = False
            etiqueta_service = None
            app.etiqueta_service = None
    else:
        services_status['etiqueta'] = False
        etiqueta_service = None
        app.etiqueta_service = None

        logger.warning("Etiqueta Service NÃO INICIALIZADO!")
        logger.warning(f"   sheets_service: {'OK' if sheets_service else 'FALHOU'}")
        logger.warning(f"   cache_sankhya_service: {'OK' if cache_sankhya_service else 'FALHOU'}")

        if not sheets_service:
            logger.error("   sheets_service está None! Verifique Google Sheets credentials")
        if not cache_sankhya_service:
            logger.error("   cache_sankhya_service está None! Verifique Sankhya Service")

    # PARAMETRIZAÇÃO SERVICE INITIALIZATION
    try:
        from modules.wms.services.parametrizacao_service import ParametrizacaoService
        parametrizacao_service = ParametrizacaoService(sheets_service)
        services_status['parametrizacao'] = True
        logger.info("Parametrizacao Service inicializado")
    except Exception as e:
        logger.error(f"Parametrizacao Service: {e}")
        import traceback
        logger.error(traceback.format_exc())
        services_status['parametrizacao'] = False
        parametrizacao_service = None

    # HISTÓRICO DE ESTOQUE SERVICE (Snapshot Diário)
    try:
        if sheets_service and sankhya_service and sankhya_service.enabled:
            from modules.wms.services.historico_estoque_service import HistoricoEstoqueService
            historico_estoque_svc = HistoricoEstoqueService(sankhya_service, sheets_service)
            services_status['historico_estoque'] = True
            logger.info("Historico Estoque Service inicializado (Job Diário Ativo)")
        else:
            services_status['historico_estoque'] = False
            logger.warning("Historico Estoque Service desabilitado (Sankhya/Sheets inativo)")
    except Exception as e:
        logger.error(f"Erro ao inicializar Historico Estoque Service: {e}")
        import traceback
        logger.error(traceback.format_exc())
        services_status['historico_estoque'] = False
        historico_estoque_svc = None

    # DRIVE UPLOAD SERVICE (OAUTH 2.0)

    if sheets_service:
        try:
            from modules.wms.services.drive_upload_service import DriveUploadService

            drive_root_folder_id = os.environ.get('GOOGLE_DRIVE_ROOT_FOLDER_ID')

            if not drive_root_folder_id:
                logger.warning("GOOGLE_DRIVE_ROOT_FOLDER_ID não configurado")
                logger.warning("   Upload de fotos para Drive não estará disponível")
                app.drive_upload_service = None
            else:
                credentials_file = 'credentials.json'
                token_file = 'token.pickle'

                if not os.path.exists(credentials_file):
                    logger.error(f"Arquivo não encontrado: {credentials_file}")
                    logger.error("Execute localmente: python setup_oauth_drive.py")
                    logger.error("Depois faça commit de credentials.json e token.pickle")
                    app.drive_upload_service = None
                elif not os.path.exists(token_file):
                    logger.error(f"Arquivo não encontrado: {token_file}")
                    logger.error("Execute localmente: python setup_oauth_drive.py")
                    logger.error("Depois faça commit de token.pickle")
                    app.drive_upload_service = None
                else:
                    logger.info(f"Pasta raiz configurada: {drive_root_folder_id}")

                    drive_upload_service = DriveUploadService(
                        credentials_file=credentials_file,
                        token_file=token_file,
                        pasta_raiz_id=drive_root_folder_id
                    )

                    app.drive_upload_service = drive_upload_service
                    logger.info("DriveUploadService (OAuth) inicializado")

        except Exception as e:
            logger.error(f"Erro ao inicializar DriveUploadService: {e}")
            import traceback
            logger.error(traceback.format_exc())
            logger.warning("Upload de fotos para Drive não estará disponível")
            app.drive_upload_service = None
    else:
        logger.warning("GoogleSheetsService não disponível - DriveUploadService não será inicializado")
        app.drive_upload_service = None

    # ADMIN HIERARCHY BLUEPRINTS

    try:
        from core.routes.admin_hierarchy_routes import (
            admin_entry_bp, admin_global_bp, admin_empresa_bp, admin_depto_bp,
            init_admin_hierarchy_routes
        )
        from core.services.admin_hierarchy_service import AdminHierarchyService

        hierarchy_svc = AdminHierarchyService(sheets_service)
        app.admin_hierarchy_service = hierarchy_svc

        init_admin_hierarchy_routes(sheets_service, hierarchy_svc)

        app.register_blueprint(admin_entry_bp)
        app.register_blueprint(admin_global_bp)
        app.register_blueprint(admin_empresa_bp)
        app.register_blueprint(admin_depto_bp)

        logger.info("Admin Hierarchy blueprints registrados")
    except Exception as e:
        import traceback
        logger.error(f"Admin Hierarchy Blueprints: {e}")
        logger.error(traceback.format_exc())

    # ATRIBUIR SERVIÇOS AO OBJETO APP

    app.sheets_service = sheets_service
    app.email_service = email_service
    app.db_manager = db_manager
    app.auth_service = auth_service
    app.ticket_service = ticket_service
    app.sankhya_service = sankhya_service
    app.avaria_sankhya_service = avaria_sankhya_service
    app.cache_sankhya_service = cache_sankhya_service
    app.historico_estoque_service = historico_estoque_svc
    app.parametrizacao_service = parametrizacao_service
    # app.etiqueta_service já definido acima dentro do bloco try

    logger.info("=" * 70)
    logger.info("SERVIÇOS ATRIBUÍDOS AO APP:")
    logger.info(f"   app.sheets_service: {type(app.sheets_service).__name__ if hasattr(app, 'sheets_service') and app.sheets_service else 'None'}")
    logger.info(f"   app.auth_service: {type(app.auth_service).__name__ if hasattr(app, 'auth_service') and app.auth_service else 'None'}")
    logger.info(f"   app.ticket_service: {type(app.ticket_service).__name__ if hasattr(app, 'ticket_service') and app.ticket_service else 'None'}")
    logger.info(f"   app.sankhya_service: {type(app.sankhya_service).__name__ if hasattr(app, 'sankhya_service') and app.sankhya_service else 'None'}")
    logger.info(f"   app.cache_sankhya_service: {type(app.cache_sankhya_service).__name__ if hasattr(app, 'cache_sankhya_service') and app.cache_sankhya_service else 'None'}")
    logger.info(f"   app.historico_estoque_service: {type(app.historico_estoque_service).__name__ if hasattr(app, 'historico_estoque_service') and app.historico_estoque_service else 'None'}")
    logger.info(f"   app.parametrizacao_service: {type(app.parametrizacao_service).__name__ if hasattr(app, 'parametrizacao_service') and app.parametrizacao_service else 'None'}")
    logger.info(f"   app.etiqueta_service: {type(app.etiqueta_service).__name__ if hasattr(app, 'etiqueta_service') and app.etiqueta_service else 'None'}")
    logger.info("=" * 70)

    # AUTH BLUEPRINTS

    try:
        from core.routes.auth_routes import auth_bp, init_auth_routes
        init_auth_routes(auth_service, sheets_service, email_service, app.config)
        app.register_blueprint(auth_bp, url_prefix='/auth')
        logger.info("Auth blueprints registrados")
    except Exception as e:
        logger.error(f"CRÍTICO - Auth Blueprints: {e}")
        raise Exception(f"Falha ao registrar Auth Blueprints: {e}")

    # HOME BLUEPRINTS

    try:
        from core.routes.home_routes import home_bp
        app.register_blueprint(home_bp)
        logger.info("Home blueprints registrados")
    except Exception as e:
        logger.error(f"Home Blueprints: {e}")
        logger.warning("Usando rotas fallback para home")

    # HELPDESK BLUEPRINTS
    #
    # api_bp e init_api_routes importados com alias (helpdesk_api_bp /
    # helpdesk_init_api_routes) para não conflitar com o api_bp de avarias_api.py

    try:
        from modules.helpdesk.routes.ticket_routes import ticket_bp, init_ticket_routes
        from modules.helpdesk.routes.admin_routes import admin_bp, init_admin_routes
        from modules.helpdesk.routes.api_routes import (
            api_bp as helpdesk_api_bp,
            init_api_routes as helpdesk_init_api_routes,
        )
        from modules.helpdesk.routes.public_routes import public_bp, init_public_routes

        init_ticket_routes(ticket_svc=ticket_service, sheets_svc=sheets_service, auth_svc=auth_service)
        init_admin_routes(ticket_service, sheets_service)
        helpdesk_init_api_routes(ticket_service, sheets_service)
        init_public_routes(ticket_service, sheets_service, app.config)

        app.register_blueprint(ticket_bp, url_prefix='/helpdesk')
        app.register_blueprint(admin_bp, url_prefix='/helpdesk/admin')
        app.register_blueprint(helpdesk_api_bp, url_prefix='/helpdesk/api')
        app.register_blueprint(public_bp, url_prefix='/helpdesk')

        logger.info("HelpDesk blueprints registrados")
    except Exception as e:
        logger.error(f"HelpDesk Blueprints: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.warning("HelpDesk não será carregado")

    # WMS BLUEPRINTS

    try:
        from modules.wms.routes.wms_routes import wms_bp, init_wms_routes
        from modules.wms.routes.status_routes import status_bp, init_status_routes
        from modules.wms.routes.itens_routes import itens_bp, init_itens_routes
        from modules.wms.routes.avarias_standalone_routes import avarias_bp, init_avarias_routes
        from modules.wms.routes.batch_import_routes import batch_import_bp
        from modules.wms.routes.parametrizacao_routes import param_bp, init_parametrizacao_routes

        sankhya_svc_local  = getattr(app, 'sankhya_service', None)
        avaria_svc_local   = getattr(app, 'avaria_sankhya_service', None)
        cache_svc_local    = getattr(app, 'cache_sankhya_service', None)
        etiqueta_svc_local = getattr(app, 'etiqueta_service', None)
        wms_svc_local      = getattr(app, 'wms_service', None)

        # Inicializar rotas WMS
        init_wms_routes(
            sheets_service,
            auth_service,
            app.config,
            avaria_svc_local
        )

        # Capturar wms_service criado internamente pelo init_wms_routes
        from modules.wms.routes import wms_routes as _wms_routes_mod
        if hasattr(_wms_routes_mod, 'wms_service') and _wms_routes_mod.wms_service:
            app.wms_service = _wms_routes_mod.wms_service
            wms_svc_local = app.wms_service
            logger.info("wms_service armazenado em app para uso em outros blueprints")
        else:
            logger.warning("wms_service não encontrado no módulo wms_routes")

        # ── AutomacaoStatusService ────────────────────────────────────────
        # Inicializado aqui para ter acesso ao wms_svc_local já disponível
        try:
            from modules.wms.services.automacao_status_service import AutomacaoStatusService
            from modules.wms.services.config_service import WmsConfigService as _WmsConfigServiceForAuto
            import modules.wms.routes.parametrizacao_routes as _param_rt_auto
            _cfg_for_auto = getattr(_param_rt_auto, 'config_service', None) or _WmsConfigServiceForAuto(sheets_service)
            automacao_svc = AutomacaoStatusService(
                sheets_service=sheets_service,
                wms_avaria_service=wms_svc_local,
                config_service=_cfg_for_auto,
            )
            app.automacao_service = automacao_svc
            services_status['automacoes'] = True
            logger.info("AutomacaoStatusService inicializado")

            # Injetar automacao_svc no etiqueta_service agora que está disponível.
            if etiqueta_service is not None and hasattr(etiqueta_service, 'automacao_service'):
                etiqueta_service.automacao_service = automacao_svc
                app.etiqueta_service = etiqueta_service  # atualiza referência no app
                logger.info("automacao_svc injetado retroativamente no EtiquetaService")
            elif etiqueta_service is not None:
                # Fallback: tentar atribuição direta do atributo privado se o nome diferir
                try:
                    etiqueta_service._automacao_service = automacao_svc
                    app.etiqueta_service = etiqueta_service
                    logger.info("automacao_svc injetado em etiqueta_service._automacao_service")
                except Exception as inject_e:
                    logger.warning(f"Injeção automacao em etiqueta_service (não-fatal): {inject_e}")

            # Injetar automacao_service em wms_routes fora do try/except do ASN,
            # para que uma falha no ASN não deixe wms_routes.automacao_service em None
            # (o que impediria baixa_pagamento de disparar automações de status).
            try:
                from modules.wms.routes import wms_routes as _wms_rt
                _wms_rt.automacao_service = automacao_svc
                logger.info("automacao_service injetado em wms_routes (garantido)")
            except Exception as e_fix10:
                logger.warning(f"Injeção automação em wms_routes falhou: {e_fix10}")

        except Exception as e:
            import traceback
            logger.error(f"AutomacaoStatusService: {e}")
            logger.error(traceback.format_exc())
            automacao_svc = None
            app.automacao_service = None
            services_status['automacoes'] = False

        # Inicializar outras rotas
        init_status_routes(sheets_service)
        init_parametrizacao_routes(sheets_service, email_service=email_service)

        # ── Scheduler de alertas (anti-429) ──────────────────────────────────
        # Background thread que pré-calcula o resumo de alertas a cada 4 min.
        # Elimina o stampede de leituras simultneas que causava 429 em cascata.
        try:
            from modules.wms.routes.parametrizacao_routes import init_alertas_scheduler
            init_alertas_scheduler(app)
        except Exception as e_sched:
            logger.warning(f"AlertasScheduler não iniciado (não-fatal): {e_sched}")
        

        # ── Injetar automacao_service nas rotas de parametrizações ──
        if automacao_svc:
            try:
                import modules.wms.routes.parametrizacao_routes as _param_rt
                if hasattr(_param_rt, '_automacao_service') and _param_rt._automacao_service:
                    _param_rt._automacao_service.wms = wms_svc_local
                    logger.info("wms_service injetado no _automacao_service (parametrizacao_routes)")
                else:
                    logger.warning("_automacao_service não encontrado em parametrizacao_routes")
            except Exception as e:
                logger.warning(f"Injeção wms em automação (param): {e}")

        init_itens_routes(sankhya_token=None)

        # Inicializar avarias
        logger.info("Inicializando rotas de avarias...")
        init_avarias_routes(
            sankhya_svc=sankhya_svc_local,
            avaria_svc=avaria_svc_local,
            auth_svc=auth_service,
            etiqueta_svc=etiqueta_svc_local,
            cache_sankhya_svc=cache_svc_local,
            automacao_service=automacao_svc,
            wms_svc=wms_svc_local,
        )
        logger.info("Rotas de avarias inicializadas")

        # Registrar blueprints WMS
        app.register_blueprint(avarias_bp, url_prefix='/wms/sankhya_avarias')
        app.register_blueprint(wms_bp, url_prefix='/wms')
        app.register_blueprint(status_bp, url_prefix='/wms/status')
        app.register_blueprint(itens_bp, url_prefix='/wms')
        app.register_blueprint(batch_import_bp)

        # ── Validação de Estoque ──────────────────────────────────────────
        try:
            from modules.wms.routes.validacao_estoque_routes import (
                validacao_estoque_bp, init_validacao_estoque_routes
            )
            # Reutiliza sankhya_service e cache já instanciados — mesma instncia que avarias usa
            init_validacao_estoque_routes(
                sankhya_service=sankhya_service,
                cache_sankhya_service=cache_sankhya_service,
                sheets_service=sheets_service,
                automacao_service=getattr(app, 'automacao_service', None),
                wms_service=wms_svc_local,
            )
            # Injetar validacao_service no etiqueta_service
            if etiqueta_service is not None:
                from modules.wms.routes.validacao_estoque_routes import _service as _val_svc
                if _val_svc:
                    etiqueta_service.validacao_service = _val_svc
                    app.etiqueta_service = etiqueta_service
                    logger.info('ValidacaoEstoqueService injetado no EtiquetaService')
                else:
                    logger.error('_val_svc é None após init_validacao_estoque_routes — injeção falhou!')
            else:
                logger.warning('etiqueta_service é None — ValidacaoEstoqueService não injetado')

            # Injetar WmsConfigService no EtiquetaService para de/para etiqueta inicial
            try:
                import modules.wms.routes.parametrizacao_routes as _param_rt_cfg
                _etq_cfg_svc = getattr(_param_rt_cfg, 'config_service', None)
                if etiqueta_service is not None and _etq_cfg_svc is not None:
                    etiqueta_service.config_service = _etq_cfg_svc
                    app.etiqueta_service = etiqueta_service
                    logger.info('WmsConfigService injetado no EtiquetaService')
                else:
                    logger.warning('WmsConfigService não disponível para EtiquetaService')
            except Exception as _e_cfg:
                logger.warning(f'Injeção de config_service no EtiquetaService falhou: {_e_cfg}')

            app.register_blueprint(validacao_estoque_bp, url_prefix='/wms/validacao-estoque')
            logger.info('Validação Estoque blueprint registrado em /wms/validacao-estoque')
        except Exception as e:
            logger.error(f'Validação Estoque Blueprint: {e}')
        app.register_blueprint(param_bp, url_prefix='/wms/parametrizacao')

        # API Keys por empresa
        from modules.wms.routes.api_keys_routes import api_keys_bp, init_api_keys_routes
        init_api_keys_routes(sheets_service)
        app.register_blueprint(api_keys_bp)
        logger.info("WMS API Keys blueprint registrado")

        # ── ASN — Portal de Agendamentos ─────────────────────────────────────
        try:
            from modules.wms.routes.asn_routes import asn_bp, init_asn_routes
            base_url = os.environ.get('BASE_URL', '')
            automacao_svc_local = getattr(app, 'automacao_service', None)
            init_asn_routes(
                sheets_service=sheets_service,
                email_service=email_service,
                wms_service=wms_svc_local,
                base_url=base_url,
                automacao_service=automacao_svc_local,
                sankhya_avaria_service=getattr(app, 'avaria_sankhya_service', None),
                etiqueta_service=getattr(app, 'etiqueta_service', None),
            )
            app.register_blueprint(asn_bp, url_prefix='/wms/asn')
            services_status['asn'] = True
            logger.info("ASN blueprint registrado em /wms/asn")

            # Transportadoras (Normalização e Sync Sankhya)
            try:
                from modules.wms.routes.transportadoras_routes import transportadoras_bp, init_transportadoras_routes
                init_transportadoras_routes(
                    sheets_service=sheets_service,
                    wms_service=wms_svc_local,
                    sankhya_service=sankhya_service
                )
                app.register_blueprint(transportadoras_bp, url_prefix='/wms/transportadoras')
                logger.info("Transportadoras blueprint registrado em /wms/transportadoras")
            except Exception as e:
                logger.error(f"Transportadoras Blueprint: {e}")

        except Exception as e:
            import traceback
            logger.error(f"ASN Blueprint: {e}")
            logger.error(traceback.format_exc())
            services_status['asn'] = False

        logger.info("WMS blueprints registrados")

    except Exception as e:
        logger.error(f"WMS Blueprints: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.warning("WMS não será carregado")

    # CONVITES DE TRANSPORTADORA (público: /cadastro/transportadora/<token>)

    try:
        from modules.wms.routes.convite_routes import (
            convite_bp, cadastro_bp, init_convite_routes
        )
        init_convite_routes(sheets_service)

        # /wms/convite/...  → autenticado (operador gera/lista convites)
        app.register_blueprint(convite_bp, url_prefix='/wms/convite')

        # /cadastro/...     → público (transportadora faz auto-cadastro via token)
        app.register_blueprint(cadastro_bp, url_prefix='/cadastro')

        services_status['convites'] = True
        logger.info("Convite blueprints registrados (/wms/convite + /cadastro)")
    except Exception as e:
        import traceback
        logger.error(f"Convite Blueprints: {e}")
        logger.error(traceback.format_exc())
        services_status['convites'] = False

    # API REST AVARIAS (avarias_api.py) — registra com alias
    #
    # Importado com nome diferente para não conflitar com o api_bp do HelpDesk.

    try:
        from api.v1.avarias_api import (
            api_bp as avarias_rest_bp,
            init_api_routes as init_avarias_rest,
        )

        wms_svc_avarias = getattr(app, 'wms_service', None)

        # Inicializa mesmo sem wms_service
        init_avarias_rest(wms_svc_avarias, sheets_service, webhook_service)
        app.register_blueprint(avarias_rest_bp)

        if wms_svc_avarias:
            logger.info("avarias_api.py registrada em /api/v1/ (wms_service disponível)")
        else:
            logger.warning("avarias_api.py registrada em /api/v1/ SEM wms_service")

    except Exception as e:
        import traceback
        logger.error(f"Erro ao registrar avarias_api.py: {e}")
        logger.error(traceback.format_exc())

    # ASN API REST (asn_api.py) — endpoints para integração com TMS de transportadoras

    try:
        from api.v1.asn_api import (
            asn_api_bp,
            init_asn_api_routes,
        )

        asn_svc_for_api = getattr(app, 'asn_service', None)

        # Tenta obter o asn_service a partir do blueprint registrado
        if asn_svc_for_api is None:
            try:
                from modules.wms.routes.asn_routes import asn_service as _asn_svc
                asn_svc_for_api = _asn_svc
            except Exception:
                pass

        init_asn_api_routes(asn_svc_for_api, sheets_service)
        app.register_blueprint(asn_api_bp)

        if asn_svc_for_api:
            logger.info("asn_api.py registrada em /api/v1/asn/ (asn_service disponível)")
        else:
            logger.warning("asn_api.py registrada em /api/v1/asn/ SEM asn_service")

    except Exception as e:
        import traceback
        logger.error(f"Erro ao registrar asn_api.py: {e}")
        logger.error(traceback.format_exc())

    # FALLBACK /api/v1/health — rota direta INCONDICIONAL
    #
    # Garante que /api/v1/health sempre responda mesmo se ambos os blueprints
    # acima falharem por qualquer motivo de importação ou registro.

    _api_health_registered = any(
        str(rule) == '/api/v1/health'
        for rule in app.url_map.iter_rules()
    )

    if not _api_health_registered:
        logger.warning("/api/v1/health não registrado pelos blueprints — criando rota de fallback")

        @app.route('/api/v1/health', methods=['GET'])
        def api_v1_health_fallback():
            return jsonify({
                'status': 'online',
                'timestamp': datetime.now().isoformat(),
                'version': '1.0',
                'note': 'fallback — wms_api_bp não carregado',
                'services': {
                    'wms_api':  getattr(app, 'wms_service', None) is not None,
                    'sheets':   services_status.get('sheets', False),
                    'sankhya':  services_status.get('sankhya', False),
                    'etiqueta': services_status.get('etiqueta', False),
                }
            }), 200
    else:
        logger.info("/api/v1/health já registrado pelos blueprints — rota de fallback não necessária")

    # ROTA DE ADMINISTRAÇÃO: FORÇAR CARGA RETROATIVA DE ESTOQUE
    @app.route('/api/v1/wms/estoque/retroativo', methods=['POST'])
    @api_admin_empresa_required
    def api_rodar_retroativo():
        if not hasattr(app, 'historico_estoque_service') or not app.historico_estoque_service:
            return jsonify({'success': False, 'msg': 'Serviço de Histórico Inativo'}), 503
            
        data_inicio = request.args.get('data_inicio', '01/01/2026')
        empresa     = request.args.get('empresa') or None
        import threading
        threading.Thread(
            target=app.historico_estoque_service.processar_retroativo,
            args=(data_inicio,),
            kwargs={'empresa': empresa},
            daemon=True,
        ).start()
        return jsonify({
            'success': True,
            'estrategia': 'início+fim de cada mês passado, diário no mês atual',
            'msg': f'Carga retroativa disparada a partir de {data_inicio} (empresa={empresa or "todas"}). Acompanhe os logs.',
        })

    # ROTA: DIAGNÓSTICO DAS COLUNAS TGFITE NO SANKHYA
    @app.route('/api/v1/wms/estoque/diagnostico', methods=['GET'])
    @api_admin_empresa_required
    def api_diagnostico_estoque():
        """Valida colunas reais do TGFITE antes de ativar o retroativo."""
        if not hasattr(app, 'historico_estoque_service') or not app.historico_estoque_service:
            return jsonify({'success': False, 'msg': 'Serviço de Histórico Inativo'}), 503
        resultado = app.historico_estoque_service.diagnosticar_colunas_tgfite()
        return jsonify(resultado)

    # ROTA: CONSULTA DE EVOLUÇÃO DE ESTOQUE POR LOCAL
    @app.route('/api/v1/wms/estoque/evolucao', methods=['GET'])
    @api_module_required('wms')
    def api_evolucao_estoque():
        """
        Consulta a evolução histórica de estoque por local.

        Query params:
          data_inicio  DD/MM/YYYY  (opcional)
          data_fim     DD/MM/YYYY  (opcional)
          empresa      CODEMP      (opcional)
          codlocal     CODLOCAL    (opcional)
          modo         'registros' | 'resumo'  (default: 'resumo')
        """
        if not hasattr(app, 'historico_estoque_service') or not app.historico_estoque_service:
            return jsonify({'success': False, 'msg': 'Serviço de Histórico Inativo'}), 503

        svc         = app.historico_estoque_service
        data_inicio = request.args.get('data_inicio')
        data_fim    = request.args.get('data_fim')
        empresa     = request.args.get('empresa')
        codlocal    = request.args.get('codlocal')
        modo        = request.args.get('modo', 'resumo')

        if modo == 'resumo':
            resumo = svc.resumo_por_local(data_inicio, data_fim, empresa)
            return jsonify({'success': True, 'modo': 'resumo', 'total': len(resumo), 'dados': resumo})
        else:
            dados = svc.consultar_evolucao(data_inicio, data_fim, empresa, codlocal)
            return jsonify({'success': True, 'modo': 'registros', **dados})

    # LOG DE ROTAS /api/v1 REGISTRADAS (diagnóstico de startup)

    api_routes = sorted(
        str(rule) for rule in app.url_map.iter_rules()
        if '/api/v1' in str(rule)
    )
    logger.info("=" * 70)
    logger.info(f" ROTAS /api/v1 REGISTRADAS ({len(api_routes)}):")
    for route in api_routes:
        logger.info(f"   {route}")
    logger.info("=" * 70)

    @app.after_request
    def add_no_cache_for_api(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    # ERROR HANDLERS

    @app.errorhandler(404)
    def not_found(error):
        pid = os.getpid()
        api_rules = [str(r) for r in app.url_map.iter_rules() if '/api/v1' in str(r)]
        logger.warning(
            f"404 → {request.method} {request.path} | "
            f"PID: {pid} | "
            f"Rotas /api/v1 neste worker: {api_rules}"
        )
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        """Página 500 personalizada"""
        logger.error(f"500 → {request.method} {request.path} | Erro: {error}")
        return render_template('errors/500.html'), 500

    # CONTEXT PROCESSORS

    @app.context_processor
    def utility_processor():
        """Injeta variáveis e funções utilitárias em todos os templates"""

        def format_date_br(date_str, include_time=True):
            if not date_str or date_str.strip() == '':
                return ''

            date_str = str(date_str).strip()

            try:
                if 'T' in date_str or len(date_str) > 19:
                    dt_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    format_str = '%d/%m/%Y %H:%M:%S'
                elif len(date_str) > 10:
                    try:
                        if '.' in date_str:
                            dt_obj = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S.%f')
                        else:
                            dt_obj = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        dt_obj = datetime.strptime(date_str, '%Y-%m-%d')
                        format_str = '%d/%m/%Y'

                    if 'dt_obj' in locals() and len(date_str) > 10:
                        format_str = '%d/%m/%Y %H:%M:%S'
                    else:
                        format_str = '%d/%m/%Y'
                elif len(date_str) == 10:
                    dt_obj = datetime.strptime(date_str, '%Y-%m-%d')
                    format_str = '%d/%m/%Y'
                else:
                    return date_str

                if include_time and 'H' in format_str:
                    return dt_obj.strftime('%d/%m/%Y %H:%M:%S')
                else:
                    return dt_obj.strftime('%d/%m/%Y')

            except Exception:
                return date_str

        def check_perm(module_name, screen_name):
            if 'user_email' not in session:
                return False
            permissions = session.get('permissions', {})
            allowed_modules = permissions.get('modules', [])
            if module_name not in allowed_modules:
                return False
            screens = permissions.get('screens', {}).get(module_name, {})
            return screens.get('*', False) or screens.get(screen_name, False)

        def safe_url_for(endpoint, **values):
            try:
                return url_for(endpoint, **values)
            except Exception as e:
                logger.debug(f"Endpoint {endpoint} não encontrado: {e}")
                return '#'

        def endpoint_exists(endpoint):
            try:
                url_for(endpoint)
                return True
            except Exception:
                return False

        return {
            'company_name': app.config.get('COMPANY_NAME'),
            'current_year': datetime.now().year,
            'check_perm': check_perm,
            'safe_url_for': safe_url_for,
            'endpoint_exists': endpoint_exists,
            'format_date_br': format_date_br,
        }

    @app.before_request
    def log_global():
        logger.info(
            f"🌐 GLOBAL | PID:{os.getpid()} | "
            f"{request.method} {request.path} | "
            f"endpoint={request.endpoint}"
        )

    # HEALTH CHECK RAILWAY (/health)

    @app.route('/health')
    def health():
        """Endpoint de health check para Railway"""
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'services': services_status
        }), 200

    # KEEP-ALIVE: Evita cold start após inatividade

    try:
        from core.utils.keepalive import start_keepalive
        start_keepalive(app, sheets_service, sankhya_service)
        logger.info("KeepAlive Scheduler iniciado")
    except Exception as e:
        logger.warning(f"KeepAlive não iniciou (não-fatal): {e}")

    logger.info("=" * 70)
    logger.info("APLICAÇÃO INICIADA COM SUCESSO")
    logger.info(f"   Sheets Connected:  {services_status['sheets']}")
    logger.info(f"   Email Enabled:     {services_status['email']}")
    logger.info(f"   Database Ready:    True")
    logger.info(f"   Auth Service:      {services_status['auth']}")
    logger.info(f"   Ticket Service:    {services_status['tickets']}")
    logger.info(f"   Cache Sankhya:     {services_status.get('cache_sankhya', False)}")
    logger.info(f"   Parametrização:    {services_status.get('parametrizacao', False)}")
    logger.info(f"   Etiqueta Service:  {services_status.get('etiqueta', False)}")
    logger.info(f"   Automações:        {services_status.get('automacoes', False)}")
    logger.info(f"   ASN Service:       {services_status.get('asn', False)}")
    logger.info(f"   Convites:          {services_status.get('convites', False)}")
    logger.info("=" * 70)

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=app.config['DEBUG'])
