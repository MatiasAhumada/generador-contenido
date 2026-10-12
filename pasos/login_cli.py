"""Autenticación y estado de la sesión local de Codex CLI.

El inicio de sesión se hace una sola vez desde una terminal con ``codex login``.
Este módulo consulta esa sesión para la interfaz del Estudio; nunca almacena
tokens ni intenta crear una autenticación propia.
"""
import subprocess

try:
    from . import cli_codex
except ImportError:  # ejecutado con la carpeta pasos en sys.path
    import cli_codex


class ErrorLogin(RuntimeError):
    """Error que la interfaz puede mostrar tal cual."""


CARPETA_CUENTAS = ""


def situacion(carpeta=""):
    """Comprueba si Codex CLI usa la sesión OAuth de ChatGPT del equipo."""
    try:
        ejecutable = cli_codex.localizar("el Estudio")
    except RuntimeError as fallo:
        return {"conectada": False, "correo": "", "plan": "", "metodo": "",
                "error": str(fallo)}
    try:
        proceso = subprocess.run(
            [ejecutable, "login", "status"], capture_output=True, timeout=30,
            env=cli_codex.entorno(), **cli_codex.SIN_VENTANA)
    except (OSError, subprocess.TimeoutExpired) as fallo:
        return {"conectada": False, "correo": "", "plan": "", "metodo": "",
                "error": f"no se ha podido consultar Codex CLI: {fallo}"}

    salida = ((proceso.stdout or b"") + (proceso.stderr or b"")).decode(
        "utf-8", errors="replace").strip()
    baja = salida.lower()
    chatgpt = "logged in using chatgpt" in baja
    api_key = "api key" in baja
    error = ""
    if api_key:
        error = ("Codex CLI está conectado con una clave API. Ejecuta `codex logout` "
                 "y después `codex login` para usar la sesión de ChatGPT.")
    elif proceso.returncode != 0 and not chatgpt:
        error = salida or "Codex CLI no tiene una sesión activa. Ejecuta `codex login`."
    return {"conectada": bool(proceso.returncode == 0 and chatgpt),
            "correo": "", "plan": "ChatGPT" if chatgpt else "",
            "metodo": "chatgpt" if chatgpt else ("api_key" if api_key else ""),
            "error": error}


def entrar(cuenta_id="codex-local", carpeta=""):
    raise ErrorLogin("abre una terminal, ejecuta `codex login` y vuelve a comprobar la conexión")


def pegar(cuenta_id, codigo):
    raise ErrorLogin("Codex gestiona el inicio de sesión; ejecuta `codex login` en una terminal")


def mirar(cuenta_id="codex-local"):
    ficha = situacion()
    return {"estado": "dentro" if ficha["conectada"] else "fallo",
            "enlace": "", "mensaje": ficha.get("error") or "", "ficha": ficha}


def cancelar(cuenta_id="codex-local"):
    return False


def salir(carpeta=""):
    raise ErrorLogin("para cerrar la sesión de Codex, ejecuta `codex logout` en una terminal")


def olvidar_todos():
    """Compatibilidad con el ciclo de vida del antiguo flujo de acceso web."""
    return None


def describir():
    return "sesión local de Codex CLI iniciada con `codex login`"
