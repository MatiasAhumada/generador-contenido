"""Compara modelos de imagen de OpenRouter con el mismo prompt y referencias.

Ejemplo (PowerShell):
  python herramientas/comparar_imagenes_openrouter.py `
    --prompt-file escena.txt --referencia estilo.png --referencia personaje.png

La clave se solicita de forma oculta si OPENROUTER_API_KEY no esta definida.
Las imagenes y el informe se escriben en la carpeta indicada; no modifica el
motor normal ni las claves del Estudio.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import mimetypes
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

API = "https://openrouter.ai/api/v1/images"
MODELOS = (
    "bytedance-seed/seedream-5-0-flash",
    "bytedance-seed/seedream-4.5",
)


def data_url(ruta: Path) -> str:
    mime = mimetypes.guess_type(ruta.name)[0] or "image/png"
    contenido = base64.b64encode(ruta.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{contenido}"


def nombre_seguro(modelo: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", modelo).strip("_")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    origen = parser.add_mutually_exclusive_group(required=True)
    origen.add_argument("--prompt", help="texto de la escena")
    origen.add_argument("--prompt-file", type=Path, help="archivo UTF-8 con el prompt")
    parser.add_argument("--referencia", action="append", type=Path, default=[],
                        help="imagen de referencia; repetir para varias (máx. 14)")
    parser.add_argument("--salida", type=Path, default=Path("comparacion-imagenes"),
                        help="carpeta de salida (por defecto: ./comparacion-imagenes)")
    parser.add_argument("--modelo", action="append", dest="modelos",
                        help="slug de modelo; repetir. GPT Image 2 via OpenRouter es opcional: openai/gpt-image-2")
    parser.add_argument("--resolucion", choices=("1K", "2K"), default="1K")
    parser.add_argument("--aspecto", choices=("2:3", "9:16", "16:9", "1:1"), default="2:3",
                        help="relacion de aspecto; 2:3 coincide con la salida vertical actual del motor")
    args = parser.parse_args()

    prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
    prompt = prompt.strip()
    if not prompt:
        parser.error("el prompt no puede estar vacio")
    if len(args.referencia) > 14:
        parser.error("OpenRouter permite como maximo 14 referencias en este benchmark")
    for ruta in args.referencia:
        if not ruta.is_file():
            parser.error(f"no existe la referencia: {ruta}")

    clave = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not clave:
        clave = getpass.getpass("Clave de OpenRouter (no se guarda): ").strip()
    if not clave:
        parser.error("falta la clave de OpenRouter")

    modelos = args.modelos or list(MODELOS)
    args.salida.mkdir(parents=True, exist_ok=True)
    refs = [{"type": "image_url", "image_url": {"url": data_url(ruta)}}
            for ruta in args.referencia]
    informe = {
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
        "aspect_ratio": args.aspecto,
        "resolution": args.resolucion,
        "referencias": [str(ruta.resolve()) for ruta in args.referencia],
        "resultados": [],
    }

    for modelo in modelos:
        payload = {"model": modelo, "prompt": prompt, "n": 1,
                   "resolution": args.resolucion, "aspect_ratio": args.aspecto,
                   "output_format": "png"}
        if refs:
            payload["input_references"] = refs
        inicio = time.monotonic()
        fila = {"modelo": modelo}
        try:
            respuesta = requests.post(
                API,
                headers={"Authorization": f"Bearer {clave}",
                         "Content-Type": "application/json",
                         "X-Title": "AS Video Studio image benchmark"},
                json=payload, timeout=600)
            if not respuesta.ok:
                raise RuntimeError(f"HTTP {respuesta.status_code}: {respuesta.text[:1000]}")
            datos = respuesta.json()
            if not datos.get("data"):
                raise RuntimeError("la API respondio sin imagenes")
            imagen = datos["data"][0]
            ext = "png"
            archivo = args.salida / f"{nombre_seguro(modelo)}.{ext}"
            archivo.write_bytes(base64.b64decode(imagen["b64_json"], validate=True))
            usage = datos.get("usage") or {}
            fila.update({"estado": "ok", "archivo": str(archivo.resolve()),
                         "segundos": round(time.monotonic() - inicio, 1),
                         "coste_usd": usage.get("cost"), "usage": usage,
                         "media_type": imagen.get("media_type")})
            coste = usage.get("cost")
            print(f"{modelo}: OK — {fila['segundos']} s — "
                  f"{('$' + format(coste, '.4f')) if isinstance(coste, (int, float)) else 'costo no informado'} — {archivo}")
        except Exception as error:  # cada modelo sigue aunque otro falle
            fila.update({"estado": "error", "error": str(error),
                         "segundos": round(time.monotonic() - inicio, 1)})
            print(f"{modelo}: ERROR — {error}", file=sys.stderr)
        informe["resultados"].append(fila)

    ruta_informe = args.salida / "informe.json"
    ruta_informe.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Informe: {ruta_informe.resolve()}")
    return 0 if any(fila["estado"] == "ok" for fila in informe["resultados"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
