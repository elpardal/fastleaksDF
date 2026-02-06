#!/bin/bash
set -e

echo "🚀 Iniciando fastleaksDF v2.0 (5 serviços + RabbitMQ)"

# Pré-requisitos
command -v rabbitmq-server >/dev/null || { echo "❌ RabbitMQ não instalado"; exit 1; }
command -v poetry >/dev/null || { echo "❌ Poetry não instalado"; exit 1; }

# Storage
mkdir -p storage

# Iniciar RabbitMQ em background (se não estiver rodando)
if ! pgrep -x "beam.smp" > /dev/null; then
  echo "▶️  Iniciando RabbitMQ..."
  sudo systemctl start rabbitmq-server || rabbitmq-server -detached
  sleep 5
fi

# Criar exchanges/queues
python3 << EOF
import asyncio
from aio_pika import connect_robust, ExchangeType

async def setup():
    conn = await connect_robust("amqp://guest:guest@localhost:5672/")
    ch = await conn.channel()
    ex = await ch.declare_exchange("fastleaksdf", ExchangeType.TOPIC, durable=True)
    for q in ["documents.pending", "documents.downloaded", "files.extracted", "iocs.pending", "documents.failed"]:
        await ch.declare_queue(q, durable=True)
    await conn.close()
    print("✅ RabbitMQ configurado")

asyncio.run(setup())
EOF

# Iniciar serviços em background (logs separados)
cd services/telegram-listener && poetry run python main.py >> ../../logs/telegram-listener.log 2>&1 &
PID1=$!
cd ../downloader && poetry run python main.py >> ../../logs/downloader.log 2>&1 &
PID2=$!
cd ../extractor && poetry run python main.py >> ../../logs/extractor.log 2>&1 &
PID3=$!
cd ../scanner && poetry run python main.py >> ../../logs/scanner.log 2>&1 &
PID4=$!
cd ../persister && poetry run python main.py >> ../../logs/persister.log 2>&1 &
PID5=$!

echo "✅ Serviços rodando:"
echo "   telegram-listener: $PID1"
echo "   downloader:        $PID2"
echo "   extractor:         $PID3"
echo "   scanner:           $PID4"
echo "   persister:         $PID5"
echo ""
echo "📌 Logs em ./logs/"
echo "🛑 Para parar: kill $PID1 $PID2 $PID3 $PID4 $PID5"

wait