# -*- coding: utf-8 -*-
"""Conversor de mapas de terceiros para o Operations Center (plugin QGIS)."""


def classFactory(iface):  # noqa: N802 (nome exigido pelo QGIS)
    from .plugin import ConversorOCPlugin
    return ConversorOCPlugin(iface)
