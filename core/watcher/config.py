"""Configuração tipada do Watcher V2."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WatcherV2Config:
    input_root: Path
    output_root: Path
    staging_root: Path
    runtime_root: Path
    mode: str                        # shadow | controlled | production
    classifier_enabled: bool

    # Limites operacionais
    estabilidade_intervalo_s: float = 10.0
    estabilidade_tentativas: int = 2
    max_pdfs_por_ciclo: int = 50
    max_erros_consecutivos: int = 3

    @property
    def pasta_digitadas(self) -> Path:
        return self.output_root / "Digitadas"

    @property
    def pasta_investigar(self) -> Path:
        return self.output_root / "Investigar"

    @property
    def pasta_investigar_classificacao(self) -> Path:
        return self.pasta_investigar / "Classificacao"

    @property
    def pasta_investigar_tipo_nao_suportado(self) -> Path:
        return self.pasta_investigar / "Tipo_Nao_Suportado"

    @property
    def pasta_investigar_desconhecida(self) -> Path:
        return self.pasta_investigar / "Concessionaria_Desconhecida"

    @property
    def pasta_investigar_duplicado(self) -> Path:
        return self.pasta_investigar / "Duplicado_Em_Producao"

    @property
    def pasta_investigar_erros(self) -> Path:
        return self.pasta_investigar / "Erros"

    @property
    def pasta_existiam_consen(self) -> Path:
        return self.output_root / "Ja_existiam_no_Consen"

    # ── Novos destinos específicos ─────────────────────────────────────────────

    @property
    def pasta_uc_nao_cadastrada(self) -> Path:
        return self.output_root / "Watcher_V2" / "UC_Nao_Cadastrada"

    @property
    def pasta_falso_salvamento(self) -> Path:
        return self.output_root / "Watcher_V2" / "Falso_Salvamento"

    @property
    def pasta_salvamento_incompleto(self) -> Path:
        return self.output_root / "Watcher_V2" / "Salvamento_Incompleto"

    @property
    def pasta_historico(self) -> Path:
        return self.output_root / "Historico"

    @property
    def pasta_relatorios(self) -> Path:
        return self.output_root / "Relatorios"

    @property
    def estado_db(self) -> Path:
        return self.runtime_root / "estado_v2.json"

    @property
    def hash_index(self) -> Path:
        return self.runtime_root / "hashes_conhecidos.json"

    @property
    def lock_proprio(self) -> Path:
        return self.runtime_root / "watcher_v2.lock"

    def criar_estrutura(self) -> None:
        """Cria todas as pastas de saída necessárias (idempotente)."""
        dirs = [
            self.pasta_digitadas,
            self.pasta_investigar_classificacao,
            self.pasta_investigar_tipo_nao_suportado,
            self.pasta_investigar_desconhecida,
            self.pasta_investigar_duplicado,
            self.pasta_investigar_erros,
            self.pasta_existiam_consen,
            self.pasta_uc_nao_cadastrada,
            self.pasta_falso_salvamento,
            self.pasta_salvamento_incompleto,
            self.pasta_historico,
            self.pasta_relatorios,
            self.runtime_root,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


_NET_INPUT  = r"\\10.10.250.21\Energia\CONTASDEENERGIAELETRICA\BB\ENZO\Faturas_V2"
_NET_OUTPUT = r"\\10.10.250.21\Energia\CONTASDEENERGIAELETRICA\BB\ENZO\Watcher_V2"
_NET_STAGING = r"\\10.10.250.21\Energia\ARQUIVOS ENZO\watcher_v2\staging"
_LOCAL_RUNTIME = Path(__file__).resolve().parents[2] / "runtime" / "watcher_v2"


def carregar_config() -> WatcherV2Config:
    """Lê configuração de variáveis de ambiente com defaults documentados."""
    return WatcherV2Config(
        input_root=Path(os.environ.get("WATCHER_V2_INPUT_ROOT", _NET_INPUT)),
        output_root=Path(os.environ.get("WATCHER_V2_OUTPUT_ROOT", _NET_OUTPUT)),
        staging_root=Path(os.environ.get("WATCHER_V2_STAGING_ROOT", _NET_STAGING)),
        runtime_root=Path(os.environ.get("WATCHER_V2_RUNTIME_ROOT", str(_LOCAL_RUNTIME))),
        mode=os.environ.get("WATCHER_V2_MODE", "shadow"),
        classifier_enabled=os.environ.get("WATCHER_V2_CLASSIFIER_ENABLED", "true").lower() != "false",
        estabilidade_intervalo_s=float(os.environ.get("WATCHER_V2_ESTABILIDADE_S", "10")),
        estabilidade_tentativas=int(os.environ.get("WATCHER_V2_ESTABILIDADE_TENTATIVAS", "2")),
        max_pdfs_por_ciclo=int(os.environ.get("WATCHER_V2_MAX_PDFS_CICLO", "50")),
        max_erros_consecutivos=int(os.environ.get("WATCHER_V2_MAX_ERROS_CONSECUTIVOS", "3")),
    )
