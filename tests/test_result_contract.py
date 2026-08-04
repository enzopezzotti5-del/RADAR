from radar_v2.app.services.run_service import result_for_exit_code


def test_result_contract_success():
    assert result_for_exit_code(0) == ("success", "Concluído")


def test_result_contract_no_input():
    assert result_for_exit_code(3) == ("skipped", "Sem entrada")


def test_result_contract_real_failures_remain_errors():
    for code in (1, 2, 4, 127):
        assert result_for_exit_code(code) == ("error", "Falhou")
