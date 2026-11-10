# CLAUDE.md — Metrics Anomaly Engine

> Este archivo va en la **raíz del repositorio**. Claude Code lo lee automáticamente al iniciar cada sesión.
> Contiene las reglas de trabajo. El diseño técnico detallado está en `docs/ARCHITECTURE.md`.

---

## 1. Qué es este proyecto

Pipeline de telemetría de infraestructura en tiempo real con detección de anomalías:

- Un **agente** simula N servidores y envía métricas (CPU, memoria, latencia, RPS) cada segundo.
- Un **gateway** (FastAPI) las recibe y las publica en **Redis Streams**.
- Un **worker** las persiste en **TimescaleDB**, ejecuta detectores (reglas, estadístico EWMA e Isolation Forest) y emite alertas con deduplicación e histéresis.
- Un **dashboard** (React) muestra métricas y alertas en vivo vía WebSocket.
- Una **evaluación offline** con anomalías inyectadas (ground truth) mide precision, recall, F1 y tiempo de detección de cada detector.

**Objetivo:** proyecto de portafolio para prácticas en empresas grandes (backend, SRE, cloud).
**Prioridades, en orden:** 1) corrección, 2) decisiones defendibles y documentadas, 3) resultados medidos y reproducibles, 4) cantidad de funcionalidades.

---

## 2. Documentos de referencia

| Archivo | Para qué |
|---|---|
| `docs/ARCHITECTURE.md` | **Fuente de verdad** del diseño: componentes, contratos de datos, streams, esquema de BD, detección, API. |
| `PROMPTS.md` | Fases del proyecto. Se trabaja una fase por sesión. |
| `docs/adr/NNN-titulo.md` | Architecture Decision Records: una decisión por archivo. |
| `docs/learning/fase-NN.md` | Notas de aprendizaje para el autor, en español. |
| `docs/benchmarks.md` | Resultados medidos (generados por scripts, nunca escritos a mano). |

---

## 3. Reglas de trabajo (obligatorias)

1. **Una fase a la vez.** Implementa solo lo que pide la fase actual. No adelantes trabajo de fases futuras, salvo stubs mínimos imprescindibles.
2. **Plan antes de código.** Al iniciar una fase, presenta un plan breve (archivos a crear o modificar, enfoque, riesgos, dudas) y **espera aprobación** antes de escribir código.
3. **`docs/ARCHITECTURE.md` manda.** No cambies stack, contratos, nombres de streams, tablas ni endpoints por tu cuenta. Si algo del diseño no funciona o falta, detente, explica el problema y propone el cambio como ADR.
4. **Sin dependencias nuevas** fuera de la sección 4 sin justificarlo y pedir confirmación.
5. **No inventes resultados.** Cualquier número en README o benchmarks debe salir de un script del repo, con seed y comando documentados.
6. **Nunca debilites tests** (borrar, saltar, relajar asserts) para que pasen. Si un test falla, arregla el código o explica por qué el test está mal y pregunta.
7. **Sin secretos en el repo.** La configuración va solo por variables de entorno, documentadas en `.env.example`.
8. **Ante ambigüedad, pregunta.** Si no hay respuesta, elige la opción más simple y menciónala en el resumen final.
9. **Cambios pequeños y revisables.** Prefiere varios archivos claros a uno gigante.

---

## 4. Stack (fijo)

| Capa | Tecnología |
|---|---|
| Lenguaje backend | Python 3.12 |
| Entorno y dependencias Python | uv (`backend/.python-version` y `backend/uv.lock`, ver ADR-000) |
| API | FastAPI + Uvicorn, Pydantic v2, pydantic-settings |
| Bus de mensajes | Redis 7, **Streams** con consumer groups (cliente `redis` asyncio) |
| Base de datos | PostgreSQL + TimescaleDB 2.x, cliente `asyncpg` (sin ORM) |
| ML | scikit-learn (IsolationForest), numpy, joblib |
| HTTP cliente | httpx (async) |
| Observabilidad propia | prometheus-client, structlog (logs JSON) |
| Tests | pytest, pytest-asyncio, pytest-cov |
| Calidad | ruff (lint y format), mypy |
| Gráficas de benchmark | matplotlib (solo en scripts de evaluación) |
| Frontend | React + TypeScript + Vite, Recharts |
| Infra local | Docker Compose, Makefile |
| CI | GitHub Actions |
| Opcional (fase 10) | Prometheus + Grafana en un perfil de compose |

---

## 5. Convenciones de código

### Python
- Type hints en todo. `mypy --strict` obligatorio en `engine/detection` y `engine/simulation`.
- **Pydantic v2** para todo dato que cruza una frontera: HTTP, Redis o base de datos.
- `async` en gateway y worker. Nada bloqueante en el event loop; si la inferencia o un cálculo pesa, usa `run_in_executor`.
- **`engine/detection` es puro:** sin I/O, sin Redis, sin BD, sin asyncio. Recibe datos y devuelve resultados. Se usa igual online (worker) y offline (evaluación).
- **`engine/simulation` es puro y determinista** dado un `seed`.
- Los routers de FastAPI solo validan y delegan. La lógica va en servicios.
- Logs estructurados con structlog. Nunca `print`.
- No captures `Exception` genérica, salvo en el loop principal del worker (se loguea con contexto y se continúa).
- Identificadores, comentarios, docstrings, commits y README en **inglés**. `docs/learning/` en **español**.

### TypeScript / React
- `strict: true`, sin `any`.
- Componentes funcionales y hooks. Sin librerías de estado extra (nada de Redux).
- Lógica de conexión en hooks (`useLiveStream`), no en componentes.

### Git
- Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`).

---

## 6. Tests

- `tests/unit/`: sin servicios externos, rápidos.
- `tests/integration/`: contra Redis y TimescaleDB **reales** (Docker Compose localmente, servicios en CI). **No mockear Redis ni la BD en integración.**
- Cada detector tiene tests con series sintéticas cuyo resultado esperado es conocido (por ejemplo, un pico debe detectarse y ruido normal no).
- Cobertura objetivo: ≥ 80 % en `engine/detection` y `engine/simulation`.

---

## 7. Comandos (Makefile)

| Comando | Qué hace |
|---|---|
| `make up` / `make down` | Levanta o baja todo con Docker Compose |
| `make logs` | Logs de los servicios |
| `make install` | Crea el entorno con Python 3.12 e instala las dependencias de `uv.lock` |
| `make lint` | ruff + mypy (backend), eslint + tsc (frontend) |
| `make test` | Tests unitarios |
| `make test-int` | Tests de integración (requiere `make up`) |
| `make train` | Entrena el modelo y guarda el artefacto |
| `make eval` | Evaluación offline, regenera `docs/benchmarks.md` |
| `make load` | Prueba de carga |
| `make demo` | Levanta todo y arranca el agente con escenario de demo |

---

## 8. Cierre de fase (obligatorio al terminar cada fase)

1. `make lint` y `make test` pasan (y `make test-int` si la fase toca servicios).
2. Si la fase toca servicios, `make up` funciona desde cero.
3. Documentación al día: `ARCHITECTURE.md` solo si se aprobó un cambio; ADR si hubo una decisión.
4. Crea `docs/learning/fase-NN.md` **en español** con:
   - Qué se construyó y por qué.
   - Flujo de datos paso a paso.
   - Decisiones tomadas y alternativas descartadas.
   - Modos de fallo y cómo se manejan.
   - 3 ejercicios prácticos para el autor (modificar o romper algo y observar).
   - 8 a 10 preguntas de entrevista con respuestas modelo.
5. Resumen final: archivos cambiados, cómo probarlo a mano, deuda técnica y mensaje de commit sugerido.
6. **Detente.** No empieces la siguiente fase.

---

## 9. Prohibido

- Usar Redis **Pub/Sub** para el pipeline de métricas. Se usan **Streams**.
- Decidir qué métricas se guardan según la detección. **Todo se persiste.**
- Que el modelo ML solo vea datos que superaron un umbral. Los detectores corren **en paralelo** sobre todo el stream.
- Hacer `XACK` antes de que el batch esté guardado en la BD.
- Lógica de negocio en routers.
- Afirmaciones de marketing en README o docs ("enterprise-grade", "destruye la competencia", "0 ms", "distribuido" sin justificar).
- Notebooks como parte del pipeline.
