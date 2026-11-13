"""Leitura de arquivos do kernel, que podem sumir ou falhar a qualquer momento."""

from pathlib import Path


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def read_int(path):
    text = read_text(path)
    try:
        return int(text)
    except (TypeError, ValueError):
        return None
