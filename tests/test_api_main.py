"""Network-free unit tests for the multi-worker startup guard in
src/api/main.py (item 32) — jobs, the response cache, and the rate limiter
are all in-process dicts, correct only under exactly one worker."""

import logging

import src.api.main as main_module


def test_single_worker_is_silent(monkeypatch, caplog):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr(main_module.sys, "argv", ["uvicorn", "src.api.main:app", "--workers", "1"])
    with caplog.at_level(logging.CRITICAL, logger=main_module.logger.name):
        main_module._check_single_worker()
    assert caplog.records == []


def test_multi_worker_flag_logs_critical(monkeypatch, caplog):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr(main_module.sys, "argv", ["uvicorn", "src.api.main:app", "--workers", "4"])
    with caplog.at_level(logging.CRITICAL, logger=main_module.logger.name):
        main_module._check_single_worker()
    assert len(caplog.records) == 1
    assert "workers" in caplog.records[0].getMessage()


def test_web_concurrency_env_logs_critical(monkeypatch, caplog):
    monkeypatch.setattr(main_module.sys, "argv", ["uvicorn"])
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    with caplog.at_level(logging.CRITICAL, logger=main_module.logger.name):
        main_module._check_single_worker()
    assert len(caplog.records) == 1
