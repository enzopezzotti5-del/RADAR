from pathlib import Path


CALENDAR = Path(__file__).resolve().parents[1] / "src" / "pages" / "Calendar.tsx"


def test_invoice_calendar_uses_only_download_operational_cards() -> None:
    source = CALENDAR.read_text(encoding="utf-8")
    for label in (
        "Faturas baixadas",
        "Execucoes concluidas",
        "Concessionarias com atividade",
    ):
        assert label in source
    assert "['Processadas'" not in source
    assert "['Puladas'" not in source
    assert "['Outros'" not in source


def test_invoice_calendar_hides_error_metrics_and_chart_series() -> None:
    source = CALENDAR.read_text(encoding="utf-8")
    invoice_section = source.split("{view === 'faturas' && !hasInvoiceMetricsForFilter", 1)[0]
    assert 'dataKey="errors"' not in invoice_section
    assert 'name="Erros"' not in invoice_section
    assert 'E {invoiceDay' not in invoice_section
    assert 'Downloads no mes' in source


def test_invoice_graphs_do_not_render_skipped_or_other_bars() -> None:
    source = CALENDAR.read_text(encoding="utf-8")
    assert 'dataKey="skipped_existing"' not in source
    assert 'dataKey="other" name="Outros"' not in source
