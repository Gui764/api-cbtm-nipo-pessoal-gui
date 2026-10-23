"""Parsers do texto renderizado das páginas de evento da CBTM (app.cbtm.org.br).

Trabalham com tokens de texto (quebra de linha / tab) em vez de seletores CSS,
para ficarem robustos a mudanças de layout. Sem dependências externas.
"""
import re

SEP = re.compile(r"[\n\t]+")
RE_INT = re.compile(r"^\d+$")
RE_DATA = re.compile(r"\d{2}/\d{2}/\d{4}")
RE_JOGO = re.compile(r"Jogo\s*(\d+)(?:\s*-\s*Mesa\s*([\w\-]+))?(?:\s*-\s*(\d{1,2}:\d{2}))?", re.I)
RE_PLACAR = re.compile(r"^(\d+)\s*-\s*(\d+)$")
RE_GRUPO = re.compile(r"^(.*?)\s*-\s*Grupo\s*(\d+)$", re.I)
RE_STATUS = re.compile(r"^(?:(?P<cat>.*?)\s+)?(?P<st>Pré-?\s?inscrito|Inscrito)$", re.I)
RE_RANK = re.compile(r"(\d+)\s*pts\.?\s*Ranking", re.I)
RE_RAT = re.compile(r"(\d+)\s*pts\.?\s*Rating", re.I)
RE_CATEGORIA = re.compile(
    r"^(.*?)\s+(\d+)\s+Inscrito\(s\)\s+e\s+(\d+)\s+Pré-inscrito\(s\)", re.I
)
HEAD_GRUPO = {"#", "JOGADORES", "PT. RAK", "PT. RAT"}


def tokens(txt: str) -> list[str]:
    return [t.strip() for t in SEP.split(txt or "") if t.strip()]


def parse_categoria(rotulo: str) -> dict:
    """'ADULTO MAS 10 Inscrito(s) e 0 Pré-inscrito(s)' -> dict."""
    m = RE_CATEGORIA.match(" ".join(rotulo.split()))
    if not m:
        return {"rotulo": rotulo, "categoria": rotulo, "inscritos": None, "pre_inscritos": None}
    return {
        "rotulo": rotulo,
        "categoria": m[1].strip(),
        "inscritos": int(m[2]),
        "pre_inscritos": int(m[3]),
    }


def parse_inscricoes(cards: list[str]) -> list[dict]:
    out = []
    for c in cards:
        t = tokens(c)
        rank = rating = None
        for tok in t:
            if m := RE_RANK.search(tok):
                rank = int(m[1])
            if m := RE_RAT.search(tok):
                rating = int(m[1])
        idx = next((i for i, tok in enumerate(t) if RE_STATUS.match(tok)), None)
        if idx is None:
            continue
        m = RE_STATUS.match(t[idx])
        status = "Pré-inscrito" if "pr" in m["st"].lower()[:2] else "Inscrito"
        if m["cat"]:
            categoria, i_clube = m["cat"], idx - 1
        else:
            categoria, i_clube = (t[idx - 1] if idx >= 1 else None), idx - 2
        clube = t[i_clube] if i_clube >= 0 else None
        nome = t[i_clube - 1] if i_clube - 1 >= 0 else None
        if not nome:
            continue
        liga = None
        if clube and (ml := re.search(r"\s*-?\s*(LIGA-[A-Z0-9]+)$", clube, re.I)):
            liga, clube = ml[1].upper(), clube[:ml.start()].strip(" -")
        out.append({
            "nome": nome, "clube": clube, "liga": liga, "categoria": categoria,
            "status": status, "pontos_ranking": rank, "pontos_rating": rating,
        })
    return out


def parse_grupos(txt: str) -> list[dict]:
    t = tokens(txt)
    grupos, atual, i = [], None, 0
    while i < len(t):
        tok = t[i]
        if m := RE_GRUPO.match(tok):
            atual = {"grupo": int(m[2]), "categoria": m[1].strip(), "jogadores": []}
            grupos.append(atual)
            i += 1
            continue
        if (atual is not None and tok not in HEAD_GRUPO and RE_INT.match(tok)
                and i + 1 < len(t) and not RE_INT.match(t[i + 1])):
            pos, nome, j, nums = int(tok), t[i + 1], i + 2, []
            while j < len(t) and RE_INT.match(t[j]) and len(nums) < 2:
                nums.append(int(t[j]))
                j += 1
            status = None
            # após as 2 notas, um texto não numérico (nunca um nome, que vem após a posição) é o status
            if (j < len(t) and not RE_INT.match(t[j]) and not RE_GRUPO.match(t[j])
                    and t[j] not in HEAD_GRUPO):
                status = t[j]
                j += 1
            atual["jogadores"].append({
                "posicao": pos, "nome": nome,
                "pontos_ranking": nums[0] if len(nums) > 0 else None,
                "pontos_rating": nums[1] if len(nums) > 1 else None,
                "status": status,
            })
            i = j
            continue
        i += 1
    return grupos


def parse_jogo(card: str) -> dict | None:
    jogo = mesa = hora = data = placar = fase = None
    jogadores, atual = [], None
    for tok in tokens(card):
        if m := RE_JOGO.search(tok):
            jogo, mesa, hora = int(m[1]), m[2], m[3]
            if d := RE_DATA.search(tok):
                data = d[0]
        elif RE_DATA.fullmatch(tok):
            data = tok
        elif jogo is None and placar is None and RE_PLACAR.match(tok) and fase is not None:
            placar = tok
        elif jogo is None:
            if fase is None:
                fase = tok
        elif RE_INT.match(tok):
            if atual is not None:
                if atual["sets"] is None:
                    atual["sets"] = int(tok)
                else:
                    atual["parciais"].append(int(tok))
        else:
            atual = {"nome": tok, "sets": None, "parciais": []}
            jogadores.append(atual)
    if jogo is None or len(jogadores) < 2:
        return None
    jogadores = jogadores[:2]
    a, b = jogadores
    vencedor = None
    if a["sets"] is not None and b["sets"] is not None and a["sets"] != b["sets"]:
        vencedor = a["nome"] if a["sets"] > b["sets"] else b["nome"]
    etapa = fase.split(" - ", 1)[1].strip() if fase and " - " in fase else fase
    categoria = fase.split(" - ", 1)[0].strip() if fase and " - " in fase else None
    return {
        "jogo": jogo, "categoria": categoria, "etapa": etapa, "placar": placar,
        "mesa": mesa, "horario": hora, "data": data,
        "jogadores": jogadores, "vencedor": vencedor,
    }


def _perdedor(j: dict) -> str | None:
    if not j.get("vencedor"):
        return None
    return next((p["nome"] for p in j["jogadores"] if p["nome"] != j["vencedor"]), None)


def montar_eliminatoria(jogos: list[dict]) -> dict:
    """Deriva a fase eliminatória e o pódio a partir dos jogos que não são de grupo."""
    fases: dict[str, list[dict]] = {}
    for j in jogos:
        etapa = (j.get("etapa") or "").strip()
        if not etapa or re.match(r"grupo", etapa, re.I):
            continue
        fases.setdefault(etapa, []).append(j)
    podio: dict = {}
    final = next((l for e, l in fases.items() if e.lower() == "final"), [])
    if final and final[0].get("vencedor"):
        podio["1o"] = final[0]["vencedor"]
        podio["2o"] = _perdedor(final[0])
    semis = [j for e, l in fases.items() if re.match(r"semi", e, re.I) for j in l]
    terceiros = [p for p in (_perdedor(j) for j in semis) if p]
    if terceiros:
        podio["3o"] = terceiros
    return {"fases": fases, "podio": podio}
