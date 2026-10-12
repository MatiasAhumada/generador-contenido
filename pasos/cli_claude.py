"""Compatibilidad de importación para versiones antiguas del proyecto.

El proveedor actual es :mod:`cli_codex`; este módulo ya no inicia procesos de
Claude ni mantiene una implementación paralela.
"""
import sys

try:
    from . import cli_codex as _cli_codex
except ImportError:  # ejecutado con la carpeta pasos en sys.path
    import cli_codex as _cli_codex

sys.modules[__name__] = _cli_codex
