import src.ui.app as app


class _Recorder:
    """Stands in for the streamlit module and records what got rendered."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, " ".join(str(a) for a in args)))
            return self

        return call

    def columns(self, spec):
        return [self] * (spec if isinstance(spec, int) else len(spec))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _render(report: dict, monkeypatch) -> list[tuple[str, str]]:
    rec = _Recorder()
    monkeypatch.setattr(app, "st", rec)
    app.render_report(report)
    return rec.calls


def test_a_degraded_report_says_so_in_the_ui(monkeypatch):
    base = {"company_name": "X", "investment_signal": "HOLD", "confidence_score": 0.4}

    degraded = _render({**base, "degraded": True}, monkeypatch)
    assert any(name == "warning" and "degraded" in text.lower() for name, text in degraded)

    clean = _render({**base, "degraded": False}, monkeypatch)
    assert not any(name == "warning" for name, _ in clean)
