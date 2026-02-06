# fastleaksDF v2.0

[![Arquitetura Modular](https://img.shields.io/badge/Arquitetura-Microservices_Leves-2ea44f)](https://github.com/elpardal/fastleaksDF)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab)](https://www.python.org)
[![RabbitMQ](https://img.shields.io/badge/RabbitMQ-AMQP-ff6600)](https://www.rabbitmq.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-SQLModel-336791)](https://www.postgresql.org)

Sistema modular de monitoramento contínuo de vazamentos de dados sensíveis envolvendo ativos do Governo do Distrito Federal. Projetado para operar em ambiente de produção com alta resiliência, segurança e observabilidade — **sem dependências obrigatórias de Docker ou Kubernetes**.

---

## Arquitetura Geral

```mermaid
flowchart TD
    subgraph TELEGRAM [Fontes de Dados]
        T1[Canal Telegram 1]
        T2[Canal Telegram 2]
        T3[...]
    end

    subgraph CORE [Core Services]
        L[telegram-listener<br/>📡 Captura eventos]
        D[downloader<br/>⬇️ Download + SHA256]
        E[extractor<br/>📦 Descompactação segura]
        S[scanner<br/>🔍 Varredura de IOCs]
        P[persister<br/>💾 Persistência auditável]
    end

    subgraph INFRA [Infraestrutura]
        R[(RabbitMQ<br/>Filas Assíncronas)]
        PG[(PostgreSQL<br/>Dados Auditáveis)]
        FS[(Filesystem<br/>Storage Criptografado)]
    end

    subgraph OUTPUT [Saídas]
        A[Alertas Tempo Real]
        V[Visualização Web]
        M[Integração MISP]
    end

    TELEGRAM --> L
    L -->|documents.pending| R
    R --> D
    D -->|documents.downloaded| R
    R --> E
    E -->|files.extracted| R
    R --> S
    D -->|não extraível| S
    S -->|iocs.pending| R
    R --> P
    P --> PG
    D --> FS
    E --> FS
    PG --> A
    PG --> V
    PG --> M

    classDef service fill:#2ea44f,stroke:#1a7f37,color:white,stroke-width:2px
    classDef infra fill:#6e40c9,stroke:#4a219e,color:white,stroke-width:2px
    classDef data fill:#d2691e,stroke:#a0522d,color:white,stroke-width:2px
    classDef output fill:#1f75fe,stroke:#0d4a96,color:white,stroke-width:2px

    class L,D,E,S,P service
    class R,PG,FS infra
    class TELEGRAM data
    class OUTPUT output
```

### Fluxo de Dados Detalhado

```mermaid
sequenceDiagram
    participant T as Telegram
    participant L as telegram-listener
    participant R as RabbitMQ
    participant D as downloader
    participant E as extractor
    participant S as scanner
    participant P as persister
    participant DB as PostgreSQL

    T->>L: Novo documento postado
    L->>R: Publica documents.pending
    R->>D: Consome mensagem
    D->>T: Download do arquivo
    D->>D: SHA256 + armazenamento
    D->>R: Publica documents.downloaded
    
    alt Arquivo compactado
        R->>E: Consome mensagem
        E->>E: Validação segurança<br>(zip bomb, path traversal)
        E->>E: Extração recursiva
        E->>R: Publica files.extracted (×N)
        R->>S: Consome arquivos extraídos
    else Arquivo plano
        R->>S: Consome diretamente
    end
    
    S->>S: Varredura IOC patterns
    S->>R: Publica iocs.pending (×N)
    R->>P: Consome IOC
    P->>DB: Persiste com deduplicação
    DB-->>P: Confirmação
    P-->>S: Ack mensagem
```

---

## Por Que Esta Arquitetura?

| Característica | Benefício Operacional |
|----------------|------------------------|
| **Serviços Independentes** | Atualize/escalone um serviço sem downtime do sistema |
| **Comunicação Assíncrona** | Falha em um serviço ≠ perda de dados (mensagens persistem na fila) |
| **Dead-Letter Queue** | Mensagens problemáticas são isoladas para análise humana |
| **Deduplicação em 3 Camadas** | Zero ruído: mesmo documento ou IOC não é processado duas vezes |
| **Segurança desde a Base** | Proteção nativa contra zip bombs, path traversal e flood attacks |
| **Logs Estruturados JSON** | Pronto para ingestão em ELK, Grafana Loki ou Splunk |
| **Zero Vendor Lock-in** | Roda nativo em qualquer Linux — containers são opcionais |

---

## Setup em 5 Minutos

### Pré-requisitos

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y rabbitmq-server postgresql-14 unrar python3.11 python3.11-venv git

# Iniciar serviços
sudo systemctl start rabbitmq-server postgresql
```

### Configuração do Projeto

```bash
# 1. Clonar repositório
git clone https://github.com/elpardal/fastleaksDF.git
cd fastleaksDF

# 2. Ambiente Python
python3.11 -m venv .venv
source .venv/bin/activate
pip install poetry
poetry install

# 3. Configurar variáveis
cp .env.example .env
nano .env  # Preencha com suas credenciais

# 4. Banco de dados
sudo -u postgres psql -c "CREATE USER fastleaks WITH PASSWORD 'sua_senha_aqui';"
sudo -u postgres psql -c "CREATE DATABASE fastleaksdf OWNER fastleaks;"

# 5. Inicializar filas RabbitMQ
python3 << EOF
import asyncio
from aio_pika import connect_robust, ExchangeType

async def setup():
    conn = await connect_robust("amqp://guest:guest@localhost:5672/")
    ch = await conn.channel()
    await ch.declare_exchange("fastleaksdf", ExchangeType.TOPIC, durable=True)
    await ch.declare_exchange("fastleaksdf-dlq", ExchangeType.TOPIC, durable=True)
    for q in ["documents.pending", "documents.downloaded", "files.extracted", "iocs.pending", "documents.failed"]:
        await ch.declare_queue(q, durable=True)
    await conn.close()
    print("✅ RabbitMQ configurado")

asyncio.run(setup())
EOF

# 6. Executar serviços (em terminais separados)
cd services/telegram-listener && poetry run python main.py
cd services/downloader && poetry run python main.py
cd services/extractor && poetry run python main.py
cd services/scanner && poetry run python main.py
cd services/persister && poetry run python main.py
```

### Estrutura de Pastas

```
fastleaksdf/
├── shared/                 # Código compartilhado (models, config, utils)
├── services/
│   ├── telegram-listener/  # Captura eventos Telegram → fila
│   ├── downloader/         # Download + hashing → fila
│   ├── extractor/          # Descompactação segura → fila
│   ├── scanner/            # Varredura IOC → fila
│   └── persister/          # Persistência PostgreSQL
├── storage/                # Arquivos baixados/extraídos (gitignored)
├── logs/                   # Logs estruturados JSON por serviço
├── .env                    # Configuração sensível
└── run-dev.sh              # Script de inicialização rápida
```

---

## Segurança Implementada

### Proteções Técnicas

| Vetor de Ameaça | Mitigação |
|-----------------|-----------|
| **Zip Bomb** | Limite de 100 MB extraídos + máximo 1.000 arquivos por arquivo |
| **Path Traversal** | Validação rigorosa com `Path.resolve().is_relative_to()` |
| **Flood Telegram** | `flood_sleep_threshold=120` no Telethon + retry exponencial |
| **Deduplicação** | 3 camadas: `doc_id` Telegram → SHA256 arquivo → (documento + valor IOC) |
| **Mensagens Falhas** | Dead-Letter Queue (DLQ) com análise humana obrigatória |
| **Storage** | Estrutura de pastas ofuscada (`storage/ab/cd/sha256_filename`) |
| **Credenciais** | `.env` nunca commitado + variáveis sensíveis não logadas |

### Patterns de IOCs Monitorados

```python
# Configuráveis via .env
CPF:          \b\d{3}\.\d{3}\.\d{3}-\d{2}\b
Email GDF:    \b[A-Za-z0-9._%+-]+@(gdfnet\.df\.gov\.br|df\.gov\.br)\b
Domínio DF:   \b[a-z0-9-]+\.df\.gov\.br\b
IP Interno:   \b10\.(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d?)\.\d{1,3}\.\d{1,3}\b
Credenciais:  (?i)(password|senha|passwd)[\s:=\"']{0,3}([A-Za-z0-9@#$%^&*()_+\-={}\[\]:;\"'<>,.?/\\|`~]{8,})
```

---

## Observabilidade

### Logs Estruturados (JSON)

```json
{
  "timestamp": "2026-02-07T14:30:45.123456Z",
  "level": "info",
  "service": "downloader",
  "event": "download_concluido",
  "sha256": "a3f5c8d2...",
  "filename": "funcionarios.zip",
  "size_mb": 24.7,
  "extractable": true,
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Monitoramento em Tempo Real

```bash
# Logs de todos os serviços
tail -f logs/*.log | jq '.'

# Métricas rápidas
grep -c "download_concluido" logs/downloader.log
grep -c "iocs_encontrados" logs/scanner.log
grep -c "ioc_persistido" logs/persister.log

# Filas RabbitMQ
sudo rabbitmqctl list_queues name messages messages_ready messages_unacknowledged
```

---

## Modos de Operação

### Modo Desenvolvimento (1 VM)
```bash
# Todos os serviços em terminais separados
./run-dev.sh
```

### Modo Produção (systemd)
```ini
# /etc/systemd/system/fastleaksdf@.service
[Unit]
Description=fastleaksDF v2.0 - %i
After=network.target rabbitmq-server.service postgresql.service

[Service]
User=fastleaks
WorkingDirectory=/opt/fastleaksdf/services/%i
EnvironmentFile=/opt/fastleaksdf/.env
ExecStart=/opt/fastleaksdf/.venv/bin/poetry run python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
# Habilitar e iniciar todos os serviços
for svc in telegram-listener downloader extractor scanner persister; do
  sudo systemctl enable fastleaksdf@$svc
  sudo systemctl start fastleaksdf@$svc
done
```

### Modo Containerizado (Opcional)
```yaml
# docker-compose.yml disponível no repositório
services:
  rabbitmq:
    image: rabbitmq:3-management
    ports: ["5672:5672", "15672:15672"]
  
  postgres:
    image: postgres:14
    environment:
      POSTGRES_USER: fastleaks
      POSTGRES_PASSWORD: senha_segura
      POSTGRES_DB: fastleaksdf
  
  telegram-listener:
    build: ./services/telegram-listener
    env_file: .env
    depends_on: [rabbitmq]
```

---

## Roadmap v2.x

| Versão | Funcionalidade | Status |
|--------|----------------|--------|
| **v2.0** | Arquitetura modular + RabbitMQ + segurança base | ✅ Lançado |
| **v2.1** | Interface web para visualização de leaks (FastAPI + HTMX) | 🚧 Em desenvolvimento |
| **v2.2** | Alertas em tempo real via Telegram bot próprio | ⏳ Planejado |
| **v2.3** | Classificação de sensibilidade (baixa/média/alta) | ⏳ Planejado |
| **v2.4** | Integração com MISP para compartilhamento controlado | ⏳ Planejado |
| **v2.5** | Métricas Prometheus + Grafana dashboard | ⏳ Planejado |

---

## Testando o Sistema

### Teste Manual com Arquivo de Exemplo

1. Crie um arquivo de teste com IOCs simulados:
```bash
cat > teste_vazamento.txt << EOF
Lista de servidores DF - CONFIDENCIAL
======================================
nome,cpf,email,senha
Joao Silva,123.456.789-00,joao.silva@gdfnet.df.gov.br,SenhaForte123!
Maria Santos,987.654.321-00,maria.santos@df.gov.br,Admin@2026
Servidor interno: 10.25.42.15
Dominio: intranet.df.gov.br
EOF
```

2. Envie para um canal Telegram monitorado pelo sistema

3. Verifique os logs:
```bash
tail -f logs/scanner.log | grep iocs_encontrados
tail -f logs/persister.log | grep ioc_persistido
```

4. Consulte o banco:
```sql
SELECT i.ioc_type, i.value, d.sha256, t.filename, i.created_at
FROM iocs i
JOIN documents d ON i.document_id = d.id
JOIN telegram_sources t ON d.source_id = t.id
ORDER BY i.created_at DESC
LIMIT 10;
```

---

## Contribuindo

1. Fork o repositório
2. Crie uma branch para sua feature (`git checkout -b feature/nova-coisa`)
3. Commit suas mudanças (`git commit -am 'Adiciona contribuição'`)
4. Push para a branch (`git push origin feature/contribuicao`)
5. Abra um Pull Request

**Requisitos para contribuição:**
- Testes unitários para lógica de negócio crítica
- Logs estruturados em todos os novos serviços
- Documentação atualizada no README

---

## Licença

Este projeto é licenciado sob a licença MIT - veja o arquivo [LICENSE](LICENSE) para detalhes.

> ⚠️ **Aviso Legal**: Este sistema foi projetado exclusivamente para monitoramento defensivo de vazamentos públicos envolvendo ativos do Governo do Distrito Federal. Seu uso deve estar em conformidade com a LGPD (Lei nº 13.709/2018) e demais legislações brasileiras aplicáveis. O autor não se responsabiliza pelo uso indevido desta ferramenta.

---

## Agradecimentos

- [Telethon](https://github.com/LonamiWebs/Telethon) - Cliente Telegram Python assíncrono
- [SQLModel](https://sqlmodel.tiangolo.com) - ORM/Pydantic híbrido
- [aio-pika](https://github.com/mosquito/aio-pika) - Cliente AMQP assíncrono
- [structlog](https://www.structlog.org) - Logs estruturados profissionais

---

> ✨ **FastLeaksDF v2.0** — Monitoramento contínuo, resiliência nativa, segurança desde a base.  
> Desenvolvido com ❤️ para proteger os dados dos cidadãos do Distrito Federal e Entorno.