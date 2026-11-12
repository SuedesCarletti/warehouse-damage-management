# core/routes/__init__.py
from .auth_routes import auth_bp
from .home_routes import home_bp

__all__ = ['auth_bp', 'home_bp']
