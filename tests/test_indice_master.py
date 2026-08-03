def test_filelock_capability_is_exported_to_downloaders():
    from core import indice_master
    from scripts.infra import indice_master as legacy

    assert indice_master._FILELOCK_OK is legacy._FILELOCK_OK
    assert indice_master._FILELOCK_OK is True


def test_root_shim_exposes_real_filelock_state():
    import indice_master
    from scripts.infra import indice_master as implementation

    assert indice_master._FILELOCK_OK is implementation._FILELOCK_OK
