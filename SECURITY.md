# Política de Segurança

## Versões suportadas

| Versão | Suporte |
|---|---|
| 0.2.x | ✔ correções de segurança |
| < 0.2 | ✘ (continha endpoints de shell sem autorização — atualize) |

## Como relatar uma vulnerabilidade

**Não abra issue pública.** Envie para **<CONTATO_DE_SEGURANCA>** (a definir pelos
mantenedores) ou use *Security Advisories* privados do repositório, com: versão,
descrição, passos de reprodução (em repositório descartável) e impacto. Resposta inicial
em até 7 dias.

## Escopo de interesse

- Execução de operação Git mutável sem autorização explícita (bypass do
  `GitPermissionManager`, do executor ou do runner).
- Injeção de argumentos/opções no Git, execução de comandos via transporte, hooks
  disparados indevidamente, escrita fora do repositório.
- Reutilização de autorização em outro estado/plano.
- Vazamento de credenciais em logs ou auditoria.
- API HTTP opcional: acesso sem token, CSRF.

## Modelo de ameaça (resumo)

Entradas não confiáveis: texto do usuário, saída da LLM, conteúdo do repositório
administrado (nomes de arquivos, branches, configuração local). Fora do escopo: código
malicioso executando dentro do próprio processo do GRIPPO e hooks/configurações que o
próprio usuário instalou no repositório dele. Detalhes em `docs/PERMISSIONS.md`.
