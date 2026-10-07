#!/usr/bin/env python3
"""
Agente SNMP pass_persist para a atividade de Gerenciamento de Redes.

Publica a subarvore 1.3.6.1.4.1.99999 (ATIVIDADE-SNMP-MIB):
  .1.1.1.0  atvNomeEquipe        DisplayString  read-write  (estatico, salvo em disco)
  .1.1.2.0  atvLocalLaboratorio  DisplayString  read-write  (estatico, salvo em disco)
  .1.2.1.0  atvDataHora          DisplayString  read-only   (dinamico)
  .1.2.2.0  atvMemoriaLivreMB    Gauge32        read-only   (dinamico)
  .1.2.3.0  atvProcessosAtivos   Gauge32        read-only   (dinamico)
  .1.2.4.0  atvLimiteMemoriaMB   Integer32      read-write
  .1.3.1.1.2.N atvDiscoMontagem    DisplayString read-only
  .1.3.1.1.3.N atvDiscoUsoPercent  Gauge32       read-only
  .1.3.1.1.4.N atvDiscoObservacao  DisplayString read-write

Uso no snmpd.conf:
  pass_persist .1.3.6.1.4.1.99999 /usr/local/bin/agente_atividade.py
"""

import json
import os
import subprocess
import sys
import time

BASE = ".1.3.6.1.4.1.99999"
ESTADO = os.environ.get("ATIVIDADE_ESTADO", "/var/lib/snmp/atividade_estado.json")

PADRAO = {
    "atvNomeEquipe": "Equipe Exemplo",
    "atvLocalLaboratorio": "Laboratorio de Redes",
    "atvLimiteMemoriaMB": 512,
    "observacoes": {},          # indice (str) -> texto
}


def carregar():
    try:
        with open(ESTADO) as f:
            dados = json.load(f)
        estado = dict(PADRAO)
        estado.update(dados)
        return estado
    except Exception:
        return dict(PADRAO)


def salvar(estado):
    try:
        os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
        tmp = ESTADO + ".tmp"
        with open(tmp, "w") as f:
            json.dump(estado, f)
        os.replace(tmp, ESTADO)
    except Exception:
        pass            # sem permissao de escrita: o SET vale ate reiniciar


# ----------------------------------------------------------------- dinamicos

def memoria_livre_mb():
    try:                                                    # Linux
        with open("/proc/meminfo") as f:
            for linha in f:
                if linha.startswith("MemAvailable:"):
                    return int(linha.split()[1]) // 1024
    except Exception:
        pass
    try:                                                    # macOS
        saida = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        livre = ativo = 0
        for linha in saida.splitlines():
            if "page size of" in linha:
                tam = int(linha.split("page size of")[1].split()[0])
            if linha.startswith("Pages free:"):
                livre = int(linha.split(":")[1].strip().rstrip("."))
            if linha.startswith("Pages inactive:"):
                ativo = int(linha.split(":")[1].strip().rstrip("."))
        return (livre + ativo) * tam // (1024 * 1024)
    except Exception:
        return 0


def processos_ativos():
    try:
        return len([p for p in os.listdir("/proc") if p.isdigit()])
    except Exception:
        pass
    try:
        saida = subprocess.run(["ps", "-A"], capture_output=True, text=True, timeout=5).stdout
        return max(len(saida.strip().splitlines()) - 1, 0)
    except Exception:
        return 0


def discos():
    """Devolve [(indice, ponto_de_montagem, uso_percent)] a partir do df."""
    linhas = []
    try:
        saida = subprocess.run(["df", "-P"], capture_output=True, text=True, timeout=5).stdout
        for linha in saida.splitlines()[1:]:
            campos = linha.split()
            if len(campos) >= 6 and campos[4].endswith("%"):
                linhas.append((campos[5], int(campos[4].rstrip("%"))))
    except Exception:
        pass
    linhas.sort()
    return [(i, nome, uso) for i, (nome, uso) in enumerate(linhas[:32], start=1)]


# ------------------------------------------------------------------- arvore

def arvore():
    """Monta a lista ordenada [(oid, tipo, valor, gravavel, chave)]."""
    e = carregar()
    itens = [
        (BASE + ".1.1.1.0", "string",  e["atvNomeEquipe"],       True,  "atvNomeEquipe"),
        (BASE + ".1.1.2.0", "string",  e["atvLocalLaboratorio"], True,  "atvLocalLaboratorio"),
        (BASE + ".1.2.1.0", "string",  time.strftime("%Y-%m-%d %H:%M:%S"), False, None),
        (BASE + ".1.2.2.0", "gauge",   memoria_livre_mb(),       False, None),
        (BASE + ".1.2.3.0", "gauge",   processos_ativos(),       False, None),
        (BASE + ".1.2.4.0", "integer", int(e["atvLimiteMemoriaMB"]), True, "atvLimiteMemoriaMB"),
    ]
    for indice, nome, uso in discos():
        itens.append((BASE + ".1.3.1.1.2.%d" % indice, "string", nome, False, None))
        itens.append((BASE + ".1.3.1.1.3.%d" % indice, "gauge",  uso,  False, None))
        itens.append((BASE + ".1.3.1.1.4.%d" % indice, "string",
                      e["observacoes"].get(str(indice), ""), True, "obs:%d" % indice))
    itens.sort(key=lambda it: ordem(it[0]))
    return itens


def ordem(oid):
    return tuple(int(p) for p in oid.strip(".").split("."))


def responder(item):
    print(item[0])
    print(item[1])
    print(item[2])
    sys.stdout.flush()


def nao_encontrado():
    print("NONE")
    sys.stdout.flush()


def gravar(chave, valor):
    estado = carregar()
    if chave == "atvLimiteMemoriaMB":
        try:
            estado[chave] = int(valor)
        except ValueError:
            return "wrong-type"
    elif chave.startswith("obs:"):
        estado["observacoes"][chave.split(":")[1]] = valor
    else:
        estado[chave] = valor
    salvar(estado)
    return "DONE"


def main():
    while True:
        linha = sys.stdin.readline()
        if not linha:
            break
        comando = linha.strip().lower()

        if comando == "ping":
            print("PONG"); sys.stdout.flush()

        elif comando == "get":
            oid = sys.stdin.readline().strip()
            achou = [i for i in arvore() if i[0] == oid]
            responder(achou[0]) if achou else nao_encontrado()

        elif comando == "getnext":
            oid = sys.stdin.readline().strip()
            alvo = ordem(oid)
            proximos = [i for i in arvore() if ordem(i[0]) > alvo]
            responder(proximos[0]) if proximos else nao_encontrado()

        elif comando == "set":
            oid = sys.stdin.readline().strip()
            resto = sys.stdin.readline().strip()
            partes = resto.split(" ", 1)
            valor = partes[1].strip('"') if len(partes) > 1 else ""
            item = [i for i in arvore() if i[0] == oid]
            if not item:
                print("not-writable")
            elif not item[0][3]:
                print("not-writable")
            else:
                print(gravar(item[0][4], valor))
            sys.stdout.flush()

        else:
            break


if __name__ == "__main__":
    main()
