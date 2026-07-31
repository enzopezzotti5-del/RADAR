# Downloaders

| Concessionária | Entry point | Tecnologia/configuração |
|---|---|---|
| CPFL/RGE | `core/downloaders/cpfl/*.py` | browser e índices locais |
| CEMIG | `core/downloaders/cemig/cemig.py` | Selenium/browser |
| COPEL | `core/downloaders/copel/*.py` | browser; `COPEL_ACCESSOS_XLS_PATH` opcional |
| Neoenergia | `core/downloaders/neoenergia/worker_*.py` | browser/pipelines |
| ENEL | `core/downloaders/enel_*/*.py` | browser/API conforme distribuidora |
| CELESC | `core/downloaders/celesc/*.py` | browser |
| Equatorial GO | `core/downloaders/equatorial_go/equatorial_goias.py` | browser |
| Light | `core/downloaders/light_rj/_test_login.py` | browser |
| Energisa | `core/downloaders/energisa/*.py` | portal e IMAP |

Entradas e saídas específicas são declaradas no catálogo. Credenciais, perfis
Chrome, cookies, índices e faturas são runtime local e não pertencem ao Git.
