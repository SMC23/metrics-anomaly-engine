# PROMPTS.md — Construcción por fases con Claude Code

## Cómo usar este archivo

1. Crea el repositorio y coloca `CLAUDE.md` en la raíz, `docs/ARCHITECTURE.md` y este `PROMPTS.md`.
2. **Una sesión por fase.** Abre Claude Code y pega primero el **prompt de arranque** y luego el prompt de la fase.
3. Claude presentará un plan. **Léelo antes de aprobar.** Si no entiendes algo del plan, pregunta ahí mismo.
4. Al terminar la fase, revisa el diff, ejecuta los comandos de prueba tú mismo y haz commit.
5. **Lee `docs/learning/fase-NN.md` y haz los ejercicios** antes de pasar a la siguiente fase. Este paso es el que hace que el proyecto sea tuyo.

> Consejo: si una sesión se vuelve larga o confusa, ciérrala y abre una nueva con el prompt de arranque + "continúa la fase N; esto es lo que falta: ...".

---

## Prompt de arranque (al inicio de cada sesión)

```text
Lee CLAUDE.md, docs/ARCHITECTURE.md y PROMPTS.md completos antes de hacer nada.
Luego revisa el estado actual del repositorio (estructura, últimos commits, docs/learning/).
Dime en 5 líneas: qué fases están completas, qué falta de la fase actual y cualquier
inconsistencia que veas entre el código y ARCHITECTURE.md. No escribas código todavía.
```

---

## Fase 0 — Esqueleto del repositorio

```text
Fase 0: esqueleto del repositorio.

Objetivo: dejar la base sobre la que se construirá todo, sin lógica de negocio.

Tareas:
- Estructura de carpetas según la sección 2 de ARCHITECTURE.md (solo carpetas y
  __init__.py necesarios; nada de módulos vacíos de relleno).
- backend/pyproject.toml con las dependencias de la sección 4 de CLAUDE.md, config de
  ruff, mypy y pytest.
- backend/Dockerfile (multi-stage, usuario no root) que sirva para gateway, worker y agent
  cambiando el comando.
- docker-compose.yml con redis y timescaledb (con healthchecks y volumen), montando db/init/.
- engine/config.py con pydantic-settings y .env.example con las variables de la sección 11.
- engine/logging.py con structlog en JSON.
- Makefile con los comandos de la sección 7 de CLAUDE.md (los que aún no aplican pueden
  imprimir "not implemented yet").
- .gitignore, README.md mínimo (una línea de descripción + "work in progress").
- .github/workflows/ci.yml con lint + tests unitarios.

Criterios de aceptación:
- `make up` levanta redis y timescaledb sanos.
- `make lint` y `make test` pasan (incluye un test real de carga de configuración).

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 1 — Contratos y base de datos

```text
Fase 1: contratos de datos y esquema de base de datos.

Tareas:
- engine/schemas.py: Metric, MetricBatch, Alert, enums Severity/AlertState/DetectorName,
  según la sección 4 de ARCHITECTURE.md, con todas las validaciones descritas.
- engine/redis_keys.py: nombres de streams y función de partición por host (hash estable,
  NO usar hash() de Python porque cambia entre procesos).
- db/init/*.sql: tablas, hypertable, continuous aggregate, retención e índices de la sección 6.
- engine/db/: pool asyncpg y funciones de insert/query necesarias para las fases siguientes
  (solo firma + implementación de las que se usen en fase 2 a 4).
- ADR-001: Redis Streams vs Pub/Sub vs Kafka.
- ADR-002: tabla ancha vs formato largo (una fila por métrica) en TimescaleDB.

Tests:
- Unitarios de validación de schemas (casos válidos e inválidos, bordes).
- Unitario: la partición de un host es estable y está en rango.
- Integración: el esquema se crea, la hypertable existe, el insert con conflicto no duplica.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 2 — Gateway de ingesta

```text
Fase 2: gateway de ingesta (solo ingesta y salud; la API de lectura y el WebSocket son de la fase 8).

Tareas:
- engine/gateway/app.py con lifespan que abre y cierra Redis y pool de BD.
- POST /v1/metrics según 4.2 y 3.3: valida, agrega ingested_at_ms, agrupa por partición
  y hace XADD con pipeline de Redis y MAXLEN aproximado.
- 503 si Redis falla, con log estructurado.
- /healthz, /readyz, /metrics (Prometheus) con las métricas de gateway de la sección 10.
- Servicio gateway en docker-compose.

Tests:
- Unitarios con TestClient para validación (400/422) y respuesta 202.
- Integración: un POST real termina en el stream correcto con los campos esperados.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 3 — Simulación y agente

```text
Fase 3: simulación determinista y agente.

Tareas:
- engine/simulation: perfiles y anomalías de la sección 3.1. API principal: un generador que,
  dado (hosts, duración, intervalo, seed, plan de anomalías), produce métricas y la lista de
  ground truth. Debe poder generar en modo "rápido" (sin esperar tiempo real) para evaluación.
- engine/agent/__main__.py: CLI de la sección 3.2, envío por batch con httpx, backoff con jitter,
  buffer acotado, cierre limpio con Ctrl+C.
- Escenarios: normal (sin anomalías), demo (una anomalía de cada tipo cada pocos minutos),
  load (muchos hosts, sin anomalías).
- Servicio agent en docker-compose con escenario demo.

Tests:
- Mismo seed => misma salida exacta; seeds distintos => salidas distintas.
- Cada tipo de anomalía produce la forma esperada en su ventana y no altera otras métricas.
- El agente reintenta ante 503 (con un servidor falso).

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 4 — Worker: consumo confiable y persistencia

```text
Fase 4: worker con consumer group y persistencia (sin detección todavía).

Tareas (sección 3.4 de ARCHITECTURE.md, pasos 1, 2, 3, 6 y 7):
- Creación idempotente del consumer group.
- Loop XREADGROUP por partición, batch insert idempotente, XACK después del commit.
- XAUTOCLAIM periódico y DLQ tras 5 entregas o mensaje inválido.
- Manejo de SIGTERM y backoff si la BD falla (sin confirmar).
- Métricas Prometheus del worker en :9100.
- Dejar pipeline.py como punto de extensión vacío para la fase 5.

Tests de integración (los más importantes del proyecto):
- Mensajes publicados terminan en la tabla metrics.
- Matar el worker a mitad de un batch: al reiniciar, no se pierde ni se duplica nada.
- Un mensaje malformado termina en la DLQ y no bloquea el resto.

Aplica el cierre de fase de CLAUDE.md. En docs/learning explica en detalle
at-least-once, idempotencia y la PEL (pending entries list) de Redis.
```

---

## Fase 5 — Detección v1: reglas, EWMA y alert manager

```text
Fase 5: detectores rules y ewma + alert manager.

Tareas:
- engine/detection/base.py, window.py, rules.py, ewma.py según la sección 7.
  Recuerda: este paquete es puro (sin I/O ni asyncio) y con mypy --strict.
- engine/worker/alerting.py según 7.4 (fire_after, resolve_after, cooldown, dedup).
  Diseñado para ser testeable sin Redis ni BD (el reloj se inyecta).
- engine/worker/pipeline.py: ventanas por host, detectores en paralelo sobre cada muestra,
  alert manager, persistencia en alerts y XADD a alerts:events.
- Reconstrucción de ventanas y alertas firing al arrancar el worker.

Tests:
- Reglas: cada umbral dispara y no dispara justo en el borde.
- EWMA: un pico se detecta, el ruido normal no; no hay alertas durante el warm-up.
- Alert manager: histéresis, cooldown y dedup con un reloj falso.
- Integración: escenario demo corto genera alertas en la tabla y en el stream.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 6 — Isolation Forest

```text
Fase 6: detector Isolation Forest.

Tareas:
- engine/detection/features.py: las 24 features de 7.3, vectorizado con numpy.
- engine/ml/train.py: genera datos normales con la simulación (seed fija), separa
  entrenamiento y validación, entrena, calibra el umbral y guarda el artefacto con metadatos.
- engine/detection/iforest.py: carga el artefacto, valida que la lista de features coincide,
  evalúa cada IFOREST_EVAL_EVERY muestras.
- Integración en el pipeline del worker; si no hay modelo, sigue sin él y lo loguea.
- ADR-003: por qué Isolation Forest, por qué features por ventana y sus limitaciones
  (modelo global para todos los perfiles, sin reentrenamiento ante drift).

Tests:
- Features: valores correctos en series conocidas (p. ej. slope de una recta).
- Entrenar con seed fija es reproducible.
- Un memory_leak lento produce scores más anómalos que una serie normal.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 7 — Evaluación offline (benchmarks)

```text
Fase 7: evaluación offline según la sección 8.

Tareas:
- engine/ml/evaluate.py: escenarios con seed fija, cada detector y el combinado,
  métricas a nivel de evento por detector y por tipo de anomalía, tiempo de detección.
- Escribe docs/benchmarks.md con tablas, el comando exacto y la seed usada, y
  gráficas en docs/img/ (una serie con anomalía + alertas superpuestas; F1 por detector).
- make eval regenera todo.

Reglas especiales:
- Reporta los resultados tal como salgan. Si iforest no supera a ewma en algún tipo, dilo
  y propón por qué en una sección "Analysis".
- No ajustes umbrales mirando los datos de test. Si calibras, usa un set de validación
  separado y documenta cómo.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 8 — API de lectura y WebSocket

```text
Fase 8: API de lectura y stream en vivo del gateway (sección 9).

Tareas:
- GET /v1/hosts, /v1/metrics/query (raw y 1m desde el continuous aggregate), /v1/alerts.
- WS /v1/live: un único task de fondo lee metrics:raw:* y alerts:events con XREAD y
  reparte a los clientes según su suscripción. Clientes lentos no deben bloquear a los demás
  (cola acotada por cliente; si se llena, se descartan mensajes de métricas, nunca alertas).
- Ping cada 20 s, CORS según config.

Tests:
- Unitarios de los endpoints con datos de prueba.
- Integración: un cliente WS suscrito a un host recibe solo sus métricas y todas las alertas.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 9 — Dashboard

```text
Fase 9: frontend (React + TS + Vite + Recharts).

Tareas:
- Página Dashboard: selector de host, 4 gráficas de línea (últimos 5 min), panel de alertas
  con severidad por color y estado firing/resolved, indicador de conexión.
- Hooks: useMetricsHistory (carga inicial por REST), useLiveStream (WS con reconexión y
  backoff, buffer de máx. 300 puntos por host).
- Diseño limpio y sobrio, modo oscuro. Sin librerías de UI pesadas.
- Dockerfile (build + nginx) y servicio en compose en el puerto 8080.

Criterios:
- `make demo` muestra métricas moviéndose y alertas apareciendo sin recargar.
- `npm run lint` y `npm run build` sin errores; tipos estrictos.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 10 — Rendimiento y escalado

```text
Fase 10: prueba de carga, particionado y observabilidad.

Tareas:
- scripts/loadtest.py: lanza el agente en modo load con N hosts, mide durante X minutos y
  calcula throughput (métricas/s) y p50/p95/p99 de pipeline_e2e_latency_seconds leyendo
  /metrics de los servicios.
- Probar con STREAM_PARTITIONS=1 y 1 worker vs. STREAM_PARTITIONS=2 y 2 workers.
- Perfil "observability" en compose con Prometheus y Grafana, con un dashboard provisionado
  (ingesta, latencia e2e, pending, alertas).
- Agregar los resultados a docs/benchmarks.md con especificaciones de la máquina.
- ADR-004: particionado por host y por qué no basta con agregar consumers al mismo group.

Reglas: reporta números reales de tu máquina; nada estimado.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 11 — README y pulido final

```text
Fase 11: README final en inglés y revisión general.

README con:
- Una línea de qué es + captura del dashboard (deja el placeholder y dime cómo tomarla).
- Diagrama Mermaid de la arquitectura (que renderice en GitHub).
- Quickstart: clonar, `make demo`, URLs.
- Design decisions: resumen de cada ADR en 1-2 líneas con enlace.
- Results: tablas tomadas de docs/benchmarks.md (sin redondear a favor).
- Limitations and future work, honesto.
- Cómo correr tests, evaluación y carga.

Revisión general:
- Busca código muerto, TODOs olvidados, inconsistencias con ARCHITECTURE.md, tests débiles.
- Lista los hallazgos y propón arreglos antes de aplicarlos.

Tono: técnico y sobrio, sin adjetivos de marketing.

Aplica el cierre de fase de CLAUDE.md.
```

---

## Fase 12 — Apropiación (la más importante para la entrevista)

Hazla en varias sesiones. Aquí tú haces el trabajo y la IA te evalúa.

**12.1 Recorrido guiado**
```text
Explícame el recorrido completo de una métrica desde que el agente la genera hasta que
aparece en el dashboard, archivo por archivo y función por función. Después hazme 5
preguntas sobre ese recorrido y espera mis respuestas antes de corregirme.
```

**12.2 Reescritura**
```text
Voy a reescribir engine/worker/alerting.py desde cero sin mirar el original.
No me des código. Solo dame la especificación de comportamiento y los tests que debe pasar.
Cuando termine, compara mi versión con la original y dime qué casos se me escaparon.
```
(Repite con `consumer.py`, `features.py` y `evaluate.py`.)

**12.3 Caos**
```text
Dame 6 experimentos de fallo para ejecutar yo mismo (apagar Redis, matar el worker,
mandar datos corruptos, llenar el stream, borrar el modelo, BD lenta). Para cada uno:
el comando, qué debería pasar según ARCHITECTURE.md y qué debo observar.
No me digas el resultado; te lo cuento yo y lo analizamos.
```

**12.4 Feature propia**
Agrega algo que no esté en el plan, diseñándolo tú: un nuevo tipo de anomalía, un detector nuevo, un canal de notificación (webhook) o métricas reales con `psutil`. Usa la IA solo para revisar tu diseño y tu código.

**12.5 Simulacro de entrevista**
```text
Actúa como un ingeniero senior que entrevista para una práctica de backend/SRE.
Entrevístame sobre este proyecto durante 30 minutos, una pregunta a la vez, con
repreguntas cuando mi respuesta sea vaga. Mezcla diseño, fallos, escalado, ML y
"¿qué harías distinto?". Al final dame retroalimentación concreta y una nota del 1 al 10.
```
Repítelo en inglés:
```text
Same interview, now in English. Correct my English only at the end, not during the interview.
```

---

## Prompts de utilidad

**Depurar**
```text
Tengo este error: <pega el error completo y el comando que ejecuté>.
Antes de cambiar código: explica la causa raíz probable, cómo confirmarla y el arreglo
mínimo. No toques archivos fuera de lo necesario ni relajes tests.
```

**Revisión de código**
```text
Revisa el diff de la última fase como un revisor exigente: corrección, manejo de errores,
concurrencia, consistencia con ARCHITECTURE.md y calidad de los tests. Lista hallazgos por
severidad. No arregles nada todavía.
```

**Explicar algo**
```text
Explícame <archivo/función/concepto> como si yo tuviera que defenderlo en una entrevista:
qué hace, por qué así, qué alternativas había y qué pasaría si lo quito.
```

**Cuando la IA se desvía**
```text
Detente. Esto contradice CLAUDE.md / ARCHITECTURE.md en <punto>. Revierte ese cambio
y explica por qué te desviaste. Si crees que el diseño está mal, propón un ADR en vez de
cambiarlo directamente.
```
