$ErrorActionPreference = "Stop"

# Local fallback launcher for self-hosted SearXNG before it is wired into docker-compose.
$image = "searxng/searxng:latest"
$containerName = "qanorm-searxng"
$port = "8080:8080"

$existing = docker ps -a --filter "name=$containerName" --format "{{.Names}}"
if ($existing -contains $containerName) {
    docker start $containerName | Out-Null
    Write-Output "Started existing container '$containerName' on http://localhost:8080"
    exit 0
}

docker run -d --name $containerName -p $port `
  -e BASE_URL=http://localhost:8080/ `
  -e INSTANCE_NAME=qanorm-searxng `
  $image | Out-Null

Write-Output "Started new container '$containerName' on http://localhost:8080"
