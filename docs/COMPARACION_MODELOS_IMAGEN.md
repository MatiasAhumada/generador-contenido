# Comparar modelos de imagen con OpenRouter

Esta herramienta permite comparar varios modelos con el mismo prompt, formato
vertical 2:3 (el tamaño vertical del motor actual) y hasta 14 referencias. Usa
la API de OpenRouter sin cambiar la configuración normal de producción. Pide
la clave de forma oculta en la consola y no la guarda.

Desde PowerShell, abre la carpeta del proyecto y ejecuta:

```powershell
python herramientas/comparar_imagenes_openrouter.py `
  --prompt-file escena.txt `
  --referencia estilo.png `
  --referencia personaje.png
```

Por defecto genera una imagen para cada modelo en `comparacion-imagenes/` y
guarda un `informe.json` con el importe devuelto por OpenRouter y el tiempo de
cada llamada. Revisa las tres imágenes con el mismo criterio: fidelidad a las
referencias, coherencia con el estilo, composición vertical, detalles y cuánto
trabajo de corrección haría falta. El menor precio por imagen solo conviene si
la imagen sirve para el video sin regenerarla.

Modelos iniciales:

| Modelo | Precio publicado | Papel en la comparación |
|---|---:|---|
| Seedream 5.0 Flash | US$0,018 / imagen | Candidato económico; acepta hasta 14 referencias |
| Seedream 4.5 | US$0,04 / imagen | Candidato de calidad; acepta hasta 14 referencias |
| OpenAI GPT Image 2 por OpenRouter | Variable por tokens | Control opcional, se agrega con `--modelo openai/gpt-image-2` |

Los precios pueden cambiar y dependen del endpoint elegido. El informe usa
`usage.cost` de la respuesta para anotar el cobro real. Para comparar con OpenAI
API directa hay que usar la misma imagen, modelo, resolución y referencias, y
contrastar el uso/costo registrado en cada plataforma. La tarifa del modelo
OpenAI a través de OpenRouter no representa un descuento inherente frente al
uso directo de ese mismo modelo.

La tabla local del proyecto conserva tarifas antiguas. En OpenAI API, la tarifa
publicada actualmente para GPT Image 2 es US$4/M de tokens de imagen de entrada,
US$2,50/M de texto de entrada y US$15/M de salida; en OpenRouter aparece a
US$8/M y US$30/M. Para la tanda histórica de 174 imágenes, recalcular con el
consumo documentado de 1,17 M tokens de entrada y 34,7 mil de salida da un rango
aproximado de US$3,45–5,20 en OpenAI API directa, según cuánto de la entrada haya
sido texto o imágenes. La tabla anterior estimaba US$10,40 con las tarifas viejas.

Con precio fijo, 174 imágenes serían US$3,13 con Seedream 5.0 Flash y US$6,96
con Seedream 4.5. Por eso empezaría comparando solo los dos Seedream; agrega
GPT Image 2 por OpenRouter como control opcional, ya que su precio publicado es
más alto que llamar directamente a OpenAI. La calidad visual, los reintentos y
el costo informado por cada respuesta determinan el costo final. Los créditos
que ya están cargados en OpenRouter no necesitan sumar ahora una comisión de
recarga.

Se pueden limitar modelos con `--modelo` repetido, agregar el control opcional
`--modelo openai/gpt-image-2` y cambiar la salida con `--salida`. La prueba
predeterminada genera dos imágenes y tiene un costo fijo publicado de US$0,058,
antes de posibles cambios en las tarifas del proveedor.
