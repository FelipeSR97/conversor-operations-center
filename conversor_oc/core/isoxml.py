# -*- coding: utf-8 -*-
"""
Pastas TASKDATA (ISOXML / ISO 11783-10) de monitores ISOBUS (ex.: Topcon).

O Operations Center importa a pasta TASKDATA compactada como Dados de Trabalho,
sem conversão. Este módulo:
  * encontra pastas TASKDATA (inclusive "TASKDATA_CORRUPT_BACKUP_...");
  * confere cada log TLG (XML + BIN, decodificação até o último byte);
  * recria o TASKDATA.XML quando ele falta (caso do backup "corrompido" do Topcon);
  * remove referências quebradas (TLG sem BIN, AFE apontando para arquivo ausente);
  * exclui tarefas de teste (por nome) e tarefas sem registros;
  * gera um zip com a pasta TASKDATA na raiz, pronto para o Operations Center.
"""
import datetime
import hashlib
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

# elementos que podem ser referenciados por XFR no TASKDATA.XML
TIPOS_XFR = ['CTR', 'FRM', 'DVC', 'PFD', 'PDT', 'PGP', 'CPC', 'CCT', 'CLD', 'CSD',
             'CTP', 'OTQ', 'VPN', 'WKR', 'TSK']
_TAM_PTN = {'A': 4, 'B': 4, 'C': 4, 'D': 1, 'E': 2, 'F': 2, 'G': 1, 'H': 4, 'I': 2}


def _arquivos(pasta):
    """Mapa NOME_MAIUSCULO -> nome real (pen drives misturam maiúsculas)."""
    return {f.upper(): f for f in os.listdir(pasta)}


def eh_taskdata(pasta):
    try:
        nomes = _arquivos(pasta)
    except OSError:
        return False
    tem_tsk = any(re.match(r'TSK\d+\.XML$', n) for n in nomes)
    raiz = nomes.get('TASKDATA.XML')
    if raiz and not tem_tsk:
        try:
            tem_tsk = ET.parse(os.path.join(pasta, raiz)).getroot().find('TSK') is not None
        except ET.ParseError:
            pass
    return tem_tsk


def encontrar(raiz):
    """Pastas TASKDATA dentro da árvore de entrada."""
    achadas = []
    for d, subdirs, _ in os.walk(raiz):
        if eh_taskdata(d):
            achadas.append(d)
            subdirs[:] = []
    return sorted(achadas)


def conferir_tlg(pasta, nome):
    """Retorna (n_registros, None) se o log está íntegro, ou (n, motivo)."""
    nomes = _arquivos(pasta)
    x, b = nomes.get(nome.upper() + '.XML'), nomes.get(nome.upper() + '.BIN')
    if not x:
        return 0, 'sem %s.XML' % nome
    if not b:
        return 0, 'sem %s.BIN' % nome
    try:
        h = ET.parse(os.path.join(pasta, x)).getroot()
    except ET.ParseError as e:
        return 0, 'XML inválido (%s)' % e
    ptn = h.find('PTN')
    pa = ptn.attrib if ptn is not None else {}
    n_dlv = len(h.findall('DLV'))
    tam = 6 + sum(_TAM_PTN[k] for k, v in pa.items() if v == '' and k in _TAM_PTN)
    dados = open(os.path.join(pasta, b), 'rb').read()
    i = n = 0
    try:
        while i < len(dados):
            i += tam
            k = dados[i]
            i += 1
            if k > n_dlv:
                return n, 'binário inconsistente'
            i += 5 * k
            n += 1
    except IndexError:
        return n, 'binário truncado'
    if i != len(dados):
        return n, 'binário truncado'
    return n, None


def _iso(t):
    try:
        return datetime.datetime.strptime(t[:19], '%Y-%m-%dT%H:%M:%S').strftime('%d/%m/%Y %H:%M')
    except (TypeError, ValueError):
        return ''


def _ler_tarefas(pasta):
    """Lista (arquivo, texto, ElementTree root) dos TSK*.XML."""
    nomes = _arquivos(pasta)
    out = []
    for up, real in sorted(nomes.items()):
        if re.match(r'TSK\d+\.XML$', up):
            txt = open(os.path.join(pasta, real), encoding='utf-8', errors='replace', newline='').read()
            out.append((real, txt, ET.fromstring(txt.encode('utf-8'))))
    return out


def processar(pasta, saida, excluir_nomes=('TESTE',), log=print, nome_base=None, ja_vistas=None):
    """
    Confere/recupera uma pasta TASKDATA e grava <saida>/TASKDATA_<nome>.zip.
    Retorna lista de dicts (uma linha por tarefa) para o relatório.
    """
    nomes = _arquivos(pasta)
    linhas = []
    excluir = [e.strip().upper() for e in excluir_nomes if e and e.strip()]
    raiz_xml = nomes.get('TASKDATA.XML')
    tarefas_arq = _ler_tarefas(pasta)
    inline = False
    if not tarefas_arq and raiz_xml:          # tarefas dentro do próprio TASKDATA.XML
        inline = True
    mudou = raiz_xml is None
    avisos_gerais = []
    if raiz_xml is None:
        avisos_gerais.append('TASKDATA.XML ausente: índice recriado')

    tsk_novo = {}
    usados_tlg = set()
    if not inline:
        for arq, txt, root in tarefas_arq:
            novo = txt
            for t in root.findall('TSK'):
                a, nome = t.get('A'), t.get('B') or ''
                tlgs = [x.get('A') for x in t.findall('TLG')]
                regs, quebrados = 0, []
                for tl in tlgs:
                    n, erro = conferir_tlg(pasta, tl)
                    if erro:
                        quebrados.append('%s: %s' % (tl, erro))
                    else:
                        regs += n
                tims = t.findall('TIM')
                ini = _iso(tims[0].get('A')) if tims else ''
                fim = _iso(tims[-1].get('B') or tims[-1].get('A')) if tims else ''
                motivo = None
                chave = (a, tims[0].get('A') if tims else '')
                if ja_vistas is not None and chave in ja_vistas:
                    motivo = 'excluída (já incluída por outra pasta TASKDATA)'
                elif any(e in nome.upper() for e in excluir):
                    motivo = 'excluída (nome contém %s)' % ', '.join(e for e in excluir if e in nome.upper())
                elif regs == 0:
                    motivo = 'excluída (sem registros)'
                if motivo:
                    # <TSK .../> (tarefa vazia) ou <TSK ...> ... </TSK>
                    novo = re.sub(r'[ \t]*<TSK A="%s"(?:[^>]*/>|[^>]*[^/>]>.*?</TSK>)[ \t]*\r?\n?'
                                  % re.escape(a), '', novo, count=1, flags=re.S)
                    mudou = True
                    linhas.append(dict(formato='ISOXML (TASKDATA)', saida='', poligonos=0,
                                       inicio=ini, fim=fim, tarefa=nome, avisos=motivo))
                    continue
                for q in quebrados:      # tira só o log quebrado, mantém a tarefa
                    tl = q.split(':')[0]
                    novo = re.sub(r'[ \t]*<TLG A="%s"\s*/>[ \t]*\r?\n?' % re.escape(tl), '', novo)
                    mudou = True
                if ja_vistas is not None:
                    ja_vistas.add(chave)
                usados_tlg.update(tl for tl in tlgs if not any(q.startswith(tl + ':') for q in quebrados))
                av = list(avisos_gerais)
                if quebrados:
                    av.append('logs removidos: ' + '; '.join(quebrados))
                linhas.append(dict(formato='ISOXML (TASKDATA)', saida='', poligonos=regs,
                                   inicio=ini, fim=fim, tarefa=nome, avisos=', '.join(av),
                                   _incluida=True))
            tsk_novo[arq] = novo
    else:
        avisos_gerais.append('tarefas dentro do TASKDATA.XML: enviado sem alteração')

    incluidas = [l for l in linhas if l.get('_incluida')]
    if not inline and not incluidas:
        for l in linhas:
            l['avisos'] = (l['avisos'] + '; ' if l['avisos'] else '') + 'nenhuma tarefa válida: zip não gerado'
        return linhas

    # monta a pasta de saída
    tmp = tempfile.mkdtemp(prefix='oc_tsk_')
    try:
        dst = os.path.join(tmp, 'TASKDATA')
        os.makedirs(dst)
        if inline or not mudou:
            for real in nomes.values():
                if os.path.isfile(os.path.join(pasta, real)):
                    shutil.copy(os.path.join(pasta, real), dst)
        else:
            ref_tipos = []
            for tipo in TIPOS_XFR:
                for up, real in sorted(nomes.items()):
                    if re.match(r'%s\d+\.XML$' % tipo, up):
                        if tipo == 'TSK':
                            open(os.path.join(dst, real), 'w', encoding='utf-8', newline='').write(tsk_novo[real])
                        else:
                            shutil.copy(os.path.join(pasta, real), dst)
                        ref_tipos.append(real[:-4])
            for tl in sorted(usados_tlg):
                for ext in ('.XML', '.BIN'):
                    shutil.copy(os.path.join(pasta, nomes[tl.upper() + ext]), dst)
            atrib = ('VersionMajor="4" VersionMinor="2" ManagementSoftwareManufacturer="" '
                     'ManagementSoftwareVersion="" DataTransferOrigin="2"')
            modelo = os.path.join(pasta, raiz_xml) if raiz_xml else None
            if not modelo:   # backup sem índice: copia os atributos da TASKDATA vizinha
                pai = os.path.dirname(pasta)
                for irma in sorted(os.listdir(pai)):
                    cand = os.path.join(pai, irma)
                    if cand != pasta and os.path.isdir(cand):
                        n2 = _arquivos(cand).get('TASKDATA.XML')
                        if n2:
                            modelo = os.path.join(cand, n2)
                            break
            if modelo:
                try:
                    r = ET.parse(modelo).getroot()
                    # P231_cur_task (Topcon) aponta para a tarefa aberta no monitor, que pode ter sido retirada
                    atrib = ' '.join('%s="%s"' % (k, v.replace('"', '&quot;')) for k, v in r.attrib.items()
                                     if not k.startswith('P231_cur_task'))
                except ET.ParseError:
                    pass
            corpo = '\n'.join('    <XFR A="%s" B="1"/>' % a for a in ref_tipos)
            with open(os.path.join(dst, 'TASKDATA.XML'), 'w', encoding='utf-8', newline='') as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n<ISO11783_TaskData %s>\n%s\n'
                        '</ISO11783_TaskData>\n' % (atrib, corpo))
        nome = re.sub(r'[^A-Za-z0-9_-]+', '_', nome_base or os.path.basename(pasta)).strip('_')[:60]
        zpath = os.path.join(saida, 'TASKDATA_%s.zip' % nome)
        i = 2
        while os.path.exists(zpath):
            zpath = os.path.join(saida, 'TASKDATA_%s_%d.zip' % (nome, i))
            i += 1
        with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in sorted(os.listdir(dst)):
                z.write(os.path.join(dst, f), 'TASKDATA/' + f)
        if inline:
            linhas.append(dict(formato='ISOXML (TASKDATA)', saida=zpath, poligonos='',
                               inicio='', fim='', tarefa='', avisos=', '.join(avisos_gerais)))
        for l in linhas:
            if l.pop('_incluida', False):
                l['saida'] = zpath
        log('ISOXML %s: %d tarefas incluídas -> %s' % (os.path.basename(pasta), len(incluidas),
                                                        os.path.basename(zpath)))
        return linhas
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def assinatura(pasta):
    """Hash dos TSK + nomes dos TLG, para não enviar a mesma TASKDATA duas vezes."""
    h = hashlib.md5()
    for arq, txt, _ in _ler_tarefas(pasta):
        h.update(txt.encode('utf-8'))
    return h.hexdigest()
