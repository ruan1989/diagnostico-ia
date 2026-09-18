# InvestAI — análise de oportunidades em cripto e renda passiva

Sistema completo de análise de oportunidades de investimento, com painel web,
motor de operação autônoma na Bitget (futuros USDT-M) e um módulo separado de
análise de FIIs para renda passiva.

---

## Antes de tudo: sobre "quase 100% de acerto"

Você pediu entradas com quase 100% de acerto. Preciso ser direto: **isso não
existe**, e nenhum sistema — deste ou de qualquer outro autor — entrega isso em
mercado futuro. Se existisse, quem o tivesse não venderia nem divulgaria.

O motivo não é falta de tecnologia. É que o preço futuro depende de informação
que ainda não foi criada no momento da entrada. Qualquer sistema que exiba
"97% de acerto" está fazendo uma destas três coisas:

1. **Contando a amostra errada** — 9 acertos em 10 operações não é 90% de
   probabilidade; é ruído. Precisa de centenas de operações para significar algo.
2. **Escondendo o tamanho da perda** — dá para acertar 95% das vezes ganhando
   1 e perdendo 30 na exceção. O resultado é negativo, e o gráfico de "taxa de
   acerto" fica bonito até o dia da liquidação.
3. **Otimizando em cima do passado** (*overfitting*) — ajustar parâmetros até o
   histórico ficar perfeito produz um sistema que descreve o passado e não
   prevê nada.

**O que dá para construir, e é o que este sistema faz:** medir *expectativa
positiva* e sobreviver ao erro. Não é acertar sempre — é ganhar mais nos
acertos do que se perde nos erros, e limitar cada erro a um tamanho que não
comprometa a conta.

Por isso o sistema:

- **recusa operar** quando a evidência histórica é fraca — a maior parte das
  varreduras devolve poucos sinais, ou nenhum, e isso é o comportamento correto;
- mostra a **taxa de acerto medida** em walk-forward, encolhida para a média
  quando a amostra é pequena (nunca exibe um número inflado por sorte);
- arrisca **0,5% do capital por operação** por padrão, com stop definido *antes*
  do alvo;
- tem **kill switch** que desliga tudo no limite de drawdown e não se rearma
  sozinho;
- registra **por que** entrou em cada operação, fator por fator, para você
  poder auditar prejuízo em vez de adivinhar.

Um sistema disciplinado com 55% de acerto e razão 1:2 ganha dinheiro de forma
consistente. Um sistema com "95% de acerto" e risco descontrolado zera a conta.
Este foi construído para ser o primeiro.

---

## O que o sistema entrega

| Componente | O que faz |
|---|---|
| **Painel de oportunidades** | Cartões por sinal com plano completo (entrada, stop, 3 alvos), score decomposto em 9 fatores auditáveis, estatística histórica e o que invalida a tese |
| **Matriz de mercado** | Panorama de todas as criptos do universo: regime, RSI, ADX, ATR%, liquidez, funding e score dos dois lados |
| **Motor de backtest** | Walk-forward barra a barra, com taxas, slippage e funding, e premissas deliberadamente pessimistas |
| **Gestão de risco** | Dimensionamento por risco fixo, limites diário/semanal, drawdown, perdas consecutivas, concentração por grupo correlacionado, cooldown |
| **Execução autônoma** | Modo papel e modo real na Bitget, com stop anexado na exchange e ordens idempotentes |
| **Renda passiva (FII)** | Ranking de FIIs com penalização de armadilha de dividend yield, simulador de carteira com teto por fundo e por segmento |
| **Auditoria** | Todo sinal, ordem, bloqueio de risco e operação gravados em SQLite |

---

## Instalação

Requer Python 3.11 ou superior.

```bash
cd investment-analyzer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edite conforme a seção de configuração
```

### Conhecer o sistema sem chave de API e sem rede

O projeto inclui um gerador de mercado sintético determinístico. Ele serve para
você percorrer todo o fluxo — varredura, backtest, execução em papel, painel —
antes de conectar qualquer coisa:

```bash
INVESTAI_SYNTHETIC=1 ./run.sh
```

Abra <http://127.0.0.1:8000>. O painel exibe um aviso permanente enquanto o modo
sintético estiver ativo. **Os preços nesse modo são simulados; não servem para
decidir nada.**

### Usando dados reais de mercado

Dados públicos da Bitget (candles, funding, liquidez) **não exigem chave de
API**. Basta não ativar o modo sintético:

```bash
./run.sh
```

---

## Uso pela linha de comando

```bash
# varre o universo e lista o que passou nos dois portões de qualidade
python scripts/cli.py scan

# mede a estratégia em histórico longo, por direção
python scripts/cli.py backtest BTCUSDT --tf 1H --barras 5000

# a checagem mais importante: quantos pares realmente têm expectativa positiva
python scripts/cli.py validar

# renda passiva
python scripts/cli.py fiis
python scripts/cli.py carteira --capital 50000
```

`validar` é o comando que responde a pergunta que importa: *em quantos pares
esta estratégia tem, de fato, resultado histórico positivo?* Rode antes de
considerar dinheiro real.

---

## Conectar a Bitget

> Você mencionou "fazer o login e ficar logado". Automação em exchange **não
> funciona com a senha da conta** — funciona com chave de API. Este sistema
> nunca pede, recebe ou armazena a senha da sua conta Bitget, e você não deve
> fornecê-la a nenhum software de terceiros.

### 1. Crie a chave na Bitget

Em **Bitget → API Management → Create API Key**:

- permissões: **Read-only** + **Trade** (em Futuros)
- **NÃO** habilite **Withdraw** nem **Transfer**
- restrinja por IP, se o seu ambiente tiver IP fixo
- guarde a *passphrase* que você definir — ela é parte da credencial

Com saque desabilitado, o pior cenário em caso de vazamento é alguém operar na
sua conta. Não é bom, mas o dinheiro não sai dela.

### 2. Guarde a chave no sistema

**Pelo painel** (aba *Operação & Bitget*): informe key, secret, passphrase e uma
**senha mestra**. A chave é cifrada com Fernet (AES-128-CBC + HMAC), com chave
derivada por scrypt a partir da sua senha mestra, e gravada em
`data/bitget_keystore.json` com permissão `0600`.

Reabrindo o sistema, clique em **Destravar chave salva** e informe a senha
mestra — é isso que o "ficar logado" significa aqui, de forma segura. A senha
mestra não é guardada em lugar algum; sem ela, a chave é irrecuperável.

**Por variável de ambiente** (para servidor/Docker): preencha `BITGET_API_KEY`,
`BITGET_API_SECRET` e `BITGET_API_PASSPHRASE` no `.env`.

---

## Operação autônoma: papel antes de dinheiro real

O motor **sempre inicia em simulação**. Passar para ordens reais exige duas
ações deliberadas e separadas:

1. conectar a chave de API;
2. na aba *Operação & Bitget*, digitar exatamente a frase
   **`OPERAR COM DINHEIRO REAL`** e clicar em *Armar modo real*.

Essa fricção é intencional: ninguém deve descobrir *depois* que o sistema estava
enviando ordens de verdade.

### Roteiro recomendado

| Etapa | O que fazer | Como saber que pode avançar |
|---|---|---|
| 1 | `python scripts/cli.py validar` com dados reais | Há pares com ≥ 20 operações, acerto ≥ 50%, PF ≥ 1,35 e expectativa ≥ +0,15R |
| 2 | Motor em **papel** por 4 a 8 semanas | Aba *Histórico* mostra expectativa realizada positiva e drawdown dentro do limite |
| 3 | Modo real com o **menor capital** que você aceita perder inteiro | O desempenho real se parece com o de papel |
| 4 | Aumentar capital gradualmente | Só depois de 100+ operações reais coerentes |

Pular a etapa 2 é a forma mais comum e mais caro de descobrir que a estratégia
não funcionava no seu par, no seu horário, com o seu custo de corretagem.

O motor roda em duas frequências: **gestão de posição a cada 20 s** (stop, alvos
parciais, breakeven) e **varredura de mercado a cada 5 min**. Proteger posição
aberta é urgente; procurar entrada nova não é.

---

## Como ler um sinal

```
BTCUSDT   compra   grade B   tendência de alta        69.8
entrada 108.827,96 | stop 104.674,39 (3,82%) | alvos 116.304 / 121.288 / 127.519
probabilidade estimada 46,8%   retorno esperado +0,57R
37 operações medidas · acerto 51,4% · PF 2,03 · pior sequência: 5 perdas
```

- **Grade A** — confluência alta **e** histórico medido positivo. Operável.
- **Grade B** — confluência boa, estatística aceitável. Operável.
- **Grade C** — técnica interessante, mas amostra pequena ou desempenho abaixo
  dos mínimos. **O motor não envia ordem.** Fica só em acompanhamento.
- **Rejeitado** — não passou no score mínimo ou reprovou em filtro duro
  (volatilidade extrema, liquidez baixa, risco/retorno ruim).

Repare que **probabilidade de 46,8% com retorno esperado positivo não é
contradição**: com alvo em 1,8R, acertar menos da metade das vezes ainda dá
lucro. É assim que seguidores de tendência ganham dinheiro. Um sistema que
promete acerto alto normalmente tem alvo pequeno e stop enorme — o oposto
disso.

**"Pior sequência: 5 perdas"** é o número que mais importa na prática. Se cinco
prejuízos seguidos fariam você desligar o robô, o tamanho de posição está alto
para o seu perfil, não para a matemática.

### Os 9 fatores do score

| Fator | Peso | O que mede |
|---|---|---|
| `alinhamento_tf` | 18% | Estrutura de médias e concordância entre 15m, 1H e 4H |
| `forca_tendencia` | 14% | ADX e diferença entre +DI e −DI |
| `momentum` | 13% | Histograma do MACD normalizado pelo preço |
| `estrutura` | 12% | Posição no canal de Donchian (excluindo o candle atual) |
| `localizacao` | 11% | Distância à EMA9 em ATR — **penaliza entrada esticada** |
| `rsi_contexto` | 9% | RSI lido conforme o regime (confirmação em tendência, exaustão em faixa) |
| `volume` | 8% | Volume contra a média de 20 |
| `volatilidade` | 8% | ATR% — pune tanto volatilidade baixa demais quanto extrema |
| `funding` | 7% | Posicionamento, usado **de forma contrária** |

O fator de funding é invertido de propósito: funding muito positivo significa
multidão comprada pagando para continuar comprada. Entrar *long* ali é entrar
no fim da fila de liquidação.

---

## Gestão de risco

Este é o módulo mais importante do sistema. Os padrões são conservadores.

| Limite | Padrão | Efeito |
|---|---|---|
| Risco por operação | 0,5% do capital | Define o tamanho da posição a partir da distância do stop |
| Perda máxima no dia | 2% | Bloqueia novas entradas até o dia seguinte |
| Perda máxima na semana | 5% | Bloqueia novas entradas |
| Drawdown máximo | 10% | **Kill switch** — para tudo, com rearme manual |
| Perdas consecutivas | 4 | Pausa o robô |
| Posições simultâneas | 3 | Limita exposição total |
| Alavancagem máxima | 5x | Teto; a alavancagem real é consequência do stop |
| Correlação | 2 por grupo | Impede 3 posições que são a mesma aposta |
| Cooldown após stop | 60 min | Evita revanche imediata |

Dois pontos de projeto que merecem destaque:

**A alavancagem não é uma escolha independente.** Você define quanto quer
arriscar (0,5%) e onde fica o stop; o tamanho da posição — e portanto a
alavancagem — sai dessas duas coisas. É por isso que o sistema não tem um campo
"alavancagem 20x": isso seria escolher o risco pelo lado errado.

**As janelas de perda são de calendário, não relativas ao boot.** Reiniciar o
processo não reabre um limite diário já estourado.

---

## Renda passiva: FIIs

O erro mais comum em FII é comprar pelo dividend yield mais alto da lista. DY
alto quase sempre tem explicação: rendimento não recorrente, contrato vencendo,
inquilino único em dificuldade, vacância subindo. O score aqui trata DY como
**curva**, não como escala — acima da faixa saudável do tipo de fundo, cada
ponto extra **piora** a nota.

Além da soma ponderada de 7 fatores, há uma **camada de teto**: defeito grave
limita o score final independentemente do resto. Um fundo com 26% de vacância
não passa de 32 pontos, mesmo com bons números em todo o resto — porque uma
média ponderada diluiria esse defeito de forma perigosamente enganosa.

```bash
python scripts/cli.py fiis
python scripts/cli.py carteira --capital 50000
```

O simulador de carteira distribui proporcionalmente ao score, com teto de 20%
por fundo e **no máximo 2 fundos por segmento**.

### Os dados de FII precisam ser atualizados por você

Fundamentais de FII (P/VP, vacância, número de imóveis, PL) não estão
disponíveis de forma confiável e gratuita em nenhuma API única. O arquivo
`data/fiis_snapshot.json` que acompanha o projeto é um **modelo de
preenchimento com valores ilustrativos** — os tickers e segmentos são reais, os
indicadores não. Ele está datado no passado de propósito, para que o sistema
**sempre** marque `snapshot_desatualizado` e o painel exiba o aviso em vermelho.

Antes de qualquer aporte real, substitua cada campo pelos números do relatório
gerencial mensal do fundo e dos informes publicados na B3 (fundos.net). Se
configurar `BRAPI_TOKEN`, o sistema atualiza o **preço** pela brapi.dev e
reescala DY e P/VP de forma coerente — mas vacância, PL e número de imóveis
continuam sendo sua responsabilidade.

---

## Arquitetura

```
src/investai/
├── config.py            Configuração + validação cruzada entre blocos
├── models.py            Tipos centrais (Candle, Signal, Position, Trade, ...)
├── datahub.py           Cache e paginação de histórico
├── store.py             SQLite: sinais, operações, posições, auditoria
├── api.py               FastAPI: API HTTP + painel
├── indicators/ta.py     EMA, RSI, ATR, MACD, Bollinger, ADX, Donchian, VWAP
├── analysis/
│   ├── features.py      Extração de features (com garantia anti-lookahead)
│   ├── confluence.py    Score de 9 fatores, plano de trade, classificação
│   └── screener.py      Orquestra dados + backtest + sinal por par
├── backtest/
│   ├── engine.py        Walk-forward barra a barra
│   └── metrics.py       Win rate, PF, expectativa, drawdown, Sharpe
├── risk/manager.py      Dimensionamento e circuit breakers
├── trading/
│   ├── executor.py      Execução em papel e real
│   └── engine.py        Loop autônomo com travas do modo real
├── passive/
│   ├── fii.py           Score de FII com camada de teto
│   └── data.py          Snapshot local + atualização de preço
└── web/                 Painel (HTML/CSS/JS, sem dependências externas)
```

### A garantia anti-lookahead

Um backtest que "olha o futuro" produz resultados espetaculares e inúteis. Duas
decisões de projeto impedem isso estruturalmente:

1. Todas as funções de indicador devolvem listas do **mesmo tamanho** da
   entrada, com `None` onde não há dados suficientes. Isso elimina o
   desalinhamento de índices, que é a causa mais comum do problema.
2. O canal de Donchian **exclui o candle atual** — um rompimento é medido
   contra o passado, não contra si mesmo.

E há testes que verificam isso diretamente: as features calculadas no candle *i*
têm que ser idênticas quer existam ou não candles depois de *i*, e rodar o
backtest sobre um prefixo da série tem que produzir exatamente as mesmas
entradas que rodar sobre a série completa.

### Premissas do backtest (todas pessimistas de propósito)

- O sinal nasce no **fechamento** da barra e é executado na **abertura da barra
  seguinte**. Nunca há execução no mesmo candle que gerou o sinal.
- Se stop e alvo são tocados na mesma barra, assume-se que o **stop veio
  primeiro** (não há dado intrabar para desempatar).
- Taxa taker (0,06%) na entrada e em cada saída, mais slippage (0,03%) sempre
  contra o operador.
- Funding debitado a cada 8 h de posição aberta.
- A última posição é encerrada a mercado no fim da série, para não inflar o
  resultado com um trade aberto e lucrativo no papel.

### Honestidade estatística embutida

- **Encolhimento bayesiano**: a probabilidade exibida mistura o histórico com um
  *prior* de 40% com peso de 25 operações. Com 8 operações a 87,5% de acerto, o
  sistema mostra ~51%, não 87%.
- **Profit factor sem perdas** é reportado como marcador (99), nunca como "a
  estratégia é perfeita" — e o portão de amostra mínima impede esse caso de
  gerar ordem.
- **Amostra mínima de 20 operações** por par e direção. Abaixo disso, o sinal
  vira grade C e não é operado, qualquer que seja o score.

---

## Segurança

- A senha da conta Bitget **nunca** é usada, pedida ou armazenada.
- Chave de API cifrada em disco (scrypt + Fernet), arquivo `0600`, segredo nunca
  devolvido pela API HTTP nem exibido em log (o `repr` da credencial é
  mascarado de propósito, para não vazar em traceback).
- O serviço escuta em `127.0.0.1` por padrão. Todo endpoint que muda algo exige
  o cabeçalho `X-API-Token`, comparado em tempo constante. Sem
  `INVESTAI_API_TOKEN` definido, um token é gerado no boot e impresso no log.
- Ordens levam `clientOid` determinístico: um reenvio após falha de rede não
  cria posição dobrada.
- O stop-loss vai **anexado à ordem de abertura**. Se o robô cair logo após
  abrir, a proteção já está na exchange.
- O executor nunca chama saque ou transferência.

> Se você expuser este painel na internet, coloque-o atrás de HTTPS com
> `INVESTAI_API_TOKEN` forte. Quem alcança o painel com o token pode enviar
> ordens na sua conta.

---

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

245 testes cobrindo indicadores, ausência de lookahead, métricas de backtest,
circuit breakers de risco, idempotência de ordem, cifragem do keystore,
armadilhas de DY em FII e autenticação da API.

Os testes rodam offline, com dados sintéticos determinísticos e timestamp fixo —
não dependem de rede nem de chave de API.

---

## Limitações conhecidas

- **A estratégia é de seguimento de tendência com confirmação multi-timeframe.**
  Ela vai de mal a pior em mercado lateral e prolongado, e os filtros de regime
  reduzem esse dano sem eliminá-lo.
- **O backtest não modela livro de ofertas.** Em par de baixa liquidez ou em
  choque de volatilidade, o slippage real pode ser muito maior que os 0,03%
  assumidos. O filtro de liquidez mínima de US$ 20 milhões/24h existe por isso.
- **Não modela liquidação forçada.** Com os limites padrão a margem isolada não
  chega perto disso, mas se você aumentar a alavancagem, esse cenário passa a
  existir e não está simulado.
- **Sem análise de notícia ou evento macro.** Anúncio regulatório, quebra de
  exchange ou decisão de juros invalidam qualquer leitura técnica em segundos.
- **O snapshot de FII não é automático** — ver a seção de renda passiva.
- **A Bitget devolve no máximo 1000 candles por chamada.** O hub pagina para
  trás, mas o histórico disponível ainda limita o tamanho da amostra em pares
  novos.

---

## Aviso final

Este software é uma ferramenta de análise e automação, não consultoria de
investimento. Não sou consultor autorizado pela CVM, e nada aqui é recomendação
personalizada. Mercado futuro de criptomoedas com alavancagem pode causar perda
superior ao capital aplicado. FII não tem rendimento fixo nem garantido, e a
distribuição pode ser reduzida ou suspensa.

Nenhum número exibido por este sistema é uma promessa de resultado. Use capital
que você aceita perder inteiro, comece em modo papel, e desconfie de qualquer
sistema — inclusive deste — que apresente resultado bom demais.
