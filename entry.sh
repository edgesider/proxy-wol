#!/usr/bin/env bash

PORT=${PORT:-8000}
TARGET_HOST=${TARGET_HOST:?TARGET_HOST is not set}
TARGET_PORT=${TARGET_PORT:?TARGET_PORT is not set}
TARGET_MAC=${TARGET_MAC:?TARGET_MAC is not set}
TARGET_SSH_CMD=${TARGET_SSH_CMD:?TARGET_SSH_CMD is not set}

cmd="gunicorn proxy:main --worker-class aiohttp.GunicornWebWorker -b 0.0.0.0:$PORT --reuse-port --capture-output --access-logfile -"
echo $cmd
$cmd