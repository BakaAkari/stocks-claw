import json

import pytest

from stocks.engine.atomic_io import atomic_write_json


def test_atomic_write_replaces_content(tmp_path):
    path = tmp_path / "nested" / "assets.json"
    atomic_write_json(path, [{"名称": "沪深300"}])
    atomic_write_json(path, [{"名称": "黄金"}])
    assert json.loads(path.read_text(encoding="utf-8")) == [{"名称": "黄金"}]
    assert "黄金" in path.read_text(encoding="utf-8")  # ensure_ascii=False
    assert [p.name for p in path.parent.iterdir()] == ["assets.json"]


def test_failed_write_keeps_previous_file(tmp_path):
    path = tmp_path / "assets.json"
    atomic_write_json(path, {"v": 1})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"v": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["assets.json"]
