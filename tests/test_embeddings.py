"""Tests for the local embedder (``devx.token_optim.embeddings``).

The ``sentence_transformers`` package is optional, so these tests inject a
fake module into ``sys.modules`` to exercise both the installed and the
missing-dependency paths offline.
"""

from __future__ import annotations

import sys
import types

import pytest

from devx.token_optim.embeddings import EmbeddingDependencyError, LocalEmbedder


def make_fake_st_module(cls) -> types.ModuleType:
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = cls
    return module


class FakeVectors:
    def __init__(self, rows):
        self._rows = rows

    def tolist(self):
        return self._rows


class FakeModel:
    instances: list = []

    def __init__(self, model_name, **kwargs):
        self.model_name = model_name
        self.kwargs = kwargs
        FakeModel.instances.append(self)

    def encode(self, texts, **kwargs):
        return FakeVectors([[0.25, 0.75] for _ in texts])


class TypeErrorThenOk:
    calls = 0
    last_kwargs = None

    def __init__(self, model_name, **kwargs):
        type(self).calls += 1
        if type(self).calls == 1:
            raise TypeError("first call fails")
        self.kwargs = kwargs
        type(self).last_kwargs = kwargs


class OfflineTypeError:
    instances = []

    def __init__(self, model_name, **kwargs):
        if kwargs.get("local_files_only"):
            raise TypeError("offline unsupported")
        self.kwargs = kwargs
        OfflineTypeError.instances.append(self)


def test_missing_dependency_raises(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", types.ModuleType("sentence_transformers")
    )
    with pytest.raises(EmbeddingDependencyError, match="Install the embeddings extra"):
        LocalEmbedder("test-model")


def test_constructs_with_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sentence_transformers", make_fake_st_module(FakeModel))
    FakeModel.instances.clear()
    embedder = LocalEmbedder("test-model", cache_dir=tmp_path / "cache")
    assert (tmp_path / "cache").is_dir()
    instance = FakeModel.instances[-1]
    assert instance.model_name == "test-model"
    assert instance.kwargs["device"] == "cpu"
    assert instance.kwargs["cache_folder"] == str(tmp_path / "cache")
    assert embedder.identity == "test-model"


def test_embed_returns_rows(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", make_fake_st_module(FakeModel))
    FakeModel.instances.clear()
    embedder = LocalEmbedder("test-model")
    assert embedder.embed(["a", "b"]) == [[0.25, 0.75], [0.25, 0.75]]


def test_embed_empty_returns_empty_list(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", make_fake_st_module(FakeModel))
    FakeModel.instances.clear()
    embedder = LocalEmbedder("test-model")
    assert embedder.embed([]) == []


def test_local_files_only_passes_kwarg(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", make_fake_st_module(FakeModel))
    FakeModel.instances.clear()
    LocalEmbedder("test-model", local_files_only=True)
    assert FakeModel.instances[-1].kwargs.get("local_files_only") is True


def test_typeerror_fallback_retries_without_flag(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", make_fake_st_module(TypeErrorThenOk)
    )
    TypeErrorThenOk.calls = 0
    TypeErrorThenOk.last_kwargs = None
    embedder = LocalEmbedder("test-model")
    assert embedder.identity == "test-model"
    assert TypeErrorThenOk.calls == 2
    assert TypeErrorThenOk.last_kwargs == {"device": "cpu"}


def test_offline_mode_raises_when_unsupported(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", make_fake_st_module(OfflineTypeError)
    )
    OfflineTypeError.instances.clear()
    with pytest.raises(RuntimeError, match="cannot enforce offline loading"):
        LocalEmbedder("test-model", local_files_only=True)
    assert OfflineTypeError.instances == []