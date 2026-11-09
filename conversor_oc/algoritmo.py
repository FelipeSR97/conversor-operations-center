# -*- coding: utf-8 -*-
import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProject,
)
from qgis.PyQt.QtGui import QIcon

from .core import conversor


def _fabrica_qgis(prj_wkt, bbox):
    """Reprojeção com o próprio QGIS (UTM/SIRGAS -> WGS84)."""
    crs = QgsCoordinateReferenceSystem()
    if prj_wkt:
        crs = QgsCoordinateReferenceSystem.fromWkt(prj_wkt)
        if crs.isValid() and 'SIRGAS' in prj_wkt.upper() and '22S' in prj_wkt.upper().replace('_ZONE_', ''):
            crs = QgsCoordinateReferenceSystem('EPSG:31982')
    if not crs.isValid() or crs.isGeographic():
        return None
    destino = QgsCoordinateReferenceSystem('EPSG:4326')
    ct = QgsCoordinateTransform(crs, destino, QgsProject.instance())

    def transformar(x, y):
        p = ct.transform(QgsPointXY(x, y))
        return (p.x(), p.y())
    return transformar


class ConverterMapasAlgorithm(QgsProcessingAlgorithm):
    ENTRADA = 'ENTRADA'
    SAIDA = 'SAIDA'
    TAXA = 'TAXA_PADRAO'
    SEPARAR = 'SEPARAR'
    JUNTAR = 'JUNTAR_DUPLICADOS'
    EXCLUIR = 'EXCLUIR_TAREFAS'
    RELATORIO = 'RELATORIO'
    TOTAL = 'TOTAL'

    def name(self):
        return 'converter_mapas'

    def displayName(self):  # noqa: N802
        return 'Converter mapas para o Operations Center'

    def group(self):
        return ''

    def groupId(self):  # noqa: N802
        return ''

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))

    def shortHelpString(self):  # noqa: N802
        return (
            '<p>Converte mapas de trabalho (shapefile) de monitores de terceiros para o '
            'layout de colunas do <b>Stara</b>, que o John Deere Operations Center '
            'importa como <b>Dados de Trabalho</b>.</p>'
            '<p><b>Formatos reconhecidos automaticamente:</b> Stara, Hexagon (MP AGRO), '
            'Verion, Trimble / GS3 / GS4, Trimble Coverage, Jacto, plataforma SPRAYING, '
            'Topcon relatório (Taxa Efetiva), AgNav (KML convertido) e mapas só com geometria.</p>'
            '<p><b>ISOXML (pasta TASKDATA, ex.: Topcon):</b> enviada sem conversão, compactada '
            'com a pasta TASKDATA na raiz. Se faltar o TASKDATA.XML (backup "corrompido"), '
            'o índice é recriado; logs quebrados, tarefas sem registros e tarefas de teste '
            '(nome contendo o texto informado) são retirados. Tarefas repetidas entre a '
            'TASKDATA e o backup entram uma vez só. Shapefiles da pasta Reports do Topcon são '
            'ignorados quando a TASKDATA do mesmo pen drive está presente.</p>'
            '<p><b>Entrada:</b> uma pasta com os mapas em qualquer estrutura de subpastas, '
            'inclusive .zip (e zip dentro de zip). O número da OS é lido do nome da pasta '
            '(ex.: <i>123456_08-10-2026 16-15-16</i>) quando o campo DESCOS estiver vazio.</p>'
            '<p><b>Saída:</b> um .zip por trabalho, pronto para o Operations Center '
            '(Files &gt; Carregar), e um <i>relatorio_conversao.csv</i> com avisos: '
            'Tempo fictício, taxa padrão usada, datas espúrias, arquivos duplicados e '
            'trabalhos que cobrem muitos dias.</p>'
            '<p><b>Taxa padrão:</b> usada só quando o mapa não tem taxa nenhuma (AgNav, '
            'mapas só com geometria). Taxa zerada sobe, mas o mapa não aparece.</p>'
        )

    def createInstance(self):  # noqa: N802
        return ConverterMapasAlgorithm()

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFile(
            self.ENTRADA, 'Pasta com os mapas (shapefiles e/ou .zip)',
            behavior=QgsProcessingParameterFile.Folder))
        self.addParameter(QgsProcessingParameterNumber(
            self.TAXA, 'Taxa padrão quando o mapa não tem taxa (por hectare)',
            type=QgsProcessingParameterNumber.Double, defaultValue=30.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SEPARAR, 'Separar SPRAYING por máquina e dia', defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.JUNTAR, 'Arquivo repetido em várias OS: gerar um só (DESCOS com todas as OS)',
            defaultValue=True))
        self.addParameter(QgsProcessingParameterString(
            self.EXCLUIR, 'ISOXML: excluir tarefas cujo nome contém (separe por vírgula)',
            defaultValue='TESTE', optional=True))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.SAIDA, 'Pasta de saída (zips para o Operations Center)'))
        self.addOutput(QgsProcessingOutputFile(self.RELATORIO, 'Relatório da conversão'))
        self.addOutput(QgsProcessingOutputNumber(self.TOTAL, 'Arquivos gerados'))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        entrada = self.parameterAsFile(parameters, self.ENTRADA, context)
        saida = self.parameterAsString(parameters, self.SAIDA, context)
        if not entrada or not os.path.exists(entrada):
            raise QgsProcessingException('Pasta de entrada não encontrada.')
        opcoes = conversor.Opcoes(
            taxa_padrao=self.parameterAsDouble(parameters, self.TAXA, context),
            separar_maquina_dia=self.parameterAsBool(parameters, self.SEPARAR, context),
            juntar_duplicados=self.parameterAsBool(parameters, self.JUNTAR, context),
            excluir_tarefas=self.parameterAsString(parameters, self.EXCLUIR, context) or '',
        )
        gerados, relatorio = conversor.converter_pasta(
            entrada, saida, opcoes, _fabrica_qgis,
            log=feedback.pushInfo,
            progresso=feedback.setProgress,
            cancelado=feedback.isCanceled,
        )
        feedback.pushInfo('')
        feedback.pushInfo('%d arquivos gerados em %s' % (len(gerados), saida))
        feedback.pushInfo('Relatório: %s' % relatorio)
        return {self.SAIDA: saida, self.RELATORIO: relatorio, self.TOTAL: len(gerados)}
