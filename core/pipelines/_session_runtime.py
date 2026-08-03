from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Garante que ENERGIA/ esteja no sys.path antes de qualquer import `core.*`,
# necessário quando o módulo é importado diretamente a partir de core/pipelines/
# (execução via `python core/pipelines/pipeline_*.py`).
_PROJECT_ROOT_EARLY = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_EARLY) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_EARLY))

from core.sessao_meta import PipelineSessao

PROJECT_ROOT = _PROJECT_ROOT_EARLY
CORE_DIR = PROJECT_ROOT / "core"
PIPELINE_SESSION_ROOT = Path(
    os.environ.get("ENERGIA_PIPELINE_SESSION_ROOT", str(PROJECT_ROOT / "logs" / "pipeline_sessoes"))
)


@dataclass(frozen=True)
class PipelineProfile:
    script_name: str
    concessionaria: str
    grupo: str
    native_session: bool = False


def _profile(script_name: str, concessionaria: str, grupo: str, *, native_session: bool = False) -> PipelineProfile:
    return PipelineProfile(
        script_name=script_name,
        concessionaria=concessionaria,
        grupo=grupo,
        native_session=native_session,
    )


PIPELINE_PROFILES: dict[str, PipelineProfile] = {
    "pipeline_ceee_bt.py": _profile("pipeline_ceee_bt.py", "CEEE", "BT"),
    "pipeline_celesc.py": _profile("pipeline_celesc.py", "CELESC", "BT_MT"),
    "pipeline_celesc_bt.py": _profile("pipeline_celesc_bt.py", "CELESC", "BT"),
    "pipeline_celesc_mt.py": _profile("pipeline_celesc_mt.py", "CELESC", "MT"),
    "pipeline_cemig.py": _profile("pipeline_cemig.py", "CEMIG", "BT"),
    "pipeline_cemig_190.py": _profile("pipeline_cemig_190.py", "CEMIG", "BT"),
    "pipeline_chesp_bt.py": _profile("pipeline_chesp_bt.py", "CHESP", "BT"),
    "pipeline_chesp_mt.py": _profile("pipeline_chesp_mt.py", "CHESP", "MT"),
    "pipeline_copel_bt.py": _profile("pipeline_copel_bt.py", "COPEL", "BT"),
    "pipeline_copel_mt.py": _profile("pipeline_copel_mt.py", "COPEL", "MT"),
    "pipeline_copel_mt_recuperacao.py": _profile("pipeline_copel_mt_recuperacao.py", "COPEL", "MT"),
    "pipeline_cpfl_bt.py": _profile("pipeline_cpfl_bt.py", "CPFL", "BT"),
    "pipeline_cpfl_mt.py": _profile("pipeline_cpfl_mt.py", "CPFL", "MT"),
    "pipeline_demei_bt.py": _profile("pipeline_demei_bt.py", "DEMEI", "BT"),
    "pipeline_edp_es_bt.py": _profile("pipeline_edp_es_bt.py", "EDP", "BT"),
    "pipeline_edp_sp_bt.py": _profile("pipeline_edp_sp_bt.py", "EDP", "BT"),
    "pipeline_enel.py": _profile("pipeline_enel.py", "ENEL", "BT_MT"),
    "pipeline_enel_erros.py": _profile("pipeline_enel_erros.py", "ENEL", "BT"),
    "pipeline_enel_faltantes.py": _profile("pipeline_enel_faltantes.py", "ENEL", "BT"),
    "pipeline_enel_faltantes_bte_corrigidas.py": _profile("pipeline_enel_faltantes_bte_corrigidas.py", "ENEL", "BT"),
    "pipeline_enel_mt_lote.py": _profile("pipeline_enel_mt_lote.py", "ENEL", "MT"),
    "pipeline_energisa_bt.py": _profile("pipeline_energisa_bt.py", "ENERGISA", "BT"),
    "pipeline_energisa_mt.py": _profile("pipeline_energisa_mt.py", "ENERGISA", "MT"),
    "pipeline_equatorial_al_bt.py": _profile("pipeline_equatorial_al_bt.py", "EQUATORIAL", "BT"),
    "pipeline_equatorial_ap_bt.py": _profile("pipeline_equatorial_ap_bt.py", "EQUATORIAL", "BT"),
    "pipeline_equatorial_go.py": _profile("pipeline_equatorial_go.py", "EQUATORIAL", "BT_MT", native_session=True),
    "pipeline_equatorial_go_mt.py": _profile("pipeline_equatorial_go_mt.py", "EQUATORIAL", "MT"),
    "pipeline_equatorial_ma_bt.py": _profile("pipeline_equatorial_ma_bt.py", "EQUATORIAL", "BT"),
    "pipeline_equatorial_ma_mt.py": _profile("pipeline_equatorial_ma_mt.py", "EQUATORIAL", "MT"),
    "pipeline_equatorial_pa_bt.py": _profile("pipeline_equatorial_pa_bt.py", "EQUATORIAL", "BT"),
    "pipeline_equatorial_pa_mt.py": _profile("pipeline_equatorial_pa_mt.py", "EQUATORIAL", "MT"),
    "pipeline_equatorial_pi_bt.py": _profile("pipeline_equatorial_pi_bt.py", "EQUATORIAL", "BT"),
    "pipeline_equatorial_pi_mt.py": _profile("pipeline_equatorial_pi_mt.py", "EQUATORIAL", "MT"),
    "pipeline_light_bt.py": _profile("pipeline_light_bt.py", "LIGHT", "BT"),
    "pipeline_light_mt.py": _profile("pipeline_light_mt.py", "LIGHT", "MT"),
    "pipeline_lote_bt.py": _profile("pipeline_lote_bt.py", "MULTI", "BT", native_session=True),
    "pipeline_neoenergia_bahia.py": _profile("pipeline_neoenergia_bahia.py", "NEOENERGIA", "BT"),
    "pipeline_neoenergia_ceb_bt.py": _profile("pipeline_neoenergia_ceb_bt.py", "NEOENERGIA", "BT"),
    "pipeline_neoenergia_cosern.py": _profile("pipeline_neoenergia_cosern.py", "NEOENERGIA", "BT"),
    "pipeline_neoenergia_elektro.py": _profile("pipeline_neoenergia_elektro.py", "NEOENERGIA", "BT_MT"),
    "pipeline_neoenergia_pernambuco.py": _profile("pipeline_neoenergia_pernambuco.py", "NEOENERGIA", "BT"),
    "pipeline_pequenas_bt.py": _profile("pipeline_pequenas_bt.py", "PEQUENAS", "BT"),
    "pipeline_producao_bt.py": _profile("pipeline_producao_bt.py", "PRODUCAO", "BT"),
    "pipeline_producao_enzo.py": _profile("pipeline_producao_enzo.py", "PRODUCAO", "BT_MT"),
    "pipeline_producao_equatorial_go.py": _profile("pipeline_producao_equatorial_go.py", "EQUATORIAL", "BT_MT"),
    "pipeline_rge_sul_bt.py": _profile("pipeline_rge_sul_bt.py", "RGE", "BT"),
}


PIPELINE_SESSION_AWARE = {
    name for name, profile in PIPELINE_PROFILES.items() if profile.native_session
}


def iter_pipeline_scripts() -> Iterable[str]:
    return PIPELINE_PROFILES.keys()


def resolve_profile(script: str | Path) -> PipelineProfile | None:
    name = Path(script).name.lower()
    return PIPELINE_PROFILES.get(name)


def is_pipeline_script(script: str | Path) -> bool:
    return resolve_profile(script) is not None


def is_native_session_script(script: str | Path) -> bool:
    profile = resolve_profile(script)
    return bool(profile and profile.native_session)


def pipeline_session_root(script: str | Path) -> Path:
    profile = resolve_profile(script)
    stem = Path(script).stem
    if profile is None:
        return PIPELINE_SESSION_ROOT / stem
    return PIPELINE_SESSION_ROOT / profile.script_name.removesuffix(".py")


def infer_mes_ano(argv: list[str] | None = None) -> tuple[str, str]:
    argv = list(argv or sys.argv[1:])
    mes = None
    ano = None
    for i, token in enumerate(argv):
        if token == "--mes" and i + 1 < len(argv):
            mes = argv[i + 1]
        elif token == "--ano" and i + 1 < len(argv):
            ano = argv[i + 1]
        elif token.startswith("--mes="):
            mes = token.split("=", 1)[1]
        elif token.startswith("--ano="):
            ano = token.split("=", 1)[1]
    hoje = dt.date.today()
    return f"{int(mes):02d}" if mes else f"{hoje.month:02d}", str(int(ano)) if ano else str(hoje.year)


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def maybe_wrap_pipeline_command(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd

    python_idx = next((i for i, token in enumerate(cmd) if Path(token).suffix.lower() == ".py"), None)
    if python_idx is None:
        return cmd

    script = Path(cmd[python_idx])
    if script.name.lower() == "session_runner.py":
        return cmd
    profile = resolve_profile(script)
    if profile is None or profile.native_session:
        return cmd

    wrapper = Path(__file__).resolve().with_name("session_runner.py")
    return cmd[:python_idx] + [str(wrapper), "--script", str(script), "--"] + cmd[python_idx + 1 :]


def _drain_stream(stream, print_fn) -> None:
    try:
        for linha in iter(stream.readline, ""):
            linha = linha.rstrip()
            if linha:
                print_fn(linha)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run_child_process(
    cmd: list[str],
    *,
    env_extra: dict[str, str] | None = None,
    print_fn=print,
    warn_fn=print,
) -> int:
    env = _child_env(env_extra)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, print_fn), daemon=True)
    t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, lambda line: warn_fn(f"[ERR] {line}")), daemon=True)
    t_out.start()
    t_err.start()

    interrupted = False
    try:
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
            except KeyboardInterrupt:
                if proc.poll() is not None:
                    break
                interrupted = True
                warn_fn("Interrompido manualmente. Encerrando subprocesso...")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                break
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=5)

    if interrupted:
        return 130
    return int(proc.returncode or 0)


def executar_pipeline_com_sessao(script: str | Path, argv: list[str] | None = None) -> int:
    script_path = Path(script)
    profile = resolve_profile(script_path)
    if profile is None:
        raise ValueError(f"Pipeline não registrado: {script_path}")

    argv = list(argv or [])
    if profile.native_session:
        return _run_child_process(
            [sys.executable, "-u", str(script_path), *argv],
            print_fn=print,
            warn_fn=print,
        )

    mes, ano = infer_mes_ano(argv)
    staging_root = pipeline_session_root(script_path)
    with PipelineSessao(
        staging_root,
        conc=profile.concessionaria,
        grupo=profile.grupo,
        mes=mes,
        ano=ano,
        arquivos=[],
    ) as sess:
        sess.etapa("execucao", "em_execucao", script=str(script_path), argumentos=list(argv))
        try:
            rc = _run_child_process(
                [sys.executable, "-u", str(script_path), *argv],
                print_fn=print,
                warn_fn=print,
            )
            if rc == 0:
                sess.etapa("execucao", "ok", rc=rc)
                return rc
            if rc == 130:
                raise KeyboardInterrupt()
            sess.etapa("execucao", "erro", rc=rc)
            raise subprocess.CalledProcessError(rc, [sys.executable, "-u", str(script_path), *argv])
        except KeyboardInterrupt:
            sess.status("interrompido", retomavel=True, motivo="Interrompido pelo usuário")
            raise
        except Exception as exc:
            sess.status("erro", retomavel=False, motivo=str(exc)[:500])
            raise


def build_session_command(cmd: list[str]) -> list[str]:
    return maybe_wrap_pipeline_command(cmd)
