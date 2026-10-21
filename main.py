import sys
import asyncio

# Força o Windows a usar o motor assíncrono correto para o Playwright
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os
import re
import time
import json
import sqlite3
import logging
from enum import Enum
from contextlib import asynccontextmanager
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout
from fastapi.middleware.cors import CORSMiddleware

from parsers import (
    parse_categoria, parse_inscricoes, parse_grupos, parse_jogo,
    montar_eliminatoria, tokens,
)

# ---------------------------------------------------------------- LOGS
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- BASE DE DADOS SQLITE
DB_FILE = "torneios_cbtm.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_eventos (
                chave TEXT PRIMARY KEY,
                timestamp REAL,
                dados TEXT
            )
        """)

init_db()

# ---------------------------------------------------------------- CONSTANTES
ALLOWED_HOSTS = {"app.cbtm.org.br", "cbtm.org.br", "www.cbtm.org.br"}
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))  # Cache no banco dura 1 hora (3600 segundos)
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))
SECOES = {"inscricoes", "grupos", "resultados"}

OPCAO = re.compile(r"Inscrito\(s\)\s+e\s+\d+\s+Pré-inscrito\(s\)", re.I)

_state: dict = {}
_sem = asyncio.Semaphore(MAX_CONCURRENCY)

@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("A iniciar o motor Playwright...")
    pw = await async_playwright().start()
    _state["pw"] = pw
    _state["browser"] = await pw.chromium.launch(
        headless=True,  # Voltou a invisível para máxima velocidade
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    yield
    logger.info("A encerrar o motor Playwright...")
    await _state["browser"].close()
    await pw.stop()

app = FastAPI(title="CBTM API - Simulador Nipo", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://gui764-tenisdemesa-pontuacao.great-site.net"
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
 )
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://gui764-tenisdemesa-pontuacao.great-site.net"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- VALIDAÇÃO
def validar_url(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or (p.hostname or "").lower() not in ALLOWED_HOSTS:
        raise HTTPException(400, "URL inválida: envie um link do domínio cbtm.org.br")
    return p._replace(scheme="https").geturl()

# ---------------------------------------------------------------- NAVEGADOR
async def nova_pagina():
    ctx = await _state["browser"].new_context(
        locale="pt-BR", 
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = await ctx.new_page()
    page.set_default_timeout(25_000)
    chamadas: list[dict] = []

    async def filtro(route):
        req = route.request
        if req.resource_type in {"image", "media", "font", "stylesheet"}:
            return await route.abort()
        if req.resource_type == "document" and not (urlparse(req.url).hostname or "").lower().endswith("cbtm.org.br"):
            return await route.abort()
        await route.continue_()

    await page.route("**/*", filtro)
    page.on("response", lambda r: chamadas.append({"url": r.url, "status": r.status})
            if "json" in r.headers.get("content-type", "") else None)
    return ctx, page, chamadas

async def aguardar(page: Page, ms: int = 600):
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except PWTimeout:
        pass
    await page.wait_for_timeout(ms)

# ---------------------------------------------------------------- JAVASCRIPT HELPERS
JS_CARDS = """(needle) => {
  const re = new RegExp(needle, 'g');
  const n = el => ((el.innerText || '').match(re) || []).length;
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    if (n(el) !== 1) continue;
    const p = el.parentElement;
    if (p && n(p) === 1) continue;
    out.push(el.innerText);
  }
  return out;
}"""

JS_PASSO = """(reset) => {
  let resta = false;
  for (const el of document.querySelectorAll('*')) {
    const oy = getComputedStyle(el).overflowY;
    if (!['auto', 'scroll'].includes(oy) || el.scrollHeight <= el.clientHeight + 20) continue;
    if (reset) { el.scrollTop = 0; continue; }
    el.scrollTop += el.clientHeight * 0.8;
    if (el.scrollTop + el.clientHeight < el.scrollHeight - 5) resta = true;
  }
  if (reset) { window.scrollTo(0, 0); return false; }
  window.scrollBy(0, window.innerHeight * 0.8);
  if (window.scrollY + window.innerHeight < document.documentElement.scrollHeight - 5) resta = true;
  return resta;
}"""

async def abrir_dropdown(page: Page) -> bool:
    for sel in [".dxeButtonEditSys", "mat-select", "[role=combobox]", ".v-select", "[class*=select]", "select"]:
        loc = page.locator(sel).first
        if await loc.count():
            try:
                await loc.wait_for(state="visible", timeout=3000)
                await loc.click(force=True)
                await page.wait_for_timeout(1000)
                if await page.get_by_text(OPCAO).count() > 1:
                    return True
                await page.keyboard.press("Escape")
            except:
                pass
    
    atual = page.get_by_text(OPCAO).first
    if await atual.count():
        try:
            await atual.click(force=True)
            await page.wait_for_timeout(1000)
            return await page.get_by_text(OPCAO).count() > 1
        except:
            pass
    return False

async def listar_categorias(page: Page) -> list[dict]:
    try:
        await page.wait_for_selector(".mat-option-text, .v-list-item__title", timeout=5000, state="attached")
    except:
        pass
    
    aberto = await abrir_dropdown(page)
    loc = page.get_by_text(OPCAO)
    rotulos: list[str] = []
    
    count = await loc.count()
    if count == 0:
        elementos = await page.evaluate("() => Array.from(document.querySelectorAll('.mat-option-text, .v-list-item__title')).map(e => e.innerText.trim())")
        rotulos = [r for r in elementos if r and "Inscrito" in r]
    else:
        for i in range(count):
            txt = " ".join((await loc.nth(i).inner_text()).split())
            if txt not in rotulos:
                rotulos.append(txt)
                
    if aberto:
        await page.keyboard.press("Escape")
    return [parse_categoria(r) for r in rotulos]

async def selecionar_categoria(page: Page, rotulo: str, unica: bool):
    if unica:
        return
    await abrir_dropdown(page)
    try:
        await page.get_by_text(rotulo, exact=True).last.click(timeout=3000, force=True)
    except:
        pass
    await aguardar(page)

async def abrir_aba(page: Page, padrao: str):
    rx = re.compile(padrao, re.I)
    loc = page.get_by_role("tab", name=rx)
    if not await loc.count():
        loc = page.get_by_text(rx)
    if await loc.count():
        try:
            await loc.first.click(timeout=3000, force=True)
        except:
            pass
    await aguardar(page)

async def coletar(page: Page, needle: str, rolar: bool = False) -> list[str]:
    if not rolar:
        return await page.evaluate(JS_CARDS, needle)
    vistos: dict[str, str] = {}
    await page.evaluate(JS_PASSO, True)
    for _ in range(80):
        for c in await page.evaluate(JS_CARDS, needle):
            vistos.setdefault(c, c)
        if not await page.evaluate(JS_PASSO, False):
            break
        await page.wait_for_timeout(800)
    for c in await page.evaluate(JS_CARDS, needle):
        vistos.setdefault(c, c)
    return list(vistos)

async def titulo_evento(page: Page) -> str:
    corpo = await page.inner_text("body")
    for ln in tokens(corpo):
        if re.match(r"^\d+\s*-\s*\S", ln):
            return ln
    return await page.title()

# ---------------------------------------------------------------- EXTRAÇÃO
async def extrair_evento(page: Page, url: str, incluir: set[str], filtro: str | None,
                         debug: bool, chamadas: list[dict]) -> dict:
    qs = {k.lower(): v for k, v in parse_qs(urlparse(url).query).items()}
    titulo = await titulo_evento(page)
    m = re.match(r"^(\d+)\s*-\s*(.+)$", titulo)
    evento = {
        "id": int(m[1]) if m else (int(qs["eve_evento_id"][0]) if "eve_evento_id" in qs else None),
        "nome": m[2].strip() if m else titulo,
        "url": url,
    }
    cats = await listar_categorias(page)
    if not cats:
        atual = page.get_by_text(OPCAO).first
        if await atual.count():
            cats = [parse_categoria(" ".join((await atual.inner_text()).split()))]
    unica = len(cats) <= 1
    if filtro:
        f = filtro.lower()
        cats = [c for c in cats if f in c["rotulo"].lower()]

    resultado = []
    for c in cats:
        await selecionar_categoria(page, c["rotulo"], unica)
        item = {k: c[k] for k in ("categoria", "inscritos", "pre_inscritos")}
        await page.wait_for_timeout(2000) 

        if "inscricoes" in incluir:
            await abrir_aba(page, r"inscri")
            await page.wait_for_timeout(2000)
            item["inscricoes"] = parse_inscricoes(await coletar(page, r"pts\.\s*Ranking"))
        if "grupos" in incluir:
            await abrir_aba(page, r"^\s*grupos")
            await page.wait_for_timeout(2000)
            item["grupos"] = parse_grupos(await page.inner_text("body"))
        if "resultados" in incluir:
            await abrir_aba(page, r"resultados")
            await page.wait_for_timeout(2000)
            jogos = [j for j in (parse_jogo(t) for t in await coletar(page, r"Jogo\s*\d+", rolar=True)) if j]
            jogos.sort(key=lambda j: j["jogo"])
            item["resultados"] = jogos
            item["fase_eliminatoria"] = montar_eliminatoria(jogos)
        resultado.append(item)

    out = {"tipo": "evento", "evento": evento, "categorias": resultado}
    if debug:
        out["_debug"] = {
            "url_final": page.url,
            "categorias_detectadas": [c["rotulo"] for c in cats],
            "texto_pagina": (await page.inner_text("body"))[:4000],
            "chamadas_json": chamadas[:50],
        }
    return out

async def extrair_pagina(page: Page, debug: bool, chamadas: list[dict]) -> dict:
    dados = await page.evaluate("""() => ({
      titulo: document.title,
      descricao: (document.querySelector('meta[name=description]') ||
                  document.querySelector('meta[property="og:description"]'))?.content || null,
      titulos: [...document.querySelectorAll('h1,h2')].map(e => e.innerText.trim()).filter(Boolean).slice(0, 20),
      links: [...document.querySelectorAll('a[href]')]
              .map(a => ({texto: a.innerText.trim(), url: a.href})).filter(l => l.texto).slice(0, 200),
      texto: document.body.innerText.slice(0, 20000)
    })""")
    return {"tipo": "pagina", **dados, "_debug": {"chamadas_json": chamadas[:50]} if debug else {}}

async def extrair(url: str, incluir: set[str], filtro: str | None, debug: bool) -> dict:
    ctx, page, chamadas = await nova_pagina()
    try:
        logger.info(f"A ler o site: {url}")
        try:
            await page.goto(url, wait_until="commit", timeout=45_000)
        except PWTimeout:
            logger.warning("Timeout parcial. A tentar ler os elementos que carregaram...")
            
        await page.wait_for_timeout(8000) 
        
        if "login" in page.url.lower() and "login" not in url.lower():
            raise HTTPException(403, "A página exige login e não pode ser lida.")
        if "/iui/app/evento" in urlparse(url).path.lower():
            return await extrair_evento(page, url, incluir, filtro, debug, chamadas)
        return await extrair_pagina(page, debug, chamadas)
    except Exception as e:
        logger.error(f"Erro na extração: {str(e)}")
        raise HTTPException(504, f"Falha ao ler o site da CBTM: {str(e)}")
    finally:
        await ctx.close()

# ---------------------------------------------------------------- ENDPOINTS E MODELOS
class MenuCategorias(str, Enum):
    ADULTO_MAS = "ADULTO MAS"
    SUB_11_MAS = "SUB-11 MAS"
    SUB_13_MAS = "SUB-13 MAS"
    SUB_15_MAS = "SUB-15 MAS"
    SUB_17_MAS = "SUB-17 MAS"
    MASTER_40_FEM = "MASTER 40FEM"
    MASTER_40_MAS = "MASTER 40MAS"
    MASTER_50_MAS = "MASTER 50MAS"

class Pedido(BaseModel):
    url: str = Field(..., examples=["https://app.cbtm.org.br/IUI/App/Evento?tab=0&EVE_Evento_ID=6135"])
    categoria: str | None = Field(None)
    incluir: list[str] = Field(default_factory=lambda: sorted(SECOES))
    debug: bool = False

async def processar(p: Pedido) -> dict:
    url = validar_url(p.url)
    incluir = set(p.incluir)
    if not incluir <= SECOES:
        raise HTTPException(400, f"'incluir' aceita apenas: {sorted(SECOES)}")
    
    chave = f"{url}|{sorted(incluir)}|{(p.categoria or '').lower()}"
    
    # 1. Verifica na Base de Dados (SQLite)
    if not p.debug:
        with sqlite3.connect(DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT timestamp, dados FROM cache_eventos WHERE chave=?", (chave,))
            row = cur.fetchone()
            if row and (time.time() - row[0] < CACHE_TTL):
                logger.info("Torneio carregado instantaneamente da base de dados!")
                return {**json.loads(row[1]), "banco_dados": True}

    # 2. Se não estiver no banco (ou se o debug estiver ativo), extrai direto do site
    async with _sem:
        try:
            dados = await asyncio.wait_for(extrair(url, incluir, p.categoria, p.debug), REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            raise HTTPException(504, "Tempo limite da requisição excedido.")
            
    # 3. Guarda o novo resultado no Banco de Dados
    if not p.debug:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cache_eventos (chave, timestamp, dados)
                VALUES (?, ?, ?)
            """, (chave, time.time(), json.dumps(dados)))
            logger.info("Novo torneio guardado na base de dados com sucesso.")

    return {**dados, "banco_dados": False}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/extrair")
async def extrair_get(
    url: str,
    categoria: MenuCategorias | None = Query(None, description="Selecione ou deixe vazio para trazer tudo"),
    incluir: str = Query(",".join(sorted(SECOES))),
    debug: bool = False,
):
    cat_str = categoria.value if categoria else None
    return await processar(Pedido(url=url, categoria=cat_str,
                                  incluir=[s.strip() for s in incluir.split(",") if s.strip()],
                                  debug=debug))

@app.post("/api/extrair")
async def extrair_post(p: Pedido):
    return await processar(p)
