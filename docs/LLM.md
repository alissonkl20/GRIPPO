# LLM local

## Backend: Ollama (mantido)

O projeto já usava Ollama. A integração foi reescrita, mas o backend foi mantido porque:

- executa modelos GGUF quantizados em **CPU, GPU ou ambos** (offload parcial por camadas
  com `num_gpu`), com detecção automática de CUDA/ROCm/Metal;
- **uma única instância** do modelo é compartilhada por todos os processos `grippo`
  (o servidor mantém os pesos; o GRIPPO não duplica memória nem recarrega a cada início);
- `keep_alive` controla por quanto tempo o modelo fica residente;
- não adiciona dependência compilada ao GRIPPO (o cliente é só `httpx`).

`llama-cpp-python` foi avaliado e **não** adotado: carregaria os pesos dentro do processo
Python (≈2 GB a mais de RSS por `grippo` aberto), recarregaria o modelo a cada execução
da CLI e exigiria compilação com CUDA na instalação.

## Como o GRIPPO usa o modelo

| Etapa | Chamada | Tokens de saída (máx.) |
|---|---|---|
| Interpretar o pedido | `/api/chat` com JSON schema (`action`, `steps[op,args]`) | 220 |
| Selecionar arquivos do commit | 1 chamada sim/não **por arquivo** (até 30 arquivos) | 12 |
| Mensagem de commit | 1 chamada só com os arquivos selecionados | 60 |

- Pedidos inequívocos de leitura ("git status", "mostre o histórico") usam o roteador
  determinístico e **não** chamam o modelo.
- Se o Ollama estiver fora do ar, o modelo não estiver instalado, estourar o tempo,
  faltar memória ou a resposta for inválida, o GRIPPO informa e usa o roteador
  determinístico; nunca troca silenciosamente para outro modelo.
- Uma inferência por vez por processo (semáforo); conexão HTTP reutilizada.
- Contexto limitado: últimos `max_history_turns` turnos, `max_prompt_chars` caracteres,
  `max_diff_chars` de diff; `num_ctx` define a janela.
- `temperature = 0`: o mesmo pedido no mesmo estado gera a mesma proposta.

## Configuração de hardware

```toml
[llm]
model = "qwen2.5:3b"   # Q4_K_M, 3,1B parâmetros, 1,93 GB em disco
num_ctx = 4096
num_gpu = -1           # -1 automático · 0 só CPU · N = N camadas na GPU (offload parcial)
num_thread = 0         # CPU: 0 = padrão; ajuste para o nº de núcleos físicos
keep_alive = "10m"
```

Orçamento de ~4 GB para o modelo: pesos 1,93 GB + KV cache (qwen2.5-3b, f16:
36 camadas × 2 cabeças KV × 128 × 2 × 2 bytes ≈ 36 KiB/token → ≈ 144 MiB em 4096
tokens) + buffers de computação. Medido: **2 059 MiB** reportados pelo Ollama
(`size`), 2 194 MiB por processo no `nvidia-smi`. Cabe com folga em 4 GB.

`grippo model import ARQUIVO.gguf --name meu-modelo` registra um GGUF local; depois
`grippo config set llm.model meu-modelo`.

## Medições reais

Máquina de teste: AMD Ryzen 5 2600 (6c/12t), 15 GiB RAM (≈1–2 GiB livres durante os
testes, swap cheio), NVIDIA RTX 3060 12 GB (driver 595.99), Ubuntu 26.04, Ollama 0.40.2
(snap). Comando: `grippo bench --runs 3 --json`. Números de uma execução; variam com
carga da máquina.

| Métrica | qwen2.5:3b Q4_K_M, GPU (`num_gpu=-1`) | qwen2.5:0.5b Q4_K_M, só CPU (`num_gpu=0`) |
|---|---|---|
| Memória do modelo (Ollama `size`) | 2 059 MiB, 100 % VRAM | 462 MiB, 100 % RAM |
| VRAM (nvidia-smi, processo llama-server) | 2 194 MiB | 0 |
| RSS do processo `llama-server` | 759 MiB (runtime CUDA/mmap) | 614 MiB |
| Carregamento do modelo | 11,2 s (94,9 s na primeiríssima carga, cache de disco frio + swap) | 3,2 s |
| Primeira chamada a frio (carga + prompt de ~2k tokens) | 12,3 s | 25,7 s |
| Latência por pedido (mediana, modelo carregado) | **0,38 s** | 1,27 s |
| Geração | **116 tok/s** | 33 tok/s |
| Acurácia de intenção (14 pedidos pt/en, `tests/llm_real`) | **14/14** | 7/14 |
| Seleção de arquivos de commit (7 casos) | **7/7** (2 execuções seguidas) | 1/7 |

Processo GRIPPO (Python): **36–40 MiB RSS**; crescimento de **+0,38 MiB** após 18
inferências; 1 000 snapshots Git sem crescimento de memória, fds, threads ou processos
(`tests/perf`). Snapshot do repositório: mediana 9 ms. Início da CLI: 63 ms
(`--version`), 276 ms (`status`).

Não medido nesta máquina: qwen2.5:3b só em CPU e offload parcial — com ~1 GiB de RAM
livre, carregar 2 GB na RAM forçaria swap/OOM e poderia encerrar outros programas do
usuário. Expectativa (não medida): ordem de 10–20 tok/s em CPU de 6 núcleos.

## Limitações conhecidas

- Um modelo de 3B quantizado **não** é um agente geral: ele escolhe entre operações de um
  catálogo e classifica arquivos. Raciocínios longos, merges complexos e resolução de
  conflitos não são delegados a ele — o GRIPPO mostra o estado e pede decisão.
- Seleção de arquivos é sensível ao texto do prompt (medimos 1/7 a 7/7 entre variantes);
  por isso ela combina palavras-chave determinísticas, decisão sim/não por arquivo e
  agrupamento por nome, e **pede esclarecimento** em vez de incluir tudo.
- O modelo às vezes propõe algo inadequado para pedidos vagos ("organize meu
  repositório" → criar branch). Nada executa sem confirmação, e operações destrutivas
  exigem digitar `confirmar`.
- Pedidos em português com termos técnicos em inglês funcionam bem; outros idiomas não
  foram avaliados.
- O primeiro pedido após o modelo ser descarregado paga o carregamento (segundos a
  dezenas de segundos). `keep_alive` maior evita isso ao custo de memória residente.
