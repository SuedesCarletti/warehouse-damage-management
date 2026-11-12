"""
etiqueta_service.py - VERSÃO COMPLETA E CORRIGIDA
Service para gestão de etiquetas de produtos
Usa CacheSankhyaService para obter prefixos do TGFLOC
"""

import logging
import random
import re as _re
import string
import time
from datetime import datetime
from typing import Tuple, Optional, Dict, List

logger = logging.getLogger(__name__)


def _col_letra(idx: int) -> str:
    """Índice 0-based de coluna → letra A1 (A..Z, AA..)."""
    n = idx + 1
    out = ''
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _letra_col(letra: str) -> int:
    """Letra A1 → índice 0-based de coluna."""
    n = 0
    for ch in letra:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


class EtiquetaService:
    """Serviço para gerenciamento de etiquetas de produtos"""

    def __init__(self, sheets_service, cache_sankhya_service,
                 automacao_service=None, validacao_service=None,
                 config_service=None):
        """
        Inicializa serviço de etiquetas

        Args:
            sheets_service: Instância do GoogleSheetsService
            cache_sankhya_service: Instância do CacheSankhyaService
            automacao_service: Instância do AutomacaoStatusService (opcional)
            validacao_service: Instância do ValidacaoEstoqueService (opcional)
            config_service: Instância do WmsConfigService (opcional)
        """
        self.sheets = sheets_service
        self.cache_sankhya = cache_sankhya_service
        self.automacao = automacao_service
        self.validacao_service = validacao_service
        self.config_service = config_service
        self.sequencial_sheet = 'Sequencial_Global'
        self.itens_sheet = 'Itens_Avarias'
        self.movimentacoes_sheet = 'Movimentacoes'

        logger.info("EtiquetaService inicializado")

    # HELPER INTERNO — INVALIDAR CACHE

    def _invalidar_cache(self):
        """
        Invalida o cache WMS após qualquer escrita em Itens_Avarias.
        Seguro chamar em qualquer contexto — erros são silenciosos.
        """
        try:
            if hasattr(self.sheets, 'invalidate_wms_cache'):
                self.sheets.invalidate_wms_cache()
                logger.debug("Cache WMS invalidado pelo EtiquetaService")
        except Exception as e:
            logger.warning(f"Falha ao invalidar cache WMS (não-fatal): {e}")

    def _criar_pendencia_validacao(
        self,
        id_etiqueta_origem: str,
        id_etiqueta_destino: str,
        dados_produto: list,
        headers: list,
        codlocal_destino: int,
        operador: str,
        empresa: str = '',               # recebe user_empresa da sessão
        codlocal_origem_override: str = '',  # usa este local como origem (ex: 696 em transferências)
        criar_lote_auto: bool = False,   # True em transferências para criar lote automático
    ):
        """
        Registra pendência de validação de estoque de forma não-bloqueante.
        Chamado após triagem ou transferência bem-sucedida.
        Falha silenciosamente — nunca interrompe o fluxo principal.
        """
        if not self.validacao_service:
            logger.warning('_criar_pendencia_validacao: validacao_service não injetado — snapshot não capturado')
            return

        try:
            def _col(nome):
                try:
                    return dados_produto[headers.index(nome)]
                except (ValueError, IndexError):
                    return ''

            codprod      = _col('codprod')
            descrprod    = _col('descrprod') or _col('descricao_produto')
            avaria_id    = _col('avaria_id')
            quantidade_s = _col('quantidade') or _col('qtd_avariada') or _col('qtdneg') or '1'

            # BUG FIX #1 — codemp:
            # Prioridade para o código numérico da coluna ('6', '1', etc.).
            # "empresa" recebido da sessão é o nome do tenant ('Teste', 'Empresax'),
            # que o Sankhya não reconhece e resulta em snapshot vazio.
            # O nome do tenant entra apenas como último recurso.
            codemp_val = (
                _col('codemp')
                or _col('cod_empresa')
                or _col('empresa')
                or empresa
                or ''
            )

            # codlocal_origem_override: usado em transferências para passar o local
            # atual do produto (ex: 696) em vez do codlocalorig original (ex: 401).
            codlocal_origem = codlocal_origem_override or _col('codlocalorig') or _col('codlocal') or ''

            logger.debug(
                f'🔍 _criar_pendencia_validacao: codlocal_origem={codlocal_origem!r} '
                f'(override={codlocal_origem_override!r}) '
                f'codemp_val={codemp_val!r} (empresa sessão={empresa!r})'
            )

            try:
                quantidade = float(str(quantidade_s).replace(',', '.'))
            except (ValueError, TypeError):
                quantidade = 1.0

            numnota_venda     = _col('nota')  or _col('numnota_venda')  or ''
            numnota_devolucao = _col('nfd')   or _col('numnota_devolucao') or _col('numnota') or ''
            # Ponto 7: codparc vem da avaria (aba Avarias), não do item.
            # Tentativa de leitura do item como fallback — valor real vem do cabeçalho
            # quando _criar_pendencia_validacao_inicial o passa via parâmetro.
            codparc_val = _col('codparc') or ''

            id_val = self.validacao_service.registrar_pendencia_triagem(
                id_etiqueta_origem=id_etiqueta_origem,
                id_etiqueta_destino=id_etiqueta_destino,
                avaria_id=avaria_id,
                codprod=codprod,
                descrprod=descrprod,
                quantidade=quantidade,
                codlocal_origem=codlocal_origem,
                codlocal_destino=codlocal_destino,
                operador=operador,
                codemp=codemp_val,
                numnota_venda=numnota_venda,
                numnota_devolucao=numnota_devolucao,
                codparc=codparc_val,
                empresa=empresa,
            )

            # Criar auto-lote para transferências (ex: 696 → local final)
            if criar_lote_auto and id_val and codlocal_destino:
                try:
                    mapa_l = self.cache_sankhya.obter_locais_financeiros() \
                             if self.cache_sankhya else {}
                    if codlocal_origem:
                        self.validacao_service.criar_lote_auto_etiqueta_inicial(
                            id_validacao=id_val,
                            codprod=codprod,
                            descrprod=descrprod,
                            codlocal_origem=str(codlocal_origem),
                            descr_local_origem=mapa_l.get(str(codlocal_origem), {}).get('descrlocal', ''),
                            codlocal_destino=str(codlocal_destino),
                            descr_local_destino=mapa_l.get(str(codlocal_destino), {}).get('descrlocal', ''),
                            numnota_venda=numnota_venda,
                            numnota_devolucao=numnota_devolucao,
                            codemp=codemp_val,
                            criado_por=operador,
                            avaria_id=avaria_id,
                            empresa=empresa,
                        )
                    else:
                        logger.warning(
                            f'⚠️ Lote auto NÃO criado para transferência {id_etiqueta_origem}: '
                            f'codlocal_origem vazio'
                        )
                except Exception as e_lote:
                    logger.warning(f'criar_lote_auto para transferência falhou (não-fatal): {e_lote}')

        except Exception as e:
            logger.warning(f'_criar_pendencia_validacao falhou (não-fatal): {e}')

    def _resolver_codlocal_destino_inicial(
        self,
        row: list,
        headers: list,
        empresa: str,
        avaria_id: str = '',
        avaria_data: list = None,       # já lido fora do loop — evita leitura extra por item
        avaria_headers: list = None,
    ) -> str:
        """
        Resolve o codlocal_destino para etiqueta inicial via WmsConfigService.

        Lógica:
          1. Lê config pelo NOME do tenant (empresa='Empresax'), não pelo codemp ERP.
          2. Verifica exceções por status_indenizacao / status_estoque da avaria.
             Usa avaria_data/avaria_headers se já passados (evita leitura extra da aba).
          3. Se nenhuma exceção bater → retorna codlocal padrão.
          4. Se nada configurado → retorna ''.

        Returns: codlocal como string (ex: '696') ou '' se não resolvido.
        """
        if not self.config_service or not empresa:
            return ''
        try:
            def _col(nome):
                try:
                    return row[headers.index(nome)]
                except (ValueError, IndexError):
                    return ''

            cfg              = self.config_service.get_etiqueta_inicial_config(empresa)
            excecoes         = cfg.get('excecoes', {})
            codlocal_destino = cfg.get('codlocal_padrao', '')

            av_id = avaria_id or _col('avaria_id')

            # Verificar exceções por status — usa dados já lidos para não
            # fazer chamada extra à API por item dentro do loop
            if excecoes and av_id:
                try:
                    # Usa avaria_data passado externamente (lido UMA vez antes do loop)
                    if avaria_data is None or avaria_headers is None:
                        ws_av        = self.sheets.sheet.worksheet('Avarias')
                        av_all       = ws_av.get_all_values()
                        avaria_headers = av_all[0] if av_all else []
                        avaria_data    = av_all[1:] if len(av_all) > 1 else []

                    for av_row in avaria_data:
                        def _avcol(n):
                            try:
                                return av_row[avaria_headers.index(n)]
                            except (ValueError, IndexError):
                                return ''
                        if _avcol('id') == av_id:
                            for chave_exc, codlocal_exc in excecoes.items():
                                if ':' not in chave_exc:
                                    continue
                                campo_exc, valor_exc = chave_exc.split(':', 1)
                                if _avcol(campo_exc.strip()) == valor_exc.strip():
                                    codlocal_destino = str(codlocal_exc)
                                    logger.debug(
                                        f'🔀 Exceção etiqueta inicial: {chave_exc} → {codlocal_exc}'
                                    )
                                    break
                            break
                except Exception as e_av:
                    logger.debug(f'ℹLeitura exceção etiqueta inicial (não-fatal): {e_av}')

            return str(codlocal_destino).strip() if codlocal_destino else ''

        except Exception as e:
            logger.warning(f'_resolver_codlocal_destino_inicial falhou: {e}')
            return ''

    def _criar_pendencia_validacao_inicial(
        self,
        id_etiqueta: str,
        row: list,
        headers: list,
        operador: str,
        empresa: str,               # nome do tenant — para lookup de config e lote
        codlocal_destino: str,      # já resolvido por _resolver_codlocal_destino_inicial
        numnota_venda: str = '',    # do cabeçalho da aba Avarias (lido fora do loop)
        numnota_devolucao: str = '', # do cabeçalho da aba Avarias (lido fora do loop)
        avaria_id_ref: str = '',    # repassado para agrupamento de lote por avaria
        codparc: str = '',          # Ponto 7: CODPARC do parceiro (do cabeçalho da avaria)
    ):
        """
        Registra pendência de validação de estoque para etiqueta inicial (prefixo 00).

        Idêntico ao fluxo de triagem — cria registro Pendente em WMS_ValidacoesEstoque
        com snapshot TGFEST capturado no momento da criação da etiqueta.
        O codlocal_destino é recebido já resolvido (não resolve aqui).

        Falha silenciosamente — nunca interrompe o fluxo principal.
        """
        if not self.validacao_service:
            logger.warning('_criar_pendencia_validacao_inicial: validacao_service não injetado')
            return

        try:
            def _col(nome):
                try:
                    return row[headers.index(nome)]
                except (ValueError, IndexError):
                    return ''

            codprod      = _col('codprod')
            descrprod    = _col('descrprod')
            avaria_id    = _col('avaria_id')
            codemp_val   = _col('codemp') or _col('cod_empresa') or ''
            quantidade_s = _col('qtd_avariada') or _col('qtdneg') or '1'

            # codlocal_origem: onde o produto está no ERP no momento da geração da etiqueta.
            # ATENÇÃO: codlocaldest é o local para onde será transferido (destino) — NÃO usar aqui.
            # codlocalorig é o local de origem correto (ex: 401 DISPONÍVEL, 200 J.C.CURITIBA).
            codlocal_origem = _col('codlocalorig') or ''

            try:
                quantidade = float(str(quantidade_s).replace(',', '.'))
            except (ValueError, TypeError):
                quantidade = 1.0

            logger.debug(
                f'🔍 _criar_pendencia_validacao_inicial: id_etiqueta={id_etiqueta} '
                f'codprod={codprod} codlocal_origem={codlocal_origem!r} '
                f'codlocal_destino={codlocal_destino!r} empresa={empresa!r} codemp={codemp_val!r}'
            )

            # NFs vêm do cabeçalho da avaria (parâmetros passados por criar_etiquetas_iniciais).
            # _col('nota') e _col('nfd') NUNCA funcionam aqui — essas colunas estão em
            # Avarias, não em Itens_Avarias.  Os fallbacks abaixo existem só por segurança.
            numnota_venda     = numnota_venda     or _col('numnota_venda')     or ''
            numnota_devolucao = numnota_devolucao or _col('numnota_devolucao') or ''

            # avaria_id para agrupamento do lote automático
            avaria_id_lote = avaria_id_ref or avaria_id or ''

            id_val = self.validacao_service.registrar_pendencia_triagem(
                id_etiqueta_origem=id_etiqueta,  # parâmetro correto desta função (prefixo 00)
                id_etiqueta_destino='',           # etiqueta inicial ainda não tem destino
                avaria_id=avaria_id,
                codprod=codprod,
                descrprod=descrprod,
                quantidade=quantidade,
                codlocal_origem=codlocal_origem,
                codlocal_destino=codlocal_destino,
                operador=operador,
                codemp=codemp_val,
                numnota_venda=numnota_venda,
                numnota_devolucao=numnota_devolucao,
                codparc=codparc,  # Ponto 7
                empresa=empresa,
            )

            if id_val:
                try:
                    mapa_l = self.cache_sankhya.obter_locais_financeiros() \
                             if self.cache_sankhya else {}
                    # FIX: só criar lote se tiver destino resolvido
                    if not codlocal_destino:
                        logger.warning(
                            f'⚠️ Lote auto NÃO criado para {id_etiqueta}: '
                            f'codlocal_destino vazio (config ausente para empresa={empresa!r})'
                        )
                    else:
                        self.validacao_service.criar_lote_auto_etiqueta_inicial(
                            id_validacao=id_val,
                            codprod=codprod,
                            descrprod=descrprod,
                            codlocal_origem=str(codlocal_origem),
                            descr_local_origem=mapa_l.get(str(codlocal_origem), {}).get('descrlocal', ''),
                            codlocal_destino=str(codlocal_destino),
                            descr_local_destino=mapa_l.get(str(codlocal_destino), {}).get('descrlocal', ''),
                            numnota_venda=numnota_venda,
                            numnota_devolucao=numnota_devolucao,
                            codemp=codemp_val,
                            criado_por=operador,
                            avaria_id=avaria_id_lote,
                            empresa=empresa,
                        )
                except Exception as e_lote:
                    logger.warning(f'criar_lote_auto falhou (não-fatal): {e_lote}')

        except Exception as e:
            logger.warning(f'_criar_pendencia_validacao_inicial falhou (não-fatal): {e}')

    @staticmethod
    def _codlocal_centena(codlocalorig: str, sufixo: int = 97) -> str:
        """Computes refused destination: centena(codlocalorig) * 100 + sufixo.
        Ex: 401 → centena 4 → 497; 201 → 297"""
        try:
            primeiro_digito = str(int(str(codlocalorig).strip()))[0]
            return str(int(primeiro_digito) * 100 + sufixo)
        except Exception:
            return ''

    # GERAÇÃO DE ETIQUETAS

    def _gerar_chave_aleatoria(self, tamanho: int = 4) -> str:
        """Gera chave aleatória alfanumérica"""
        caracteres = string.ascii_uppercase + string.digits
        return ''.join(random.choices(caracteres, k=tamanho))

    def _obter_proximo_sequencial(self) -> int:
        """Obtém e incrementa o sequencial global, criando a aba se necessário"""
        try:
            try:
                ws = self.sheets.sheet.worksheet(self.sequencial_sheet)
            except Exception:
                logger.warning(f"Aba '{self.sequencial_sheet}' não encontrada. Criando...")
                ws = self.sheets.sheet.add_worksheet(
                    title=self.sequencial_sheet, rows=10, cols=1
                )
                ws.update('A1', [['ultimo_sequencial']])
                ws.update_cell(2, 1, 0)
                logger.info(f"Aba '{self.sequencial_sheet}' criada com sequencial=0")

            try:
                valor_atual = ws.cell(2, 1).value
                if not valor_atual:
                    ws.update_cell(2, 1, 1)
                    return 1

                ultimo = int(valor_atual)
                proximo = ultimo + 1
                ws.update_cell(2, 1, proximo)
                return proximo

            except Exception as e:
                logger.error(f"Erro ao ler/atualizar sequencial: {e}")
                ws.update_cell(2, 1, 1)
                return 1

        except Exception as e:
            logger.error(f"Erro fatal ao obter sequencial: {e}")
            return 1

    def _ler_sequencial_atual(self) -> Optional[int]:
        """Lê o último sequencial usado (1 chamada). None se a aba não existir/vazia."""
        ws = self.sheets.sheet.worksheet(self.sequencial_sheet)
        valor = ws.cell(2, 1).value
        return int(valor) if valor else 0

    def _reservar_sequenciais(self, quantidade: int, atual_pre_lido: Optional[int] = None) -> List[int]:
        """
        Reserva `quantidade` sequenciais consecutivos com UMA escrita (e no máximo
        uma leitura, dispensada se `atual_pre_lido` vier da pré-leitura paralela).
        Retorna [] em caso de falha.
        """
        if quantidade <= 0:
            return []
        try:
            try:
                ws = self.sheets.sheet.worksheet(self.sequencial_sheet)
            except Exception:
                ws = None
            if ws is None:
                ultimo = self._obter_proximo_sequencial() - 1
                ws = self.sheets.sheet.worksheet(self.sequencial_sheet)
            else:
                ultimo = atual_pre_lido if atual_pre_lido is not None else self._ler_sequencial_atual()
            ws.update_cell(2, 1, ultimo + quantidade)
            return list(range(ultimo + 1, ultimo + quantidade + 1))
        except Exception as e:
            logger.error(f"Erro ao reservar sequenciais: {e}")
            return []

    def gerar_id_etiqueta(self, codlocal: Optional[int] = None, prefixo: Optional[str] = None) -> Tuple[bool, str, str]:
        """
        Gera ID único de etiqueta no formato: PP-XXXX-NNNN

        Args:
            codlocal: Código do local (ex: 0, 7, 15) - busca prefixo no Sankhya
            prefixo: Prefixo direto (ex: '00', '07') - não consulta Sankhya

        Returns:
            (sucesso, id_etiqueta, mensagem)
        """
        try:
            if prefixo:
                prefixo_final = prefixo
            elif codlocal is not None:
                prefixo_obtido = self.cache_sankhya.obter_prefixo_por_codlocal(codlocal)
                if not prefixo_obtido:
                    return False, "", f"Local {codlocal} não encontrado no Sankhya ou inválido"
                prefixo_final = prefixo_obtido
            else:
                return False, "", "Necessário informar codlocal ou prefixo"

            chave = self._gerar_chave_aleatoria(4)
            sequencial = self._obter_proximo_sequencial()
            id_etiqueta = f"{prefixo_final}-{chave}-{sequencial:04d}"

            logger.info(f"Etiqueta gerada: {id_etiqueta}")
            return True, id_etiqueta, "Etiqueta gerada com sucesso"

        except Exception as e:
            logger.error(f"Erro ao gerar etiqueta: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, "", str(e)

    # ETIQUETAS INICIAIS (PREFIXO 00)

    def criar_etiquetas_iniciais(self, avaria_id: str, operador: str,
                                  empresa: str = '',
                                  codprods_recusados: set = None,
                                  todos_recusados: bool = False,
                                  sequencias_estaticas: set = None) -> Tuple[bool, List[str], str]:
        """
        Cria etiquetas iniciais (prefixo 00) para produtos de uma avaria.

        Args:
            avaria_id: ID da avaria
            operador:  Email do operador
            empresa:   Nome do tenant (ex: 'Empresax') — usado na resolução
                       do codlocal_destino via parametrização.

        Returns:
            (sucesso, lista_ids_etiquetas, mensagem, codlocal_destino)
        """
        codlocal_destino_padrao = ''  # preenchido após resolução de config/exceções
        try:
            from concurrent.futures import ThreadPoolExecutor

            ws = self.sheets.sheet.worksheet(self.itens_sheet)

            # ── Leituras independentes em PARALELO (Itens, Avarias, sequencial) ──
            def _ler_itens():
                return ws.get_all_values()

            def _ler_avarias():
                try:
                    return self.sheets.sheet.worksheet('Avarias').get_all_values()
                except Exception as e_av0:
                    logger.warning(f'Leitura da aba Avarias (não-fatal): {e_av0}')
                    return []

            def _ler_seq():
                try:
                    return self._ler_sequencial_atual()
                except Exception as e_seq0:
                    logger.warning(f'Pré-leitura sequencial falhou (não-fatal): {e_seq0}')
                    return None

            with ThreadPoolExecutor(max_workers=3) as pool:
                f_itens = pool.submit(_ler_itens)
                f_av    = pool.submit(_ler_avarias)
                f_seq   = pool.submit(_ler_seq)
                all_data = f_itens.result()
                av_all   = f_av.result()
                seq_atual_pre = f_seq.result()

            if len(all_data) < 2:
                return False, [], "Planilha vazia", ''

            # ── Corrigir cabeçalhos duplicados (só escreve se houver) ──────
            try:
                seen = {}
                has_duplicates = False
                corrected_headers = []
                for col_name in all_data[0]:
                    if col_name in seen:
                        has_duplicates = True
                        counter = seen[col_name] + 1
                        seen[col_name] = counter
                        new_name = f"{col_name}_{counter}"
                        corrected_headers.append(new_name)
                        logger.warning(f"Coluna duplicada '{col_name}' renomeada para '{new_name}'")
                    else:
                        seen[col_name] = 0
                        corrected_headers.append(col_name)
                if has_duplicates:
                    logger.info("Corrigindo cabeçalho com duplicatas...")
                    ws.update('A1', [corrected_headers])
                    all_data[0] = corrected_headers
                    logger.info("Cabeçalho corrigido")
            except Exception as e:
                logger.error(f"Erro ao verificar cabeçalhos: {e}")

            headers = all_data[0]
            rows = all_data[1:]

            # ── Localizar colunas necessárias ─────────────────────────────
            try:
                col_avaria_id    = headers.index('avaria_id')
                col_id_etiqueta  = headers.index('id_etiqueta')
                col_prefixo      = headers.index('prefixo_estoque')
                col_status       = headers.index('status_etiqueta')
                col_data_ident   = headers.index('data_identificacao')
                col_op_ident     = headers.index('operador_identificacao')
            except ValueError as e:
                return False, [], f"Coluna não encontrada: {e}", ''

            def _idx(nome):
                try:
                    return headers.index(nome)
                except ValueError:
                    return None

            col_codlocaldest = _idx('codlocaldest')   # destino intermediário (opcional)
            col_seq          = _idx('sequencia')
            col_codlocalorig = _idx('codlocalorig')
            col_codprod      = _idx('codprod')
            col_qtd_avariada = _idx('qtd_avariada')
            col_qtdneg       = _idx('qtdneg')
            col_etiq_ant     = _idx('etiqueta_anterior')
            col_data_triagem = _idx('data_triagem')
            col_op_triagem   = _idx('operador_triagem')

            # ── Filtrar itens sem etiqueta ────────────────────────────────
            itens_para_etiquetar = []
            for idx, row in enumerate(rows, start=2):
                if len(row) > col_avaria_id and row[col_avaria_id] == avaria_id:
                    tem_etiqueta = len(row) > col_id_etiqueta and row[col_id_etiqueta].strip()
                    if not tem_etiqueta:
                        if sequencias_estaticas and col_seq is not None and len(row) > col_seq:
                            try:
                                seq_val = int(str(row[col_seq]).strip())
                                if seq_val in sequencias_estaticas:
                                    logger.info(f"⏸Ignorando etiqueta inicial para seq={seq_val} (item pendente/estático)")
                                    continue
                            except (ValueError, TypeError):
                                pass
                        itens_para_etiquetar.append((idx, row))

            if not itens_para_etiquetar:
                return False, [], "Todos os produtos já possuem etiqueta", ''

            logger.info(f"Gerando etiquetas para {len(itens_para_etiquetar)} produtos")

            # ── Cabeçalho da avaria (já lido em paralelo): nota/nfd/codparc + exceções ──
            av_hdrs = av_all[0] if av_all else []
            av_data = av_all[1:] if len(av_all) > 1 else []

            numnota_avaria           = ''
            numnota_devolucao_avaria = ''
            codparc_avaria           = ''
            try:
                if 'id' in av_hdrs:
                    i_id = av_hdrs.index('id')
                    for av_row in av_data:
                        if len(av_row) > i_id and av_row[i_id] == avaria_id:
                            def _avcol(n):
                                try:
                                    return av_row[av_hdrs.index(n)]
                                except (ValueError, IndexError):
                                    return ''
                            numnota_avaria           = _avcol('nota') or _avcol('nf_venda') or ''
                            numnota_devolucao_avaria = _avcol('nfd') or ''
                            codparc_avaria           = _avcol('codparc') or ''
                            break
            except Exception as e_av:
                logger.warning(f'Leitura nota/nfd da avaria {avaria_id} (não-fatal): {e_av}')

            # ── Pré-resolver codlocal_destino UMA VEZ ─────────────────────
            codlocal_destino_padrao = self._resolver_codlocal_destino_inicial(
                row=itens_para_etiquetar[0][1],
                headers=headers,
                empresa=empresa,
                avaria_id=avaria_id,
                avaria_data=av_data,
                avaria_headers=av_hdrs,
            )

            # ── Configuração de roteamento por centena para itens recusados ──
            usar_centena = False
            sufixo_centena = 97
            if self.config_service:
                try:
                    cfg_all = self.config_service.get_all_configs(empresa)
                    usar_centena = cfg_all.get('asn_recusado_roteamento_centena', 'nao') == 'sim'
                    try:
                        sufixo_centena = int(cfg_all.get('asn_recusado_sufixo_centena', '97'))
                    except (ValueError, TypeError):
                        sufixo_centena = 97
                except Exception as e_cfg:
                    logger.warning(f'Leitura config centena (não-fatal): {e_cfg}')

            # ── Classificar itens e reservar TODOS os sequenciais de uma vez ──
            def _cel(row, col):
                return row[col] if col is not None and len(row) > col else ''

            plano = []   # dicts por item
            for linha, row in itens_para_etiquetar:
                codprod_item = _cel(row, col_codprod)
                eh_recusado_asn = (_cel(row, col_status) == 'recusado_asn')
                eh_recusado_flag = bool(
                    todos_recusados
                    or (codprods_recusados and codprod_item and codprod_item in codprods_recusados)
                )
                eh_recusado = eh_recusado_asn or eh_recusado_flag
                codlocalorig_item = _cel(row, col_codlocalorig)
                if eh_recusado:
                    if usar_centena:
                        codlocal_item = (
                            self._codlocal_centena(codlocalorig_item, sufixo_centena)
                            or codlocal_destino_padrao
                            or '497'
                        )
                    else:
                        codlocal_item = '497'
                else:
                    codlocal_item = codlocal_destino_padrao
                # Linhas com etiqueta_anterior preenchida mantêm o fluxo legado (recusar_item),
                # que cancela pendências órfãs da linhagem.
                inline_recusa = eh_recusado and not str(_cel(row, col_etiq_ant)).strip() \
                    and col_qtd_avariada is not None
                plano.append({
                    'linha': linha, 'row': row, 'codprod': codprod_item,
                    'recusado': eh_recusado, 'inline': inline_recusa,
                    'codlocalorig': codlocalorig_item, 'codlocal_item': codlocal_item,
                })

            total_seq = sum(2 if p['inline'] else 1 for p in plano)
            seqs = self._reservar_sequenciais(total_seq, atual_pre_lido=seq_atual_pre)
            if not seqs or len(seqs) < total_seq:
                return False, [], "Erro ao reservar sequenciais de etiqueta", ''
            seq_it = iter(seqs)

            def _novo_id(prefixo):
                return f"{prefixo}-{self._gerar_chave_aleatoria(4)}-{next(seq_it):04d}"

            # ── Montar todas as escritas ──────────────────────────────────
            data_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ncols = len(headers)
            cell_updates = []
            linhas_97 = []
            baixar_linhas = []
            etiquetas_geradas = []
            etiquetas_97_geradas = []
            etiquetas_recusadas_legado = []
            pendencias = []
            prefixo_cache = {}

            def _prefixo_recusa(codlocal_recusa):
                if codlocal_recusa not in prefixo_cache:
                    prefixo_cache[codlocal_recusa] = (
                        self.cache_sankhya.obter_prefixo_por_codlocal(codlocal_recusa) or '97'
                    )
                return prefixo_cache[codlocal_recusa]

            mapa_l = {}
            try:
                mapa_l = self.cache_sankhya.obter_locais_financeiros() if self.cache_sankhya else {}
            except Exception:
                mapa_l = {}

            for p in plano:
                linha, row, codlocal_item = p['linha'], p['row'], p['codlocal_item']
                id_etiqueta = _novo_id('00')
                p['id'] = id_etiqueta

                L = _col_letra
                cell_updates += [
                    {'range': f'{L(col_id_etiqueta)}{linha}', 'values': [[id_etiqueta]]},
                    {'range': f'{L(col_prefixo)}{linha}',     'values': [['00']]},
                    {'range': f'{L(col_status)}{linha}',      'values': [['ativa']]},
                    {'range': f'{L(col_data_ident)}{linha}',  'values': [[data_atual]]},
                    {'range': f'{L(col_op_ident)}{linha}',    'values': [[operador]]},
                ]
                if codlocal_item and col_codlocaldest is not None:
                    cell_updates.append({'range': f'{L(col_codlocaldest)}{linha}', 'values': [[codlocal_item]]})
                etiquetas_geradas.append(id_etiqueta)

                if not p['recusado']:
                    quantidade_s = _cel(row, col_qtd_avariada) or _cel(row, col_qtdneg) or '1'
                    try:
                        quantidade = float(str(quantidade_s).replace(',', '.'))
                    except (ValueError, TypeError):
                        quantidade = 1.0
                    codlocalorig_item = p['codlocalorig']
                    pendencias.append({
                        'id_etiqueta':         id_etiqueta,
                        'avaria_id':           _cel(row, col_avaria_id),
                        'codprod':             p['codprod'],
                        'descrprod':           _cel(row, _idx('descrprod')),
                        'quantidade':          quantidade,
                        'codlocal_origem':     codlocalorig_item,
                        'codlocal_destino':    codlocal_item,
                        'codemp':              _cel(row, _idx('codemp')) or _cel(row, _idx('cod_empresa')) or '',
                        'numnota_venda':       numnota_avaria or _cel(row, _idx('numnota_venda')) or '',
                        'numnota_devolucao':   numnota_devolucao_avaria or _cel(row, _idx('numnota_devolucao')) or '',
                        'codparc':             codparc_avaria,
                        'descr_local_origem':  mapa_l.get(str(codlocalorig_item), {}).get('descrlocal', ''),
                        'descr_local_destino': mapa_l.get(str(codlocal_item), {}).get('descrlocal', ''),
                    })
                elif p['inline']:
                    # Recusa total inline (equivale a recusar_item(qtd=0, criar_validacao=False)):
                    # 00 vira 'baixada' e nasce a linha 97/centena com status 'recusada'.
                    try:
                        qtd_total = float(str(_cel(row, col_qtd_avariada)).replace(',', '.'))
                    except (ValueError, TypeError):
                        qtd_total = 1.0
                    codlocal_recusa_str = (
                        self._codlocal_centena(p['codlocalorig'], sufixo_centena)
                        if (usar_centena and p['codlocalorig']) else ''
                    ) or '497'
                    try:
                        codlocal_recusa = int(codlocal_recusa_str)
                    except (ValueError, TypeError):
                        codlocal_recusa = 497
                    prefixo_recusa = _prefixo_recusa(codlocal_recusa)
                    id_97 = _novo_id(prefixo_recusa)

                    if col_qtdneg is not None:
                        cell_updates.append({
                            'range': f'{L(col_qtdneg)}{linha}', 'values': [[str(qtd_total)]],
                        })

                    nova = list(row) + [''] * max(0, ncols - len(row))
                    nova = nova[:ncols]
                    nova[col_id_etiqueta]  = id_97
                    nova[col_prefixo]      = prefixo_recusa
                    nova[col_status]       = 'recusada'
                    nova[col_data_ident]   = data_atual
                    nova[col_op_ident]     = operador
                    if col_codlocaldest is not None:
                        nova[col_codlocaldest] = str(codlocal_recusa)
                    if col_etiq_ant is not None:
                        nova[col_etiq_ant] = id_etiqueta
                    if col_data_triagem is not None:
                        nova[col_data_triagem] = data_atual
                    if col_op_triagem is not None:
                        nova[col_op_triagem] = operador
                    nova[col_qtd_avariada] = str(qtd_total)
                    if col_qtdneg is not None:
                        nova[col_qtdneg] = str(qtd_total)
                    linhas_97.append(nova)
                    baixar_linhas.append(linha)
                    etiquetas_97_geradas.append(id_97)
                else:
                    etiquetas_recusadas_legado.append(id_etiqueta)

            # ── Escrita 1: todas as células das 00 em UM batch_update ──────
            try:
                ws.batch_update(cell_updates)
            except Exception as e_batch:
                logger.warning(f'batch_update em lote falhou, fallback individual: {e_batch}')
                for upd in cell_updates:
                    m = _re.match(r'([A-Z]+)(\d+)$', upd['range'])
                    ws.update_cell(int(m.group(2)), _letra_col(m.group(1)) + 1, upd['values'][0][0])

            # ── Escrita 2: todas as linhas 97 em UM append_rows ────────────
            if linhas_97:
                try:
                    ws.append_rows(linhas_97, value_input_option='USER_ENTERED')
                except Exception as e_app:
                    logger.warning(f'append_rows 97 falhou ({e_app}); tentando novamente')
                    ws.append_rows(linhas_97, value_input_option='USER_ENTERED')
                ws.batch_update([
                    {'range': f'{_col_letra(col_status)}{ln}', 'values': [['baixada']]}
                    for ln in baixar_linhas
                ])
                for p in plano:
                    if p['inline']:
                        logger.info(f"2ª etiqueta recusa gerada: {p['id']} → local {p['codlocal_item']} (inline)")

            logger.info(
                f"✅ {len(etiquetas_geradas)} etiqueta(s) 00 gravadas em lote para avaria {avaria_id} "
                f"({len(linhas_97)} recusa(s) inline)"
            )

            if not etiquetas_geradas:
                return False, [], "Nenhuma etiqueta foi gerada", ''

            # Invalidar cache — Itens_Avarias foi modificado
            self._invalidar_cache()

            # ── Pendências/lotes de validação (apenas itens normais) — EM LOTE ──
            if pendencias:
                if self.validacao_service and hasattr(self.validacao_service, 'registrar_pendencias_e_lotes_iniciais'):
                    self.validacao_service.registrar_pendencias_e_lotes_iniciais(
                        pendencias, operador, empresa
                    )
                elif self.validacao_service:
                    for pd in pendencias:
                        linha_p = next((p for p in plano if p.get('id') == pd['id_etiqueta']), None)
                        if linha_p:
                            self._criar_pendencia_validacao_inicial(
                                id_etiqueta=pd['id_etiqueta'], row=linha_p['row'], headers=headers,
                                operador=operador, empresa=empresa,
                                codlocal_destino=pd['codlocal_destino'],
                                numnota_venda=numnota_avaria, numnota_devolucao=numnota_devolucao_avaria,
                                avaria_id_ref=avaria_id, codparc=codparc_avaria,
                            )

            # ── Recusados em fluxo legado (etiqueta_anterior preenchida) ────
            set_recusados = set(etiquetas_recusadas_legado)
            for p in plano:
                if p['recusado'] and p['inline']:
                    set_recusados.add(p['id'])
            for id_etq_recusado in etiquetas_recusadas_legado:
                try:
                    ok_97, id_etq_97, msg_97 = self.recusar_item(
                        id_etiqueta=id_etq_recusado,
                        quantidade_recusada=0,
                        operador=operador,
                        empresa=empresa,
                        criar_validacao=False,
                    )
                    if ok_97:
                        etiquetas_97_geradas.append(id_etq_97)
                    else:
                        logger.warning(f'Falha ao gerar 2ª etiqueta para {id_etq_recusado}: {msg_97}')
                except Exception as e_97:
                    logger.warning(f'recusar_item falhou para {id_etq_recusado} (não-fatal): {e_97}')

            etiquetas_finais = [e for e in etiquetas_geradas if e not in set_recusados]
            etiquetas_finais.extend(etiquetas_97_geradas)

            total = len(etiquetas_geradas)
            sufixo_97 = f' | {len(etiquetas_97_geradas)} etiqueta(s) 97 gerada(s)' if etiquetas_97_geradas else ''
            logger.info(f"{total} etiquetas geradas para avaria {avaria_id}{sufixo_97}")
            return True, etiquetas_finais, f"{total} etiquetas geradas{sufixo_97}", codlocal_destino_padrao

        except Exception as e:
            logger.error(f"Erro ao criar etiquetas: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, [], str(e), ''

    # TRIAGEM

    def triar_produto(self, id_etiqueta_00: str, codlocal_destino: int,
                      operador: str, observacao: str = "",
                      empresa: str = '') -> Tuple[bool, str, str]:
        """
        Realiza triagem: baixa etiqueta 00 e cria nova com prefixo do local destino.

        Args:
            id_etiqueta_00: Etiqueta atual (ex: '00-A8F3-0001')
            codlocal_destino: Código do local destino (ex: 7, 15, 23)
            operador: Email do operador
            observacao: Observação da triagem

        Returns:
            (sucesso, novo_id_etiqueta, mensagem)
        """
        try:
            # 1. Validar etiqueta atual
            if not id_etiqueta_00.startswith('00-'):
                return False, "", "Apenas etiquetas com prefixo 00 podem ser triadas"

            # 2. Buscar prefixo do local destino
            prefixo_destino = self.cache_sankhya.obter_prefixo_por_codlocal(codlocal_destino)
            if not prefixo_destino:
                return False, "", f"Local {codlocal_destino} não encontrado"

            # 3. Gerar nova etiqueta
            sucesso, novo_id, msg = self.gerar_id_etiqueta(codlocal=codlocal_destino)
            if not sucesso:
                return False, "", f"Erro ao gerar nova etiqueta: {msg}"

            # 4. Buscar produto na planilha
            ws = self.sheets.sheet.worksheet(self.itens_sheet)
            all_data = ws.get_all_values()

            if len(all_data) < 2:
                return False, "", "Planilha vazia"

            headers = all_data[0]
            rows = all_data[1:]

            try:
                col_id_etiqueta = headers.index('id_etiqueta')
                col_prefixo     = headers.index('prefixo_estoque')
                col_status      = headers.index('status_etiqueta')
            except ValueError as e:
                return False, "", f"Coluna não encontrada: {e}"

            # codlocaldest: coluna que rastreia o local atual do produto —
            # atualizada a cada triagem/transferência para permitir
            # determinação correta da origem em movimentações subsequentes.
            try:
                col_codlocaldest = headers.index('codlocaldest')
            except ValueError:
                col_codlocaldest = None

            # Procurar produto
            linha_produto = None
            dados_produto = None

            for idx, row in enumerate(rows, start=2):
                if row[col_id_etiqueta] == id_etiqueta_00:
                    linha_produto = idx
                    dados_produto = row
                    break

            if not linha_produto:
                return False, "", f"Etiqueta {id_etiqueta_00} não encontrada"

            # 5. Baixar etiqueta 00
            data_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ws.update_cell(linha_produto, col_status + 1, 'baixada')

            # 6. Criar novo registro (duplicar linha e atualizar campos)
            nova_linha = list(dados_produto)

            local_info = self.cache_sankhya.obter_local_por_codlocal(codlocal_destino)
            nova_localizacao = str(codlocal_destino)
            if local_info and 'descrlocal' in local_info:
                nova_localizacao = f"{codlocal_destino} - {local_info['descrlocal']}"

            nova_linha[col_id_etiqueta] = novo_id
            nova_linha[col_prefixo] = prefixo_destino
            nova_linha[headers.index('etiqueta_anterior')] = id_etiqueta_00
            nova_linha[col_status] = 'ativa'
            nova_linha[headers.index('data_triagem')] = data_atual
            nova_linha[headers.index('operador_triagem')] = operador

            # Mantém codlocaldest como o local atual do produto após a triagem.
            # Isso permite que transferências futuras identifiquem a origem correta.
            if col_codlocaldest is not None:
                nova_linha[col_codlocaldest] = str(codlocal_destino)

            try:
                col_localizacao = headers.index('localizacao')
                # Atualiza localizacao da nova etiqueta com o local de destino resolvido.
                nova_linha[col_localizacao] = nova_localizacao
            except ValueError:
                logger.warning("Coluna 'localizacao' não encontrada")

            if observacao:
                try:
                    col_obs = headers.index('observacao_produto')
                    nova_linha[col_obs] = observacao
                except ValueError:
                    pass

            ws.append_row(nova_linha)

            # 7. Registrar movimentação
            self._registrar_movimentacao(
                id_etiqueta_00,
                'BAIXA_TRIAGEM',
                operador,
                f"Triado para local {codlocal_destino} ({prefixo_destino}). Nova etiqueta: {novo_id}"
            )

            # 8. Disparar automação de status
            avaria_id = dados_produto[headers.index('avaria_id')] if 'avaria_id' in headers else ''
            self._disparar_automacao_etiqueta(
                trigger='item_localizado',
                avaria_id=avaria_id,
                operador=operador,
                contexto={
                    'prefixo': str(prefixo_destino),
                    'codlocal': str(codlocal_destino),
                }
            )

            # Invalidar cache — Itens_Avarias foi modificado
            self._invalidar_cache()

            # Registrar pendência de validação de estoque (não-bloqueante)
            self._criar_pendencia_validacao(
                id_etiqueta_origem=id_etiqueta_00,
                id_etiqueta_destino=novo_id,
                dados_produto=dados_produto,
                headers=headers,
                codlocal_destino=codlocal_destino,
                operador=operador,
                empresa=empresa,        # ← repassa
                criar_lote_auto=True,
            )

            logger.info(f"Produto triado: {id_etiqueta_00} → {novo_id}")
            return True, novo_id, f"Produto triado com sucesso: {novo_id}"

            logger.info(f"Produto triado: {id_etiqueta_00} → {novo_id}")
            return True, novo_id, f"Produto triado com sucesso: {novo_id}"

        except Exception as e:
            logger.error(f"Erro na triagem: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, "", str(e)

    # TRANSFERÊNCIA

    def transferir_produto(self, id_etiqueta_origem: str, codlocal_destino: int,
                           operador: str, observacao: str = "",
                           empresa: str = '') -> Tuple[bool, str, str]:
        """
        Transfere produto para outro estoque.
        Baixa etiqueta origem e gera nova etiqueta no destino.

        Args:
            id_etiqueta_origem: ID da etiqueta atual
            codlocal_destino: Código do local destino
            operador: Email do operador
            observacao: Observação da transferência
            empresa: Nome do tenant (necessário para auto-lote e isolamento)

        Returns:
            (sucesso, nova_etiqueta_id, mensagem)
        """
        try:
            ws = self.sheets.sheet.worksheet(self.itens_sheet)
            all_data = ws.get_all_values()

            if len(all_data) < 2:
                return False, "", "Planilha vazia"

            headers = all_data[0]
            rows = all_data[1:]

            try:
                col_id_etiqueta = headers.index('id_etiqueta')
                col_status      = headers.index('status_etiqueta')
                col_prefixo     = headers.index('prefixo_estoque')
            except ValueError as e:
                return False, "", f"Coluna não encontrada: {e}"

            # codlocaldest: local atual do produto — atualizado a cada movimentação
            # para que transferências futuras identifiquem a origem correta (ex: 696).
            try:
                col_codlocaldest = headers.index('codlocaldest')
            except ValueError:
                col_codlocaldest = None

            linha_produto = None
            dados_produto = None

            for idx, row in enumerate(rows, start=2):
                if row[col_id_etiqueta] == id_etiqueta_origem:
                    linha_produto = idx
                    dados_produto = row
                    break

            if not linha_produto:
                return False, "", f"Etiqueta {id_etiqueta_origem} não encontrada"

            status_atual = dados_produto[col_status]
            if status_atual != 'ativa':
                return False, "", "Etiqueta já foi baixada"

            # Local de origem real = codlocaldest da etiqueta atual (onde o produto está agora).
            # Fallback para codlocalorig se codlocaldest vazio (etiquetas antigas).
            codlocal_origem_atual = ''
            if col_codlocaldest is not None and len(dados_produto) > col_codlocaldest:
                codlocal_origem_atual = str(dados_produto[col_codlocaldest]).strip()
            if not codlocal_origem_atual:
                try:
                    col_colorig = headers.index('codlocalorig')
                    codlocal_origem_atual = str(dados_produto[col_colorig]).strip() \
                        if len(dados_produto) > col_colorig else ''
                except ValueError:
                    pass

            sucesso, nova_id, msg = self.gerar_id_etiqueta(codlocal_destino)
            if not sucesso:
                return False, "", f"Erro ao gerar nova etiqueta: {msg}"

            prefixo_destino = self.cache_sankhya.obter_prefixo_por_codlocal(codlocal_destino)
            if not prefixo_destino:
                return False, "", f"Prefixo não encontrado para local {codlocal_destino}"

            data_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ws.update_cell(linha_produto, col_status + 1, 'baixada')

            local_info = self.cache_sankhya.obter_local_por_codlocal(codlocal_destino)
            nova_localizacao = str(codlocal_destino)
            if local_info and 'descrlocal' in local_info:
                nova_localizacao = f"{codlocal_destino} - {local_info['descrlocal']}"

            nova_linha = list(dados_produto)
            nova_linha[col_id_etiqueta] = nova_id
            nova_linha[col_prefixo] = prefixo_destino
            nova_linha[headers.index('etiqueta_anterior')] = id_etiqueta_origem
            nova_linha[col_status] = 'ativa'
            nova_linha[headers.index('data_triagem')] = data_atual
            nova_linha[headers.index('operador_triagem')] = operador

            # Atualiza codlocaldest para o novo local do produto.
            if col_codlocaldest is not None:
                nova_linha[col_codlocaldest] = str(codlocal_destino)

            try:
                col_localizacao = headers.index('localizacao')
                # Atualiza localizacao da nova etiqueta com o local de destino resolvido.
                nova_linha[col_localizacao] = nova_localizacao
            except ValueError:
                logger.warning("Coluna 'localizacao' não encontrada")

            if observacao:
                try:
                    col_obs = headers.index('observacao_produto')
                    nova_linha[col_obs] = observacao
                except ValueError:
                    pass

            ws.append_row(nova_linha)

            self._registrar_movimentacao(
                id_etiqueta_origem,
                'TRANSFERENCIA',
                operador,
                f"Transferido para local {codlocal_destino} ({prefixo_destino}). Nova etiqueta: {nova_id}. {observacao}"
            )

            # Invalidar cache — Itens_Avarias foi modificado
            self._invalidar_cache()

            # Pendência de validação (não-bloqueante).
            # Usa codlocalorig do produto (origem original) como origem do registro —
            # NÃO o codlocaldest atual (ex: 696). Isso permite que o segundo lote
            # mostre a origem original enquanto o primeiro lote (696) não for validado.
            # Após validação do primeiro lote, _atualizar_origem_lotes_relacionados()
            # atualiza a origem para 696 automaticamente.
            # Lote NÃO criado aqui — o segundo lote é gerado apenas via botão "Gerar Lote".
            self._criar_pendencia_validacao(
                id_etiqueta_origem=id_etiqueta_origem,
                id_etiqueta_destino=nova_id,
                dados_produto=dados_produto,
                headers=headers,
                codlocal_destino=codlocal_destino,
                operador=operador,
                empresa=empresa,
            )

            logger.info(f"Produto transferido: {id_etiqueta_origem} → {nova_id}")
            return True, nova_id, f"Produto transferido com sucesso para {prefixo_destino}"

        except Exception as e:
            logger.error(f"Erro na transferência: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, "", str(e)

    # BAIXA DE ETIQUETA

    def recusar_item(
        self,
        id_etiqueta: str,
        quantidade_recusada: float,
        operador: str,
        empresa: str = '',
        observacao: str = '',
        criar_validacao: bool = True,
    ) -> Tuple[bool, str, str]:
        """
        Processa recusa de item com fluxo completo de etiquetas:
        1. Se parcial (qtd_recusada < qtd_total): divide o item em dois
           - Original: fica com qtd restante, mantém etiqueta atual
           - Novo item: qtd recusada, entra no fluxo de recusa
        2. Para a parte recusada:
           a. Gera etiqueta 00 (primeira etiqueta)
           b. Tria para local 497 via centena (segunda etiqueta)
           c. Marca segunda etiqueta como 'recusada'
           d. Cria lote para local 497
        3. Preenche qtdneg e qtd_avariada na parte recusada

        Returns:
            (sucesso, id_etiqueta_final, mensagem)
        """
        try:
            ws = self.sheets.sheet.worksheet(self.itens_sheet)
            all_data = ws.get_all_values()

            if len(all_data) < 2:
                return False, '', 'Planilha vazia'

            headers = all_data[0]
            rows = all_data[1:]

            def _hdr(nome):
                try:
                    return headers.index(nome)
                except ValueError:
                    return None

            col_id_etiqueta  = _hdr('id_etiqueta')
            col_status       = _hdr('status_etiqueta')
            col_qtd_avariada = _hdr('qtd_avariada')
            col_qtdneg       = _hdr('qtdneg')
            col_codlocalorig = _hdr('codlocalorig')
            col_prefixo      = _hdr('prefixo_estoque')
            col_localizacao  = _hdr('localizacao')
            col_codlocaldest = _hdr('codlocaldest')
            col_etiq_ant     = _hdr('etiqueta_anterior')
            col_data_triagem = _hdr('data_triagem')
            col_op_triagem   = _hdr('operador_triagem')
            col_avaria_id    = _hdr('avaria_id')

            if col_id_etiqueta is None or col_status is None:
                return False, '', 'Colunas obrigatórias não encontradas'

            # ── Localizar item ──────────────────────────────────────────────────
            linha_item = None
            dados_item = None
            for idx, row in enumerate(rows, start=2):
                if len(row) > col_id_etiqueta and row[col_id_etiqueta] == id_etiqueta:
                    linha_item = idx
                    dados_item = list(row)
                    break

            if not linha_item:
                return False, '', f'Etiqueta {id_etiqueta} não encontrada'

            status_atual = dados_item[col_status] if col_status < len(dados_item) else ''
            if status_atual != 'ativa':
                return False, '', 'Etiqueta já foi baixada ou não está ativa'

            # ── Linhagem de etiquetas deste item (para cancelar pendências órfãs) ──
            # A etiqueta recebida (ex: 00-XXXX) pode já ter gerado uma pendência de
            # validação/lote (ex: para 696) antes de o item ser recusado agora.
            # Como o fluxo abaixo REESCREVE id_etiqueta na mesma linha (gera uma nova
            # etiqueta 00), precisamos capturar a linhagem (etiqueta atual + toda a
            # cadeia etiqueta_anterior) ANTES dessa reescrita, para depois cancelar
            # qualquer pendência 'Pendente' ligada a qualquer etiqueta dessa cadeia.
            linhagem_etiquetas = []
            if col_etiq_ant is not None:
                mapa_ant = {}
                for row_l in rows:
                    row_l = list(row_l) + [''] * max(0, col_etiq_ant + 1 - len(row_l))
                    eid = row_l[col_id_etiqueta] if col_id_etiqueta < len(row_l) else ''
                    if eid:
                        mapa_ant[eid] = row_l[col_etiq_ant]
                atual = id_etiqueta
                visitado = set()
                while atual and atual not in visitado:
                    linhagem_etiquetas.append(atual)
                    visitado.add(atual)
                    atual = mapa_ant.get(atual, '')
            else:
                linhagem_etiquetas = [id_etiqueta]

            # ── Quantidades ─────────────────────────────────────────────────────
            try:
                qtd_total = float(str(dados_item[col_qtd_avariada]).replace(',', '.'))
            except (ValueError, IndexError, TypeError):
                qtd_total = 1.0

            if quantidade_recusada <= 0:
                quantidade_recusada = qtd_total

            quantidade_recusada = min(quantidade_recusada, qtd_total)
            eh_parcial = quantidade_recusada < qtd_total - 0.001

            # ── Determinar local de recusa (fixo 497, independente do local de
            #    origem do item — mesma config de criar_etiquetas_iniciais decide
            #    se a empresa optou por roteamento por centena) ───────────────────
            codlocalorig = ''
            if col_codlocalorig is not None and col_codlocalorig < len(dados_item):
                codlocalorig = dados_item[col_codlocalorig]

            usar_centena_recusa = False
            sufixo_centena_recusa = 97
            if self.config_service:
                try:
                    cfg_all_recusa = self.config_service.get_all_configs(empresa)
                    usar_centena_recusa = cfg_all_recusa.get('asn_recusado_roteamento_centena', 'nao') == 'sim'
                    try:
                        sufixo_centena_recusa = int(cfg_all_recusa.get('asn_recusado_sufixo_centena', '97'))
                    except (ValueError, TypeError):
                        sufixo_centena_recusa = 97
                except Exception as e_cfg_recusa:
                    logger.warning(f'Leitura config centena em recusar_item (não-fatal): {e_cfg_recusa}')

            codlocal_recusa_str = (
                self._codlocal_centena(codlocalorig, sufixo_centena_recusa)
                if (usar_centena_recusa and codlocalorig) else ''
            )
            if not codlocal_recusa_str:
                codlocal_recusa_str = '497'

            try:
                codlocal_recusa = int(codlocal_recusa_str)
            except (ValueError, TypeError):
                codlocal_recusa = 497

            data_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # ── Linha que entrará no fluxo de recusa ────────────────────────────
            # Se parcial: divide o item
            if eh_parcial:
                qtd_restante = qtd_total - quantidade_recusada

                # Atualizar original com quantidade restante
                upd_orig = []
                upd_orig.append({
                    'range':  f'{chr(65 + col_qtd_avariada)}{linha_item}',
                    'values': [[str(qtd_restante)]],
                })
                if col_qtdneg is not None:
                    upd_orig.append({
                        'range':  f'{chr(65 + col_qtdneg)}{linha_item}',
                        'values': [[str(qtd_restante)]],
                    })
                ws.batch_update(upd_orig)

                # Criar nova linha para a parte recusada
                nova_linha = list(dados_item) + [''] * max(0, len(headers) - len(dados_item))
                nova_linha[col_id_etiqueta] = ''          # sem etiqueta ainda
                nova_linha[col_status]      = 'pendente'  # aguardando etiqueta
                nova_linha[col_qtd_avariada] = str(quantidade_recusada)
                if col_qtdneg is not None:
                    nova_linha[col_qtdneg] = str(quantidade_recusada)
                if col_etiq_ant is not None:
                    nova_linha[col_etiq_ant] = id_etiqueta
                if col_data_triagem is not None:
                    nova_linha[col_data_triagem] = ''
                if col_op_triagem is not None:
                    nova_linha[col_op_triagem] = ''

                ws.append_row(nova_linha[:len(headers)], value_input_option='USER_ENTERED')
                logger.info(
                    f'✂️ Split recusa: original={id_etiqueta} qtd_restante={qtd_restante} '
                    f'| nova linha recusada qtd={quantidade_recusada}'
                )

                # Recarregar planilha para obter índice da nova linha
                all_data = ws.get_all_values()
                rows2 = all_data[1:]
                avaria_id_val = dados_item[col_avaria_id] if col_avaria_id is not None and col_avaria_id < len(dados_item) else ''
                linha_recusa = None
                dados_recusa = None
                # Busca do FINAL para o início: a nova linha foi append_row ao final da planilha.
                # Buscar do início poderia encontrar linhas 'pendente' órfãs de tentativas anteriores.
                for idx2, row2 in reversed(list(enumerate(rows2, start=2))):
                    r2 = list(row2) + [''] * max(0, len(headers) - len(row2))
                    if (r2[col_id_etiqueta] == ''
                            and r2[col_status] == 'pendente'
                            and (col_avaria_id is None or r2[col_avaria_id] == avaria_id_val)
                            and abs(float(r2[col_qtd_avariada] or 0) - quantidade_recusada) < 0.01):
                        linha_recusa = idx2
                        dados_recusa = r2
                        break

                if not linha_recusa:
                    return False, '', 'Erro ao localizar linha de split após inserção'

                self._registrar_movimentacao(
                    id_etiqueta, 'SPLIT_RECUSA', operador,
                    f'Quebra parcial: {quantidade_recusada} recusadas, {qtd_restante} restantes. {observacao}'
                )
            else:
                # Recusa total: usa o próprio item original
                linha_recusa = linha_item
                dados_recusa = list(dados_item) + [''] * max(0, len(headers) - len(dados_item))
                # Garantir que qtdneg == qtd_avariada == quantidade_recusada
                upd_full = []
                if col_qtdneg is not None:
                    upd_full.append({
                        'range':  f'{chr(65 + col_qtdneg)}{linha_recusa}',
                        'values': [[str(quantidade_recusada)]],
                    })
                if upd_full:
                    ws.batch_update(upd_full)

            # ── 1ª Etiqueta: prefixo 00 ─────────────────────────────────────────
            sucesso_00, id_etiqueta_00, msg_00 = self.gerar_id_etiqueta(prefixo='00')
            if not sucesso_00:
                return False, '', f'Erro ao gerar etiqueta 00: {msg_00}'

            ws.batch_update([
                {'range': f'{chr(65 + col_id_etiqueta)}{linha_recusa}', 'values': [[id_etiqueta_00]]},
                {'range': f'{chr(65 + col_prefixo)}{linha_recusa}',     'values': [['00']]},
                {'range': f'{chr(65 + col_status)}{linha_recusa}',      'values': [['ativa']]},
            ])
            logger.info(f'1ª etiqueta recusa: {id_etiqueta_00} (local origem={codlocalorig} → recusa={codlocal_recusa})')

            # ── 2ª Etiqueta: prefixo 97/centena → tria para 497 ────────────────
            prefixo_recusa = self.cache_sankhya.obter_prefixo_por_codlocal(codlocal_recusa)
            if not prefixo_recusa:
                prefixo_recusa = '97'

            sucesso_97, id_etiqueta_97, msg_97 = self.gerar_id_etiqueta(codlocal=codlocal_recusa)
            if not sucesso_97:
                # fallback: usar prefixo calculado
                sucesso_97, id_etiqueta_97, msg_97 = self.gerar_id_etiqueta(prefixo=prefixo_recusa)
            if not sucesso_97:
                return False, '', f'Erro ao gerar etiqueta {prefixo_recusa}: {msg_97}'

            # Buscar descrição do local 497
            local_info_97 = self.cache_sankhya.obter_local_por_codlocal(codlocal_recusa)
            nova_localizacao_97 = str(codlocal_recusa)
            if local_info_97 and 'descrlocal' in local_info_97:
                nova_localizacao_97 = f"{codlocal_recusa} - {local_info_97['descrlocal']}"

            # Recarregar linha da recusa para ter dados atualizados
            all_data = ws.get_all_values()
            headers_att = all_data[0]
            rows_att = all_data[1:]
            dados_recusa_att = None
            for row_att in rows_att:
                row_ext = list(row_att) + [''] * max(0, len(headers_att) - len(row_att))
                if row_ext[col_id_etiqueta] == id_etiqueta_00:
                    dados_recusa_att = row_ext
                    break

            if not dados_recusa_att:
                dados_recusa_att = dados_recusa

            # Marcar 1ª etiqueta como baixada
            ws.update_cell(linha_recusa, col_status + 1, 'baixada')

            # Criar 2ª linha (etiqueta do local 497)
            nova_linha_97 = list(dados_recusa_att)
            # Garantir tamanho correto
            while len(nova_linha_97) < len(headers):
                nova_linha_97.append('')

            nova_linha_97[col_id_etiqueta] = id_etiqueta_97
            nova_linha_97[col_prefixo]     = prefixo_recusa
            nova_linha_97[col_status]      = 'recusada'
            if col_etiq_ant is not None:
                nova_linha_97[col_etiq_ant] = id_etiqueta_00
            if col_data_triagem is not None:
                nova_linha_97[col_data_triagem] = data_atual
            if col_op_triagem is not None:
                nova_linha_97[col_op_triagem] = operador
            if col_codlocaldest is not None:
                nova_linha_97[col_codlocaldest] = str(codlocal_recusa)
            # localizacao herdada do item pai — não sobrescrever aqui
            # Garantir qtdneg e qtd_avariada na 2ª etiqueta
            nova_linha_97[col_qtd_avariada] = str(quantidade_recusada)
            if col_qtdneg is not None:
                nova_linha_97[col_qtdneg] = str(quantidade_recusada)

            ws.append_row(nova_linha_97[:len(headers)], value_input_option='USER_ENTERED')

            logger.info(f'2ª etiqueta recusa: {id_etiqueta_97} → local {nova_localizacao_97} | status=recusada')

            # ── Registrar movimentações ─────────────────────────────────────────
            self._registrar_movimentacao(
                id_etiqueta_00, 'BAIXA_TRIAGEM_RECUSA', operador,
                f'Triado para recusa no local {codlocal_recusa}. Nova etiqueta: {id_etiqueta_97}. {observacao}'
            )
            self._registrar_movimentacao(
                id_etiqueta_97, 'RECUSA', operador, observacao
            )

            # ── Criar lote para local 497 (pendência de validação) ──────────────
            try:
                if self.validacao_service and criar_validacao:
                    avaria_id_val2 = dados_recusa_att[col_avaria_id] if col_avaria_id is not None else ''
                    codprod_val    = dados_recusa_att[_hdr('codprod')] if _hdr('codprod') is not None else ''
                    descrprod_val  = dados_recusa_att[_hdr('descrprod')] if _hdr('descrprod') is not None else ''
                    codemp_val     = (dados_recusa_att[_hdr('codemp')] if _hdr('codemp') is not None else '') or ''

                    mapa_l = self.cache_sankhya.obter_locais_financeiros() if self.cache_sankhya else {}

                    # Carregar NF da avaria (aba Avarias)
                    numnota_venda = ''
                    numnota_devolucao = ''
                    codparc = ''
                    try:
                        ws_av = self.sheets.sheet.worksheet('Avarias')
                        av_all = ws_av.get_all_values()
                        av_hdrs = av_all[0] if av_all else []
                        for av_row in (av_all[1:] if len(av_all) > 1 else []):
                            try:
                                if av_row[av_hdrs.index('id')] == avaria_id_val2:
                                    def _avcol(n):
                                        try: return av_row[av_hdrs.index(n)]
                                        except (ValueError, IndexError): return ''
                                    numnota_venda     = _avcol('nota') or _avcol('nf_venda') or ''
                                    numnota_devolucao = _avcol('nfd') or ''
                                    codparc           = _avcol('codparc') or ''
                                    break
                            except Exception:
                                continue
                    except Exception as e_av:
                        logger.warning(f'Leitura NF da avaria (recusar_item): {e_av}')

                    id_val = self.validacao_service.registrar_pendencia_triagem(
                        id_etiqueta_origem=id_etiqueta_00,
                        id_etiqueta_destino=id_etiqueta_97,
                        avaria_id=avaria_id_val2,
                        codprod=codprod_val,
                        descrprod=descrprod_val,
                        quantidade=quantidade_recusada,
                        codlocal_origem=str(codlocalorig),
                        codlocal_destino=str(codlocal_recusa),
                        operador=operador,
                        codemp=codemp_val,
                        numnota_venda=numnota_venda,
                        numnota_devolucao=numnota_devolucao,
                        codparc=codparc,
                        empresa=empresa,
                    )
                    if id_val:
                        self.validacao_service.criar_lote_auto_etiqueta_inicial(
                            id_validacao=id_val,
                            codprod=codprod_val,
                            descrprod=descrprod_val,
                            codlocal_origem=str(codlocalorig),
                            descr_local_origem=mapa_l.get(str(codlocalorig), {}).get('descrlocal', ''),
                            codlocal_destino=str(codlocal_recusa),
                            descr_local_destino=nova_localizacao_97,
                            numnota_venda=numnota_venda,
                            numnota_devolucao=numnota_devolucao,
                            codemp=codemp_val,
                            criado_por=operador,
                            avaria_id=avaria_id_val2,
                            empresa=empresa,
                        )
                        logger.info(f'Lote criado para recusa: local={codlocal_recusa} val={id_val}')
            except Exception as e_lote:
                logger.warning(f'Criação de lote para recusa (não-fatal): {e_lote}')

            # ── Cancelar pendências órfãs de validação/lote (ex: 696) ────────────
            # O item pode já ter gerado pendência para o destino normal antes de
            # ser recusado agora — sem isso, ele fica preso num lote de destino
            # que ele nunca vai efetivamente alcançar.
            if self.validacao_service:
                for tag_lin in linhagem_etiquetas:
                    try:
                        resultado_cancel = self.validacao_service.cancelar_pendencias_de_etiqueta(
                            id_etiqueta_origem=tag_lin,
                            operador=operador,
                            motivo=f'Item recusado ({id_etiqueta_97}) — pendência do destino original tornou-se obsoleta.',
                        )
                        if resultado_cancel.get('canceladas'):
                            logger.info(
                                f'🚫 Pendência(s) órfã(s) cancelada(s) para {tag_lin}: '
                                f'{resultado_cancel}'
                            )
                    except Exception as e_cancel:
                        logger.warning(f'Cancelamento de pendência órfã para {tag_lin} falhou (não-fatal): {e_cancel}')

            # ── Invalidar cache ─────────────────────────────────────────────────
            self._invalidar_cache()

            logger.info(
                f'✅ Recusa concluída: {id_etiqueta} → '
                f'split={eh_parcial} qtd_recusada={quantidade_recusada} '
                f'etiqueta_97={id_etiqueta_97}'
            )
            return True, id_etiqueta_97, (
                f'Item recusado com sucesso. '
                f'{"Parcial: " + str(quantidade_recusada) + " un. " if eh_parcial else ""}'
                f'Etiqueta: {id_etiqueta_97}'
            )

        except Exception as e:
            logger.error(f'Erro em recusar_item: {e}')
            import traceback
            logger.error(traceback.format_exc())
            return False, '', str(e)

    def baixar_etiqueta(self, id_etiqueta: str, tipo_movimentacao: str,
                        operador: str, observacao: str = "") -> Tuple[bool, str]:
        """
        Baixa etiqueta após movimentação.

        Args:
            id_etiqueta: ID da etiqueta
            tipo_movimentacao: Tipo ('SAIDA', 'TRANSFERENCIA', 'DESCARTE', 'RECUSA')
            operador: Email do operador
            observacao: Observação da movimentação

        Returns:
            (sucesso, mensagem)
        """
        try:
            ws = self.sheets.sheet.worksheet(self.itens_sheet)
            all_data = ws.get_all_values()

            if len(all_data) < 2:
                return False, "Planilha vazia"

            headers = all_data[0]
            rows = all_data[1:]

            try:
                col_id_etiqueta = headers.index('id_etiqueta')
                col_status      = headers.index('status_etiqueta')
            except ValueError as e:
                return False, f"Coluna não encontrada: {e}"

            linha_etiqueta = None

            for idx, row in enumerate(rows, start=2):
                if row[col_id_etiqueta] == id_etiqueta:
                    linha_etiqueta = idx
                    break

            if not linha_etiqueta:
                return False, f"Etiqueta {id_etiqueta} não encontrada"

            novo_status = 'recusada' if tipo_movimentacao == 'RECUSA' else 'baixada'
            ws.update_cell(linha_etiqueta, col_status + 1, novo_status)

            self._registrar_movimentacao(
                id_etiqueta,
                tipo_movimentacao,
                operador,
                observacao
            )

            # Invalidar cache — Itens_Avarias foi modificado
            self._invalidar_cache()

            logger.info(f"Etiqueta {id_etiqueta} marcada como '{novo_status}' ({tipo_movimentacao})")
            return True, f"Etiqueta marcada como {novo_status} com sucesso"

        except Exception as e:
            logger.error(f"Erro ao baixar etiqueta: {e}")
            return False, str(e)

    # QUEBRA DE PRODUTO

    def quebrar_produto(self, id_etiqueta_origem: str, quebras: List[Dict],
                        operador: str) -> Tuple[bool, List[str], str]:
        """
        Quebra produto em múltiplos estados/destinos.
        Baixa etiqueta origem e gera múltiplas novas etiquetas.

        Args:
            id_etiqueta_origem: ID da etiqueta original
            quebras: Lista de dicts com codlocal_destino, observacao e quantidade
            operador: Email do operador
        """
        try:
            ws = self.sheets.sheet.worksheet(self.itens_sheet)
            all_data = ws.get_all_values()

            if len(all_data) < 2:
                return False, [], "Planilha vazia"

            headers = all_data[0]
            rows = all_data[1:]

            try:
                col_id_etiqueta  = headers.index('id_etiqueta')
                col_status       = headers.index('status_etiqueta')
                col_prefixo      = headers.index('prefixo_estoque')
                col_qtd_avariada = headers.index('qtd_avariada')
                col_localizacao  = headers.index('localizacao')
            except ValueError as e:
                return False, [], f"Coluna não encontrada: {e}"

            linha_produto = None
            dados_produto = None

            for idx, row in enumerate(rows, start=2):
                if row[col_id_etiqueta] == id_etiqueta_origem:
                    linha_produto = idx
                    dados_produto = row
                    break

            if not linha_produto:
                return False, [], f"Etiqueta {id_etiqueta_origem} não encontrada"

            status_atual = dados_produto[col_status]
            if status_atual != 'ativa':
                return False, [], "Etiqueta já foi baixada"

            try:
                quantidade_original = float(dados_produto[col_qtd_avariada])
            except (ValueError, IndexError):
                quantidade_original = 1.0

            if quantidade_original <= 1:
                return False, [], "Produto com quantidade 1 ou menor não pode ser quebrado"

            quantidades_especificadas = all('quantidade' in q for q in quebras)

            if quantidades_especificadas:
                soma_qtds = sum(float(q.get('quantidade', 0)) for q in quebras)
                if abs(soma_qtds - quantidade_original) > 0.01:
                    return False, [], f"Soma das quantidades ({soma_qtds}) diferente da original ({quantidade_original})"
            else:
                qtd_por_quebra = quantidade_original / len(quebras)
                for quebra in quebras:
                    quebra['quantidade'] = qtd_por_quebra

            # Baixar etiqueta original
            data_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ws.update_cell(linha_produto, col_status + 1, 'baixada')

            novas_etiquetas = []

            for quebra in quebras:
                codlocal   = quebra.get('codlocal_destino')
                observacao = quebra.get('observacao', '')
                quantidade = quebra.get('quantidade', 1.0)

                sucesso, nova_id, msg = self.gerar_id_etiqueta(codlocal)
                if not sucesso:
                    logger.error(f"Erro ao gerar etiqueta para local {codlocal}: {msg}")
                    continue

                prefixo = self.cache_sankhya.obter_prefixo_por_codlocal(codlocal)
                if not prefixo:
                    logger.error(f"Prefixo não encontrado para local {codlocal}")
                    continue

                local_info = self.cache_sankhya.obter_local_por_codlocal(codlocal)
                nova_localizacao = str(codlocal)
                if local_info and 'descrlocal' in local_info:
                    nova_localizacao = f"{codlocal} - {local_info['descrlocal']}"

                nova_linha = list(dados_produto)
                nova_linha[col_id_etiqueta]  = nova_id
                nova_linha[col_prefixo]      = prefixo
                nova_linha[headers.index('etiqueta_anterior')] = id_etiqueta_origem
                nova_linha[col_status]       = 'ativa'
                nova_linha[headers.index('data_triagem')]      = data_atual
                nova_linha[headers.index('operador_triagem')]  = operador
                nova_linha[col_qtd_avariada] = str(quantidade)
                # Atualiza localizacao da nova etiqueta com o local de destino resolvido.
                nova_linha[col_localizacao] = nova_localizacao

                if observacao:
                    try:
                        col_obs = headers.index('observacao_produto')
                        nova_linha[col_obs] = observacao
                    except ValueError:
                        pass

                ws.append_row(nova_linha)
                novas_etiquetas.append(nova_id)

                logger.info(f"Etiqueta gerada: {nova_id} - Qtd: {quantidade} - Local: {nova_localizacao}")

            self._registrar_movimentacao(
                id_etiqueta_origem,
                'QUEBRA',
                operador,
                f"Produto quebrado em {len(novas_etiquetas)} partes. Qtd original: {quantidade_original}. "
                f"Novas etiquetas: {', '.join(novas_etiquetas)}"
            )

            # Invalidar cache — Itens_Avarias foi modificado
            self._invalidar_cache()

            logger.info(f"Quebra concluída: {id_etiqueta_origem} → {novas_etiquetas}")
            return True, novas_etiquetas, f"Produto quebrado em {len(novas_etiquetas)} novas etiquetas"

        except Exception as e:
            logger.error(f"Erro na quebra: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, [], str(e)

    # CONSULTA DE ETIQUETAS

    def validar_etiqueta(self, id_etiqueta: str) -> Tuple[bool, Optional[Dict], str]:
        """
        Valida etiqueta e retorna dados.

        Args:
            id_etiqueta: ID da etiqueta

        Returns:
            (sucesso, dados_etiqueta, mensagem)
        """
        try:
            ws = self.sheets.sheet.worksheet(self.itens_sheet)
            all_data = ws.get_all_values()

            if len(all_data) < 2:
                return False, None, "Planilha vazia"

            headers = all_data[0]
            rows = all_data[1:]

            col_id_etiqueta = headers.index('id_etiqueta')

            for row in rows:
                if row[col_id_etiqueta] == id_etiqueta:
                    dados = {}
                    for idx, header in enumerate(headers):
                        if idx < len(row):
                            dados[header] = row[idx]
                    return True, dados, "Etiqueta encontrada"

            return False, None, "Etiqueta não encontrada"

        except Exception as e:
            logger.error(f"Erro ao validar etiqueta: {e}")
            return False, None, str(e)

    # MOVIMENTAÇÕES

    def _registrar_movimentacao(self, id_etiqueta: str, tipo: str,
                                operador: str, observacao: str = ""):
        """
        Registra movimentação de etiqueta.

        Args:
            id_etiqueta: ID da etiqueta
            tipo: Tipo da movimentação
            operador: Email do operador
            observacao: Observação
        """
        if time.time() < getattr(self, '_mov_indisponivel_ate', 0):
            return
        try:
            try:
                ws_mov = self.sheets.sheet.worksheet(self.movimentacoes_sheet)
            except Exception:
                # Aba inexistente: evita 1+ chamadas desperdiçadas a cada movimentação
                self._mov_indisponivel_ate = time.time() + 600
                logger.warning(f"Aba '{self.movimentacoes_sheet}' indisponível — movimentações não serão gravadas por 10 min")
                return
            data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            nova_movimentacao = [id_etiqueta, tipo, operador, data_hora, observacao]
            ws_mov.append_row(nova_movimentacao)
            logger.info(f"Movimentação registrada: {id_etiqueta} - {tipo}")

        except Exception as e:
            logger.error(f"Erro ao registrar movimentação: {e}")

    # AUTOMAÇÕES

    def _disparar_automacao_etiqueta(
            self,
            trigger: str,
            avaria_id: str,
            operador: str,
            contexto: dict = None
    ) -> None:
        """
        Dispara automação de status para eventos de movimentação de etiqueta.
        Silencioso: erros não bloqueiam o fluxo principal.
        """
        if not self.automacao or not avaria_id:
            return

        try:
            # Determinar empresa da avaria via sheets (sem depender de wms_service)
            empresa = ''
            try:
                ws = self.sheets.sheet.worksheet('Avarias')
                all_data = ws.get_all_values()
                if all_data and len(all_data) > 1:
                    headers = all_data[0]
                    if 'id' in headers and 'empresa' in headers:
                        col_id  = headers.index('id')
                        col_emp = headers.index('empresa')
                        for row in all_data[1:]:
                            if len(row) > col_id and row[col_id] == avaria_id:
                                empresa = row[col_emp] if len(row) > col_emp else ''
                                break
            except Exception:
                pass

            resultados = self.automacao.aplicar(
                trigger=trigger,
                avaria_id=avaria_id,
                empresa=empresa,
                contexto=contexto or {},
                operador_email=operador,
            )

            aplicados  = [r for r in resultados if r.sucesso]
            bloqueados = [r for r in resultados if r.bloqueado]

            if aplicados:
                logger.info(
                    f"🤖 Automação '{trigger}' aplicada em {avaria_id}: "
                    f"{[r.novo_status for r in aplicados]}"
                )
            if bloqueados:
                for r in bloqueados:
                    logger.info(
                        f"ℹ️ Automação '{trigger}' não aplicada em {avaria_id}: {r.msg}"
                    )

        except Exception as e:
            logger.warning(f"Automação '{trigger}' ignorada para {avaria_id}: {e}")
