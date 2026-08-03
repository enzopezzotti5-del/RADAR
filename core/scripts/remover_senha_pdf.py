"""
remover_senha_pdf.py
--------------------
Remove a senha de um PDF (você precisa ter a senha).

Uso:
    .venv\Scripts\python.exe remover_senha_pdf.py arquivo.pdf

O arquivo sem senha será salvo como:
    arquivo_sem_senha.pdf
"""
import sys
from pathlib import Path

try:
    import pikepdf
except ImportError:
    print("Instale pikepdf:  .venv\\Scripts\\pip install pikepdf")
    sys.exit(1)


def remover_senha(caminho_pdf: str, senha: str) -> None:
    entrada = Path(caminho_pdf)
    if not entrada.exists():
        print(f"Arquivo nao encontrado: {entrada}")
        sys.exit(1)

    saida = entrada.with_stem(entrada.stem + "_sem_senha")

    try:
        with pikepdf.open(str(entrada), password=senha) as pdf:
            pdf.save(str(saida))
        print(f"Salvo em: {saida}")
    except pikepdf.PasswordError:
        print("Senha incorreta.")
        sys.exit(1)
    except Exception as e:
        print(f"Erro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python remover_senha_pdf.py <arquivo.pdf>")
        sys.exit(1)

    caminho = sys.argv[1]
    senha = input("Digite a senha do PDF: ")
    remover_senha(caminho, senha)
