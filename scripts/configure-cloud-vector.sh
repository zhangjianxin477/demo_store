#!/usr/bin/env bash
set -euo pipefail

# Configure the cloud tenant without putting credentials in the upload archive.
# Run from the project root: bash scripts/configure-cloud-vector.sh

ENV_FILE="${1:-deploy/tenants/tenant-a/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "配置文件不存在: $ENV_FILE" >&2
  exit 1
fi

read -r -p "Milvus/Zilliz URI [保留当前值则回车]: " MILVUS_URI
if [[ -z "$MILVUS_URI" ]]; then
  MILVUS_URI="$(sed -n 's/^MILVUS_URI=//p' "$ENV_FILE" | tail -n 1)"
fi
if [[ -z "$MILVUS_URI" ]]; then
  echo "必须提供 Milvus URI" >&2
  exit 1
fi

read -r -s -p "Milvus Token（不会显示）: " MILVUS_TOKEN
echo
if [[ -z "$MILVUS_TOKEN" ]]; then
  echo "必须提供 Milvus Token" >&2
  exit 1
fi

set_env() {
  local key="$1" value="$2"
  # URI and token are expected to contain no newlines; remove old entries and append.
  sed -i "/^${key}=/d" "$ENV_FILE"
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
}

set_env VECTOR_BACKEND milvus
set_env MILVUS_URI "$MILVUS_URI"
set_env MILVUS_TOKEN "$MILVUS_TOKEN"
set_env MILVUS_COLLECTION knowledge_hub_chunks
set_env MILVUS_METRIC_TYPE COSINE
set_env EMBEDDING_DIMENSION 1024
set_env EMBEDDING_LOCAL_ENABLED false

chmod 600 "$ENV_FILE"
echo "已更新 $ENV_FILE"
echo "VECTOR_BACKEND=milvus"
echo "EMBEDDING_DIMENSION=1024"
echo "EMBEDDING_LOCAL_ENABLED=false"
echo "请重建 tenant-a 容器后检查 /api/v1/health。"
