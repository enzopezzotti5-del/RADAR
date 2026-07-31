#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Downloader COPEL MT para UCs especificas de recuperacao.

Baixa somente as instalacoes listadas em `copel_mt_recuperacao_ucs.csv`
e grava os PDFs em:
    DOWNLOAD COPEL/MM.AAAA/MT/BB_xxxxxxx.pdf
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent.parent.parent
sys.path.insert(0, str(_THIS_DIR))
sys.path.insert(0, str(_ROOT_DIR / "core"))
sys.path.insert(0, str(_ROOT_DIR))

import _venv_check  # noqa
import copel_mt as C


UCS_CSV_DEFAULT = _THIS_DIR / "copel_mt_recuperacao_ucs.csv"
DESTINO_DIR = C.COPEL_DIR
INDEX_LOCAL = C.COPEL_DIR / "indice_faturas_copel_mt_recuperacao.csv"
INDEX_FIELDS = C.INDEX_FIELDS


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


class IndiceLocalRecuperacao:
    def __init__(self) -> None:
        self.memoria: set[tuple[str, str]] = set()
        self.proximo: int = 0
        self._carregar()

    def _carregar(self) -> None:
        if not C._exists_unc(INDEX_LOCAL):
            C._mkdir_seguro(C.COPEL_DIR)
            with open(INDEX_LOCAL, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow(INDEX_FIELDS)
            C.log("Indice local MT recuperacao criado (vazio)", "INFO")
            return

        try:
            with open(INDEX_LOCAL, encoding="utf-8-sig", newline="") as f:
                conteudo = f.read()
        except Exception as exc:
            C.log(f"Indice local recuperacao inacessivel: {exc}", "WARN")
            return

        for row in csv.DictReader(io.StringIO(conteudo)):
            inst = (row.get("INSTALACAO") or "").strip()
            ref = (row.get("MES_REF") or "").strip()
            if inst and ref:
                self.memoria.add((inst, ref))
            match = re.search(r"(\d+)$", row.get("INDICE", ""))
            if match:
                self.proximo = max(self.proximo, int(match.group(1)) + 1)

        C.log(
            f"Indice local MT recuperacao: {len(self.memoria)} registros | proximo local={self.proximo or '(do master)'}",
            "OK",
        )

    def ja_baixado(self, instalacao: str, mes_ref: str) -> bool:
        return (instalacao, mes_ref) in self.memoria

    def gravar(
        self,
        indice_bb: str,
        instalacao: str,
        mes_ref: str,
        nr_fatura: str,
        cnpj: str,
        arquivo: str,
    ) -> None:
        with open(INDEX_LOCAL, "a", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow(
                [
                    indice_bb,
                    instalacao,
                    mes_ref,
                    nr_fatura,
                    datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "Pendente",
                    cnpj,
                    arquivo,
                ]
            )
        self.memoria.add((instalacao, mes_ref))
        match = re.search(r"(\d+)$", indice_bb)
        if match:
            numero = int(match.group(1))
            if numero >= self.proximo:
                self.proximo = numero + 1


def carregar_ucs_alvo(path_csv: Path) -> list[str]:
    if not path_csv.exists():
        raise FileNotFoundError(f"CSV de UCs nao encontrado: {path_csv}")

    texto = path_csv.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(texto)))
    vistos: set[str] = set()
    resultado: list[str] = []

    for row in rows:
        uc = _digits(row.get("instalacao") or row.get("uc") or row.get("instalacao_normalizada") or "")
        if not uc or uc in vistos:
            continue
        vistos.add(uc)
        resultado.append(uc)

    if not resultado:
        raise RuntimeError(f"Nenhuma UC valida encontrada em {path_csv}")
    return resultado


def carregar_instalacoes_alvo(path_csv: Path) -> tuple[list[C.Instalacao], list[str]]:
    alvos = carregar_ucs_alvo(path_csv)
    todas = C.carregar_instalacoes()
    mapa = {_digits(inst.instalacao): inst for inst in todas}

    selecionadas: list[C.Instalacao] = []
    ausentes: list[str] = []
    for uc in alvos:
        item = mapa.get(uc)
        if item is None:
            ausentes.append(uc)
            continue
        selecionadas.append(item)

    C.log(f"UCs alvo no CSV: {len(alvos)}", "INFO")
    C.log(f"UCs encontradas em acessos_copel.xlsx: {len(selecionadas)}", "OK")
    if ausentes:
        C.log(f"UCs ausentes na planilha de acessos: {', '.join(ausentes)}", "WARN")
    return selecionadas, ausentes


def _salvar_relatorio(
    *,
    baixados: list[tuple[str, str, str]],
    pulados: list[tuple[str, str]],
    erros: list[tuple[str, str]],
    ausentes: list[str],
) -> Path:
    C._mkdir_seguro(DESTINO_DIR)
    path_rel = DESTINO_DIR / f"relatorio_copel_mt_recuperacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    linhas = [
        "=" * 72,
        "RELATORIO COPEL MT RECUPERACAO",
        f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        "=" * 72,
        "",
        f"Baixados: {len(baixados)}",
    ]
    for instalacao, mes_ref, carimbo in baixados:
        linhas.append(f"OK  {instalacao:<12}  {mes_ref:<7}  {carimbo}")
    linhas.extend(["", f"Pulados: {len(pulados)}"])
    for instalacao, motivo in pulados:
        linhas.append(f"--  {instalacao:<12}  {motivo}")
    linhas.extend(["", f"Erros: {len(erros)}"])
    for instalacao, motivo in erros:
        linhas.append(f"ER  {instalacao:<12}  {motivo}")
    linhas.extend(["", f"Ausentes na planilha de acessos: {len(ausentes)}"])
    for instalacao in ausentes:
        linhas.append(f"NF  {instalacao}")

    path_rel.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    C.log(f"Relatorio salvo: {path_rel}", "OK")
    return path_rel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Downloader COPEL MT para instalacoes especificas de recuperacao.")
    parser.add_argument("--ucs-csv", type=Path, default=UCS_CSV_DEFAULT, help="CSV com as instalacoes alvo.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    C.log("=" * 72)
    C.log("COPEL MT RECUPERACAO — UCs especificas")
    C.log("=" * 72)
    C._mkdir_seguro(DESTINO_DIR)
    C.log(f"Pasta destino: {DESTINO_DIR}", "INFO")

    try:
        instalacoes, ausentes = carregar_instalacoes_alvo(args.ucs_csv)
    except Exception as exc:
        C.log(f"Falha ao carregar UCs alvo: {exc}", "ERR")
        return 1

    if not instalacoes:
        C.log("Nenhuma instalacao alvo encontrada. Abortando.", "ERR")
        return 1

    indice_local = IndiceLocalRecuperacao()
    master = C._carregar_master()

    if master:
        proximo_master = master._proximo_num
        if indice_local.proximo < proximo_master:
            C.log(f"Contador local ajustado: {indice_local.proximo} -> {proximo_master} (do master)", "INFO")
            indice_local.proximo = proximo_master

    temp_dir = _THIS_DIR / "downloads_temp_copel_mt_recuperacao"
    C._mkdir_seguro(temp_dir)
    driver = C.build_driver(temp_dir)

    baixados: list[tuple[str, str, str]] = []
    pulados: list[tuple[str, str]] = []
    erros: list[tuple[str, str]] = []

    try:
        for idx, inst in enumerate(instalacoes, 1):
            C.log(f"[{idx}/{len(instalacoes)}] Instalacao alvo: {inst.instalacao}", "INFO")
            C._voltar_janela_principal(driver, driver.current_window_handle)

            if not C.fazer_login_uc(driver, inst):
                erros.append((inst.instalacao, "Falha no login"))
                continue

            handle_principal = driver.current_window_handle
            if not C.acessar_historico(driver):
                erros.append((inst.instalacao, "Historico nao abriu"))
                continue

            faturas = C.ler_historico(driver)
            if not faturas:
                pulados.append((inst.instalacao, f"Sem faturas >= {C.ANO_MINIMO}"))
                continue

            for fatura in faturas:
                if indice_local.ja_baixado(inst.instalacao, fatura.mes_ref):
                    pulados.append((inst.instalacao, f"{fatura.mes_ref} ja no indice local"))
                    continue
                if C._master_ja_baixado(master, inst.instalacao, fatura.mes_ref, "COPEL"):
                    pulados.append((inst.instalacao, f"{fatura.mes_ref} ja no master"))
                    continue

                pdf_temp = C.baixar_fatura(driver, fatura, temp_dir, handle_principal)
                if pdf_temp is None:
                    erros.append((inst.instalacao, f"Falha no download {fatura.mes_ref}"))
                    continue

                carimbo = master.consumir_carimbo() if master else f"BB_{indice_local.proximo:07d}"
                pasta_dest = DESTINO_DIR / C._mes_pasta(fatura.mes_ref) / C._subpasta_tensao()
                C._mkdir_seguro(pasta_dest)
                destino = pasta_dest / f"{carimbo}.pdf"

                try:
                    pdf_temp.rename(destino)
                except Exception:
                    try:
                        import shutil

                        shutil.copy2(pdf_temp, destino)
                        pdf_temp.unlink(missing_ok=True)
                    except Exception as exc:
                        erros.append((inst.instalacao, f"Erro ao mover {fatura.mes_ref}: {exc}"))
                        continue

                C.gravar_registro(
                    master,
                    indice_local,
                    inst.instalacao,
                    fatura,
                    inst.cnpj,
                    str(destino),
                    carimbo_pre=carimbo,
                )
                baixados.append((inst.instalacao, fatura.mes_ref, carimbo))

        C.log("=" * 72)
        C.log(f"Concluido — baixados: {len(baixados)} | pulados: {len(pulados)} | erros: {len(erros)}", "OK")
        C.log("=" * 72)
        _salvar_relatorio(baixados=baixados, pulados=pulados, erros=erros, ausentes=ausentes)
        return 0 if not erros else 1

    except KeyboardInterrupt:
        C.log("Interrompido pelo usuario.", "WARN")
        _salvar_relatorio(baixados=baixados, pulados=pulados, erros=erros, ausentes=ausentes)
        return 130
    except Exception:
        C.log(traceback.format_exc(), "ERR")
        _salvar_relatorio(baixados=baixados, pulados=pulados, erros=erros, ausentes=ausentes)
        return 1
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        C.log("Driver encerrado.", "INFO")


if __name__ == "__main__":
    raise SystemExit(main())
