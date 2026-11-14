import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import traceback

# CORREÇÃO: Importar modelos Avaria e ItemAvaria
from modules.wms.models.avaria import Avaria        # Importação explícita
from modules.wms.models.item_avaria import ItemAvaria  # Importação explícita

# ••• Imports adicionais para filtragem por regras •••
from .parametrizacao_service import ParametrizacaoService
from ..models.regra_exclusao import RegraExclusao

logger = logging.getLogger(__name__)

class AvariaService:
    """
    Service para gestão de avarias
    Mapeia dados do Sankhya para estrutura de Avarias no Google Sheets
    """
    
    def __init__(self, sankhya_service, sheets_service, cache_sankhya_service=None):
        """
        Inicializa serviço de avarias
        
        Args:
            sankhya_service: Instância do SankhyaService
            sheets_service: Instância do GoogleSheetsService
            cache_sankhya_service: Instância do CacheSankhyaService (opcional)
        """
        self.sankhya = sankhya_service
        self.sheets = sheets_service
        self.cache_sankhya = cache_sankhya_service
        
        # ••• Inicializar ParametrizacaoService •••
        self.param_service = ParametrizacaoService(sheets_service)
        
        # CFOPs excluídos do cálculo de VLRSUBST
        self.excluded_cfops = {
            5101, 5102, 6101, 6102, 6108,
            6104, 5104, 6106, 5106, 6117,
            5117, 6119, 5119, 6120, 5120,
            6123, 5123
        }

    # ── valor base por localização dos itens ────────────────────────────
    @staticmethod
    def calcular_valor_base_por_locais(itens: list, locais_indenizaveis: list) -> float:
        """
        Soma valor_item * qtd_avariada apenas para itens cuja `localizacao`
        bater com algum local configurado.
    
        Matching (ordem):
          1. Exato:   "497" == "497"
          2. Prefixo: config="497"  →  item="497 - EM PROCESSO DE INDENIZAÇÃO"  ✓
                      config="497"  →  item="4970 - OUTRO LOCAL"                ✗
    
        Isso permite configurar só o código numérico (ex: "497, 613") sem precisar
        listar o nome completo do local.
    
        Args:
            itens: Lista de dicts com {localizacao, valor_item, qtd_avariada}
            locais_indenizaveis: Lista de strings configuradas na parametrização
    
        Returns:
            float: Soma dos valores dos itens que batem
        """
        if not locais_indenizaveis or not itens:
            return 0.0
    
        locais_norm = [l.strip().lower() for l in locais_indenizaveis if l.strip()]

        def _bate(loc_item: str) -> bool:
            loc = loc_item.strip().lower()
            for cfg in locais_norm:
                if loc == cfg:
                    return True
                # prefixo: cfg seguido de ' ', '-' ou ' -'
                if (loc.startswith(cfg + ' ')
                        or loc.startswith(cfg + '-')
                        or loc.startswith(cfg + ' -')):
                    return True
            return False
    
        total = 0.0
        for item in itens:
            loc          = str(item.get('localizacao',  '') or '').strip()
            codlocaldest = str(item.get('codlocaldest', '') or '').strip()

            # localizacao é o campo principal; codlocaldest é fallback se localizacao vazia
            campo_ativo = loc if loc else codlocaldest
            if not campo_ativo or not _bate(campo_ativo):
                continue

            campo_bateu = campo_ativo
            try:
                vi = float(str(item.get('valor_item', 0) or 0).replace(',', '.'))
                qa = float(str(item.get('qtd_avariada', 1) or 1).replace(',', '.'))
                total += vi * qa
                logger.debug(
                    f"   campo='{campo_bateu}' vi={vi} qa={qa} → +{vi * qa:.2f}"
                )
            except (ValueError, TypeError) as exc:
                logger.warning(f"   Erro ao somar item (campo='{campo_bateu}'): {exc}")
    
        logger.info(
            f"   📍 calcular_valor_base_por_locais: total=R${total:.2f} "
            f"({len(locais_norm)} locais configurados)"
        )
        return round(total, 2)

    @staticmethod
    def _bate_local(loc_item: str, locais_indenizaveis: list) -> bool:
        """Verifica se uma localização bate com algum local configurado (exato ou prefixo)."""
        if not loc_item or not locais_indenizaveis:
            return False
        loc = loc_item.strip().lower()
        for cfg in [l.strip().lower() for l in locais_indenizaveis if l.strip()]:
            if loc == cfg:
                return True
            if loc.startswith(cfg + ' ') or loc.startswith(cfg + '-') or loc.startswith(cfg + ' -'):
                return True
        return False

    def _calculate_indenizacao_value(
        self,
        valor_base: float,
        indenizacao_type: str,
        reembolso_pct: float,
        valor_itens_indenizaveis: float = 0.0,
    ) -> float:
        """
        Calcula o valor final da indenização com base nas regras de negócio.
        Se valor_itens_indenizaveis > 0, substitui valor_base (prioridade de localização).
        Se valor_itens_indenizaveis = 0, mantém valor_base (NFD/NF) como fallback.
        """
        if valor_itens_indenizaveis > 0:
            valor_base = valor_itens_indenizaveis
            logger.info(f"   Usando valor por localização: R$ {valor_base:.2f}")
        # Normalizar tipo para comparação robusta (aceita valores antigos e novos)
        tipo_norm = (indenizacao_type or '').strip().lower()

        if tipo_norm in ('nao_indenizavel', 'não indenizável', 'nao indenizavel', 'não indenizavel'):
            return 0.0

        if valor_base <= 0:
            return 0.0

        indenizacao_valor = 0.0

        if tipo_norm in ('total', 'indenização total', 'indenizacao total'):
            # Total = 100% do valor base
            indenizacao_valor = valor_base
        elif tipo_norm in ('parcial', 'indenização parcial', 'indenizacao parcial'):
            # Parcial = percentual informado aplicado ao valor base
            indenizacao_valor = (valor_base * reembolso_pct) / 100.0
        else:
            # Sem tipo selecionado, usar apenas o percentual (fallback)
            indenizacao_valor = (valor_base * reembolso_pct) / 100.0
            
        return round(indenizacao_valor, 2)

    def calculate_update_value(
        self,
        valor_nf: str,
        valor_nfd: str,
        indenizacao_type: str,
        reembolso_pct: float,
        valor_itens_indenizaveis: float = 0.0,
    ) -> float:
        """
        Calcula o valor de indenização a ser atualizado.
        Se valor_itens_indenizaveis > 0 (itens nos locais configurados), usa como base.
        Caso contrário, usa NFD com fallback para NF.
        """
        try:
            valor_nf_base = float(str(valor_nf or 0).replace(',', '.'))
            valor_nfd_base = float(str(valor_nfd or 0).replace(',', '.'))
        except ValueError:
            logger.warning("Valores de NF/NFD inválidos para cálculo de indenização.")
            return 0.0

        # NFD > NF como base inicial; substituído por locais se valor > 0
        valor_base = valor_nfd_base if valor_nfd_base > 0 else valor_nf_base

        logger.info(f"Calculando indenização (EDIÇÃO):")
        logger.info(f"   - Valor NF: R$ {valor_nf_base}")
        logger.info(f"   - Valor NFD: R$ {valor_nfd_base}")
        logger.info(f"   - Valor itens locais: R$ {valor_itens_indenizaveis:.2f}")
        logger.info(f"   Base inicial: R$ {valor_base} ({'NFD' if valor_nfd_base > 0 else 'NF fallback'})")

        return self._calculate_indenizacao_value(
            valor_base, indenizacao_type, reembolso_pct,
            valor_itens_indenizaveis=valor_itens_indenizaveis,
        )

    def buscar_e_mapear_nota(self, numnota: str, codemp: str = None, incluir_nfd: str = None, nunota: str = None, tipo_nota_hint: str = None) -> Tuple[Optional[Dict], Optional[List[Dict]], Optional[str]]:
            """
            tipo_nota_hint: 'NF' ou 'NFD' quando o chamador já sabe o tipo da nota
            (ex.: vindo do ASN da transportadora). Usado para filtrar por TIPMOV na
            busca do cabeçalho e evitar colisão quando o mesmo NUMNOTA existe tanto
            como NF quanto como NFD no Sankhya.
            """
            try:
                logger.info(f"┌ Buscando nota {numnota} (NUNOTA: {nunota or 'auto'}, tipo: {tipo_nota_hint or 'auto'})...")

                tipmov_hint = None
                if tipo_nota_hint:
                    tipmov_hint = 'D' if str(tipo_nota_hint).upper() == 'NFD' else 'V'

                # 1. Buscar dados completos da nota
                # Se nunota foi fornecido (seleção do modal), ele é priorizado internamente
                cabecalho, itens = self.sankhya.buscar_dados_completos_nota(
                    numnota, codemp, nunota=nunota, tipmov=tipmov_hint
                )
                
                if not cabecalho:
                    return None, None, f"Nota fiscal {numnota} não encontrada no Sankhya"
                
                # --- ADIÇÃO: BUSCA DE TRANSPORTADORA USANDO CACHE (L80) ---
                codparctransp = cabecalho.get('CODPARCTRANSP')
                transportadora_nome = None
                
                if codparctransp:
                    # TENTAR CACHE
                    if self.cache_sankhya:
                        try:
                            transportadoras = self.cache_sankhya.obter_transportadoras()
                            codparc_int = int(codparctransp)
                            if codparc_int in transportadoras:
                                transportadora_nome = transportadoras[codparc_int].get('nomeparc')
                                logger.info(f"Transportadora encontrada no cache: {transportadora_nome}")
                        except Exception as e:
                            logger.warning(f"Erro ao buscar transportadora no cache: {e}")
                            transportadora_nome = None
                    
                    if not transportadora_nome:
                        # FALLBACK PARA SANKHYA DIRETO
                        try:
                            logger.info(f"╏Buscando transportadora {codparctransp} (fallback Sankhya)...")
                            transp_data = self.sankhya.buscar_transportadora(codparctransp)
                            if transp_data:
                                transportadora_nome = transp_data.get('NOMEPARC')
                        except Exception as e:
                            logger.warning(f"Failed to fetch transportadora directly: {e}")
                            
                    cabecalho['NOMEPARC_TRANSP'] = transportadora_nome if transportadora_nome else 'N/A'
                else:
                    cabecalho['NOMEPARC_TRANSP'] = 'N/A'
                
                # --- FIM ADIÇÃO ---
                
                # 2. Determinar se é NF ou NFD
                tipmov = cabecalho.get('TIPMOV')
                
                dados_avaria = {}
                nf_origem = None

                if tipmov == 'D':
                    # É uma NFD - buscar NF origem
                    logger.info(f"└ NFD detectada - buscando NF origem...")
                    
                    # FIX CRÍTICO: quando nunota foi fornecido (seleção do modal),
                    # o cabecalho já é o exato — ler AD_NFORIGEM diretamente.
                    # Não chamar buscar_nota_origem_por_nfd(numnota) que re-busca
                    # pelo NUMNOTA original e retorna sempre a primeira NFD.
                    if nunota and cabecalho.get('AD_NFORIGEM'):
                        numnota_origem = cabecalho.get('AD_NFORIGEM')
                        logger.info(f"└ AD_NFORIGEM do cabeçalho selecionado: {numnota_origem}")
                        nf_origem = self.sankhya.buscar_nota_fiscal(
                            numnota_origem, codemp, tipmov='V'
                        )
                    else:
                        nf_origem = self.sankhya.buscar_nota_origem_por_nfd(numnota, codemp)
                    
                    if nf_origem:
                        dados_avaria = self._mapear_cabecalho_nfd(cabecalho, nf_origem)
                    else:
                        logger.warning("NFD sem AD_NFORIGEM ou NF origem não encontrada")
                        dados_avaria = self._mapear_cabecalho_apenas_nfd(cabecalho)
                
                else:
                    # É uma NF de venda
                    if incluir_nfd:
                        # Usuário preencheu NFD depois - buscar dados da NFD
                        logger.info(f"└ Buscando NFD {incluir_nfd}...")
                        nfd = self.sankhya.buscar_nota_fiscal(incluir_nfd, codemp, tipmov='D')
                        
                        if nfd:
                            dados_avaria = self._mapear_cabecalho_nf_com_nfd(cabecalho, nfd)
                        else:
                            logger.warning(f"NFD {incluir_nfd} não encontrada")
                            dados_avaria = self._mapear_cabecalho_apenas_nf(cabecalho)
                    else:
                        # Apenas NF de venda (sem devolução ainda)
                        dados_avaria = self._mapear_cabecalho_apenas_nf(cabecalho)
                
                # 2.5 Calcula VLRDESTAQUE, VLRFRETE e Total M3 para repassar ao mapeamento de itens
                vlr_destaque = 0.0
                vlr_frete = 0.0
                total_m3 = 0.0

                try:
                    # CORREÇÃO CRÍTICA (Requisito do Usuário):
                    # O valor destaque deve ser buscado NO NFD (se TIPMOV='D') ou NF (se TIPMOV='V'),
                    # ou seja, usar o valor presente no cabeçalho da nota consultada,
                    # ignorando a NF de origem (nf_origem) para este cálculo.
                    vlr_destaque = self._safe_float(cabecalho.get('VLRDESTAQUE', 0))
                    # Frete do cabeçalho — usado como FALLBACK por item (ver _calcular_valor_nf_venda/
                    # _calcular_valor_nf_devolucao): o Sankhya sempre preenche o frete em UM dos dois
                    # campos (AD_VLRFRETEECOMMERCE por item OU VLRFRETE no cabeçalho, nunca os dois com
                    # valores a somar) — então não é soma, é "usa o do item se tiver, senão rateia o do
                    # cabeçalho por M3".
                    vlr_frete = self._safe_float(cabecalho.get('VLRFRETE', 0))

                    # Calcular Total M3 dos itens
                    for item in itens:
                        total_m3 += float(item.get('M3', 0)) # Campo M3 adicionado no SankhyaService
                except ValueError:
                    logger.warning("Erro de conversão ao calcular VLRDESTAQUE, VLRFRETE ou Total M3.")

                logger.info(f"◓ Total M3: {total_m3}, VLRDESTAQUE: {vlr_destaque}, VLRFRETE: {vlr_frete}")

                # 3. Mapear itens
                itens_mapeados = self._mapear_itens(itens, tipmov, vlr_destaque, total_m3, vlr_frete)
                
                logger.info(f"Mapeamento concluído: {len(itens_mapeados)} itens")
                return dados_avaria, itens_mapeados, None
                
            except Exception as e:
                erro_msg = f"Erro ao buscar nota: {str(e)}"
                logger.error(f"{erro_msg}")
                logger.debug(traceback.format_exc())
                return None, None, erro_msg
    
    def _mapear_cabecalho_apenas_nf(self, cabecalho: Dict) -> Dict:
            return {
                'nf_venda': cabecalho.get('NUMNOTA'),
                'nfd': '', 
                'quantidade': cabecalho.get('QTDVOL'),
                'volume': cabecalho.get('VOLUME'),
                'transportadora': cabecalho.get('NOMEPARC_TRANSP', ''),
                'codparctransp': cabecalho.get('CODPARCTRANSP'),
                'data_nota': cabecalho.get('DTNEG'),
                'data_nfd': '',
                'codemp': cabecalho.get('CODEMP'),
                'nunota_nf': cabecalho.get('NUNOTA'),
                'nunota_nfd': '',
                'tipmov_origem': 'V',
                'vlrnota': cabecalho.get('VLRNOTA', ''),
                'valor_nfd': '',
                'parceiro': cabecalho.get('NOMEPARC', '')
            }
    
    def _mapear_cabecalho_nf_com_nfd(self, cabecalho_nf: Dict, cabecalho_nfd: Dict) -> Dict:
            return {
                'nf_venda': cabecalho_nf.get('NUMNOTA'),
                'nfd': cabecalho_nfd.get('NUMNOTA'),
                'quantidade': cabecalho_nf.get('QTDVOL'),
                'volume': cabecalho_nf.get('VOLUME'),
                'transportadora': cabecalho_nf.get('NOMEPARC_TRANSP', ''),
                'codparctransp': cabecalho_nf.get('CODPARCTRANSP'),
                'data_nota': cabecalho_nf.get('DTNEG'),
                'data_nfd': cabecalho_nfd.get('DTNEG'),
                'codemp': cabecalho_nf.get('CODEMP'),
                'nunota_nf': cabecalho_nf.get('NUNOTA'),
                'nunota_nfd': cabecalho_nfd.get('NUNOTA'),
                'tipmov_origem': 'V',
                'vlrnota': cabecalho_nf.get('VLRNOTA', ''),
                'valor_nfd': cabecalho_nfd.get('VLRNOTA', ''),
                'parceiro': cabecalho_nf.get('NOMEPARC', ''),
                'codparc': cabecalho_nf.get('CODPARC', ''),
            }

    def _mapear_cabecalho_apenas_nfd(self, cabecalho: Dict) -> Dict:
            return {
                'nf_venda': '',  # Não conseguiu buscar automaticamente
                'nfd': cabecalho.get('NUMNOTA'),
                'quantidade': cabecalho.get('QTDVOL'),
                'volume': cabecalho.get('VOLUME'),
                'transportadora': cabecalho.get('NOMEPARC_TRANSP', ''),
                'codparctransp': cabecalho.get('CODPARCTRANSP'),
                'data_nota': '',
                'data_nfd': cabecalho.get('DTNEG'),
                'codemp': cabecalho.get('CODEMP'),
                'nunota_nf': '',
                'nunota_nfd': cabecalho.get('NUNOTA'),
                'tipmov_origem': 'D',
                'vlrnota': '',
                'valor_nfd': cabecalho.get('VLRNOTA', ''),
                'parceiro': cabecalho.get('NOMEPARC', ''),
                'codparc': cabecalho.get('CODPARC', ''),
            }

    def _mapear_cabecalho_nfd(self, cabecalho_nfd: Dict, cabecalho_nf: Dict) -> Dict:
            return {
                'nf_venda': cabecalho_nf.get('NUMNOTA'),
                'nfd': cabecalho_nfd.get('NUMNOTA'),
                'quantidade': cabecalho_nfd.get('QTDVOL'),       
                'volume': cabecalho_nfd.get('VOLUME'),           
                'transportadora': cabecalho_nfd.get('NOMEPARC_TRANSP', ''),
                'codparctransp': cabecalho_nfd.get('CODPARCTRANSP'),
                'data_nota': cabecalho_nf.get('DTNEG'),
                'data_nfd': cabecalho_nfd.get('DTNEG'),
                'codemp': cabecalho_nf.get('CODEMP'),
                'nunota_nf': cabecalho_nf.get('NUNOTA'),
                'nunota_nfd': cabecalho_nfd.get('NUNOTA'),
                'tipmov_origem': 'D',
                
                'vlrnota': cabecalho_nf.get('VLRNOTA', ''),
                'valor_nfd': cabecalho_nfd.get('VLRNOTA', ''),
                
                'parceiro': cabecalho_nf.get('NOMEPARC', ''),
                'codparc': cabecalho_nf.get('CODPARC', ''),  # CODPARC do parceiro
            }

    @staticmethod
    def _safe_float(value, default=0.0) -> float:
        """Converte valor monetário/numérico do Sankhya para float (BR/US)."""
        from core.services.sankhya.service import parse_valor_sankhya
        return parse_valor_sankhya(value, default)

    def _mapear_itens(self, itens: List[Dict], tipmov: str, vlr_destaque: float, total_m3: float,
                       vlr_frete: float = 0.0) -> List[Dict]:
            itens_mapeados = []

            for item in itens:
                # Calcular valor do item baseado no TIPMOV
                if tipmov == 'V':
                    valor_item = self._calcular_valor_nf_venda(item, vlr_frete, total_m3)
                else:
                    valor_item = self._calcular_valor_nf_devolucao(item, vlr_destaque, total_m3, vlr_frete)
                
                codlocalorig_raw = item.get('CODLOCALORIG', '') or ''
                codlocalorig = str(codlocalorig_raw).split(',')[0].strip()

                item_mapeado = {
                    'sequencia': item.get('SEQUENCIA'),
                    'codprod': item.get('CODPROD'),
                    'descrprod': item.get('DESCRPROD', ''),
                    'codgrupoprod': item.get('CODGRUPOPROD', ''),
                    'qtdneg': item.get('QTDNEG'),
                    'codlocaldest': item.get('CODLOCALDEST', ''),
                    'codlocalorig': codlocalorig,
                    'codemp': item.get('CODEMP', ''),
                    'valor_item': valor_item,
                    'localizacao': '',
                }
                
                itens_mapeados.append(item_mapeado)
            
            return itens_mapeados
    
    def _calcular_valor_nf_venda(self, item: Dict, vlr_frete_nota: float = 0.0, total_m3: float = 0.0) -> float:
            """
            Fórmula: VLRTOT - VLRDESC + VLRSUBST + FRETE
            FRETE = AD_VLRFRETEECOMMERCE do item quando preenchido; senão, VLRFRETE do
            cabeçalho da nota rateado por participação em M3 (mesmo método do VLRDESTAQUE
            na devolução). O Sankhya preenche o frete em UM dos dois campos por nota,
            nunca os dois com valores a somar — por isso é fallback, não soma.
            """
            try:
                vlrtot   = self._safe_float(item.get('VLRTOT'))
                vlrdesc  = self._safe_float(item.get('VLRDESC'))
                vlrsubst = self._safe_float(item.get('VLRSUBST'))
                vlrfrete_item = self._safe_float(item.get('AD_VLRFRETEECOMMERCE'))

                if vlrfrete_item > 0:
                    vlrfrete = vlrfrete_item
                else:
                    m3_item = self._safe_float(item.get('M3', 0))
                    participacao = (m3_item / total_m3) if total_m3 > 0 else 0.0
                    vlrfrete = participacao * vlr_frete_nota

                # FALLBACK: VLRTOT vazio/zero → recalcular com VLRUNIT × QTDNEG
                if vlrtot == 0:
                    vlrunit = self._safe_float(item.get('VLRUNIT'))
                    qtdneg  = self._safe_float(item.get('QTDNEG'), 1.0)
                    if vlrunit > 0 and qtdneg > 0:
                        vlrtot = vlrunit * qtdneg
                        logger.info(
                            f"▣ [NF VENDA] VLRTOT era 0 para CODPROD={item.get('CODPROD')} "
                            f"— recalculado via VLRUNIT({vlrunit}) × QTDNEG({qtdneg}) = {vlrtot}"
                        )

                valor_calculado = vlrtot - vlrdesc + vlrsubst + vlrfrete

                # FIX: Dividir por QTDNEG para retornar valor UNITÁRIO
                # (consistente com _calcular_valor_nf_devolucao)
                qtd_neg = self._safe_float(item.get('QTDNEG'), 1.0)
                if qtd_neg > 0:
                    valor_calculado = valor_calculado / qtd_neg

                logger.debug(
                    f"■ [NF VENDA] CODPROD={item.get('CODPROD')}: "
                    f"VLRTOT={vlrtot} | DESC={vlrdesc} | SUBST={vlrsubst} | FRETE={vlrfrete} | QTDNEG={qtd_neg} → unit={valor_calculado}"
                )
                return round(valor_calculado, 2)
            except Exception as e:
                logger.warning(f"Erro ao calcular valor do item NF Venda: {e}")
                return 0.0
    
    def _calcular_valor_nf_devolucao(self, item: Dict, vlr_destaque: float, total_m3: float,
                                      vlr_frete: float = 0.0) -> float:
        """
        Fórmula: VLRTOT - VLRDESC + VLRSUBST + (Participacao * VLRDESTAQUE) + FRETE
        Participacao = M3_item / Total_M3_nota
        FRETE = AD_VLRFRETEECOMMERCE do item quando preenchido; senão, VLRFRETE do
        cabeçalho rateado pela mesma participação em M3 (fallback, não soma — ver
        _calcular_valor_nf_venda).
        """
        try:
            vlrtot   = self._safe_float(item.get('VLRTOT', 0))
            vlrdesc  = self._safe_float(item.get('VLRDESC', 0))
            vlrsubst = self._safe_float(item.get('VLRSUBST', 0))
            m3_item  = self._safe_float(item.get('M3', 0))

            participacao = 0.0
            if total_m3 > 0:
                participacao = m3_item / total_m3

            valor_destaque_prop = participacao * vlr_destaque

            vlrfrete_item = self._safe_float(item.get('AD_VLRFRETEECOMMERCE'))
            valor_frete = vlrfrete_item if vlrfrete_item > 0 else (participacao * vlr_frete)

            # CORREÇÃO: Desconto deve ser SUBTRAÍDO, e Valor Destaque (prop)/Frete são SOMADOS
            valor_calculado_total = vlrtot - vlrdesc + vlrsubst + valor_destaque_prop + valor_frete
            
            # --- FIX: Dividir pelo QTDNEG para obter o valor unitário por item (solução do usuário) ---
            qtd_neg = float(item.get('QTDNEG', 1))
            
            if qtd_neg <= 0:
                logger.warning("QTDNEG é zero ou negativo, retornando valor total sem divisão.")
                return round(valor_calculado_total, 2)
            
            valor_unitario = valor_calculado_total / qtd_neg
            
            return round(valor_unitario, 2)
            
        except Exception as e:
            logger.error(f"Erro CRÍTICO ao calcular valor do item (devolução): {e}")
            try:
                return round(self._safe_float(item.get('VLRTOT', 0)), 2)
            except:
                return 0.0
    
    def salvar_avaria(self, dados_cabecalho: dict, itens: List[dict], 
                     dados_formulario: dict, user_email: str, user_empresa: str) -> Tuple[bool, str]:
        """
        MODIFICADO: Salva avaria incluindo empresa do usuário e Valor Indenização calculado
        
        Args:
            dados_cabecalho: Dados do cabeçalho da NF/NFD
            itens: Lista de itens selecionados
            dados_formulario: Dados do formulário
            user_email: Email do usuário
            user_empresa: Empresa do usuário (Matriz, Empresax, EmpresaY)
        
        Returns:
            Tuple[bool, str]: (sucesso, mensagem)
        """
        try:
            # 1. Gerar ID único da avaria
            avaria_id = self._gerar_id_avaria()
            
            # 2. Preparar dados incluindo empresa
            data_to_build_avaria = {
                'id': avaria_id,
                'cod_empresa': dados_cabecalho.get('codemp'),
                'nota': dados_cabecalho.get('nota', '') or dados_cabecalho.get('nf_venda', ''),
                'nfd': dados_cabecalho.get('nfd', ''),
                'quantidade': dados_cabecalho.get('quantidade', ''),
                'volume': dados_cabecalho.get('volume', ''),
                'transportadora': dados_cabecalho.get('transportadora', ''),
                'valor_nf': dados_cabecalho.get('valor_nf', '') or dados_cabecalho.get('vlrnota', ''),
                'data_registro': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'registrado_por': user_email,
                'empresa': user_empresa,
                'status_estoque': dados_formulario.get('status_estoque', 'Pendente'),
                'status_indenizacao': dados_formulario.get('status_indenizacao', 'Pendente'),
                
                # Campos do formulário
                'cte': dados_formulario.get('cte', ''),
                'valor_nfd': dados_formulario.get('valor_nfd', ''),
                'nfp': dados_formulario.get('nfp', ''),
                'valor_nfp': dados_formulario.get('valor_nfp', ''),
                'motivo_devolucao': dados_formulario.get('motivo_devolucao', ''),
                'indenizacao': dados_formulario.get('indenizacao', ''),
                'prazo_pagamento': dados_formulario.get('prazo_pagamento', ''),
                'data_pagamento': dados_formulario.get('data_pagamento', ''),
                'data_entrega': dados_formulario.get('data_entrega', ''),
                'data_coleta': dados_formulario.get('data_coleta', ''),
                'cod_rastreio_correios': dados_formulario.get('cod_rastreio_correios', ''),
                'observacao': dados_formulario.get('observacao', ''),
                'data_protocolo_mkt': dados_formulario.get('data_protocolo_mkt', ''),
                'protocolo_insucesso': dados_formulario.get('protocolo_insucesso', ''),
                
                # Campos de motivo inicial
                'cod_motivo_devolucao': dados_formulario.get('cod_motivo_devolucao', ''),
                'motivo_inicial': dados_formulario.get('motivo_inicial', ''),
                'cod_motivo_inicial': dados_formulario.get('cod_motivo_inicial', ''),
                'reembolso_pct': float(dados_formulario.get('reembolso_pct', 0) or 0),
                'codparc': dados_cabecalho.get('codparc', ''),  # CODPARC do parceiro (Ponto 6)
            }

            # CORREÇÃO: Garantir que motivos sejam NOMES, não códigos
            def _resolver_nome_motivo(val_raw):
                if val_raw and str(val_raw).strip().isdigit() and self.cache_sankhya:
                    try:
                        desc = self.cache_sankhya.obter_descricao_motivo(int(val_raw))
                        return desc if desc else val_raw
                    except:
                        return val_raw
                return val_raw

            # Resolver motivo atual (final)
            motivo_final_raw = dados_formulario.get('motivo_devolucao', '').strip()
            data_to_build_avaria['motivo_devolucao'] = _resolver_nome_motivo(motivo_final_raw)
            
            # Resolver motivo inicial
            motivo_ini_raw = dados_formulario.get('motivo_inicial', '').strip()
            # Se motivo_inicial vier vazio mas temos motivo_devolucao, usamos ele como inicial
            if not motivo_ini_raw:
                motivo_ini_raw = motivo_final_raw
                
            data_to_build_avaria['motivo_inicial'] = _resolver_nome_motivo(motivo_ini_raw)

            if data_to_build_avaria['motivo_devolucao'] != motivo_final_raw:
                 logger.info(f"Convertendo motivo_devolucao: {motivo_final_raw} -> {data_to_build_avaria['motivo_devolucao']}")
            if data_to_build_avaria['motivo_inicial'] != motivo_ini_raw:
                 logger.info(f"Convertendo motivo_inicial: {motivo_ini_raw} -> {data_to_build_avaria['motivo_inicial']}")
            
            # 2.5 CÁLCULO DO VALOR INDENIZAÇÃO
            valor_nf_base  = float(str(data_to_build_avaria.get('valor_nf',  0) or 0).replace(',', '.'))
            valor_nfd_base = float(str(data_to_build_avaria.get('valor_nfd', 0) or 0).replace(',', '.'))
            # NFD/NF é o último recurso — itens têm prioridade
            valor_base_fallback = valor_nfd_base if valor_nfd_base > 0 else valor_nf_base

            # ── Passo 1: Soma total de TODOS os itens selecionados ──────────
            valor_itens_total = 0.0
            for _it in itens:
                try:
                    _vi = float(str(_it.get('valor_item', 0) or 0).replace(',', '.'))
                    _qa = float(str(_it.get('qtd_avariada', 1) or 1).replace(',', '.'))
                    valor_itens_total += _vi * _qa
                except (ValueError, TypeError):
                    pass
            logger.info(f"   Soma total dos itens selecionados: R$ {valor_itens_total:.2f}")

            # Quando itens estão selecionados, valor_nf deve refletir apenas os itens registrados
            if valor_itens_total > 0:
                data_to_build_avaria['valor_nf'] = round(valor_itens_total, 2)
                logger.info(f"   valor_nf ajustado para soma dos itens: R$ {data_to_build_avaria['valor_nf']:.2f}")
                # Recalcular bases com o novo valor_nf
                valor_nf_base = data_to_build_avaria['valor_nf']
                valor_base_fallback = valor_nfd_base if valor_nfd_base > 0 else valor_nf_base

            # ── Passo 2: Locais configurados → filtrar subconjunto ──────────
            valor_itens_indenizaveis = 0.0
            try:
                import modules.wms.routes.parametrizacao_routes as _param_rt
                _cfg_svc = getattr(_param_rt, 'config_service', None)
                if _cfg_svc:
                    raw_locais = _cfg_svc.get_config(user_empresa, 'indenizacao_locais_contabilizar', '')
                    locais_list = [l.strip() for l in raw_locais.split(',') if l.strip()]
                    if locais_list:
                        # Locais configurados → usa somente itens nesses locais
                        valor_itens_indenizaveis = self.calcular_valor_base_por_locais(itens, locais_list)
                        logger.info(f"   Locais configurados → R$ {valor_itens_indenizaveis:.2f}")
                    else:
                        # Sem locais → todos os itens são base
                        valor_itens_indenizaveis = valor_itens_total
                        logger.info(f"   Sem locais → usando total dos itens: R$ {valor_itens_indenizaveis:.2f}")
            except Exception as _e:
                logger.warning(f"Erro ao buscar locais indenizáveis: {_e}")
                valor_itens_indenizaveis = valor_itens_total  # fallback seguro

            logger.info(f"Calculando valor indenização:")
            logger.info(f"   - Valor NF:     R$ {valor_nf_base}")
            logger.info(f"   - Valor NFD:    R$ {valor_nfd_base}")
            logger.info(f"   - Itens total:  R$ {valor_itens_total:.2f}")
            logger.info(f"   - Base efetiva: R$ {valor_itens_indenizaveis:.2f}"
                        f" (fallback NF/NFD: R$ {valor_base_fallback})")

            valor_indenizacao_calc = self._calculate_indenizacao_value(
                # valor_base = fallback NFD/NF, usado só quando itens_indenizaveis = 0
                valor_base=valor_base_fallback,
                indenizacao_type=data_to_build_avaria.get('indenizacao', ''),
                reembolso_pct=data_to_build_avaria.get('reembolso_pct'),
                valor_itens_indenizaveis=valor_itens_indenizaveis,
            )
            data_to_build_avaria['valor_inden_calc'] = valor_indenizacao_calc # Armazenar como float
            data_to_build_avaria['valor_indenizacao'] = valor_indenizacao_calc # Armazenar como float
    
            # 3. Criar objeto Avaria
            avaria_obj = Avaria.from_dict(data_to_build_avaria)
            dados_avaria_cabecalho_ordenados = avaria_obj.to_dict()

            # Campos monetários enviados como string "532.31" para evitar que o locale
            # PT-BR do Sheets reformate o número como "532,31".
            _campos_financeiros = {'valor_nf', 'valor_nfd', 'valor_nfp', 'valor_indenizacao', 'reembolso_pct'}
            def _fmt_monetario_sheets(v):
                s = str(v).replace('R$', '').replace(' ', '').strip()
                if '.' in s and ',' in s:
                    s = s.replace('.', '').replace(',', '.')
                elif ',' in s:
                    s = s.replace(',', '.')
                try:
                    return f"{float(s):.2f}"
                except (ValueError, TypeError):
                    return v

            row_sheets = [
                _fmt_monetario_sheets(v) if k in _campos_financeiros else v
                for k, v in dados_avaria_cabecalho_ordenados.items()
            ]

            # 4. Salvar cabeçalho na aba Avarias
            logger.info(f"Salvando cabeçalho da avaria {avaria_id} (Empresa: {user_empresa})...")
            self.sheets.append_row('Avarias', row_sheets)
            
            # 5. Salvar itens na aba Itens_Avarias
            logger.info(f"Salvando {len(itens)} itens da avaria {avaria_id}...")
            
            # OTIMIZAÇÃO: Montar todas as linhas e salvar em UMA chamada
            rows_to_append = []
            
            for idx, item in enumerate(itens, start=1):
                from modules.wms.models.item_avaria import _parse_valor_item
                
                # NORMALIZAÇÃO EXTRA: Garantir que o valor recebido seja tratado se vier como string com vírgula
                qtdneg_f = _parse_valor_item(item.get('qtdneg', 0))
                qtd_ava_f = _parse_valor_item(item.get('qtd_avariada', item.get('qtdneg', 0)))
                vlr_item_f = _parse_valor_item(item.get('valor_item', 0))

                item_avaria = ItemAvaria(
                    avaria_id=avaria_id,
                    sequencia=idx,
                    codprod=item.get('codprod', ''),
                    descrprod=item.get('descrprod', ''),
                    qtdneg=qtdneg_f,
                    qtd_avariada=qtd_ava_f, 
                    codlocaldest=item.get('codlocaldest', ''),
                    codlocalorig=item.get('codlocalorig', ''),
                    localizacao=item.get('localizacao', ''),
                    valor_item=vlr_item_f,
                    codgrupoprod=item.get('codgrupoprod', ''),
                    codemp=dados_cabecalho.get('codemp', '')
                )
                
                item_dict = item_avaria.to_dict()
                # valor_item como string "532.31" para evitar locale PT-BR no Sheets
                if item_dict.get('valor_item') is not None:
                    try:
                        item_dict['valor_item'] = f"{float(item_dict['valor_item']):.2f}"
                    except (ValueError, TypeError):
                        pass
                rows_to_append.append(list(item_dict.values()))
            
            # UMA ÚNICA chamada para todos os itens!
            if rows_to_append:
                success_itens, msg_itens = self.sheets.batch_append_rows('Itens_Avarias', rows_to_append)
                if not success_itens:
                    logger.warning(f"Falha ao salvar itens em batch: {msg_itens}")
            
            # Invalidar cache WMS para que a nova avaria apareça imediatamente
            self.sheets.invalidate_wms_cache()

            logger.info(f"Avaria {avaria_id} salva com sucesso!")
            logger.info(f"   Usuário: {user_email}")
            logger.info(f"   Empresa: {user_empresa}")
            logger.info(f"   Itens: {len(itens)}")

            return True, avaria_id, f"Avaria {avaria_id} registrada com sucesso!"
            
        except Exception as e:
            logger.error(f"Erro ao salvar avaria: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, None, f"Erro ao salvar: {str(e)}"
            
    def _ensure_itens_avarias_sheet_exists(self):
        """Garante que a aba Itens_Avarias existe"""
        try:
            self.sheets.sheet.worksheet('Itens_Avarias')
            logger.info("Aba 'Itens_Avarias' já existe")
        except Exception as e:
            if "No worksheet found" in str(e) or "worksheet" in str(e).lower():
                try:
                    logger.info("⌨Criando aba 'Itens_Avarias'...")
                    headers = [
                        'avaria_id', 'sequencia', 'codprod', 'descrprod', 'qtdneg',
                        'qtd_avariada', 'codlocaldest', 'codlocalorig', 'localizacao',
                        'valor_item', 'codgrupoprod', 'codemp',
                        'id_etiqueta', 'prefixo_estoque', 'etiqueta_anterior', 'status_etiqueta',
                        'observacao_produto', 'link_fotos_drive', 'data_identificacao',
                        'data_triagem', 'operador_identificacao', 'operador_triagem',
                    ]
                    ws = self.sheets.sheet.add_worksheet(
                        title='Itens_Avarias',
                        rows=10000,
                        cols=len(headers)
                    )
                    ws.update('A1', [headers])
                    logger.info(f"Aba 'Itens_Avarias' criada com {len(headers)} colunas")
                except Exception as create_e:
                    logger.error(f"Erro ao criar aba 'Itens_Avarias': {create_e}")
            else:
                logger.error(f"Erro ao verificar aba 'Itens_Avarias': {e}")
    
    def _gerar_id_avaria(self) -> str:
        """
        Gera ID único para a avaria, seguindo o padrão sugerido:
        Padrão: 00-YYMMDD-XXXX (onde XXXX é o sequencial do dia)
        
        CORREÇÃO: Implementado para usar self.sheets.get_all_records (agora existente)
        """
        try:
            # Obter data atual no formato YYMMDD (ex: 260116)
            data_hoje_str = datetime.now().strftime('%y%m%d')
            
            # Buscar avarias (agora self.sheets.get_all_records deve existir)
            avarias = self.sheets.get_all_records('Avarias')
            
            prefixo_hoje = f"00-{data_hoje_str}"
            
            # Filtrar avarias de hoje e encontrar o último sequencial
            avarias_hoje = [
                a for a in avarias 
                if a.get('id', '').startswith(prefixo_hoje)
            ]
            
            # Calcular próximo sequencial
            proximo_seq = len(avarias_hoje) + 1
            
            # Gerar ID no formato 00-YYMMDD-XXXX
            avaria_id = f"{prefixo_hoje}-{proximo_seq:04d}"
            
            logger.info(f"ID gerado (Padrão 00-YYMMDD-XXXX): {avaria_id}")
            return avaria_id
            
        except Exception as e:
            # Se a leitura do sheets falhar, usamos um fallback baseado em tempo com prefixo AVFALHA
            logger.error(f"Erro ao gerar ID via Sheets (usando fallback): {e}")
            return f"AVFALHA{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    def listar_avarias(self, filtros: Dict = None) -> List[Dict]:
        """
        Lista avarias com filtros opcionais
        
        MODIFICADO: Removido filtro de status geral.
        """
        try:
            avarias = self.sheets.get_all_records('Avarias')
            
            # Aplicar filtros se fornecidos
            if filtros:
                # Filtro status geral REMOVIDO
                # if filtros.get('status'):
                #     avarias = [a for a in avarias if a.get('status') == filtros['status']]
                
                if filtros.get('registrado_por'):
                    avarias = [a for a in avarias if a.get('registrado_por') == filtros['registrado_por']]
                
            
            return avarias
            
        except Exception as e:
            logger.error(f"Erro ao listar avarias: {e}")
            return []
            
    # Método para APLICAÇÃO DE REGRAS DE EXCLUSÃO •••
    def get_pending_avaria_items(self, user_empresa: str) -> List[Dict]:
        """
        Busca todos os itens de avarias (Avarias + Itens_Avarias)
        e aplica as Regras de Exclusão da parametrização para filtrar a lista.
        
        Retorna uma lista de avarias, cada uma com sua lista de itens pendentes
        (itens que não foram excluídos pelas regras e pertencem à empresa do usuário).
        """
        if not self.sheets.connected:
            logger.error("Google Sheets não conectado. Não é possível listar itens pendentes.")
            return []

        try:
            # 1. Carregar Regras de Exclusão Ativas
            rules = self.param_service.get_all_rules(include_inactive=False)
            logger.info(f"◒ {len(rules)} regras de exclusão ativas carregadas.")
            
            # 2. Carregar Avarias e Itens (usando cache se o sheets permitir)
            # CÓDIGO OTIMIZADO — usa cache de 60 segundos
            avarias_raw = self.sheets.get_wms_avarias_cached()
            itens_raw = self.sheets.get_wms_itens_cached()
            
            # Mapear avarias por ID para acesso rápido (convertendo para objeto Avaria)
            avarias_map = {a.get('id'): Avaria.from_dict(a) for a in avarias_raw}
            
            # 3. Agrupar itens por avaria e filtrar
            avarias_com_itens = {}
            for item_dict in itens_raw:
                avaria_id = item_dict.get('avaria_id')
                if avaria_id not in avarias_map:
                    continue 

                avaria_obj = avarias_map[avaria_id]
                
                # Filtrar por empresa do usuário (Requisito de segurança)
                if avaria_obj.empresa != user_empresa:
                    continue

                item_obj = ItemAvaria.from_dict(item_dict)
                
                # Aplicar Regras de Exclusão
                is_excluded = False
                for rule in rules:
                    if rule.matches(avaria_obj, item_obj):
                        is_excluded = True
                        logger.debug(f"  Item {item_obj.id_etiqueta or item_obj.codprod} excluído pela regra {rule.id}")
                        break
                
                if is_excluded:
                    continue
                
                # Se não foi excluído, adicionar ao resultado
                if avaria_id not in avarias_com_itens:
                    # Incluir o cabeçalho da avaria
                    avarias_com_itens[avaria_id] = {
                        'avaria': avaria_obj.to_dict(), 
                        'itens': []
                    }
                
                # Anexar informações formatadas do item
                item_data = item_obj.to_dict()
                item_data['descr_local'] = self.cache_sankhya.obter_descricao_local(item_obj.prefixo_estoque) if self.cache_sankhya else ""
                
                avarias_com_itens[avaria_id]['itens'].append(item_data)
            
            # 4. Formatar resultado (remover avarias sem itens pendentes)
            pending_list = [a for a in avarias_com_itens.values() if a['itens']]
            
            total_items_pending = sum(len(a['itens']) for a in pending_list)
            logger.info(f"Total de avarias/itens pendentes (após exclusão) para {user_empresa}: {total_items_pending}")
            return pending_list
            
        except Exception as e:
            logger.error(f"Erro ao buscar itens pendentes com exclusão: {e}")
            logger.error(traceback.format_exc())
            return []


    def obter_avaria_detalhada(self, avaria_id: str) -> Optional[Dict]:
        """
        Obtém avaria com seus itens
        
        Args:
            avaria_id: ID da avaria
            
        Returns:
            Dicionário com cabeçalho (objeto Avaria) e itens (lista de dicts) ou None
        """
        try:
            # 1. Buscar cabeçalho (raw dict) — USA CACHE
            avarias_raw = self.sheets.get_wms_avarias_cached()
            avaria_dict = next((a for a in avarias_raw if a.get('id') == avaria_id), None)
            
            if not avaria_dict:
                return None
            
            # 2. CONVERSÃO CRÍTICA: Converter dict para objeto Avaria
            # Isso garante que a rota possa chamar .to_dict() e acessar propriedades.
            avaria_obj = Avaria.from_dict(avaria_dict)
            
            # 3. Buscar itens (raw dicts) — USA CACHE
            itens = self.sheets.get_wms_itens_cached()
            
            # Filtro de itens da avaria específica
            # NOTA: Removido filtro de 'baixada'/'recusada' aqui para permitir visualização de histórico
            # nos detalhes e edição de avarias já processadas.
            itens_raw = [
                i for i in itens
                if i.get('avaria_id') == avaria_id
            ]

            # Normaliza campos numéricos (valor_item, qtdneg, qtd_avariada)
            # via from_dict → to_dict, garantindo que cheguem ao frontend como floats
            itens_avaria = [ItemAvaria.from_dict(i).to_dict() for i in itens_raw]

            # Nota: O retorno é um dicionário contendo o objeto Avaria e a lista de itens
            return {
                'avaria': avaria_obj,
                'itens': itens_avaria
            }
            
        except Exception as e:
            logger.error(f"Erro ao obter avaria: {e}")
            return None
