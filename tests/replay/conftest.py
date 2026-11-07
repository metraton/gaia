"""Registers the ``replay`` marker that selects the validator replay, which the default suite skips."""


def pytest_configure(config):
    config.addinivalue_line("markers", "replay: old-vs-new validator replay over real history; run with -m replay")
