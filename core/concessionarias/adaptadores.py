from __future__ import annotations
import sys
from pathlib import Path
from .modelos import ContextoExecucao, PipelineSpec
def comando_legado(spec:PipelineSpec, ctx:ContextoExecucao)->list[str]:
    if spec.script is None: return []
    cmd=[sys.executable,str(spec.script),"--mes",ctx.mes,"--ano",ctx.ano,*spec.argumentos]
    if spec.aceita_pasta: cmd += ["--pasta",str(ctx.pasta_entrada)]
    if spec.aceita_session_root: cmd += ["--session-root",str(ctx.session_root)]
    if ctx.retomar and spec.aceita_retomar: cmd.append("--retomar")
    if ctx.dry_run and spec.aceita_dry_run: cmd.append("--dry-run")
    return cmd
