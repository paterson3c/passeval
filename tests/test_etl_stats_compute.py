"""Tests del módulo `etl.stats_compute`.

Las agregaciones derivadas que calcula este módulo:
- `top_items` se deriva de `stats.token_frequencies` y `stats.pattern_stats`.
- `global_summary` se calcula desde `stats.length_histogram` +
  `stats.charset_histogram` + `stats.entropy_histogram`.

Como las funciones públicas son consultas SQL, los tests se centran en:
- parse_args: contrato de la CLI.
- _percentile_from_buckets: lógica pura de interpolación lineal.
- Operaciones SQL: se verifican con un fake cursor que captura el
  texto del query y los parámetros, asegurando que se ejecute la
  secuencia correcta (DELETE + INSERT) y que los parámetros sean los
  esperados.
- compute_global_summary integra correctamente los tres histogramas
  cuando hay datos suficientes.
"""
from __future__ import annotations

import pytest

from etl import stats_compute


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

def test_parse_args_minimo_fija_dataset_name():
    args = stats_compute.parse_args(["--dataset-name", "rockyou2024"])
    assert args.dataset_name == "rockyou2024"
    assert args.top_n == 100  # default


def test_parse_args_top_n_personalizable():
    args = stats_compute.parse_args(["--dataset-name", "x", "--top-n", "50"])
    assert args.top_n == 50


def test_parse_args_dataset_name_obligatorio():
    with pytest.raises(SystemExit):
        stats_compute.parse_args(["--top-n", "10"])


# ---------------------------------------------------------------------------
# _percentile_from_buckets
# ---------------------------------------------------------------------------

def test_percentil_50_centro_de_un_unico_bucket():
    """Bucket [10.0, 10.5) con 100 items -> mediana ≈ 10.25."""
    p = stats_compute._percentile_from_buckets([(10.0, 100)], 0.5)
    assert abs(p - 10.25) < 1e-9


def test_percentil_50_a_traves_de_dos_buckets_iguales():
    """[10,11) con 50 + [11,12) con 50: P50 cae al final del primero."""
    buckets = [(10.0, 50), (11.0, 50)]
    p = stats_compute._percentile_from_buckets(buckets, 0.5)
    assert abs(p - 10.5) < 1e-9


def test_percentil_90_recae_en_bucket_alto():
    """100+100+100, p90: target=270 -> está en el tercer bucket."""
    buckets = [(0.0, 100), (1.0, 100), (2.0, 100)]
    p = stats_compute._percentile_from_buckets(buckets, 0.9)
    assert abs(p - 2.35) < 1e-9


def test_percentil_buckets_vacios_no_revienta():
    assert stats_compute._percentile_from_buckets([], 0.5) == 0.0


# ---------------------------------------------------------------------------
# Cursor fake: cola de respuestas; detecta el query por palabra clave
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Captura SQLs y permite encolar respuestas por palabra clave.

    `responses_by_table` mapea fragmentos de tabla a la lista de filas
    que `fetchall()` debe devolver cuando se ejecuta una query que
    contenga ese fragmento. `fetchone_queue` se usa para los `SELECT`
    de fila única (lookup_dataset_id).
    """

    def __init__(self):
        self.queries: list[tuple[str, tuple | dict]] = []
        self.rowcounts = iter([])
        self.rowcount = 0
        self.responses_by_table: dict[str, list[tuple]] = {}
        self.fetchone_queue: list[tuple] = []
        self._last_table: str | None = None

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        try:
            self.rowcount = next(self.rowcounts)
        except StopIteration:
            self.rowcount = 0
        # Decide qué tabla está siendo leída para el próximo fetch*
        self._last_table = None
        for marker in self.responses_by_table:
            if marker in sql:
                self._last_table = marker
                break

    def fetchone(self):
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def fetchall(self):
        if self._last_table is None:
            return []
        rows = self.responses_by_table.get(self._last_table, [])
        # Una vez consumidos, vacía esa entrada para que un segundo
        # query con el mismo marker no devuelva el mismo set por error
        self.responses_by_table[self._last_table] = []
        return rows


# ---------------------------------------------------------------------------
# lookup_dataset_id
# ---------------------------------------------------------------------------

def test_lookup_dataset_id_existe():
    cur = _FakeCursor()
    cur.fetchone_queue = [(7,)]
    assert stats_compute.lookup_dataset_id(cur, "x") == 7
    assert "FROM stats.datasets" in cur.queries[0][0]


def test_lookup_dataset_id_no_existe_lanza_value_error():
    cur = _FakeCursor()
    with pytest.raises(ValueError):
        stats_compute.lookup_dataset_id(cur, "no_existe")


# ---------------------------------------------------------------------------
# compute_top_items: 3 queries (DELETE, INSERT subs, INSERT patterns)
# ---------------------------------------------------------------------------

def test_compute_top_items_inserta_substring_y_pattern():
    cur = _FakeCursor()
    cur.rowcounts = iter([1, 80, 60])
    n_subs, n_pat = stats_compute.compute_top_items(cur, dataset_id=3, top_n=100)
    assert n_subs == 80
    assert n_pat == 60
    assert "DELETE FROM stats.top_items" in cur.queries[0][0]
    assert "'substring'" in cur.queries[1][0]
    assert "'pattern'" in cur.queries[2][0]
    assert cur.queries[1][1] == (3, 3, 100)
    assert cur.queries[2][1] == (3, 3, 100)


# ---------------------------------------------------------------------------
# compute_global_summary
# ---------------------------------------------------------------------------

def test_compute_global_summary_sin_length_histogram_no_inserta():
    """Sin filas en stats.length_histogram, no hay base estadística."""
    cur = _FakeCursor()
    cur.responses_by_table["stats.length_histogram"] = []
    stats_compute.compute_global_summary(cur, dataset_id=99)
    # solo se ejecutó el SELECT de length_histogram; no hay charset ni entropy
    assert len(cur.queries) == 1
    assert "stats.length_histogram" in cur.queries[0][0]


def test_compute_global_summary_con_datos_emite_delete_e_insert():
    """Con histogramas poblados, deriva sample_size + porcentajes + percentiles."""
    cur = _FakeCursor()
    # length_histogram: 1000 contraseñas, longitudes 6-12
    cur.responses_by_table["stats.length_histogram"] = [
        (6, 100), (7, 200), (8, 400), (9, 200), (12, 100),
    ]
    # charset_histogram: mascaras observadas
    #   1=lower, 4=digit, 5=lower+digit, 12=digit+symbol, 7=lower+upper+digit
    cur.responses_by_table["stats.charset_histogram"] = [
        (4, 200),    # solo dígitos
        (1, 100),    # solo letras
        (3, 50),     # letras (lower+upper)
        (5, 600),    # alfanumérico (lower+digit)
        (12, 50),    # con símbolo
    ]
    # entropy_histogram
    cur.responses_by_table["stats.entropy_histogram"] = [
        ("shannon", 2.0, 100),
        ("shannon", 2.5, 200),
        ("shannon", 3.0, 700),
        ("charset", 30.0, 1000),
    ]
    stats_compute.compute_global_summary(cur, dataset_id=5)

    qs = [q[0] for q in cur.queries]
    # secuencia esperada
    assert any("stats.length_histogram" in q for q in qs)
    assert any("stats.charset_histogram" in q for q in qs)
    assert any("stats.entropy_histogram" in q for q in qs)
    assert any("DELETE FROM stats.global_summary" in q for q in qs)
    assert any("INSERT INTO stats.global_summary" in q for q in qs)

    # Verifica que los parámetros del INSERT son coherentes
    insert_query = next(q for q in cur.queries if "INSERT INTO stats.global_summary" in q[0])
    params = insert_query[1]
    assert params["sample_size"] == 1000
    # avg_length = (6*100+7*200+8*400+9*200+12*100)/1000 = 8.2
    assert abs(params["avg_length"] - 8.2) < 1e-9
    assert params["min_length"] == 6
    assert params["max_length"] == 12
    # pct_digits_only = 200/1000 = 20%
    assert params["pct_digits_only"] == 20.0
    # pct_letters_only = (100+50)/1000 = 15%
    assert params["pct_letters_only"] == 15.0
    # pct_alphanumeric: 200+100+50+600 = 950, 95%
    assert params["pct_alphanumeric"] == 95.0
    # pct_with_symbols: 50, 5%
    assert params["pct_with_symbols"] == 5.0




def test_compute_length_histogram_eliminada():
    """`length_histogram` la puebla el ETL en línea; este módulo no la toca."""
    assert not hasattr(stats_compute, "compute_length_histogram")
