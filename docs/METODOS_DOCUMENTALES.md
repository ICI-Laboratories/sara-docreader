# Métodos de SARA DocReader

Investigación y decisiones: 21 de septiembre de 2026. Revisión funcional: 9 de octubre de 2026.

## Flujo interactivo y procesamiento

El original se guarda antes de responder `202`. El registro y el trabajo se insertan en una transacción. El gateway transmite las nuevas cargas al catálogo sin acumular todo el PDF en RAM. La extracción se ejecuta en un proceso separado con límite de tiempo; cada worker reclama un trabajo con `FOR UPDATE SKIP LOCKED`, renueva un lease y sólo publica resultados si conserva su token. Los trabajos sobreviven a reinicios y permiten tres intentos automáticos y reintento manual.

PostgreSQL documenta `SKIP LOCKED` como útil para consumidores de una tabla tipo cola. Su vista inconsistente no debe trasladarse a consultas normales del catálogo. Se eligió una cola pequeña integrada para el servidor del laboratorio; si las necesidades incluyen prioridades complejas o cadenas distribuidas, evaluar una cola especializada antes de ampliar este mecanismo.

Fuente: [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html).

## OCR por página

Se extrae texto nativo ordenado; páginas con imágenes o sin texto pasan por Tesseract a través de PyMuPDF. El OCR parcial conserva texto nativo cuando lo hay. Se guardan números de página en todos los fragmentos, incluidos los que llegan de páginas mixtas. Tesseract español e inglés se instala en la imagen del worker.

Es una primera heurística conservadora: una ilustración puede activar OCR aunque no lo necesite. Tampoco reconstruye perfectamente tablas o fórmulas. Docling sigue siendo candidato para una segunda ruta de alta fidelidad, seleccionada por necesidad del documento; no se incluye en el camino rápido ni se afirma equivalencia de calidad sin evaluación.

Fuentes: [PyMuPDF OCR](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html), [opciones Docling](https://docling-project.github.io/docling/reference/pipeline_options/).

## Fragmentación y recuperación

La primera implementación divide texto dentro de cada página, con un máximo de 1800 caracteres y solapamiento de 180. Conserva referencia de página y evita fragmentos sin procedencia. Es un punto de partida medible, no fragmentación semántica ni conteo exacto de tokens. Antes de producción intensiva, comparar contra segmentación por encabezados y presupuesto del tokenizer.

Además se conserva el texto completo de cada página en `document_pages`, con índice GIN de trigramas. `GET /api/search?exact=true&q=...&limit=20&offset=0` busca una palabra, identificador o frase literal incluso si atraviesa límites de fragmentos. Ignora mayúsculas y normaliza espacios/saltos de línea; conserva acentos, puntuación y símbolos. `%`, `_` y barras invertidas son caracteres literales. Devuelve páginas coincidentes, `total`, `has_more` y `next_offset`; el total cuenta páginas, no todas las apariciones de la palabra. La consulta acepta hasta 500 caracteres y no une frases entre páginas. Todos los resultados y sus conteos se filtran por propietario.

La migración reconstruye las páginas de bibliotecas existentes a partir de sus fragmentos, eliminando el solapamiento de hasta 180 caracteres. Es idempotente y no vuelve a ejecutar OCR ni cambia originales. En cargas nuevas se conserva el texto de página antes de fragmentarlo; los trabajos de síntesis reutilizan también ese texto completo.

El índice `tsvector` usa configuración `simple`, útil para conservar términos técnicos de ambos idiomas; no aplica stemming español/inglés ni BM25. Un índice GIN persiste la estructura invertida. La búsqueda por nombre usa trigramas para consultas parciales y paginación por fecha/UUID.

La ruta opcional de significado consulta `/v1/embeddings` del gateway autenticado, valida 1024 dimensiones y valores finitos, y compara sólo fragmentos indexados con el mismo identificador de modelo, revisión y perfil de consulta. Evitar etiquetas mutables del modelo en producción y reprocesar al cambiar sus pesos. No reutilizar NPZ anteriores: la aplicación anterior generaba con `bge-m3` y consultaba con `bge-large-en-v1.5`. La generación tiene un presupuesto total de 180 segundos por trabajo (`SARA_EMBEDDING_BUDGET_SECONDS`): si falla o se agota, se publican todas las páginas y fragmentos de texto, con error de embeddings explícito.

Los 60 candidatos de cada modalidad se fusionan mediante `1/(60+posición)` (RRF); se devuelven hasta 20 fragmentos. Se inicia con búsqueda vectorial exacta, filtrada por propietario. HNSW puede reducir latencia en catálogos grandes, pero se debe evaluar recall y selectividad de permisos antes de introducirlo. Si el servicio de embeddings falla, se conserva la búsqueda textual y la interfaz informa la degradación.

Cuando la consulta textual completa no encuentra coincidencias, se buscan sus palabras significativas con OR, omitiendo palabras frecuentes de preguntas en español e inglés. Esto permite recuperar «microelectrotermografía» aunque se pregunte «¿Qué dice sobre microelectrotermografía?» y el servicio de embeddings esté caído. No sustituye un analizador semántico ni corrige errores de OCR.

Fuentes: [índices GIN](https://www.postgresql.org/docs/17/textsearch-indexes.html), [pgvector y búsqueda híbrida](https://github.com/pgvector/pgvector), [Ollama embed](https://docs.ollama.com/api/embed).

## Consultas y lectura

El chat recupera candidatos de los documentos seleccionados (máximo 20) y acepta hasta 12 mensajes previos, con roles exclusivamente `user` y `assistant`. Los seguimientos reconocibles incorporan el tema de la pregunta anterior a la recuperación. El historial mantiene el hilo, pero no se considera evidencia. Las fuentes incluyen nombre de documento, UUID, página, texto efectivamente enviado y enlace al PDF; las citas `[Fuente N]` se numeran de nuevo en cada turno.

Para citas numéricas explícitas como `[23]`, se buscan entradas de bibliografía que empiezan con `[23]`, `23.` o `23)` dentro del mismo documento autorizado. Se admiten dos saltos y ocho fuentes adicionales, con un máximo de 24 búsquedas. Las entradas vecinas de la bibliografía no se incorporan a la cadena. Es una heurística comprobable de referencias numéricas: no resuelve automáticamente estilos autor/año, notas ambiguas ni obras externas no cargadas. Una referencia bibliográfica no demuestra que se haya leído la obra citada.

El modelo desplegado admite 8192 tokens. Pregunta, historial y evidencia comparten un presupuesto estimado de 5100; el historial puede ocupar hasta 1200. Se reservan 1800 tokens para la salida y margen para instrucciones/formato. El estimador cobra un token por cada dos bytes de prosa latina, y por byte de puntuación o escritura no latina; no es el tokenizer exacto del modelo. Se priorizan la fuente principal y su cadena de referencias. El modelo recibe fragmentos acotados, no libros enteros en una petición. El stream SSE sólo comunica `done` tras finalización real: un corte, salida vacía, límite de generación o número de fuente inexistente termina con error. La correspondencia semántica entre cada afirmación y la evidencia aún requiere evaluación de fidelidad.

PDF.js se carga bajo demanda y dibuja sólo una página a la vez. El endpoint de archivo soporta Range y autorización por UUID de usuario. SvelteKit mantiene los recursos del visor separados de la carga inicial. Las fuentes Inter/Manrope están alojadas en el mismo despliegue, sin solicitudes a Google Fonts.

Fuentes: [PDF.js](https://mozilla.github.io/pdf.js/examples/), [rendimiento SvelteKit](https://svelte.dev/docs/kit/performance), [SvelteKit adapter-node](https://svelte.dev/docs/kit/adapter-node).

La síntesis y clasificación son trabajos bajo demanda: reutilizan texto/vectores ya indexados y muestrean fragmentos repartidos desde el principio hasta el final. Declaran que la muestra no es una lectura exhaustiva y cuántos fragmentos entraron en el contexto (3500 tokens estimados de entrada y 1400 de salida). Se persisten resultado y categoría sugerida; un fallo deja un error explícito. La matriz de similitud compara vectores promedio del mismo modelo, filtrados por propietario. No mide certeza ni demuestra relación científica.

Los límites predeterminados son 100 MiB y 1500 páginas por PDF, y 900 segundos por trabajo. Un libro nativo de 500 páginas no debe truncarse a las primeras páginas; un escaneo de 500 páginas puede superar el tiempo disponible de OCR y debe comunicar el fallo. El catálogo se pagina y no tiene un límite artificial de cantidad de documentos, pero disco, índices y concurrencia son finitos.

## Regresiones reproducibles

La suite `tests/test_library.py` usa una base PostgreSQL/pgvector desechable cuyo nombre termina en `_test`. Incluye un PDF real de 500 páginas con texto breve: palabra única en página 487, referencia `[23]` en 233, segunda referencia `[71]` en 119, paginación de las 500 páginas y aislamiento entre propietarios. También verifica una frase de 439 caracteres que no cabe completa en ningún fragmento, símbolos literales y reconstrucción idempotente del índice anterior. Este PDF breve verifica integridad y recuperación; no representa por sí solo la carga de un libro denso o un escaneo.

`tests/test_document_context.py` cubre seguimientos, diversidad entre documentos, presupuestos con Unicode, muestreo hasta la última página, caída de embeddings sin pérdida del texto y streams incompletos. Ejecución: `SARA_TEST_DATABASE_URL=... python -m unittest discover -s tests -p 'test*.py'`. No apuntar las pruebas a la base de producción.

## Evaluación pendiente con documentos del laboratorio

Construir un conjunto revisado de preguntas y páginas relevantes: español/inglés, palabras exactas, términos técnicos, tablas, PDF escaneado y mixto. Medir recall@10, MRR, fidelidad de citas, tasa de OCR fallido, tiempo a primera página, tiempo a primer token, p50/p95 de búsqueda y memoria con 10 usuarios concurrentes.

Comparar: texto GIN; texto + vectores/RRF; RRF + reranker; extracción directa/OCR contra Docling. Mantener la opción más sencilla que cumpla calidad y latencia. No se ha demostrado aún mejora porcentual frente al sistema anterior ni rendimiento a 10.000 documentos.
