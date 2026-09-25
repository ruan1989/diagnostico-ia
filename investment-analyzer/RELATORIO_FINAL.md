# Relatório final — §74

Gerado em 2026-09-25, no commit desta branch.
**Regra desta página: nenhum item é declarado OK sem evidência verificável.**
Onde a evidência não existe, o item está marcado como tal, e não omitido.

---

## 1. Resumo executivo

| | |
|---|---|
| Módulos | 86 arquivos, 23.656 linhas em `src/investai/` |
| Testes | 40 arquivos, 13.916 linhas, **1.354 passando**, 1 skip condicional |
| Análise estática | pyflakes limpo em `src/`, `tests/`, `scripts/`; `node --check` no painel |
| Auditoria de lookahead | 23 verificações, veredicto LIMPO, roda em job próprio no CI |

**Este sistema não está autorizado a operar dinheiro real neste momento**, e o
próprio diagnóstico diz isso. Os motivos estão no item 14.

---

## 2. Classificação por componente

Estados usados: `OK` (construído e com evidência), `CORRIGIDO` (tinha defeito,
foi consertado, com o defeito descrito), `WARNING` (funciona com ressalva),
`NOT IMPLEMENTED`, `NOT VALIDATED` (existe, nunca foi exercitado de verdade),
`BLOCKED` (depende de algo fora do alcance deste ambiente).

### 2.1 Travas de dinheiro real

| § | Componente | Estado | Evidência |
|---|---|---|---|
| 5, 43 | Guarda de fase no executor | **CORRIGIDO** | `Executor.abrir` consultava só a gestão de risco: com o modo real armado, a ordem ia para a corretora independentemente de a estratégia ter passado no out-of-sample. Era o único caminho de dinheiro real sem evidência. `trading/guarda.py` fecha; 57 testes, incluindo um que prova que uma reprovação duas horas após armar já barra a ordem seguinte |
| 32 | Idempotência atravessando restart | **CORRIGIDO** | `clientOid` só cobria a mesma sessão. `trading/idempotencia.py` grava a intenção ANTES do envio (um teste lê o banco de dentro da chamada à corretora para provar a ordem) e recusa reenvio sob incerteza. 10 reinícios em laço produzem uma ordem só |
| 33 | Reconciliação com a corretora | **OK** | `ops/reconciliacao.py`; divergência PAUSA e não corrige. 33 testes. Falha de consulta vira `NAO_CONFERIDO`, nunca `COERENTE` |
| 66–68 | `diagnostico` / `status` / `emergency_stop` | **OK** | CLI + scripts nomeados + endpoints. A trava da parada sobrevive a reinício — verificado com processo novo lendo o mesmo banco. Trava vem ANTES do fechamento: se fechar falhar, o sistema fica travado, não meio-aberto |

### 2.2 Estatística e modelos

| § | Componente | Estado | Evidência |
|---|---|---|---|
| 15–19, 21 | Walk-forward, Monte Carlo, overfitting, EV com IC | **OK** | Pré-existente, mantido; gate reprova a estratégia atual (ver item 14) |
| 20 | Calibração de probabilidade | **CORRIGIDO** | `validation/calibracao.py`. Três defeitos achados medindo: `skill > 0` aprovava o modelo da taxa-base por resíduo de ponto flutuante (5e-15); a identidade de Murphy não fecha para previsão contínua e o resíduo agora é campo visível; o recalibrador isotônico tinha linha morta cujo comentário prometia média ponderada |
| 13 | Agente ML | **OK** | `ml/` + `agents/ml.py`. `prever_calibrado` devolve `None` sem calibração fora da amostra — não há caminho lateral para a saída crua. Em série sintética o modelo não tem poder preditivo (skill −0,002) e o sistema **recusa** emitir probabilidade; em dado com sinal real recupera os coeficientes verdadeiros (0,97 contra 1,1) |
| 44 | ModelRegistry | **OK** | Versiona por (par, timeframe, lado); recusa promover modelo sem calibração; para de servir modelo que perdeu calibração mesmo sem despromoção |
| 22 | Modelos correlacionados | **CORRIGIDO** | `analysis/redundancia.py`. A primeira fórmula devolvia o número efetivo de fontes, não o peso, e dava o MESMO resultado para três fontes idênticas e três independentes |
| 47 | Drift | **CORRIGIDO** | `ops/drift.py`, quatro tipos. Dois limiares fixos estavam dentro do ruído: ECE (piso medido 0,041 a n=600 contra corte de 0,02) e skill (desvio 0,038 contra corte de 0,05). Ambos agora derivam do tamanho da amostra |
| 14 | Auditoria de leakage | **OK** | `validation/leakage.py`; metade dos testes são funções deliberadamente defeituosas, porque "LIMPO" de um auditor que nunca acha nada não significa nada. Dois limites reais do método estão documentados no módulo |
| 50–53 | Métricas segmentadas | **CORRIGIDO** | `reporting/segmentado.py`. Uma checagem da primeira versão era matematicamente impossível (a média global é a média ponderada dos segmentos). Substituída por reponderação de composição e detecção de Simpson entre grupos |

### 2.3 Execução e dados

| § | Componente | Estado | Evidência |
|---|---|---|---|
| 3, 34 | WebSocket | **CORRIGIDO / BLOCKED** | `exchanges/feed_ws.py` + `feed_estado.py`. Validado contra servidor WS real em 127.0.0.1. Três defeitos: `parar()` não parava (suíte caiu de 33s para 1,3s); reconexão demorava 20s a mais por `gather` no ping; queda antes do primeiro dado não gerava lacuna. **BLOCKED** contra a Bitget: a rede deste ambiente recusa `bitget.com` |
| 4, 42 | Demo trading | **CORRIGIDO / BLOCKED** | Fase DEMO entre SHADOW e ASSISTIDO, com gate de encanamento. Substituição em massa quebrou os caminhos de volta — posição vinda como `SBTCSUSDT` faria a reconciliação acusar posição fantasma e ausente no mesmo ciclo. **BLOCKED** contra a Bitget real |
| 38 | Universo com remoção automática | **CORRIGIDO** | `assets/universo.py` com histerese assimétrica. Ativo SEM dado medido saía com score 100 e liderava o ranking: falta de informação estava sendo premiada. Agora o score é descontado pela fração medida (100 vira 55) |
| 60 | Alertas externos | **OK / NOT VALIDATED** | `reporting/notificacao.py` com três filtros e resumo de supressões. Token nunca em log, `repr`, `estado()` ou mensagem de erro. **NOT VALIDATED** contra o Telegram real: não há token neste ambiente |
| 62 | Testes de chaos | **OK** | 20 testes. Um teste meu era vazio (`or True`) e virou dois honestos: a idempotência **não** cobre relógio andando para trás, e a camada que cobre é o gestor de risco |

### 2.4 Interface

| § | Componente | Estado | Evidência |
|---|---|---|---|
| 54–59 | Painel | **CORRIGIDO** | Aba *Travas & diagnóstico*, verificada no navegador com dados reais. Três defeitos: `id` duplicado fazia o código escrever na tabela de OUTRA aba; `.bloco` não existia no CSS e o diagnóstico vazava da tela; a aba dizia "REAL" em vermelho enquanto o cabeçalho dizia "SIMULAÇÃO" |

---

## 3. O que continua faltando, sem rodeio

* **Bitget real**: nenhum item marcado `BLOCKED` foi exercitado contra a
  corretora. A rede deste ambiente recusa `bitget.com` — confirmado por curl,
  pelo Chromium e pelo relatório do próprio proxy. Existe o comando
  `conferir-bitget` e o workflow manual para rodar de uma rede que funcione.
* **Telegram**: o canal foi testado com transporte falso, nunca com o real.
* **`iniciar.bat`**: escrito, nunca executado — não há shell Windows aqui.
* **Shadow mode com amostra**: 0 de 40 decisões liquidadas. A expectativa que
  o painel mostra é anedota, e o painel diz isso.

---

## 4. A estratégia atual não pode operar real

Não é opinião; é o que o gate mediu:

* expectativa out-of-sample **+0,171R** central;
* **piso do intervalo de confiança: −0,169R** — o intervalo cruza zero;
* degradação in-sample → out-of-sample de **41,9%**;
* reprovada em **3 de 11 critérios**: `ic_expectativa_inferior`,
  `ev_pessimista_r`, `overfit_veredicto`.

Isso não é defeito do código de validação. É o código de validação
funcionando.

---

## 5. Como conferir cada afirmação desta página

```bash
cd investment-analyzer
python -m pytest -q                    # 1.354 testes
python -m pyflakes src/ tests/ scripts/
python scripts/diagnostico.py          # saída 1 = não pode operar real
python scripts/status.py               # o que está acontecendo agora
./iniciar.sh                           # sobe o painel
```

O job `vazamento` no CI roda a auditoria de lookahead a cada commit.
