"""Paquete ETL: ingesta reanudable, cómputo offline de stats y monitor.

Componentes:

- `ingest`: ETL principal con flags --dataset-path / --dataset-name /
  --limit / --dry-run / --reset. Reanudable por fichero vía
  `stats.file_progress`.
- `_stats_accumulators`: acumuladores en RAM para length/entropy/
  patterns/tokens, volcados al cierre con upserts incrementales.
- `stats_compute`: recálculo offline de length_histogram, top_items y
  global_summary desde tablas SQL (sin necesidad del dataset original).
- `monitor`: dashboard textual de progreso (snapshot único o --watch).
"""
