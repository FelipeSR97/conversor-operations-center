# Conversor Operations Center (plugin QGIS)

Converte mapas de trabalho (shapefile) de monitores de terceiros para o layout de
colunas do **Stara**, que o John Deere Operations Center importa como
**Dados de Trabalho**.

## Instalação

1. QGIS 3.22 ou mais novo.
2. *Complementos > Gerenciar e instalar complementos > Instalar a partir do ZIP*.
3. Escolha `conversor_oc.zip` e clique em *Instalar complemento*.
4. Aparece um ícone na barra de ferramentas e a ferramenta
   **Conversor Operations Center > Converter mapas para o Operations Center**
   na Caixa de Ferramentas de Processamento.

Não precisa instalar nenhuma biblioteca extra: a leitura e a gravação de shapefile
usam o pyshp (licença MIT), que vem dentro do plugin, e a reprojeção usa o próprio QGIS.

## Uso

| Parâmetro | O que faz |
|---|---|
| Pasta com os mapas | Pasta em qualquer estrutura de subpastas. Pode conter `.zip` (até zip dentro de zip). |
| Taxa padrão | Usada só quando o mapa não tem taxa nenhuma (AgNav, mapas só com geometria). Padrão: 30. |
| Separar SPRAYING por máquina e dia | A plataforma SPRAYING junta três máquinas em uma semana; separado, cada arquivo é um trabalho. |
| Arquivo repetido em várias OS | Gera um arquivo só, com `DESCOS` listando todas as OS (ex.: `111111/222222/333333`). |
| ISOXML: excluir tarefas | Tarefas da TASKDATA cujo nome contém estes textos (separados por vírgula) ficam de fora. Padrão: `TESTE`. |
| Pasta de saída | Recebe um `.zip` por trabalho e o `relatorio_conversao.csv`. |

Suba no Operations Center os `.zip` da pasta de saída (*Files > Carregar*), um por um
ou vários de uma vez. **Não compacte a pasta de saída em outro zip.**

## Formatos reconhecidos

| Formato | Como é reconhecido | Tempo | Taxa |
|---|---|---|---|
| Stara | `Taxa_da_ap` + `Tempo` | original | original |
| Hexagon (MP AGRO) | `c1Dosage` | real (`Date`) | `c1Dosage` / `c1Recom` |
| Verion | `P1-DR` | real se houver `Data`/`Fecha` em texto; senão fictício (5 s) | `P1-DR` / `P1-DT` |
| Trimble / GS3 / GS4 | `TX_Aplicad` / `TX_Alvo` | real (`Timestamp` ou `DATE`) | aplicada; se toda zerada, usa a alvo |
| Trimble Coverage | `AppldRate` + `DateClosed` | real | `AppldRate` |
| Jacto | `Rate0` + `Date` | real | `Rate0` |
| SPRAYING (LAYER_MAP) | `AppliedDos` + `timestamp` | real | `AppliedDos` / `Configured` |
| Topcon – relatório Taxa Efetiva (antigo "ENC") | coluna `l/ha` | fictício (1 s) | `l/ha` |
| AgNav (KML convertido) | `FolderPath` + `Name` | fictício (1 s) | taxa padrão |
| Só geometria | só campos da OS / `LAYER` | fictício (1 s) | taxa padrão |

## ISOXML (pasta TASKDATA) — Topcon e outros monitores ISOBUS

O Operations Center importa a pasta `TASKDATA` compactada (com a pasta `TASKDATA` na raiz do
zip) como Dados de Trabalho, com hora e taxa reais. Por isso o plugin **não converte**
ISOXML: só confere e empacota.

- Qualquer pasta com `TSK*.XML` é tratada como TASKDATA, inclusive
  `TASKDATA_CORRUPT_BACKUP_...` do Topcon.
- Cada log (`TLG*.XML` + `TLG*.BIN`) é decodificado até o último byte; log quebrado ou sem
  `.BIN` é retirado, e a tarefa continua com os demais.
- Sem `TASKDATA.XML` (backup "corrompido"): o índice é recriado a partir dos arquivos
  existentes, com os atributos da TASKDATA vizinha. Referências a arquivos ausentes
  (ex.: `AFE` apontando para `LINKLIST.XML`) ficam de fora.
- Tarefas sem registros e tarefas de teste (nome com `TESTE`, configurável) são retiradas.
- A mesma tarefa na TASKDATA e no backup entra uma vez só (a TASKDATA normal tem prioridade).
- Os shapefiles de `Reports/…/Shapefiles` do Topcon (um mapa por variável: Taxa Efetiva,
  Elevação, Pressão, Boundary, Guidelines…) são ignorados quando a TASKDATA do mesmo pen
  drive está presente. Sem TASKDATA, só a camada *Taxa Efetiva* é convertida, com aviso.

No relatório, cada tarefa ISOXML aparece em uma linha, com nº de registros, início, fim e
o motivo de exclusão, quando houver.

## O que o plugin corrige

- monta as colunas do Stara e a coluna `Tempo` (sem elas o Operations Center
  classifica como Prescrição ou Inválido);
- remove polígonos sem vértices (Hexagon) e anéis degenerados;
- fecha anéis abertos (Verion, ENC) e acerta o sentido (horário);
- reprojeta UTM/SIRGAS 2000 para WGS84 e grava em 2D;
- lê arquivos "juntados" com colunas repetidas (`Timestamp1`, `TX_Alvo1`, … até 5 cópias);
- detecta data dia/mês × mês/dia; remove datas espúrias (1970, mês errado);
- converte velocidade em m/s para km/h (Hexagon, Trimble Coverage) e calcula
  velocidade no SPRAYING pela distância entre leituras;
- preenche `DESCOS` com o número da OS do nome da pasta quando estiver vazio;
- grava `.prj` WGS84 e `.cpg` UTF-8.

## Relatório

`relatorio_conversao.csv` (separado por `;`, abre direto no Excel) tem uma linha por
arquivo gerado, com formato, nº de polígonos, início e fim e os avisos:

- **Tempo FICTÍCIO** ou **taxa padrão (FICTÍCIA)**: o trabalho sobe, mas esses valores
  não são reais;
- **mesmo arquivo em N pastas**: possível duplicidade de área entre OS;
- **cobre N dias**: provável junção de trabalhos diferentes pela rotina;
- **data ambígua**: todos os dias ≤ 12; confira a data no Operations Center.

## Linha de comando (sem QGIS)

```
python core/conversor.py PASTA_ENTRADA PASTA_SAIDA [--taxa-padrao 30]
```

Fora do QGIS, a reprojeção de mapas em UTM precisa do `pyproj` instalado.
