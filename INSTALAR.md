# Instalar AS Video Studio en tu servidor

Esto deja el estudio funcionando en un VPS, con su dirección web, su candado y su
contraseña. **Son tres pasos y unos cinco minutos**, y no hace falta saber nada
de servidores: se pega una línea y se espera.

---

## Antes de empezar: lo que necesitas tener

| Qué | Para qué | Cuánto cuesta |
|---|---|---|
| **Un VPS con Ubuntu 24.04** | la máquina donde vive el estudio | desde ~7 €/mes |
| **Codex CLI con tu cuenta de ChatGPT** | escribe el guion, analiza imágenes y alimenta el asistente | uso incluido de Codex, sujeto a sus límites |
| **Una clave de OpenAI** | dibuja las imágenes | se paga por imagen (abajo) |
| **Una clave de Cartesia** | pone la voz | plan gratuito para empezar |
| Jamendo y FreeSound *(opcional)* | música y efectos | gratis |

**Del VPS, lo que importa**: 8 GB de memoria y 4 núcleos como mínimo. Con 8
núcleos el montaje del vídeo va casi el doble de rápido (medido: 7 minutos
frente a 11 para un vídeo de dos minutos y medio). Y espacio: un vídeo largo con
todas sus versiones puede ocupar decenas de gigas.

**El coste de generación depende** de la duración, el número de planos, las
referencias que acompaña cada imagen y la calidad elegida. El estudio estima el
total antes de empezar y permite elegir la calidad en Configuración.

> **Tiene que ser Ubuntu 24.04.** Con 22.04 la instalación no puede terminar
> (su Python es demasiado viejo), y el instalador te lo dice nada más empezar.
> Si tu VPS tiene otro sistema, en el panel se reinstala eligiendo Ubuntu 24.04.

---

## Los tres pasos

### 1. Abre la consola de tu servidor

En Hostinger: entra en tu VPS y pulsa **«Consola web»**. Se abre una terminal
negra dentro del navegador; no instala nada en tu ordenador.

**Mira lo que pone en la última línea:**

- Si ya dice algo como **`root@srv1234567:~#`**, ya estás dentro. **No escribas
  nada más**: salta al paso 2. *(La consola suele entrar sola como root. Si
  escribes `root` ahí te dirá «Command 'root' not found», que no es un fallo:
  es que le has pedido un programa llamado «root».)*
- Si dice **`login:`**, escribe `root` y pulsa Intro; y en `Password:`, la
  contraseña de root. *(No se ve nada mientras la escribes: es normal.)*

¿No tienes esa contraseña? En esa misma página del panel está **«¿Olvidó la
contraseña root? Restablece tu contraseña»**. Tarda un minuto.

### 2. Pega esta línea y pulsa Intro

```bash
curl -fsSL https://raw.githubusercontent.com/NeverBlink/as-video-studio/main/instalar.sh -o instalar.sh && bash instalar.sh
```

Ahora espera. Irá contando lo que hace: el vídeo, las fuentes, el navegador, el
candado. **Si algo va mal te lo dirá en castellano y te dirá qué hacer**; no se
queda callado.

### 3. Elige tu contraseña

Al final te preguntará qué contraseña quieres para entrar en tu estudio (mínimo
12 caracteres). Escríbela y pulsa Intro — **no se ve mientras la escribes**.

Y ya está. Te dará la dirección de tu estudio, que es el nombre de tu servidor:

```
https://srv1234567.hstgr.cloud
```

Ábrela en el navegador, mete tu contraseña y estás dentro.

---

## Lo que viene después

La primera vez que entras sale una **guía de inicio** que te pide las cosas de
una en una y con el enlace de dónde se consigue cada una:

1. **Conectar Codex.** En el servidor ejecuta `asvs codex-login` y completa el
   acceso de ChatGPT con la verificación del dispositivo que indica Codex.
2. **La clave de OpenAI**, para las imágenes.
3. **La clave de Cartesia**, para la voz.
4. Jamendo y FreeSound si quieres música y efectos.

Las claves se guardan desde la pantalla. Si te saltas la guía, vuelve a salir
desde **Configuración** (el engranaje de arriba a la derecha). Codex se conecta
desde la terminal del servidor con `asvs codex-login`.

Codex usa el cupo de tu plan de ChatGPT. Las imágenes llaman a OpenAI API y la
voz a Cartesia: son servicios separados y sus costes no están cubiertos por la
suscripción de Codex.

---

## Si algo se tuerce

### La consola web de Hostinger no me deja pegar

Pasa. Entonces hazlo desde tu propio ordenador, que tampoco hay que instalar
nada:

- **Windows**: abre *PowerShell* (botón de inicio, escribe «powershell»)
- **Mac**: abre *Terminal*

y escribe esto, con la IP de tu servidor (la ves en el panel):

```bash
ssh root@LA.IP.DE.TU.VPS
```

La primera vez pregunta si te fías: escribe `yes`. Después te pide la contraseña
de root. Y ya puedes pegar la línea del paso 2.

### ¿Cómo sé si está bien?

En la consola del servidor:

```bash
asvs estado
```

Te dice, en una pantalla, qué está en marcha y qué no. **Si pides ayuda, manda
eso.**

### Se me ha olvidado la contraseña del estudio

Entra en la consola del servidor como root y escribe:

```bash
estudio-clave
```

Te la enseña. Y para cambiarla, `estudio-clave --nueva`.

Esto funciona porque la copia vive en el servidor, en una carpeta que sólo root
puede leer — y a root sólo se llega desde tu panel. **Quien tiene el panel tiene
la llave, y nadie más.**

### Sale «no seguro» en el navegador

El candado sólo se puede sacar si el nombre del servidor apunta a él. Si el
instalador no pudo, te lo dijo. Cuando esté, en la consola:

```bash
asvs https
```

### Quiero la última versión

```bash
asvs actualizar
```

Trae el código nuevo y reinicia. **No toca tus vídeos, ni tus estilos, ni tus
claves**: viven aparte del programa, precisamente para esto.

---

## Todos los comandos, por si acaso

| Comando | Qué hace |
|---|---|
| `asvs estado` | cómo está todo |
| `asvs registro` | lo último que ha dicho el estudio (para buscar un fallo) |
| `asvs reiniciar` | apagar y encender |
| `asvs actualizar` | traer la última versión |
| `estudio-clave` | ver la contraseña de acceso |
| `estudio-clave --nueva` | cambiarla |
| `asvs https` | (re)intentar el candado |

---

## Una cosa que conviene saber

**El estudio no sabe lo que es un usuario, a propósito.** Quien decide si entras
es el acceso que el instalador pone delante. Eso quiere decir dos cosas:

- **No dejes el estudio abierto a internet sin ese acceso.** Cualquiera que
  supiera la dirección generaría imágenes con tus claves, o sea con tu dinero.
  El instalador lo monta todo junto para que no pueda pasar.
- **Es tu servidor y son tus claves.** Aquí no hay ninguna cuenta intermedia:
  los vídeos, los estilos y el gasto son tuyos y están en tu máquina.
