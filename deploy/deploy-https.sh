#!/usr/bin/env bash
# Knowledge Hub 一键部署脚本（HTTPS 公网访问）
# 用法: ./deploy-https.sh <domain> <email> [tenant_name]
# 示例: ./deploy-https.sh hub.example.com admin@example.com myhub

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

DOMAIN="${1:-}"
EMAIL="${2:-}"
TENANT="${3:-hub}"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    log_error "参数不完整"
    echo ""
    echo "用法: $0 <domain> <email> [tenant_name]"
    echo "示例: $0 hub.example.com admin@example.com myhub"
    echo ""
    echo "前置条件:"
    echo "  1. 服务器有公网 IP，80/443 端口可访问"
    echo "  2. 域名 DNS 已解析到服务器 IP"
    echo "  3. 已安装 Docker 和 Docker Compose"
    exit 1
fi

# ==================== Step 1: 环境检查 ====================
log_step "1/7 检查部署环境..."

for cmd in docker docker-compose; do
    if ! command -v "$cmd" &>/dev/null; then
        log_error "$cmd 未安装"
        exit 1
    fi
done

if ! docker info &>/dev/null; then
    log_error "Docker 未运行或无权限"
    exit 1
fi

log_info "环境检查通过"

# ==================== Step 2: 生成自签名证书（用于首次启动） ====================
log_step "2/7 生成自签名证书（首次启动用，后续替换为 Let's Encrypt）..."

SSL_DIR="$PROJECT_DIR/nginx/ssl"
mkdir -p "$SSL_DIR"

if [ ! -f "$SSL_DIR/fullchain.pem" ] || [ ! -f "$SSL_DIR/privkey.pem" ]; then
    openssl req -x509 -nodes \
        -newkey rsa:2048 \
        -keyout "$SSL_DIR/privkey.pem" \
        -out "$SSL_DIR/fullchain.pem" \
        -days 365 \
        -subj "/CN=$DOMAIN" \
        -addext "subjectAltName=DNS:$DOMAIN,DNS:*.$DOMAIN" 2>/dev/null

    chmod 644 "$SSL_DIR/fullchain.pem"
    chmod 600 "$SSL_DIR/privkey.pem"
    log_info "自签名证书已生成"
else
    log_info "已有证书，跳过生成"
fi

# ==================== Step 3: 更新 Nginx 配置中的域名 ====================
log_step "3/7 更新 Nginx 配置..."

NGINX_CONF="$PROJECT_DIR/nginx/conf.d/default.conf"
if grep -q "yourdomain\.com" "$NGINX_CONF"; then
    sed -i "s/yourdomain\.com/$DOMAIN/g" "$NGINX_CONF"
    log_info "Nginx 域名已更新为 $DOMAIN"
else
    log_info "Nginx 域名已是 $DOMAIN"
fi

# ==================== Step 4: 创建/更新租户配置 ====================
log_step "4/7 配置租户..."

TENANT_DIR="$PROJECT_DIR/tenants/$TENANT"
mkdir -p "$TENANT_DIR"

if [ ! -f "$TENANT_DIR/.env" ]; then
    # 生成随机 SECRET_KEY
    SECRET_KEY=$(openssl rand -hex 32)

    cat > "$TENANT_DIR/.env" << EOF
APP_NAME="Knowledge Hub"
APP_VERSION="1.0.0"
APP_ENV=production
DEBUG=false

HOST=0.0.0.0
PORT=8080

TENANT_ID=$TENANT

OPENAI_API_KEY=
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
OPENAI_MODEL=glm-4-flash
OPENAI_TEMPERATURE=0.7
OPENAI_MAX_TOKENS=2000
EMBEDDING_MODEL=embedding-3
EMBEDDING_DIMENSION=2048

CHUNK_SIZE=512
CHUNK_OVERLAP=64

RAG_TOP_K=5
RAG_SIMILARITY_THRESHOLD=0.65
HYBRID_SEARCH_WEIGHT_VECTOR=0.6
HYBRID_SEARCH_WEIGHT_KEYWORD=0.4

LOG_LEVEL=WARNING
CORS_ORIGINS=["https://$DOMAIN","https://$TENANT.$DOMAIN"]

SECRET_KEY=$SECRET_KEY

STORAGE_MODE=local
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_BUCKET=knowledge-hub-files
EOF

    log_info "租户配置已创建: $TENANT_DIR/.env"
    log_warn "请编辑 .env 文件设置 OPENAI_API_KEY"
else
    log_info "租户配置已存在"
fi

# ==================== Step 5: 更新 docker-compose ====================
log_step "5/7 更新 Docker Compose 配置..."

COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# 确保租户服务在 compose 中
if ! grep -q "container_name: kh-$TENANT" "$COMPOSE_FILE"; then
    # 添加新租户服务
    cat >> "$COMPOSE_FILE" << EOF

  $TENANT:
    <<: *app-common
    container_name: kh-$TENANT
    env_file:
      - ./deploy/tenants/$TENANT/.env
    volumes:
      - ${TENANT}-data:/app/data
    networks:
      internal:
        aliases:
          - $TENANT
EOF

    # 添加 volume
    if ! grep -q "${TENANT}-data:" "$COMPOSE_FILE"; then
        sed -i "/^networks:/i\\  ${TENANT}-data:\n    driver: local\n" "$COMPOSE_FILE"
    fi

    log_info "租户 $TENANT 已添加到 docker-compose.yml"
fi

# ==================== Step 6: 启动服务 ====================
log_step "6/7 启动服务..."

cd "$PROJECT_DIR"

# 构建并启动
docker-compose build
docker-compose up -d

log_info "等待服务启动..."
sleep 10

# 检查健康状态
if curl -sf "http://localhost:8080/api/v1/health" > /dev/null 2>&1; then
    log_info "服务健康检查通过"
else
    log_warn "服务可能还在启动中，请稍后检查"
fi

# ==================== Step 7: 获取 Let's Encrypt 证书 ====================
log_step "7/7 获取 Let's Encrypt 正式证书..."

read -rp "是否现在获取 Let's Encrypt 证书？(需要域名已解析到本机) [y/N] " confirm
if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
    # 使用 certbot 容器获取证书
    docker-compose --profile ssl run --rm certbot certonly \
        --webroot \
        --webroot-path /var/www/certbot \
        --agree-tos \
        --no-eff-email \
        --email "$EMAIL" \
        -d "$DOMAIN" \
        -d "*.$DOMAIN"

    # 复制证书
    CERT_SRC="/etc/letsencrypt/live/$DOMAIN"
    docker cp "kh-certbot:$CERT_SRC/fullchain.pem" "$SSL_DIR/fullchain.pem" 2>/dev/null || \
        cp "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$SSL_DIR/fullchain.pem" 2>/dev/null || true
    docker cp "kh-certbot:$CERT_SRC/privkey.pem" "$SSL_DIR/privkey.pem" 2>/dev/null || \
        cp "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$SSL_DIR/privkey.pem" 2>/dev/null || true

    # 重载 Nginx
    docker exec kh-nginx nginx -s reload 2>/dev/null || true

    log_info "Let's Encrypt 证书已安装"
else
    log_info "跳过证书获取，当前使用自签名证书"
    log_info "稍后可运行: docker-compose --profile ssl run --rm certbot certonly --webroot --webroot-path /var/www/certbot -d $DOMAIN -d *.$DOMAIN --email $EMAIL --agree-tos"
fi

# ==================== 完成 ====================
echo ""
echo "============================================"
log_info "部署完成！"
echo "============================================"
echo ""
echo "  访问地址: https://$DOMAIN"
echo "  健康检查: https://$DOMAIN/api/v1/health"
echo ""
echo "  租户配置: $TENANT_DIR/.env"
echo "  SSL 证书: $SSL_DIR/"
echo "  Nginx 日志: $PROJECT_DIR/nginx/logs/"
echo ""
echo "  管理命令:"
echo "    查看状态: docker-compose ps"
echo "    查看日志: docker-compose logs -f"
echo "    停止服务: docker-compose down"
echo "    重启服务: docker-compose restart"
echo "    证书续期: docker-compose --profile ssl run --rm certbot renew"
echo ""
echo "  安全提醒:"
echo "    1. 请修改 $TENANT_DIR/.env 中的 OPENAI_API_KEY"
echo "    2. SECRET_KEY 已自动生成，请妥善保管"
echo "    3. CORS 已限制为 https://$DOMAIN"
echo "    4. API 文档在生产环境已禁用"
echo "============================================"
