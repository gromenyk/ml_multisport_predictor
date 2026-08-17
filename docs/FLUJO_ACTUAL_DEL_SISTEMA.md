# Informe del flujo actual del Planificador Multideporte

**Estado documentado:** 9 de agosto de 2026  
**Alcance:** funcionamiento técnico y funcional actualmente implementado.

## 1. Resumen ejecutivo

El sistema combina dos mecanismos diferentes:

1. Un conjunto de modelos entrenados con planificaciones históricas genera una **semana base** siguiendo la estructura aprendida de los entrenadores.
2. Un motor determinista y explicable construye el **plan definitivo**, adaptando esa semana base según el estado actual del atleta, la carga, la recuperación, la disponibilidad, los objetivos activos, las métricas y la memoria de tolerancia a entrenamientos anteriores.

El circuito principal es:

```text
Planillas históricas y semanas adaptadas aprobadas
                    |
                    v
       Contexto de 4 semanas planificadas
                    |
                    v
          Predicción de semana base
                    |
                    +------------------------------+
                    |                              |
                    v                              v
       Athlete State a fecha de corte       Ventanas y objetivos
                    |                              |
                    +---------------+--------------+
                                    |
                                    v
                         Motor adaptativo explicable
                                    |
                                    v
                    Revisión y aprobación humana
                                    |
                                    v
              Plan publicado en calendario e historial
                                    |
                                    v
                  Entrenamiento realizado y archivo FIT
                                    |
              +-----------------------+-----------------------+
              |                       |                       |
              v                       v                       v
       Carga realizada       Métricas automáticas     Rutas/segmentos
              |                       |                       |
              +-----------------------+-----------------------+

Feedback individual + bienestar + recuperación  -----------+
                                                       |
                                                       v
                         Nuevo Athlete State
                                                       |
                                                       v
                                       Siguiente ciclo semanal
```

La disposición vertical de este esquema no representa dependencia entre las fuentes. La carga, las métricas, las rutas/segmentos, el feedback individual y el bienestar/recuperación son entradas paralelas al Athlete State. En el flujo de uso, el feedback suele registrarse justo después de cargar un FIT, pero no es una salida de la carga ni de las métricas.

Ningún cambio adaptativo se publica automáticamente. La aprobación humana es obligatoria.

## 2. Páginas y responsabilidad actual

| Página | Responsabilidad |
|---|---|
| Ingestión de datos | Cargar FIT, procesar actividades, registrar respuesta postentrenamiento y controlar el uso analítico de cada actividad. También procesa planillas históricas. |
| Planificación | Paso 1: generar la semana base histórica. Paso 2: contextualizarla, proponer cambios, revisarlos y publicar el plan definitivo. |
| Calendario | Mostrar eventos, ventanas, sesiones predichas/publicadas, actividades realizadas, carga y protocolos de evaluación compatibles con la sesión del día. |
| Carga de entrenamiento | Comparar carga prescrita, predicha base, adaptada aprobada y realizada; mostrar carga diaria, aguda, habitual, balance, cambio semanal y monotonía. |
| Estado del atleta | Consolidar entrenamiento, recuperación, progreso, memoria longitudinal, rutas/segmentos, peso, grasa corporal y disponibilidad. |
| Objetivos | Administrar eventos, ventanas, catálogos, asignaciones, prioridades, métricas, zonas de FC, mediciones y evaluación de rutas GPX. |
| Segmentos | Definir segmentos desde FIT, reconocer intentos históricos, agrupar rutas repetidas y comparar velocidades por zona de FC. |
| Entrenamiento del modelo | Visible pero congelado para impedir reentrenamientos accidentales. |

## 3. Fuentes de datos

### 3.1 Archivos FIT

Los FIT aportan, cuando el dispositivo los contiene:

- fecha, hora y disciplina;
- modalidad de ciclismo, especialmente Ruta o MTB;
- distancia;
- duración activa, tiempo transcurrido y tiempo pausado manualmente;
- frecuencia cardiaca por registro;
- tiempo en zonas Garmin;
- velocidad;
- altitud, desnivel acumulado y recorrido GPS;
- marcas de tiempo y estado de movimiento.

Flujo de ingestión:

1. El FIT se guarda en `garmin/fit_files/`.
2. `garmin/extract_fit.py` lo normaliza como `garmin/activities/activity_*.json`.
3. Se ejecuta incrementalmente la evaluación de métricas.
4. Se actualizan las familias de rutas repetidas.
5. Se recalcula incrementalmente la carga realizada.

La interfaz mantiene además un índice incremental liviano en `data/derived/activity_index.json` para sus selectores y tablas. Este índice contiene solo fecha, disciplina, distancia y duración; evita releer los registros completos de todas las actividades FIT en cada interacción.

Los FIT originales no se modifican. Los JSON de actividad son la fuente normalizada utilizada por los cálculos posteriores.

### 3.2 Planillas históricas y semanas aprobadas

- Las planillas procesadas del entrenador se almacenan en `data/processed_data/`.
- Las semanas adaptadas y aprobadas se exportan a `data/planned_history/`.
- Si ambas fuentes contienen la misma semana, la semana adaptada aprobada tiene prioridad para construir el contexto futuro.
- Las planillas humanas originales no se sobrescriben.

### 3.3 Bienestar y recuperación

Los registros diarios se almacenan en `data/athlete_state/daily_wellness.json`.

Campos disponibles:

- duración del sueño;
- puntuación y clasificación Garmin del sueño;
- calidad subjetiva del sueño;
- VFC;
- frecuencia cardiaca en reposo;
- fatiga general;
- fatiga muscular;
- motivación;
- existencia de molestias;
- notas opcionales.

El importador Garmin de sueño puede poblar duración, puntuación, calidad Garmin, VFC y FC en reposo. Las variables subjetivas se ingresan manualmente.

### 3.4 Peso y composición corporal

Los datos de contexto corporal se almacenan en `data/athlete_state/manual_context.json` e incluyen:

- peso;
- porcentaje de grasa corporal.

Pueden ingresarse manualmente o importarse desde el CSV de Garmin.

### 3.5 Disponibilidad futura

Se almacena por fecha en `data/athlete_state/availability_calendar.json`.

- **Libre:** el motor puede asignar lo que estime compatible.
- **Limitada:** existe un máximo de minutos.
- **No disponible:** no se debe entrenar; el motor intenta reubicar una sesión existente.

El Athlete State prepara los siguientes 14 días. Si no existe un registro explícito, el día se considera libre.

### 3.6 Feedback individual del entrenamiento

Se guarda en `data/athlete_state/session_feedback.json` y se vincula a una actividad FIT.

Contiene:

- correspondencia con una sesión planificada, actividad adicional o actividad no identificable;
- porcentaje completado, cuando corresponde a una sesión planificada;
- RPE de 1 a 10;
- sensación al terminar;
- comportamiento de la energía;
- molestias;
- temperatura percibida: muy caluroso, caluroso, agradable, frío o muy frío;
- condición del terreno: normal, barroso o nevado;
- comentario opcional.

Este feedback alimenta la memoria longitudinal mediante cumplimiento, RPE, sensación final, energía y molestias. La temperatura y la condición del terreno quedan guardadas como contexto estructurado de la actividad, pero todavía no alteran automáticamente la carga, las métricas ni la adaptación. El antiguo feedback global de la semana fue retirado porque solo se almacenaba y no intervenía en ninguna decisión.

### 3.7 Eventos, ventanas, objetivos y métricas

Se almacenan principalmente en `data/objectives/`:

- `events.json`: eventos futuros y disciplinas relacionadas;
- `calendar_windows.json`: ventanas temporales;
- `development_goal_catalog.json`: catálogo de objetivos;
- `metric_catalog.json`: catálogo informativo de métricas;
- `heart_rate_zones.json`: zonas de FC con vigencia temporal;
- `measurements.json`: observaciones automáticas y manuales;
- `metric_evaluation_cache.json`: memoria incremental del procesamiento.

Cada ventana puede contener:

- fechas de inicio y término;
- objetivos asignados;
- métricas concretas asociadas a cada objetivo;
- prioridad del objetivo: principal, secundaria o de apoyo;
- porcentajes de influencia por disciplina.

Los porcentajes por disciplina son pesos de decisión. No representan una obligación de repartir ese mismo porcentaje de kilómetros o carga.

### 3.8 Segmentos y rutas

- El catálogo de segmentos conserva nombre, disciplina, modalidad, geometría y límites.
- Los intentos se detectan desde FIT.
- Las rutas completas se agrupan automáticamente en familias cuando su geometría es suficientemente similar.
- Segmentos y rutas se separan por zona observada de FC.
- Una actividad puede excluirse de estas comparaciones mediante su política de uso analítico.

## 4. Gobierno analítico por actividad

Cada actividad puede utilizar uno de estos perfiles:

| Perfil | Carga | Volumen | Zonas | Rendimiento | Segmentos/rutas | Pausas | Memoria | Cumplimiento |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Completo | Sí | Sí | Sí | Sí | Sí | Sí | Sí | Sí |
| Solo carga | Sí | Sí | Sí | No | No | No | No | No |
| Carga + cumplimiento | Sí | Sí | Sí | No | No | No | No | Sí |
| Excluir completamente | No | No | No | No | No | No | No | No |
| Personalizado | Configurable | Configurable | Configurable | Configurable | Configurable | Configurable | Configurable | Configurable |

Esto permite, por ejemplo, conservar la carga fisiológica de una salida social sin usar su velocidad, sus pausas o sus segmentos para evaluar rendimiento.

## 5. Cálculo de carga de entrenamiento

### 5.1 Fórmula común

La carga utiliza tiempo por zona de FC:

```text
carga = 0,65 × suma(
    minutos_en_zona × factor_deporte × factor_zona × factor_terreno
)
```

Factores actualmente configurados:

- Deportes: todos los deportes activos usan factor 1,0.
- Terrenos: Plano, Ondulado, Montaña y Subida usan factor 1,0.
- Zonas: Z1=1, Z2=2, Z3=3, Z4=4, Z5=5 y Z6=6.

En la configuración actual, la diferencia de carga proviene esencialmente del tiempo y la zona de FC. Disciplina y terreno todavía no alteran el valor porque sus factores son iguales a 1,0.

### 5.2 Carga realizada

Para una actividad FIT:

1. Se identifica el deporte canónico: Bici, Carrera, Trail o Kayak.
2. Se obtiene el tiempo por zona desde el resumen Garmin; si falta, se reconstruye desde registros y marcas de tiempo.
3. Si faltan límites de zona, puede utilizarse la actividad temporalmente más cercana que sí los contenga, dejando trazabilidad.
4. Se calcula la carga con la fórmula común.
5. Se agregan las actividades del mismo día.
6. Un caché por actividad, configuración y política permite recalcular solo lo nuevo o modificado.

### 5.3 Carga prescrita y predicha

- Los bloques con duración explícita usan esa duración.
- Los bloques expresados en distancia convierten distancia a duración mediante ritmos o velocidades de referencia por deporte y zona.
- Las sesiones estructuradas descomponen calentamiento, trabajo, recuperación y vuelta a la calma.
- La misma fórmula común permite comparar prescrito, predicho y realizado.
- La curva **Adaptada aprobada** se construye desde los planes adaptativos activos ya publicados. Es distinta de la curva Predicha, que conserva siempre la salida base e inmutable del modelo.

### 5.4 Indicadores derivados

| Indicador | Fórmula o definición | Uso actual |
|---|---|---|
| Carga aguda | Suma móvil de los últimos 7 días | Athlete State y reglas de sobrecarga/progresión. |
| Carga habitual | Suma de 42 días dividida por 6; equivalente semanal | Athlete State, balance y presupuesto adaptativo. |
| Balance de carga | Carga habitual − carga aguda | Positivo: margen relativo; negativo: carga reciente superior a la habitual. |
| Cambio semanal | `(carga 7d actual − carga 7d anterior) / carga 7d anterior` | Visualización. |
| Monotonía | Media de carga diaria 7d / desviación estándar diaria 7d | Visualización descriptiva. |
| Strain | Carga aguda × monotonía | Calculado y visualizado; no activa actualmente una regla adaptativa directa. |
| Días desde intensidad | Días desde la última actividad con tiempo en Z3–Z6 | Visible como contexto; no posee actualmente una regla independiente. |
| Pausa | Tiempo pausado / tiempo transcurrido | Athlete State visual; no modifica actualmente la propuesta adaptativa. |

## 6. Métricas de rendimiento automáticas

El catálogo JSON describe las métricas para la interfaz. La ejecución técnica está separada en extractor, calculador, protocolo y registro. Al procesar un FIT, el orquestador:

1. comprueba la política analítica de la actividad;
2. comprueba disciplina y modalidad;
3. obtiene el perfil de zonas vigente en la fecha del FIT;
4. extrae ventanas candidatas;
5. aplica el protocolo;
6. calcula y guarda observaciones;
7. reemplaza de forma idempotente el alcance actividad+métrica+versión;
8. omite actividades sin cambios mediante caché incremental.

Las ventanas aceptadas no se solapan. En las métricas segmentarias se admite como máximo 15 segundos entre registros, al menos 95% de movimiento y hasta 10 segundos detenidos acumulados.

### 6.1 Catálogo implementado

| Métrica | Disciplina/serie | Protocolo | Resultado | Objetivo compatible |
|---|---|---|---|---|
| Velocidad a FC estandarizada | Carrera y Trail, Z2 por disciplina | 3 min; ≥90% en Z2 con ±2 bpm; FC media en Z2; pendiente media −1% a +1% | Velocidad media, km/h | Eficiencia y economía aeróbica; rendimiento en plano |
| VAM a FC estandarizada | Carrera/Trail; Z2 y Z3 separadas; pendiente 10–15% | 5 min; ≥90% en la zona con ±2 bpm; pendiente media 10–15% | `desnivel neto positivo / duración × 3600`, m/h | Rendimiento en ascenso vertical |
| VAM ciclismo de ruta a FC estandarizada | Bici Ruta; Z2 y Z3 separadas; 5–10% | 5 min; reglas de FC y continuidad comunes | VAM, m/h | Rendimiento en ascenso vertical |
| VAM MTB a FC estandarizada | Bici MTB; Z2 y Z3 separadas; 5–10% | 5 min; reglas de FC y continuidad comunes | VAM, m/h | Rendimiento en ascenso vertical |
| Puntuación de resistencia en kayak | Kayak; actividad completa | Distancia y velocidad media disponibles | `distancia_km × velocidad_media_kmh`, km²/h | Resistencia en kayak |
| Velocidad a FC estandarizada Z4 | Carrera y Trail por disciplina | 10 min; ≥90% en Z4 con ±2 bpm; pendiente −1% a +1% | Velocidad media, km/h | Rendimiento en el umbral |
| VAM Trail Z4 | Trail; 10–15% | 10 min; ≥90% en Z4 con ±2 bpm | VAM, m/h | Rendimiento en el umbral |
| VAM Ruta Z4 | Bici Ruta; 5–10% | 10 min; ≥90% en Z4 con ±2 bpm | VAM, m/h | Rendimiento en el umbral |
| VAM MTB Z4 | Bici MTB; 5–10% | 10 min; ≥90% en Z4 con ±2 bpm | VAM, m/h | Rendimiento en el umbral |
| Velocidad a FC estandarizada Z5 | Carrera y Trail por disciplina | 3 min; ≥90% en Z5 con ±2 bpm; pendiente −1% a +1% | Velocidad media, km/h | Capacidad aeróbica máxima |

Para visualización y tendencia se usa la mediana de todas las ventanas válidas de una misma actividad y serie. Las observaciones originales permanecen guardadas.

### 6.2 Progreso de métricas

Cada serie se mantiene separada por las dimensiones declaradas:

- disciplina;
- zona de FC;
- banda de pendiente, cuando corresponde;
- modalidad de ciclismo mediante implementaciones independientes.

Para cada serie, el Athlete State calcula:

- última mediana por actividad;
- fecha de última medición;
- días desde la medición;
- número de observaciones y actividades resumidas;
- media y variabilidad;
- regresión lineal y tendencia estimada por 30 días;
- confianza baja con menos de 5 actividades, media desde 5 y alta desde 10.

No se genera un score global que mezcle series incompatibles.

### 6.3 Vigencia de mediciones

El intervalo objetivo es de 28 días:

| Días desde la última medición | Estado |
|---:|---|
| 0–14 | Reciente |
| 15–21 | Vigente |
| 22–27 | Próxima a vencer |
| 28–35 | Evaluación pendiente |
| Más de 35 | Desactualizada |
| Sin observaciones | Sin medición |

Esta vigencia se utiliza para priorizar recetas y mostrar protocolos en el calendario. El protocolo solo debe aparecer sobre una sesión compatible; no se asigna un test de kayak a un día de descanso.

## 7. Segmentos y rutas repetidas

### 7.1 Segmentos

- Se definen sobre la geometría de una actividad FIT.
- Los puntos inicial y final pueden ajustarse en perfil y mapa.
- Los intentos posteriores se detectan automáticamente.
- La comparación principal usa velocidad.
- Z2 y Z3 permanecen como series separadas; también pueden existir otras clasificaciones.

### 7.2 Rutas completas

- Las actividades se comparan geográficamente y se agrupan en familias de ruta.
- Los nuevos FIT se incorporan durante el procesamiento.
- La clasificación de zona puede confirmarse o corregirse manualmente para evitar que bajadas o tramos no representativos distorsionen la comparación.

### 7.3 Progreso geográfico dentro del Athlete State

Una serie ruta/segmento/zona entra al Athlete State a partir de 5 intentos. No se incorporan como progreso válido las series Mixto o Sin clasificar.

El nivel actual es la mediana de los últimos 3 intentos. Se compara con:

- intento anterior;
- referencia cercana a tres meses atrás;
- seis meses;
- un año;
- inicio de los datos.

Clasificación del cambio:

- mejora clara: ≥3%;
- mejora leve: ≥1%;
- estable: entre −1% y +1%;
- pérdida leve: entre −3% y −1%;
- pérdida clara: ≤−3%.

**Uso actual:** se muestra en Athlete State y sirve como evidencia contextual. El motor adaptativo todavía no consume directamente este progreso para activar una regla o alterar una sesión.

## 8. Construcción del Athlete State

El Athlete State se genera para una fecha de corte. Solo utiliza datos con fecha menor o igual a esa fecha, lo que permite simulación histórica sin mirar el futuro.

### 8.1 Bloque Training

Incluye:

- carga aguda 7 días;
- carga anterior 7 días;
- carga habitual equivalente de 42 días;
- balance, cambio semanal, monotonía y strain;
- carga diaria y línea de tiempo de 90 días;
- tiempo por zonas 28 días;
- carga por disciplina en 7 y 28 días, y por modalidad de ciclismo en 28 días;
- volumen, sesión más larga y fecha de progresión por disciplina;
- continuidad y pausa;
- exposición mecánica Carrera/Trail;
- días desde cualquier actividad;
- días desde intensidad.

### 8.2 Bloque Recovery

Para cada señal numérica principal se calculan:

- último valor disponible;
- promedio de los últimos 7 días;
- línea base móvil de 60 días anteriores;
- desviación del último valor frente a esa línea base.

Las señales principales son sueño, VFC, FC en reposo, fatiga general y fatiga muscular. El registro conserva además motivación, calidad del sueño y molestias.

La antigua “completitud de recuperación” fue eliminada: no intervenía en decisiones y no evaluaba la actualidad ni continuidad de los datos.

### 8.3 Readiness descriptivo

Parte desde 50 puntos y combina:

- sueño frente a línea base: hasta ±15;
- VFC frente a línea base: hasta ±12;
- FC en reposo frente a línea base: hasta ±10, con signo inverso;
- fatiga general: `(5 − fatiga) × 4`;
- fatiga muscular: `(4 − fatiga muscular) × 3`;
- molestia declarada: −20;
- sobrecarga relativa superior al 25%: −8.

La carga aguda al menos 20% bajo la habitual se registra como factor favorable, pero no suma directamente puntos en esta función.

Etiquetas:

- evidencia insuficiente: menos de 2 señales;
- preparación baja: menos de 40;
- normal: 40–69;
- alta: 70 o más.

Confianza:

- baja: menos de 3 señales;
- media: 3–4;
- alta: 5 o más.

Es un indicador descriptivo, no médico.

### 8.4 Bloque Progress

Contiene la evolución de todas las métricas automáticas y manuales, separadas por serie, además de su vigencia.

### 8.5 Bloque Context

Incluye:

- último peso y grasa corporal;
- disponibilidad de los siguientes 14 días;
- siguiente evento y días restantes;
- ventanas activas;
- pesos de influencia por disciplina.

### 8.6 Memoria longitudinal

Su objetivo es recordar qué dosis de resistencia fue realmente tolerada y evitar que cada semana comience desde cero.

Solo construye progresión con sesiones de resistencia: al menos 60% del tiempo registrado en Z1–Z2.

Clasificación:

- **Bien tolerada:** normalmente ≥90% completado, RPE <8, sin caída fuerte de energía, sin molestias y sin alarma de recuperación posterior.
- **Difícil:** cumplimiento <90%, RPE ≥8, sensación muy demandante, caída fuerte de energía, molestia leve o alarma en las 48 horas siguientes.
- **No tolerada:** cumplimiento <75%, sensación excesiva o molestia relevante.
- **No evaluable:** no existe feedback suficiente.

Alarmas en las 48 horas posteriores:

- molestia declarada;
- fatiga general ≥8;
- fatiga muscular ≥8;
- sueño <5,5 horas.

Por disciplina conserva:

- última sesión evaluable;
- sesión más larga bien tolerada;
- racha consecutiva de sesiones bien toleradas;
- acción sugerida: progresar, consolidar, retroceder o evidencia insuficiente.

Progresión máxima de la sesión comparable:

- Bici y Kayak: siguiente escalón completo de +10% desde la referencia bien tolerada;
- Carrera y Trail: +7%;
- alternativas conservadora y equilibrada aplican parcialmente ese escalón;
- cambios menores a 5 km en Bici o 1 km en otras disciplinas no se presentan.

Con dos respuestas consecutivas bien toleradas, modo construcción y un descanso compatible, puede añadirse una sesión Z2 equivalente al 25% de la referencia. No se agrega junto a intensidad Z4–Z6.

## 9. Generación de la semana base

### 9.1 Contexto

La predicción utiliza exactamente las últimas 4 semanas planificadas consecutivas:

- inicialmente provienen de planillas humanas;
- a medida que se aprueban semanas adaptadas, estas reemplazan a la semana humana equivalente dentro del contexto;
- no utiliza directamente los FIT realizados ni el Athlete State para predecir la base.

Esto conserva el estilo y la estructura histórica de planificación. La realidad ejecutada entra después, por medio de la capa adaptativa.

### 9.2 Salidas de los modelos

Para cada día se predicen o seleccionan:

- deporte o descanso;
- tipo de sesión;
- sesión estructurada o no;
- clase de objetivo para sesiones estructuradas;
- plantilla histórica compatible;
- terreno;
- zona;
- distancia.

Las sesiones estructuradas reutilizan una biblioteca extraída de las planillas históricas.

### 9.3 Restricción temporal

Solo se genera la semana calendario inmediatamente posterior a la última semana disponible. El sistema evita encadenar indefinidamente semanas futuras sin ejecución ni aprobación intermedia.

### 9.4 Reentrenamiento

Los modelos de producción están congelados. Las semanas adaptadas aprobadas entran en el contexto de inferencia, pero no reentrenan automáticamente los modelos.

## 10. Motor adaptativo

El motor es determinista, versionado y explicable. Recibe:

- semana base;
- Athlete State a la fecha inmediatamente anterior;
- ventanas activas;
- objetivos y métricas asignadas;
- prioridades e influencia por disciplina;
- vigencia y tendencia de métricas;
- disponibilidad;
- memoria longitudinal;
- biblioteca de plantillas históricas.

### 10.1 Secuencia acumulativa de salvaguardas

Las reglas no son modos excluyentes. Se aplican en esta secuencia y cada una
recibe la semana ya corregida por las anteriores:

1. Resolver días no disponibles.
2. Aplicar límites de tiempo.
3. Resolver conflictos de intensidad.
4. Aplicar reducción por recuperación limitada si corresponde.
5. Aplicar descarga por sobrecarga aguda si corresponde.
6. Solo si no quedó activa una salvaguarda de recuperación o carga, optimizar
   objetivos manteniendo o construyendo carga.

Así, una restricción de disponibilidad no anula una descarga necesaria: ambas
quedan registradas como cambios separados y explicables.

### 10.2 Reglas principales

| Regla | Activación | Consecuencia |
|---|---|---|
| Disponibilidad | Sesión en día no disponible | Intentar reubicarla sin cambiar contenido. |
| Límite de tiempo | Duración estimada supera minutos disponibles | Reducir distancia conservando disciplina, zona y terreno. |
| Separación de intensidad | Conflicto entre estímulos exigentes | Evalúa la semana junto con los dos días calendario previos: usa primero el FIT realizado y, si falta, el plan aprobado como evidencia provisional. Primero intenta reubicar; si no hay hueco, suaviza una Z3 a Z2 conservando disciplina, distancia y terreno; como último recurso la convierte en descanso. Z4–Z6 requieren separación; se permiten hasta dos Z3 consecutivas entre familias mecánicas distintas con recuperación compatible. |
| Recuperación limitada | Readiness <40 y confianza media/alta | Reducir 10% Z1–Z3 y 15% Z4–Z6. |
| Sobrecarga aguda | `(habitual − aguda) / habitual ≤ −25%` | Concentrar una reducción proporcional al exceso en un máximo de dos sesiones. |
| Progreso geográfico | Ruta o segmento comparable con al menos 5 intentos y zona FC definida | Una pérdida clara individual aporta atención a la receta y orienta el estímulo específico; no reduce ni bloquea por sí sola la progresión de carga. |
| Construcción | Readiness ≥55, confianza media/alta, carga aguda ≥20% bajo la habitual y sin molestia | Habilitar progresión y recetas intensas. |
| Memoria | Última sesión de resistencia bien tolerada y construcción habilitada | Progresar desde la referencia tolerada. |
| Frecuencia | Dos sesiones bien toleradas consecutivas, construcción y descanso compatible | Posible sesión adicional Z2. |

### 10.3 Selección de disciplina de foco

Si hay pesos diferenciados, se favorece la disciplina con mayor porcentaje. Si están equilibrados o no existen:

1. se buscan evaluaciones vencidas o métricas estancadas;
2. se considera cuánto tiempo lleva cada disciplina sin progresar;
3. se considera la exposición/carga reciente;
4. se exige que exista una sesión aeróbica modificable y disponibilidad compatible.

El porcentaje influye en la decisión; no fuerza una distribución equivalente de carga o kilómetros.

### 10.4 Recetas por objetivo

| Objetivo | Señal y operación |
|---|---|
| Eficiencia aeróbica | Trabajo continuo Z2 y oportunidad comparable de Speed@FC. |
| Rendimiento en ascenso vertical | Ascenso sostenido en la disciplina de la serie VAM que necesita atención. |
| Resistencia en kayak | Exposición continua y sostenible en kayak. |
| Rendimiento en el umbral | Bloques controlados de 10 minutos Z4 específicos a la métrica. |
| Capacidad aeróbica máxima | Repeticiones de 3 minutos Z5 con recuperación. |

Solo se aplica una receta fisiológica principal por semana. Para umbral y capacidad aeróbica máxima se elige una plantilla estructurada real del historial compatible con disciplina, objetivo y zona.

Las rutas y segmentos nunca se comparan entre zonas de frecuencia cardíaca. El
motor usa solamente las series elegibles del Athlete State, separadas por zona,
con cinco o más intentos. Para una disciplina resume las series mediante la
mediana de su cambio porcentual, por lo que una única ruta atípica no domina la
decisión.

### 10.5 Alternativas y presupuesto

Se generan tres alternativas:

- conservadora: 35% de la intensidad del ajuste;
- equilibrada: 65%;
- directa: 100%.

En modo construcción, la carga semanal objetivo aumenta aproximadamente:

- 5% sin racha positiva;
- 8% con una sesión bien tolerada consecutiva;
- 10% con dos o más.

También se considera la carga habitual como referencia. El aumento se concentra en una o dos sesiones relevantes y se descartan microajustes inferiores a 5 km en Bici o 1 km en otras disciplinas.

La alternativa se puntúa por:

- cercanía al presupuesto de carga;
- separación de intensidad;
- magnitud del cambio;
- receta aplicada;
- influencia de la disciplina prioritaria.

Cuando se activa la sobrecarga aguda no se aplica el antiguo recorte uniforme del 5%. En el umbral de activación, la descarga semanal objetivo comienza en 5%; aumenta gradualmente con el exceso hasta un máximo de 15%. Se elige primero la sesión futura de mayor carga de las disciplinas que más carga acumularon durante los siete días anteriores, con un máximo de dos sesiones y un recorte máximo de 35% por sesión. Se conservan disciplina, tipo, zona y terreno.

### 10.5.1 Lectura de una descarga consecutiva

La regla de sobrecarga compara la carga aguda de los últimos 7 días con la carga habitual equivalente de 42 días. Por tanto, una semana puede ser menor que la anterior y aun así activar una descarga adicional si la carga reciente continúa al menos 25% por encima de la habitual.

El recorte se aplica sobre la **semana base que se está evaluando**, no sobre la semana adaptada anterior. En consecuencia, si el modelo base de la siguiente semana propone una estructura más alta, una semana descargada puede contener alguna sesión individual ligeramente mayor que la equivalente de la semana previa. La lectura correcta debe hacerse sobre la carga semanal total, el balance y la semana base de referencia, no solo comparando una distancia aislada.

Una simulación de este tipo no constituye una nueva predicción del modelo: reutiliza una semana base hipotética y no inventa FIT, sueño, VFC ni feedback. La planificación real siempre debe recalcularse con los datos efectivamente disponibles a la fecha de corte.

### 10.6 Aprobación y publicación

La interfaz muestra:

- semana base;
- contexto utilizado;
- reglas activadas y descartadas;
- alternativas;
- antes y después por sesión;
- memoria y plantilla utilizadas;
- límites aplicados.

Cada cambio puede excluirse antes de aprobar. Al publicar:

1. se crea una versión activa en `adaptive_published_plans.json`;
2. versiones anteriores de la misma semana quedan reemplazadas;
3. la semana aparece en el calendario;
4. se exporta al historial planificado para que pueda integrar el contexto de las próximas cuatro semanas.

## 11. Qué datos afectan realmente cada componente

### 11.1 Matriz de uso

| Dato o indicador | Carga | Athlete State | Predicción base | Adaptación | Memoria | Métricas | Rutas/segmentos | Solo visual hoy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Planillas históricas |  |  | Sí | Como semana base |  |  |  |  |
| Semanas adaptadas aprobadas | Estimada |  | Sí, dentro de 4 semanas | Sí | Asociación plan-realizado |  |  |  |
| Distancia FIT | Respaldo si falta tiempo por zona | Sí | No | Indirecto | Sí | Sí | Sí |  |
| Tiempo por zonas FIT | Sí, principal | Sí | No | Sí, vía carga | Clasifica resistencia/intensidad | Sí |  |  |
| FC por registro | Sí | Agregada | No | Vía carga/recuperación |  | Sí | Clasificación de zona |  |
| GPS y altitud | Terreno descriptivo | Exposición | No | Indirecto en recetas | Desnivel guardado | Pendiente/VAM | Sí |  |
| Tiempo pausado |  | Sí | No | No actualmente | Se conserva, no clasifica | Continuidad de ventanas |  | Sí |
| Sueño duración |  | Sí | No | Sí | Alarma 48 h |  |  |  |
| Puntuación/calidad Garmin |  | Sí | No | Se muestra; no modifica directamente el readiness actual |  |  |  | Sí principalmente |
| VFC |  | Sí | No | Sí |  |  |  |  |
| FC en reposo |  | Sí | No | Sí |  |  |  |  |
| Fatiga general |  | Sí | No | Sí | Sí, 48 h |  |  |  |
| Fatiga muscular |  | Sí | No | Sí | Sí, 48 h |  |  |  |
| Motivación |  | Se conserva | No | No actualmente | No |  |  | Sí |
| Molestias |  | Sí | No | Sí, limitante | Sí |  |  |  |
| Peso y grasa corporal |  | Sí | No | No actualmente | No |  |  | Sí |
| Disponibilidad futura |  | Sí | No | Sí, prioritaria | Limita sesión adicional |  |  |  |
| Feedback individual |  | Vía memoria | No | Sí, vía memoria | Sí |  |  |  |
| Temperatura y condición del terreno declaradas |  | Se conservan con el feedback | No | No actualmente | No actualmente | No | No | Sí |
| Tendencia de métricas |  | Sí | No | Sí, selecciona foco/receta | No | Sí |  |  |
| Vigencia de métricas |  | Sí | No | Sí, urgencia de receta | No | Sí |  |  |
| Progreso de rutas/segmentos |  | Sí | No | No actualmente | No |  | Sí | Sí |
| Monotonía y strain |  | Sí | No | No como regla directa | No |  |  | Sí |
| Días desde intensidad |  | Sí | No | Visible, sin regla independiente | No |  |  | Sí |

### 11.2 Datos que actualmente no cambian una propuesta

Aunque forman parte de la información disponible, no intervienen directamente en las reglas adaptativas actuales:

- peso y grasa corporal;
- motivación;
- temperatura percibida y condición del terreno declaradas en la evaluación;
- puntuación o clasificación Garmin del sueño, salvo su visualización; la fórmula de readiness usa duración del sueño;
- tiempo pausado;
- progreso de rutas y segmentos;
- monotonía y strain;
- exposición mecánica Carrera/Trail;
- días desde intensidad como regla autónoma;
- notas de texto libre.

Pueden ser útiles para interpretación humana y futuras reglas, pero no debe asumirse que hoy modifican kilómetros, intensidad o terreno.

## 12. Ciclo operativo recomendado

### Después de cada entrenamiento

1. Cargar el FIT.
2. Procesarlo.
3. Revisar disciplina y modalidad detectadas.
4. Definir la política analítica si la actividad fue excepcional.
5. Asociarla con la sesión planificada correcta.
6. Registrar feedback individual.
7. Registrar bienestar diario, idealmente al despertar, para mantener comparabilidad.

El procesamiento actualiza incrementalmente carga, métricas y rutas.

### Antes de planificar una semana

1. Completar disponibilidad futura.
2. Revisar ventanas, objetivos, métricas y pesos de disciplina.
3. Verificar Athlete State y calidad/actualidad de las fuentes.
4. Generar la semana base en Planificación.
5. Fijarla como base.
6. Ejecutar la adaptación.
7. Revisar reglas, alternativas y cambios.
8. Aprobar y publicar.

Idealmente la planificación definitiva se hace cuando la semana en curso está suficientemente completa, normalmente el domingo por la noche. Si se vuelve a adaptar durante la semana, debe entenderse como una nueva versión basada en los datos disponibles hasta ese momento.

## 13. Límites actuales y lectura correcta

- El sistema es un planificador local y personalizado, no un servicio médico.
- Solo reconoce de forma completa Bici, Carrera, Trail y Kayak. Un FIT de fuerza no genera por sí solo prescripciones de fuerza dentro del flujo actual.
- El modelo base aprende de planes, no directamente de actividades ejecutadas.
- La capa adaptativa corrige esa limitación utilizando el estado realizado.
- Las zonas de FC dependen de perfiles con vigencia temporal; una fecha sin perfil puede impedir una medición automática.
- Una métrica solo se calcula si la actividad es compatible y su política permite rendimiento.
- Las observaciones históricas se calculan siempre; las ventanas filtran visualización y prioridad, no la existencia de los datos.
- La carga actual no diferencia deportes ni terrenos mediante factores distintos; diferencia principalmente tiempo e intensidad cardiaca.
- Una sesión larga no es fisiológicamente equivalente a repartir toda su distancia en varias sesiones. El motor conserva una sesión principal y solo distribuye carga complementaria.
- La regresión de métricas es descriptiva y depende del número y comparabilidad de observaciones.
- Las rutas y segmentos aportan evidencia valiosa, pero todavía no generan decisiones automáticas.
- Los comentarios libres se guardan para revisión humana; no se interpretan automáticamente.
- Toda propuesta debe revisarse y aprobarse manualmente.

## 14. Archivos principales de referencia

| Área | Archivo principal |
|---|---|
| Navegación y UI | `app/app.py` |
| Página unificada | `app/ui_pages/planning.py` |
| Predicción base | `ML/predict_next_week.py` |
| Dataset de 4 semanas | `ML/dataset.py` |
| Athlete State | `athlete_state/state_builder.py` |
| Motor adaptativo | `adaptive_planning/engine.py` |
| Recetas | `adaptive_planning/recipes.py` |
| Memoria longitudinal | `adaptive_planning/progression_memory.py` |
| Feedback individual | `adaptive_planning/session_feedback_store.py` |
| Publicación e historial | `adaptive_planning/workflow_store.py`, `adaptive_planning/planned_history.py` |
| Carga común | `training_load/training_load_calculator.py` |
| Carga realizada | `training_load/performed_load.py` |
| Factores de carga | `config/training_load_factors.json` |
| Orquestador de métricas | `metrics/orchestrator.py` |
| Registro de métricas | `metrics/registry.py` |
| Vigencia de métricas | `metrics/freshness.py` |
| Progreso geográfico | `app/backend/geographic_progress.py` |
| Políticas por actividad | `app/backend/activity_usage_store.py` |

---

Este documento describe lo que el código utiliza actualmente. Distingue deliberadamente entre datos almacenados, datos visualizados y datos que realmente intervienen en una decisión automática.
