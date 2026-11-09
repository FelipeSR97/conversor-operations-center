# Histórico de versões

## 0.3.0 — preparação para o GitHub
- Testes automáticos (pytest) com mapas fictícios de cada formato e GitHub Actions.
- Correção: tarefa ISOXML vazia escrita como `<TSK .../>` não era retirada.
- Correção: campos da OS vazios eram gravados com o texto "None".
- Correção: data de GPS zerada (antes de 2000) agora é removida também em arquivos pequenos.

## 0.2.2
- Ao recriar o `TASKDATA.XML`, remove `P231_cur_task` (Topcon), que pode apontar para tarefa retirada.

## 0.2.1
- Nome do zip usa a pasta de entrada real.
- XML recriados mantêm o fim de linha original (LF), também no Windows.

## 0.2.0
- Pastas TASKDATA (ISOXML) enviadas sem conversão.
- Recuperação de backup sem `TASKDATA.XML`, exclusão de tarefas de teste e sem registros.
- Relatórios shapefile do Topcon ignorados quando há TASKDATA no mesmo pen drive.

## 0.1.0
- Primeira versão: 10 formatos de shapefile convertidos para o layout Stara com `Tempo`,
  remoção de polígonos vazios, fechamento de anéis, reprojeção, deduplicação e relatório.
