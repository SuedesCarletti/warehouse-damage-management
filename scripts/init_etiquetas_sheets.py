"""
init_etiquetas_sheets.py
Script para inicializar abas necessárias para sistema de etiquetas
Execute uma única vez para criar/atualizar a estrutura
"""

import logging
from core.services.google_sheets_service import GoogleSheetsService
from config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_sequencial_global(sheets_service):
    """Inicializa aba Sequencial_Global"""
    try:
        logger.info("Inicializando aba Sequencial_Global...")
        
        # Verificar se aba existe
        try:
            ws = sheets_service.sheet.worksheet('Sequencial_Global')
            logger.info("   Aba já existe")
            
            # Verificar se tem dados
            try:
                valor = ws.cell(2, 1).value
                if not valor:
                    ws.update('A1', [['ultimo_sequencial']])
                    ws.update_cell(2, 1, 0)
                    logger.info("   Aba inicializada com valor 0")
                else:
                    logger.info(f"   Sequencial atual: {valor}")
            except:
                ws.update('A1', [['ultimo_sequencial']])
                ws.update_cell(2, 1, 0)
                logger.info("   Aba inicializada com valor 0")
            
        except Exception as e:
            # Criar aba
            logger.info("   Criando aba...")
            ws = sheets_service.sheet.add_worksheet(
                title='Sequencial_Global',
                rows=10,
                cols=1
            )
            ws.update('A1', [['ultimo_sequencial']])
            ws.update_cell(2, 1, 0)
            logger.info("   Aba criada e inicializada")
        
        return True
        
    except Exception as e:
        logger.error(f"   Erro: {e}")
        return False


def init_movimentacoes(sheets_service):
    """Inicializa aba Movimentacoes"""
    try:
        logger.info("Inicializando aba Movimentacoes...")
        
        headers = [
            'id_movimentacao',
            'id_etiqueta',
            'tipo_movimentacao',
            'data_hora',
            'operador',
            'observacao'
        ]
        
        # Verificar se aba existe
        try:
            ws = sheets_service.sheet.worksheet('Movimentacoes')
            logger.info("   Aba já existe")
            
            # Verificar se tem header
            header_atual = ws.row_values(1)
            if not header_atual or header_atual[0] != headers[0]:
                ws.update('A1', [headers])
                logger.info("   Headers atualizados")
            else:
                logger.info("   Headers OK")
            
        except Exception as e:
            # Criar aba
            logger.info("   Criando aba...")
            ws = sheets_service.sheet.add_worksheet(
                title='Movimentacoes',
                rows=1000,
                cols=len(headers)
            )
            ws.update('A1', [headers])
            logger.info("   Aba criada")
        
        return True
        
    except Exception as e:
        logger.error(f"   Erro: {e}")
        return False


def atualizar_itens_avarias(sheets_service):
    """Atualiza aba Itens_Avarias com novos campos"""
    try:
        logger.info("Atualizando aba Itens_Avarias...")
        
        novos_campos = [
            'id_etiqueta',
            'prefixo_estoque',
            'etiqueta_anterior',
            'status_etiqueta',
            'observacao_produto',
            'link_fotos_drive',
            'data_identificacao',
            'data_triagem',
            'operador_identificacao',
            'operador_triagem'
        ]
        
        ws = sheets_service.sheet.worksheet('Itens_Avarias')
        header_atual = ws.row_values(1)
        
        if not header_atual:
            logger.error("   Header vazio")
            return False
        
        # Verificar quais campos faltam
        campos_faltantes = []
        for campo in novos_campos:
            if campo not in header_atual:
                campos_faltantes.append(campo)
        
        if not campos_faltantes:
            logger.info("   Todos os campos já existem")
            return True
        
        # Adicionar campos faltantes
        logger.info(f"   Adicionando {len(campos_faltantes)} campos...")
        
        # Obter última coluna usada
        ultima_col = len(header_atual)
        
        # Adicionar cada campo
        for campo in campos_faltantes:
            ultima_col += 1
            ws.update_cell(1, ultima_col, campo)
            logger.info(f"      • {campo}")
        
        logger.info("   Campos adicionados")
        
        return True
        
    except Exception as e:
        logger.error(f"   Erro: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def main():
    """Função principal"""
    logger.info("="*70)
    logger.info("INICIALIZAÇÃO DE ABAS PARA SISTEMA DE ETIQUETAS")
    logger.info("="*70)
    
    # Carregar configuração
    config = Config()
    
    # Conectar ao Google Sheets
    logger.info("\nConectando ao Google Sheets...")
    sheets_service = GoogleSheetsService(
        credentials_b64=config.GOOGLE_CREDENTIALS_B64,
        spreadsheet_id=config.GOOGLE_SHEET_ID
    )
    
    if not sheets_service.connected:
        logger.error("Falha ao conectar ao Google Sheets")
        return False
    
    logger.info("Conectado com sucesso\n")
    
    # Executar inicializações
    resultados = []
    
    resultados.append(("Sequencial_Global", init_sequencial_global(sheets_service)))
    resultados.append(("Movimentacoes", init_movimentacoes(sheets_service)))
    resultados.append(("Itens_Avarias (atualização)", atualizar_itens_avarias(sheets_service)))
    
    # Resumo
    logger.info("\n" + "="*70)
    logger.info("RESUMO")
    logger.info("="*70)
    
    for nome, sucesso in resultados:
        status = "OK" if sucesso else "❌ ERRO"
        logger.info(f"{status} - {nome}")
    
    sucesso_geral = all(r[1] for r in resultados)
    
    if sucesso_geral:
        logger.info("\nInicialização concluída com sucesso!")
        logger.info("\nPróximos passos:")
        logger.info("   1. Reiniciar a aplicação")
        logger.info("   2. Testar geração de etiquetas")
        logger.info("   3. Testar triagem de produtos")
    else:
        logger.info("\nAlguns erros ocorreram. Verifique os logs acima.")
    
    logger.info("="*70)
    
    return sucesso_geral


if __name__ == '__main__':
    main()
