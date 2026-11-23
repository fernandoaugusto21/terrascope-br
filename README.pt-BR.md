[English](README.md) | Português (Brasil)

# terrascope-br

Camadas de dados brasileiros para o [Terrascope](https://github.com/a-shygun/Terrascope), empacotadas como plugin. Ele
adiciona uma aba **BRASIL** com fontes públicas brasileiras. O próprio Terrascope não precisa mudar para carregá-lo.

Requer um build do Terrascope com suporte a plugins de camadas e de abas (entry points `terrascope.layers` e
`terrascope.tabs`), com o suporte a traduções e a vista inicial das abas. Isso está na branch `main` do fork, e ainda
não em uma versão publicada.

## Instalação

O Terrascope 1.0.0 do PyPI não tem suporte a plugins; por isso, instale antes um build do Terrascope com plugins, a
partir da branch `main` do fork:

    pip install "terrascope @ git+https://github.com/fernandoaugusto21/Terrascope@main"
    pip install -e .                 # from a checkout of this repository, in the same environment
    python -m terrascope_br          # checks that the core has plugins, and that the tab and its layers are registered
    terrascope --list-tabs           # BRASIL appears with origin plugin:terrascope-br
    terrascope -t brasil             # open the tab (it starts on Brazil)

O `python -m terrascope_br` termina com código 1 e diz o que falta quando uma linha da verificação falha.

Quando sair uma versão com suporte a plugins, esta linha passa a apontar para essa versão.

`pip uninstall terrascope-br` remove de novo a aba e as camadas.

### Windows: build independente (Scoop)

Cada merge na `main` gera um `terrascope.exe` independente com este plugin e a branch `main` do core, e o publica como
uma release do GitHub (`windows-<core commit>-br<plugin commit>`) com o seu manifesto do Scoop
(`.github/workflows/build-windows.yml`). O build é uma pasta no formato `--onedir` do PyInstaller, e não um arquivo
único: o zip traz o `terrascope.exe` e a sua pasta `_internal` na raiz, então o app inicia e encerra sem descompactar
tudo numa pasta temporária a cada execução. O app do Scoop se chama `terrascope-br` (ele instala o `terrascope.exe`), para
não conflitar com o app `terrascope` do próprio core; não instale os dois. Para instalar ou atualizar para o build mais
recente:

    scoop install https://github.com/fernandoaugusto21/terrascope-br/releases/latest/download/terrascope-br.json
    scoop uninstall terrascope-br; scoop install <same address>    # to move to a newer build

Cada release se chama `0.<N> (<resumo>)`: N sobe um a cada release, e o resumo junta os títulos dos pull requests que
este build tem e a release anterior não tinha, no core e aqui (a tag da release anterior indica os commits a partir dos
quais ela foi gerada; `packaging/windows/release_name.py`). Só um build da `main` do core é marcado como latest; um
build de outra branch, tag ou commit do core é uma pre-release. Os dez builds mais recentes são mantidos.

O build também roda quando a `main` do core muda: na hora, quando o workflow `notify-plugin.yml` do core tem um token
para enviar o sinal (secret `PLUGIN_DISPATCH_TOKEN` no repositório do core: um token fine-grained com
"Contents: read and write" neste repositório), e, caso contrário, uma vez por dia, quando a `main` do core mudou desde
o último build (o GitHub desliga os workflows agendados depois de 60 dias sem atividade no repositório; o Actions
mostra um botão para religá-los). Pull requests geram e verificam o executável sem publicá-lo. Um build também pode ser
iniciado à mão (Actions, "Windows build", com uma branch, tag ou commit do core).

## Camadas

| Camada (`--disable-layer`) | Tecla | Fonte | Atualização | Observações |
|---|---|---|---|---|
| `br_ufs` | U | IBGE, contornos dos estados | uma vez a cada 30 dias | arquivo nacional até o zoom 32, depois um arquivo por estado visível (veja abaixo) |
| `br_municipios` | I | IBGE, contornos dos municípios | uma vez a cada 30 dias | a partir do zoom 8; arquivo nacional até o zoom 32, depois um arquivo por estado visível |
| `br_fires` | F | INPE Queimadas | 10 min | focos das últimas 3 h |
| `br_quakes` | B | catálogo do IAG-USP, ou USGS se o IAG-USP falhar | 30 min | últimos 30 dias, Brasil e vizinhos |
| `br_radar` | T, ou o `ligado \| desligado` da legenda | IAG-USP Chuva Online, radar de 36 km | 10 min | oculta quando o quadro mais recente tem mais de 30 min; desligado, não busca nada e não tem deslizador (a escolha fica guardada entre as execuções) |
| `br_global_rain` | J | radar do RainViewer (o quadro e a paleta do core), dentro do contorno do Brasil | verificação a cada 5 min, um quadro novo a cada 10 min, aproximadamente | dBZ; desenhada abaixo de `br_radar`; nada é desenhado até o mapa de estados do IBGE estar no cache |
| `br_global_temp` | M | temperaturas das cidades (a camada de cidades do core, com os provedores abaixo), só cidades do Brasil | 15 min por cidade | cores relativas, como na aba CLIMA; nesta aba os rótulos substituem os nomes de cidades do mapa base; a partir do zoom 32, um rótulo por município (veja abaixo) |
| `br_inmet_alerts` | V | INMET, avisos meteorológicos ativos | 30 min | avisos de hoje e dos próximos dias, como áreas; oculta quando os dados têm mais de 1 h |
| `br_inundation_cge` | (chave na legenda) | CGE, estado de risco de alagamento de cada uma das 32 subprefeituras da cidade de São Paulo; contornos do GeoSampa | 5 min (contornos: 30 dias) | a partir do zoom 16; observação / atenção / iminência de transbordamento / alerta, lidos das cores do mapa de alertas do CGE (veja abaixo); estados ocultos quando têm mais de 1 h |
| `br_viario` | (chave na legenda) | vias do OpenStreetMap dos 39 municípios da Região Metropolitana de São Paulo (ODbL), um arquivo gerado toda semana e publicado como asset de uma release | no máximo uma vez por semana; guardado para uso offline | desligada ao abrir a aba; a partir do zoom 16 autopistas e vias expressas, 64 vias primárias e secundárias, 256 terciárias, 512 ruas locais; uma cor por classe (veja abaixo) |

A camada de pluviômetros da SP Águas (`br_gauges`) está inativa desde 22/11/2026: ela não é registrada e não faz
nenhuma requisição. O código continua em `sources/spaguas.py` para uso futuro (`ENABLED`).

Os nomes dos municípios (rótulos de clima e provedores) vêm da lista de localidades do IBGE, mantida por 30 dias.

### Histórico do radar

Cada quadro é desenhado como o core desenha o seu radar do clima: a cor de cada pixel é uma faixa da própria barra de
cores do quadro (faixas de 1 dBZ, de 10 a 60), e cada célula do terminal tem duas leituras, a metade de cima e a de
baixo, cada uma a faixa média de 3 x 3 pontos do quadro (desenhada onde ao menos metade deles tem chuva), na cor dessa
faixa. Uma célula com metades diferentes é um meio bloco (▀ / ▄); uma com metades iguais é uma cor de fundo, e os
contornos e nomes ficam por cima.

O site do Chuva Online serve só os seus últimos dez quadros, em dez arquivos que são regravados à medida que chegam
quadros novos (sem arquivo histórico). A camada guarda todos os quadros que vê na última hora, e o seu deslizador os
percorre, como o deslizador de radar do core (`[` e `]`, ou o mouse na linha do deslizador; o quadro mais recente é a
posição `LIVE`, `AO VIVO` em pt-BR). A animação usa as configurações de animação do radar do core: `--radar-animate` a
liga, e `frame_seconds` / `frame_hold_last_seconds` em `layers.weather.radar` no arquivo de configuração definem o
tempo. Com um core cujo deslizador tem botão de reprodução, o ▶ depois da legenda do deslizador (ou a tecla Espaço) toca
a animação a partir do quadro mais antigo, e o ■ a pausa no quadro mostrado.
O histórico começa com os dez quadros da primeira busca (cerca de 30 minutos) e cresce a cada busca.

A linha do radar na legenda tem a chave `ligado | desligado` (a tecla T faz o mesmo). Desligado, o radar não desenha
nada, não busca nada e não tem deslizador. A escolha fica guardada entre as execuções em `br/radar.json` na pasta de
configuração do Terrascope (`terrascope --show-paths` mostra a pasta); arquivo ausente ou ilegível significa ligado.

### Chuva e temperatura globais

Estas duas camadas reaproveitam o código do core (`terrascope.layers.weather`): a busca e a paleta do RainViewer, e a
camada de temperatura das cidades com o seu posicionamento de rótulos. Mudanças feitas no código de clima do core
chegam a esta aba.

- **A chuva** é desenhada só dentro do contorno do Brasil, a partir do mapa de estados do IBGE que a `br_ufs` mantém no
  seu cache. A camada não baixa esse mapa por conta própria: enquanto a `br_ufs` não o buscar, a legenda diz que está
  aguardando. A camada guarda a sua própria cópia do quadro mais recente do RainViewer; então, enquanto a aba CLIMA e
  esta camada mostram chuva, os quadros mais recentes são baixados duas vezes. Compartilhar o armazenamento de quadros
  do core evitaria isso; essa é uma mudança no core e não é feita aqui.
- **As cores da chuva** são os níveis do RainViewer do core, em dBZ. Não são as cores do IAG-USP: o radar BR diz fraca,
  de 10, 20, 30, 40 e 50 ou mais dBZ, e é desenhado por cima da chuva global onde os dois cobrem a mesma célula.
- **A temperatura** rotula só as cidades do Brasil da lista de localidades do Natural Earth. As cores são relativas à
  cidade mais fria e à mais quente lidas até o momento, como na aba CLIMA, e não a uma escala fixa.
- Cada camada tem a sua própria chave: a tecla (J, M) ou a escolha `on | off` (`ligado | desligado`) na sua linha da
  legenda. A aba liga a camada inteira.
- A aba BRASIL oculta os nomes de capitais e cidades do mapa base (`place_names: false`), porque os rótulos de
  temperatura ocupam o lugar deles, como faz a aba CLIMA.

### Sismos: duas fontes

O catálogo do IAG-USP é a fonte principal. Ele tem eventos pequenos que o feed do USGS não mostra. Quando a consulta
ao IAG-USP falha (erro de rede, erro HTTP, resposta vazia ou ilegível), a camada usa o feed `all_month` do USGS para a
mesma área e o mesmo período. O título da legenda e a caixa de informações de cada sismo dizem qual fonte está em uso
(`IAG-USP` ou `USGS (fallback)`, em pt-BR `USGS (reserva)`). Se as duas falharem, a camada mantém os últimos dados
bons, ou fica vazia com o motivo na legenda quando não há nenhum.

### Avisos meteorológicos

A camada do INMET desenha a área de cada aviso ativo, preenchida com a cor do próprio aviso (`aviso_cor`), e um
marcador `!` no meio do retângulo da área. A legenda tem uma entrada por cor presente nos dados, rotulada com a sua
gravidade. Um aviso que aparece nas duas listas da API (hoje e próximos dias) é desenhado uma vez só. Avisos sem área
ficam de fora. A API informa os horários de início e fim sem fuso horário; eles são mostrados como chegam.

### Viário (OpenStreetMap)

A camada `br_viario` desenha as vias da Região Metropolitana de São Paulo, do OpenStreetMap (© OpenStreetMap
contributors, ODbL 1.0). Cada faixa de zoom tem a sua simplificação, meio ponto no zoom mais fino da faixa (cerca de
33 m de 512 a 1024, 17 m de 1024 a 2048, 8 m de 2048 a 4096); um arquivo gerado antes das faixas mais finas continua
sendo desenhado, na faixa mais fina que ele tem nos zooms maiores. Ela fica desligada quando a aba abre: o seu interruptor está na primeira linha da legenda. Só as
vias da vista são desenhadas, uma cor por classe. Com um core que tem `LayerRender.line_layers`, as vias ficam abaixo dos
contornos do IBGE (um core mais antigo as desenha por cima, como marcadores).

O plugin não lê o OpenStreetMap diretamente. O `packaging/roads/build_roads.py` lê as vias de um extrato da Geofabrik
(`--source pbf`; a Overpass API é uma reserva lenta), mantém as que têm um nó dentro de um dos 39 municípios da RMSP
(`--area rmsp`, pela malha municipal do IBGE; `--area box` mantém um retângulo) e grava `br-viario-rmsp.npz` (formato em
`src/terrascope_br/roads_format.py`). O workflow do Windows não o gera: o `.github/workflows/build-roads.yml` o gera
toda semana e o publica numa release chamada `roads-<hora>`, marcada como não sendo a mais recente. A camada baixa o
arquivo da release mais nova que o tem, na primeira vez em que é ligada, e procura um mais novo uma vez por semana.
Offline, usa o arquivo do cache.

Para gerá-lo à mão (com numpy e osmium instalados): `python packaging/roads/build_roads.py --source pbf --pbf
sudeste-latest.osm.pbf --out out/br-viario-rmsp.npz`. Medidas do arquivo e do custo da camada:
[`docs/performance-viario.md`](docs/performance-viario.md).

### Risco de alagamento do CGE por região

O CGE publica o estado de cada subprefeitura da cidade de São Paulo só como imagem: a página `mapas.jsp?arq=alertas`
mostra a imagem `/cgesp/mapa_sp_geoserver.png` (refeita a cada poucos minutos) e um mapa de imagem HTML com um círculo
por região, na sua posição na imagem, com o seu nome. A camada lê as cores dentro de cada círculo: cinza é
observação, amarelo atenção, laranja iminência de transbordamento, vermelho alerta (a legenda dessa imagem). Os nomes
do mapa de imagem são associados às siglas do GeoSampa por uma tabela fixa; uma região que a tabela não conhece fica
de fora. O horário dos dados é o `Last-Modified` da imagem. Uma mudança no visual dessa página ou imagem aparece como
"update failed" ("falha na atualização" em pt-BR) na legenda, nunca como um estado errado. Os contornos são a camada
`geoportal:subprefeitura` do GeoSampa (WFS), simplificados para cerca de 110 m e mantidos por 30 dias.

Por região, a camada também mostra:

- **O nome** de cada subprefeitura (com a grafia do próprio CGE), a partir do zoom 128; um nome que cobriria outro
  fica de fora. Clicar em um nome abre a região na caixa de informações.
- **Os pontos de alagamento do dia** (`alagamentos.jsp`, lido a cada 5 minutos junto com os estados): o CGE os lista
  por região, sem posição, então uma região com pontos ativos ganha `≈<número>` depois do nome (só a marca, abaixo do
  zoom 128), em vermelho quando um deles está intransitável e em amarelo quando estão transitáveis. A caixa de
  informações lista os pontos do dia, os ativos primeiro (horário, rua, transitável ou não), no máximo três, e depois `+K mais`. Pontos de alagamento com
  mais de 1 h não são mostrados. Se só a lista de alagamentos falhar, os estados continuam sendo atualizados.
- **Na caixa de informações de uma região:** a sua zona; a leitura da estação do CGE daquela região (temperatura,
  chuva desde a zeragem diária da estação, vento), quando ela tem uma (27 das 32 regiões; o
  `estacoes-meteorologicas.jsp` associa os nomes das estações às regiões por uma tabela fixa); e a previsão do CGE
  para hoje e amanhã. **O CGE publica uma previsão para a cidade inteira, não uma por região**, e a caixa avisa isso.
  A estação e a previsão são buscadas quando a região é selecionada e mantidas por 10 e 30 minutos.

## Legenda

Cada camada usa no máximo duas linhas da legenda: o seu título e, em seguida, ou a sua chave de cores, ou uma mensagem
quando os dados pedem atenção (ainda sem dados, a última atualização falhou, ou sem atualização desde um horário). Os
créditos não se repetem na legenda; eles ficam na linha de atribuição da aba BRASIL.

## Clima das cidades no Brasil

O plugin registra dois provedores de clima (entry point do core `terrascope.weather_providers`), usados para todas as
cidades que o core rotula com o seu clima, em qualquer aba:

| Onde | Condições atuais | Previsão |
|---|---|---|
| município de São Paulo | CGE (centro de gerenciamento de emergências climáticas da prefeitura), estação da Sé | previsão estendida do CGE: hoje e mais dois dias, com madrugada, manhã, tarde e noite |
| qualquer outro município do Brasil | a estação automática do INMET mais próxima, a até 50 km e com no máximo 3 h (veja abaixo); senão, Open-Meteo | INMET pelo código do IBGE (5 dias); CPTEC/INPE (7 dias) quando o INMET falha |
| fora do Brasil | Open-Meteo | Open-Meteo |

A caixa de informações mostra a FONTE (por exemplo `Open-Meteo · previsão INMET`), e o título da previsão termina com o
provedor da previsão. O CGE publica apenas páginas HTML; elas são lidas pelas classes CSS, então uma mudança de layout
lá aparece como "INDISPONÍVEL" e o próximo provedor (INMET para a previsão, Open-Meteo para as condições) responde. As
previsões do CGE e do INMET não têm valores por hora: a caixa de previsão mostra os períodos do dia delas no lugar do
gráfico por hora (CGE: madrugada, manhã, tarde, noite; INMET: manhã, tarde, noite, nos primeiros dias), cada um com o
seu ícone. O INMET também informa o nascer e o pôr do sol. As linhas de chuva e vento ficam sem dados para eles.

O município de uma cidade é encontrado nos contornos do IBGE: o estado, no arquivo nacional de estados; depois o
município, no arquivo `minima` próprio desse estado (`mun-<state>-minima.geojson`, baixado na primeira vez e mantido
por 30 dias). O id do CPTEC de um município é buscado pelo nome uma vez e guardado em `cptec/cptec_ids.json`.

### Estações do INMET e a chave das estações (token)

As condições atuais de um município fora da capital vêm da estação automática do INMET mais próxima: a sua
temperatura, o vento e o horário da leitura (de hora em hora, em UTC no INMET, mostrado no horário de São Paulo). As
estações não leem o céu, então as palavras das condições vêm do período em curso da previsão de hoje do INMET. A caixa
de informações dá o nome da estação, por exemplo `INMET A701 · previsão INMET`.

- **Sem chave** (o padrão), a leitura vem do serviço de previsão do INMET, que responde com a estação mais próxima de um
  município do IBGE e a sua leitura mais recente (`apiprevmet3.inmet.gov.br/estacao/proxima/<code>`).
- **Com chave**, ela vem da API de estações do INMET (`apitempo.inmet.gov.br/token/estacao/...`), da estação
  automática mais próxima do próprio local (lista de estações do INMET, mantida por um dia).
- Uma estação a mais de 50 km ou uma leitura com mais de 3 h não é usada: o próximo provedor do core (Open-Meteo)
  responde pelas condições atuais, sem erro na tela.
- Uma chave que o INMET recusa (HTTP 401 ou 403, ou o texto `CHAVE INVÁLIDA!`, que o INMET envia com HTTP 200) não é
  enviada de novo naquela execução (depois de um HTTP 401 ou 403, que um firewall também pode enviar, ela é tentada de
  novo após 30 minutos); a resposta sem chave entra no lugar. A resposta sem chave também entra no lugar quando a com
  chave falha ou não tem uma leitura recente próxima o bastante. Uma cidade atualiza o seu clima a cada 15 minutos:
  sem chave, isso é uma requisição por município; com chave, as cidades que compartilham uma estação compartilham a
  resposta dela por 10 minutos.
- O horário mostrado é o do próprio local (por estado: Acre, Amazonas, Rondônia, Roraima, Mato Grosso e Mato Grosso do
  Sul estão atrás de São Paulo), e as palavras das condições vêm do período da previsão de hoje em curso lá; o INMET
  não tem período de madrugada, então da meia-noite às 6 h valem as palavras da manhã de hoje.

Como obter uma chave. O INMET não publica uma página de cadastro; o endereço do manual da API
(`portal.inmet.gov.br/manual/manual-de-uso-da-api-estações`) respondeu HTTP 404 em 21/11/2026. O canal usado por
outros projetos é um e-mail para o setor de cadastro do INMET:

1. Escreva para `cadastro.act@inmet.gov.br` (o endereço vem de projetos de terceiros, não de uma página do INMET; peça
   ao INMET que confirme o canal na resposta). Diga quem você é (nome e, se houver, instituição), que quer uma chave
   (token) para a API de dados das estações `apitempo.inmet.gov.br`, para quê (por exemplo: um app de terminal
   gratuito e não comercial que mostra a leitura mais recente da estação automática mais próxima) e com que
   frequência ele consulta (uma requisição por estação a cada 10 minutos, mais ou menos). Outros projetos recomendam
   usar um endereço institucional.
2. Aguarde a resposta. O INMET não publica prazo nem limite de requisições; terceiros relatam que uma requisição pode
   cobrir no máximo 6 meses de dados.
3. Coloque a chave em um destes lugares (a variável de ambiente tem prioridade):
   - PowerShell, para a janela atual: `$env:TERRASCOPE_BR_INMET_TOKEN = "your key"`; para todas as janelas novas:
     `[Environment]::SetEnvironmentVariable("TERRASCOPE_BR_INMET_TOKEN", "your key", "User")`;
   - ou o arquivo `br\inmet_token.txt` na pasta de configuração do Terrascope (`terrascope --show-paths` mostra a
     pasta): a primeira linha que não estiver em branco nem for um comentário `#` é a chave.
4. Confira: `python -m terrascope_br inmet-token` diz de onde vem a chave e se o INMET a aceita.

### Um rótulo por município

Abaixo do zoom 32, a camada de temperatura rotula as cidades do Natural Earth, como faz o core (5 delas na região
metropolitana de São Paulo). A partir do zoom 32 (onde os contornos passam a ser um arquivo por estado), ela rotula os
municípios dos estados visíveis: um ponto dentro do contorno de cada município, do mesmo arquivo `minima` que os
provedores leem, de modo que um clique consulta os provedores sobre exatamente aquele município. Os nomes vêm da lista
de localidades do IBGE.

Onde os rótulos se sobreporiam, a regra do core mantém o primeiro: os municípios com uma cidade do Natural Earth vêm
primeiro, pela população dessa cidade, e depois os outros, pela área. Só os municípios rotulados (no máximo o
`layers.weather.cities.max_labels` do core) são buscados, no máximo `max_concurrent_requests` por vez, e cada host de
origem é consultado no máximo uma vez por segundo. Os arquivos dos estados são lidos na thread de atualização da
camada; até ficarem prontos, as cidades do Natural Earth permanecem. Isso requer um core do Terrascope com
`WeatherLayer.city_places`; um core mais antigo mantém as cidades do Natural Earth em todos os zooms.

## Vista inicial e zoom

A aba BRASIL define `view: {lat: -14, lon: -52, zoom: 3}`, então o mapa abre sobre o Brasil. A vista é aplicada toda
vez que a aba é aberta; veja a seção sobre plugins do README do Terrascope.

A aba também define `zoom_max: 4096`, o limite do core para uma aba (`TAB_ZOOM_MAX_LIMIT`): 64 vezes mais perto que
o mapa-múndi. Neste mapa (58 linhas de células), um ponto braille tem cerca de 66 m no zoom 1024, 33 m no 2048 e 17 m
no 4096 (em um mapa de 45 linhas: 86, 43 e 21 m). Os contornos do IBGE acompanham o zoom para que o detalhe
corresponda aos pontos, até 256, e ficam no arquivo mais fino além disso:

| Zoom | Contornos de estados e municípios | 1 ponto (108 x 58 células) |
|---|---|---|
| até 32 | arquivos nacionais, qualidade `minima` (municípios a partir do zoom 8) | 2,1 km ou mais |
| de 32 a 128 | um arquivo por estado visível, `intermediaria` (um vértice a cada 1,2 km na Grande São Paulo) | de 2,1 km a 530 m |
| de 128 a 4096 | um arquivo por estado visível, `maxima` (um vértice a cada 250 m, o arquivo mais fino do IBGE) | de 530 m a 17 m |

Uma vista aproximada nunca pede um arquivo nacional: a malha nacional de municípios (3,6 MB) é baixada e desenhada só
nas vistas amplas. Os arquivos por estado são baixados na primeira vez em que uma vista mostra aquele estado naquele
nível (São Paulo: 1,0 MB `intermediaria`, 4,9 MB `maxima`; Minas Gerais `maxima`: 8,8 MB), lidos em segundo plano e
mantidos por 30 dias. Enquanto um nível novo carrega, os contornos anteriores continuam na tela. Os retângulos dos
estados vêm do arquivo nacional de estados (cerca de 100 kB), que a aba lê na sua vista inicial; uma vista sobre o
mar ou sobre um país vizinho não desenha nenhum contorno do IBGE. Offline, os arquivos em cache de um nível novo são
lidos de uma vez, o que pode travar a tela por até um segundo nos estados maiores.

A partir do zoom 16, o core desenha a costa a partir do Natural Earth 1:10m (`map.zoom.detail_min`), de modo que ela
coincide com os contornos do IBGE no litoral.

## Comportamento

- Os dados ficam em cache em `<cache-dir>/br`. `terrascope --offline` mostra os dados em cache sem nenhuma requisição.
- Um download que falha mantém os últimos dados bons, mostra o motivo na legenda e espera um intervalo crescente antes
  de tentar de novo. Ele nunca gera erro na tela.
- As requisições usam o User-Agent do Terrascope, gzip, um limite de tamanho de 40 MB e no mínimo 1 s entre
  requisições ao mesmo host. As camadas globais usam as requisições do core, então os limites delas são os do core.
- A coluna de horário do CSV do INPE é lida como UTC. O arquivo não diz qual fuso horário usa; o horário local
  mostrado é UTC-3.

## Idioma

Todos os textos são escritos em inglês no código-fonte e marcados com `_()`. O catálogo em português é
`src/terrascope_br/locale/pt_BR/LC_MESSAGES/terrascope_br.po` (domínio `terrascope_br`). O idioma é o mesmo que o core
do Terrascope usa (`--lang`, `app.language`). O build compila os catálogos com o Babel, como faz o core.

Para atualizar o catálogo depois de mudar textos:

    pybabel extract -F babel.cfg -o src/terrascope_br/locale/terrascope_br.pot .
    pybabel update -i src/terrascope_br/locale/terrascope_br.pot -d src/terrascope_br/locale -D terrascope_br -l pt_BR
    # fill in the new msgstr entries in the .po, then reinstall (the build hook compiles the .mo)

## Testes

    pip install -e ".[dev]"
    pytest tests

Os testes leem amostras reduzidas de `tests/data` (veja o README de lá) e não usam a rede. Eles rodam em todo pull
request e na `main` (Ubuntu e Windows, `.github/workflows/ci.yml`), contra a `main` do core.

## Desenvolvimento e releases

Como uma mudança chega a uma release, e por que o merge na `main` espera as checagens, está em [`docs/RELEASING.md`](docs/RELEASING.md) (em inglês).

## Fontes e termos

Cada fonte mantém os seus próprios termos. O [`docs/data-terms.md`](docs/data-terms.md) lista, para cada endpoint que o
plugin lê, o que a fonte publica sobre reuso, atribuição, limites de requisições, uso comercial e cache, com a URL e a
data de acesso de cada afirmação. Onde uma fonte não publica termos, o documento diz isso. Os créditos aparecem na
atribuição da aba BRASIL; as fontes mostradas só de vez em quando são citadas nas suas próprias linhas do mapa
enquanto aparecem (a fonte do clima da cidade selecionada, que cita o CPTEC/INPE sempre que a previsão dele está na
tela, como os termos dele pedem, e "GeoSampa (CC BY-SA 4.0), ..." enquanto as regiões do CGE são desenhadas). As
fontes de clima (CGE, INMET, CPTEC/INPE) e o GeoSampa foram verificados em 21/11/2026: o CPTEC/INPE não permite uso
comercial nem reprodução "em meios de divulgação" sem a sua autorização, e o CGE não publica termos; os dois são
pendências nesse documento.
O RainViewer (chuva global) e o Open-Meteo (temperaturas das cidades), lidos pelas camadas globais, ainda não estão
cobertos por esse documento; o core mostra os créditos deles na aba CLIMA, e a atribuição da aba BRASIL também os
lista.

## Licença

MIT. Cada fonte de dados mantém os seus próprios termos; veja [`docs/data-terms.md`](docs/data-terms.md) e os créditos
mostrados no app.
