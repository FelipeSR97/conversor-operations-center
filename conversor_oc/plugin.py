# -*- coding: utf-8 -*-
import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import ConversorOCProvider


class ConversorOCPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):  # noqa: N802
        self.provider = ConversorOCProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        icone = QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))
        self.action = QAction(icone, 'Converter mapas para o Operations Center…',
                              self.iface.mainWindow())
        self.action.triggered.connect(self.abrir)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('Conversor Operations Center', self.action)

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        if self.action:
            self.iface.removePluginMenu('Conversor Operations Center', self.action)
            self.iface.removeToolBarIcon(self.action)

    def abrir(self):
        import processing
        processing.execAlgorithmDialog('conversoroc:converter_mapas')
