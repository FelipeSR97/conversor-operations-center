"""Gera dist/conversor_oc.zip, pronto para 'Instalar a partir do ZIP' no QGIS."""
import os
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(RAIZ, 'dist'), exist_ok=True)
destino = os.path.join(RAIZ, 'dist', 'conversor_oc.zip')
with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
    for pasta, subpastas, arquivos in os.walk(os.path.join(RAIZ, 'conversor_oc')):
        subpastas[:] = [d for d in subpastas if d != '__pycache__']
        for f in arquivos:
            if f.endswith('.pyc'):
                continue
            caminho = os.path.join(pasta, f)
            z.write(caminho, os.path.relpath(caminho, RAIZ))
print(destino)
