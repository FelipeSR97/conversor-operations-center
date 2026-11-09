# -*- coding: utf-8 -*-
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algoritmo import ConverterMapasAlgorithm


class ConversorOCProvider(QgsProcessingProvider):
    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(ConverterMapasAlgorithm())

    def id(self):
        return 'conversoroc'

    def name(self):
        return 'Conversor Operations Center'

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))

    def longName(self):  # noqa: N802
        return self.name()
