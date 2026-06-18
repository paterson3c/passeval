# Passeval

Herramienta para evaluar la fortaleza de una contraseña combinando un modelo propio de puntuación, métricas de entropía, estimación de tiempos de cracking en cinco escenarios y un cotejo opcional contra la base pública Have I Been Pwned (HIBP).

La contraseña nunca se almacena ni se loguea. El cotejo HIBP usa *k-anonymity*: solo se envían los 5 primeros caracteres del hash SHA-1, nunca la contraseña completa.

Se ofrece en dos formas de uso:

- **Línea de comandos** (`passeval evaluate`) — uso local en terminal.
- **Interfaz web** — accesible por HTTPS, pensada para una red privada con VPN.

Este documento está dividido en dos partes:

- [**Parte I — Manual de usuario**](#parte-i--manual-de-usuario): para quien quiere instalar, configurar y usar la herramienta.
- [**Parte II — Guía del desarrollador**](#parte-ii--guía-del-desarrollador): para quien quiere entender el código, ejecutar la batería de tests o extenderlo.

---

# Parte I — Manual de usuario

## 1. Requisitos

- Python ≥ 3.12
- PostgreSQL ≥ 14 (probado en 16.13) — solo si se quiere usar la recogida opt-in de estadísticas anónimas o la ingesta de un dataset propio.
- Acceso a internet — solo si se autoriza el cotejo HIBP.

## 2. Instalación

```bash
cd passeval
python3 -m venv venv
source venv/bin/activate
pip install -e .            # paquete principal
pip install -e '.[dev]'     # opcional: añade zxcvbn y pytest (recomendado)
```

Configurar el entorno:

```bash
cp .env.example .env
$EDITOR .env
```

Rellenar al menos `PGPASSWORD`, `DB_HOST`, `DB_NAME`, `DB_USER`, `DATASET_PATH`, `DICTIONARIES_PATH` y `LOG_DIR`. La plantilla `.env.example` documenta cada variable.

Inicializar el esquema de la base de datos (solo si se va a usar PostgreSQL):

```bash
psql -U passeval_user -d passeval_db -f sql/01_schema_stats.sql
psql -U passeval_user -d passeval_db -f sql/02_schema_public.sql
psql -U passeval_user -d passeval_db -f sql/03_grants.sql
```

## 3. Uso de la línea de comandos

```bash
passeval evaluate
```

Tras un consentimiento inicial sobre HIBP y estadísticas anónimas, la herramienta pide la contraseña con `getpass` (sin eco en pantalla) e imprime:

- Longitud, composición de caracteres y entropías (Shannon y charset).
- Descomposición de la contraseña en patrones reconocidos (palabras de diccionario, fechas, secuencias de teclado, leet, repeticiones, etc.) y los segmentos sin patrón aparente.
- Puntuación 0–4 con etiqueta (Trivial / Muy débil / Débil / Fuerte / Muy fuerte) y bits estimados.
- Resultado del cotejo HIBP si se autorizó.
- Tiempos de cracking estimados en cinco escenarios: atacante online con *rate limiting*, CPU doméstica con SHA-1, GPU de consumo, granja multi-GPU y KDF moderna (Argon2id).
- Veredicto de riesgo agregado con recomendación.

Tras evaluar una contraseña, la herramienta pregunta si se desea evaluar otra. Se sale con `Ctrl+D` o respondiendo "no".

## 4. Uso de la interfaz web

### Generar el certificado HTTPS (una sola vez)

```bash
mkdir -p web/certs
openssl req -x509 -newkey rsa:4096 \
  -keyout web/certs/key.pem -out web/certs/cert.pem \
  -days 365 -nodes -subj "/CN=<IP-del-servidor>"
```

### Arrancar en modo desarrollo

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000 \
  --ssl-keyfile web/certs/key.pem --ssl-certfile web/certs/cert.pem
```

### Arrancar como servicio (recomendado)

```bash
sudo cp systemd/passeval-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now passeval-web
```

### Acceder a la interfaz

Abrir `https://<IP>:8000/` en el navegador. El certificado autofirmado producirá un aviso del navegador la primera vez; aceptar la excepción.

La interfaz incluye tema claro/oscuro, traducción al español, inglés y francés, modales de ayuda por métrica y casillas de consentimiento independientes para HIBP y para estadísticas anónimas.

### Advertencia de seguridad — modo depuración

La variable de entorno `WEB_DEBUG` controla si se muestran en claro los segmentos detectados (palabras de diccionario, sustituciones leet, etc.):

```
WEB_DEBUG=true    # SOLO desarrollo
WEB_DEBUG=false   # por defecto — recomendado siempre en producción
```

Con `WEB_DEBUG=true`, la respuesta del API revela fragmentos de la contraseña, lo que rompe la garantía de privacidad de la herramienta. **No debe activarse en ningún despliegue accesible por terceros.**

## 5. Configuración de privacidad

| Opción | Dónde se controla | Comportamiento |
|---|---|---|
| Cotejo HIBP | Pregunta interactiva al inicio de la sesión (CLI) o casilla en la interfaz web | El hash SHA-1 de la contraseña se calcula localmente; solo se envían sus 5 primeros caracteres al servidor de HIBP. La contraseña no sale del proceso local. |
| Estadísticas anónimas | Pregunta interactiva (CLI) o casilla (web). Activable globalmente con `COLLECT_USER_STATS=true` en `.env` | Se registran únicamente la longitud, la máscara de clases de caracteres y el bucket de entropía. Nunca la contraseña, sus tokens, ni ningún hash de ella. |
| Modo depuración web | Variable `WEB_DEBUG` en `.env` | Ver §4. |

## 6. Ingesta de un dataset propio (opcional)

Para alimentar las estadísticas con un dataset de contraseñas propio:

```bash
python -m etl.ingest \
    --dataset-path "$DATASET_PATH" \
    --dataset-name <nombre-del-dataset> \
    --mode heavy \
    --workers 8
```

Modos disponibles:

- `light`: solo histogramas de longitud y composición de caracteres.
- `heavy`: añade entropía, patrones detectados y tokens (modo recomendado).
- `full`: añade la puntuación 0–4 del modelo para cada contraseña.

La ingesta es **reanudable**: si se interrumpe, al relanzar el comando con los mismos argumentos los ficheros ya completados se omiten y los parcialmente procesados retoman desde el último *checkpoint*.

Para seguir el progreso en tiempo real:

```bash
python -m etl.monitor --watch --interval 30
```

---

# Parte II — Guía del desarrollador

## 7. Arquitectura

El proyecto se compone de **cuatro componentes funcionales** que comparten el mismo motor de evaluación:

```
┌──────────────────────────────────────────────────────────────┐
│                  Motor de evaluación (src/passeval)          │
│   normalize → detectors → model → cracking → report          │
│                          ▲                                   │
│                          │ build_report(password)            │
│       ┌──────────────────┼──────────────────┐                │
│       │                  │                  │                │
│   CLI (cli.py)     Web (web/app.py)    ETL (etl/ingest)      │
│   passeval         FastAPI + SPA        modo full sobre      │
│   evaluate         POST /api/evaluate   dataset masivo       │
│       │                  │                  │                │
│       └──────────┬───────┴──────────────────┘                │
│                  ▼                                           │
│        PostgreSQL: schema `stats`                            │
│        (histograms, file_progress, ingestion_runs)           │
└──────────────────────────────────────────────────────────────┘
```

CLI, web y ETL invocan la misma función `build_report(password)` del módulo `src/passeval/report.py`, garantizando que la misma contraseña produce siempre el mismo resultado independientemente del punto de entrada.

## 8. Estructura del repositorio

```
src/passeval/                 # Paquete principal — el motor de evaluación
├── cli.py                    # Comando `passeval evaluate`
├── report.py                 # Report dataclass + build_report() + render_text()
├── breach_hibp.py            # Cliente HIBP con k-anonymity
├── cracking.py               # 5 escenarios de cracking, T = G / (2v)
├── normalize.py              # NFC + decode tolerante UTF-8
├── config.py                 # Carga de .env (validación temprana)
├── user_stats.py             # Estadísticas anónimas opt-in
├── db.py                     # Conexión PostgreSQL
└── strength/
    ├── shannon.py            # Entropía de Shannon
    ├── charset.py            # Entropía charset y bitmask de alfabeto
    ├── model.py              # Modelo Wheeler 2016: decompose() + score()
    └── patterns/             # 7 detectores:
        ├── dictionary.py     #   subcadenas en lemarios (cualquier idioma)
        ├── spanish.py        #   palabras y términos culturales en castellano
        ├── dates.py          #   fechas reconocibles (años, formatos comunes)
        ├── keyboard.py       #   runs horizontales en QWERTY-ES
        ├── leet.py           #   sustituciones leet (3→e, 4→a, …)
        ├── repetition.py     #   repeticiones de carácter y de periodo 2–4
        ├── sequences.py      #   secuencias alfabéticas y numéricas
        └── match.py          #   dataclass Match común a todos los detectores

web/                          # Interfaz web
├── app.py                    # FastAPI: POST /api/evaluate envuelve build_report
└── static/index.html         # SPA vanilla JS (tema, i18n ES/EN/FR, modales)

etl/                          # Pipeline de ingesta paralela
├── ingest.py                 # Orquestador: workers, checkpoints, signal handling
├── _stats_accumulators.py    # Acumuladores en RAM (Counter por dimensión)
├── stats_compute.py          # Cálculo offline de global_summary y top_items
└── monitor.py                # Dashboard de progreso en vivo (solo lectura)

data/dictionaries/            # 8 lemarios — necesarios para el modelo
sql/                          # Esquema PostgreSQL (3 ficheros .sql)
systemd/                      # Plantillas de servicio (ingest, web)
tests/                        # Suite de tests (pytest)
scripts/                      # Scripts auxiliares de construcción de datos
```

## 9. Modelo de evaluación

El motor sigue la arquitectura del algoritmo de Wheeler (2016) adaptada al castellano:

1. **Normalización** (`normalize.py`): NFC + decode UTF-8 tolerante a bytes inválidos.
2. **Detección de patrones** (`strength/patterns/`): los 7 detectores se ejecutan en paralelo sobre la contraseña y producen una lista de `Match` (segmentos con coste estimado en intentos).
3. **Descomposición** (`strength/model.py::decompose`): programación dinámica que encuentra la cobertura de coste mínimo de la contraseña combinando matches detectados con segmentos de fuerza bruta. Cuando varios matches solapan, el algoritmo se queda con el de menor coste.
4. **Estimación de intentos** (`strength/model.py::estimate_guesses`): producto de los costes de cada segmento + factor de configuración (capitalización, orden) según Wheeler §4.2.
5. **Score 0–4** (`strength/model.py::score`): mapeo por umbrales en log₂(intentos): `<10 → 0`, `10–20 → 1`, `20–35 → 2`, `35–60 → 3`, `≥60 → 4`.
6. **Cracking** (`cracking.py`): T = G / (2 · v) sobre 5 escenarios con velocidades documentadas.
7. **Riesgo agregado** (`report.py::_risk`): combina score con resultado HIBP. Cualquier *found* en HIBP eleva a CRÍTICO independientemente del score.

## 10. Detectores de patrones

Cada detector implementa la función `detect(password) -> list[Match]` y vive como módulo independiente en `src/passeval/strength/patterns/`:

| Detector | Reconoce | Longitud mínima |
|---|---|---|
| `dictionary` | Subcadenas presentes en cualquiera de los 8 lemarios | 3 chars |
| `spanish` | Subcadenas en los lemarios `_es` + lista interna de términos culturales hispanos | 3 chars |
| `dates` | Fechas reconocibles (años de 4 dígitos, formatos `dd/mm/yyyy`, etc.) | — |
| `keyboard` | Runs horizontales sobre QWERTY-ES (`qwerty`, `asdfg`, `12345`, `poiu`, en ambos sentidos) | 3 chars |
| `leet` | Substrings que tras de-leetificación coinciden con un lemario | 3 chars (tras deleet) |
| `repetition` | Repetición de un carácter (`aaaa`) o de un periodo 2-4 (`abab`, `123123`, `abcdabcd`) | 3 chars |
| `sequences` | Secuencias estrictamente alfabéticas o numéricas, crecientes o decrecientes (`abcd`, `9876`) | 3 chars |

Para añadir un nuevo detector:

1. Crear `src/passeval/strength/patterns/mi_detector.py` con función `detect(password) -> list[Match]`.
2. Importarlo en `src/passeval/strength/model.py::decompose` y añadirlo al pipeline.
3. Si el detector emite tokens reutilizables, propagarlos también en `etl/_stats_accumulators.py::update_stats`.
4. Añadir un fichero de tests `tests/test_patterns_mi_detector.py`.

## 11. Tests

```bash
pytest tests/ -v                          # suite completa
pytest tests/ -m "not network"            # excluye integración real con HIBP
pytest tests/ --cov=passeval --cov=etl    # con cobertura
```

La suite incluye:

- Tests unitarios del motor (entropías, detectores, modelo, cracking, normalización).
- Tests del cliente HIBP con mocks (y opcionalmente integración real marcada con `@pytest.mark.network`).
- Tests del ETL: acumuladores, descomposición de batches, reanudación desde checkpoint.
- Tests del CLI y del módulo de reporte.

## 12. Base de datos

Esquema `stats` (PostgreSQL ≥ 14) con tablespace `passeval_ts`:

| Tabla | Contenido | Origen |
|---|---|---|
| `datasets` | Catálogo de datasets registrados | ETL |
| `dataset_totals` | Sumas acumuladas para medias exactas | ETL |
| `length_histogram` | Distribución de longitudes | ETL |
| `charset_histogram` | Distribución por bitmask de clases de caracteres | ETL |
| `entropy_histogram` | Buckets de entropía (Shannon y charset) | ETL |
| `pattern_stats` | Patrones detectados (tipo + representación + count) | ETL |
| `token_frequencies` | Tokens reutilizables (substrings, deleetified) | ETL |
| `score_histogram` | Distribución de la puntuación 0–4 | ETL (modo `full`) |
| `ingestion_runs` | Log de ejecuciones del ETL | ETL |
| `file_progress` | Checkpoint por fichero para reanudación | ETL |

Las tablas histográmicas se actualizan con `INSERT … ON CONFLICT DO UPDATE SET count = count + EXCLUDED.count`, lo que permite paralelismo seguro entre workers gracias al *row-level locking* de PostgreSQL.

## 13. Convenciones de código

- **Tipado**: todos los módulos usan *type hints* completos.
- **Estilo**: línea ≤ 100 caracteres, configurado en `pyproject.toml` (Ruff).
- **Docstrings**: cada módulo y función pública tiene docstring explicando contrato, no implementación.
- **Tests**: cada módulo de código tiene su contraparte en `tests/test_<modulo>.py`.
- **Sin secretos en commits**: `.env` está en `.gitignore`; las credenciales viven solo en `.env` local. La plantilla `.env.example` se versiona con placeholders.

## 14. Despliegue como servicios systemd

```bash
sudo cp systemd/passeval-ingest.service /etc/systemd/system/   # ETL
sudo cp systemd/passeval-web.service    /etc/systemd/system/   # interfaz web
sudo systemctl daemon-reload
```

Cada `.service` define `EnvironmentFile=/path/to/.env` y arranca como el usuario indicado. Los logs van a `logs/` y se siguen con `journalctl -u <servicio> -f`.

---

## Licencia

Licencia del código: ver el campo `license` en `pyproject.toml`.

Datos de terceros utilizados por los detectores léxicos (fuente primaria, en su caso indicada junto al intermediario técnico de obtención):

- **INE** — nombres y apellidos más frecuentes en España; municipios y provincias.
- **`hermitdave/FrequencyWords`** — frecuencias de palabras (OpenSubtitles 2018, ES e inglés, CC-BY-SA 4.0).
- **`JorgeDuenasLerin/diccionario-espanol-txt`** — lemario amplio del español.
- **`dominictarr/random-name`** + **Moby Word Lists** (Grady Ward) — nombres en inglés.
- **US Census Bureau** (vía FiveThirtyEight) — apellidos más comunes en EE. UU.
- **NCSC** (vía `danielmiessler/SecLists`) — 100 000 contraseñas más usadas en brechas reales.

Las cabeceras de cada fichero en `data/dictionaries/*.txt` documentan la fuente exacta, el repositorio de obtención y la fecha de descarga.