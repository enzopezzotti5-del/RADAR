"""Engine do Watcher V2 - processa um ciclo de PDFs."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from core.classificador.politica import DecisaoPolitica
from core.concessionarias.catalogo import REGISTRO, resolver_pipeline
from core.concessionarias.modelos import EstadoImplementacao, GrupoTensao

from .arquivos import calcular_sha256, esta_estavel, mover_seguro, varrer_pdfs
from .config import WatcherV2Config
from .estados import EstadoPDF
from .locks import adquirir_lock_global
from .resultados import RegistroProcessamento
from .roteamento import rotear

_RE_MES_ANO_LONGO = re.compile(r"(\d{2})[.\-_](\d{4})")
_RE_MES_ANO_CURTO = re.compile(r"\d{2}\.(\d{2})\.(\d{2})\b")


def _extrair_texto(pdf_path: Path) -> str:
    """Extrai texto do PDF. Retorna '' em falha."""
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as pdf:
            partes = []
            for pg in pdf.pages[:4]:
                texto = pg.extract_text()
                if texto:
                    partes.append(texto)
            return "\n".join(partes)
    except Exception:
        return ""


class WatcherV2Engine:
    def __init__(self, cfg: WatcherV2Config, python_exe: str | None = None):
        self.cfg = cfg
        self.python_exe = python_exe or sys.executable
        self._estado_db: dict = {}
        self._hash_index: set[str] = set()
        self._carregar_estado()

    def _carregar_estado(self) -> None:
        if self.cfg.estado_db.exists():
            try:
                self._estado_db = json.loads(self.cfg.estado_db.read_text("utf-8"))
            except Exception:
                self._estado_db = {}
        if self.cfg.hash_index.exists():
            try:
                data = json.loads(self.cfg.hash_index.read_text("utf-8"))
                self._hash_index = set(data.get("hashes", []))
            except Exception:
                self._hash_index = set()

    def _salvar_estado(self) -> None:
        self.cfg.runtime_root.mkdir(parents=True, exist_ok=True)
        self.cfg.estado_db.write_text(
            json.dumps(self._estado_db, ensure_ascii=False, indent=2), "utf-8"
        )
        self.cfg.hash_index.write_text(
            json.dumps({"hashes": sorted(self._hash_index)}, ensure_ascii=False), "utf-8"
        )

    def _registrar(self, reg: RegistroProcessamento) -> None:
        self._estado_db[reg.sha256] = reg.to_dict()
        self._hash_index.add(reg.sha256)

    def _ja_processado(self, sha: str) -> dict | None:
        return self._estado_db.get(sha)

    def consultar_por_sha(self, sha: str) -> dict | None:
        """Consulta somente leitura do estado persistido por SHA-256."""
        return self._ja_processado(sha)

    def _hidratar_registro(self, anterior: dict, *, sha: str, caminho_atual: Path) -> RegistroProcessamento:
        reg = RegistroProcessamento(
            arquivo_original=anterior.get("arquivo_original") or caminho_atual.name,
            caminho_original=anterior.get("caminho_original") or str(caminho_atual),
            sha256=sha,
        )
        reg.concessionaria_prevista = anterior.get("concessionaria_prevista")
        reg.confianca_concessionaria = float(anterior.get("confianca_concessionaria") or 0.0)
        reg.metodo_concessionaria = anterior.get("metodo_concessionaria") or ""
        reg.evidencia_concessionaria = anterior.get("evidencia_concessionaria") or ""
        reg.grupo_previsto = anterior.get("grupo_previsto")
        reg.confianca_grupo = float(anterior.get("confianca_grupo") or 0.0)
        reg.evidencias_grupo = list(anterior.get("evidencias_grupo") or [])
        reg.penalidades_grupo = list(anterior.get("penalidades_grupo") or [])
        reg.status_rotulagem = anterior.get("status_rotulagem") or ""
        reg.confianca_roteamento = float(anterior.get("confianca_roteamento") or 0.0)
        reg.decisao_politica = anterior.get("decisao_politica") or ""
        reg.estado_suporte = anterior.get("estado_suporte") or ""
        reg.pipeline_resolvido = anterior.get("pipeline_resolvido")
        reg.comando_planejado = list(anterior.get("comando_planejado") or [])
        reg.session_id = anterior.get("session_id")
        reg.staging = anterior.get("staging")
        reg.carimbo = anterior.get("carimbo")
        reg.destino = anterior.get("destino")
        reg.motivo_rejeicao = anterior.get("motivo_rejeicao") or ""
        reg.tentativas = int(anterior.get("tentativas") or 0)
        reg.timestamp_deteccao = anterior.get("timestamp_deteccao") or reg.timestamp_deteccao
        reg.timestamp_conclusao = anterior.get("timestamp_conclusao")
        estado_ant = anterior.get("estado")
        if estado_ant:
            try:
                reg.estado = EstadoPDF(estado_ant)
            except ValueError:
                reg.estado = EstadoPDF.ERRO
        return reg

    def _hash_em_producao(self, sha: str) -> bool:
        """Verifica se o hash ja existe na producao (indice local)."""
        return sha in self._hash_index

    def ciclo(self, arquivo_especifico: Path | None = None) -> list[RegistroProcessamento]:
        """Executa um ciclo de processamento. Retorna registros do ciclo."""
        if self.cfg.mode not in ("shadow", "controlled", "production"):
            raise ValueError(f"Modo desconhecido: {self.cfg.mode!r}")

        if arquivo_especifico:
            pdfs = [arquivo_especifico] if arquivo_especifico.exists() else []
        else:
            pdfs = varrer_pdfs(self.cfg.input_root)
            if self.cfg.mode == "controlled":
                raise RuntimeError(
                    "Modo 'controlled' exige --arquivo explicito. "
                    "Use: watcher_v2.py --arquivo <caminho>"
                )

        resultados: list[RegistroProcessamento] = []
        for pdf in pdfs[: self.cfg.max_pdfs_por_ciclo]:
            reg = self._processar_pdf(pdf)
            resultados.append(reg)
            self._registrar(reg)

        self._salvar_estado()
        return resultados

    def _processar_pdf(self, pdf: Path) -> RegistroProcessamento:
        reg = RegistroProcessamento(
            arquivo_original=pdf.name,
            caminho_original=str(pdf),
            sha256="",
        )

        try:
            reg.sha256 = calcular_sha256(pdf)
        except OSError as exc:
            reg.estado = EstadoPDF.ERRO
            reg.motivo_rejeicao = f"Erro ao calcular SHA: {exc}"
            return reg

        anterior = self._ja_processado(reg.sha256)
        if anterior:
            estado_ant = anterior.get("estado", "")
            if estado_ant in (EstadoPDF.PIPELINE_CONCLUIDO.value, EstadoPDF.REVISAO_MANUAL.value):
                reg.estado = EstadoPDF.DUPLICADO
                reg.motivo_rejeicao = f"ja processado: estado={estado_ant}"
                if self.cfg.mode != "shadow":
                    self._mover_para(pdf, self.cfg.pasta_investigar_duplicado, reg)
                return reg
            if estado_ant in (
                EstadoPDF.ERRO.value,
                EstadoPDF.STAGING_CRIADO.value,
                EstadoPDF.PIPELINE_INICIADO.value,
            ):
                reg = self._hidratar_registro(anterior, sha=reg.sha256, caminho_atual=pdf)
                reg.estado = EstadoPDF.ERRO
                reg.arquivo_original = pdf.name
                reg.caminho_original = str(pdf)
                reg.motivo_rejeicao = (
                    f"sessao anterior preservada: estado={estado_ant}; "
                    "retomar ou reconciliar sem novo carimbo"
                )
                return reg

        reg.estado = EstadoPDF.AGUARDANDO_ESTABILIDADE
        if self.cfg.mode != "shadow":
            if not esta_estavel(
                pdf,
                self.cfg.estabilidade_intervalo_s,
                self.cfg.estabilidade_tentativas,
            ):
                reg.motivo_rejeicao = "arquivo instavel (ainda sendo copiado)"
                return reg

        texto = _extrair_texto(pdf)
        if not texto or len(texto) < 30:
            reg.estado = EstadoPDF.ERRO
            reg.motivo_rejeicao = "texto insuficiente (PDF ilegivel ou protegido)"
            return reg

        roteamento = rotear(texto, arquivo=pdf.name)

        reg.concessionaria_prevista = roteamento.concessionaria.canonica
        reg.confianca_concessionaria = roteamento.concessionaria.confianca
        reg.metodo_concessionaria = roteamento.concessionaria.metodo
        reg.evidencia_concessionaria = roteamento.concessionaria.evidencia
        reg.grupo_previsto = roteamento.grupo.value if roteamento.grupo else None
        reg.confianca_grupo = roteamento.confianca_grupo
        reg.evidencias_grupo = list(roteamento.evidencias_grupo)
        reg.penalidades_grupo = list(roteamento.penalidades_grupo)
        reg.status_rotulagem = roteamento.status_rotulagem
        reg.confianca_roteamento = roteamento.politica.confianca_roteamento
        reg.decisao_politica = roteamento.politica.decisao.value
        reg.estado_suporte = roteamento.estado_suporte
        reg.pipeline_resolvido = roteamento.pipeline_script
        reg.comando_planejado = roteamento.comando
        reg.estado = EstadoPDF.CLASSIFICADO

        decisao = roteamento.politica.decisao
        motivo_politica = (roteamento.politica.motivo or "").lower()

        if decisao == DecisaoPolitica.DESCONHECIDO or not roteamento.concessionaria.canonica:
            reg.estado = EstadoPDF.CONCESSIONARIA_DESCONHECIDA
            reg.motivo_rejeicao = roteamento.politica.motivo
            if self.cfg.mode != "shadow":
                self._mover_para(pdf, self.cfg.pasta_investigar_desconhecida, reg)
            return reg

        if (
            roteamento.estado_suporte != EstadoImplementacao.SUPORTADO.value
            and "suport" in motivo_politica
        ):
            reg.estado = EstadoPDF.TIPO_NAO_SUPORTADO
            reg.motivo_rejeicao = f"pipeline nao implementado: {roteamento.estado_suporte}"
            if self.cfg.mode != "shadow":
                conc_dir = (roteamento.concessionaria.canonica or "NAO_SUPORTADO").replace("/", "_")
                grupo_dir = roteamento.grupo.value.upper() if roteamento.grupo else "DESCONHECIDO"
                dest_dir = self.cfg.pasta_investigar_tipo_nao_suportado / conc_dir / grupo_dir
                self._mover_para(pdf, dest_dir, reg)
            return reg

        if decisao == DecisaoPolitica.REVISAO_MANUAL:
            reg.estado = EstadoPDF.REVISAO_MANUAL
            reg.motivo_rejeicao = roteamento.politica.motivo
            if self.cfg.mode != "shadow":
                conc_dir = (roteamento.concessionaria.canonica or "DESCONHECIDA").replace("/", "_")
                grupo_dir = roteamento.grupo.value.upper() if roteamento.grupo else "DESCONHECIDO"
                dest_dir = self.cfg.pasta_investigar_classificacao / conc_dir / grupo_dir
                self._mover_para(pdf, dest_dir, reg)
            return reg

        estado_suporte = roteamento.estado_suporte
        if estado_suporte != EstadoImplementacao.SUPORTADO.value:
            reg.estado = EstadoPDF.TIPO_NAO_SUPORTADO
            reg.motivo_rejeicao = f"pipeline nao implementado: {estado_suporte}"
            if self.cfg.mode != "shadow":
                conc_dir = (roteamento.concessionaria.canonica or "NAO_SUPORTADO").replace("/", "_")
                grupo_dir = roteamento.grupo.value.upper() if roteamento.grupo else "DESCONHECIDO"
                dest_dir = self.cfg.pasta_investigar_tipo_nao_suportado / conc_dir / grupo_dir
                self._mover_para(pdf, dest_dir, reg)
            return reg

        reg.estado = EstadoPDF.ACEITO_AUTOMATICAMENTE
        if self.cfg.mode == "shadow":
            reg.motivo_rejeicao = "shadow: nenhuma acao operacional"
            return reg

        self._executar_pipeline(pdf, reg, texto)
        return reg

    def _mover_para(self, pdf: Path, destino_dir: Path, reg: RegistroProcessamento) -> None:
        """Move arquivo em modos nao-shadow."""
        if self.cfg.mode == "shadow":
            return
        try:
            destino = mover_seguro(pdf, destino_dir, reg.sha256)
            reg.destino = str(destino)
        except Exception as exc:
            reg.estado = EstadoPDF.ERRO
            reg.motivo_rejeicao = f"Erro ao mover: {exc}"

    def _inferir_mes_ano(self, pdf: Path, texto_pdf: str = "") -> tuple[str, str]:
        if texto_pdf:
            m = re.search(r"\b(0[1-9]|1[0-2])/(20\d{2})\b\s+\d{2}/\d{2}/20\d{2}\s+R\$", texto_pdf)
            if m:
                return m.group(1), m.group(2)
            m = re.search(r"(?m)^\\s*(0[1-9]|1[0-2])/(20\\d{2})\\b", texto_pdf)
            if m:
                return m.group(1), m.group(2)
        nome = pdf.stem
        m = _RE_MES_ANO_LONGO.search(nome)
        if m and 1 <= int(m.group(1)) <= 12:
            return m.group(1), m.group(2)
        m = _RE_MES_ANO_CURTO.search(nome)
        if m and 1 <= int(m.group(1)) <= 12:
            return m.group(1), f"20{m.group(2)}"
        ref = dt.date.today().replace(day=1) - dt.timedelta(days=1)
        return f"{ref.month:02d}", str(ref.year)

    def _resolver_comando_pipeline(
        self,
        reg: RegistroProcessamento,
        session_input: Path,
        texto_pdf: str = "",
    ) -> tuple[list[str], str, str, str | None]:
        if not reg.concessionaria_prevista or not reg.grupo_previsto:
            raise RuntimeError("roteamento incompleto para execucao operacional")

        grupo = GrupoTensao(reg.grupo_previsto.lower())
        info = resolver_pipeline(reg.concessionaria_prevista, grupo)
        script = info.get("pipeline")
        args = [str(a) for a in info.get("argumentos", [])]
        if not script:
            raise RuntimeError("pipeline nao resolvido")

        mes, ano = self._inferir_mes_ano(Path(reg.caminho_original), texto_pdf=texto_pdf)
        script_path = str(script)

        if Path(script_path).name == "pipeline_lote_bt.py":
            conc_id = args[0] if args else None
            if not conc_id:
                spec = REGISTRO.get(reg.concessionaria_prevista)
                if spec:
                    pipe = spec.grupos.get(grupo)
                    conc_id = pipe.identificador if pipe else None
            if not conc_id:
                raise RuntimeError("pipeline_lote_bt sem concessionaria resolvida")
            cmd = [
                self.python_exe,
                script_path,
                "--concessionaria",
                conc_id,
                "--pasta",
                str(session_input),
                "--mes",
                mes,
                "--ano",
                ano,
            ]
            if len(args) > 1:
                cmd.extend(args[1:])
            return cmd, mes, ano, conc_id

        cmd = [self.python_exe, script_path, "--mes", mes, "--ano", ano]
        if info.get("aceita_pasta"):
            cmd.extend(["--pasta", str(session_input)])
        if args:
            cmd.extend(args)
        return cmd, mes, ano, None

    def _escrever_meta_sessao(self, session_root: Path, payload: dict) -> None:
        meta_path = session_root / "_sessao_v2.json"
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _descobrir_carimbo_lote_bt(self, conc_id: str | None, arquivo_original: str) -> str | None:
        if not conc_id:
            return None
        meta_path = Path("//10.10.250.21/Energia/ARQUIVOS ENZO/lote_bt_staging") / conc_id / "_sessao_meta.json"
        if not meta_path.exists():
            return None
        try:
            dados = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        for item in dados.get("arquivos", []) or []:
            if (item.get("arquivo_origem") or "") == arquivo_original:
                return item.get("carimbo")
        return None

    def _localizar_destino_final(self, carimbo: str | None) -> str | None:
        if not carimbo:
            return None
        raiz_bb = self.cfg.output_root.parent
        candidatos = [
            raiz_bb / "Digitadas",
            raiz_bb / "Ja_existiam_no_Consen",
            self.cfg.output_root / "Investigar",
        ]
        for pasta in candidatos:
            if not pasta.exists():
                continue
            for pdf in pasta.rglob(f"{carimbo}*.pdf"):
                if pdf.is_file():
                    return str(pdf)
        return None

    def _executar_pipeline(self, pdf: Path, reg: RegistroProcessamento, texto_pdf: str) -> None:
        """Staging -> lock global -> pipeline real (apenas em production/controlled)."""
        try:
            self._validar_staging_root()
        except Exception as exc:
            reg.estado = EstadoPDF.ERRO
            reg.motivo_rejeicao = f"staging indisponivel antes do carimbo: {exc}"
            return

        try:
            with adquirir_lock_global(timeout_s=2.0):
                session_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
                staging_dir = self.cfg.staging_root / session_id
                staging_entrada = staging_dir / "entrada"
                staging_entrada.mkdir(parents=True, exist_ok=False)

                cmd, mes, ano, conc_id = self._resolver_comando_pipeline(
                    reg,
                    staging_entrada,
                    texto_pdf=texto_pdf,
                )
                reg.session_id = session_id
                reg.staging = str(staging_entrada)
                reg.comando_planejado = list(cmd)

                destino_staging = mover_seguro(pdf, staging_entrada, reg.sha256)
                (staging_dir / "_sessao_manifesto.txt").write_text(f"{destino_staging.name}\n", encoding="utf-8")
                self._escrever_meta_sessao(
                    staging_dir,
                    {
                        "session_id": session_id,
                        "sha256": reg.sha256,
                        "arquivo_original": reg.arquivo_original,
                        "caminho_original": reg.caminho_original,
                        "arquivo_staging": str(destino_staging),
                        "concessionaria": reg.concessionaria_prevista,
                        "grupo": reg.grupo_previsto,
                        "pipeline": reg.pipeline_resolvido,
                        "comando": cmd,
                        "mes": mes,
                        "ano": ano,
                        "status": "staging_criado",
                        "inicio": dt.datetime.now().isoformat(),
                    },
                )

                reg.estado = EstadoPDF.STAGING_CRIADO
                reg.estado = EstadoPDF.PIPELINE_INICIADO
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                rc = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[2]), env=env).returncode

                reg.carimbo = self._descobrir_carimbo_lote_bt(conc_id, reg.arquivo_original)
                reg.destino = self._localizar_destino_final(reg.carimbo)
                reg.timestamp_conclusao = dt.datetime.now().isoformat()
                self._escrever_meta_sessao(
                    staging_dir,
                    {
                        "session_id": session_id,
                        "sha256": reg.sha256,
                        "arquivo_original": reg.arquivo_original,
                        "caminho_original": reg.caminho_original,
                        "arquivo_staging": str(destino_staging),
                        "concessionaria": reg.concessionaria_prevista,
                        "grupo": reg.grupo_previsto,
                        "pipeline": reg.pipeline_resolvido,
                        "comando": cmd,
                        "mes": mes,
                        "ano": ano,
                        "status": "concluido" if rc == 0 else "erro",
                        "carimbo": reg.carimbo,
                        "destino": reg.destino,
                        "inicio": reg.timestamp_deteccao,
                        "fim": reg.timestamp_conclusao,
                        "exit_code": rc,
                    },
                )

                if rc == 0:
                    reg.estado = EstadoPDF.PIPELINE_CONCLUIDO
                    reg.motivo_rejeicao = ""
                else:
                    reg.estado = EstadoPDF.ERRO
                    reg.motivo_rejeicao = f"pipeline retornou exit code {rc}"
        except RuntimeError as exc:
            reg.estado = EstadoPDF.ERRO
            reg.motivo_rejeicao = str(exc)
            return

    def ciclo_lote(self, pdfs_especificos: list[Path] | None = None) -> list[list[RegistroProcessamento]]:
        """Agrupa PDFs por (concessionaria, grupo, pipeline) e processa cada grupo sequencialmente.

        Retorna lista de grupos, cada grupo é uma lista de RegistroProcessamento.
        Em shadow, apenas classifica e agrupa sem mover nada.
        Em controlled/production, processa cada grupo em staging isolado.
        Quando pdfs_especificos for informado, processa somente esses caminhos.
        """
        if self.cfg.mode not in ("shadow", "controlled", "production"):
            raise ValueError(f"Modo desconhecido: {self.cfg.mode!r}")

        pdfs = list(pdfs_especificos) if pdfs_especificos is not None else varrer_pdfs(self.cfg.input_root)

        # ── Passo 1: classificar cada PDF individualmente (sem mover) ──
        classificados: list[RegistroProcessamento] = []
        for pdf in pdfs[: self.cfg.max_pdfs_por_ciclo]:
            reg = self._classificar_pdf(pdf)
            self._registrar(reg)
            classificados.append(reg)

        # ── Passo 2: separar aceitos dos rejeitados ──
        aceitos = [r for r in classificados if r.estado == EstadoPDF.ACEITO_AUTOMATICAMENTE]
        rejeitados = [r for r in classificados if r.estado != EstadoPDF.ACEITO_AUTOMATICAMENTE]

        # ── Passo 3: agrupar aceitos por (concessionaria, grupo, pipeline_resolvido) ──
        grupos: dict[tuple[str, str, str], list[RegistroProcessamento]] = {}
        for reg in aceitos:
            chave = (
                reg.concessionaria_prevista or "DESCONHECIDA",
                reg.grupo_previsto or "?",
                Path(reg.pipeline_resolvido).name if reg.pipeline_resolvido else "sem_pipeline",
            )
            grupos.setdefault(chave, []).append(reg)

        self._salvar_estado()

        if self.cfg.mode == "shadow":
            return [classificados]

        # ── Passo 4: processar cada grupo sequencialmente (com gate de erros) ──
        resultado_grupos: list[list[RegistroProcessamento]] = []
        if rejeitados:
            resultado_grupos.append(rejeitados)

        erros_consecutivos = 0
        max_erros = getattr(self.cfg, "max_erros_consecutivos", 3)
        ciclo_parado_por_erros = False

        for chave, regs in grupos.items():
            if erros_consecutivos >= max_erros:
                for r in regs:
                    r.estado = EstadoPDF.ERRO
                    r.motivo_rejeicao = f"ciclo interrompido apos {erros_consecutivos} erros consecutivos"
                resultado_grupos.append(list(regs))
                ciclo_parado_por_erros = True
                continue

            grupo_result = self._executar_grupo(regs)
            resultado_grupos.append(grupo_result)

            if any(r.estado == EstadoPDF.ERRO for r in grupo_result):
                erros_consecutivos += 1
            else:
                erros_consecutivos = 0

        self.relatorio_ciclo = self._montar_relatorio_ciclo(
            resultado_grupos,
            ciclo_parado_por_erros=ciclo_parado_por_erros,
            erros_consecutivos=erros_consecutivos,
        )

        self._salvar_estado()
        return resultado_grupos

    def _montar_relatorio_ciclo(
        self,
        resultado_grupos: list[list[RegistroProcessamento]],
        *,
        ciclo_parado_por_erros: bool = False,
        erros_consecutivos: int = 0,
    ) -> dict:
        todos = [r for g in resultado_grupos for r in g]
        carimbos = [r.carimbo for r in todos if r.carimbo]
        sessoes = list({r.session_id for r in todos if r.session_id})
        return {
            "timestamp": dt.datetime.now().isoformat(),
            "snapshot": len(todos),
            "grupos": len(resultado_grupos),
            "sessoes": sessoes,
            "carimbos": carimbos,
            "erros_consecutivos": erros_consecutivos,
            "ciclo_parado_por_erros": ciclo_parado_por_erros,
            "resultados": {
                r.sha256: {
                    "estado": r.estado.value,
                    "arquivo": r.arquivo_original,
                    "carimbo": r.carimbo,
                    "motivo": r.motivo_rejeicao,
                }
                for r in todos
            },
        }

    def _classificar_pdf(self, pdf: Path) -> RegistroProcessamento:
        """Classifica um PDF sem mover ou executar pipeline. Equivale a shadow."""
        reg = RegistroProcessamento(
            arquivo_original=pdf.name,
            caminho_original=str(pdf),
            sha256="",
        )
        try:
            reg.sha256 = calcular_sha256(pdf)
        except OSError as exc:
            reg.estado = EstadoPDF.ERRO
            reg.motivo_rejeicao = f"Erro ao calcular SHA: {exc}"
            return reg

        anterior = self._ja_processado(reg.sha256)
        if anterior:
            estado_ant = anterior.get("estado", "")
            if estado_ant in (EstadoPDF.PIPELINE_CONCLUIDO.value, EstadoPDF.REVISAO_MANUAL.value):
                reg.estado = EstadoPDF.DUPLICADO
                reg.motivo_rejeicao = f"ja processado: estado={estado_ant}"
                return reg
            if estado_ant in (
                EstadoPDF.ERRO.value,
                EstadoPDF.STAGING_CRIADO.value,
                EstadoPDF.PIPELINE_INICIADO.value,
            ):
                reg = self._hidratar_registro(anterior, sha=reg.sha256, caminho_atual=pdf)
                reg.arquivo_original = pdf.name
                reg.caminho_original = str(pdf)
                reg.estado = EstadoPDF.ERRO
                reg.motivo_rejeicao = (
                    f"sessao anterior preservada: estado={estado_ant}; "
                    "retomar ou reconciliar sem novo carimbo"
                )
                return reg

        texto = _extrair_texto(pdf)
        if not texto or len(texto) < 30:
            reg.estado = EstadoPDF.ERRO
            reg.motivo_rejeicao = "texto insuficiente (PDF ilegivel ou protegido)"
            return reg

        roteamento = rotear(texto, arquivo=pdf.name)

        reg.concessionaria_prevista = roteamento.concessionaria.canonica
        reg.confianca_concessionaria = roteamento.concessionaria.confianca
        reg.metodo_concessionaria = roteamento.concessionaria.metodo
        reg.evidencia_concessionaria = roteamento.concessionaria.evidencia
        reg.grupo_previsto = roteamento.grupo.value if roteamento.grupo else None
        reg.confianca_grupo = roteamento.confianca_grupo
        reg.evidencias_grupo = list(roteamento.evidencias_grupo)
        reg.penalidades_grupo = list(roteamento.penalidades_grupo)
        reg.status_rotulagem = roteamento.status_rotulagem
        reg.confianca_roteamento = roteamento.politica.confianca_roteamento
        reg.decisao_politica = roteamento.politica.decisao.value
        reg.estado_suporte = roteamento.estado_suporte
        reg.pipeline_resolvido = roteamento.pipeline_script
        reg.comando_planejado = roteamento.comando
        reg.estado = EstadoPDF.CLASSIFICADO

        decisao = roteamento.politica.decisao
        motivo_politica = (roteamento.politica.motivo or "").lower()

        if decisao == DecisaoPolitica.DESCONHECIDO or not roteamento.concessionaria.canonica:
            reg.estado = EstadoPDF.CONCESSIONARIA_DESCONHECIDA
            reg.motivo_rejeicao = roteamento.politica.motivo
            return reg

        if (
            roteamento.estado_suporte != EstadoImplementacao.SUPORTADO.value
            and "suport" in motivo_politica
        ):
            reg.estado = EstadoPDF.TIPO_NAO_SUPORTADO
            reg.motivo_rejeicao = f"pipeline nao implementado: {roteamento.estado_suporte}"
            return reg

        if decisao == DecisaoPolitica.REVISAO_MANUAL:
            reg.estado = EstadoPDF.REVISAO_MANUAL
            reg.motivo_rejeicao = roteamento.politica.motivo
            return reg

        if roteamento.estado_suporte != EstadoImplementacao.SUPORTADO.value:
            reg.estado = EstadoPDF.TIPO_NAO_SUPORTADO
            reg.motivo_rejeicao = f"pipeline nao implementado: {roteamento.estado_suporte}"
            return reg

        reg.estado = EstadoPDF.ACEITO_AUTOMATICAMENTE
        return reg

    def _executar_grupo(self, regs: list[RegistroProcessamento]) -> list[RegistroProcessamento]:
        """Cria um staging único para o grupo e roda o pipeline uma vez.

        Todos os registros do grupo compartilham o mesmo session_id e staging.
        O erro em um grupo não afeta outros grupos.
        """
        if not regs:
            return regs

        # Usa o primeiro registro para resolver o comando do grupo
        rep = regs[0]

        try:
            self._validar_staging_root()
        except Exception as exc:
            for r in regs:
                r.estado = EstadoPDF.ERRO
                r.motivo_rejeicao = f"staging indisponivel: {exc}"
            return regs

        try:
            with adquirir_lock_global(timeout_s=2.0):
                session_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
                staging_dir = self.cfg.staging_root / session_id
                staging_entrada = staging_dir / "entrada"
                staging_entrada.mkdir(parents=True, exist_ok=False)

                # Mover todos os PDFs do grupo para o staging
                destinos_staging: list[Path] = []
                for r in regs:
                    pdf_path = Path(r.caminho_original)
                    if not pdf_path.exists():
                        r.estado = EstadoPDF.ERRO
                        r.motivo_rejeicao = "arquivo nao encontrado no momento do staging"
                        continue
                    dst = mover_seguro(pdf_path, staging_entrada, r.sha256)
                    destinos_staging.append(dst)
                    r.session_id = session_id
                    r.staging = str(staging_entrada)

                # Resolver comando usando staging com todos os PDFs
                cmd, mes, ano, conc_id = self._resolver_comando_pipeline(
                    rep, staging_entrada, texto_pdf=""
                )

                manifesto = "\n".join(d.name for d in destinos_staging) + "\n"
                (staging_dir / "_sessao_manifesto.txt").write_text(manifesto, encoding="utf-8")
                self._escrever_meta_sessao(
                    staging_dir,
                    {
                        "session_id": session_id,
                        "concessionaria": rep.concessionaria_prevista,
                        "grupo": rep.grupo_previsto,
                        "pipeline": rep.pipeline_resolvido,
                        "comando": cmd,
                        "mes": mes,
                        "ano": ano,
                        "arquivos": [
                            {"arquivo_original": r.arquivo_original, "sha256": r.sha256}
                            for r in regs
                        ],
                        "status": "staging_criado",
                        "inicio": dt.datetime.now().isoformat(),
                    },
                )

                for r in regs:
                    r.estado = EstadoPDF.STAGING_CRIADO
                    r.comando_planejado = list(cmd)
                    r.estado = EstadoPDF.PIPELINE_INICIADO

                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                rc = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[2]), env=env).returncode

                ts_fim = dt.datetime.now().isoformat()
                self._escrever_meta_sessao(
                    staging_dir,
                    {
                        "session_id": session_id,
                        "concessionaria": rep.concessionaria_prevista,
                        "grupo": rep.grupo_previsto,
                        "pipeline": rep.pipeline_resolvido,
                        "comando": cmd,
                        "mes": mes,
                        "ano": ano,
                        "arquivos": [
                            {"arquivo_original": r.arquivo_original, "sha256": r.sha256}
                            for r in regs
                        ],
                        "status": "concluido" if rc == 0 else "erro",
                        "exit_code": rc,
                        "inicio": regs[0].timestamp_deteccao,
                        "fim": ts_fim,
                    },
                )

                for r in regs:
                    r.timestamp_conclusao = ts_fim
                    if rc == 0:
                        r.carimbo = self._descobrir_carimbo_lote_bt(conc_id, r.arquivo_original)
                        r.destino = self._localizar_destino_final(r.carimbo)
                        r.estado = EstadoPDF.PIPELINE_CONCLUIDO
                        r.motivo_rejeicao = ""
                    else:
                        r.estado = EstadoPDF.ERRO
                        r.motivo_rejeicao = f"pipeline retornou exit code {rc}"

        except RuntimeError as exc:
            for r in regs:
                r.estado = EstadoPDF.ERRO
                r.motivo_rejeicao = str(exc)

        return regs

    def _validar_staging_root(self) -> None:
        """Cria e valida leitura/escrita do staging root antes de qualquer carimbo."""
        root = self.cfg.staging_root
        root.mkdir(parents=True, exist_ok=True)

        probe = root / f"._watcher_v2_probe_{os.getpid()}_{uuid.uuid4().hex}.tmp"
        try:
            probe.write_text("watcher_v2 staging probe", encoding="utf-8")
            probe.read_text(encoding="utf-8")
        finally:
            try:
                probe.unlink(missing_ok=True)
            except Exception:
                pass
