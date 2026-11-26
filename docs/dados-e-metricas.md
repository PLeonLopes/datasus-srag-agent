# Dados e Métricas

[← Voltar ao README](../README.md)

## Dados

Fonte: [SRAG 2019 a 2026 (SIVEP-Gripe)](https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026), Open DATASUS. São usados os arquivos do ano corrente e do anterior (a janela de 12 meses atravessa a virada do ano), em **Parquet**, que permite ler só as colunas necessárias.

| Etapa | O que faz | Código |
|---|---|---|
| Download | A API CKAN do portal responde 404, então os links são descobertos na página do dataset (o nome muda a cada atualização semanal). Só HTTPS no host do S3 do Ministério, sem redirects, gravação atômica e sha256 num `manifest.json` | `src/srag_agent/data/download.py` |
| ETL | Lê só a allowlist, converte códigos em rótulos, deriva a faixa etária e grava no SQLite com `CHECK` por coluna, troca atômica e a tabela `load_metadata` com a proveniência | `src/srag_agent/data/etl.py` |
| EDA | Preenchimento das 194 colunas e distribuição dos códigos só das colunas da allowlist, sem valores individuais | [`eda_colunas.md`](./eda_colunas.md) |
| Sintéticos | Mesmo formato, para testes e demonstração; marcados nos metadados, e o ETL recusa misturá-los com dados reais | `src/srag_agent/data/synthetic.py` |

Snapshot usado no exemplo: **589.437 casos** de 29/12/2024 a 22/11/2026, nenhum descartado por data inválida.

### Colunas lidas (10 de 194)

| Coluna da fonte | Coluna no banco | Uso |
|---|---|---|
| `DT_SIN_PRI` | `symptom_date` | Data do caso (início dos sintomas) |
| `EVOLUCAO` | `outcome` | Mortalidade |
| `HOSPITAL`, `UTI` | `hospitalized`, `icu` | Internação em UTI |
| `VACINA`, `VACINA_COV` | `flu_vaccine`, `covid_vaccine` | Vacinação entre os casos |
| `CLASSI_FIN` | `classification` | Agente causador (influenza, COVID-19, outros vírus) |
| `SG_UF_NOT` | `state` | UF de notificação |
| `CS_SEXO` | `sex` | Sexo |
| `NU_IDADE_N` + `TP_IDADE` | `age_group` | Faixa etária (a idade exata não é guardada) |

## Métricas

Todas as janelas terminam **14 dias antes da data dos dados** (o snapshot, não o dia de hoje), porque os casos recentes ainda estão sendo notificados. A data do caso é o início dos sintomas.

| Métrica | Definição | Janela | Exemplo (dados até 23/11/2026) |
|---|---|---|---|
| Taxa de aumento de casos | Casos dos últimos 28 dias contra os 28 anteriores (e 7 contra 7, como sinal rápido) | 28 e 7 dias | **-9,0%** (14.173 contra 15.567); 7 dias: -7,2% |
| Taxa de mortalidade | Óbitos por SRAG ÷ casos com desfecho (cura, óbito por SRAG ou por outras causas) | 90 dias | **5,0%** (1.940 de 38.516); 21,6% ainda sem desfecho |
| Taxa de internação em UTI | Internados em UTI ÷ internados com a informação de UTI | 90 dias | **29,1%** (13.058 de 44.808) |
| Vacinação entre os casos | Vacinados ÷ casos que informaram (gripe e COVID-19 separadas) | 90 dias | **38,8%** gripe; **40,0%** COVID-19 |

O enunciado pede "taxa de ocupação de UTI" e "taxa de vacinação da população". A base não tem o número de leitos nem a população, então essas métricas foram nomeadas pelo que de fato medem: a gravidade dos internados e a vacinação **entre os casos de SRAG**. Os guardrails bloqueiam os termos enganosos ("ocupação de UTI", "cobertura vacinal") no texto do LLM.

Gráficos: casos por dia (últimos 30 dias) e por mês (últimos 12 meses). Os dias ainda incompletos e o mês corrente aparecem destacados, para a queda no fim das barras não ser lida como real.
