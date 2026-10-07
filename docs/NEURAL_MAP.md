# Mapa neural simbólico (4 GB + Ollama local)

Em máquinas com **~4 GB de RAM**, um LLM local não pode carregar contexto enorme nem “pensar” em várias voltas como um agente grande na nuvem. O GRIPPO trata o modelo como **uma camada fina de decisão**; o “cérebro pesado” é **simbólico** — snapshot Git, léxico, skills e tools no harness.

Diagrama editável: [diagrams/GRIPPO-neural-map.excalidraw](./diagrams/GRIPPO-neural-map.excalidraw) (abrir em [excalidraw.com](https://excalidraw.com) ou no app).

## Problema: LLM “pensando demais”

| Abordagem lenta | Por quê |
|-----------------|--------|
| Colar manual do Git no prompt | Muitos tokens, pouca RAM |
| Várias rodadas de raciocínio livre | Latência alta em 3B–4B Q4 |
| Modelo inventa estado do repo | Erro + retrabalho |

## Ideia: ativação esparsa + visão breve + busca rápida

Metáfora **neural** (não é deep learning extra): vários “nós” representam pedaços de conhecimento; em cada pedido **só alguns acendem**.

```
                    ┌─────────────────────────────────────┐
                    │  Léxico Git (L1) — fixo no harness   │
                    │  add, commit, stash, branch, HEAD…   │
                    └──────────────────┬──────────────────┘
                                       │ ativa poucos nós
ENTRADA ──► ROTEAR intenção ──► VISÃO RÁPIDA ──► RECUPERAR skill ──► micro-plano ──► SAÍDA
 (frase)      (leve / regras)    (snapshot)       (L2 / SKILL.md)     (Ollama)      (tool)
```

### Camadas A → D (no diagrama Excalidraw)

Cada coluna de elipses **A, B, C, D** é uma **etapa de ativação**, não um passo lento de raciocínio:

| Símbolo | Função | Quem executa | Custo |
|---------|--------|--------------|-------|
| **A** | Intenção | Palavras-chave, fase do repo, subcomando CLI | ~0 LLM |
| **B** | Conceito Git | Léxico: o que é stash, index, branch… | Documentação / `docs/GIT.md` (futuro) |
| **C** | Skill | Um `SKILL.md` por fluxo (`semantic-commit`, …) | Leitura de arquivo |
| **D** | Ação | Tool + JSON (`git_stage`, `git_commit`, …) | Harness + shell `git` |

**L1** = léxico Git embutido (tabela intenção → conceito).  
**L2** = “pesquisa rápida”: carregar só a skill candidata + tools permitidas.  
**L2′** = schema das tools (parâmetros fechados), para o modelo não improvisar flags.

O **Ollama** entra principalmente no **micro-plano**: escolher skill (se ambíguo), paths para `git add`, texto do commit — sempre com snapshot e diff **truncados**.

## Fluxo comparado

### ❌ Caminho pesado (evitar)

```
Usuário → LLM longo → “imaginar” git status → comando shell na sorte
```

### ✅ Caminho GRIPPO (mapa neural simbólico)

1. **ENTRADA** — “commita isso”, “stash”, “push”.
2. **ROTEAR** — `phase` + keywords → skill provável (`semantic-commit`, `stash-save`, …).
3. **VISÃO RÁPIDA** — `RepositorySnapshot` (branch, paths, clean/staged); **sem** diff gigante ainda.
4. **RECUPERAR** — corpo da skill + 1 `git_diff` filtrado se necessário.
5. **MICRO-PLANO** — 1 chamada Ollama, poucos tokens → tool call estruturada.
6. **VALIDAR** — policy, paths, confirmação humana em commit/push.
7. **EXECUTAR** — `git` via subprocess; refresh do snapshot.
8. **SAÍDA** — mensagem curta + estado novo.

Isso alinha com o agent loop em [ARCHITECTURE.md](./ARCHITECTURE.md): OBSERVE → PLAN → … e com o ciclo em [SKILLS.md](./SKILLS.md): input → estruturar → operar → executar.

## O que o modelo “sabe” de Git

Não precisa memorizar tudo. Precisa de:

- **Visão breve** do estado (JSON compacto).
- **Uma skill ativa** com regras do fluxo atual.
- **Léxico mínimo** (stash = guardar trabalho local; commit = gravar no HEAD; etc.) — pode viver em `docs/GIT.md` e em trechos citados pelas skills.

O harness garante que **B** e **C** venham de fontes determinísticas; o LLM só conecta **A** → **D** na sessão atual.

## Metas de performance (orientação)

| Técnica | Efeito |
|---------|--------|
| Ativar 1 skill, não 8 | Menos prompt |
| Snapshot antes do diff | Menos tokens se já for óbvio |
| `max_tool_calls_per_turn` baixo | Menos loops |
| Q4 + modelo 3B–4B | Cabe em 4 GB |
| Git real pós-ação | VERIFY sem “achismo” |

## Implementação no código (roadmap)

| Peça | Pacote / local |
|------|----------------|
| Snapshot | `app/harness/worktree/snapshot.py` |
| Skills (L2) | `app/harness/skills/` + `skills/` |
| Tools (D) | `app/harness/tools/git.py` |
| Roteamento (A) | futuro `app/harness/neural/router.py` |
| Léxico (L1) | futuro `app/harness/neural/lexicon.yaml` |
| Micro-plano | `app/harness/llm/` + Ollama |

O nome **neural** aqui é **arquitetura de ativação**: esparso, rápido e guiado — não um segundo modelo treinado.
