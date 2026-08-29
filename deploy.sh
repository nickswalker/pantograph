#!/bin/bash
set -e

SERVER_USER="rcr"
APP_PATH="~/panto.raceconditionrunning.com"

echo "🔨 Building Docker image for x86..."
docker buildx build --platform linux/amd64 \
  -t pantograph:latest \
  --output type=docker,dest=pantograph.tar .

echo "📤 Transferring image to server..."
rsync pantograph.tar $SERVER_USER:~/

echo "🚀 Deploying on server..."
ssh $SERVER_USER "
  echo '📥 Loading Docker image...'
  docker load < pantograph.tar

  echo '🧹 Cleaning up tar file...'
  rm pantograph.tar

  echo '🔄 Restarting services...'
  cd $APP_PATH
  docker compose up -d

  echo '✅ Deployment complete!'
"

echo "🧹 Cleaning up local tar file..."
rm pantograph.tar

echo "🎉 Deploy finished!"