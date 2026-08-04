from core.downloaders.copel.copel_bt import autonomous_exit_code as bt_exit
from core.downloaders.copel.copel_mt import autonomous_exit_code as mt_exit


def test_copel_download_is_success():
    assert bt_exit(1, 0) == 0
    assert mt_exit(1, 0) == 0


def test_copel_no_new_invoice_is_no_input():
    assert bt_exit(0, 0) == 3
    assert mt_exit(0, 0) == 3


def test_copel_technical_error_is_failure():
    assert bt_exit(1, 1) == 1
    assert mt_exit(0, 1) == 1
