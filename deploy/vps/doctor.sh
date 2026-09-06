#!/usr/bin/env bash
set -u

echo "== Knowledge Hub VPS Doctor =="
date
echo

echo "== Systemd services =="
systemctl is-active knowledge-hub.service 2>/dev/null || true
systemctl is-active nginx 2>/dev/null || true
echo

echo "== Service status =="
systemctl status knowledge-hub.service --no-pager -l 2>/dev/null || true
echo
systemctl status nginx --no-pager -l 2>/dev/null || true
echo

echo "== Local API =="
curl -sS -m 8 -i http://127.0.0.1:8080/api/v1/info || true
echo

echo "== Nginx local proxy =="
curl -sS -m 8 -i http://127.0.0.1/api/v1/info || true
echo

echo "== Public domain from server =="
curl -k -sS -m 12 -i https://corenote.cloud/api/v1/info || true
echo

echo "== Ports =="
ss -ltnp | grep -E ':(80|443|8080)\s' || true
echo

echo "== Disk and memory =="
df -h
echo
free -h
echo

echo "== Recent app logs =="
journalctl -u knowledge-hub.service -n 120 --no-pager 2>/dev/null || true
echo

echo "== Recent nginx logs =="
journalctl -u nginx -n 80 --no-pager 2>/dev/null || true

