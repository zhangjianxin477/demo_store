#!/usr/bin/env bash
# Let's Encrypt SSL 证书自动获取和续期脚本
# 用法: ./setup-ssl.sh <domain> <email>
# 示例: ./setup-ssl.sh hub.example.com admin@example.com

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SSL_DIR="$PROJECT_DIR/nginx/ssl"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ]; then
    log_error "域名不能为空"
    echo "用法: $0 <domain> <email>"
    echo "示例: $0 hub.example.com admin@example.com"
    exit 1
fi

if [ -z "$EMAIL" ]; then
    log_error "邮箱不能为空（Let's Encrypt 用于通知）"
    echo "用法: $0 <domain> <email>"
    exit 1
fi

# 检查 certbot 是否安装
if ! command -v certbot &>/dev/null; then
    log_info "安装 certbot..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y certbot
    elif command -v yum &>/dev/null; then
        sudo yum install -y certbot
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y certbot
    else
        log_error "无法自动安装 certbot，请手动安装: https://certbot.eff.org/"
        exit 1
    fi
fi

# 创建 SSL 目录
mkdir -p "$SSL_DIR"

# 检查是否已有有效证书
if [ -f "$SSL_DIR/fullchain.pem" ] && [ -f "$SSL_DIR/privkey.pem" ]; then
    # 检查证书是否过期
    if openssl x509 -checkend 2592000 -noout -in "$SSL_DIR/fullchain.pem" 2>/dev/null; then
        log_warn "已有有效证书（30天内不过期），如需重新获取请先删除旧证书"
        log_info "证书路径: $SSL_DIR/fullchain.pem"
        log_info "续期命令: $0 --renew"
        exit 0
    fi
fi

# 使用 standalone 模式获取证书（需要 80 端口空闲）
log_info "正在获取 Let's Encrypt 证书..."
log_info "域名: $DOMAIN"
log_info "邮箱: $EMAIL"

# 先停止可能占用 80 端口的服务
log_info "临时停止 Nginx（如果运行中）..."
sudo systemctl stop nginx 2>/dev/null || true
sudo docker stop kh-nginx 2>/dev/null || true

# 获取证书
sudo certbot certonly \
    --standalone \
    --agree-tos \
    --no-eff-email \
    --email "$EMAIL" \
    -d "$DOMAIN" \
    -d "*.$DOMAIN" \
    --non-interactive

# 复制证书到项目目录
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"

if [ -d "$CERT_DIR" ]; then
    sudo cp "$CERT_DIR/fullchain.pem" "$SSL_DIR/fullchain.pem"
    sudo cp "$CERT_DIR/privkey.pem" "$SSL_DIR/privkey.pem"
    sudo chmod 644 "$SSL_DIR/fullchain.pem"
    sudo chmod 600 "$SSL_DIR/privkey.pem"
    log_info "证书已复制到 $SSL_DIR/"
else
    log_error "证书获取失败，未找到 $CERT_DIR"
    exit 1
fi

# 设置自动续期 cron
log_info "配置自动续期..."
RENEW_SCRIPT="$PROJECT_DIR/renew-ssl.sh"
cat > "$RENEW_SCRIPT" << 'RENEW_EOF'
#!/usr/bin/env bash
# Let's Encrypt 证书自动续期脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSL_DIR="$SCRIPT_DIR/nginx/ssl"

# 续期证书
sudo certbot renew --quiet

# 查找最新证书并复制
for cert_dir in /etc/letsencrypt/live/*/; do
    domain=$(basename "$cert_dir")
    if [ -f "$cert_dir/fullchain.pem" ] && [ -f "$cert_dir/privkey.pem" ]; then
        sudo cp "$cert_dir/fullchain.pem" "$SSL_DIR/fullchain.pem"
        sudo cp "$cert_dir/privkey.pem" "$SSL_DIR/privkey.pem"
        sudo chmod 644 "$SSL_DIR/fullchain.pem"
        sudo chmod 600 "$SSL_DIR/privkey.pem"
        echo "[$(date)] 证书已续期: $domain"
    fi
done

# 重载 Nginx
if command -v systemctl &>/dev/null; then
    sudo systemctl reload nginx 2>/dev/null || true
fi
sudo docker exec kh-nginx nginx -s reload 2>/dev/null || true
RENEW_EOF

chmod +x "$RENEW_SCRIPT"

# 添加 cron 任务（每天凌晨 3 点检查续期）
CRON_LINE="0 3 * * * $RENEW_SCRIPT >> $PROJECT_DIR/ssl-renew.log 2>&1"
(crontab -l 2>/dev/null | grep -v "$RENEW_SCRIPT"; echo "$CRON_LINE") | crontab -

log_info "自动续期已配置（每天 3:00 AM 检查）"

# 重启 Nginx
log_info "重启 Nginx..."
sudo systemctl start nginx 2>/dev/null || true

log_info "SSL 证书配置完成！"
log_info "  证书: $SSL_DIR/fullchain.pem"
log_info "  私钥: $SSL_DIR/privkey.pem"
log_info "  续期: $RENEW_SCRIPT"
log_info ""
log_info "请更新 Nginx 配置中的 server_name 为: $DOMAIN"
log_info "请更新 .env 中的 CORS_ORIGINS 为: https://$DOMAIN"
