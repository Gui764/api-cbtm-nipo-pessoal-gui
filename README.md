# 🏓 CBTM Tournament API & Simulator

> Um **projeto pessoal** desenvolvido para extrair, estruturar e simular dados de torneios oficiais de tênis de mesa da CBTM (especialmente focado nas etapas da **Liga Nipo Brasileira**).

## 🚀 Sobre o Projeto
Este projeto consiste num *backend* em alta performance construído para automatizar a leitura de páginas complexas de eventos da CBTM (que utilizam componentes legados em DevExpress e Angular). A API realiza *web scraping* automatizado, processa as informações de inscrições, grupos e resultados, e armazena os dados localmente para consulta rápida e simulação de cenários.

---

## 🛠️ Tecnologias Utilizadas
* **Python 3.12+**
* **FastAPI** (Construção da API REST e documentação automática Swagger)
* **Playwright** (Automação de navegador para extração de dados dinâmicos)
* **SQLite** (Sistema de cache local para persistência rápida de torneios)
* **Pydantic** (Validação de dados)

---

## ✨ Funcionalidades
* **Extração Automatizada:** Lê páginas de eventos da CBTM (`app.cbtm.org.br`) contornando lentidões e menus complexos.
* **Múltiplas Secções:** Suporta extração de listas de inscritos, composição de grupos e resultados/fases eliminatórias por categoria.
* **Cache Inteligente:** Utiliza SQLite para guardar os dados dos torneios, permitindo consultas instantâneas e evitando sobrecarga no site oficial.
* **Documentação Interativa:** Interface Swagger UI integrada (`/docs`) com suporte a menu de seleção de categorias.

---

## ⚙️ Como Executar o Projeto Localmente

1. **Clone o repositório:**
   ```bash
   git clone [https://github.com/seu-usuario/seu-repositorio.git](https://github.com/seu-usuario/seu-repositorio.git)
   cd seu-repositorio
