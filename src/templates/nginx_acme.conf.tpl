user nginx;
worker_processes auto;

error_log /var/log/nginx/error.log warn;
pid       /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format  main  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" "$http_x_forwarded_for"';

    access_log  /var/log/nginx/access.log  main;

    sendfile        on;
    keepalive_timeout  65;

    # HTTP-only server for ACME HTTP-01 challenge
    # This is used temporarily during Let's Encrypt certificate acquisition
    server {
        listen 80;
        server_name ${OPAL_HOSTNAME};

        location /.well-known/acme-challenge/ {
            root /var/www/certbot;
        }

        location / {
            return 503 "Service temporarily unavailable during certificate setup";
        }
    }
}
