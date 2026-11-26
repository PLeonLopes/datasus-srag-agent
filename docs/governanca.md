# Governança, Guardrails e Dados Sensíveis

[← Voltar ao README](../README.md)

## Trilha de auditoria

JSON Lines só de acréscimo em `logs/audit.jsonl` (`src/srag_agent/governance/audit.py`). Cada evento tem `run_id`, sequência, horário, status (`ok`, `degraded`, `blocked`, `error`) e dados.

| Evento | O que registra |
|---|---|
| `run.start` / `run.end` | Provedor, modelo, data dos dados, sha256 de cada arquivo de origem; sha256 do relatório gerado |
| `guardrail.input` / `guardrail.output` | Resultado das checagens, com as violações e a tentativa |
| `llm.call` | Nó, tentativa, **prompt completo, resposta**, tool calls, duração e tokens |
| `llm.unavailable` / `llm.error` | Erro, status HTTP e a mensagem (distingue cota esgotada de provedor sobrecarregado) |
| `tool.*` | Argumentos e resultado de cada ferramenta, inclusive as consultas SQL e as notícias completas |
| `decision.*` | Ferramenta forçada pelo código, limite de rodadas, uso do texto de reserva |
| `step.*` | Duração de cada nó do grafo e da atualização dos dados |

Proteções:

- **Cadeia de hash.** Cada linha guarda o hash da anterior; `verify_chain` detecta linha editada, apagada ou fora de ordem.
- **Arquivo `.head`.** O hash da última linha fica num arquivo à parte, então cortar o fim do log é detectado.
- **Redação de segredos.** `SecretStr` e campos com nome de chave, token ou senha viram `[REDACTED]` antes de gravar; contagens como `input_tokens` são preservadas.
- **Nada de dados de pacientes.** O modelo só recebe agregados e manchetes, então registrar os prompts completos é seguro.

Um recorte exportado (como [`exemplo/auditoria_exemplo.jsonl`](./exemplo/auditoria_exemplo.jsonl)) é verificado com `verify_chain(path, partial=True)`: a primeira linha pode apontar para um evento anterior, mas os hashes e o encadeamento continuam conferidos.

## Guardrails

Todos em código, não só no prompt.

| Camada | Verificação | Código |
|---|---|---|
| Entrada | O banco precisa ter exatamente as colunas da allowlist e pelo menos 10 casos nos 21 dias antes do snapshot; snapshot antigo e dados sintéticos geram aviso | `governance/guardrails.py` |
| SQL livre | Um único `SELECT` agregado sobre `cases`, sem subconsultas, CTEs, `UNION` ou *window functions*; `LIMIT` de no máximo 200 | `tools/sql.py` |
| | **Grupos com menos de 5 casos são omitidos** (risco de reidentificação); sem subconsultas, a contagem é exata | `tools/sql.py` |
| | Conexão somente leitura e `set_authorizer` do SQLite: o motor nega qualquer coisa além de ler `cases` com funções da allowlist; timeout de 5 s | `tools/sql.py`, `data/database.py` |
| Notícias | Hosts em allowlist, sem redirects, HTML removido, filtros de recência e relevância; manchetes com padrão de **injeção de prompt** são descartadas e contadas (com normalização de acentos, caracteres invisíveis e letras espaçadas) | `tools/news.py` |
| | No prompt, as notícias vão num bloco `<noticias_nao_confiaveis>` | `agent/prompts.py` |
| Saída do LLM | Todo número do texto precisa existir nas evidências entregues ao modelo (formato brasileiro, sem arredondar) | `governance/guardrails.py` |
| | "Alta" para variação negativa (ou "queda" para positiva) é bloqueada | |
| | Bloqueia CPF, CNPJ, telefone, e-mail, links, conselho médico, previsões, termos enganosos ("cobertura vacinal", "ocupação de UTI") e textos com mais de 350 palavras | |
| Falhas | Texto recusado: uma nova tentativa com as violações como feedback, depois texto de reserva "sem IA"; LLM indisponível (429, 5xx, timeout): texto de reserva; chave inválida ou modelo inexistente: a execução **para** | `agent/graph.py`, `llm/errors.py` |
| Custo | Até 4 rodadas de ferramentas e 2 tentativas de redação: no máximo 7 chamadas ao LLM por relatório | `agent/graph.py` |

## Tratamento de dados sensíveis

- **Minimização na leitura.** Só 10 das 194 colunas saem do Parquet; as outras (número da notificação, data de nascimento, município, unidade de saúde, raça e etnia, ocupação, lotes de vacina, CPF e campos de texto livre) nunca entram na memória. Um teste usa uma coluna "isca" para provar isso.
- **Generalização.** A idade exata vira faixa etária (0-4, 5-19, 20-39, 40-59, 60-79, 80+).
- **Supressão de grupos pequenos** nas consultas do agente: grupos com menos de 5 casos não aparecem (uma tentativa de cruzar óbitos de um dia por UF, faixa etária e sexo teve todos os grupos omitidos).
- **Só agregados chegam ao LLM**, e as respostas são barradas se contiverem CPF, CNPJ, telefone ou e-mail.
- **Dados fora do Git.** Os arquivos baixados e o banco ficam em `data/`, ignorada; republicar os dados brutos contrariaria a minimização.
- **Segredos.** Chaves só no `.env` (ignorado), lidas como `SecretStr`; nunca aparecem em logs, auditoria, relatório ou interface (há testes para isso).

## O que as execuções reais ensinaram

Problemas que só apareceram com o modelo de verdade, todos registrados pela auditoria e corrigidos:

| O que aconteceu | Como apareceu | Correção |
|---|---|---|
| `google/gemma-4-31b-it:free` respondeu 429 em todas as chamadas | `llm.unavailable` com a mensagem "Provider returned error": sobrecarga do provedor, não a cota da conta | O relatório saiu com texto de reserva, como previsto; a mensagem do erro passou a ser registrada |
| As retentativas do SDK triplicavam o consumo da cota gratuita | Cada chamada virou 3 requisições | `SRAG_LLM_MAX_RETRIES=0` recomendado no OpenRouter gratuito (padrão na interface) |
| O modelo escreveu "cobertura vacinal" e "% dos casos foram para a UTI" | Números corretos, interpretação errada | Glossário no prompt do redator e um **guardrail de termos enganosos** |
| A redação de segredos apagava `input_tokens` e `total_tokens` | Contagens apareciam como `[REDACTED]` | A regra passou a esconder só nomes de segredo (`api_key`, `access_token`) |
| O modelo citou o total de novembro como se fosse até 09/11 | O número existe nas evidências, então o guardrail aprovou | Registrado como limitação: o guardrail confere a existência do número, não a atribuição |
