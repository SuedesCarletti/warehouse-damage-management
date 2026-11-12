"""
drive_upload_service.py - VERSÃO COM ESTRUTURA HIERÁRQUICA NFD/Etiqueta
=========================================================================

ESTRUTURA NO DRIVE:
===================
WMS_Fotos_Produtos/
└── NFD-12345/  (ou NF-67890 se não tiver NFD)
    ├── 00-Z8C5-0001/  (etiqueta 1)
    │   ├── foto1.jpg
    │   └── foto2.jpg
    │
    └── 00-Z8C5-0002/  (etiqueta 2)
        └── foto1.jpg

LÓGICA:
=======
1. Criar/buscar pasta da NFD (ou NF)
2. Criar/buscar pasta da etiqueta dentro da NFD
3. Upload de fotos na pasta da etiqueta
4. Retornar link da pasta da etiqueta
"""

import logging
from typing import Tuple, List, Optional, Dict
import os
import pickle
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive.file']


class DriveUploadService:
    """Serviço para upload com estrutura hierárquica NFD/Etiqueta"""
    
    def __init__(self, credentials_file: str, token_file: str, 
                 pasta_raiz_id: Optional[str] = None):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.pasta_raiz_id_config = pasta_raiz_id
        self.pasta_raiz_id = None
        self.pasta_raiz_nome = 'WMS_Fotos_Produtos'
        
        try:
            creds = self._get_credentials()
            self.drive_service = build('drive', 'v3', credentials=creds)
            logger.info("DriveUploadService (hierárquico) inicializado")
            
            if self.pasta_raiz_id_config:
                self._validar_pasta_raiz_config()
            else:
                logger.warning("GOOGLE_DRIVE_ROOT_FOLDER_ID não configurado!")
                
        except Exception as e:
            logger.error(f"Erro ao inicializar DriveUploadService: {e}")
            raise
    
    def _get_credentials(self):
        """Obtém credenciais OAuth 2.0"""
        creds = None
        
        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                creds = pickle.load(token)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                logger.error("Token OAuth inválido")
                raise Exception("OAuth token inválido. Execute setup_oauth_drive.py")
        
        return creds
    
    def _validar_pasta_raiz_config(self) -> bool:
        """Valida pasta raiz"""
        try:
            folder = self.drive_service.files().get(
                fileId=self.pasta_raiz_id_config,
                fields='id, name, mimeType'
            ).execute()
            
            if folder.get('mimeType') != 'application/vnd.google-apps.folder':
                logger.error("ID não é uma pasta")
                return False
            
            self.pasta_raiz_id = self.pasta_raiz_id_config
            logger.info(f"Pasta raiz validada: {folder.get('name')}")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao validar pasta raiz: {e}")
            return False
    
    # NOVO MÉTODO: Buscar ou criar pasta NFD/NF
    def _obter_pasta_nota(self, nfd: Optional[str], nf: str) -> Tuple[bool, str, str]:
        """
        Busca ou cria pasta da nota fiscal (NFD ou NF)
        
        Args:
            nfd: Número da NFD (pode ser None)
            nf: Número da NF (sempre existe)
            
        Returns:
            (sucesso, pasta_id, link_pasta)
        """
        try:
            if not self.pasta_raiz_id:
                return False, "", "Pasta raiz não configurada"
            
            # Determinar nome da pasta: NFD se existir, senão NF
            nome_pasta_nota = f"NFD-{nfd}" if nfd else f"NF-{nf}"
            
            # Buscar pasta existente
            query = f"'{self.pasta_raiz_id}' in parents and name = '{nome_pasta_nota}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            
            results = self.drive_service.files().list(
                q=query,
                fields='files(id, webViewLink)',
                pageSize=1
            ).execute()
            
            files = results.get('files', [])
            
            if files:
                # Pasta já existe
                pasta_id = files[0].get('id')
                link_pasta = files[0].get('webViewLink')
                logger.info(f"Pasta da nota encontrada: {nome_pasta_nota} (ID: {pasta_id})")
                return True, pasta_id, link_pasta
            
            # Criar pasta nova
            file_metadata = {
                'name': nome_pasta_nota,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [self.pasta_raiz_id]
            }
            
            folder = self.drive_service.files().create(
                body=file_metadata,
                fields='id, webViewLink'
            ).execute()
            
            pasta_id = folder.get('id')
            link_pasta = folder.get('webViewLink')
            
            # Tornar pública
            permission = {
                'type': 'anyone',
                'role': 'reader'
            }
            self.drive_service.permissions().create(
                fileId=pasta_id,
                body=permission
            ).execute()
            
            logger.info(f"Pasta da nota criada: {nome_pasta_nota} (ID: {pasta_id})")
            return True, pasta_id, link_pasta
            
        except Exception as e:
            logger.error(f"Erro ao obter pasta da nota: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, "", str(e)
    
    # NOVO MÉTODO: Buscar ou criar pasta da etiqueta (dentro da NFD)
    def _obter_pasta_etiqueta(self, id_etiqueta: str, 
                              pasta_nota_id: str) -> Tuple[bool, str, str]:
        """
        Busca ou cria pasta da etiqueta dentro da pasta da nota
        
        Args:
            id_etiqueta: ID da etiqueta (ex: "00-Z8C5-0001")
            pasta_nota_id: ID da pasta NFD/NF
            
        Returns:
            (sucesso, pasta_id, link_pasta)
        """
        try:
            # Buscar pasta existente
            query = f"'{pasta_nota_id}' in parents and name = '{id_etiqueta}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            
            results = self.drive_service.files().list(
                q=query,
                fields='files(id, webViewLink)',
                pageSize=1
            ).execute()
            
            files = results.get('files', [])
            
            if files:
                # Pasta já existe
                pasta_id = files[0].get('id')
                link_pasta = files[0].get('webViewLink')
                logger.info(f"Pasta da etiqueta encontrada: {id_etiqueta} (ID: {pasta_id})")
                return True, pasta_id, link_pasta
            
            # Criar pasta nova
            file_metadata = {
                'name': id_etiqueta,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [pasta_nota_id]
            }
            
            folder = self.drive_service.files().create(
                body=file_metadata,
                fields='id, webViewLink'
            ).execute()
            
            pasta_id = folder.get('id')
            link_pasta = folder.get('webViewLink')
            
            # Tornar pública
            permission = {
                'type': 'anyone',
                'role': 'reader'
            }
            self.drive_service.permissions().create(
                fileId=pasta_id,
                body=permission
            ).execute()
            
            logger.info(f"Pasta da etiqueta criada: {id_etiqueta} (ID: {pasta_id})")
            return True, pasta_id, link_pasta
            
        except Exception as e:
            logger.error(f"Erro ao obter pasta da etiqueta: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, "", str(e)
    
    # MÉTODO MODIFICADO: Upload com estrutura hierárquica
    def upload_fotos_produto(self, id_etiqueta: str, 
                            filepaths: List[str],
                            nfd: Optional[str] = None,
                            nf: str = None) -> Tuple[bool, str, str, List[Dict[str, str]]]:
        """
        🔥 NOVA VERSÃO: Upload com estrutura hierárquica NFD/Etiqueta
        
        Args:
            id_etiqueta: ID da etiqueta (ex: "00-Z8C5-0001")
            filepaths: Lista de caminhos dos arquivos
            nfd: Número da NFD (opcional)
            nf: Número da NF (obrigatório)
            
        Returns:
            (sucesso, link_pasta_etiqueta, mensagem, lista_fotos)
        """
        lista_fotos = []
        
        try:
            if not nf:
                return False, "", "Número da NF é obrigatório", []
            
            logger.info(f"Estrutura: {'NFD-' + nfd if nfd else 'NF-' + nf} → {id_etiqueta}")
            
            # PASSO 1: Obter pasta da nota (NFD ou NF)
            sucesso, pasta_nota_id, link_nota = self._obter_pasta_nota(nfd, nf)
            
            if not sucesso:
                return False, "", f"Erro ao criar pasta da nota: {pasta_nota_id}", []
            
            # PASSO 2: Obter pasta da etiqueta (dentro da pasta da nota)
            sucesso, pasta_etiqueta_id, link_etiqueta = self._obter_pasta_etiqueta(
                id_etiqueta, 
                pasta_nota_id
            )
            
            if not sucesso:
                return False, "", f"Erro ao criar pasta da etiqueta: {pasta_etiqueta_id}", []
            
            # PASSO 3: Upload das fotos na pasta da etiqueta
            for filepath in filepaths:
                filename = os.path.basename(filepath)
                
                file_metadata = {
                    'name': filename,
                    'parents': [pasta_etiqueta_id]
                }
                
                media = MediaFileUpload(
                    filepath,
                    mimetype='image/jpeg',
                    resumable=True
                )
                
                file = self.drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, name'
                ).execute()
                
                file_id = file.get('id')
                file_name = file.get('name')
                
                lista_fotos.append({
                    'file_id': file_id,
                    'filename': file_name
                })
                
                logger.info(f"Foto enviada: {filename} (ID: {file_id})")
            
            logger.info(f"{len(filepaths)} foto(s) enviada(s) para {id_etiqueta}")
            
            # Retornar link da pasta da ETIQUETA
            return True, link_etiqueta, f"{len(filepaths)} foto(s) enviada(s)", lista_fotos
            
        except Exception as e:
            logger.error(f"Erro ao upload de fotos: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, "", str(e), []
    
    # NOVO MÉTODO: Listar fotos de uma pasta (etiqueta)
    def listar_fotos_etiqueta(self, pasta_id_drive: str) -> Tuple[bool, List[Dict[str, str]], str]:
        """
        Lista todas as fotos dentro de uma pasta específica do Google Drive
        
        Args:
            pasta_id_drive: ID da pasta no Google Drive
            
        Returns:
            (sucesso, lista_fotos, mensagem)
            
        Exemplo de retorno:
            (True, [
                {'file_id': 'abc123', 'filename': 'foto1.jpg'},
                {'file_id': 'def456', 'filename': 'foto2.jpg'}
            ], "2 foto(s) encontrada(s)")
        """
        try:
            if not pasta_id_drive:
                return False, [], "pasta_id_drive não fornecido"
            
            logger.info(f"Listando fotos da pasta: {pasta_id_drive}")
            
            # Query para buscar apenas arquivos de imagem dentro da pasta
            # Não buscar pastas, apenas arquivos
            query = f"'{pasta_id_drive}' in parents and mimeType contains 'image/' and trashed = false"
            
            results = self.drive_service.files().list(
                q=query,
                fields='files(id, name, mimeType, webViewLink, thumbnailLink)',
                pageSize=100,  # Máximo de 100 fotos por vez
                orderBy='createdTime desc'  # Mais recentes primeiro
            ).execute()
            
            files = results.get('files', [])
            
            if not files:
                logger.info(f"Nenhuma foto encontrada na pasta {pasta_id_drive}")
                return True, [], "Nenhuma foto encontrada"
            
            # Montar lista de fotos
            lista_fotos = []
            for file in files:
                lista_fotos.append({
                    'file_id': file.get('id'),
                    'filename': file.get('name'),
                    'mimeType': file.get('mimeType'),
                    'webViewLink': file.get('webViewLink'),
                    'thumbnailLink': file.get('thumbnailLink')
                })
            
            logger.info(f"{len(lista_fotos)} foto(s) encontrada(s)")
            return True, lista_fotos, f"{len(lista_fotos)} foto(s) encontrada(s)"
            
        except Exception as e:
            logger.error(f"Erro ao listar fotos: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, [], f"Erro ao listar fotos: {str(e)}"
    
    # MÉTODO AUXILIAR: Extrair folder_id de link
    @staticmethod
    def extract_folder_id_from_link(link: str) -> Optional[str]:
        """Extrai folder_id de um link do Drive"""
        try:
            if not link:
                return None
                
            if '/folders/' in link:
                folder_id = link.split('/folders/')[1].split('?')[0]
                return folder_id
            elif 'id=' in link:
                folder_id = link.split('id=')[1].split('&')[0]
                return folder_id
            return None
        except Exception as e:
            logger.error(f"Erro ao extrair folder_id: {e}")
            return None
