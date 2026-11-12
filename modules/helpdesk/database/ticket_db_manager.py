"""
database/ticket_db_manager.py
✅ VERSÃO COM FILTRO POR EMPRESA
"""

import sqlite3
import logging
from typing import List, Dict, Optional
from contextlib import contextmanager
from datetime import datetime

from modules.helpdesk.models import Ticket

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Gerenciador de operações no banco de dados"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
        self._add_empresa_column_if_missing()  # NOVO: Adiciona coluna empresa se não existir
    
    @contextmanager
    def get_connection(self):
        """Context manager para conexões seguras"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Erro no banco: {e}")
            raise e
        finally:
            conn.close()
    
    def init_database(self):
        """Inicializa estrutura do banco"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabela tickets
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_number TEXT UNIQUE NOT NULL,
                    user_email TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT DEFAULT 'Aberto',
                    assigned_to TEXT,
                    third_party_email TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP,
                    email_opened BOOLEAN DEFAULT 0,
                    resolution_token TEXT,
                    created_by TEXT NOT NULL,
                    empresa TEXT
                )
            ''')
            
            # Tabela logs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS status_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    old_status TEXT,
                    new_status TEXT,
                    changed_by TEXT,
                    change_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
                )
            ''')
            
            logger.info("Banco de dados inicializado")
    
    def _add_empresa_column_if_missing(self):
        """NOVO: Adiciona coluna empresa se não existir (migração segura)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Verificar se coluna existe
                cursor.execute("PRAGMA table_info(tickets)")
                columns = [column[1] for column in cursor.fetchall()]
                
                if 'empresa' not in columns:
                    logger.info("Adicionando coluna 'empresa' na tabela tickets...")
                    cursor.execute('ALTER TABLE tickets ADD COLUMN empresa TEXT')
                    logger.info("Coluna 'empresa' adicionada com sucesso")
                else:
                    logger.info("Coluna 'empresa' já existe")
        except Exception as e:
            logger.error(f"Erro ao adicionar coluna empresa: {e}")
    
    # TICKETS - CREATE
    
    def create_ticket(self, ticket: Ticket) -> int:
        """
        Cria novo ticket no banco
        
        Args:
            ticket: Objeto Ticket
            
        Returns:
            int: ID do ticket criado
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # MODIFICADO: Incluir campo empresa
            cursor.execute('''
                INSERT INTO tickets (
                    ticket_number, user_email, user_name, department,
                    category, subject, description, priority, status,
                    third_party_email, resolution_token, created_by, empresa
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ticket.ticket_number,
                ticket.user_email,
                ticket.user_name,
                ticket.department,
                ticket.category,
                ticket.subject,
                ticket.description,
                ticket.priority,
                ticket.status,
                ticket.third_party_email,
                ticket.resolution_token,
                ticket.created_by,
                ticket.empresa  # NOVO CAMPO
            ))
            
            return cursor.lastrowid
    
    # TICKETS - READ
    
    def get_ticket_by_number(self, ticket_number: str) -> Optional[Ticket]:
        """Busca ticket por número"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM tickets WHERE ticket_number = ?', (ticket_number,))
            row = cursor.fetchone()
            
            if row:
                return Ticket.from_dict(dict(row))
            return None
    
    def get_ticket_by_token(self, token: str) -> Optional[Ticket]:
        """Busca ticket por token de resolução"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM tickets WHERE resolution_token = ?', (token,))
            row = cursor.fetchone()
            
            if row:
                return Ticket.from_dict(dict(row))
            return None
    
    def get_tickets(self, filters: dict = None) -> List[Ticket]:
        """
        Lista tickets com filtros opcionais
        
        Args:
            filters: dict com 'status', 'user_email', 'priority', 'empresa', etc
            
        Returns:
            List[Ticket]: Lista de tickets
        """
        query = 'SELECT * FROM tickets'
        params = []
        
        if filters:
            conditions = []
            
            # NOVO: Filtro por empresa
            if 'empresa' in filters and filters['empresa']:
                conditions.append('empresa = ?')
                params.append(filters['empresa'])
            
            if 'status' in filters:
                conditions.append('status = ?')
                params.append(filters['status'])
            
            if 'user_email' in filters:
                conditions.append('user_email = ?')
                params.append(filters['user_email'])
            
            if 'priority' in filters:
                conditions.append('priority = ?')
                params.append(filters['priority'])
            
            if 'category' in filters:
                conditions.append('category = ?')
                params.append(filters['category'])
            
            if 'search' in filters:
                conditions.append('(ticket_number LIKE ? OR subject LIKE ? OR description LIKE ?)')
                search_term = f"%{filters['search']}%"
                params.extend([search_term, search_term, search_term])
            
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
        
        query += ' ORDER BY created_at DESC'
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [Ticket.from_dict(dict(row)) for row in rows]
    
    # TICKETS - UPDATE
    
    def update_ticket_status(self, ticket_number: str, new_status: str, 
                            reason: str = "") -> bool:
        """Atualiza status de um ticket"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            update_query = '''
                UPDATE tickets 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
            '''
            params = [new_status]
            
            if new_status in ['Resolvido', 'Fechado']:
                update_query += ', closed_at = CURRENT_TIMESTAMP'
            
            update_query += ' WHERE ticket_number = ?'
            params.append(ticket_number)
            
            cursor.execute(update_query, params)
            return cursor.rowcount > 0
    
    def mark_email_opened(self, ticket_number: str) -> bool:
        """Marca email como visualizado"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE tickets SET email_opened = 1 WHERE ticket_number = ?',
                (ticket_number,)
            )
            return cursor.rowcount > 0
    
    # STATUS LOGS
    
    def add_status_log(self, ticket_id: int, new_status: str, 
                       changed_by: str, old_status: str = None,
                       change_reason: str = "") -> int:
        """Adiciona log de mudança de status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO status_logs (
                    ticket_id, old_status, new_status, 
                    changed_by, change_reason
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                ticket_id, old_status, new_status,
                changed_by, change_reason
            ))
            
            return cursor.lastrowid
    
    def get_status_logs(self, ticket_id: int) -> List[dict]:
        """Retorna histórico de status de um ticket"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM status_logs 
                WHERE ticket_id = ?
                ORDER BY created_at DESC
            ''', (ticket_id,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ESTATÍSTICAS
    
    def get_ticket_count(self, filters: dict = None) -> int:
        """Conta tickets com filtros"""
        query = 'SELECT COUNT(*) as count FROM tickets'
        params = []
        
        if filters:
            conditions = []
            
            # NOVO: Filtro por empresa
            if 'empresa' in filters and filters['empresa']:
                conditions.append('empresa = ?')
                params.append(filters['empresa'])
            
            if 'status' in filters:
                conditions.append('status = ?')
                params.append(filters['status'])
            if 'user_email' in filters:
                conditions.append('user_email = ?')
                params.append(filters['user_email'])
            
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchone()
            return result['count'] if result else 0
