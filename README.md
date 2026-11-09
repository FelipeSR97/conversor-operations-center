# Conversor Operations Center

**Plugin QGIS que leva mapas de trabalho de monitores de outras marcas para o
John Deere Operations Center.**

![testes](https://github.com/FelipeSR97/conversor-operations-center/actions/workflows/testes.yml/badge.svg)
![QGIS](https://img.shields.io/badge/QGIS-3.22%2B-589632)
![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB)
![Licença](https://img.shields.io/badge/licen%C3%A7a-MIT-blue)

---

## O problema

Em frotas mistas, boa parte das operações de pulverização, calcário e gesso é feita
com monitores que não são John Deere: Stara, Hexagon, Verion, Trimble, Jacto, Topcon,
AgNav… Todos exportam "shapefile", mas o Operations Center só reconhecia os do Stara
como **Dados de Trabalho**. Os demais entravam como *Prescrição*, *Desconhecido* ou
*Inválido* — e o histórico de aplicação dessas áreas simplesmente não existia na
plataforma.

## O que o plugin faz

Você aponta uma pasta (do jeito que vem do campo: subpastas por OS, zips, até zip
dentro de zip) e ele devolve um `.zip` por trabalho, pronto para *Files > Carregar*
no Operations Center, e um relatório CSV com tudo o que precisou ser ajustado.

```
pasta do campo/                         saída/
├── 123456_08-10-2026/                  ├── 123456_MP_AGRO_25000_0000_OC.zip
│   └── MP AGRO_25000_0000.shp   ──►    ├── 222222_workregPoly_OC.zip
├── 222222/workregPoly.shp              ├── TASKDATA_PEN_DRIVE_TASKDATA.zip
└── PEN DRIVE/                          └── relatorio_conversao.csv
    ├── TASKDATA/  (ISOXML)
    └── TASKDATA_CORRUPT_BACKUP/
```

| Formato | Reconhecido por | Tempo | Taxa |
|---|---|---|---|
| Stara | `Taxa_da_ap` + `Tempo` | original | original |
| Hexagon (MP AGRO) | `c1Dosage` | real | aplicada / recomendada |
| Verion | `P1-DR` | real (se houver `Data`) ou fictício | aplicada / alvo |
| Trimble, GS3, GS4 | `TX_Aplicad` | real | aplicada (ou alvo, se zerada) |
| Trimble Coverage | `AppldRate` + `DateClosed` | real | aplicada |
| Jacto | `Rate0` | real | aplicada |
| Plataforma SPRAYING | `AppliedDos` + `timestamp` | real | aplicada / configurada |
| Topcon – relatório | `l/ha` | fictício | aplicada |
| AgNav (KML convertido) | `FolderPath` | fictício | taxa padrão |
| Só geometria | sem atributos | fictício | taxa padrão |
| **ISOXML (TASKDATA)** | `TSK*.XML` | real | real — vai **sem conversão** |

## Como cheguei nas regras

O Operations Center não documenta o que faz um shapefile de terceiros virar
"Dado de Trabalho". As regras deste plugin saíram de testes controlados: pegar um
arquivo que funciona (Stara), outro que não funciona, e mudar **uma variável por vez**
até descobrir o que importava.

| Hipótese testada | Resultado |
|---|---|
| Corrigir geometria (polígonos vazios, anéis abertos, `.cpg`) | Necessário, mas não suficiente: Hexagon passou a entrar como *Prescrição* |
| Colunas originais + coluna `Tempo` | Prescrição |
| Colunas do Stara sem `Tempo` | Inválido |
| **Colunas do Stara + `Tempo`** | **Dados de Trabalho** ✅ |
| Campos da OS vazios / altitude 0 / velocidade 0 | Indiferente |
| UTM e polígonos 3D | Indiferente |
| Taxa toda zerada | Sobe, mas o mapa não aparece |
| Zip dentro de zip | Inválido |
| Pasta ISOXML `TASKDATA` compactada | Dados de Trabalho, com hora e taxa reais ✅ |
| Backup "corrompido" do Topcon | Faltava só o `TASKDATA.XML`; recriado, sobe ✅ |

Pelo caminho apareceram problemas que nenhum monitor avisa e que o plugin agora
trata e registra no relatório:

- **29 a 61 %** dos registros do Hexagon eram polígonos sem vértices (seção fechada);
- a rotina de ArcGIS que preparava os arquivos do Verion **apagava a hora** e trocava
  dia/mês (`8/12` virava 8 de dezembro);
- o mesmo arquivo copiado para várias OS (área duplicada no Operations Center);
- arquivos "juntados" com colunas repetidas (`Timestamp1`, `TX_Alvo1`… até 5 cópias)
  e trabalhos de meses diferentes no mesmo arquivo;
- datas de GPS zeradas (1970) e velocidades em m/s rotuladas como km/h.

Resultado na validação: **209 arquivos de 10 formatos**, todos aceitos no padrão
exigido; mais a recuperação de pen drives Topcon com tarefas que estavam perdidas.

## Instalação

1. Baixe o `conversor_oc.zip` na página de [Releases](../../releases)
   (ou gere com `python scripts/empacotar.py`).
2. No QGIS: *Complementos > Gerenciar e instalar complementos > Instalar a partir do ZIP*.
3. A ferramenta aparece na barra e na Caixa de Ferramentas de Processamento
   (**Conversor Operations Center**). Funciona também em lote e no Model Builder.

Nenhuma dependência extra: leitura/escrita de shapefile com o
[pyshp](https://github.com/GeospatialPython/pyshp) (MIT) embutido e reprojeção pelo
próprio QGIS.

### Sem QGIS (linha de comando)

```bash
pip install pyproj   # só para mapas em UTM
python conversor_oc/core/conversor.py PASTA_ENTRADA PASTA_SAIDA --taxa-padrao 30
```

## Arquitetura

```
conversor_oc/
├── __init__.py, plugin.py, provider.py   integração com o QGIS (menu, barra, Processing)
├── algoritmo.py                          parâmetros da ferramenta e reprojeção via QGIS
└── core/                                 motor — Python puro, sem QGIS, testável
    ├── conversor.py                      detecção de formato, conversão, relatório
    ├── isoxml.py                         TASKDATA: conferência de logs, recuperação, empacotamento
    └── ext/shapefile.py                  pyshp (MIT)
tests/                                    pytest com mapas fictícios gerados na hora
```

O motor não importa nada do QGIS: a reprojeção é uma função injetada (QGIS dentro do
plugin, `pyproj` nos testes e na linha de comando). Isso permite testar toda a lógica
no GitHub Actions sem instalar o QGIS.

## Testes

```bash
pip install pytest pyproj
pytest -q
```

Os testes geram mapas **fictícios** de cada formato (Hexagon com seções fechadas e data
de 1970, Verion com anéis abertos, Trimble em UTM 3D, TASKDATA sem índice e com log
quebrado…) e conferem o padrão de saída. Nenhum dado real de cliente faz parte do
repositório.

## Limitações

- Onde o monitor não grava hora (Verion processado, Topcon-relatório, AgNav), o `Tempo`
  é **fictício**; onde não grava taxa, usa-se uma **taxa padrão**. O relatório marca
  esses casos — o trabalho sobe, mas esses valores não são medidos.
- As regras refletem o comportamento do Operations Center observado em out/2026 e podem
  mudar.

## Aviso

Projeto independente, sem vínculo com a Deere & Company. *John Deere* e
*Operations Center* são marcas de seus respectivos donos, assim como os nomes dos demais
fabricantes citados, usados apenas para identificar formatos de arquivo.

## Autor

**Felipe Santos Rocha** — Engenheiro Agrônomo, agricultura de precisão e soluções
conectadas.
