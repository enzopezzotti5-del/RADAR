from core.downloaders.cpfl.cpfl_rge_base import autonomous_batch_exit_code


def test_batch_success_requires_completed_work():
    assert autonomous_batch_exit_code(candidates=2, completed=2, technical_errors=0) == 0


def test_batch_no_input_is_skipped():
    assert autonomous_batch_exit_code(candidates=0, completed=0, technical_errors=0) == 3


def test_batch_technical_error_cannot_be_masked_by_partial_success():
    assert autonomous_batch_exit_code(candidates=2, completed=1, technical_errors=1) == 1
