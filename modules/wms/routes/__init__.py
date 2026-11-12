
"""
routes/__init__.py
Exporta todos os blueprints do módulo WMS
"""

from .wms_routes import wms_bp
from .status_routes import status_bp
from .itens_routes import itens_bp
from .avarias_standalone_routes import avarias_bp
from .parametrizacao_routes import param_bp
from .validacao_estoque_routes import validacao_estoque_bp
from .transportadoras_routes import transportadoras_bp

__all__ = ['wms_bp', 'status_bp', 'itens_bp', 'avarias_bp', 'param_bp', 'validacao_estoque_bp', 'transportadoras_bp']
