# Fase 1 — Contratos de datos y esquema de base de datos

## Qué se construyó y por qué

Esta fase define **qué forma tienen los datos** en cada frontera del sistema y **dónde viven**.
Todavía no hay gateway ni worker: lo que se hace aquí es el "idioma común" que hablarán.

| Archivo | Qué contiene | Por qué existe |
|---|---|---|
| `backend/src/engine/schemas.py` | `Metric`, `MetricBatch`, `StreamMessage`, `Alert` y los enums `Severity`, `AlertState`, `DetectorName` | Todo dato que cruza HTTP, Redis o la BD se valida con un único modelo. Si el gateway y el worker usaran validaciones distintas, un dato podría entrar por un lado y romper el otro. |
| `backend/src/engine/redis_keys.py` | Nombres de streams, `partition_for_host`, `consumer_name` | Un solo lugar para los nombres evita errores tipográficos entre servicios, y la partición tiene que ser idéntica en todos los procesos. |
| `db/init/001_extensions.sql` | Activa TimescaleDB | Lo demás depende de la extensión. |
| `db/init/002_metrics.sql` | Hypertable `metrics`, continuous aggregate `metrics_1m`, políticas de refresco y retención | Sección 6 de `ARCHITECTURE.md`. |
| `db/init/003_alerts.sql` | Tabla `alerts` con `CHECK` e índices | Igual. Los `CHECK` repiten en la BD las reglas de `Alert`. |
| `backend/src/engine/db/pool.py` | `create_pool`, `check_connection` | Pool asyncpg para el gateway (fase 2, `/readyz`) y el worker (fase 4). |
| `backend/src/engine/db/metrics.py` | `insert_metrics` | El insert idempotente por lotes que usará el worker. |
| `docs/adr/001`, `002`, `005` | Decisiones | Streams vs Pub/Sub vs Kafka; tabla ancha; recorte del stream y creación del consumer group. |
| `tests/unit/test_schemas.py`, `test_redis_keys.py` | Tests unitarios | Casos válidos, inválidos y bordes. |
| `tests/integration/test_db_*.py` | Tests contra TimescaleDB real | El esquema existe tal como se diseñó y el insert no duplica. |

Además: `make test-int` ya ejecuta los tests de integración y CI tiene un job `integration` que
levanta Redis y TimescaleDB con Docker Compose.

## Flujo de datos paso a paso

Aún no circulan datos de verdad, pero los contratos ya fijan el recorrido que tendrá una
métrica:

1. **Agente → gateway (HTTP).** El body es un `MetricBatch`: de 1 a 1000 `Metric`. Cada `Metric`
   se valida: `host` con el patrón `^[a-z0-9-]{1,64}$`, porcentajes en [0, 100], latencia y RPS
   ≥ 0, sin `NaN` ni infinito, `ts` con zona horaria y como mucho 5 minutos en el futuro. Los
   campos desconocidos se rechazan (`extra="forbid"`): un `cpu_pc` mal escrito es un error, no
   un campo ignorado en silencio.
2. **Gateway → Redis.** El gateway calcula `partition_for_host(host, STREAM_PARTITIONS)`, crea un
   `StreamMessage(payload=metric, ingested_at_ms=...)` y llama a `to_stream_fields()`, que
   produce `{"payload": "<json>", "ingested_at_ms": "<entero>"}`: en Redis todos los campos son
   texto.
3. **Redis → worker.** El worker recibe ese mapa (como `str` o como `bytes`, según cómo se
   configure el cliente) y llama a `StreamMessage.from_stream_fields(...)`. Cualquier problema
   (falta un campo, JSON inválido, bytes que no son UTF-8, una métrica fuera de rango) produce
   **un único tipo de error**, `ValidationError`. Así el worker de la fase 4 tendrá una sola
   rama: "inválido → `metrics:dlq`".
4. **Worker → TimescaleDB.** `insert_metrics(conn, metrics)` inserta el lote entero en **una
   sola sentencia** con `unnest` y devuelve cuántas filas eran nuevas. Si se ejecuta dentro de
   una transacción, todo el lote entra o no entra nada.
5. **Lectura agregada.** Las consultas por minuto leen `metrics_1m`, que combina buckets ya
   materializados con lo más reciente calculado al vuelo (ver más abajo).

## Los tres conceptos clave de esta fase

### 1. La PEL y el problema de `MAXLEN`

**Qué es la PEL.** Con consumer groups, Redis guarda por cada grupo una *pending entries list*:
la lista de IDs que **se entregaron a algún consumer pero todavía no se confirmaron** con
`XACK`. Para cada entrada recuerda qué consumer la tiene, cuánto tiempo lleva sin tocarse y
cuántas veces se ha entregado.

La PEL es lo que hace posible el *at-least-once*:

```text
XREADGROUP ... '>'   → Redis entrega el mensaje y lo anota en la PEL
(insert en la BD)
XACK                 → Redis lo quita de la PEL
```

Si el worker muere entre las dos cosas, el mensaje sigue en la PEL. Otro consumer (o el mismo
al reiniciar) lo recupera con `XAUTOCLAIM` y lo procesa otra vez. Por eso `CLAUDE.md` prohíbe
hacer `XACK` antes de guardar: el ACK es la promesa de "ya está a salvo en la BD".

**El problema.** `XADD ... MAXLEN ~ 1000000` recorta el stream **por cantidad**, empezando por
las entradas más viejas, **sin mirar la PEL ni el grupo**. La PEL guarda solo IDs, no los datos.
Si el worker está caído o va lento el tiempo suficiente para que el stream supere el límite:

- Las entradas **pendientes** recortadas dejan un ID "huérfano" en la PEL. Al pedir el historial
  (`XREADGROUP ... 0`) aparecen con la lista de campos vacía, y `XAUTOCLAIM` las devuelve en un
  tercer array de "IDs borrados" y las saca de la PEL.
- Las entradas **nunca entregadas** desaparecen sin dejar rastro en la PEL.

Esto lo comprobamos en el Redis local antes de escribir el ADR-005 (tabla en el ADR): con 10
entradas, 4 leídas sin ACK y `XTRIM MAXLEN 3`, las 4 pendientes salieron como "borradas" en
`XAUTOCLAIM` y 3 entradas no leídas desaparecieron.

**Cómo se detecta.** `XINFO STREAM` da `entries-added` (cuántas se añadieron en toda la vida del
stream) y `XINFO GROUPS` da `entries-read` y `lag` del grupo. Si el contador del grupo es
fiable:

```text
perdidas_sin_entregar = entries-added − entries-read − lag
                      = 10 − 4 − 3 = 3
```

**Por qué el gateway crea el grupo.** Ese contador solo es fiable si el grupo existe **antes
de la primera entrada**. Lo comprobamos: un grupo creado tarde, aunque sea desde `0`, sobre un
stream ya recortado tiene `entries-read` vacío, y entonces no se puede contar lo perdido. Si se
creara con `$`, se saltaría todo lo que ya estuviera en el stream. Por eso el ADR-005 (en estado
*Proposed*) propone que el gateway, al arrancar, ejecute
`XGROUP CREATE metrics:raw:{p} detectors 0 MKSTREAM` en cada partición, igual que el worker,
tratando el error `BUSYGROUP` como éxito. Así:

- ningún mensaje queda fuera del grupo, sin importar qué servicio arranque primero;
- `lag` está definido y las pérdidas por recorte se pueden contar y exportar como métricas.

`MAXLEN` se mantiene como **válvula de seguridad de memoria**, no como algo normal: si salta,
es pérdida de datos y queda contada. La implementación es de las fases 2 y 4.

### 2. Continuous aggregates con `materialized_only = false`

Un *continuous aggregate* es una vista materializada que TimescaleDB mantiene de forma
incremental. `metrics_1m` guarda, por host y minuto, el `avg` y el `max` de cada métrica. Una
política (`add_continuous_aggregate_policy`) la refresca cada minuto recalculando la ventana
`[ahora − 2 h, ahora − 1 min]`.

El problema: entre dos refrescos, los datos más recientes **todavía no están materializados**.
Hay dos modos:

| `materialized_only` | Qué devuelve una consulta a `metrics_1m` |
|---|---|
| `true` (valor por defecto desde TimescaleDB 2.13) | Solo los buckets materializados. El último minuto o dos no aparecen hasta el siguiente refresco. |
| `false` (*real-time aggregation*, lo que usamos) | Los buckets materializados **más** un agregado calculado al vuelo sobre las filas crudas posteriores al último punto materializado (el *watermark*). |

Para un dashboard en vivo, `true` mostraría un hueco al final de la gráfica. Con `false` la vista
siempre está completa. El coste es que cada consulta hace un poco de trabajo extra sobre las
filas crudas más recientes; como el refresco corre cada minuto, son pocas.

El test `test_real_time_aggregate_sees_rows_before_refresh` lo demuestra: inserta dos filas y
consulta `metrics_1m` **sin refrescar**, y el bucket ya aparece con avg 20 y max 30.

Dos detalles importantes del SQL:

- `end_offset => 1 minute`: no se materializa el minuto en curso, que aún está recibiendo datos;
  ese minuto lo cubre la parte en tiempo real.
- `start_offset => 2 hours` tiene que ser **mucho menor que la retención del crudo (7 días)**.
  Si el refresco recalculara un rango cuyas filas crudas ya se borraron, los buckets de ese
  rango quedarían vacíos en `metrics_1m`. Con 2 horas eso nunca pasa, y el agregado puede vivir
  90 días aunque el crudo se borre a los 7.

### 3. Por qué la PK `(host, time)` permite la idempotencia

*At-least-once* significa que **el mismo mensaje puede llegar dos veces** al worker: por
ejemplo, si el worker guarda el lote, muere antes del `XACK` y otro consumer lo reclama. La
solución no es intentar que no se repita (eso sería *exactly-once*, mucho más caro), sino que
**procesarlo dos veces tenga el mismo efecto que procesarlo una**. Eso es idempotencia.

Para lograrla hace falta una clave que identifique un dato **por su contenido**, no por cuándo
se insertó:

- Un ID autoincremental (`serial`) no sirve: cada reintento crearía un ID nuevo y una fila nueva.
- El ID del mensaje de Redis tampoco: si el agente reenvía el mismo batch por HTTP (tras un
  timeout, por ejemplo), el gateway crea un mensaje nuevo con otro ID.
- `(host, time)` sí sirve: un host produce **una** muestra por instante. Si llega otra vez la
  misma muestra, por el camino que sea, choca con la misma clave.

Con esa clave, el insert usa `ON CONFLICT (host, time) DO NOTHING`: la fila repetida se ignora y
la sentencia no falla. `insert_metrics` devuelve cuántas filas eran nuevas, lo que permite
medir cuántos duplicados llegan. Los tests cubren: lote repetido (0 nuevas), lote parcialmente
solapado (solo las nuevas), duplicados dentro del mismo lote, misma hora en hosts distintos (no
es conflicto) y que gana la **primera** escritura.

¿Por qué `(host, time)` y no `(time, host)`? En TimescaleDB toda clave única de una hypertable
debe incluir la columna de partición (`time`), y ambas lo cumplen. Poner `host` primero hace que
el mismo índice sirva para "un host, un rango de tiempo", que es la consulta del dashboard y de
la reconstrucción de ventanas del worker. Así no hace falta otro índice.

Limitación a tener presente: si un host enviara **dos lecturas distintas con el mismo
timestamp**, la segunda se descartaría en silencio. Para un agente que envía una muestra por
segundo es el comportamiento correcto.

## Decisiones tomadas y alternativas descartadas

| Decisión | Por qué | Alternativa descartada |
|---|---|---|
| Redis Streams (ADR-001) | Persistencia, PEL y redelivery sin código propio | Pub/Sub (pierde mensajes sin suscriptor), Kafka (demasiada operación para este tamaño) |
| Tabla ancha (ADR-002) | 1 mensaje = 1 fila = 1 clave de idempotencia; el modelo ML no necesita pivotar | Formato largo: 4× filas y un pivot en cada lectura |
| Gateway y worker crean el grupo desde `0` (ADR-005, *Proposed*) | Ningún mensaje queda fuera y el lag es medible | Solo el worker, o crear con `$` |
| `StreamMessage` en `schemas.py` | Es un contrato que cruza Redis; debe validarse como los demás | Serializar a mano en el gateway y parsear a mano en el worker |
| `from_stream_fields` convierte todo error en `ValidationError` | El worker tendrá una sola rama hacia la DLQ | Dejar escapar `UnicodeDecodeError` o `JSONDecodeError` |
| BLAKE2b para la partición | Estable entre procesos y versiones de Python | `hash()` de Python: cambia con `PYTHONHASHSEED` en cada proceso |
| `extra="forbid"` y `allow_inf_nan=False` en los contratos | Un campo mal escrito o un `NaN` son errores del emisor y deben verse | Ignorarlos: los `NaN` romperían EWMA y las features del modelo más adelante |
| Modelos `frozen` | Un dato validado no se puede modificar después por accidente | Modelos mutables |
| `ts` normalizado a UTC | Comparaciones y claves consistentes sin importar la zona del emisor | Guardar la zona original |
| `CHECK` en `alerts` | Defensa en profundidad: la BD rechaza estados imposibles aunque falle el código | Confiar solo en Pydantic |
| Insert con `unnest` | Un lote = una sentencia = un viaje de red; devuelve el número de filas nuevas | `executemany`: un viaje lógico por fila y no informa cuántas se insertaron |
| CI de integración con Docker Compose | Aplica `db/init/` igual que en local | *Services* de GitHub Actions: arrancan antes del checkout y no pueden montar `db/init/` |

## Modos de fallo y cómo se manejan

| Fallo | Qué pasa |
|---|---|
| Métrica fuera de rango, `NaN`, campo extra o `ts` sin zona | `ValidationError`; en la fase 2 será un 422 |
| `ts` más de 5 min en el futuro | Rechazada. Un reloj mal sincronizado en el agente produce errores visibles en vez de datos "del futuro" |
| Entrada del stream corrupta (JSON roto, bytes no UTF-8, falta un campo) | `ValidationError` en `from_stream_fields`; en la fase 4 irá a `metrics:dlq` |
| Mensaje entregado dos veces | `ON CONFLICT DO NOTHING`; `insert_metrics` devuelve 0 |
| Fallo a mitad de una transacción | Rollback: no entra ninguna fila del lote (test `test_insert_inside_transaction_is_rolled_back`) |
| Stream recortado con mensajes sin procesar | Pérdida de datos. ADR-005 propone detectarla y contarla |
| Se cambia la función de partición | Los hosts cambiarían de partición y de worker. Los tests de valores fijos (*golden*) lo detectan |
| Se edita un `.sql` con el volumen ya creado | No se aplica: `docker-entrypoint-initdb.d` solo se ejecuta con el volumen vacío. Hay que usar `docker compose down -v` (borra los datos) |
| Error de SQL en un script de init | El contenedor falla al inicializar y `make up` no queda sano |

## Ejercicios prácticos

1. **Ver la PEL y el recorte con tus ojos.** Con `make up`:
   ```bash
   docker compose exec redis redis-cli
   XGROUP CREATE t g 0 MKSTREAM
   XADD t '*' n 1   # repite 10 veces
   XREADGROUP GROUP g c COUNT 4 STREAMS t >
   XPENDING t g
   XTRIM t MAXLEN 3
   XINFO STREAM t        # mira entries-added
   XINFO GROUPS t        # mira entries-read y lag
   XAUTOCLAIM t g c2 0 0-0
   DEL t
   ```
   Calcula `entries-added − entries-read − lag` y explica qué entradas son. Repite creando el
   grupo **después** de los `XADD` y del `XTRIM`, y observa qué pasa con `entries-read`.

2. **Romper la idempotencia.** En `db/init/002_metrics.sql`:
   - Primero cambia la PK por `id bigserial PRIMARY KEY`. Ejecuta `docker compose down -v` y
     `make up`, y lee `docker compose logs timescaledb`. ¿Por qué `create_hypertable` se niega?
   - Después quita la PK del todo (ninguna restricción única). Recrea el volumen y ejecuta
     `make test-int`. ¿Qué error da `ON CONFLICT (host, time)` y qué tests fallan?
   - Piensa: si además quitaras el `ON CONFLICT` del insert, ¿qué pasaría al reprocesar un
     lote? ¿Qué test lo detectaría?

   Restaura el archivo y vuelve a recrear el volumen.

3. **Ver la agregación en tiempo real.** Inserta filas a mano:
   ```bash
   docker compose exec timescaledb psql -U metrics -d metrics
   INSERT INTO metrics VALUES (now(), 'demo-01', 10, 50, 100, 200);
   SELECT * FROM metrics_1m WHERE host = 'demo-01';
   ALTER MATERIALIZED VIEW metrics_1m SET (timescaledb.materialized_only = true);
   SELECT * FROM metrics_1m WHERE host = 'demo-01';
   ALTER MATERIALIZED VIEW metrics_1m SET (timescaledb.materialized_only = false);
   DELETE FROM metrics WHERE host = 'demo-01';
   ```
   Explica por qué la segunda consulta no devuelve nada.

## Preguntas de entrevista

1. **¿Qué es la PEL de Redis y para qué sirve?**
   Es la lista, por consumer group, de mensajes entregados pero no confirmados con `XACK`.
   Guarda el consumer que tiene cada uno, el tiempo sin actividad y el número de entregas.
   Permite recuperar mensajes de un consumer que murió (`XAUTOCLAIM`) y por tanto ofrecer
   entrega *at-least-once*.

2. **¿Qué pasa si `MAXLEN` recorta mensajes que están en la PEL?**
   Los datos se pierden, pero el ID sigue en la PEL. `XAUTOCLAIM` los devuelve como borrados y
   los quita. Los mensajes nunca entregados se pierden sin rastro. Por eso `MAXLEN` debe ser
   solo una válvula de memoria, dimensionada en horas de caída que puede absorber, y la pérdida
   debe medirse (`entries-added − entries-read − lag`).

3. **¿Por qué el gateway también crea el consumer group?**
   Para que el grupo exista antes de la primera entrada. Si se crea tarde con `$` se saltan
   mensajes; si se crea tarde con `0` sobre un stream recortado, Redis no conoce `entries-read`
   y el lag deja de servir para contar pérdidas. Es idempotente: `BUSYGROUP` se trata como éxito.

4. **¿Qué es la idempotencia y cómo la consigues aquí?**
   Que aplicar una operación varias veces tenga el mismo efecto que una. Con la PK natural
   `(host, time)` y `ON CONFLICT (host, time) DO NOTHING`: un mensaje repetido choca con la
   misma clave y se ignora.

5. **¿Por qué no usar el ID del mensaje de Redis como clave?**
   Porque un reenvío HTTP del agente genera un mensaje nuevo con otro ID. La clave tiene que
   identificar el dato, no el transporte.

6. **¿Qué es una hypertable y por qué la PK incluye `time`?**
   Una tabla que TimescaleDB parte automáticamente en *chunks* por tiempo (aquí, uno por día).
   Las restricciones únicas se comprueban por chunk, así que deben incluir la columna de
   partición; si no, no se podría garantizar la unicidad entre chunks.

7. **¿Qué significa `materialized_only = false`?**
   Agregación en tiempo real: la vista combina los buckets materializados con un cálculo al
   vuelo sobre las filas crudas posteriores al último refresco. Así el dashboard ve los últimos
   minutos sin esperar a la política. Desde TimescaleDB 2.13 el valor por defecto es `true`,
   por eso se pone explícitamente.

8. **¿Por qué el `start_offset` del refresco es 2 h y no, por ejemplo, 30 días?**
   Porque refrescar un rango cuyas filas crudas ya se borraron por retención vaciaría esos
   buckets. Mientras `start_offset` sea menor que la retención del crudo (7 días), el agregado
   conserva los 90 días aunque el crudo desaparezca. Además, refrescar menos rango cuesta menos.

9. **¿Por qué no usar `hash()` de Python para particionar?**
   Porque Python lo aleatoriza por proceso (`PYTHONHASHSEED`) para strings. Dos gateways
   mandarían el mismo host a particiones distintas, y la ventana de ese host quedaría repartida
   entre dos workers. Uso BLAKE2b, que es determinista, y tests con valores fijos.

10. **¿Por qué tabla ancha y no una fila por métrica?**
    Las cuatro métricas llegan siempre juntas. Una fila por muestra significa una clave de
    idempotencia por mensaje, 4× menos filas y entradas de índice, y el modelo ML lee las cuatro
    alineadas sin pivotar. A cambio, añadir una métrica requiere una migración.
