"""Tests del detector de palabras de diccionario."""
from __future__ import annotations

from passeval.strength.patterns.dictionary import (
    clear_cache,
    load_dictionaries,
    match_dictionary,
)


def _write_dict(path, words, header="# fuente: test"):
    path.write_text(header + "\n" + "\n".join(words) + "\n", encoding="utf-8")


def test_load_dictionaries_lee_orden_como_rank(tmp_path):
    clear_cache()
    _write_dict(tmp_path / "common.txt", ["password", "admin", "qwerty"])
    dicts = load_dictionaries(tmp_path)
    assert "common" in dicts
    assert dicts["common"]["password"] == 0
    assert dicts["common"]["admin"] == 1
    assert dicts["common"]["qwerty"] == 2


def test_load_ignora_comentarios_y_lineas_vacias(tmp_path):
    clear_cache()
    (tmp_path / "x.txt").write_text(
        "# fuente: foo, fecha: 2026-04-21\n\nuno\n\ndos\n", encoding="utf-8"
    )
    dicts = load_dictionaries(tmp_path)
    assert dicts["x"] == {"uno": 0, "dos": 1}


def test_match_dictionary_encuentra_subcadena(tmp_path):
    clear_cache()
    _write_dict(tmp_path / "es.txt", ["hola", "casa", "amor"])
    dicts = load_dictionaries(tmp_path)
    matches = match_dictionary("HolaMundo", dicts)
    tokens = [(m.token, m.metadata["matched_word"]) for m in matches]
    assert ("Hola", "hola") in tokens


def test_match_case_insensitive_pero_token_preserva(tmp_path):
    clear_cache()
    _write_dict(tmp_path / "x.txt", ["password"])
    dicts = load_dictionaries(tmp_path)
    matches = match_dictionary("PASSWORD", dicts)
    assert len(matches) == 1
    assert matches[0].token == "PASSWORD"
    assert matches[0].metadata["matched_word"] == "password"


def test_match_falso_positivo_controlado(tmp_path):
    # "x" sola no debe coincidir aunque "x" no esté en el diccionario.
    clear_cache()
    _write_dict(tmp_path / "x.txt", ["hola"])
    dicts = load_dictionaries(tmp_path)
    assert match_dictionary("zzz", dicts) == []


def test_match_dictionary_sin_diccionarios_devuelve_lista_vacia():
    assert match_dictionary("cualquiercosa", {}) == []


def test_directorio_inexistente_devuelve_dict_vacio(tmp_path):
    clear_cache()
    dicts = load_dictionaries(tmp_path / "no-existe")
    assert dicts == {}


def test_rank_se_propaga_a_metadata(tmp_path):
    clear_cache()
    _write_dict(tmp_path / "x.txt", ["a", "ba", "cba"])  # rank 0,1,2
    dicts = load_dictionaries(tmp_path)
    matches = match_dictionary("xcbax", dicts)
    cba = [m for m in matches if m.metadata["matched_word"] == "cba"]
    assert len(cba) == 1
    assert cba[0].metadata["rank"] == 2
