"""
El unico sitio que arma y lanza una orden del CLI de Codex.

Por que existe
--------------
La lista de argumentos estaba copiada en tres ficheros (p3_guion, p5_revision_audio
y app.py) y ya paso una vez que "faltaban los flags del CLI en dos de los cuatro
sitios": el paso de guion tardaba 70 s y el mismo trabajo lanzado desde otra
pantalla tardaba diez minutos, porque a esa copia se le habia olvidado --effort.
Un solo sitio hace imposible ese fallo.

El esfuerzo es una variable importante
--------------------------------------
Codex recibe siempre el esfuerzo de forma explicita. Las previsiones iniciales
son aproximaciones hasta que el Estudio acumula tiempos reales de esta sesión:
no se reutilizan mediciones de proveedores anteriores.

De ahi salen dos reglas que este modulo aplica siempre:

1. El esfuerzo va SIEMPRE explicito. Si no, el mismo ajuste tarda distinto segun
   con que entorno se arranco el servicio, y entonces ninguna medida vale.
2. **El tiempo maximo escala con el esfuerzo.** Un techo fijo de 420 s con
   esfuerzo 'high' es una trampa: garantiza TiempoAgotado, y TiempoAgotado no se
   reintenta a proposito. Ofrecer un esfuerzo cuyo techo no le da tiempo a
   terminar es ofrecer un boton que solo sabe fallar.

Uso
---
    from . import cli_codex

    texto, sobre = cli_codex.ejecutar(
        instruccion, modelo="sonnet", esfuerzo="low",
        cwd=carpeta, tiempo_max_s=0)      # 0 = automatico segun el esfuerzo

`sobre` es el JSON del CLI con un bloque `_ajuste` anadido (modelo, esfuerzo y
segundos reales), que es lo que despues leen el medidor de coste y el historico.
El CLI no informa del esfuerzo en su respuesta, asi que si no lo estampa quien
llama, el historico se queda ciego justo en la variable que mas mueve el tiempo.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time

# ------------------------------------------------------------------ catalogo

# Alias que entiende el CLI. Se validan contra esta lista porque un modelo mal
# escrito hoy solo se detecta por returncode != 0, o sea despues de haber pagado
# el arranque del CLI y la espera entera.
# Sin ventana negra. Codex CLI se lanza como proceso de consola, y desde un
# servicio eso abre una ventana encima de todo por cada llamada en Windows.
# Escrito aqui y no importado de `medios`: este modulo se carga antes que el, y
# no tiene ninguna otra razon para conocerlo.
SIN_VENTANA = {}
if os.name == "nt":
    SIN_VENTANA = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}

MODELOS = ("haiku", "sonnet", "opus")

# Los nombres antiguos siguen aceptandose porque hay ajustes y recetas
# guardados con ellos. Solo son alias de esfuerzo; Codex recibe ids actuales.
MODELOS_CODEX = {
    "haiku": "gpt-6-luna",
    "sonnet": "gpt-6.1-sol",
    "opus": "gpt-6-astra",
}
MODELOS_CODEX_VALIDOS = tuple(MODELOS_CODEX.values())

# Un id completo tambien vale: el catalogo cambia mas rapido que este fichero.
PATRON_MODELO_COMPLETO = re.compile(r"^gpt-[a-z0-9][a-z0-9.\-]*$")

ESFUERZOS = ("low", "medium", "high", "xhigh", "max")

# El Estudio usa gpt-6-luna en todas las fases y parte de esfuerzo alto. Las
# fases permiten elevar aún más el esfuerzo cuando una decisión afecta muchas
# unidades del vídeo.
MODELO_POR_DEFECTO = "haiku"
ESFUERZO_POR_DEFECTO = "high"

# Factores iniciales de tiempo para estimar antes de tener historial; se ajustan
# con el historial real de cada combinación.
FACTOR_ESFUERZO = {"low": 1.0, "medium": 2.5, "high": 5.0, "xhigh": 8.0, "max": 12.0}

# Lo mismo para el modelo, relativo a sonnet.
FACTOR_MODELO = {"haiku": 0.6, "sonnet": 1.0, "opus": 2.2}

# Techo absoluto. Un trabajo puede tardar mucho y eso es legitimo (la barra lo
# ensena y se puede cancelar), pero mas de una hora esperando a una sola llamada
# es casi siempre algo colgado, no algo trabajando.
TIEMPO_MAXIMO_S = 3600
TIEMPO_MINIMO_S = 30

# Variables heredadas de instalaciones anteriores. Se limpian para que el
# servicio no dependa del entorno desde el que se inició.
ENTORNO_FUERA = ("CLAUDE_EFFORT", "MAX_THINKING_TOKENS")

# Y las que sacarian la llamada FUERA de la suscripcion: una clave de API, un
# token, una pasarela o un proveedor en la nube. El Estudio consume la
# suscripcion del CLI y solo esa, asi que ninguna variable heredada del entorno
# puede convertir un paso en pago por uso sin que nadie se entere. Se borran en
# el entorno del HIJO, que es lo unico que hace la garantia independiente de
# desde donde se arranque el servicio.
#
# Duplicado a proposito en los cinco sitios que llaman al CLI (watcher, creador,
# nocturno de X, nocturno de SEO y aqui), por la misma razon que uso_cli.py: los
# sistemas se conectan por contrato, nunca importandose codigo.
PAGO_POR_USO = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")

# Sin herramientas el CLI contesta en un solo turno: para redactar texto no tiene
# nada que leer ni que escribir, todo el material va en la instruccion.
HERRAMIENTAS_VETADAS = ("Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob",
                        "Grep", "WebFetch", "WebSearch", "Task", "TodoWrite")


class TiempoAgotado(RuntimeError):
    """La llamada al CLI vencio. No se reintenta: volveria a vencer."""


class LimiteAgotado(RuntimeError):
    """Se acabo el cupo de la suscripcion. Tampoco se reintenta.

    Pasa lo mismo que con un timeout y por eso comparte forma: reintentar es
    volver a fallar. Pero se distingue por dos motivos, y los dos se vieron el
    24-08-2026, cuando el cupo semanal se agoto a mitad de una tanda:

    1. **Lo que se ve.** El CLI sale con codigo 1 y su mensaje ("has agotado tu
       limite semanal, se renueva el 27 de agosto a las 7:00") viaja DENTRO del
       sobre JSON, en `result`. Sin mirar ahi, lo que llegaba a la pantalla era
       600 caracteres de JSON crudo con `input_tokens: 0` -- que parece una
       averia del Estudio y no lo es.

    2. **Lo que se hace.** Un cupo agotado se reintenta unas cuantas veces en un
       segundo cada una, se gastan todos los reintentos y el paso muere igual;
       peor: el error que se guarda es el del ULTIMO intento, o sea el mismo
       mensaje pero sin la pista de que ya se sabia desde el primero.

    Con la cuenta de respaldo SI se reintenta una vez (`ejecutar`): un login
    distinto tiene un cupo distinto, que es justo para lo que existe.
    """


# ------------------------------------------------------------------ validacion

#: Con que codigo sale una SUITE que no ha podido correr porque no hay cupo.
#: No es 1 a proposito: `pruebas.ps1` lo pinta como CUPO y no lo cuenta como
#: fallo. Una suite que necesita el CLI de verdad --la cadena de guion es la
#: unica-- se pondria en rojo durante los dias que tarde en renovarse el cupo, y
#: un rojo que no significa nada roto es peor que no correrla: ensena a ignorar
#: el rojo. 3 y no 2 porque 2 es lo que devuelve el propio python al no poder
#: abrir el fichero.
CODIGO_SIN_CUPO = 3

#: Como suena un cupo agotado, en los dos idiomas en que puede contestar el CLI.
#: Se casa por PAREJAS (algo de limite + algo de agotado/renovado) y no por
#: frases enteras: el texto exacto cambia cada vez que cambia el CLI, y una
#: frase literal que deje de casar volveria a dar el JSON crudo en pantalla.
_LIMITE_QUE = ("limit", "limite", "límite", "quota", "cuota")
_LIMITE_COMO = ("reset", "renov", "reach", "hit", "exhaust", "has alcanzado",
                "has agotado", "agotado", "exceed", "superado", "upgrade")


def limite_de(texto):
    """El mensaje si el CLI dice que se acabo el cupo; si no, None. -> str|None

    Se lee del `result` del sobre, que es donde el CLI pone su explicacion en
    cristiano. Lo que devuelve es ese mismo texto limpio, para poder ensenarlo
    tal cual: dice la fecha en que se renueva, y esa fecha es lo unico que le
    importa a quien esta mirando la pantalla.
    """
    plano = str(texto or "").strip()
    if not plano:
        return None
    bajo = plano.lower()
    if any(q in bajo for q in _LIMITE_QUE) and any(c in bajo for c in _LIMITE_COMO):
        return plano
    return None


def modelo_valido(valor):
    """True si el CLI de Codex va a entender ese modelo o alias heredado."""
    texto = str(valor or "").strip().lower()
    return bool(texto) and (texto in MODELOS
                            or texto in MODELOS_CODEX_VALIDOS
                            or bool(PATRON_MODELO_COMPLETO.match(texto)))


def normalizar_modelo(valor, estricto=True, defecto=MODELO_POR_DEFECTO):
    texto = str(valor or "").strip().lower()
    if not texto:
        return defecto
    if not modelo_valido(texto):
        if estricto:
            raise ValueError(
                f"modelo desconocido: {texto!r}. Usa uno de "
                f"{', '.join(MODELOS)} o un id completo tipo 'gpt-6.1-sol'")
        return defecto
    return texto


def normalizar_esfuerzo(valor, estricto=True, defecto=ESFUERZO_POR_DEFECTO):
    texto = str(valor or "").strip().lower()
    if not texto:
        return defecto
    if texto not in ESFUERZOS:
        if estricto:
            raise ValueError(
                f"esfuerzo tiene que ser uno de {', '.join(ESFUERZOS)}")
        return defecto
    return texto


def factor(modelo=MODELO_POR_DEFECTO, esfuerzo=ESFUERZO_POR_DEFECTO):
    """Cuanto se espera que multiplique una combinacion al tiempo de sonnet/low."""
    modelo = str(modelo or "").strip().lower()
    alias = next((nombre for nombre, real in MODELOS_CODEX.items()
                  if real == modelo), modelo)
    return (FACTOR_MODELO.get(alias, 1.0)
            * FACTOR_ESFUERZO.get(str(esfuerzo or "").strip().lower(), 1.0))


def tiempo_max(esfuerzo=ESFUERZO_POR_DEFECTO, base_s=420, modelo=None,
               pedido_s=0):
    """Segundos que se le dan a UNA llamada antes de cortarla.

    `pedido_s` mayor que cero manda: es el techo que ha fijado una persona a
    mano. Con 0 el techo se deriva del esfuerzo, que es lo unico que evita la
    trampa de ofrecer 'high' contra un techo pensado para 'low'.
    """
    try:
        pedido_s = int(pedido_s or 0)
    except (TypeError, ValueError):
        pedido_s = 0
    if pedido_s > 0:
        return max(TIEMPO_MINIMO_S, min(TIEMPO_MAXIMO_S, pedido_s))
    escala = factor(modelo or MODELO_POR_DEFECTO, esfuerzo)
    return int(max(TIEMPO_MINIMO_S,
                   min(TIEMPO_MAXIMO_S, round(float(base_s) * escala))))


# Ajuste por defecto de cada fase, elegido por la COMPLEJIDAD de su tarea y no
# por inercia.
#
# De donde salen estos valores
# ----------------------------
# De la matriz por tipo de tarea que publican las guias del parametro de
# esfuerzo, no de una corazonada:
#
#   pregunta simple, clasificacion, lotes ......... bajo
#   generacion de contenido, redaccion rutinaria .. medio
#   revision de codigo, depuracion, trabajo creativo alto
#   arquitectura, analisis legal, flujos agenticos  maximo
#
# Y de tres hallazgos medidos que corrigen la intuicion de "mas esfuerzo es
# siempre mejor":
#
#   - En FrontierCode v1.1, Opus 5 con esfuerzo MEDIO es la configuracion mas
#     eficiente por computo (53,4 %), y el esfuerzo alto da rendimientos
#     decrecientes o ligeramente negativos en tareas de codigo.
#   - La calidad escala casi lineal hasta 'xhigh' y ahi se aplana: 'max' es
#     mucho gasto para muy poco, y quien lo ha medido lo llama una trampa.
#     El alto ya da el 90-95 % de la calidad del maximo por una fraccion.
#   - El esfuerzo bajo esta INFRAUTILIZADO: tareas como redactar una respuesta
#     corta a partir de datos concretos salen igual de bien en bajo.
#
# El coste no es lineal: de bajo a medio son ~2,5x los tokens, a alto ~6x y a
# maximo ~12x. Por eso subir un escalon tiene que ganarselo.
POR_FASE = {
    # Redactar el guion desde un transcript de 19.000 palabras, con horquilla de
    # palabras, troceado en bloques y adaptacion a varios idiomas. En la matriz
    # es "generacion de contenido", que pide MEDIO. Se resistio la tentacion de
    # poner alto: el historial dice que en bajo termina bien 20 de 20 veces, asi
    # que medio ya es subir un escalon sobre algo que funciona.
    "guion": {"modelo": "haiku", "esfuerzo": "medium",
              "porque": "generacion de contenido larga con restricciones; en la "
                        "matriz por tarea eso es esfuerzo medio, y el historial "
                        "dice que en bajo ya termina bien 20 de 20 veces"},

    # Leer varias transcripciones enteras de un canal y destilar de ellas COMO
    # escribe. Es el gemelo en texto de 'guia_estilo': una sola llamada, entrada
    # larga, y su salida --las instrucciones de guion-- la heredan todos los
    # videos del canal. Ahi se aplica la misma lectura que alli, con una
    # diferencia que baja un escalon: esto lo lee y lo edita una persona en un
    # campo de texto antes de que escriba nada, asi que un matiz perdido se ve y
    # se corrige, mientras que la guia de estilo se aplica a ciegas en cada
    # imagen.
    "tono": {"modelo": "haiku", "esfuerzo": "medium",
             "porque": "destila de varias transcripciones como escribe un canal, "
                       "y eso lo heredan todos sus videos; medio y no alto "
                       "porque lo lee y lo corrige una persona antes de usarlo"},

    # Leer el guion ENTERO y repartirlo en personajes, sitios y tramos. Es una
    # sola llamada por video, con todo el texto delante, y de ella depende que
    # los planos no se repitan: es el sitio donde pagar esfuerzo sale barato.
    # Medio y no alto por lo mismo que el guion: alto se reserva para cuando el
    # historial diga que medio se queda corto.
    # Elegir voz leyendo ciento veinte descripciones y fijar cuatro mandos. Es
    # lectura y criterio, no razonamiento largo, y ademas la propuesta se ve
    # antes de grabar nada: equivocarse no cuesta una toma, cuesta un clic.
    "voz_descrita": {"modelo": "haiku", "esfuerzo": "low",
                     "porque": "leer un catalogo y elegir; lo que salga se ve "
                               "en los mandos antes de pagar ninguna sintesis"},

    "catalogo_visual": {"modelo": "haiku", "esfuerzo": "medium",
                        "porque": "una sola llamada por video que decide el "
                                  "reparto y los sitios de todo el montaje; "
                                  "equivocarse aqui sale mucho mas caro que el "
                                  "escalon de esfuerzo"},

    # Mirar una imagen por plano y decidir el grafismo. Es trabajo de juicio
    # visual repetido muchas veces, no de razonamiento largo: medio sobra y el
    # coste manda, porque son 174 imagenes en tandas de 24.
    # Elegir que tramos se cuentan mejor con texto que con dibujo, y ESCRIBIR
    # esa frase. Es lo contrario del de arriba: no mira ninguna imagen, mira el
    # guion entero, y decide poco (tres o cuatro cartelas en treinta planos)
    # pero cada decision es de criterio narrativo y ademas hay que redactarla.
    # Ahi el esfuerzo si se nota, y como son una o dos llamadas por video, sale
    # barato pagarlo.
    # QUE SE VE EN CADA PLANO, con TODOS los planos delante. El trabajo no es
    # describir un plano --eso lo hace cualquiera-- sino que cada uno se
    # distinga del de al lado, y eso es comparar. Una o dos llamadas por video.
    "direccion": {"modelo": "haiku", "esfuerzo": "medium",
                  "porque": "el trabajo es COMPARAR unos planos con otros para "
                            "que no se repitan, no describir uno: es criterio "
                            "sobre el conjunto, y son una o dos llamadas por "
                            "video, asi que el esfuerzo alto sale barato"},

    # EL PROMPT ENTERO DE CADA PLANO. Lo mismo que `direccion` un escalon mas
    # arriba: alli el agente escribe una linea y el codigo la encaja entre el
    # sitio, el reparto y el tono; aqui escribe el parrafo entero y ademas
    # ELIGE el sitio entre los que su tramo pone a mano.
    "redactor": {"modelo": "haiku", "esfuerzo": "medium",
                 "porque": "escribe el encargo entero de cada plano y elige su "
                           "sitio entre los del tramo: son mas decisiones por "
                           "plano que dirigirlo, y sigue siendo una sola "
                           "llamada por video"},

    "plan_cartelas": {"modelo": "haiku", "esfuerzo": "medium",
                      "porque": "pocas decisiones por video pero de criterio "
                                "narrativo, y ademas hay que redactar el texto: "
                                "una o dos llamadas, el esfuerzo alto cuesta "
                                "poco y se nota en lo que escribe"},

    # MIRAR las imagenes ya generadas y decir cual esta mal. El unico agente que
    # Reescribir UN bloque de unas 30 palabras aplicando una nota del revisor, y
    # una llamada POR BLOQUE. Es el ejemplo de manual de lo que sale igual de
    # bien en bajo, y ahi la latencia se multiplica por el numero de bloques.
    "revision_audio": {"modelo": "haiku", "esfuerzo": "low",
                       "porque": "reescribir 30 palabras con una nota concreta "
                                 "es de los casos que salen igual de bien en "
                                 "bajo, y se llama una vez por bloque"},

    # Mirar capturas anotadas, decidir que cambiar y EDITAR ficheros del
    # proyecto: flujo agentico con efectos en disco. La matriz apunta a maximo,
    # pero el maximo esta medido como rendimientos decrecientes, y el alto da el
    # 90-95 % por mucho menos.
    "capturas_agente": {"modelo": "haiku", "esfuerzo": "high",
                        "porque": "es agentico y toca ficheros: la matriz pide "
                                  "maximo, pero alto da el 90-95 % de esa "
                                  "calidad por una fraccion del gasto"},

    # Mirar de 3 a 8 fotogramas y describir el estilo que comparten. Es
    # observacion descriptiva, la categoria que sale bien en bajo.
    "guia_estilo": {"modelo": "haiku", "esfuerzo": "medium",
                    "porque": "es la UNICA descripcion en palabras del dibujo de "
                              "este video, y la reciben todas las imagenes que se "
                              "generan: los planos y las hojas de personaje. Era "
                              "el escalon mas barato del Estudio -- 23 segundos, "
                              "193 palabras -- para la pieza que decide como se "
                              "ve el video entero, y se notaba: describia bien el "
                              "trazo y se dejaba lo que mas canta, como se "
                              "resuelve una cara. Es UNA llamada por video"},

    # Decidir, plano a plano, si la imagen que ya existe sigue valiendo con la
    # frase nueva. Es comparar dos frases cortas y contestar si o no: en la
    # matriz por tarea es clasificacion, que es la categoria que sale igual de
    # bien en bajo. Y ademas tiene que ser BARATO: si analizar si se puede
    # conservar cuesta mas que rehacerlo, nadie lo usaria.
    "conservacion": {"modelo": "haiku", "esfuerzo": "low",
                     "porque": "clasificar si dos frases piden la misma imagen "
                               "es de lo que sale igual de bien en bajo, y el "
                               "analisis tiene que costar menos que rehacer"},

    # MIRAR LOS PLANOS GENERADOS Y DECIR CUAL CONTRADICE EL RELATO. Es la fase
    # que mas criterio pide de todas las que miran imagenes: no es «esta bien
    # dibujado», es «esto que se ve contradice lo que se esta contando». Hay que
    # tener el guion entero en la cabeza y ademas resistirse a senalar lo que
    # solo es mejorable, que es lo que convierte una revision util en una lista
    # de la compra de cien reparaciones.
    # REPARTIR EL REPASO EN CAMBIOS. Es la fase con mas consecuencias por
    # decision: elegir mal el ambito de una nota puede volver a grabar la voz de
    # un video entero para arreglar algo que era la musica. Y hay que saber lo
    # que cuesta cada camino, que es lo que separa «arreglalo» de «arreglalo
    # barato».
    "repaso": {"modelo": "haiku", "esfuerzo": "medium",
               "porque": "elegir el cambio mas barato que resuelve una nota "
                         "pide entender el pipeline entero: equivocarse de "
                         "ambito puede regrabar un video para bajar la musica"},
    # El corrector de UN plano: lee la nota, abre las imagenes del inventario
    # (el plano, sus vecinos, las hojas, las laminas) y decide que se adjunta y
    # con que etiqueta. Es una imagen por llamada y la equivocacion se paga en
    # imagenes: merece el modelo grande (pasos/corrector.py).
    "corrector_imagen": {"modelo": "haiku", "esfuerzo": "medium",
                         "porque": "entender una nota como «el mismo ordenador "
                                   "que en la escena anterior» exige mirar las "
                                   "imagenes y elegir bien que se adjunta; una "
                                   "eleccion mala se paga en imagenes"},

}


def _orden_modelo(nombre):
    """Cuanto sube un modelo en la escala. Los ids ya van de menos a mas."""
    return MODELOS.index(nombre) if nombre in MODELOS else len(MODELOS)


def _orden_esfuerzo(nombre):
    """Lo mismo para el esfuerzo."""
    return ESFUERZOS.index(nombre) if nombre in ESFUERZOS else 0


def por_defecto_de(fase):
    """Ajuste por defecto de la fase, sin escribirlo en los proyectos guardados.

    El valor global es un suelo: una fase puede subir el esfuerzo recomendado
    cuando su resultado afecta muchas imágenes o decisiones del vídeo.
    """
    ficha = POR_FASE.get(str(fase))
    modelo, esfuerzo = MODELO_POR_DEFECTO, ESFUERZO_POR_DEFECTO
    porque = f"el canal corre todo en {modelo} con esfuerzo {esfuerzo}"
    if ficha:
        # El ajuste global es un suelo para que el usuario pueda subir el
        # esfuerzo base sin rebajar la recomendación específica de una fase.
        modelo = max(modelo, ficha["modelo"], key=_orden_modelo)
        esfuerzo = max(esfuerzo, ficha["esfuerzo"], key=_orden_esfuerzo)
        if (modelo, esfuerzo) != (ficha["modelo"], ficha["esfuerzo"]):
            porque += (f"; para esta fase bastaria con {ficha['modelo']}/"
                       f"{ficha['esfuerzo']} ({ficha['porque']})")
        else:
            porque = ficha["porque"]
    return {"modelo": modelo, "esfuerzo": esfuerzo, "porque": porque,
            "recomendado": dict(ficha) if ficha else None}


def catalogo():
    """Lo que la interfaz necesita para pintar el selector, desde una sola fuente.

    La UI ofrecia tres esfuerzos y el backend aceptaba cinco: dos listas que se
    desincronizan solas. Esta es la lista, y la UI la pide.
    """
    return {
        "modelos": [
            {"id": "haiku", "nombre": "Codex Luna",
             "nota": "gpt-6-luna; el Estudio lo ejecuta con esfuerzo alto"},
        ],
        "esfuerzos": [
            {"id": "low", "nombre": "Bajo",
             "nota": "razonamiento ligero; usar para tareas mecánicas"},
            {"id": "medium", "nombre": "Medio",
             "nota": "buen equilibrio entre calidad y consumo del plan"},
            {"id": "high", "nombre": "Alto",
             "nota": "para decisiones que necesitan más análisis"},
            {"id": "xhigh", "nombre": "Muy alto",
             "nota": "razonamiento extendido; puede consumir más del límite del plan"},
            {"id": "max", "nombre": "Maximo",
             "nota": "todo lo que sabe dar. Puede tardar mas de diez minutos"},
        ],
        "por_defecto": {"modelo": MODELO_POR_DEFECTO,
                        "esfuerzo": ESFUERZO_POR_DEFECTO},
        "factores": {"modelo": dict(FACTOR_MODELO),
                     "esfuerzo": dict(FACTOR_ESFUERZO)},
        "aviso_factores": ("los factores son ordenes de magnitud para estimar "
                           "antes de tener historial, no medidas"),
    }


def clave_ajuste(modelo, esfuerzo):
    """Identificador estable de una combinacion, para agrupar el historico."""
    return f"{normalizar_modelo(modelo, estricto=False)}|" \
           f"{normalizar_esfuerzo(esfuerzo, estricto=False)}"


# -------------------------------------------------------------------- proceso

def localizar(para="el Estudio"):
    """Ruta del ejecutable del CLI de Codex."""
    for nombre in ("codex.exe", "codex", "codex.cmd"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    raise RuntimeError(
        f"no se encuentra Codex CLI en el PATH y {para} lo necesita "
        "(instálalo desde https://developers.openai.com/codex/cli/) ")


def entorno(cuenta=None):
    """Entorno del hijo: sin lo que le cambia el razonamiento y sin lo que lo
    sacaria del plan de ChatGPT.

    Codex usa el inicio de sesión OAuth local. Se eliminan las claves de API y
    los proveedores personalizados del proceso hijo para que una variable
    heredada no desvíe llamadas del plan hacia facturación por uso.
    """
    limpio = dict(os.environ)
    for clave in ENTORNO_FUERA + PAGO_POR_USO + (
            "ESTUDIO_SECRETOS", "ESTUDIO_API", "ANTHROPIC_MODEL"):
        limpio.pop(clave, None)
    return limpio


_CACHE_LOGIN = {"cuando": 0.0, "ok": False, "mensaje": ""}
_LOCK_LOGIN = threading.Lock()


def validar_sesion_chatgpt(ejecutable=None):
    """Impide usar una sesión de API por error: solo acepta ChatGPT OAuth.

    `codex login status` es local y no genera contenido. El estado se conserva
    cinco segundos para que las llamadas paralelas de una tanda no lancen una
    comprobación extra cada una.
    """
    ahora = time.monotonic()
    with _LOCK_LOGIN:
        if ahora - _CACHE_LOGIN["cuando"] < 5:
            if _CACHE_LOGIN["ok"]:
                return
            raise RuntimeError(_CACHE_LOGIN["mensaje"])
        exe = ejecutable or localizar()
        try:
            proceso = subprocess.run(
                [exe, "login", "status"], capture_output=True, timeout=30,
                env=entorno(), **SIN_VENTANA)
        except (OSError, subprocess.TimeoutExpired) as fallo:
            mensaje = f"no se pudo comprobar la sesión de Codex: {fallo}"
            ok = False
        else:
            salida = ((proceso.stdout or b"") + (proceso.stderr or b"")).decode(
                "utf-8", errors="replace").strip()
            baja = salida.lower()
            ok = proceso.returncode == 0 and "logged in using chatgpt" in baja
            if ok:
                mensaje = ""
            elif "api key" in baja:
                mensaje = ("Codex CLI está conectado con una clave API. Para usar "
                           "el plan, ejecuta `codex logout` y después `codex login`.")
            else:
                mensaje = salida or "Codex no tiene una sesión ChatGPT activa. Ejecuta `codex login`."
        _CACHE_LOGIN.update(cuando=ahora, ok=ok, mensaje=mensaje)
        if not ok:
            raise RuntimeError(mensaje)


#: Con que se sale del paso cuando no hay ni almacen: el login por defecto del
#: CLI, que es como iba antes de que existiera el almacen. NUNCA una lista
#: vacia: sin cuentas no habria a quien llamar, y un almacen ilegible no puede
#: dejar al Estudio mudo.
POR_DEFECTO = {"etiqueta": "Codex local", "config_dir": ""}


def cuentas():
    """Codex CLI usa una sola sesión local de ChatGPT para este equipo."""
    return [dict(POR_DEFECTO, id="codex-local", entrada=True)]


def nombre_de(cuenta, indice=None):
    """Como se llama una cuenta cuando hay que decirlo por escrito."""
    etiqueta = (cuenta or {}).get("etiqueta") or ""
    if etiqueta:
        return etiqueta
    if indice is None:
        return "la cuenta"
    return "la principal" if indice == 0 else f"la {indice + 1}.a"


def matar_arbol(proceso):
    """Mata el proceso Y sus hijos.

    En Windows algunos lanzadores del CLI crean procesos hijos: al cancelar,
    se intenta cerrar el árbol para no dejar procesos colgados.
    """
    # esto es limpieza: si taskkill no esta, no puede o falla, lo que importa
    # sigue siendo el error que provoco la limpieza, no el de matar
    #
    # EN LINUX NO HAY TASKKILL, y matar solo el node del CLI dejaba sueltos sus
    # hijos (el servidor MCP del asistente, una herramienta a medias). Alli el
    # CLI va en su propio grupo de procesos (`_una_pasada_cruda`) y se mata el
    # grupo entero.
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proceso.pid)],
                           capture_output=True, timeout=30, **SIN_VENTANA)
        else:
            os.killpg(proceso.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proceso.kill()
    except Exception:  # noqa: BLE001
        pass


def construir_orden(modelo=MODELO_POR_DEFECTO, esfuerzo=ESFUERZO_POR_DEFECTO,
                    sistema=None, herramientas_vetadas=HERRAMIENTAS_VETADAS,
                    permisos=None, extra=None, herramientas_permitidas=(),
                    salida=None):
    """Construye una ejecución local de Codex sin claves API.

    Los argumentos que antes pertenecían a Claude se traducen aquí: se conserva
    el contrato público para que los pasos no tengan que cambiar a la vez.
    """
    extras = list(extra or [])
    persistir = "--persistir" in extras
    reanudar = None
    dirs = []
    imagenes = []
    mcp = None
    i = 0
    while i < len(extras):
        actual = extras[i]
        if actual == "--resume" and i + 1 < len(extras):
            reanudar = extras[i + 1]
            i += 2
            continue
        if actual in ("--add-dir", "--mcp-config") and i + 1 < len(extras):
            if actual == "--add-dir":
                dirs.append(extras[i + 1])
            else:
                mcp = extras[i + 1]
            i += 2
            continue
        if actual in ("--image", "-i") and i + 1 < len(extras):
            imagenes.append(extras[i + 1])
            i += 2
            continue
        # Claude-only switches; Codex gets the equivalent flags below.
        if actual in ("--no-session-persistence", "--strict-mcp-config"):
            i += 1
            continue
        if actual == "--persistir":
            i += 1
            continue
        i += 1

    orden = [localizar(), "--sandbox",
             "workspace-write" if permisos == "acceptEdits" else "read-only"]
    for ruta in dict.fromkeys(str(c) for c in dirs if c and os.path.isdir(c)):
        orden += ["--add-dir", os.path.abspath(ruta)]
    orden.append("exec")
    if reanudar:
        orden += ["resume", str(reanudar)]
    orden += ["--model", _modelo_real(modelo), "--json", "--skip-git-repo-check"]
    orden += ["--config", f'model_reasoning_effort="{_esfuerzo_real(esfuerzo)}"']
    if salida:
        orden += ["--output-last-message", os.path.abspath(salida)]
    for ruta in imagenes:
        if os.path.isfile(ruta):
            orden += ["--image", os.path.abspath(ruta)]
    if not persistir and not reanudar:
        orden.append("--ephemeral")
    if mcp:
        orden.extend(_ajustes_mcp(mcp))
    return orden


def _modelo_real(modelo):
    modelo = normalizar_modelo(modelo)
    return MODELOS_CODEX.get(modelo, modelo)


def _esfuerzo_real(esfuerzo):
    return normalizar_esfuerzo(esfuerzo)


def _ajustes_mcp(ruta):
    """Traduce el fichero MCP que usaba Claude a overrides locales de Codex."""
    try:
        with open(ruta, "r", encoding="utf-8-sig") as fh:
            documento = json.load(fh)
    except (OSError, ValueError) as fallo:
        raise RuntimeError(f"no se pudo leer la configuración MCP del Estudio: {fallo}") from fallo
    servidores = documento.get("mcpServers") if isinstance(documento, dict) else None
    if not isinstance(servidores, dict):
        raise RuntimeError("la configuración MCP no contiene mcpServers")
    salida = []
    for nombre, ficha in servidores.items():
        if not isinstance(ficha, dict) or not ficha.get("command"):
            continue
        base = f"mcp_servers.{nombre}"
        for clave in ("command", "args", "env"):
            if clave in ficha:
                salida += ["--config", f"{base}.{clave}={json.dumps(ficha[clave], ensure_ascii=False)}"]
        salida += ["--config", f"{base}.enabled=true"]
    return salida


def ejecutar(instruccion, modelo=MODELO_POR_DEFECTO,
             esfuerzo=ESFUERZO_POR_DEFECTO, cwd=None, tiempo_max_s=0,
             base_tiempo_s=420, sistema=None,
             herramientas_vetadas=HERRAMIENTAS_VETADAS, permisos=None,
             extra=None, avance=None, consejos_extra=None, para="la llamada",
             herramientas_permitidas=()):
    """Lanza el CLI en headless y devuelve (texto de la respuesta, sobre JSON).

    La instruccion va SIEMPRE por stdin, nunca como argumento: un transcript de
    20 minutos pasa de 60 KB y no cabe en una linea de comandos de Windows.

    El timeout es finito y mata el arbol de procesos: nunca se deja un paso en
    'ejecutando' para siempre.
    """
    modelo = normalizar_modelo(modelo)
    esfuerzo = normalizar_esfuerzo(esfuerzo)
    lista = cuentas()
    for indice, cuenta in enumerate(lista):
        try:
            return _una_pasada(
                instruccion, modelo, esfuerzo, cwd, tiempo_max_s, base_tiempo_s,
                sistema, herramientas_vetadas, permisos, extra, avance,
                consejos_extra, para, cuenta, herramientas_permitidas)
        except TiempoAgotado:
            # A proposito NO se baja a la siguiente cuenta con un timeout: el
            # techo ya escala con el esfuerzo y volver a intentarlo es volver a
            # esperar media hora para volver a vencer. La clase lo lleva escrito.
            raise
        except RuntimeError as fallo:
            # EL ULTIMO FALLO SUBE TAL CUAL, con su tipo y su texto. No se
            # envuelve en un "las N cuentas fallaron" por dos motivos medidos:
            # `LimiteAgotado` deja de reconocerse como tal (y con el, el aviso
            # en cristiano de que el cupo se renueva el dia X), y la valvula
            # CUPO de las suites casa por TEXTO -- envolverlo pinta FALLA donde
            # tenia que pintar CUPO.
            queda = indice + 1 < len(lista)
            cancelado = avance is not None and getattr(avance, "cancelado", False)
            if not queda or cancelado:
                raise
            print(f"[cli] {nombre_de(cuenta, indice)} fallo "
                  f"({str(fallo)[:120]}); se sigue con "
                  f"{nombre_de(lista[indice + 1], indice + 1)}", flush=True)
    # inalcanzable: `cuentas()` nunca devuelve una lista vacia. Escrito de todas
    # formas porque "inalcanzable" envejece mal.
    raise RuntimeError("no se pudo completar la llamada con la sesión local de Codex")


def _anotar_salud(cuenta, fallo=None, para=""):
    """Apunta en salud_cli como respondio la cuenta. Nunca lanza: es un registro."""
    try:
        try:
            from . import salud_cli                       # noqa: PLC0415
        except ImportError:                               # pasos/ suelto en sys.path
            import salud_cli                              # noqa: PLC0415
        if fallo is None:
            salud_cli.anotar(cuenta, "ok", "", para)
        else:
            estado, mensaje = salud_cli.clasificar(fallo)
            salud_cli.anotar(cuenta, estado, mensaje, para)
    except Exception:                                     # noqa: BLE001
        pass


def _una_pasada(instruccion, modelo, esfuerzo, cwd, tiempo_max_s, base_tiempo_s,
                sistema, herramientas_vetadas, permisos, extra, avance,
                consejos_extra, para, cuenta, herramientas_permitidas=()):
    """UNA llamada al CLI con UNA cuenta, apuntando como respondio.

    Es el UNICO sitio por el que pasa toda llamada al CLI, y por eso la salud de
    la cuenta (pasos/salud_cli.py) se apunta aqui y no en cada paso: un cupo
    agotado a mitad del guion se ve en la burbuja al momento. Una cancelacion
    no se apunta: no dice nada de la cuenta.
    """
    try:
        resultado = _una_pasada_cruda(
            instruccion, modelo, esfuerzo, cwd, tiempo_max_s, base_tiempo_s,
            sistema, herramientas_vetadas, permisos, extra, avance,
            consejos_extra, para, cuenta, herramientas_permitidas)
    except Exception as fallo:
        if not (avance is not None and getattr(avance, "cancelado", False)):
            _anotar_salud(cuenta, fallo, para)
        raise
    _anotar_salud(cuenta, None, para)
    return resultado


def _una_pasada_cruda(instruccion, modelo, esfuerzo, cwd, tiempo_max_s, base_tiempo_s,
                      sistema, herramientas_vetadas, permisos, extra, avance,
                      consejos_extra, para, cuenta, herramientas_permitidas=()):
    """El cuerpo de la llamada, sin el registro de salud."""
    import json
    import tempfile

    validar_sesion_chatgpt()

    techo = tiempo_max(esfuerzo, base_s=base_tiempo_s, modelo=modelo,
                       pedido_s=tiempo_max_s)
    fd, salida_modelo = tempfile.mkstemp(prefix="codex_respuesta_", suffix=".txt")
    os.close(fd)
    orden = construir_orden(
        modelo, esfuerzo, sistema=sistema,
        herramientas_vetadas=herramientas_vetadas, permisos=permisos,
        extra=extra, herramientas_permitidas=herramientas_permitidas,
        salida=salida_modelo)
    prompt = (f"INSTRUCCIONES DEL SISTEMA:\n{sistema}\n\nSOLICITUD:\n{instruccion}"
              if sistema else (instruccion or ""))
    try:
        # en Linux, en su propio grupo de procesos: es lo que deja a
        # `matar_arbol` cerrar tambien sus hijos
        grupo = {} if os.name == "nt" else {"start_new_session": True}
        proceso = subprocess.Popen(orden, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   cwd=cwd, env=entorno(cuenta), **SIN_VENTANA,
                                   **grupo)
    except OSError as fallo:
        try:
            os.remove(salida_modelo)
        except OSError:
            pass
        raise RuntimeError(
            f"no se ha podido lanzar Codex CLI: {fallo}") from fallo

    if avance is not None:
        avance.al_cancelar = lambda: matar_arbol(proceso)

    arranque = time.time()
    try:
        crudo, error_crudo = proceso.communicate(prompt.encode("utf-8"), timeout=techo)
    except subprocess.TimeoutExpired as fallo:
        matar_arbol(proceso)
        try:
            os.remove(salida_modelo)
        except OSError:
            pass
        consejos = list(consejos_extra or [])
        if esfuerzo != "low":
            consejos.insert(0, f"baja 'esfuerzo' de '{esfuerzo}' a 'low': es lo "
                               f"que mas alarga la espera")
        consejos.append("comprueba que 'codex exec' contesta desde una consola")
        consejos.append(f"sube 'tiempo_max_s' por encima de {techo} si el trabajo "
                        f"es largo de verdad")
        raise TiempoAgotado(
            f"Codex CLI no ha respondido en {techo} s y se ha cortado "
            f"{para} (se ha matado el proceso y sus hijos). Que probar: "
            + "; ".join(consejos) + ".") from fallo

    segundos = time.time() - arranque
    if avance is not None and getattr(avance, "cancelado", False):
        try:
            os.remove(salida_modelo)
        except OSError:
            pass
        raise RuntimeError(f"{para} se canceló mientras esperaba a Codex CLI")

    salida = crudo.decode("utf-8", errors="replace")
    error = error_crudo.decode("utf-8", errors="replace").strip()
    sobre = {"usage": {"input_tokens": 0, "output_tokens": 0},
             "session_id": "", "model": _modelo_real(modelo)}
    mensajes_error = []
    for linea in salida.splitlines():
        try:
            evento = json.loads(linea)
        except ValueError:
            continue
        tipo = evento.get("type")
        if tipo == "thread.started":
            sobre["session_id"] = str(evento.get("thread_id") or "")
        elif tipo == "turn.completed":
            uso = evento.get("usage") or evento.get("token_usage") or {}
            if isinstance(uso, dict):
                sobre["usage"].update({
                    "input_tokens": int(uso.get("input_tokens") or 0),
                    "output_tokens": int(uso.get("output_tokens") or 0),
                    "cache_read_input_tokens": int(uso.get("cached_input_tokens") or 0),
                })
        elif tipo in ("error", "turn.failed"):
            mensajes_error.append(str(evento.get("message") or evento.get("error") or ""))
    try:
        with open(salida_modelo, "r", encoding="utf-8-sig") as fh:
            texto = fh.read().strip()
    except OSError:
        texto = ""
    finally:
        try:
            os.remove(salida_modelo)
        except OSError:
            pass

    if proceso.returncode != 0:
        motivo = " ".join(mensajes_error).strip() or error or salida.strip()
        limite = limite_de(motivo)
        if limite:
            raise LimiteAgotado(
                f"se ha agotado el límite del plan de ChatGPT y no se puede "
                f"hacer {para}: {limite}")
        if any(p in motivo.lower() for p in ("usage limit", "rate limit", "limit reached")):
            raise LimiteAgotado(f"Codex alcanzó un límite de uso del plan: {motivo[:500]}")
        raise RuntimeError(
            f"Codex CLI falló (código {proceso.returncode}) después de "
            f"{int(segundos)} s: {motivo[:600] or 'sin mensaje'}")
    if not texto:
        raise RuntimeError(
            "Codex CLI no devolvió respuesta final: "
            f"{(error or salida)[:600] or '(salida vacía)'}")

    # El sobre del CLI no dice con que esfuerzo se le hablo, y el esfuerzo es la
    # variable que mas mueve el tiempo: si no se estampa aqui, el medidor de
    # coste y el historico se quedan ciegos justo donde importa.
    sobre["_ajuste"] = {"modelo": modelo, "esfuerzo": esfuerzo,
                        "segundos": round(segundos, 2), "tiempo_max_s": techo,
                        "cuenta": "sesión local de Codex"}
    return texto, sobre


# =========================================================================
# PEDIR UNA RESPUESTA LARGA: QUE LA ESCRIBA, NO QUE LA DEVUELVA
#
# El limite que obligaba a trocear las listas largas en tandas (`POR_TANDA`) es
# el largo del MENSAJE, no el del contexto ni el del modelo. Medido el
# 22-08-2026 con el mismo encargo por los dos caminos, en sonnet/medium:
#
#     120 items de 60 palabras   en la RESPUESTA  120/120  16.941 car.   88 s
#                                a FICHERO        120/120  14.738 car.   78 s
#     300 items de 60 palabras   en la RESPUESTA   NO PARSEA   790 car.  623 s
#                                a FICHERO        300/300  95.370 car.  578 s
#
# A 300 la respuesta vuelve rota despues de diez minutos y el fichero sale
# entero en menos tiempo. Es lo mismo que hace un agente cuando escribe miles de
# lineas de codigo: no las mete en su mensaje final, las escribe con Write en
# varias pasadas y contesta «hecho».
#
# LO QUE OBLIGA A CUIDAR, porque esto le da la herramienta Write a un agente:
#   - corre en una carpeta TEMPORAL de un solo uso, creada aqui y borrada aqui.
#     Nunca el proyecto ni el repo, que es donde corren las demas fases;
#   - `Bash` se veta siempre, pase lo que pase: no hace falta para escribir un
#     JSON y es la herramienta con la que se sale de una carpeta;
#   - si el fichero no aparece, el error dice QUE ficheros si dejo. Sin eso,
#     «no escribio nada» no distingue entre equivocarse de nombre, de carpeta o
#     no llegar a escribir, y son tres arreglos distintos.
#
#

#: Como se llama el fichero que se le pide. Uno solo para todas las fases: si
#: cada una eligiera el suyo, el dia que una falle habria que ir a mirar cual
#: era el nombre en esa.
FICHERO_SALIDA = "salida.json"

CIERRE = """

=====================================================================
COMO ENTREGARLO
=====================================================================

GUARDA EL RESULTADO EN EL FICHERO `{fichero}` de este directorio, con la forma
exacta que se pide arriba. Escribelo en varias pasadas si te resulta mas comodo
(crea el archivo y añade bloques si hace falta); lo unico que importa es que al
terminar sea un JSON valido y completo.

Se escribe a fichero y NO se devuelve en el mensaje a proposito: una lista larga
en la respuesta se corta a la mitad y se pierde entera.

Cuando este escrito, responde solo con: LISTO
"""


def escribiendo(instruccion, modelo=MODELO_POR_DEFECTO,
                esfuerzo=ESFUERZO_POR_DEFECTO, tiempo_max_s=0,
                base_tiempo_s=600, herramientas_vetadas=(), avance=None,
                para="la llamada", que="la respuesta", carpetas=(),
                herramientas_permitidas=()):
    """Como `ejecutar`, pero el agente ESCRIBE su JSON y aqui se lee. -> dict.

    La instruccion NO tiene que decir como entregarlo: se le anade el cierre
    (ver CIERRE), asi que todas las fases piden la entrega igual y el dia que
    esto cambie, cambia en un sitio.

    `herramientas_vetadas` se respeta y se le suma `Bash` siempre.

    `carpetas` se añaden al espacio autorizado de Codex (--add-dir). Con
    `workspace-write`, el agente también puede modificarlas; solo se pasan las
    rutas que esta fase necesita y que existen.
    """
    import json                                                # noqa: PLC0415
    import shutil                                              # noqa: PLC0415
    import tempfile                                            # noqa: PLC0415

    vetadas = tuple(dict.fromkeys(tuple(herramientas_vetadas or ()) + ("Bash",)))
    for imprescindible in ("Write", "Edit", "Read"):
        if imprescindible in vetadas:
            raise ValueError(
                f"'{imprescindible}' no se puede vetar aqui: es como el agente "
                f"entrega la respuesta (ver cli_codex.escribiendo)")
    extra = ["--no-session-persistence"]
    for ruta in dict.fromkeys(str(c) for c in (carpetas or ()) if c):
        if os.path.isdir(ruta):
            extra += ["--add-dir", os.path.abspath(ruta)]
    carpeta = tempfile.mkdtemp(prefix="cli_salida_")
    try:
        ejecutar(instruccion + CIERRE.format(fichero=FICHERO_SALIDA),
                 modelo=modelo, esfuerzo=esfuerzo, cwd=carpeta,
                 tiempo_max_s=tiempo_max_s, base_tiempo_s=base_tiempo_s,
                 herramientas_vetadas=vetadas, permisos="acceptEdits",
                 extra=extra, avance=avance, para=para,
                 herramientas_permitidas=herramientas_permitidas)
        ruta = os.path.join(carpeta, FICHERO_SALIDA)
        if not os.path.exists(ruta):
            hay = sorted(os.listdir(carpeta))[:8]
            raise RuntimeError(
                f"el agente de {para} no ha escrito {FICHERO_SALIDA}"
                + (f" (dejo: {', '.join(hay)})" if hay else " (no dejo nada)"))
        with open(ruta, "r", encoding="utf-8-sig") as fh:
            crudo = fh.read()
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
    try:
        return json.loads(crudo)
    except ValueError:
        # con vallas de markdown o una frase delante: se rescata igual que se
        # rescata la respuesta de cualquier otro agente
        try:
            from . import comun                                # noqa: PLC0415
        except ImportError:                          # pasos/ suelto en sys.path
            import comun                                       # noqa: PLC0415
        return comun.extraer_json(crudo, que)
