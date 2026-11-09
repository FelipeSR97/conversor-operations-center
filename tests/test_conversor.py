# -*- coding: utf-8 -*-
"""
Testes do motor de conversão com mapas FICTÍCIOS gerados na hora.
Nenhum dado real de cliente faz parte do repositório.

    pip install pytest pyproj
    pytest -q
"""
import datetime
import io
import os
import struct
import sys
import zipfile

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from conversor_oc.core import conversor, isoxml  # noqa: E402
from conversor_oc.core.ext import shapefile  # noqa: E402

COLUNAS_STARA = ['Taxa_da_ap', 'Taxa_Req_1', 'Altura', 'Secoes', 'Tempo',
                 'Velocidade', 'Desvio_pad', 'Produto']
WGS84 = conversor.WGS84_WKT
UTM22S = ('PROJCS["SIRGAS_2000_UTM_Zone_22S",GEOGCS["GCS_SIRGAS_2000",DATUM["D_SIRGAS_2000",'
          'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
          'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
          'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",10000000.0],'
          'PARAMETER["Central_Meridian",-51.0],PARAMETER["Scale_Factor",0.9996],'
          'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')


# --------------------------------------------------------------------------
# geradores de mapas fictícios
# --------------------------------------------------------------------------
def _retangulo(x, y, dx=0.0001, dy=0.0001, fechar=True):
    r = [(x, y), (x, y + dy), (x + dx, y + dy), (x + dx, y)]
    return r + [r[0]] if fechar else r


def _gravar(pasta, nome, campos, linhas, prj=WGS84, tipo=shapefile.POLYGON, cpg='UTF-8'):
    os.makedirs(pasta, exist_ok=True)
    base = os.path.join(pasta, nome)
    w = shapefile.Writer(base, shapeType=tipo, encoding='utf-8')
    for c in campos:
        w.field(*c)
    for aneis, rec in linhas:
        if aneis is None:
            w.null()
        elif tipo == shapefile.POLYGONZ:
            w.polyz([[(x, y, 0.0) for x, y in a] for a in aneis])
        else:
            w.poly(aneis)
        w.record(*rec)
    w.close()
    if prj:
        open(base + '.prj', 'w').write(prj)
    if cpg:
        open(base + '.cpg', 'w').write(cpg)
    return base + '.shp'


def _hexagon(pasta, n=60):
    campos = [('Date', 'C', 30, 0), ('c1Dosage', 'N', 24, 15), ('c1Recom', 'N', 24, 15),
              ('Altitude', 'N', 24, 15), ('Speed', 'N', 24, 15), ('c1Insumo', 'C', 80, 0),
              ('OpenSectio', 'C', 10, 0), ('DESCOS', 'C', 254, 0)]
    t0 = datetime.datetime(2026, 9, 20, 13, 0, 0)
    linhas = []
    for i in range(n):
        t = (t0 + datetime.timedelta(seconds=i)).strftime('%d/%m/%Y %H:%M:%S UTC')
        aberta = i % 3 != 0
        aneis = [_retangulo(-47.9 + i * 0.0001, -19.5)] if aberta else None  # seção fechada = vazio
        linhas.append((aneis, [t, 2100.0 if aberta else 0, 2200.0, 950.0, 3.5,
                               'GESSO', '1' if aberta else '0', '']))
    # 1 registro com data espúria (1970) — deve ser descartado
    linhas.append(([_retangulo(-47.8, -19.5)], ['01/01/1970 00:00:00 UTC', 2100, 2200, 950, 3.5, 'GESSO', '1', '']))
    return _gravar(pasta, 'MP AGRO_25000_0000', campos, linhas, cpg='ANSI 1252')


def _verion(pasta, n=30, com_hora=False):
    campos = [('P1-DT', 'N', 24, 15), ('P1-DR', 'N', 24, 15), ('Altura[m]', 'N', 24, 15),
              ('Velocidade', 'N', 24, 15)]
    if com_hora:
        campos.append(('Data', 'C', 30, 0))
    t0 = datetime.datetime(2026, 8, 12, 14, 0, 0)
    linhas = []
    for i in range(n):
        rec = [150.0, 148.0 + i % 5, 1000.0, 12.0]
        if com_hora:
            t = t0 + datetime.timedelta(seconds=5 * i)
            rec.append('%d/%d/%d %s' % (t.month, t.day, t.year, t.strftime('%I:%M:%S %p').lstrip('0')))
        linhas.append(([_retangulo(-47.9 + i * 0.0001, -19.5, fechar=False)], rec))  # anel aberto
    return _gravar(pasta, 'workregPoly', campos, linhas, cpg='ANSI 1252')


def _trimble_utm(pasta, n=40):
    campos = [('Timestamp', 'C', 30, 0), ('TX_Aplicad', 'N', 19, 11), ('TX_Alvo', 'N', 19, 11),
              ('Elevation', 'N', 19, 11), ('VehicleSpe', 'N', 19, 11), ('0199', 'N', 19, 11)]
    t0 = datetime.datetime(2026, 9, 29, 20, 0, 0)
    linhas = []
    for i in range(n):
        x, y = 806000 + i * 10, 7840000
        aneis = [[(x, y), (x, y + 10), (x + 10, y + 10), (x + 10, y), (x, y)]]
        t = (t0 + datetime.timedelta(seconds=i)).strftime('%d/%m/%Y %H:%M:%S')
        linhas.append((aneis, [t, 1900.0, 2000.0, 950.0, 25.0, 1]))
    return _gravar(pasta, '107542856', campos, linhas, prj=UTM22S, tipo=shapefile.POLYGONZ)


def _so_geometria(pasta, n=10):
    campos = [('LAYER', 'C', 10, 0)]
    linhas = [([_retangulo(-47.9 + i * 0.0001, -19.5)], ['0']) for i in range(n)]
    return _gravar(pasta, 'cobertura', campos, linhas)


def _ler_zip(caminho):
    z = zipfile.ZipFile(caminho)
    nomes = z.namelist()
    base = [n for n in nomes if n.endswith('.shp')][0][:-4]
    r = shapefile.Reader(shp=io.BytesIO(z.read(base + '.shp')), shx=io.BytesIO(z.read(base + '.shx')),
                         dbf=io.BytesIO(z.read(base + '.dbf')), encoding='utf-8')
    return nomes, r


def _rodar(tmp_path, **op):
    saida = str(tmp_path / 'saida')
    gerados, rel = conversor.converter_pasta(str(tmp_path / 'entrada'), saida,
                                             conversor.Opcoes(**op), conversor.fabrica_pyproj,
                                             log=lambda m: None)
    linhas = open(rel, encoding='utf-8-sig').read().strip().split('\n')[1:]
    return gerados, [dict(zip(['origem', 'formato', 'saida', 'poligonos', 'inicio', 'fim', 'avisos'],
                              l.split(';'))) for l in linhas]


def _checar_padrao(zip_path):
    nomes, r = _ler_zip(zip_path)
    assert sorted(os.path.splitext(n)[1] for n in nomes) == ['.cpg', '.dbf', '.prj', '.shp', '.shx']
    assert not any(n.endswith('.zip') for n in nomes), 'nunca zip dentro de zip'
    assert [f[0] for f in r.fields[1:9]] == COLUNAS_STARA
    assert r.shapeType == shapefile.POLYGON
    for s in r.shapes():
        assert s.points, 'nenhum polígono vazio'
        assert s.points[0] == s.points[-1], 'anéis fechados'
    return r


# --------------------------------------------------------------------------
# testes
# --------------------------------------------------------------------------
def test_deteccao_de_formatos():
    d = conversor.detectar_formato
    assert d(['Taxa_da_ap', 'Tempo']) == 'STARA'
    assert d(['Date', 'c1Dosage']) == 'HEXAGON'
    assert d(['P1-DT', 'P1-DR']) == 'VERION'
    assert d(['timestamp', 'AppliedDos']) == 'SPRAYING'
    assert d(['Date', 'Time', 'Rate0']) == 'JACTO'
    assert d(['DateClosed', 'AppldRate']) == 'TRIMBLE_COV'
    assert d(['Timestamp', 'TX_Aplicad']) == 'TRIMBLE'
    assert d(['l/ha']) == 'ENC'
    assert d(['Name', 'FolderPath']) == 'AGNAV'
    assert d(['LAYER', 'DESCOS']) == 'GEOMETRIA'
    assert d(['qualquer', 'coisa']) is None


def test_hexagon_remove_vazios_e_data_espuria(tmp_path):
    _hexagon(str(tmp_path / 'entrada' / '123456_20-09-2026 10-00-00' / '123456'))
    gerados, rel = _rodar(tmp_path)
    assert len(gerados) == 1
    r = _checar_padrao(gerados[0])
    recs = r.records()
    assert len(recs) == 40                       # 60 - 20 vazios; a data de 1970 sai
    assert recs[0][4] == 0 and recs[-1][4] > 0   # Tempo real, em segundos
    assert abs(recs[0][5] - 12.6) < 0.1          # 3,5 m/s -> km/h
    assert recs[0][recs and 11] == '123456'      # DESCOS vem do nome da pasta
    assert 'data espúria' in rel[0]['avisos']


def test_verion_fecha_aneis_e_avisa_tempo_ficticio(tmp_path):
    _verion(str(tmp_path / 'entrada' / '222222'))
    gerados, rel = _rodar(tmp_path)
    r = _checar_padrao(gerados[0])
    assert [x[4] for x in r.records()][:3] == [0, 5, 10]
    assert 'FICTÍCIO' in rel[0]['avisos']


def test_verion_com_hora_usa_tempo_real(tmp_path):
    _verion(str(tmp_path / 'entrada' / '333333'), com_hora=True)
    gerados, rel = _rodar(tmp_path)
    r = _checar_padrao(gerados[0])
    assert [x[4] for x in r.records()][:3] == [0, 5, 10]
    assert 'FICTÍCIO' not in rel[0]['avisos']
    assert rel[0]['inicio'].startswith('12/08/2026')  # mês/dia do Verion lido corretamente


def test_trimble_utm_reprojetado_para_wgs84(tmp_path):
    pytest.importorskip('pyproj')
    _trimble_utm(str(tmp_path / 'entrada' / '444444'))
    gerados, rel = _rodar(tmp_path)
    r = _checar_padrao(gerados[0])
    x, y = r.shape(0).points[0]
    assert -50 < x < -46 and -21 < y < -18
    assert 'reprojetado' in rel[0]['avisos']


def test_so_geometria_usa_taxa_padrao(tmp_path):
    _so_geometria(str(tmp_path / 'entrada' / '555555'))
    gerados, rel = _rodar(tmp_path, taxa_padrao=30)
    r = _checar_padrao(gerados[0])
    assert {x[0] for x in r.records()} == {30.0}
    assert 'taxa padrão' in rel[0]['avisos']


def test_arquivo_repetido_em_duas_os_vira_um_so(tmp_path):
    _so_geometria(str(tmp_path / 'entrada' / '666666'))
    _so_geometria(str(tmp_path / 'entrada' / '777777'))
    gerados, rel = _rodar(tmp_path)
    assert len(gerados) == 1
    _, r = _ler_zip(gerados[0])
    assert r.record(0)[11] == '666666/777777'


def test_entrada_zipada_com_zip_dentro(tmp_path):
    origem = tmp_path / 'tmp_src'
    _so_geometria(str(origem))
    interno = tmp_path / 'interno.zip'
    with zipfile.ZipFile(interno, 'w') as z:
        for f in os.listdir(origem):
            z.write(origem / f, f)
    (tmp_path / 'entrada' / '888888').mkdir(parents=True)
    with zipfile.ZipFile(tmp_path / 'entrada' / '888888' / 'lote.zip', 'w') as z:
        z.write(interno, 'interno.zip')
    gerados, _ = _rodar(tmp_path)
    assert len(gerados) == 1


# --------------------------------------------------------------------------
# ISOXML
# --------------------------------------------------------------------------
def _tlg(pasta, nome, n=50):
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<TIM A="" D="4">'
           '<PTN A="" B="" D=""/><DLV A="0002" B="" C="DET-1"/></TIM>\n')
    open(os.path.join(pasta, nome + '.XML'), 'w').write(xml)
    with open(os.path.join(pasta, nome + '.BIN'), 'wb') as f:
        for i in range(n):
            f.write(struct.pack('<IH', 36000000 + i * 1000, 17000))       # hora + dia
            f.write(struct.pack('<iiB', -195000000, -479000000 + i, 4))   # lat, lon, status
            f.write(struct.pack('<BBi', 1, 0, 2000000))                   # 1 DLV: 200 L/ha


def _taskdata(pasta, com_indice=True, quebrar=False):
    os.makedirs(pasta, exist_ok=True)
    w = lambda n, t: open(os.path.join(pasta, n), 'w', encoding='utf-8', newline='').write(t)
    w('CTR00000.XML', '<?xml version="1.0" encoding="UTF-8"?>\n<XFC>\n    <CTR A="CTR-1" B="CLIENTE FICTICIO"/>\n</XFC>\n')
    w('PFD00000.XML', '<?xml version="1.0" encoding="UTF-8"?>\n<XFC>\n    <PFD A="PFD-1" C="TALHAO 1" D="0" E="CTR-1"/>\n</XFC>\n')
    tsk = ('<?xml version="1.0" encoding="UTF-8"?>\n<XFC>\n'
           '    <TSK A="TSK-1" B="TALHAO 1 OP 25" E="PFD-1" G="4">\n'
           '        <TIM A="2026-07-20T10:00:00" B="2026-07-20T10:01:00" D="4"/>\n'
           '        <TLG A="TLG00001"/>\n        <TLG A="TLG00002"/>\n    </TSK>\n'
           '    <TSK A="TSK-2" B="TESTE PULVERIZACAO" E="PFD-1" G="4">\n'
           '        <TIM A="2026-07-21T10:00:00" B="2026-07-21T10:01:00" D="4"/>\n'
           '        <TLG A="TLG00003"/>\n    </TSK>\n'
           '    <TSK A="TSK-3" B="1" E="PFD-1" G="1"/>\n</XFC>\n')
    w('TSK00000.XML', tsk)
    for t in ('TLG00001', 'TLG00002', 'TLG00003'):
        _tlg(pasta, t)
    if quebrar:
        os.remove(os.path.join(pasta, 'TLG00002.BIN'))
    if com_indice:
        w('TASKDATA.XML', '<?xml version="1.0" encoding="UTF-8"?>\n<ISO11783_TaskData VersionMajor="4" '
          'VersionMinor="2" DataTransferOrigin="2" P231_cur_task="TSK-3">\n    <XFR A="CTR00000" B="1"/>\n'
          '    <XFR A="PFD00000" B="1"/>\n    <XFR A="TSK00000" B="1"/>\n</ISO11783_TaskData>\n')


def test_isoxml_decodifica_log():
    import tempfile
    d = tempfile.mkdtemp()
    _tlg(d, 'TLG00009', n=7)
    assert isoxml.conferir_tlg(d, 'TLG00009') == (7, None)


def test_isoxml_backup_sem_indice_e_recuperado(tmp_path):
    _taskdata(str(tmp_path / 'entrada' / 'PENDRIVE' / 'TASKDATA_CORRUPT_BACKUP'), com_indice=False, quebrar=True)
    gerados, rel = _rodar(tmp_path, excluir_tarefas='TESTE')
    assert len(gerados) == 1
    z = zipfile.ZipFile(gerados[0])
    nomes = z.namelist()
    assert all(n.startswith('TASKDATA/') for n in nomes)
    assert 'TASKDATA/TASKDATA.XML' in nomes
    assert 'TASKDATA/TLG00002.XML' not in nomes                 # log quebrado retirado
    assert 'TASKDATA/TLG00003.BIN' not in nomes                 # log da tarefa de teste
    tsk = z.read('TASKDATA/TSK00000.XML').decode()
    assert 'TSK-1' in tsk and 'TSK-2' not in tsk and 'TSK-3' not in tsk
    assert b'\r\n' not in z.read('TASKDATA/TASKDATA.XML')       # mantém LF
    avisos = ' '.join(r['avisos'] for r in rel)
    assert 'TESTE' in avisos and 'sem registros' in avisos and 'TLG00002' in avisos


def test_isoxml_integra_vai_sem_alteracao(tmp_path):
    origem = tmp_path / 'entrada' / 'PENDRIVE' / 'TASKDATA'
    _taskdata(str(origem), com_indice=True)
    gerados, _ = _rodar(tmp_path, excluir_tarefas='')
    # sem nada a excluir além da tarefa vazia "1", o índice é recriado sem P231_cur_task
    z = zipfile.ZipFile(gerados[0])
    assert 'P231_cur_task' not in z.read('TASKDATA/TASKDATA.XML').decode()
