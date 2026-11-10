# Fase 0 — Esqueleto del repositorio

## Qué se construyó y por qué

Esta fase no contiene lógica de negocio. Deja lista la base que usarán las demás:

- **Estructura de paquetes** (`backend/src/engine/...`). Se usa el *src layout*: el código solo se puede importar si el paquete está instalado, así que los tests prueban lo mismo que se ejecuta en producción.
- **`pyproject.toml`** con tres grupos de dependencias: las de ejecución, `dev` (tests y calidad) y `eval` (matplotlib). La imagen Docker instala solo las de ejecución.
- **`uv.lock` y `.python-version`**: fijan Python 3.12 y la versión exacta de cada dependencia. `make install` crea el entorno en `backend/.venv` sin tocar el Python del sistema.
- **`Dockerfile`**: una sola imagen para gateway, worker y agente; cada servicio definirá su comando en compose.
- **`docker-compose.yml`**: Redis y TimescaleDB con healthchecks y volúmenes.
- **`engine/config.py`**: configuración validada al arrancar.
- **`engine/logging.py`**: logs JSON, una línea por evento.
- **`Makefile` y CI**: los mismos comandos en local y en GitHub Actions.

## Flujo paso a paso

Todavía no circulan métricas. Hay dos flujos que conviene entender.

**Carga de configuración**
1. Un proceso crea `Settings()`.
2. pydantic-settings busca cada campo en las variables de entorno y después en `.env` o `../.env`. El entorno tiene prioridad sobre el archivo.
3. Los validadores `before` convierten `WORKER_PARTITIONS` y `CORS_ORIGINS` de texto separado por comas a listas.
4. Pydantic comprueba tipos y rangos.
5. El validador final comprueba que cada partición del worker exista y no esté repetida.
6. Si algo falla, se lanza `ValidationError` y el proceso no arranca.

**`make up`**
1. Compose lee `.env` para resolver `POSTGRES_*`. Si falta alguna, falla con el mensaje `copy .env.example to .env`.
2. Arranca los contenedores y monta `db/init/` en TimescaleDB.
3. `--wait` bloquea el comando hasta que los healthchecks pasen, o devuelve error si no pasan.

## Decisiones y alternativas descartadas

| Decisión | Por qué | Alternativa descartada |
|---|---|---|
| `REDIS_URL` y `DATABASE_URL` sin valor por defecto | Un servicio mal configurado debe fallar al arrancar, no conectarse a un destino equivocado | Usar `localhost` por defecto: oculta errores dentro de contenedores |
| `Annotated[list[int], NoDecode]` | pydantic-settings intenta leer las listas como JSON, y `0,1` no es JSON válido | Exigir `[0,1]` en `.env`: más incómodo y propenso a errores |
| Redis con AOF (`--appendonly yes`) | Sin persistencia, reiniciar Redis perdería los streams y los mensajes pendientes | Solo RDB: las instantáneas periódicas pierden más datos |
| Puertos publicados solo en `127.0.0.1` | Redis no tiene contraseña y Postgres usa una de desarrollo; no deben quedar expuestos en la red | Publicar en todas las interfaces |
| `mypy` en dos pasadas | `strict` es una opción global y no se puede activar por módulo en `pyproject.toml` | Copiar cada opción estricta en overrides: se desincroniza con el tiempo |
| Reglas `T20` y `BLE` en ruff | Convierten en errores de lint dos prohibiciones de `CLAUDE.md`: `print` y capturar excepciones genéricas | Confiar en la revisión manual |
| `.gitattributes` con LF | En Windows, `core.autocrlf=true` podría meter CRLF en archivos que leen contenedores Linux | Depender de la configuración de cada máquina |
| uv con `uv.lock` (ADR-000) | Fija el intérprete y las versiones exactas igual en local, CI y Docker; `--locked` falla si el lock no coincide con `pyproject.toml` | pip sin lock: cada instalación puede resolver versiones distintas |
| `IFOREST_EVAL_EVERY=10` por defecto | `ARCHITECTURE.md` no fija un valor; es provisional hasta la Fase 6 | — |

## Modos de fallo

| Fallo | Qué ocurre |
|---|---|
| Falta `.env` al ejecutar `make up` | Compose se detiene y pide copiar `.env.example` |
| Falta `REDIS_URL` o `DATABASE_URL` | `Settings()` lanza `ValidationError` y el proceso no arranca |
| `WORKER_PARTITIONS` fuera de rango | `ValidationError`; evita un worker leyendo una partición que nadie escribe |
| Un PostgreSQL nativo de Windows ocupa el puerto 5432 | `make up` falla con `ports are not available`, porque Docker Desktop publica los puertos en Windows. Hay que detener ese servicio; `ss` dentro de WSL no lo muestra |
| Un healthcheck no pasa | `make up` termina con error en vez de dar por buenos servicios que no responden |
| Redis se cae abruptamente | Con la política AOF por defecto (`everysec`) se puede perder aproximadamente el último segundo de escrituras |
| Cambias `POSTGRES_PASSWORD` con el volumen ya creado | Postgres conserva la contraseña anterior: las variables solo se aplican al inicializar un volumen vacío |

## Ejercicios prácticos

1. **Romper la configuración.** Tras `make install`, ejecuta desde `backend/`: `REDIS_URL=http://x uv run --locked --extra dev python -c "from engine.config import Settings; Settings()"`. Lee el error de Pydantic. Después prueba `WORKER_PARTITIONS=5 STREAM_PARTITIONS=2` con el mismo comando. Las variables escritas delante del comando solo afectan a esa ejecución, así que no hay nada que limpiar.
2. **Comprobar que Redis conserva datos.** Con `make up` en marcha, ejecuta `docker compose exec redis redis-cli XADD test '*' a 1`. Reinicia con `docker compose restart redis` y ejecuta `XLEN test`. Repite tras borrar `--appendonly yes` y recrear el contenedor con el volumen vacío, y compara. Devuelve la opción a su sitio.
3. **Ver actuar el lint.** Añade un `print("hola")` en `engine/config.py` y ejecuta `make lint`. Identifica la regla que falla (`T201`) y deshaz el cambio.

## Preguntas de entrevista

1. **¿Por qué validar la configuración al arrancar?**
   Porque un error aparece de inmediato y con un mensaje claro. Si se descubre al usar el valor, puede surgir horas después, en mitad del tráfico.

2. **¿Qué aporta el src layout?**
   Impide importar el código por estar en el directorio actual. Los tests ejercitan el paquete instalado, y un error de empaquetado se detecta antes.

3. **¿Por qué una sola imagen Docker para tres servicios?**
   Comparten código y dependencias. Una imagen garantiza la misma versión en todos y simplifica la compilación. El inconveniente es que cada servicio lleva dependencias que no usa, como scikit-learn en el gateway.

4. **¿Qué ganas aquí con el multi-stage build?**
   La imagen final recibe solo el virtualenv, sin el código fuente de compilación ni archivos temporales. Hoy la ganancia es pequeña porque ambas etapas usan la misma base. Crecerá si alguna dependencia necesita herramientas de compilación, que se quedarían en la primera etapa.

5. **¿Por qué ejecutar el contenedor como usuario no root?**
   Reduce el daño si alguien compromete el proceso: no tendrá privilegios de root dentro del contenedor, lo que limita qué puede modificar y dificulta escapar de él.

6. **¿Por qué un healthcheck además de `depends_on`?**
   `depends_on` por sí solo espera a que el contenedor arranque, no a que el servicio acepte conexiones. Postgres tarda varios segundos en estar listo, y el healthcheck con `pg_isready` detecta ese momento.

7. **¿Por qué AOF en Redis si TimescaleDB ya persiste los datos?**
   Un mensaje está solo en Redis desde el `XADD` hasta el `XACK`. Si Redis pierde el stream en ese intervalo, el mensaje nunca llega a la base de datos. AOF reduce esa ventana a alrededor de un segundo con `everysec`, pero no la elimina.

8. **¿Por qué logs JSON?**
   Cada evento tiene campos consultables, como `host` o `batch_size`, que un sistema de logs puede filtrar y agregar sin expresiones regulares frágiles.

9. **¿Por qué versiones fijas en las imágenes y no `latest`?**
   Para que una compilación sea reproducible y para que una actualización sea una decisión explícita y revisable. Esto importa más en TimescaleDB, cuyos datos en disco están ligados a la versión.

10. **¿Qué garantiza hoy tu CI y qué no?**
    Garantiza lint, tipos y tests unitarios en cada push a `main` y en cada pull request. Aún no compila la imagen Docker ni levanta Redis o TimescaleDB; las pruebas de integración llegarán en fases posteriores.
