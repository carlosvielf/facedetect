# Registro de validação — 20/10/2026

## Executado

- Inspeção recursiva do workspace: pasta de firmware vazia, sem arquivos originais da Freenove.
- Consulta à documentação e ao código oficial do UniFace, incluindo `FaceAnalyzer`, modelos padrão e extras CPU/GPU.
- Parse com `ConvertFrom-Json`: `frontend/package.json`, `frontend/tsconfig.json` e `frontend/vercel.json` válidos.
- Comparação textual entre modelos e migração inicial: as dez tabelas desta etapa aparecem em ambos. Esta verificação não valida SQL nem substitui executar a migração.
- Verificação de Python dentro e fora do sandbox: o comando aponta para o alias da Microsoft Store, sem instalação de Python disponível.
- Tentativa de `npm run build`: falhou antes de iniciar porque npm não existe no PATH.
- Verificação de Docker: comando não encontrado.

## Não executado / não aprovado

- pytest: sem interpretador Python e dependências instaladas.
- TypeScript/Vite: sem Node/npm.
- Migração PostgreSQL: sem Docker/PostgreSQL disponível.
- Inferência UniFace, download dos pesos, fotos de cadastro, acurácia e calibração.
- ESP32, pinout, captura física, desconexão/reconexão reais.
- Navegação em navegador e responsividade visual.
- Deploy: não realizado.

Os testes incluídos são destinados a autorização, revisão, imutabilidade, unicidade, matching, criptografia, confirmação temporal, captura HTTP e exportações. Usam vetores sintéticos e HTTP controlado somente nos testes; o aplicativo usa UniFace e captura reais. Os arquivos de teste ainda precisam ser executados, e não há afirmação de aprovação.

Siga o README para instalar os pré-requisitos, executar testes/build e realizar a validação com hardware. Há pendências de normalização e funcionalidades institucionais detalhadas no README; esta entrega é a primeira etapa integrada, não o sistema completo homologado.
