from scripts.infra import indice_master as _indice_master_impl
from scripts.infra.indice_master import *  # noqa: F401,F403

# O import estrela omite nomes privados. Downloaders legados consultam este
# indicador somente para relatar o estado real do lock compartilhado.
_FILELOCK_OK = _indice_master_impl._FILELOCK_OK
