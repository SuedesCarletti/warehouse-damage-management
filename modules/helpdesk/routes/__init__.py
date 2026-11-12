"""
routes/__init__.py
Exporta blueprints ativos do módulo HelpDesk
"""

from .ticket_routes import ticket_bp
from .api_routes import api_bp
from .public_routes import public_bp

__all__ = ['ticket_bp', 'api_bp', 'public_bp']
