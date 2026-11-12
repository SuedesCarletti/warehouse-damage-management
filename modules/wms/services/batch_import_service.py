"""
Serviço de Importação em Lote para WMS
Valida e importa avarias em lote a partir de arquivos Excel/CSV
"""

import logging
import pandas as pd
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field
from core.services.sankhya.service import parse_valor_sankhya

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """Representa um erro de validação"""
    linha: int
    campo: str
    valor: Any
    erro: str
    tipo: str  # 'error', 'warning'


@dataclass
class ValidationResult:
    """Resultado da validação de uma linha"""
    linha: int
    valido: bool
    dados: Dict = field(default_factory=dict)
    erros: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)


class BatchImportValidator:
    """
    Validador de importação em lote
    Aplica todas as regras de negócio antes da importação
    """
    
    def __init__(self, sankhya_service, wms_service):
        """
        Args:
            sankhya_service: Serviço de integração Sankhya
            wms_service: Serviço WMS para verificar dados existentes
        """
        self.sankhya = sankhya_service
        self.wms = wms_service
        
        # Cache para otimizar consultas repetidas
        self._cache_notas = {}
        self._cache_produtos = {}
    
    def validar_linha(self, linha: int, dados: Dict) -> ValidationResult:
        """
        Valida uma linha completa aplicando todas as regras
        
        Args:
            linha: Número da linha (para referência no erro)
            dados: Dicionário com dados da linha
            
        Returns:
            ValidationResult com status e erros encontrados
        """
        result = ValidationResult(linha=linha, valido=True)
        
        try:
            # 1. VALIDAÇÕES BÁSICAS - CAMPOS OBRIGATÓRIOS
            # volume e valor_nf serão preenchidos automaticamente do Sankhya
            campos_obrigatorios = {
                'cod_empresa': 'Código da Empresa',
                'nota': 'Número da Nota Fiscal',
                'quantidade': 'Quantidade',
                'transportadora': 'Transportadora',
                'motivo_devolucao': 'Motivo da Devolução',
                'codprod': 'Código do Produto',
                'qtd_avariada': 'Quantidade Avariada'
            }
            
            for campo, descricao in campos_obrigatorios.items():
                valor = dados.get(campo)
                if pd.isna(valor) or str(valor).strip() == '':
                    result.erros.append(ValidationError(
                        linha=linha,
                        campo=campo,
                        valor=valor,
                        erro=f"{descricao} é obrigatório",
                        tipo='error'
                    ))
                    result.valido = False
            
            # Se faltam campos obrigatórios, não continua validação
            if not result.valido:
                return result
            
            # 2. VALIDAÇÃO DA NOTA FISCAL NO SANKHYA
            cod_empresa = str(dados['cod_empresa'])
            numnota = str(dados['nota'])
            cache_key = f"{cod_empresa}_{numnota}"
            
            # Verificar cache primeiro
            if cache_key not in self._cache_notas:
                nota_sankhya = self.sankhya.buscar_nota_fiscal(
                    numnota=numnota,
                    codemp=cod_empresa
                )
                self._cache_notas[cache_key] = nota_sankhya
            else:
                nota_sankhya = self._cache_notas[cache_key]
            
            if not nota_sankhya:
                result.erros.append(ValidationError(
                    linha=linha,
                    campo='nota',
                    valor=numnota,
                    erro=f"Nota {numnota} não encontrada no Sankhya para empresa {cod_empresa}",
                    tipo='error'
                ))
                result.valido = False
                return result
    
            # CORREÇÃO: Usar dados do Sankhya para preencher volume e valor da NF
            if nota_sankhya:
                dados['valor_nf'] = nota_sankhya.get('VLRNOTA', dados.get('valor_nf', 0.0)) # Usa valor do Sankhya
                dados['volume'] = dados.get('volume') if not pd.isna(dados.get('volume')) else nota_sankhya.get('VOLUME', dados.get('quantidade'))
                logger.info(f"[L{linha}] Preenchido Volume: {dados['volume']} e ValorNF: {dados['valor_nf']} via Sankhya.")
    
            
            # 3. VALIDAÇÃO DE TIPMOV - DETECTAR NOTAS DUPLICADAS
            tipmov_sankhya = nota_sankhya.get('TIPMOV')
            
            # Verificar se existe outra nota com mesmo número mas TIPMOV diferente
            notas_duplicadas = self._verificar_notas_duplicadas(numnota, cod_empresa, tipmov_sankhya)
            if notas_duplicadas:
                result.warnings.append(ValidationError(
                    linha=linha,
                    campo='nota',
                    valor=numnota,
                    erro=f"ATENÇÃO: Nota {numnota} existe com TIPMOV diferente: {', '.join(notas_duplicadas)}",
                    tipo='warning'
                ))

            # AUTO-PREENCHIMENTO DE CAMPOS DO SANKHYA
            # Preencher valor_nf automaticamente se não informado
            if pd.isna(dados.get('valor_nf')) or str(dados.get('valor_nf', '')).strip() == '' or dados.get('valor_nf') is None:
                valor_nf_sankhya = self._parse_number(nota_sankhya.get('VLRNOTA', 0))
                if valor_nf_sankhya > 0:
                    dados['valor_nf'] = valor_nf_sankhya
                    logger.info(f"  Linha {linha}: valor_nf preenchido automaticamente do Sankhya: R$ {valor_nf_sankhya:.2f}")
                else:
                    # Se Sankhya não retornou valor, marcar como erro
                    result.erros.append(ValidationError(
                        linha=linha,
                        campo='valor_nf',
                        valor='',
                        erro="Valor da NF não informado e não encontrado no Sankhya",
                        tipo='error'
                    ))
                    result.valido = False
            
            # Preencher volume automaticamente se não informado
            if pd.isna(dados.get('volume')) or str(dados.get('volume', '')).strip() == '' or dados.get('volume') is None:
                # Prioridade 1: Volume do Sankhya
                volume_sankhya = self._parse_number(nota_sankhya.get('VOLUME', 0))
                if volume_sankhya > 0:
                    dados['volume'] = volume_sankhya
                    logger.info(f"  Linha {linha}: volume preenchido do Sankhya: {volume_sankhya}")
                else:
                    # Prioridade 2: Usar quantidade como fallback
                    qtd = self._parse_number(dados.get('quantidade', 0))
                    if qtd > 0:
                        dados['volume'] = qtd
                        logger.info(f"  Linha {linha}: volume preenchido com quantidade: {qtd}")
                    else:
                        # Se não tem quantidade nem volume, marcar como erro
                        result.erros.append(ValidationError(
                            linha=linha,
                            campo='volume',
                            valor='',
                            erro="Volume não informado e não foi possível determinar automaticamente",
                            tipo='error'
                        ))
                        result.valido = False
            
            # 4. VALIDAÇÃO DE NFD E NF ORIGEM
            nfd = dados.get('nfd')
            if nfd and not pd.isna(nfd):
                nfd = str(nfd)
                
                # Buscar NFD no Sankhya
                nfd_cache_key = f"{cod_empresa}_{nfd}"
                if nfd_cache_key not in self._cache_notas:
                    nfd_sankhya = self.sankhya.buscar_nota_fiscal(
                        numnota=nfd,
                        codemp=cod_empresa,
                        tipmov='D'
                    )
                    self._cache_notas[nfd_cache_key] = nfd_sankhya
                else:
                    nfd_sankhya = self._cache_notas[nfd_cache_key]
                
                if not nfd_sankhya:
                    result.erros.append(ValidationError(
                        linha=linha,
                        campo='nfd',
                        valor=nfd,
                        erro=f"NFD {nfd} não encontrada no Sankhya",
                        tipo='error'
                    ))
                    result.valido = False
                else:
                    # Validar coerência entre NF e NFD
                    nf_origem_nfd = nfd_sankhya.get('AD_NFORIGEM')
                    if nf_origem_nfd and str(nf_origem_nfd) != str(numnota):
                        result.warnings.append(ValidationError(
                            linha=linha,
                            campo='nfd',
                            valor=nfd,
                            erro=f"NFD {nfd} tem NF origem {nf_origem_nfd}, mas está sendo vinculada à NF {numnota}",
                            tipo='warning'
                        ))
            
            # 5. VALIDAÇÃO DE QUANTIDADE E VOLUME
            qtd_planilha = self._parse_number(dados.get('quantidade'))
            vol_planilha = self._parse_number(dados.get('volume'))
            
            qtd_sankhya = self._parse_number(nota_sankhya.get('QTDVOL', 0))
            vol_sankhya = self._parse_number(nota_sankhya.get('VOLUME', 0))
            
            if qtd_planilha != qtd_sankhya:
                result.warnings.append(ValidationError(
                    linha=linha,
                    campo='quantidade',
                    valor=qtd_planilha,
                    erro=f"Quantidade da planilha ({qtd_planilha}) difere do Sankhya ({qtd_sankhya})",
                    tipo='warning'
                ))
            
            if vol_planilha and vol_sankhya and vol_planilha != vol_sankhya:
                result.warnings.append(ValidationError(
                    linha=linha,
                    campo='volume',
                    valor=vol_planilha,
                    erro=f"Volume da planilha ({vol_planilha}) difere do Sankhya ({vol_sankhya})",
                    tipo='warning'
                ))
            
            # 6. VALIDAÇÃO DE VALOR NF E NFD
            valor_nf_planilha = self._parse_number(dados.get('valor_nf'))
            valor_nf_sankhya = self._parse_number(nota_sankhya.get('VLRNOTA', 0))
            
            if abs(valor_nf_planilha - valor_nf_sankhya) > 0.01:  # Tolerância de 1 centavo
                result.warnings.append(ValidationError(
                    linha=linha,
                    campo='valor_nf',
                    valor=valor_nf_planilha,
                    erro=f"Valor NF da planilha ({valor_nf_planilha:.2f}) difere do Sankhya ({valor_nf_sankhya:.2f})",
                    tipo='warning'
                ))
            
            # Validar valor NFD se existir
            if nfd and 'nfd_sankhya' in locals():
                valor_nfd_planilha = self._parse_number(dados.get('valor_nfd'))
                valor_nfd_sankhya = self._parse_number(nfd_sankhya.get('VLRNOTA', 0))
                
                if valor_nfd_planilha and abs(valor_nfd_planilha - valor_nfd_sankhya) > 0.01:
                    result.warnings.append(ValidationError(
                        linha=linha,
                        campo='valor_nfd',
                        valor=valor_nfd_planilha,
                        erro=f"Valor NFD da planilha ({valor_nfd_planilha:.2f}) difere do Sankhya ({valor_nfd_sankhya:.2f})",
                        tipo='warning'
                    ))
            
            # 7. VALIDAÇÃO DE INDENIZAÇÃO
            indenizacao_raw = dados.get('indenizacao')
            if indenizacao_raw and not pd.isna(indenizacao_raw):
                indenizacao_str = str(indenizacao_raw).strip().lower()

                # Mapear para valores canônicos
                if indenizacao_str in ('1', 'total', 'indenização total',
                                       'indenizacao total', 'sim'):
                    dados['indenizacao'] = 'total'
                elif indenizacao_str in ('parcial', 'indenização parcial',
                                         'indenizacao parcial'):
                    dados['indenizacao'] = 'parcial'
                elif indenizacao_str in ('0', 'nao_indenizavel', 'não indenizável',
                                          'nao indenizavel', 'nao'):
                    dados['indenizacao'] = 'nao_indenizavel'
                else:
                    dados['indenizacao'] = indenizacao_str  # Manter se não reconhecido
            
            # 8. VALIDAÇÃO DE STATUS
            status_estoque = dados.get('status_estoque')
            if status_estoque and not pd.isna(status_estoque):
                # Verificar se já existe cadastro com este status
                avarias_existentes = self.wms.get_all_avarias()
                for av in avarias_existentes:
                    if (str(av.nota) == str(numnota) and 
                        str(av.cod_empresa) == str(cod_empresa)):
                        result.warnings.append(ValidationError(
                            linha=linha,
                            campo='status_estoque',
                            valor=status_estoque,
                            erro=f"Já existe cadastro para Nota {numnota} - Empresa {cod_empresa}",
                            tipo='warning'
                        ))
                        break
            
            # 9. VALIDAÇÃO DE REEMBOLSO
            reembolso_pct = dados.get('reembolso_pct')
            if reembolso_pct and not pd.isna(reembolso_pct):
                reembolso_pct = self._parse_number(reembolso_pct)
                indenizacao_val = dados.get('indenizacao', '')
                
                if indenizacao_val == 'total' and reembolso_pct != 100:
                    result.warnings.append(ValidationError(
                        linha=linha,
                        campo='reembolso_pct',
                        valor=reembolso_pct,
                        erro=f"Indenização Total deve ter reembolso 100%, mas tem {reembolso_pct}%",
                        tipo='warning'
                    ))
                elif indenizacao_val == 'parcial' and reembolso_pct == 100:
                    result.warnings.append(ValidationError(
                        linha=linha,
                        campo='reembolso_pct',
                        valor=reembolso_pct,
                        erro=f"Indenização Parcial não deve ter reembolso 100%",
                        tipo='warning'
                    ))
            
            # 10. VALIDAÇÃO DE PRODUTO
            codprod = str(dados['codprod'])
            qtd_avariada = self._parse_number(dados['qtd_avariada'])
            
            # Buscar itens da nota
            itens_nota = self.sankhya.buscar_itens_nota(nota_sankhya.get('NUNOTA'))
            
            produto_encontrado = False
            for item in itens_nota:
                if str(item.get('CODPROD')) == codprod:
                    produto_encontrado = True
                    
                    # Validar quantidade não ultrapasse NF
                    qtdneg = self._parse_number(item.get('QTDNEG', 0))
                    if qtd_avariada > qtdneg:
                        result.erros.append(ValidationError(
                            linha=linha,
                            campo='qtd_avariada',
                            valor=qtd_avariada,
                            erro=f"Quantidade avariada ({qtd_avariada}) ultrapassa quantidade da NF ({qtdneg})",
                            tipo='error'
                        ))
                        result.valido = False
                    
                    # Validar quantidade não ultrapasse NFD se existir
                    if nfd and 'nfd_sankhya' in locals():
                        itens_nfd = self.sankhya.buscar_itens_nota(nfd_sankhya.get('NUNOTA'))
                        for item_nfd in itens_nfd:
                            if str(item_nfd.get('CODPROD')) == codprod:
                                qtdneg_nfd = self._parse_number(item_nfd.get('QTDNEG', 0))
                                if qtd_avariada > qtdneg_nfd:
                                    result.erros.append(ValidationError(
                                        linha=linha,
                                        campo='qtd_avariada',
                                        valor=qtd_avariada,
                                        erro=f"Quantidade avariada ({qtd_avariada}) ultrapassa quantidade da NFD ({qtdneg_nfd})",
                                        tipo='error'
                                    ))
                                    result.valido = False
                    
                    # Preencher valor_item e codemp automaticamente
                    dados['valor_item'] = parse_valor_sankhya(item.get('VLRTOT', ''))
                    dados['codemp'] = cod_empresa
                    dados['codlocalorig'] = item.get('CODLOCALORIG', '')
                    dados['codgrupoprod'] = item.get('CODGRUPOPROD', '')
                    
                    break
            
            if not produto_encontrado:
                result.erros.append(ValidationError(
                    linha=linha,
                    campo='codprod',
                    valor=codprod,
                    erro=f"Produto {codprod} não encontrado na nota {numnota}",
                    tipo='error'
                ))
                result.valido = False
            
            # PREPARAR DADOS VALIDADOS
            if result.valido:
                result.dados = self._preparar_dados_importacao(dados, nota_sankhya)
        
        except Exception as e:
            logger.error(f"Erro ao validar linha {linha}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.erros.append(ValidationError(
                linha=linha,
                campo='geral',
                valor='',
                erro=f"Erro interno: {str(e)}",
                tipo='error'
            ))
            result.valido = False
        
        return result
    
    def _verificar_notas_duplicadas(self, numnota: str, codemp: str, tipmov_atual: str) -> List[str]:
        """
        Verifica se existe nota com mesmo número mas TIPMOV diferente
        
        Returns:
            Lista de TIPMOV encontrados (ex: ['V', 'D'])
        """
        try:
            # Buscar todas as notas com este número
            notas_encontradas = []
            
            # Buscar como Venda
            if tipmov_atual != 'V':
                nota_v = self.sankhya.buscar_nota_fiscal(numnota, codemp, tipmov='V')
                if nota_v:
                    notas_encontradas.append('Venda')
            
            # Buscar como Devolução
            if tipmov_atual != 'D':
                nota_d = self.sankhya.buscar_nota_fiscal(numnota, codemp, tipmov='D')
                if nota_d:
                    notas_encontradas.append('Devolução')
            
            return notas_encontradas
        except Exception as e:
            logger.error(f"Erro ao verificar notas duplicadas: {e}")
            return []
    
    def _parse_number(self, value: Any) -> float:
        """Converte valor para número, tratando diferentes formatos"""
        if pd.isna(value):
            return 0.0
        
        try:
            # Remover espaços e converter vírgula em ponto
            value_str = str(value).strip().replace(',', '.')
            return float(value_str)
        except:
            return 0.0
    
    def _preparar_dados_importacao(self, dados: Dict, nota_sankhya: Dict) -> Dict:
        """
        Prepara dados validados para importação no formato esperado pelo WMS
        
        Args:
            dados: Dados da planilha (já validados)
            nota_sankhya: Dados da nota do Sankhya
            
        Returns:
            Dicionário pronto para importação
        """
        # Estrutura esperada pelo WMS (baseado em registrar_avaria)
        dados_preparados = {
            # Cabeçalho da Avaria
            'cod_empresa': str(dados['cod_empresa']),
            'nota': str(dados['nota']),
            'nfd': str(dados.get('nfd', '')) if dados.get('nfd') and not pd.isna(dados.get('nfd')) else '',
            'quantidade': int(self._parse_number(dados['quantidade'])),
            'volume': self._parse_number(dados['volume']),
            'transportadora': str(dados['transportadora']),
            'cte': str(dados.get('cte', '')) if dados.get('cte') and not pd.isna(dados.get('cte')) else '',
            'valor_nf': self._parse_number(dados['valor_nf']),
            'valor_nfd': self._parse_number(dados.get('valor_nfd', 0)),
            'nfp': str(dados.get('nfp', '')) if dados.get('nfp') and not pd.isna(dados.get('nfp')) else '',
            'valor_nfp': self._parse_number(dados.get('valor_nfp', 0)),
            'prazo_pagamento': str(dados.get('prazo_pagamento', '')) if dados.get('prazo_pagamento') and not pd.isna(dados.get('prazo_pagamento')) else '',
            'data_pagamento': str(dados.get('data_pagamento', '')) if dados.get('data_pagamento') and not pd.isna(dados.get('data_pagamento')) else '',
            'data_coleta': str(dados.get('data_coleta', '')) if dados.get('data_coleta') and not pd.isna(dados.get('data_coleta')) else '',
            'motivo_devolucao': str(dados['motivo_devolucao']),
            'indenizacao': dados.get('indenizacao', ''),
            'observacao': str(dados.get('observacao', '')) if dados.get('observacao') and not pd.isna(dados.get('observacao')) else '',
            'status_estoque': str(dados.get('status_estoque', 'Pendente')),
            'status_indenizacao': str(dados.get('status_indenizacao', 'Pendente')),
            'cod_rastreio_correios': str(dados.get('cod_rastreio_correios', '')) if dados.get('cod_rastreio_correios') and not pd.isna(dados.get('cod_rastreio_correios')) else '',
            'reembolso_pct': self._parse_number(dados.get('reembolso_pct', 0)),
            
            # Itens selecionados (formato esperado pelo WMS)
            'itens_selecionados': [{
                'sequencia': str(dados.get('sequencia', '1')),
                'codprod': str(dados['codprod']),
                'descrprod': str(dados.get('descrprod', '')),
                'qtdneg': self._parse_number(dados.get('qtdneg', 0)),
                'qtd_avariada': self._parse_number(dados['qtd_avariada']),
                'localizacao': str(dados.get('localizacao', '')),
                'valor_item': self._parse_number(dados.get('valor_item', 0)),
                'codlocalorig': str(dados.get('codlocalorig', '')),
                'codgrupoprod': str(dados.get('codgrupoprod', '')),
                'observacao_produto': str(dados.get('observacao_produto', '')) if dados.get('observacao_produto') and not pd.isna(dados.get('observacao_produto')) else ''
            }]
        }
        
        return dados_preparados


class BatchImportService:
    """
    Serviço principal de importação em lote
    Coordena validação, processamento e importação
    """
    
    def __init__(self, sankhya_service, wms_service):
        self.validator = BatchImportValidator(sankhya_service, wms_service)
        self.wms = wms_service
        self.sankhya = sankhya_service
    
    def processar_arquivo(
        self, 
        filepath: str, 
        chunk_size: int = 100,
        user_email: str = None
    ) -> Dict:
        """
        Processa arquivo de importação em lote
        
        Args:
            filepath: Caminho do arquivo Excel/CSV
            chunk_size: Tamanho do lote para processamento
            user_email: Email do usuário que está importando
            
        Returns:
            Dict com resultados do processamento:
            {
                'total_linhas': int,
                'validadas': int,
                'importadas': int,
                'com_erro': int,
                'com_aviso': int,
                'erros': List[Dict],
                'avisos': List[Dict],
                'tempo_processamento': float
            }
        """
        inicio = datetime.now()
        
        try:
            # Carregar arquivo
            logger.info(f"Carregando arquivo: {filepath}")
            if filepath.endswith('.csv'):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)
            
            logger.info(f"Arquivo carregado: {len(df)} linhas, {len(df.columns)} colunas")
            
            resultados = {
                'total_linhas': len(df),
                'validadas': 0,
                'importadas': 0,
                'com_erro': 0,
                'com_aviso': 0,
                'erros': [],
                'avisos': [],
                'tempo_processamento': 0
            }
            
            # Processar em chunks
            for i in range(0, len(df), chunk_size):
                chunk = df.iloc[i:i+chunk_size]
                logger.info(f"Processando chunk {i//chunk_size + 1} ({len(chunk)} linhas)...")
                
                for idx, row in chunk.iterrows():
                    linha_num = idx + 2  # +2 porque Excel começa em 1 e tem header
                    
                    # Converter row para dict
                    dados = row.to_dict()
                    
                    # Validar linha
                    validation = self.validator.validar_linha(linha_num, dados)
                    
                    # Coletar erros e avisos
                    if validation.erros:
                        resultados['com_erro'] += 1
                        for erro in validation.erros:
                            resultados['erros'].append({
                                'linha': linha_num,
                                'campo': erro.campo,
                                'valor': erro.valor,
                                'erro': erro.erro
                            })
                    
                    if validation.warnings:
                        resultados['com_aviso'] += 1
                        for aviso in validation.warnings:
                            resultados['avisos'].append({
                                'linha': linha_num,
                                'campo': aviso.campo,
                                'valor': aviso.valor,
                                'aviso': aviso.erro
                            })
                    
                    # Se válido, importar
                    if validation.valido:
                        resultados['validadas'] += 1
                        
                        # Importar no WMS
                        sucesso, avaria_obj, msg = self.wms.registrar_avaria(
                            data=validation.dados,
                            user_email=user_email or 'importacao_lote@sistema.com'
                        )
                        
                        if sucesso:
                            resultados['importadas'] += 1
                            logger.info(f"  Linha {linha_num} importada com sucesso")
                        else:
                            resultados['com_erro'] += 1
                            resultados['erros'].append({
                                'linha': linha_num,
                                'campo': 'importacao',
                                'valor': '',
                                'erro': f"Erro ao importar: {msg}"
                            })
                            logger.error(f"  Erro ao importar linha {linha_num}: {msg}")
            
            # Calcular tempo total
            fim = datetime.now()
            resultados['tempo_processamento'] = (fim - inicio).total_seconds()
            
            logger.info(f"""
╔══════════════════════════════════════════════╗
║       RESUMO DA IMPORTAÇÃO EM LOTE          ║
╠══════════════════════════════════════════════╣
║ Total de linhas:        {resultados['total_linhas']:>4}            ║
║ Validadas com sucesso:  {resultados['validadas']:>4}            ║
║ Importadas com sucesso: {resultados['importadas']:>4}            ║
║ Com erros:              {resultados['com_erro']:>4}            ║
║ Com avisos:             {resultados['com_aviso']:>4}            ║
║ Tempo total:            {resultados['tempo_processamento']:.2f}s        ║
╚══════════════════════════════════════════════╝
            """)
            
            return resultados
            
        except Exception as e:
            logger.error(f"Erro fatal ao processar arquivo: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            return {
                'total_linhas': 0,
                'validadas': 0,
                'importadas': 0,
                'com_erro': 1,
                'com_aviso': 0,
                'erros': [{
                    'linha': 0,
                    'campo': 'arquivo',
                    'valor': filepath,
                    'erro': f"Erro ao processar arquivo: {str(e)}"
                }],
                'avisos': [],
                'tempo_processamento': 0
            }
