# -*- coding: utf-8 -*-
"""
Motor de conversão de mapas de trabalho de monitores de terceiros para o
layout de colunas do Stara, que o John Deere Operations Center importa como
"Dados de Trabalho".

Regras validadas em testes no Operations Center:
  * o arquivo precisa ter as colunas do Stara E a coluna Tempo
    (sem elas vira "Prescrição" ou "Inválido");
  * polígonos sem vértices precisam ser removidos;
  * taxa zerada sobe, mas o mapa não aparece -> usar taxa alvo ou taxa padrão;
  * um shapefile por zip, nunca zip dentro de zip;
  * campos da OS, altitude e velocidade podem ficar vazios/zerados;
  * projeção (UTM/WGS84) e Z não importam, mas saímos sempre em WGS84 2D.

Não depende do QGIS: usa o pyshp embutido (core/ext/shapefile.py).
A reprojeção é feita por uma função recebida de fora (QGIS ou pyproj).
"""
import collections
import datetime
import hashlib
import math
import os
import re
import shutil
import tempfile
import zipfile

try:
    from .ext import shapefile
    from . import isoxml
except ImportError:  # execução fora do pacote
    from ext import shapefile
    import isoxml

WGS84_WKT = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",'
             '6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
             'UNIT["Degree",0.0174532925199433]]')

CAMPOS_STARA = [
    ('Taxa_da_ap', 'N', 9, 1), ('Taxa_Req_1', 'N', 9, 1), ('Altura', 'N', 7, 1),
    ('Secoes', 'C', 2, 0), ('Tempo', 'N', 12, 1), ('Velocidade', 'N', 5, 1),
    ('Desvio_pad', 'N', 4, 2), ('Produto', 'C', 16, 0),
]
CAMPOS_OS = [
    ('UNIDADE', 'C', 254, 0), ('PRESTADOR', 'C', 254, 0), ('OPERACAO', 'C', 254, 0),
    ('DESCOS', 'C', 254, 0), ('FROTA', 'C', 254, 0), ('APLICACAO', 'C', 254, 0),
    ('DATA', 'D', 8, 0),
]
NOMES_OS = [c[0] for c in CAMPOS_OS]

FORMATOS = {
    'STARA': 'Stara',
    'HEXAGON': 'Hexagon (MP AGRO)',
    'VERION': 'Verion (workregPoly)',
    'TRIMBLE': 'Trimble / GS3 / GS4 (TX_Aplicad)',
    'TRIMBLE_COV': 'Trimble Coverage',
    'JACTO': 'Jacto (_job)',
    'SPRAYING': 'Plataforma SPRAYING (LAYER_MAP)',
    'ENC': 'Topcon relatório Taxa Efetiva (ENC)',
    'AGNAV': 'AgNav (KML convertido)',
    'GEOMETRIA': 'Só geometria (sem taxa nem tempo)',
}


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------
class Opcoes:
    def __init__(self, taxa_padrao=30.0, separar_maquina_dia=True,
                 juntar_duplicados=True, produto_padrao='', excluir_tarefas='TESTE'):
        self.taxa_padrao = float(taxa_padrao)
        if isinstance(excluir_tarefas, str):
            excluir_tarefas = excluir_tarefas.split(',')
        self.excluir_tarefas = [e.strip() for e in excluir_tarefas if e and e.strip()]
        self.separar_maquina_dia = bool(separar_maquina_dia)
        self.juntar_duplicados = bool(juntar_duplicados)
        self.produto_padrao = produto_padrao


def _encoding(base):
    for ext in ('.cpg', '.CPG'):
        p = base + ext
        if os.path.exists(p):
            txt = open(p, 'rb').read().decode('ascii', 'ignore').strip().upper()
            if 'UTF' in txt and '8' in txt:
                return 'utf-8'
            if '1252' in txt:
                return 'cp1252'
            if '8859' in txt:
                return 'latin1'
    return 'cp1252'


def _ler_prj(base):
    for ext in ('.prj', '.PRJ'):
        p = base + ext
        if os.path.exists(p):
            return open(p, 'rb').read().decode('latin1', 'ignore').strip()
    return ''


def _limpa_nome(s):
    s = re.sub(r'[^A-Za-z0-9_-]+', '_', s)
    return re.sub(r'_+', '_', s).strip('_')[:80] or 'mapa'


def _num(v, padrao=0.0):
    try:
        if v is None or v == '':
            return padrao
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return padrao
        return f
    except (TypeError, ValueError):
        return padrao


def _area2(rg):
    return sum((x2 - x1) * (y2 + y1) for (x1, y1), (x2, y2) in zip(rg, rg[1:]))


def _candidatos(nome):
    """Arquivos 'juntados' pela rotina repetem colunas com sufixo 1..5,
    truncadas a 10 caracteres (TX_Aplicad -> TX_Aplica1)."""
    c = [nome]
    for i in range(1, 6):
        s = str(i)
        c.append(nome[:10 - len(s)] + s)
        c.append(nome + s)
    return c


def _get(g, nome):
    """Primeiro valor não vazio entre a coluna e suas cópias numeradas."""
    for c in _candidatos(nome):
        if c in g:
            v = g[c]
            if v is None or v == '':
                continue
            if isinstance(v, (int, float)) and v == 0:
                continue
            return v
    return g.get(nome)


def _os_da_pasta(caminho):
    """Extrai o número da OS de pastas como '123456_08-10-2026 16-15-16'."""
    for parte in reversed(os.path.normpath(caminho).split(os.sep)):
        m = re.match(r'^(\d{5,})(?:_|$)', parte)
        if m:
            return m.group(1)
    return ''


# --------------------------------------------------------------------------
# leitura
# --------------------------------------------------------------------------
class Mapa:
    """Shapefile lido + metadados."""

    def __init__(self, caminho_shp):
        self.caminho = caminho_shp
        self.base = caminho_shp[:-4]
        self.encoding = _encoding(self.base)
        self.prj = _ler_prj(self.base)
        self.reader = shapefile.Reader(caminho_shp, encoding=self.encoding,
                                       encodingErrors='replace')
        self.campos = [f[0] for f in self.reader.fields[1:]]
        self.os_pasta = _os_da_pasta(os.path.dirname(caminho_shp))
        self.formato = detectar_formato(self.campos)

    def registros(self):
        r = self.reader
        for i, rec in enumerate(r.iterRecords()):
            try:
                s = r.shape(i)
            except Exception:
                s = None
            yield s, dict(zip(self.campos, rec))

    def fechar(self):
        try:
            self.reader.close()
        except Exception:
            pass


RE_TOPCON = re.compile(r'_(Boundary|[A-Z]_\d{8}_\d{4}_Guidelines|Eleva(ção|cao|#U00e7#U00e3o)_m|'
                       r'Press(ão|ao|#U00e3o) do produto[^/]*|Intensidade do sinal[^/]*|'
                       r'Taxa Efetiva_l_ha|Velocidade[^/]*|Sobreposi[^/]*)$', re.I)


def eh_relatorio_topcon(caminho_shp):
    """Shapefiles da pasta Reports do Topcon: um mapa por variável."""
    nome = os.path.basename(caminho_shp)[:-4]
    return bool(RE_TOPCON.search(nome))


def detectar_formato(campos):
    c = set(campos)
    if 'Taxa_da_ap' in c and 'Tempo' in c:
        return 'STARA'
    if 'c1Dosage' in c:
        return 'HEXAGON'
    if 'P1-DR' in c or 'P1_DR' in c:
        return 'VERION'
    if 'AppliedDos' in c and 'timestamp' in c:
        return 'SPRAYING'
    if 'Rate0' in c and 'Date' in c:
        return 'JACTO'
    if 'AppldRate' in c and 'DateClosed' in c:
        return 'TRIMBLE_COV'
    if 'TX_Aplicad' in c or 'TX_Alvo' in c:
        return 'TRIMBLE'
    if 'l/ha' in c:
        return 'ENC'
    if 'FolderPath' in c and 'Name' in c:
        return 'AGNAV'
    resto = c - set(NOMES_OS) - {'LAYER', 'DATA1', 'Id', 'ID', 'FID'}
    if not resto:
        return 'GEOMETRIA'
    return None


# --------------------------------------------------------------------------
# datas
# --------------------------------------------------------------------------
def _detecta_ordem(valores, padrao_mmdd):
    """Decide entre dia/mês e mês/dia olhando valores > 12."""
    a = b = 0
    for v in valores:
        v = str(v).strip()
        m = re.match(r'^(\d{1,2})/(\d{1,2})/\d{4}', v)
        if m:
            a += int(m.group(1)) > 12
            b += int(m.group(2)) > 12
    if a or b:
        return b > a, False
    return padrao_mmdd, True


def _parse(v, mmdd):
    v = str(v).strip().replace(' UTC', '')
    if not v:
        return None
    fmts = (['%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y %H:%M:%S'] if mmdd else
            ['%d/%m/%Y %H:%M:%S', '%d/%m/%Y %I:%M:%S %p'])
    for f in fmts:
        try:
            return datetime.datetime.strptime(v, f)
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------------
# conversão por formato -> lista de "linhas" padronizadas
# cada linha: dict(rings, taxa, alvo, alt, vel, t, prod, os, maquina, idx)
# --------------------------------------------------------------------------
def _linhas(mapa, transformar, avisos):
    fmt = mapa.formato
    campos = mapa.campos
    vals_tempo = []
    tcol = None
    mmdd = False
    if fmt == 'TRIMBLE':
        tcol = 'DATE' if 'DATE' in campos else 'Timestamp'
    elif fmt == 'VERION':
        tcol = next((c for c in ('Fecha', 'Data', 'Date') if c in campos
                     and mapa.reader.fields[1 + campos.index(c)][1] == 'C'), None)
    elif fmt == 'HEXAGON':
        tcol = 'Date'
    if tcol:
        for rec in mapa.reader.iterRecords(fields=[c for c in _candidatos(tcol) if c in campos]):
            vals_tempo.extend(str(x) for x in rec if x)
        padrao = (tcol == 'DATE') or (fmt == 'VERION')
        mmdd, amb = _detecta_ordem(vals_tempo, padrao)
        if amb and vals_tempo and fmt in ('TRIMBLE', 'VERION'):
            avisos.append('data ambígua (todos os dias <= 12): lida como %s'
                          % ('mês/dia' if mmdd else 'dia/mês'))

    linhas = []
    sem_geom = 0
    for idx, (shape, g) in enumerate(mapa.registros()):
        if shape is None or not shape.points:
            sem_geom += 1
            continue
        rings = _aneis(shape, transformar)
        if not rings:
            sem_geom += 1
            continue
        L = dict(rings=rings, taxa=0.0, alvo=0.0, alt=0.0, vel=0.0, t=None,
                 prod='', maquina='', idx=idx, g=g)
        if fmt == 'STARA':
            L.update(taxa=_num(g.get('Taxa_da_ap')), alvo=_num(g.get('Taxa_Req_1')),
                     alt=_num(g.get('Altura')), vel=_num(g.get('Velocidade')),
                     prod=str(g.get('Produto') or ''), tempo_stara=_num(g.get('Tempo')))
        elif fmt == 'HEXAGON':
            L.update(taxa=_num(g.get('c1Dosage')), alvo=_num(g.get('c1Recom')),
                     alt=_num(g.get('Altitude')), vel=_num(g.get('Speed')) * 3.6,
                     t=_parse(g.get('Date'), mmdd), prod=str(g.get('c1Insumo') or ''))
        elif fmt == 'VERION':
            L.update(taxa=_num(g.get('P1-DR', g.get('P1_DR'))),
                     alvo=_num(g.get('P1-DT', g.get('P1_DT'))),
                     alt=_num(g.get('Altura[m]', g.get('Altura_m'))),
                     vel=_num(g.get('Velocidade', g.get('Velocidad['))),
                     t=_parse(g.get(tcol), mmdd) if tcol else None, prod='VERION')
        elif fmt == 'TRIMBLE':
            L.update(taxa=_num(_get(g, 'TX_Aplicad')), alvo=_num(_get(g, 'TX_Alvo')),
                     alt=_num(_get(g, 'Elevation')), vel=_num(_get(g, 'VehicleSpe')),
                     t=_parse(_get(g, tcol), mmdd),
                     prod=str(_get(g, 'PRODUTO') or _get(g, 'PRODUCT') or ''),
                     maquina=str(_get(g, 'MACHINE') or ''))
        elif fmt == 'TRIMBLE_COV':
            t = None
            try:
                s = str(g.get('TimeClosed') or '').strip()
                h = int(s[:2]) % 12 + (12 if s[8:].lower() == 'pm' else 0)
                t = datetime.datetime.combine(g.get('DateClosed'),
                                              datetime.time(h, int(s[3:5]), int(s[6:8])))
            except Exception:
                t = None
            ap = _num(g.get('AppldRate'))
            L.update(taxa=ap, alvo=ap, alt=_num(g.get('Height')),
                     vel=_num(g.get('Speed')) * 3.6, t=t, prod=str(g.get('Material') or ''))
        elif fmt == 'JACTO':
            try:
                t = datetime.datetime.strptime('%s %s' % (g.get('Date'), g.get('Time')),
                                               '%Y/%m/%d %H:%M:%S')
            except Exception:
                t = None
            ap = _num(g.get('Rate0'))
            L.update(taxa=ap, alvo=ap, vel=_num(g.get('Speed_kmh')), t=t, prod='JACTO')
        elif fmt == 'SPRAYING':
            ts = _num(g.get('timestamp'), None)
            t = (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts)
                 if ts else None)
            maq = str(g.get('machineNam') or '').split(' - ')[-1].strip()
            L.update(taxa=_num(g.get('AppliedDos')), alvo=_num(g.get('Configured')),
                     t=t, prod='PULVERIZACAO', maquina=maq, epoch=ts)
        elif fmt == 'ENC':
            ap = _num(g.get('l/ha'))
            L.update(taxa=ap, alvo=ap, prod='ENC')
        elif fmt == 'AGNAV':
            z = 0.0
            try:
                z = _num(shape.z[0]) if getattr(shape, 'z', None) else 0.0
            except Exception:
                pass
            L.update(alt=z, prod='AGNAV')
        elif fmt == 'GEOMETRIA':
            L.update(prod='')
        linhas.append(L)
    if sem_geom:
        avisos.append('%d registros sem geometria removidos' % sem_geom)
    return linhas


def _aneis(shape, transformar):
    pts = shape.points
    partes = list(shape.parts) + [len(pts)]
    out = []
    for a, b in zip(partes, partes[1:]):
        rg = pts[a:b]
        if transformar:
            rg = [transformar(x, y) for x, y in rg]
        else:
            rg = [(x, y) for x, y in rg]
        if len(rg) < 3:
            continue
        if rg[0] != rg[-1]:
            rg.append(rg[0])
        if len(rg) < 4 or len(set(rg)) < 3:
            continue
        if _area2(rg) < 0:          # shapefile: anel externo no sentido horário
            rg = rg[::-1]
        out.append(rg)
    return out


# --------------------------------------------------------------------------
# pós-processamento comum
# --------------------------------------------------------------------------
def _velocidade_por_deslocamento(linhas):
    """SPRAYING não tem velocidade: calcula pela distância entre leituras."""
    ultimo = {}
    for L in sorted(linhas, key=lambda x: (x['maquina'], x.get('epoch') or 0)):
        rg = L['rings'][0]
        c = (sum(p[0] for p in rg) / len(rg), sum(p[1] for p in rg) / len(rg))
        L['_c'] = c
        ant = ultimo.get(L['maquina'])
        if ant and L.get('epoch') and ant.get('epoch'):
            dt = L['epoch'] - ant['epoch']
            if 0 < dt <= 10:
                dx = (c[0] - ant['_c'][0]) * 111320 * math.cos(math.radians(c[1]))
                dy = (c[1] - ant['_c'][1]) * 110540
                L['vel'] = math.hypot(dx, dy) / dt * 3.6
        ultimo[L['maquina']] = L


def _finalizar(linhas, fmt, opcoes, avisos):
    """Filtra datas espúrias, calcula Tempo, garante taxa > 0."""
    com_t = [L for L in linhas if L['t'] is not None]
    if fmt == 'STARA':
        pass
    elif com_t:
        sem_t = len(linhas) - len(com_t)
        if sem_t:
            avisos.append('%d registros sem data/hora removidos' % sem_t)
        linhas = com_t
        dias = collections.Counter(L['t'].date() for L in linhas)

        def espuria(L):
            if L['t'].year < 2000:          # relógio do GPS zerado (1970, 1980)
                return True
            return len(linhas) > 50 and dias[L['t'].date()] < 10
        ruins = sum(1 for L in linhas if espuria(L))
        if ruins:
            avisos.append('%d registros com data espúria removidos' % ruins)
            linhas = [L for L in linhas if not espuria(L)]
        linhas.sort(key=lambda L: L['t'])
        t0 = linhas[0]['t'] if linhas else None
        for L in linhas:
            L['tempo'] = (L['t'] - t0).total_seconds()
    else:
        passo = 5.0 if fmt == 'VERION' else 1.0
        for i, L in enumerate(linhas):
            L['tempo'] = i * passo
        if linhas:
            avisos.append('sem data/hora no arquivo: Tempo FICTÍCIO (%gs por registro)' % passo)
    if fmt == 'STARA':
        for L in linhas:
            L['tempo'] = L.get('tempo_stara', 0.0)

    if not linhas:
        return linhas
    if all(L['taxa'] <= 0 for L in linhas):
        if any(L['alvo'] > 0 for L in linhas):
            for L in linhas:
                L['taxa'] = L['alvo']
            avisos.append('taxa aplicada zerada no arquivo: usada a taxa alvo')
        else:
            for L in linhas:
                L['taxa'] = L['alvo'] = opcoes.taxa_padrao
            avisos.append('arquivo sem taxa: usada a taxa padrão %g (FICTÍCIA)' % opcoes.taxa_padrao)
    for L in linhas:
        if L['alvo'] <= 0 and L['taxa'] > 0 and fmt in ('ENC', 'JACTO', 'TRIMBLE_COV'):
            L['alvo'] = L['taxa']
        L['taxa'] = min(max(L['taxa'], 0), 999999)
        L['alvo'] = min(max(L['alvo'], 0), 999999)
        L['vel'] = L['vel'] if 0 <= L['vel'] < 80 else 0.0
    return linhas


def _metadados(g, os_pasta, descos_override):
    m = {}
    for n in NOMES_OS:
        v = g.get(n)
        if n == 'DATA':
            m[n] = v if v not in ('', None) else None
        else:   # texto vazio, nunca a palavra "None"
            m[n] = '' if v in ('', None) else str(v)
    if descos_override:
        m['DESCOS'] = descos_override
    elif not m['DESCOS'] and os_pasta:
        m['DESCOS'] = os_pasta
    if not isinstance(m['DATA'], (datetime.date, type(None))):
        m['DATA'] = None
    return m


def _gravar_zip(destino_base, linhas, opcoes, os_pasta, descos_override=None):
    """Grava shapefile no layout Stara e empacota em <destino_base>.zip."""
    nome = os.path.basename(destino_base)
    tmp = tempfile.mkdtemp(prefix='oc_')
    try:
        base = os.path.join(tmp, nome)
        w = shapefile.Writer(base, shapeType=shapefile.POLYGON, encoding='utf-8')
        for f in CAMPOS_STARA + CAMPOS_OS:
            w.field(*f)
        for L in linhas:
            m = _metadados(L['g'], os_pasta, descos_override)
            prod = (L['prod'] or opcoes.produto_padrao or '')[:16]
            w.poly(L['rings'])
            w.record(round(L['taxa'], 1), round(L['alvo'], 1), round(L['alt'], 1),
                     '11' if L['taxa'] > 0 else '00', round(L['tempo'], 1),
                     round(L['vel'], 1), 0, prod, *[m[n] for n in NOMES_OS])
        w.close()
        with open(base + '.prj', 'w') as f:
            f.write(WGS84_WKT)
        with open(base + '.cpg', 'w') as f:
            f.write('UTF-8')
        zpath = destino_base + '.zip'
        with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
            for ext in ('shp', 'shx', 'dbf', 'prj', 'cpg'):
                z.write(base + '.' + ext, nome + '.' + ext)
        return zpath
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# entrada: pasta / zips
# --------------------------------------------------------------------------
def _extrair_zips(pasta_entrada, tmp, log):
    """Copia a árvore de entrada e abre zips (inclusive zip dentro de zip)."""
    nome_entrada = _limpa_nome(os.path.basename(os.path.normpath(pasta_entrada))) or 'entrada'
    raiz = os.path.join(tmp, nome_entrada)
    if os.path.isfile(pasta_entrada) and pasta_entrada.lower().endswith('.zip'):
        raiz = os.path.join(tmp, 'entrada')
        os.makedirs(raiz)
        with zipfile.ZipFile(pasta_entrada) as z:
            z.extractall(os.path.join(raiz, _limpa_nome(os.path.basename(pasta_entrada)[:-4])))
    else:
        shutil.copytree(pasta_entrada, raiz)
    for _ in range(4):
        achou = False
        for d, _, fs in os.walk(raiz):
            for f in fs:
                if f.lower().endswith('.zip'):
                    p = os.path.join(d, f)
                    alvo = os.path.join(d, os.path.splitext(f)[0] + '__zip')
                    try:
                        with zipfile.ZipFile(p) as z:
                            z.extractall(alvo)
                    except zipfile.BadZipFile:
                        log('zip inválido ignorado: %s' % f)
                    os.remove(p)
                    achou = True
        if not achou:
            break
    return raiz


def _md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for bloco in iter(lambda: f.read(1 << 20), b''):
            h.update(bloco)
    return h.hexdigest()


# --------------------------------------------------------------------------
# API principal
# --------------------------------------------------------------------------
def converter_pasta(entrada, saida, opcoes=None, fabrica_transformador=None,
                    log=print, progresso=None, cancelado=None):
    """
    entrada: pasta (ou .zip) com os mapas, em qualquer estrutura de subpastas.
    saida:   pasta onde serão gravados os zips prontos e o relatorio.csv.
    fabrica_transformador(prj_wkt, bbox) -> função (x, y) -> (lon, lat) ou None
    Retorna (lista de zips gerados, caminho do relatório).
    """
    opcoes = opcoes or Opcoes()
    os.makedirs(saida, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='oc_in_')
    gerados, relatorio = [], []
    try:
        raiz = _extrair_zips(entrada, tmp, log)
        # 1) pastas TASKDATA (ISOXML): vão direto, sem conversão
        tasks = isoxml.encontrar(raiz)
        # pasta normal antes das cópias de backup, para não duplicar tarefas
        tasks.sort(key=lambda d: ('BACKUP' in os.path.basename(d).upper() or
                                  'CORRUPT' in os.path.basename(d).upper(), d))
        vistas, assinaturas = set(), set()
        for d in tasks:
            if cancelado and cancelado():
                break
            rel_nome = os.path.relpath(d, raiz)
            ass = isoxml.assinatura(d)
            if ass in assinaturas:
                relatorio.append(dict(origem=rel_nome, formato='ISOXML (TASKDATA)', saida='', poligonos='',
                                      inicio='', fim='', avisos='pasta TASKDATA repetida: ignorada'))
                continue
            assinaturas.add(ass)
            pai = os.path.basename(os.path.dirname(d)) or 'TASKDATA'
            base = _limpa_nome('%s_%s' % (_os_da_pasta(d) or pai, os.path.basename(d)))
            try:
                linhas_t = isoxml.processar(d, saida, opcoes.excluir_tarefas, log, base, vistas)
            except Exception as e:
                linhas_t = [dict(formato='ISOXML (TASKDATA)', saida='', poligonos=0, inicio='',
                                 fim='', avisos='erro: %s' % e)]
            for r in linhas_t:
                r['origem'] = rel_nome + (' :: ' + r['tarefa'] if r.get('tarefa') else '')
                relatorio.append(r)
                if r.get('saida') and r['saida'] not in gerados:
                    gerados.append(r['saida'])
        raizes_com_taskdata = {os.path.dirname(d) for d in tasks}

        # 2) shapefiles
        shps = sorted(os.path.join(d, f) for d, _, fs in os.walk(raiz)
                      for f in fs if f.lower().endswith('.shp')
                      and not any(os.path.join(d, f).startswith(t + os.sep) for t in tasks))
        log('%d pastas TASKDATA e %d shapefiles encontrados' % (len(tasks), len(shps)))
        topcon_aux = []
        for p in list(shps):
            if not eh_relatorio_topcon(p):
                continue
            nome = os.path.basename(p)
            mesmo_pendrive = any(p.startswith(r + os.sep) for r in raizes_com_taskdata)
            if 'Taxa Efetiva' in nome and not mesmo_pendrive:
                continue      # sem TASKDATA: converte como antes, com aviso
            shps.remove(p)
            topcon_aux.append((p, mesmo_pendrive))
        for p, mesmo in topcon_aux:
            relatorio.append(dict(origem=os.path.relpath(p, raiz), formato='Relatório Topcon (Reports)',
                                  saida='', poligonos='', inicio='', fim='',
                                  avisos='ignorado: ' + ('use a pasta TASKDATA do mesmo pen drive (já incluída)'
                                                         if mesmo else 'camada auxiliar de relatório')))
        # duplicados: mesmo .shp em mais de uma OS
        grupos = collections.OrderedDict()
        for p in shps:
            grupos.setdefault(_md5(p), []).append(p)
        itens = list(grupos.values())
        for n, caminhos in enumerate(itens):
            if cancelado and cancelado():
                break
            if progresso:
                progresso(100.0 * n / max(len(itens), 1))
            principal = caminhos[0]
            rel_nome = os.path.relpath(principal, raiz)
            oss = []
            for c in caminhos:
                o = _os_da_pasta(os.path.dirname(c))
                if o and o not in oss:
                    oss.append(o)
            descos = '/'.join(oss) if len(oss) > 1 else None
            os_alt = oss[0] if oss else ''
            if len(caminhos) > 1:
                dup_txt = 'mesmo arquivo em %d pastas (OS %s)%s' % (
                    len(caminhos), ', '.join(oss) or '-',
                    '' if opcoes.juntar_duplicados else '; gerado uma vez só, com a OS da 1a pasta')
            else:
                dup_txt = ''
            try:
                res = _converter_um(principal, saida, opcoes, fabrica_transformador,
                                    descos if opcoes.juntar_duplicados else None, os_alt)
            except Exception as e:  # nunca derruba o lote inteiro
                res = [dict(origem=rel_nome, formato='ERRO', saida='', poligonos=0,
                            inicio='', fim='', avisos='erro: %s' % e)]
            if eh_relatorio_topcon(principal):
                for r in res:
                    r['avisos'] = ('relatório Topcon: se tiver a pasta TASKDATA do monitor, use-a '
                                   '(tem hora e taxa reais)' + ('; ' + r['avisos'] if r['avisos'] else ''))
            for r in res:
                r['origem'] = rel_nome
                if dup_txt:
                    r['avisos'] = (dup_txt + ('; ' + r['avisos'] if r['avisos'] else ''))
                relatorio.append(r)
                if r.get('saida'):
                    gerados.append(r['saida'])
                log('%-28s %-30s %6s pol  %s' % (r['formato'], os.path.basename(r.get('saida') or '-'),
                                                r['poligonos'], r['avisos']))
        rel = os.path.join(saida, 'relatorio_conversao.csv')
        with open(rel, 'w', encoding='utf-8-sig') as f:
            f.write('origem;formato;arquivo_gerado;poligonos;inicio;fim;avisos\n')
            for r in relatorio:
                f.write(';'.join(str(r.get(k, '')).replace(';', ',') for k in
                                 ('origem', 'formato', 'saida', 'poligonos', 'inicio', 'fim', 'avisos')) + '\n')
        if progresso:
            progresso(100.0)
        return gerados, rel
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _converter_um(caminho, saida, opcoes, fabrica_transformador, descos_override, os_alt=''):
    mapa = Mapa(caminho)
    if not mapa.os_pasta and os_alt:
        mapa.os_pasta = os_alt
    try:
        fmt = mapa.formato
        if fmt is None:
            return [dict(formato='DESCONHECIDO', saida='', poligonos=0, inicio='', fim='',
                         avisos='formato não reconhecido; colunas: ' + ', '.join(mapa.campos[:15]))]
        avisos = []
        bbox = mapa.reader.bbox if len(mapa.reader) else [0, 0, 0, 0]
        transformar = None
        projetado = abs(bbox[0]) > 180 or abs(bbox[1]) > 90
        if projetado:
            if fabrica_transformador:
                transformar = fabrica_transformador(mapa.prj, bbox)
            if transformar is None:
                return [dict(formato=FORMATOS[fmt], saida='', poligonos=0, inicio='', fim='',
                             avisos='coordenadas projetadas e não foi possível reprojetar (.prj ausente ou inválido)')]
            avisos.append('reprojetado para WGS84')
        if not mapa.prj:
            avisos.append('.prj ausente: assumido WGS84')
        linhas = _linhas(mapa, transformar, avisos)
        if fmt == 'SPRAYING':
            _velocidade_por_deslocamento(linhas)
        os_id = descos_override or mapa.os_pasta
        arq = os.path.basename(caminho)[:-4]
        prefixo = os_id.replace('/', '-') if os_id else 'semOS'
        base_nome = _limpa_nome(arq if arq.startswith(prefixo) else '%s_%s' % (prefixo, arq))
        # separação por máquina e dia (SPRAYING mistura 3 máquinas por semana)
        grupos = collections.OrderedDict()
        if fmt == 'SPRAYING' and opcoes.separar_maquina_dia:
            for L in linhas:
                if L['t'] is None:
                    continue
                dia_local = (L['t'] - datetime.timedelta(hours=3)).date()
                grupos.setdefault((L['maquina'], dia_local), []).append(L)
        else:
            grupos[None] = linhas
        resultados = []
        for chave, ls in grupos.items():
            av = list(avisos)
            ls = _finalizar(ls, fmt, opcoes, av)
            if not ls:
                resultados.append(dict(formato=FORMATOS[fmt], saida='', poligonos=0, inicio='',
                                       fim='', avisos='; '.join(av + ['nenhum polígono válido'])))
                continue
            nome = base_nome
            if chave:
                nome = _limpa_nome('%s_%s_%s' % (base_nome, chave[0] or 'maq', chave[1].strftime('%Y-%m-%d')))
            ts = [L['t'] for L in ls if L['t'] is not None]
            if ts and (max(ts) - min(ts)).days >= 3:
                av.append('ATENÇÃO: cobre %d dias (possível junção de trabalhos)' % (max(ts) - min(ts)).days)
            if fmt == 'SPRAYING':
                for L in ls:
                    if not L['g'].get('FROTA'):
                        L['g']['FROTA'] = L['maquina']
                    L['g']['DATA'] = (L['t'] - datetime.timedelta(hours=3)).date()
            destino = _destino_unico(os.path.join(saida, nome + '_OC'))
            z = _gravar_zip(destino, ls, opcoes, mapa.os_pasta, descos_override)
            resultados.append(dict(formato=FORMATOS[fmt], saida=z, poligonos=len(ls),
                                   inicio=min(ts).strftime('%d/%m/%Y %H:%M') if ts else '',
                                   fim=max(ts).strftime('%d/%m/%Y %H:%M') if ts else '',
                                   avisos='; '.join(av)))
        return resultados
    finally:
        mapa.fechar()


def _destino_unico(base):
    if not os.path.exists(base + '.zip'):
        return base
    i = 2
    while os.path.exists('%s_%d.zip' % (base, i)):
        i += 1
    return '%s_%d' % (base, i)


# --------------------------------------------------------------------------
# reprojeção fora do QGIS (linha de comando / testes) via pyproj
# --------------------------------------------------------------------------
def fabrica_pyproj(prj_wkt, bbox):
    try:
        from pyproj import CRS, Transformer
    except ImportError:
        return None
    crs = None
    if prj_wkt:
        try:
            crs = CRS.from_wkt(prj_wkt)
        except Exception:
            crs = None
        if 'SIRGAS' in prj_wkt.upper() and '22S' in prj_wkt.upper().replace('_ZONE_', ''):
            crs = CRS.from_epsg(31982)
    if crs is None:
        return None
    tr = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    return lambda x, y: tuple(tr.transform(x, y))


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Converte mapas de terceiros para o padrão Stara (Operations Center).')
    ap.add_argument('entrada')
    ap.add_argument('saida')
    ap.add_argument('--taxa-padrao', type=float, default=30.0)
    ap.add_argument('--nao-separar', action='store_true')
    ap.add_argument('--nao-juntar-duplicados', action='store_true')
    ap.add_argument('--excluir-tarefas', default='TESTE',
                    help='ISOXML: tarefas cujo nome contém estes textos (separados por vírgula) são excluídas')
    a = ap.parse_args()
    converter_pasta(a.entrada, a.saida,
                    Opcoes(a.taxa_padrao, not a.nao_separar, not a.nao_juntar_duplicados,
                           excluir_tarefas=a.excluir_tarefas),
                    fabrica_pyproj)
