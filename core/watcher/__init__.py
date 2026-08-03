"""core/watcher — Módulos compartilháveis para Watcher V2."""
from .config import WatcherV2Config, carregar_config
from .estados import EstadoPDF
from .resultados import RegistroProcessamento

__all__ = ["WatcherV2Config", "carregar_config", "EstadoPDF", "RegistroProcessamento"]
