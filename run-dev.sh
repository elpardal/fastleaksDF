#!/bin/bash
set -e

echo "🚀 fastleaksDF v2.0 - Ambiente de Desenvolvimento"
echo ""

# Verifica dependências
command -v poetry >/dev/null || { echo "❌ Poetry não instalado"; exit 1; }
command -v rabbitmqctl >/dev/null || { echo "❌ RabbitMQ não instalado"; exit 1; }
command -v psql >/dev/null || { echo "❌ PostgreSQL não instalado"; exit 1; }

# Prepara ambiente
mkdir -p storage logs
cp .env.example .env 2>/dev/null || true

# Inicia RabbitMQ se necessário
if ! rabbitmqctl ping 2>/dev/null | grep -q PONG; then
  echo "▶️  Iniciando RabbitMQ..."
  sudo systemctl start rabbitmq-server 2>/dev/null || true
  sleep 3
fi

# Setup exchanges/queues
python3 << EOF
import asyncio
from aio_pika import connect_robust, ExchangeType

async def setup():
    conn = await connect_robust("amqp://guest:guest@localhost:5672/")
    ch = await conn.channel()
    
    # Exchange principal
    await ch.declare_exchange("fastleaksdf", ExchangeType.TOPIC, durable=True)
    
    # Dead-letter exchange
    await ch.declare_exchange("fastleaksdf-dlq", ExchangeType.TOPIC, durable=True)
    
    # Filas
    for q in ["documents.pending", "documents.downloaded", "files.extracted", "iocs.pending", "documents.failed"]:
        await ch.declare_queue(q, durable=True)
    
    print("✅ RabbitMQ configurado")
    await conn.close()

asyncio.run(setup())
EOF

echo ""
echo "✅ Ambiente pronto!"
echo ""
echo "Próximos passos:"
echo "  1. Edite .env com suas credenciais Telegram e DB"
echo "  2. Inicie os serviços em terminais separados:"
echo "       cd services/telegram-listener && poetry run python main.py"
echo "       cd services/downloader && poetry run python main.py"
echo "       cd services/extractor && poetry run python main.py"
echo "       cd services/scanner && poetry run python main.py"
echo "       cd services/persister && poetry run python main.py"
echo ""
echo "Logs: tail -f logs/*.log | jq ."