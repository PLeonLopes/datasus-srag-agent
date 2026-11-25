# EDA das colunas do SRAG

Gerado em 25/11/2026 por `srag_agent.data.profile` a partir de: `INFLUD25-23-11-2026.parquet`, `INFLUD26-23-11-2026.parquet`. Total: 589.437 linhas e 194 colunas.

Só agregados: nenhum valor individual é publicado, e a distribuição de códigos aparece apenas
para colunas categóricas da allowlist.

## Colunas da allowlist: distribuição dos códigos (%)

- **EVOLUCAO**: `1`: 82.0, `vazio`: 7.5, `2`: 5.9, `3`: 2.6, `9`: 1.9
- **HOSPITAL**: `1`: 95.8, `vazio`: 2.1, `2`: 2.0, `9`: 0.1
- **UTI**: `2`: 63.7, `1`: 25.7, `vazio`: 9.7, `9`: 0.9
- **VACINA**: `2`: 67.4, `1`: 25.4, `vazio`: 5.4, `9`: 1.8
- **VACINA_COV**: `1`: 51.8, `2`: 47.1, `9`: 1.1, `vazio`: 0.0
- **CLASSI_FIN**: `4`: 44.0, `2`: 32.8, `1`: 13.3, `vazio`: 4.5, `5`: 4.1, `3`: 1.3
- **CS_SEXO**: `M`: 51.9, `F`: 48.1, `I`: 0.0
- **SG_UF_NOT**: `SP`: 23.2, `MG`: 13.2, `PR`: 9.6, `RJ`: 7.0, `RS`: 6.2, `SC`: 5.5, `BA`: 4.1, `DF`: 3.6, `CE`: 3.4, `MS`: 2.9, `GO`: 2.9, `PE`: 2.3, `PA`: 2.1, `PB`: 1.8, `AM`: 1.6, `MT`: 1.5, `MA`: 1.4, `SE`: 1.2, `ES`: 1.0, `AC`: 1.0, `AP`: 0.8, `RN`: 0.8, `AL`: 0.7, `RO`: 0.6, `RR`: 0.6, `TO`: 0.5, `PI`: 0.5, `vazio`: 0.0

## Todas as colunas

| Coluna | Tipo | Preenchido (%) | Allowlist |
|---|---|---|---|
| `NU_NOTIFIC` | string | 100.0 |  |
| `DT_NOTIFIC` | timestamp[ns] | 100.0 |  |
| `SEM_NOT` | string | 100.0 |  |
| `DT_SIN_PRI` | timestamp[ns] | 100.0 | sim |
| `SEM_PRI` | string | 100.0 |  |
| `SG_UF_NOT` | string | 100.0 | sim |
| `ID_REGIONA` | string | 87.5 |  |
| `CO_REGIONA` | decimal128(8, 0) | 87.5 |  |
| `ID_MUNICIP` | string | 100.0 |  |
| `CO_MUN_NOT` | string | 100.0 |  |
| `CS_SEXO` | string | 100.0 | sim |
| `DT_NASC` | timestamp[ns] | 100.0 |  |
| `NU_IDADE_N` | decimal128(3, 0) | 100.0 | sim |
| `TP_IDADE` | string | 100.0 | sim |
| `COD_IDADE` | string | 100.0 |  |
| `CS_GESTANT` | decimal128(1, 0) | 100.0 |  |
| `CS_RACA` | string | 100.0 |  |
| `CS_ETINIA` | string | 0.7 |  |
| `CS_ESCOL_N` | decimal128(1, 0) | 64.7 |  |
| `ID_PAIS` | string | 100.0 |  |
| `CO_PAIS` | decimal128(3, 0) | 100.0 |  |
| `SG_UF` | string | 100.0 |  |
| `ID_RG_RESI` | string | 88.4 |  |
| `CO_RG_RESI` | decimal128(8, 0) | 88.4 |  |
| `ID_MN_RESI` | string | 100.0 |  |
| `CO_MUN_RES` | string | 100.0 |  |
| `CS_ZONA` | string | 93.4 |  |
| `NOSOCOMIAL` | string | 91.4 |  |
| `AVE_SUINO` | string | 88.3 |  |
| `FEBRE` | string | 92.2 |  |
| `TOSSE` | string | 95.4 |  |
| `GARGANTA` | string | 81.4 |  |
| `DISPNEIA` | string | 92.0 |  |
| `DESC_RESP` | string | 91.6 |  |
| `SATURACAO` | string | 88.1 |  |
| `DIARREIA` | string | 81.2 |  |
| `VOMITO` | string | 81.8 |  |
| `OUTRO_SIN` | string | 75.7 |  |
| `OUTRO_DES` | string | 25.8 |  |
| `FATOR_RISC` | string | 41.0 |  |
| `PUERPERA` | string | 29.9 |  |
| `CARDIOPATI` | string | 33.1 |  |
| `HEMATOLOGI` | string | 30.2 |  |
| `SIND_DOWN` | string | 30.2 |  |
| `HEPATICA` | string | 30.1 |  |
| `ASMA` | string | 31.8 |  |
| `DIABETES` | string | 32.0 |  |
| `NEUROLOGIC` | string | 30.9 |  |
| `PNEUMOPATI` | string | 31.1 |  |
| `IMUNODEPRE` | string | 30.7 |  |
| `RENAL` | string | 30.3 |  |
| `OBESIDADE` | string | 30.0 |  |
| `OBES_IMC` | string | 0.2 |  |
| `OUT_MORBI` | string | 32.4 |  |
| `MORB_DESC` | string | 15.5 |  |
| `TABAG` | string | 0.0 |  |
| `VACINA` | string | 94.6 | sim |
| `DT_UT_DOSE` | timestamp[ns] | 25.4 |  |
| `MAE_VAC` | string | 4.6 |  |
| `DT_VAC_MAE` | timestamp[ns] | 0.3 |  |
| `M_AMAMENTA` | string | 4.5 |  |
| `DT_DOSEUNI` | timestamp[ns] | 2.8 |  |
| `DT_1_DOSE` | timestamp[ns] | 3.1 |  |
| `DT_2_DOSE` | timestamp[ns] | 1.8 |  |
| `ANTIVIRAL` | string | 86.8 |  |
| `TP_ANTIVIR` | decimal128(3, 0) | 12.0 |  |
| `OUT_ANTIV` | string | 0.2 |  |
| `DT_ANTIVIR` | timestamp[ns] | 11.1 |  |
| `HOSPITAL` | string | 97.9 | sim |
| `DT_INTERNA` | timestamp[ns] | 94.4 |  |
| `SG_UF_INTE` | string | 91.6 |  |
| `ID_RG_INTE` | string | 79.7 |  |
| `CO_RG_INTE` | decimal128(8, 0) | 79.7 |  |
| `ID_MN_INTE` | string | 91.6 |  |
| `CO_MU_INTE` | string | 91.6 |  |
| `NM_UN_INTE` | string | 91.6 |  |
| `UTI` | string | 90.3 | sim |
| `DT_ENTUTI` | timestamp[ns] | 25.2 |  |
| `DT_SAIDUTI` | timestamp[ns] | 14.9 |  |
| `SUPORT_VEN` | string | 89.7 |  |
| `RAIOX_RES` | decimal128(38, 10) | 73.8 |  |
| `RAIOX_OUT` | string | 10.4 |  |
| `DT_RAIOX` | timestamp[ns] | 45.7 |  |
| `AMOSTRA` | string | 97.8 |  |
| `DT_COLETA` | timestamp[ns] | 93.0 |  |
| `TP_AMOSTRA` | decimal128(9, 0) | 91.6 |  |
| `OUT_AMOST` | string | 4.0 |  |
| `PCR_RESUL` | string | 91.9 |  |
| `DT_PCR` | timestamp[ns] | 71.0 |  |
| `POS_PCRFLU` | string | 38.1 |  |
| `TP_FLU_PCR` | string | 8.3 |  |
| `PCR_FLUASU` | string | 7.0 |  |
| `FLUASU_OUT` | string | 0.2 |  |
| `PCR_FLUBLI` | string | 0.9 |  |
| `FLUBLI_OUT` | string | 0.0 |  |
| `POS_PCROUT` | string | 39.3 |  |
| `PCR_VSR` | string | 13.9 |  |
| `PCR_PARA1` | string | 0.1 |  |
| `PCR_PARA2` | string | 0.1 |  |
| `PCR_PARA3` | string | 0.3 |  |
| `PCR_PARA4` | string | 0.1 |  |
| `PCR_ADENO` | string | 3.0 |  |
| `PCR_METAP` | string | 2.6 |  |
| `PCR_BOCA` | string | 0.3 |  |
| `PCR_RINO` | string | 15.2 |  |
| `PCR_OUTRO` | string | 0.7 |  |
| `DS_PCR_OUT` | string | 0.6 |  |
| `CLASSI_FIN` | decimal128(3, 0) | 95.5 | sim |
| `CLASSI_OUT` | string | 0.6 |  |
| `CRITERIO` | decimal128(3, 0) | 92.9 |  |
| `EVOLUCAO` | string | 92.5 | sim |
| `DT_EVOLUCA` | timestamp[ns] | 83.5 |  |
| `DT_ENCERRA` | timestamp[ns] | 92.4 |  |
| `DT_DIGITA` | timestamp[ns] | 100.0 |  |
| `HISTO_VGM` | string | 100.0 |  |
| `PAIS_VGM` | string | 0.0 |  |
| `CO_PS_VGM` | decimal128(3, 0) | 0.0 |  |
| `LO_PS_VGM` | string | 0.0 |  |
| `DT_VGM` | string | 0.0 |  |
| `DT_RT_VGM` | string | 0.0 |  |
| `PCR_SARS2` | string | 2.5 |  |
| `PAC_COCBO` | string | 0.9 |  |
| `PAC_DSCBO` | string | 0.9 |  |
| `OUT_ANIM` | string | 0.2 |  |
| `DOR_ABD` | string | 80.6 |  |
| `FADIGA` | string | 82.1 |  |
| `PERD_OLFT` | string | 80.0 |  |
| `PERD_PALA` | string | 79.9 |  |
| `TOMO_RES` | decimal128(3, 0) | 62.1 |  |
| `TOMO_OUT` | string | 4.7 |  |
| `DT_TOMO` | timestamp[ns] | 8.6 |  |
| `TP_TES_AN` | decimal128(3, 0) | 42.1 |  |
| `DT_RES_AN` | timestamp[ns] | 41.0 |  |
| `RES_AN` | string | 88.4 |  |
| `POS_AN_FLU` | string | 11.4 |  |
| `TP_FLU_AN` | string | 5.9 |  |
| `POS_AN_OUT` | string | 11.0 |  |
| `AN_SARS2` | string | 1.9 |  |
| `AN_VSR` | string | 3.8 |  |
| `AN_PARA1` | string | 0.0 |  |
| `AN_PARA2` | string | 0.0 |  |
| `AN_PARA3` | string | 0.0 |  |
| `AN_ADENO` | string | 0.1 |  |
| `AN_OUTRO` | string | 0.4 |  |
| `DS_AN_OUT` | string | 0.3 |  |
| `TP_AM_SOR` | decimal128(3, 0) | 3.1 |  |
| `SOR_OUT` | string | 1.1 |  |
| `DT_CO_SOR` | timestamp[ns] | 2.3 |  |
| `TP_SOR` | decimal128(3, 0) | 2.1 |  |
| `OUT_SOR` | string | 0.5 |  |
| `DT_RES` | timestamp[ns] | 1.8 |  |
| `RES_IGG` | string | 7.6 |  |
| `RES_IGM` | string | 7.6 |  |
| `RES_IGA` | string | 7.4 |  |
| `POV_CT` | string | 100.0 |  |
| `TP_POV_CT` | string | 0.7 |  |
| `TEM_CPF` | string | 100.0 |  |
| `ESTRANG` | string | 95.3 |  |
| `VACINA_COV` | string | 100.0 | sim |
| `DOSE_1_COV` | string | 41.8 |  |
| `DOSE_2_COV` | string | 38.0 |  |
| `DOSE_REF` | string | 22.0 |  |
| `DOSE_2REF` | string | 12.7 |  |
| `DOSE_ADIC` | string | 1.7 |  |
| `DOS_RE_BI` | string | 10.7 |  |
| `FAB_COV_1` | string | 42.0 |  |
| `FAB_COV_2` | string | 38.3 |  |
| `FAB_COVRF` | string | 22.0 |  |
| `FAB_COVRF2` | string | 12.7 |  |
| `FAB_ADIC` | string | 1.7 |  |
| `FAB_RE_BI` | string | 10.7 |  |
| `LOTE_1_COV` | string | 42.0 |  |
| `LOTE_2_COV` | string | 38.2 |  |
| `LOTE_REF` | string | 21.9 |  |
| `LOTE_REF2` | string | 12.7 |  |
| `LOTE_ADIC` | string | 1.7 |  |
| `LOT_RE_BI` | string | 10.7 |  |
| `FNT_IN_COV` | string | 100.0 |  |
| `TRAT_COV` | string | 80.7 |  |
| `TIPO_TRAT` | decimal128(3, 0) | 0.2 |  |
| `DT_TRT_COV` | timestamp[ns] | 0.2 |  |
| `OUT_TRAT` | string | 0.2 |  |
| `SURTO_SG` | string | 74.9 |  |
| `CO_DETEC` | string | 72.5 |  |
| `VG_OMS` | decimal128(9, 0) | 0.2 |  |
| `VG_OMSOUT` | string | 0.1 |  |
| `VG_LIN` | string | 0.2 |  |
| `VG_MET` | decimal128(9, 0) | 0.4 |  |
| `VG_METOUT` | string | 0.0 |  |
| `VG_DTRES` | string | 0.4 |  |
| `VG_ENC` | decimal128(9, 0) | 0.5 |  |
| `VG_REINF` | string | 0.0 |  |
| `VG_CODEST` | string | 0.7 |  |
| `REINF` | string | 100.0 |  |
