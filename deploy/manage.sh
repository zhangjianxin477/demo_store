#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
TENANTS_DIR="$PROJECT_DIR/deploy/tenants"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

check_prereqs() {
    for cmd in docker docker-compose; do
        if ! command -v "$cmd" &>/dev/null; then
            log_error "$cmd is not installed"
            exit 1
        fi
    done
}

list_tenants() {
    echo "=== Current Tenants ==="
    if [ ! -d "$TENANTS_DIR" ]; then
        echo "  (none)"
        return
    fi
    for env_file in "$TENANTS_DIR"/*/.env; do
        if [ -f "$env_file" ]; then
            tenant_dir=$(basename "$(dirname "$env_file")")
            tenant_id=$(grep "^TENANT_ID=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "unknown")
            status=$(docker inspect -f '{{.State.Status}}' "kh-$tenant_dir" 2>/dev/null || echo "stopped")
            printf "  %-20s  id=%-15s  status=%s\n" "$tenant_dir" "$tenant_id" "$status"
        fi
    done
}

create_tenant() {
    local tenant_name="$1"
    local api_key="${2:-}"
    local base_url="${3:-https://open.bigmodel.cn/api/paas/v4/}"
    local model="${4:-glm-4-flash}"
    local embedding_model="${5:-embedding-3}"
    local domain="${6:-yourdomain.com}"

    if [ -z "$tenant_name" ]; then
        log_error "Tenant name is required"
        usage
        exit 1
    fi

    if [[ ! "$tenant_name" =~ ^[a-z0-9-]+$ ]]; then
        log_error "Tenant name must contain only lowercase letters, numbers, and hyphens"
        exit 1
    fi

    local tenant_dir="$TENANTS_DIR/$tenant_name"
    if [ -d "$tenant_dir" ]; then
        log_error "Tenant '$tenant_name' already exists"
        exit 1
    fi

    if [ -z "$api_key" ]; then
        read -rsp "Enter OpenAI API Key: " api_key
        echo
    fi

    mkdir -p "$tenant_dir"

    # 生成随机 SECRET_KEY
    local secret_key
    if command -v openssl &>/dev/null; then
        secret_key=$(openssl rand -hex 32)
    else
        secret_key="change-me-$(date +%s)-$RANDOM"
    fi

    sed -e "s/{{TENANT_ID}}/$tenant_name/g" \
        -e "s|{{OPENAI_API_KEY}}|$api_key|g" \
        -e "s|{{OPENAI_BASE_URL}}|$base_url|g" \
        -e "s|{{OPENAI_MODEL}}|$model|g" \
        -e "s|{{EMBEDDING_MODEL}}|$embedding_model|g" \
        -e "s|{{DOMAIN}}|$domain|g" \
        -e "s|knowledge-hub-secret-key-change-in-production|$secret_key|g" \
        "$TENANTS_DIR/.env.template" > "$tenant_dir/.env"

    local cors_origin="https://$tenant_name.$domain"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|{{CORS_PLACEHOLDER}}|$cors_origin|g" "$tenant_dir/.env" 2>/dev/null || true
    else
        sed -i "s|{{CORS_PLACEHOLDER}}|$cors_origin|g" "$tenant_dir/.env" 2>/dev/null || true
    fi

    local escaped_name=$(echo "$tenant_name" | sed 's/-/\\-/g')

    local service_block="
  $tenant_name:
    <<: *app-common
    container_name: kh-$tenant_name
    env_file:
      - ./deploy/tenants/$tenant_name/.env
    volumes:
      - ${tenant_name}-data:/app/data
    networks:
      internal:
        aliases:
          - $tenant_name
"

    echo "$service_block" >> "$COMPOSE_FILE"

    local volume_block="
  ${tenant_name}-data:
    driver: local
"

    if ! grep -q "${tenant_name}-data:" "$COMPOSE_FILE"; then
        echo "$volume_block" >> "$COMPOSE_FILE"
    fi

    log_info "Tenant '$tenant_name' created"
    log_info "  Config: $tenant_dir/.env"
    log_info "  URL: https://$tenant_name.$domain"
    log_info ""
    log_info "To start: $0 start $tenant_name"
}

remove_tenant() {
    local tenant_name="$1"

    if [ -z "$tenant_name" ]; then
        log_error "Tenant name is required"
        usage
        exit 1
    fi

    local tenant_dir="$TENANTS_DIR/$tenant_name"
    if [ ! -d "$tenant_dir" ]; then
        log_error "Tenant '$tenant_name' does not exist"
        exit 1
    fi

    read -rp "Remove tenant '$tenant_name' and all its data? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        log_info "Cancelled"
        return
    fi

    docker-compose -f "$COMPOSE_FILE" stop "$tenant_name" 2>/dev/null || true
    docker-compose -f "$COMPOSE_FILE" rm -f "$tenant_name" 2>/dev/null || true

    docker volume rm "${tenant_name}-data" 2>/dev/null || true

    rm -rf "$tenant_dir"

    local tmp_compose=$(mktemp)
    awk -v tenant="$tenant_name" '
        /^[[:space:]]*'"$tenant_name"':/ { skip=1; next }
        /^[[:space:]]*[a-z0-9-]+:/ { skip=0 }
        /^[[:space:]]*'"${tenant_name}"'-data:/ { next }
        !skip { print }
    ' "$COMPOSE_FILE" > "$tmp_compose"
    mv "$tmp_compose" "$COMPOSE_FILE"

    log_info "Tenant '$tenant_name' removed"
}

start_tenant() {
    local tenant_name="$1"
    log_info "Starting tenant '$tenant_name'..."
    docker-compose -f "$COMPOSE_FILE" up -d --build "$tenant_name"
    log_info "Tenant '$tenant_name' started"
}

stop_tenant() {
    local tenant_name="$1"
    log_info "Stopping tenant '$tenant_name'..."
    docker-compose -f "$COMPOSE_FILE" stop "$tenant_name"
    log_info "Tenant '$tenant_name' stopped"
}

start_all() {
    log_info "Starting all services..."
    docker-compose -f "$COMPOSE_FILE" up -d --build
    log_info "All services started"
}

stop_all() {
    log_info "Stopping all services..."
    docker-compose -f "$COMPOSE_FILE" down
    log_info "All services stopped"
}

show_status() {
    docker-compose -f "$COMPOSE_FILE" ps
}

show_logs() {
    local tenant_name="${1:-}"
    if [ -n "$tenant_name" ]; then
        docker-compose -f "$COMPOSE_FILE" logs -f --tail=100 "$tenant_name"
    else
        docker-compose -f "$COMPOSE_FILE" logs -f --tail=100
    fi
}

backup_tenant() {
    local tenant_name="$1"
    local backup_dir="$PROJECT_DIR/backups/$tenant_name/$(date +%Y%m%d_%H%M%S)"

    log_info "Backing up tenant '$tenant_name'..."
    mkdir -p "$backup_dir"

    if [ -d "$TENANTS_DIR/$tenant_name" ]; then
        cp -r "$TENANTS_DIR/$tenant_name" "$backup_dir/config"
    fi

    docker run --rm \
        -v "${tenant_name}-data:/data:ro" \
        -v "$backup_dir:/backup" \
        alpine tar czf /backup/data.tar.gz -C /data .

    log_info "Backup saved to $backup_dir"
}

usage() {
    cat <<EOF
Knowledge Hub - Multi-tenant Deployment Manager

Usage: $0 <command> [arguments]

Commands:
  list                                    List all tenants
  create <name> [api_key] [base_url] [model] [embedding_model] [domain]
                                          Create a new tenant
  remove <name>                           Remove a tenant and its data
  start <name>                            Start a tenant
  stop <name>                             Stop a tenant
  start-all                               Start all services (nginx + tenants)
  stop-all                                Stop all services
  status                                  Show service status
  logs [name]                             Show logs (optional: tenant name)
  backup <name>                           Backup tenant data

Examples:
  $0 create company-a sk-xxx
  $0 create company-b sk-yyy https://api.openai.com/v1/ gpt-4o text-embedding-3-small example.com
  $0 start company-a
  $0 logs company-a
  $0 backup company-a
  $0 remove company-a
EOF
}

check_prereqs

case "${1:-}" in
    list)       list_tenants ;;
    create)     create_tenant "${2:-}" "${3:-}" "${4:-}" "${5:-}" "${6:-}" "${7:-}" ;;
    remove)     remove_tenant "${2:-}" ;;
    start)      start_tenant "${2:-}" ;;
    stop)       stop_tenant "${2:-}" ;;
    start-all)  start_all ;;
    stop-all)   stop_all ;;
    status)     show_status ;;
    logs)       show_logs "${2:-}" ;;
    backup)     backup_tenant "${2:-}" ;;
    *)          usage ;;
esac
